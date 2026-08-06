"""OncoTree code-expression PARSER + canonical form.

The production check (`tools/oncotree.py:invalid_codes`) is a token regex over ALL-CAPS runs. That is
blind to three whole families of defect: an OncoTree NAME leaked into the code field (names are
mixed-case, so they are skipped), a case-variant of a real code, and any structural problem at all.
This module parses the expression properly instead.

Grammar (the one the mapper prompts actually specify):
    expr   := or
    or     := and (' OR ' and)*
    and    := unary (' AND ' unary)*
    unary  := 'NOT(' expr ')' | '(' expr ')' | ATOM

ATOMs are resolved AFTER masking every OncoTree NAME longest-first, because names legitimately contain
parentheses and commas (`Primary Mediastinal (Thymic) Large B-Cell Lymphoma`,
`Oligodendroglioma, IDH-mutant, and 1p/19q-Codeleted` — note the lowercase 'and'). A parser that splits
before masking tears those apart and reports nonsense.
"""
from __future__ import annotations

import functools
import re

from aus_trial_universe.tasks.eligibility.mapping.cancer_type.vocab import (
    SENTINELS,
    is_subcode,
    name_to_code,
    oncotree_vocab,
    valid_codes,
)

# --------------------------------------------------------------------------- #
# Lookup tables (case-insensitive variants included, so a wrong-case operand is
# reported as a case error rather than as unknown free text)
# --------------------------------------------------------------------------- #


@functools.lru_cache(maxsize=1)
def _tables():
    """(codes, names->code, sentinels, names longest-first, lowercased -> (kind, canonical spelling))."""
    codes = {c: c for c in oncotree_vocab()}
    names = dict(name_to_code())
    sents = {s: s for s in SENTINELS}
    lower: dict[str, tuple[str, str]] = {}
    for table, kind in ((sents, "sentinel"), (codes, "code"), (names, "name")):
        for key in table:
            lower.setdefault(key.lower(), (kind, key))
    # Masking covers EVERY vocabulary name; only RESOLUTION is restricted to the unambiguous ones (`name_to_code`
    # omits a name shared by several codes). Building this list from `names` would couple the two: a name that
    # cannot be resolved would also stop being protected from the parser, and names legitimately contain commas,
    # parentheses and a lowercase "and". None of the 9 currently-ambiguous names happens to contain one, so that
    # coupling is harmless today — but by accident, not by construction.
    names_desc = sorted((n for n in set(oncotree_vocab().values()) if n not in sents), key=len, reverse=True)
    return codes, names, sents, names_desc, lower


# --------------------------------------------------------------------------- #
# AST
# --------------------------------------------------------------------------- #
class Node:
    pass


class Atom(Node):
    __slots__ = ("text",)

    def __init__(self, text: str):
        self.text = text.strip()

    def __repr__(self) -> str:
        return f"Atom({self.text!r})"


class Not(Node):
    __slots__ = ("child",)

    def __init__(self, child: Node):
        self.child = child

    def __repr__(self) -> str:
        return f"Not({self.child!r})"


class Op(Node):
    __slots__ = ("op", "kids")

    def __init__(self, op: str, kids: list[Node]):
        self.op, self.kids = op, kids

    def __repr__(self) -> str:
        return f"Op({self.op}, {self.kids!r})"


class ParseError(Exception):
    """The expression is not well-formed (unbalanced parens, empty group, dangling operator)."""


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
_MASK_RE = re.compile(r"\x01N(\d+)\x02")


def mask_names(expr: str) -> tuple[str, dict[str, str]]:
    """Replace each OncoTree NAME with an opaque token so it survives parsing as ONE atom."""
    _c, _n, _s, names_desc, _l = _tables()
    table: dict[str, str] = {}
    out = expr
    for name in names_desc:
        if name in out:
            tok = f"\x01N{len(table)}\x02"
            table[tok] = name
            out = out.replace(name, tok)
    return out, table


def unmask(text: str, table: dict[str, str]) -> str:
    return _MASK_RE.sub(lambda m: table.get(m.group(0), m.group(0)), text)


def balanced(s: str) -> bool:
    depth = 0
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def split_top(s: str, op: str) -> list[str]:
    """Split on ' <op> ' at paren-depth 0 only."""
    token, parts, depth, start, i = f" {op} ", [], 0, 0, 0
    while i < len(s):
        ch = s[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and s.startswith(token, i):
            parts.append(s[start:i])
            i += len(token)
            start = i
            continue
        i += 1
    parts.append(s[start:])
    return parts


def _parse(s: str) -> Node:
    s = s.strip()
    if not s:
        raise ParseError("empty operand")
    if not balanced(s):
        raise ParseError("unbalanced parentheses")
    for op in ("OR", "AND"):
        parts = split_top(s, op)
        if len(parts) > 1:
            if any(not p.strip() for p in parts):
                raise ParseError(f"dangling {op} operator")
            return Op(op, [_parse(p) for p in parts])
    if s.upper().startswith("NOT(") and s.endswith(")") and balanced(s[4:-1]):
        return Not(_parse(s[4:-1]))
    if s.startswith("(") and s.endswith(")") and balanced(s[1:-1]):
        return _parse(s[1:-1])
    return Atom(s)


def parse(expr: str) -> tuple[Node, dict[str, str]]:
    """Parse a code expression. Returns (ast, name-mask table). Raises ParseError."""
    masked, table = mask_names(expr)
    return _parse(masked), table


# --------------------------------------------------------------------------- #
# Operand classification
# --------------------------------------------------------------------------- #
def classify(atom_text: str, table: dict[str, str]) -> tuple[str, str | None]:
    """(kind, resolved_code) for one operand.

    kind ∈ code | sentinel | name | case_variant_code | case_variant_name | case_variant_sentinel | unknown
    resolved_code is the OncoTree code the operand SHOULD be (None when unresolvable).
    """
    codes, names, sents, _d, lower = _tables()
    raw = atom_text.strip()
    text = unmask(raw, table)
    if text in sents:
        return "sentinel", text
    if text in codes:
        return "code", text
    if text in names:
        return "name", names[text]
    hit = lower.get(text.lower())
    if hit:
        kind, canonical_text = hit
        if kind == "code":
            return "case_variant_code", canonical_text
        if kind == "sentinel":
            return "case_variant_sentinel", canonical_text
        return "case_variant_name", names[canonical_text]
    return "unknown", None


def walk(node: Node, negated: bool = False, depth: int = 0):
    """Yield (node, negated, not_depth) for every node."""
    yield node, negated, depth
    if isinstance(node, Not):
        yield from walk(node.child, not negated, depth + 1)
    elif isinstance(node, Op):
        for kid in node.kids:
            yield from walk(kid, negated, depth)


def atoms(node: Node):
    """Yield (Atom, negated) for every operand."""
    for n, neg, _d in walk(node):
        if isinstance(n, Atom):
            yield n, neg


def disjoint(a: str, b: str) -> bool:
    """True if two OncoTree codes are mutually exclusive (neither is the other or an ancestor of it).

    A sentinel is treated as covering everything, so it is never disjoint from a code — otherwise every
    legitimate `Solid tumour AND NOT(MEL)` would be flagged.
    """
    if a in SENTINELS or b in SENTINELS:
        return False
    return a != b and not is_subcode(a, b) and not is_subcode(b, a)


# --------------------------------------------------------------------------- #
# Canonical form
# --------------------------------------------------------------------------- #
def canonicalise(node: Node, table: dict[str, str], *, drop_vacuous: bool = False) -> Node:
    """Rewrite to the canonical form: operands resolved to CODES, `NOT(A) AND NOT(B)` factored to
    `NOT(A OR B)`, commutative branches sorted, duplicates removed, redundant nesting flattened.

    `drop_vacuous` additionally removes an exclusion that is disjoint from every positive code in the
    same conjunction (a no-op to a matching engine) — this is the OPEN POLICY DECISION, so it is a flag,
    not a default.
    """
    if isinstance(node, Atom):
        _kind, code = classify(node.text, table)
        return Atom(code if code else unmask(node.text, table))
    if isinstance(node, Not):
        return Not(canonicalise(node.child, table, drop_vacuous=drop_vacuous))

    kids = [canonicalise(k, table, drop_vacuous=drop_vacuous) for k in node.kids]
    flat: list[Node] = []
    for k in kids:                                   # flatten same-operator nesting
        if isinstance(k, Op) and k.op == node.op:
            flat.extend(k.kids)
        else:
            flat.append(k)

    if node.op == "AND":
        negs = [k for k in flat if isinstance(k, Not)]
        pos = [k for k in flat if not isinstance(k, Not)]
        if drop_vacuous and pos:
            pos_codes = {a.text for k in pos for a, neg in atoms(k) if not neg}
            kept_negs = []
            for n in negs:
                body = _prune_vacuous(n.child, pos_codes)
                if body is not None:
                    kept_negs.append(Not(body))
            negs = kept_negs
        if len(negs) > 1:                             # factor NOT(a) AND NOT(b) -> NOT(a OR b)
            merged = Op("OR", _dedupe(sorted((n.child for n in negs), key=render)))
            negs = [Not(merged.kids[0] if len(merged.kids) == 1 else merged)]
        pos = _reduce_composite_entities(pos, table)
        flat = _dedupe(sorted(pos, key=render)) + negs
    else:
        flat = _absorb_subsumed_disjuncts(_dedupe(sorted(flat, key=render)))

    return flat[0] if len(flat) == 1 else Op(node.op, flat)


def _absorb_subsumed_disjuncts(disjuncts: list[Node]) -> list[Node]:
    """Boolean absorption: `X OR (X AND Y)` == `X`, so drop any disjunct whose conjuncts are a strict SUPERSET of
    another disjunct's — the extra conjuncts only narrow it, so it adds no patients.

    This is TERM containment, not hierarchy: it makes no claim about codes and holds under any matching model.
    Distinct from `log_or_redundant_ancestor` (`MBN OR BL`), which relies on the OncoTree hierarchy, decides
    WHICH term should survive, and is therefore left to a human.
    """
    if len(disjuncts) < 2:
        return disjuncts
    terms = [frozenset(render(k) for k in (d.kids if isinstance(d, Op) and d.op == "AND" else [d]))
             for d in disjuncts]
    keep = []
    for i, d in enumerate(disjuncts):
        if any(j != i and terms[j] < terms[i] for j in range(len(disjuncts))):
            continue                      # a strictly weaker disjunct already covers everything this one matches
        keep.append(d)
    return keep or disjuncts


def flatten_nested_nots(node: Node, table: dict[str, str]) -> Node:
    """Rewrite a non-whitelisted nested negation into the flat union form the matching engine consumes.

    The mapper's job is a FAITHFUL transcription, so for
        "solid tumours (excluding CNS tumours other than IDHwt glioblastoma)"
    the direct mapping is `Solid tumour AND NOT(BRAIN AND NOT(GB))`. Turning that into the engine's form is a
    mechanical consequence, not a second opinion (user, 2026-08-03):

        Solid tumour AND NOT(BRAIN AND NOT(GB))            direct mapping
        Solid tumour AND (NOT(BRAIN) OR GB)                De Morgan
        (Solid tumour AND NOT(BRAIN)) OR (Solid tumour AND GB)   distribute
        (Solid tumour AND NOT(BRAIN)) OR GB                absorb: GB is inside the excluded scope

    The absorption in the last step is sound STRUCTURALLY, not by a general rule: C sits inside `NOT(B AND NOT(C))`
    where B is being carved out of A, which only makes sense if B ⊆ A — so C ⊆ A too. General `A AND C -> C`
    absorption is NOT safe (the mapper may have meant A), which is why it is confined to this rewrite.

    The two whitelisted idioms (`SKIN AND NOT(MEL)`, `NSCLC AND NOT(LUSC)`) are left nested — the engine
    special-cases them.
    """
    from aus_trial_universe.tasks.eligibility.mapping.cancer_type.checks import _is_whitelisted_difference

    if isinstance(node, Atom):
        return node
    if isinstance(node, Not):
        return Not(flatten_nested_nots(node.child, table))
    kids = [flatten_nested_nots(k, table) for k in node.kids]
    if node.op != "AND":
        return Op("OR", kids)

    for i, kid in enumerate(kids):
        if not isinstance(kid, Not):
            continue
        inner = kid.child
        if not (isinstance(inner, Op) and inner.op == "AND") or _is_whitelisted_difference(inner, table):
            continue
        inner_neg = [k for k in inner.kids if isinstance(k, Not)]
        inner_pos = [k for k in inner.kids if not isinstance(k, Not)]
        if len(inner_neg) != 1 or not inner_pos:
            continue

        rest = kids[:i] + kids[i + 1:]
        b_part = inner_pos[0] if len(inner_pos) == 1 else Op("AND", inner_pos)
        c_part = inner_neg[0].child

        branch_not_b = _conj(rest + [Not(b_part)])
        branch_c = _conj_absorbing(rest, c_part, table)
        branches = [b for b in (branch_not_b, branch_c) if b is not None]
        if not branches:
            return Atom("")
        result = branches[0] if len(branches) == 1 else Op("OR", branches)
        return flatten_nested_nots(result, table)      # a rewrite can expose another
    return Op("AND", kids)


def _conj(parts: list[Node]) -> Node:
    parts = [p for p in parts if not (isinstance(p, Atom) and not p.text)]
    if not parts:
        return Atom("")
    return parts[0] if len(parts) == 1 else Op("AND", parts)


def _conj_absorbing(rest: list[Node], c_part: Node, table: dict[str, str]) -> Node | None:
    """`rest AND c_part`, dropping any positive conjunct that c_part is already inside, and returning None if the
    conjunction cannot be satisfied (two disjoint tumour types)."""
    from aus_trial_universe.tasks.eligibility.mapping.cancer_type.checks import _codes_of

    c_codes = _codes_of(c_part, table, negated=False)
    kept: list[Node] = []
    for part in rest:
        if isinstance(part, Not):
            kept.append(part)
            continue
        p_codes = _codes_of(part, table, negated=False)
        if not p_codes:
            continue
        if all(pc in SENTINELS or any(cc == pc or is_subcode(cc, pc) for cc in c_codes) for pc in p_codes):
            continue                                    # c_part is inside this scope -> the scope is redundant
        if c_codes and all(disjoint(cc, pc) for cc in c_codes for pc in p_codes):
            return None                                 # unsatisfiable -> this branch contributes nothing
        kept.append(part)
    return _conj(kept + [c_part])


def _reduce_composite_entities(parts: list[Node], table: dict[str, str]) -> list[Node]:
    """`SMAHN AND CMML2` -> `SMAHN`. A WHO entity defined BY co-occurrence has its own node, and the associated
    neoplasm's identity is not separately expressible — so ANDing the component is both unsatisfiable under
    "one tumour type per patient" and redundant. Mechanical, so it is done here rather than asked of the LLM.
    """
    from aus_trial_universe.tasks.eligibility.mapping.cancer_type.checks import _COMPOSITE_ENTITIES

    composites = {c for key in _COMPOSITE_ENTITIES for c in key}
    present = {c for p in parts for a, neg in atoms(p) if not neg
               for c in [classify(a.text, table)[1]] if c in composites}
    if not present:
        return parts
    kept = []
    for part in parts:
        codes = {classify(a.text, table)[1] for a, neg in atoms(part) if not neg}
        if codes and not (codes & present) and all(c and all(disjoint(c, p) for p in present) for c in codes):
            continue                      # a component ANDed onto the composite adds nothing expressible
        kept.append(part)
    return kept


def _prune_vacuous(body: Node, pos_codes: set[str]) -> Node | None:
    """Strip the no-op parts of a NOT() body: any OR-branch that is a plain code disjoint from every positive
    code in the conjunction. Returns None when nothing is left (the whole NOT() clause then goes).

    Pruning has to work BRANCH BY BRANCH, not clause by clause: a real exclusion list is usually mixed —
    `DLBCLNOS AND NOT(PCM OR EP OR SPB OR BL OR ...)` names some genuine subtypes of DLBCLNOS and some unrelated
    entities, and an all-or-nothing rule keeps the whole list because of the few genuine ones.

    Non-atom branches (an AND group, a nested NOT) are kept untouched — they need judgement, not a rewrite.
    A sentinel is never disjoint from anything, so `Solid tumour AND NOT(MEL)` survives intact.
    """
    def vacuous(node: Node) -> bool:
        return isinstance(node, Atom) and bool(pos_codes) and all(disjoint(node.text, pc) for pc in pos_codes)

    if isinstance(body, Op) and body.op == "OR":
        kept = [b for b in body.kids if not vacuous(b)]
        if not kept:
            return None
        return kept[0] if len(kept) == 1 else Op("OR", kept)
    return None if vacuous(body) else body


def _dedupe(nodes: list[Node]) -> list[Node]:
    seen, out = set(), []
    for n in nodes:
        r = render(n)
        if r not in seen:
            seen.add(r)
            out.append(n)
    return out


def render(node: Node, *, parent: str | None = None) -> str:
    """Render an AST back to the expression string, parenthesising only where precedence needs it."""
    if isinstance(node, Atom):
        return node.text
    if isinstance(node, Not):
        return f"NOT({render(node.child)})"
    body = f" {node.op} ".join(render(k, parent=node.op) for k in node.kids)
    needs = parent is not None and parent != node.op
    return f"({body})" if needs else body


def canonical_string(expr: str, *, drop_vacuous: bool = False) -> str:
    """Canonical rendering of an expression (empty string is returned unchanged; unparseable input is
    returned as-is so callers can still show it side-by-side)."""
    if not (expr or "").strip():
        return ""
    try:
        node, table = parse(expr)
    except ParseError:
        return expr
    return render(canonicalise(node, table, drop_vacuous=drop_vacuous))


def positive_codes(expression: str) -> set[str] | None:
    """The codes an expression asserts POSITIVELY, or None if it does not parse."""
    if not (expression or "").strip():
        return set()
    try:
        node, table = parse(expression)
    except ParseError:
        return None
    return {classify(a.text, table)[1] or a.text for a, negated in atoms(node) if not negated}


#: The two ways a mapping can get broader, in DESCENDING order of certainty that it is wrong.
BROADEN_SENTINEL = "sentinel"   # specific code(s) -> a sentinel. Never a legitimate unification outcome.
BROADEN_ANCESTOR = "ancestor"   # code -> its own OncoTree ancestor. SOMETIMES legitimate (see below).


def broadening(old: str, new: str) -> tuple[str, str] | None:
    """Is `new` a broader population than `old`? Returns `(kind, reason)` or None.

    The one predicate shared by the reconciler's R6 guard and the `mapping_drift` gate, so the rule the pipeline
    enforces and the rule the gate checks cannot drift apart. The KIND matters because our confidence differs, and
    severity should track confidence rather than flatten it:

    `BROADEN_SENTINEL` is unambiguous. Group reconciliation unifies to the most specific code COVERING every
    member, and a sentinel is never that unless a member already is one (in which case this does not fire). Every
    one of the nine regressions the 2026-08-05 audit found was of this kind (`ACYC` -> `Solid tumour`,
    `CERVIX OR OVARY OR UTERUS OR VULVA` -> `Solid tumour`). So it is rejected in-loop AND fails the gate.

    `BROADEN_ANCESTOR` is genuinely ambiguous: `IDC` -> `BREAST` for the value "metastatic breast cancer" is the
    CORRECT repair of an over-specification, and unifying a group whose members differ in specificity necessarily
    lands on a parent. But it is also how grade was lost (`ASTR2 OR ASTR3 OR ODG2 OR ODG3` -> `ASTR OR ODG`).
    Deciding needs the source wording, which no deterministic rule has — so it is allowed through and REPORTED
    (gate WARN) rather than blocked, and the guard never rejects it.

    Only broadening is policed at all: our locked Step-2 rule is to unify to the most specific covering code, so a
    move to something narrower may be a real refinement (`DIFG` -> `DMG`), whereas broadening an INCLUSION
    silently manufactures false matches.
    """
    op, np_ = positive_codes(old), positive_codes(new)
    if op is None or np_ is None or not op:
        return None                      # unparseable, or nothing to compare against
    if (np_ & set(SENTINELS)) and not (op & set(SENTINELS)):
        return (BROADEN_SENTINEL,
                f"specific code(s) {sorted(op)} replaced by sentinel {sorted(np_ & set(SENTINELS))}")
    ancestors = [(o, n) for o in op for n in np_
                 if n not in SENTINELS and o != n and is_subcode(o, n)]
    if ancestors:
        return (BROADEN_ANCESTOR,
                "; ".join(f"{o} replaced by its ancestor {n}" for o, n in sorted(ancestors)))
    return None


def broadens(old: str, new: str) -> str | None:
    """The reason `new` broadens `old`, of ANY kind, else None. Convenience wrapper over `broadening`."""
    found = broadening(old, new)
    return found[1] if found else None


def narrows(old: str, new: str) -> str | None:
    """Is `new` a strictly NARROWER population than `old`? Returns a reason, else None.

    Reported but never blocking: dropping an OR branch (`NSGCT OR SEM` -> `SEM`) restricts the population, which
    may be a genuine refinement or may be over-restriction. It is recoverable — a reviewing clinician sees the
    free text — whereas broadening silently manufactures matches, so only `broadens` gates."""
    op, np_ = positive_codes(old), positive_codes(new)
    if op is None or np_ is None or not op or broadening(old, new):
        return None
    orphaned = [o for o in op
                if o not in SENTINELS
                and not any(n in SENTINELS or n == o or is_subcode(o, n) for n in np_)]
    if orphaned:
        return f"positive code(s) {sorted(orphaned)} no longer covered"
    return None
