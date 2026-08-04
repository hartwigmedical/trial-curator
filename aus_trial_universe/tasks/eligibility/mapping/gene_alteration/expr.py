"""Finding-model expression AST + CANONICAL FORM (the deterministic half of stage 2).

WHY. Two expressions can mean the same thing and read differently — `X & NOT(a) & NOT(b)` vs
`X & NOT(a | b)` (De Morgan), `A | B` vs `B | A` (order), `A & (A | B)` vs `A` (absorption). Whether that matters
downstream depends on whether the consumer parses the boolean structure or string-compares it, and we should not
have to care: equivalent expressions must be textually IDENTICAL before they leave the pipeline. That is what
`canonicalise` guarantees, and it is what makes group reconciliation meaningful (you cannot group by meaning if
meaning is not canonically spelled).

THE CANONICAL FORM is a negation-factored DNF:

    (p1 | p2 | ... | pn) & NOT(q1) & NOT(q2) & ...

- the POSITIVE part is disjunctive normal form: an OR of conjunctions of positive terms (usually one term each)
- NEGATIVE literals are factored out to the end, one `NOT(...)` per literal, so a De Morgan pair collapses to one
  spelling. `NOT(a | b)` -> `NOT(a) & NOT(b)`; `NOT(a & b)` CANNOT be split (it means "not both") and is kept whole.
- every list is sorted by a total order, so the rendering is a function of the meaning
- absorption is applied: `A | (A & B)` -> `A`, `A & (A | B)` -> `A`, using TERM SUBSUMPTION (a term with more
  fields implies the term with fewer), which is what makes `SmallVariant[gene=EGFR] &
  (SmallVariant[gene=EGFR] | GainDeletion[gene=EGFR & type=GAIN])` collapse to `SmallVariant[gene=EGFR]`.
- `idempotence` is a hard requirement, tested over the whole corpus: canonicalise(canonicalise(x)) == canonicalise(x).

A per-branch negative (different exclusions on different positive branches) cannot be factored; that shape is
reported by `problems()` as `unfactorable_negation` rather than silently mangled. It does not occur in the current
corpus — extraction splits such cases into separate rows.

THE DNF INVARIANT — do not "fix" an in-cell OR by splitting rows. Full statement and measurements in
`docs/planning/archive/v2_gene_alteration_correction_spec.md` §3b; the short form:

    One row = one conjunction of SOURCE CRITERIA. Within a cell, a disjunction is permitted ONLY where it
    enumerates the vocabulary tokens of a SINGLE criterion.

An OR inside a mapped cell is an artifact of the TARGET VOCABULARY being less expressive than English, not a
disjunction in the trial's logic: "TP53 alteration" is one atom that needs three terms; "RAS mutation" is one atom
that needs three genes; "SWI/SNF complex alteration" is one atom that needs 93. Measured, only 3 of 900 interpreted
cells hold a genuine positive OR — the other 132 OR-bearing expressions were introduced HERE, by mapping. Because a
cell holds the mapping of exactly ONE free-text value, every disjunct in it came from one source concept by
construction. Splitting them into rows would present one criterion as N cohorts, destroy the per-arm conjunction
count, expand 132 values into 639 disjuncts, and give the consumer a shape it handles LESS naturally than the flat
OR it already models. Row grain belongs to extraction; this module never changes it.

The in-bracket alternation `Fusion[geneStart=X | geneEnd=X]` is a SINGLE TERM and is preserved as such (a fusion is
one event with two slots; the `|` says which slot is unknown). Term bodies are opaque to the boolean layer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import product

# Term ordering — mirrors the grammar's "Order terms SmallVariant, GainDeletion, Disruption, Fusion".
_CLASS_RANK = {
    "SmallVariant": 0, "GainDeletion": 1, "Disruption": 2, "Fusion": 3, "Arm": 4,
    "Wildtype": 5, "Virus": 6, "HlaAllele": 7, "PharmocoGenotype": 8,
    "MicrosatelliteStability": 9, "homologousRecombination": 10,
    "tumorMutationBurden": 11, "tumorMutationLoad": 12,
}
# Canonical field order inside a term body (unlisted fields sort alphabetically after these).
_FIELD_ORDER = [
    "gene", "geneStart", "geneEnd", "chromosome", "arm", "type", "name", "allele",
    "transcriptImpact.hgvsProteinImpact", "transcriptImpact.affectedCodon", "transcriptImpact.affectedExon",
    "transcriptImpact.effects", "transcriptImpact.codingEffect", "inSpliceRegion",
    "region", "band", "PurpleMicrosatelliteStatus", "ChordStatus", "Status",
]
_FIELD_RANK = {f: i for i, f in enumerate(_FIELD_ORDER)}

_TERM_RE = re.compile(r"([A-Za-z][A-Za-z0-9]*)\[([^\[\]]*)\]")


class ExprError(ValueError):
    """The expression could not be parsed as finding-model."""


# --------------------------------------------------------------------------- #
# AST
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Term:
    """One `Class[body]` atom. `body` is opaque to the boolean layer but canonically ordered."""

    cls: str
    body: str

    def render(self) -> str:
        return f"{self.cls}[{self.body}]" if self.body else f"{self.cls}[]"

    @property
    def sort_key(self) -> tuple:
        return (_CLASS_RANK.get(self.cls, 99), self.cls, self.body)


@dataclass(frozen=True)
class Not:
    """Negation of a node. After canonicalisation the child is a Term or an And of Terms (never an Or)."""

    child: object

    def render(self) -> str:
        return f"NOT({render(self.child)})"

    @property
    def sort_key(self) -> tuple:
        return (50,) + _key(self.child)


@dataclass(frozen=True)
class And:
    parts: tuple = field(default_factory=tuple)

    def render(self) -> str:
        # `&` binds tighter than `|`, so a nested Or MUST be parenthesised or the rendering re-parses differently.
        return " & ".join(f"({render(p)})" if isinstance(p, Or) else render(p) for p in self.parts)

    @property
    def sort_key(self) -> tuple:
        return (60, tuple(_key(p) for p in self.parts))


@dataclass(frozen=True)
class Or:
    parts: tuple = field(default_factory=tuple)

    def render(self) -> str:
        return " | ".join(render(p) for p in self.parts)

    @property
    def sort_key(self) -> tuple:
        return (70, tuple(_key(p) for p in self.parts))


def _key(n) -> tuple:
    return n.sort_key


def render(n) -> str:
    return n.render()


# --------------------------------------------------------------------------- #
# Parsing — recursive descent over a token stream. `[...]` bodies are opaque.
# --------------------------------------------------------------------------- #
def _tokenise(s: str) -> list[str]:
    toks: list[str] = []
    i = 0
    while i < len(s):
        c = s[i]
        if c.isspace():
            i += 1
        elif c in "()&|":
            toks.append(c)
            i += 1
        elif s.startswith("NOT", i) and s[i + 3:i + 4] == "(":
            toks.append("NOT")
            i += 3
        else:
            m = _TERM_RE.match(s, i)
            if not m:
                raise ExprError(f"cannot tokenise at offset {i}: {s[i:i + 40]!r}")
            toks.append(m.group(0))
            i = m.end()
    return toks


def parse(expr: str):
    """Parse a finding-model expression into an AST. Raises ExprError on malformed input."""
    expr = (expr or "").strip()
    if not expr:
        return None
    toks = _tokenise(expr)
    pos = 0

    def peek():
        return toks[pos] if pos < len(toks) else None

    def eat(t=None):
        nonlocal pos
        if pos >= len(toks):
            raise ExprError("unexpected end of expression")
        got = toks[pos]
        if t is not None and got != t:
            raise ExprError(f"expected {t!r}, got {got!r}")
        pos += 1
        return got

    def p_or():
        parts = [p_and()]
        while peek() == "|":
            eat("|")
            parts.append(p_and())
        return parts[0] if len(parts) == 1 else Or(tuple(parts))

    def p_and():
        parts = [p_unary()]
        while peek() == "&":
            eat("&")
            parts.append(p_unary())
        return parts[0] if len(parts) == 1 else And(tuple(parts))

    def p_unary():
        t = peek()
        if t is None:
            raise ExprError("unexpected end of expression")
        if t == "NOT":
            eat("NOT")
            eat("(")
            inner = p_or()
            eat(")")
            return Not(inner)
        if t == "(":
            eat("(")
            inner = p_or()
            eat(")")
            return inner
        if t in "&|)":
            raise ExprError(f"unexpected operator {t!r}")
        raw = eat()
        m = _TERM_RE.fullmatch(raw)
        if not m:
            raise ExprError(f"not a finding-model term: {raw!r}")
        return Term(m.group(1), _canon_body(m.group(2)))

    node = p_or()
    if pos != len(toks):
        raise ExprError(f"trailing input at token {pos}: {toks[pos:]!r}")
    return node


def _canon_body(body: str) -> str:
    """Normalise a term body: whitespace, and field order when the body is a plain `&`-list.

    Bodies containing `|` (the Fusion single-gene alternation) or `(` (the compound Arm form) keep their given
    order — their internal structure is meaningful and is not ours to reshuffle.
    """
    body = re.sub(r"\s+", " ", body).strip()
    if not body or "|" in body or "(" in body:
        return " | ".join(p.strip() for p in body.split("|")) if "|" in body else body
    pieces = [p.strip() for p in body.split("&") if p.strip()]
    pieces.sort(key=lambda p: (_FIELD_RANK.get(p.partition("=")[0].strip(), 90),
                               p.partition("=")[0].strip(), p))
    return " & ".join(pieces)


# --------------------------------------------------------------------------- #
# Subsumption — the relation that drives absorption.
# --------------------------------------------------------------------------- #
def _body_fields(body: str) -> dict[str, str] | None:
    """Field map of a plain `&`-body; None when the body is structured (`|` / parens) and not comparable."""
    if "|" in body or "(" in body:
        return None
    out: dict[str, str] = {}
    for piece in body.split("&"):
        piece = piece.strip()
        if not piece:
            continue
        k, _, v = piece.partition("=")
        out[k.strip()] = v.strip()
    return out


def implies(a: Term, b: Term) -> bool:
    """True if term `a` implies term `b` — same class, and every field of `b` is present in `a` with equal value.

    `SmallVariant[gene=EGFR & affectedExon=20]` implies `SmallVariant[gene=EGFR]`: the former is a strictly more
    specific requirement, so satisfying it satisfies the latter.
    """
    if a.cls != b.cls:
        return False
    if a == b:
        return True
    fa, fb = _body_fields(a.body), _body_fields(b.body)
    if fa is None or fb is None:
        return False
    return all(k in fa and fa[k] == v for k, v in fb.items())


def _lit_implies(a, b) -> bool:
    """Literal-level implication. Positive literals use term subsumption; negatives invert it
    (`NOT(broad)` implies `NOT(specific)`)."""
    if isinstance(a, Term) and isinstance(b, Term):
        return implies(a, b)
    if isinstance(a, Not) and isinstance(b, Not):
        if isinstance(a.child, Term) and isinstance(b.child, Term):
            return implies(b.child, a.child)
        return a == b
    return False


# --------------------------------------------------------------------------- #
# Canonicalisation
# --------------------------------------------------------------------------- #
def _push_not(n):
    """Normalise negation to a conjunction of atomic negations.

    The negated child is first pushed to DNF, then De Morgan is applied across its disjuncts:
    `NOT(a | b)` -> `NOT(a) & NOT(b)`, and `NOT(a & (b | c))` -> `NOT(a & b) & NOT(a & c)`. A single conjunctive
    disjunct is kept whole — `NOT(a & b)` means "not both" and cannot be split further.
    """
    if isinstance(n, Term) or n is None:
        return n
    if isinstance(n, Not):
        conjs = _to_dnf(_flatten(_push_not(n.child)))
        parts: list = []
        for c in conjs:
            if len(c) == 1:
                # NOT(NOT x) -> x
                parts.append(c[0].child if isinstance(c[0], Not) else Not(c[0]))
            else:
                parts.append(Not(And(tuple(sorted(c, key=_key)))))
        if not parts:
            return None
        return parts[0] if len(parts) == 1 else And(tuple(parts))
    if isinstance(n, And):
        return And(tuple(_push_not(p) for p in n.parts))
    if isinstance(n, Or):
        return Or(tuple(_push_not(p) for p in n.parts))
    return n


def _flatten(n):
    if isinstance(n, And):
        parts: list = []
        for p in (_flatten(x) for x in n.parts):
            parts.extend(p.parts if isinstance(p, And) else [p])
        return And(tuple(parts)) if len(parts) != 1 else parts[0]
    if isinstance(n, Or):
        parts = []
        for p in (_flatten(x) for x in n.parts):
            parts.extend(p.parts if isinstance(p, Or) else [p])
        return Or(tuple(parts)) if len(parts) != 1 else parts[0]
    if isinstance(n, Not):
        return Not(_flatten(n.child))
    return n


def _to_dnf(n) -> list[list]:
    """Distribute to a list of conjunctions (each a list of literals: Term or Not)."""
    if n is None:
        return []
    if isinstance(n, (Term, Not)):
        return [[n]]
    if isinstance(n, And):
        out = [[]]
        for p in n.parts:
            sub = _to_dnf(p)
            out = [a + b for a, b in product(out, sub)]
        return out
    if isinstance(n, Or):
        return [c for p in n.parts for c in _to_dnf(p)]
    raise ExprError(f"cannot normalise node {n!r}")


def _simplify_conj(conj: list) -> list | None:
    """Dedupe, apply within-conjunction absorption, resolve NOT(X & Y) against a positive Y.

    Returns None when the conjunction is unsatisfiable (`X & NOT(X)`).
    """
    pos = [l for l in conj if isinstance(l, Term)]
    # NOT(X & Y) with Y required  ->  NOT(X)
    reduced: list = []
    for l in conj:
        if isinstance(l, Not) and isinstance(l.child, And):
            kids = [k for k in l.child.parts if isinstance(k, Term)]
            keep = [k for k in kids if not any(implies(p, k) for p in pos)]
            if len(keep) != len(kids):
                if not keep:
                    return None                       # NOT(all of which are required) -> unsatisfiable
                reduced.append(Not(keep[0]) if len(keep) == 1 else Not(And(tuple(keep))))
                continue
        reduced.append(l)
    # unsatisfiable: a required term is also excluded
    for l in reduced:
        if isinstance(l, Not) and isinstance(l.child, Term):
            if any(implies(p, l.child) for p in reduced if isinstance(p, Term)):
                return None
    # absorption within the conjunction: drop a literal implied by another (keep the SPECIFIC one)
    out: list = []
    for i, l in enumerate(reduced):
        if any(j != i and _lit_implies(o, l) and not (o == l and j > i)
               for j, o in enumerate(reduced)):
            continue
        out.append(l)
    # dedupe preserving order
    seen = set()
    ded = []
    for l in out:
        if l not in seen:
            seen.add(l)
            ded.append(l)
    return ded


def _absorb_disjuncts(conjs: list[list]) -> list[list]:
    """`A | (A & B)` -> `A`: drop a conjunction that is strictly stronger than another (it implies it)."""
    keep: list[list] = []
    for i, c in enumerate(conjs):
        redundant = False
        for j, o in enumerate(conjs):
            if i == j:
                continue
            # o absorbs c when every literal of o is implied by some literal of c (c => o)
            if all(any(_lit_implies(lc, lo) for lc in c) for lo in o):
                if not (set(map(str, o)) == set(map(str, c)) and j > i):
                    redundant = True
                    break
        if not redundant:
            keep.append(c)
    return keep


def canonicalise(expr: str) -> str:
    """Return the canonical negation-factored DNF rendering of `expr` (idempotent). '' for empty input."""
    node = parse(expr)
    if node is None:
        return ""
    conjs = _to_dnf(_flatten(_push_not(_flatten(node))))
    simplified = [c for c in (_simplify_conj(c) for c in conjs) if c]
    if not simplified:
        return ""
    simplified = _absorb_disjuncts(simplified)

    # Factor the negatives shared by EVERY disjunct out to the tail.
    neg_sets = [{str(l): l for l in c if isinstance(l, Not)} for c in simplified]
    shared = set(neg_sets[0]) if neg_sets else set()
    for s in neg_sets[1:]:
        shared &= set(s)
    shared_lits = sorted((neg_sets[0][k] for k in shared), key=_key) if shared else []

    disjuncts = []
    for c in simplified:
        rest = [l for l in c if not (isinstance(l, Not) and str(l) in shared)]
        rest.sort(key=_key)
        disjuncts.append(rest)
    # drop empty disjuncts that arose from a purely-negative expression
    disjuncts = [d for d in disjuncts if d]
    disjuncts.sort(key=lambda d: tuple(_key(l) for l in d))
    # dedupe identical disjuncts
    seen: set = set()
    uniq = []
    for d in disjuncts:
        sig = tuple(str(l) for l in d)
        if sig not in seen:
            seen.add(sig)
            uniq.append(d)

    parts: list[str] = []
    if uniq:
        if len(uniq) == 1:
            parts.append(" & ".join(render(l) for l in uniq[0]))
        else:
            # With several disjuncts, any multi-literal disjunct is parenthesised so the OR/AND precedence is
            # explicit (our own syntax validator rejects an unparenthesised mix, and so should a reader).
            rendered = [render(d[0]) if len(d) == 1 else "(" + " & ".join(render(l) for l in d) + ")"
                        for d in uniq]
            grouped = " | ".join(rendered)
            parts.append(f"({grouped})" if shared_lits else grouped)
    parts.extend(render(l) for l in shared_lits)
    return " & ".join(p for p in parts if p)


#: Separator inside a `literal_set` key. It must be a character that cannot occur in an expression — `|` cannot be
#: used, because a `Fusion[geneStart=X | geneEnd=X]` term contains one in its own body and callers that split the
#: key on the separator would over-count that term's literals.
LITERAL_SEP = "\x1f"


def literal_set(expr: str) -> frozenset[str]:
    """The multiset-free signature of an expression's literals — used to detect semantic (vs textual) change.

    Each element is one DNF disjunct: its literals sorted and joined by `LITERAL_SEP`. Split on `LITERAL_SEP` (never
    on `|`) to count the literals in a disjunct.
    """
    node = parse(expr)
    if node is None:
        return frozenset()
    conjs = _to_dnf(_flatten(_push_not(_flatten(node))))
    return frozenset(
        LITERAL_SEP.join(sorted(str(l) for l in c)) for c in (_simplify_conj(x) for x in conjs) if c
    )


def same_meaning(a: str, b: str) -> bool:
    """True if two expressions have the same canonical form (so differ at most in spelling)."""
    try:
        return canonicalise(a) == canonicalise(b)
    except ExprError:
        return (a or "").strip() == (b or "").strip()


def genes_of(expr: str) -> frozenset[str]:
    """Every gene symbol named anywhere in the expression (positive or negated)."""
    out: set[str] = set()
    for _, body in _TERM_RE.findall(expr or ""):
        for piece in re.split(r"[&|]", body.replace("(", " ").replace(")", " ")):
            k, _, v = piece.strip().partition("=")
            if k.strip() in ("gene", "geneStart", "geneEnd") and v.strip():
                out.add(v.strip())
    return frozenset(out)
