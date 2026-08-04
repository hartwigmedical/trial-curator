"""The EXHAUSTIVE OncoTree code-expression defect catalogue.

Every class the review found in the store, organised by the layer that can fix it. The point of making
this a single explicit table (rather than a pile of ad-hoc greps) is that "have we captured ALL the
issues?" becomes an answerable question: a defect either has an id here or it does not exist yet.

LAYERS
  lex_*  lexical   — is every operand a legal symbol?          -> deterministic repair
  syn_*  syntax    — is the expression well-formed & canonical? -> deterministic repair
  log_*  logic     — is it satisfiable, minimal, non-redundant? -> deterministic detect, some need judgement
  src_*  fidelity  — does it say what the SOURCE said?          -> needs the source cell (+ LLM to repair)
  xf_*   cross-field  — do name and code agree?                 -> deterministic
  cov_*  coverage  — is anything missing entirely?              -> deterministic

SEVERITY
  error  — must never ship (a wrong or unusable value)
  warn   — noise / non-canonical; correctness unaffected but it makes equal things look different
  review — cannot be judged without a human or an LLM reading the source
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from aus_trial_universe.tasks.eligibility.tools.oncotree_expr import (
    Atom,
    Not,
    Op,
    ParseError,
    atoms,
    balanced,
    canonical_string,
    classify,
    disjoint,
    parse,
    render,
    split_top,
    unmask,
    walk,
)
from aus_trial_universe.tasks.eligibility.tools.oncotree import SENTINELS, is_subcode

# --------------------------------------------------------------------------- #
# Catalogue
# --------------------------------------------------------------------------- #
CATALOGUE: dict[str, tuple[str, str, str]] = {
    # id                              layer      severity  description
    "lex_leaked_name":               ("lexical", "error",  "an OncoTree NAME sits in the code field instead of its CODE"),
    "lex_case_variant_code":         ("lexical", "error",  "operand is a real code in the wrong case (breast vs BREAST)"),
    "lex_case_variant_name":         ("lexical", "error",  "operand is a real name in the wrong case"),
    "lex_case_variant_sentinel":     ("lexical", "error",  "sentinel in the wrong case/spelling (solid tumour vs Solid tumour)"),
    "lex_unknown_operand":           ("lexical", "error",  "operand is neither a code, a name nor a sentinel (free text or hallucinated)"),
    "lex_catchall_node":             ("lexical", "error",  "a catch-all bucket node was used as if it were a tumour type"),
    "lex_whitespace_noise":          ("lexical", "warn",   "double spaces, or leading/trailing whitespace"),
    "syn_unparseable":               ("syntax",  "error",  "not a well-formed expression (unbalanced parens / dangling operator)"),
    "syn_empty_not":                 ("syntax",  "error",  "an empty NOT()"),
    "syn_nested_not":                ("syntax",  "error",  "a NOT() inside another NOT()"),
    "syn_multi_not_clauses":         ("syntax",  "warn",   "NOT(A) AND NOT(B) instead of the factored NOT(A OR B)"),
    "syn_redundant_outer_parens":    ("syntax",  "warn",   "the whole expression is wrapped in needless parentheses"),
    "syn_or_order_noncanonical":     ("syntax",  "warn",   "OR branches are not in canonical order"),
    "syn_ambiguous_precedence":      ("syntax",  "error",  "top-level OR and AND both unparenthesised — the exclusion mis-scopes"),
    "log_include_exclude_same":      ("logic",   "error",  "the same code is both included and excluded (X AND NOT(X))"),
    "log_duplicate_operand":         ("logic",   "warn",   "a code repeated inside one conjunction (X AND X)"),
    "log_subtype_and_parent":        ("logic",   "error",  "a subtype ANDed with its own OncoTree parent"),
    "log_sentinel_and_specific":     ("logic",   "error",  "a sentinel ANDed with a specific code"),
    "log_disjoint_and":              ("logic",   "error",  "two mutually exclusive tumour types ANDed positively — unsatisfiable"),
    "log_composite_entity_split":    ("logic",   "error",  "a composite WHO entity was ANDed from its parts instead "
                                                           "of using its own OncoTree node"),
    "log_disjoint_and_inside_not":   ("logic",   "error",  "NOT(A AND B) over two mutually exclusive types — excludes nothing"),
    "log_exclude_ancestor":          ("logic",   "error",  "an ancestor of a positive code is excluded (GB AND NOT(BRAIN)) — unsatisfiable"),
    "log_negation_only":             ("logic",   "error",  "no positive term at all — the cell states only what it is not"),
    "log_negated_sentinel":          ("logic",   "error",  "a sentinel is negated (NOT(Pan-cancer))"),
    "log_or_redundant_ancestor":     ("logic",   "warn",   "an OR branch is subsumed by another branch (BRAIN OR GB)"),
    # REMOVED 2026-08-03. `(Solid tumour AND NOT(BRAIN)) OR GB` is not a defect at all — it is the DERIVED engine
    # form of `Solid tumour AND NOT(BRAIN AND NOT(GB))` (De Morgan -> distribute -> absorb, see
    # expr.flatten_nested_nots). Flagging it as an error made the repairer rewrite it into a 30-code organ
    # enumeration; leaving it as a warning would fire benignly on every flattened value, and a warning that is
    # always noise is how people learn to ignore warnings.
    "log_vacuous_exclusion":         ("logic",   "warn",   "an excluded code is disjoint from every positive code — a no-op"),
    "xf_name_code_mismatch":         ("cross-field", "error", "oncotree_name does not mirror oncotree_code term-for-term"),
    "src_nontumour_exclusion":       ("fidelity", "review", "every source exclusion was non-tumour-type (history / CNS / mets / synchronous) yet a NOT() survived"),
    "src_qualifier_in_interpreted":  ("fidelity", "review", "the interpreted cell still carries inexpressible clinical text"),
    "src_empty_for_cancer":          ("coverage", "review", "mapping is empty though the source names a cancer"),
    "cov_unmapped_value":            ("coverage", "error",  "an interpreted value has no row in the map table"),
}


# --------------------------------------------------------------------------- #
# Nested-NOT whitelist (user, 2026-08-03)
# --------------------------------------------------------------------------- #
# A NOT() inside a NOT() is forbidden — EXCEPT for two named idioms the matching engine will special-case, because
# neither has a code of its own and both are common trial wording:
#     SKIN  AND NOT(MEL)    "non-melanomatous skin cancer"
#     NSCLC AND NOT(LUSC)   "non-squamous non-small cell lung cancer"
# So `Pan-cancer AND NOT(SKIN AND NOT(MEL))` is legal and `HGSOC AND NOT(UCEC AND NOT(UEC))` is not.
# Deliberately a CLOSED set: a new nested difference appearing in a future run should FAIL loudly and come back
# for a decision, not ship into an engine that has no carve-out for it. Extend only alongside the engine.
ALLOWED_NESTED_DIFFERENCES: frozenset[tuple[str, str]] = frozenset({("SKIN", "MEL"), ("NSCLC", "LUSC")})

# The only two nodes in the 897-code vocabulary that are CATCH-ALL BUCKETS rather than tumour types. Their names
# read as generic scope ("Mixed Cancer Types", "Other"), which is exactly the BRCA trap: a real code whose name
# superficially fits. An unspecified scope is a SENTINEL. Checked here rather than left to the prompt because a
# closed two-item ban is mechanical and a validator cannot forget it.
# NB every OTHER generic-sounding node was verified legitimate and specific — MPAL*, MGCT, MFH, CUP, MXOV, MNET.
CATCHALL_NODES: frozenset[str] = frozenset({"MIXED", "OTHER"})

# WHO entities DEFINED by the co-occurrence of two neoplasms, where OncoTree already has a node for the composite.
# "A patient has one tumour type" holds for these too — the one type IS the composite — so ANDing the parts is
# both unsatisfiable and unnecessary. Found live: `SMAHN AND MDSEB`, `SMAHN AND CMML2`, `SMAHN AND MDS`.
_COMPOSITE_ENTITIES: dict[frozenset[str], str] = {
    frozenset({"SMAHN"}): "SMAHN (Systemic Mastocytosis with an Associated Hematologic Neoplasm)",
}


def _is_whitelisted_difference(node, table) -> bool:
    """True if `node` is exactly `X AND NOT(Y)` with (X, Y) a whitelisted idiom."""
    if not (isinstance(node, Op) and node.op == "AND" and len(node.kids) == 2):
        return False
    positives = [k for k in node.kids if not isinstance(k, Not)]
    negatives = [k for k in node.kids if isinstance(k, Not)]
    if len(positives) != 1 or len(negatives) != 1:
        return False
    if not (isinstance(positives[0], Atom) and isinstance(negatives[0].child, Atom)):
        return False
    _kp, parent = classify(positives[0].text, table)
    _kc, child = classify(negatives[0].child.text, table)
    return (parent, child) in ALLOWED_NESTED_DIFFERENCES


def _offending_nested_nots(body, table) -> list:
    """The NOT() nodes inside a NOT() body that are NOT part of a whitelisted difference."""
    excused: set[int] = set()
    for sub, _neg, _d in walk(body):
        if _is_whitelisted_difference(sub, table):
            excused.update(id(k) for k in sub.kids if isinstance(k, Not))
    return [n for n, _neg, _d in walk(body) if isinstance(n, Not) and id(n) not in excused]


@dataclass
class Finding:
    value: str
    defect: str
    detail: str = ""

    @property
    def layer(self) -> str:
        return CATALOGUE[self.defect][0]

    @property
    def severity(self) -> str:
        return CATALOGUE[self.defect][1]


@dataclass
class ValueReport:
    value: str
    findings: list[Finding] = field(default_factory=list)
    canonical: str = ""
    canonical_no_vacuous: str = ""

    def add(self, defect: str, detail: str = "") -> None:
        self.findings.append(Finding(self.value, defect, detail))

    @property
    def defects(self) -> list[str]:
        return list(dict.fromkeys(f.defect for f in self.findings))


# --------------------------------------------------------------------------- #
# Per-expression checks
# --------------------------------------------------------------------------- #
def check_expression(expr: str) -> ValueReport:
    """Run every deterministic check that needs only the code expression itself."""
    rep = ValueReport(value=expr)
    raw = expr or ""
    if not raw.strip():
        return rep

    if raw != raw.strip() or "  " in raw:
        rep.add("lex_whitespace_noise", repr(raw[:60]))

    try:
        node, table = parse(raw)
    except ParseError as exc:
        rep.add("syn_unparseable", str(exc))
        return rep

    # ---- lexical ----
    for atom, _neg in atoms(node):
        kind, code = classify(atom.text, table)
        shown = unmask(atom.text, table)
        if kind == "name":
            rep.add("lex_leaked_name", f"{shown!r} -> {code}")
        elif kind == "case_variant_code":
            rep.add("lex_case_variant_code", f"{shown!r} -> {code}")
        elif kind == "case_variant_name":
            rep.add("lex_case_variant_name", f"{shown!r} -> {code}")
        elif kind == "case_variant_sentinel":
            rep.add("lex_case_variant_sentinel", f"{shown!r} -> {code}")
        elif kind == "unknown":
            rep.add("lex_unknown_operand", repr(shown[:60]))
        if code in CATCHALL_NODES:
            rep.add("lex_catchall_node", f"{code} ({shown!r}) is a catch-all bucket; use a sentinel instead")

    # ---- syntax ----
    for n, _neg, _d in walk(node):
        if isinstance(n, Not) and isinstance(n.child, Atom) and not n.child.text.strip():
            rep.add("syn_empty_not")
        if isinstance(n, Not) and _offending_nested_nots(n.child, table):
            rep.add("syn_nested_not", render(n))
        if isinstance(n, Op) and n.op == "AND" and sum(isinstance(k, Not) for k in n.kids) > 1:
            rep.add("syn_multi_not_clauses", f"{sum(isinstance(k, Not) for k in n.kids)} NOT() clauses")

    stripped = raw.strip()
    if stripped.startswith("(") and stripped.endswith(")") and balanced(stripped[1:-1]):
        rep.add("syn_redundant_outer_parens")

    masked_top = _masked(raw)
    if len(split_top(masked_top, "OR")) > 1 and len(split_top(masked_top, "AND")) > 1:
        rep.add("syn_ambiguous_precedence")

    canon = render(_canon(node, table, False))
    rep.canonical = canon
    rep.canonical_no_vacuous = render(_canon(node, table, True))
    if canon != stripped and _same_terms(canon, stripped):
        rep.add("syn_or_order_noncanonical", f"{stripped} => {canon}")

    # ---- logic ----
    _logic_checks(rep, node, table)
    return rep


def _masked(raw: str) -> str:
    from aus_trial_universe.tasks.eligibility.tools.oncotree_expr import mask_names
    return mask_names(raw)[0]


def _canon(node, table, drop_vacuous: bool):
    from aus_trial_universe.tasks.eligibility.tools.oncotree_expr import canonicalise
    return canonicalise(node, table, drop_vacuous=drop_vacuous)


def _same_terms(a: str, b: str) -> bool:
    """Do two renderings use the same multiset of word characters? (cheap 'only the ORDER differs' test)"""
    return sorted(re.findall(r"[A-Za-z0-9_]+", a)) == sorted(re.findall(r"[A-Za-z0-9_]+", b))


def _codes_of(node, table, *, negated: bool | None = None) -> set[str]:
    out = set()
    for atom, neg in atoms(node):
        if negated is not None and neg != negated:
            continue
        _kind, code = classify(atom.text, table)
        if code:
            out.add(code)
    return out


def _alternatives(kid, table) -> set[str]:
    """The codes a single AND-conjunct can be satisfied by.

    A conjunct is often an OR-group — `(A OR B) AND NOT(C)` — and its branches are ALTERNATIVES, not
    co-requirements. Collapsing them into one flat positive set (the mistake the first pass made) makes
    every OR-group look like a pile of mutually exclusive types ANDed together.
    """
    return _codes_of(kid, table, negated=False)


def _logic_checks(rep: ValueReport, node, table) -> None:
    pos_all = _codes_of(node, table, negated=False)
    neg_all = _codes_of(node, table, negated=True)

    if not pos_all and neg_all:
        rep.add("log_negation_only", f"only excludes {sorted(neg_all)}")
    if neg_all & set(SENTINELS):
        rep.add("log_negated_sentinel", ", ".join(sorted(neg_all & set(SENTINELS))))

    # ---- per AND-conjunction ----
    for n, _neg, _d in walk(node):
        if not (isinstance(n, Op) and n.op == "AND"):
            continue
        conjuncts = [(_alternatives(k, table), k) for k in n.kids if not isinstance(k, Not)]
        conjuncts = [(alts, k) for alts, k in conjuncts if alts]

        for i, (ai, _ki) in enumerate(conjuncts):
            for j, (aj, _kj) in enumerate(conjuncts[i + 1:], start=i + 1):
                if ai == aj:
                    rep.add("log_duplicate_operand", ", ".join(sorted(ai)))
                    continue
                # unsatisfiable only if NO alternative on the left is compatible with any on the right
                if all(disjoint(a, b) for a in ai for b in aj):
                    composite = _COMPOSITE_ENTITIES.get(frozenset(ai) | frozenset(aj))
                    if composite is None:
                        composite = next((c for k, c in _COMPOSITE_ENTITIES.items() if k & (ai | aj)), None)
                    if composite:
                        rep.add("log_composite_entity_split",
                                f"{sorted(ai | aj)} is the entity {composite}; use that node alone")
                    else:
                        rep.add("log_disjoint_and",
                                f"{' OR '.join(sorted(ai))} AND {' OR '.join(sorted(aj))} cannot both hold")
                elif all(is_subcode(a, b) for a in ai for b in aj):
                    rep.add("log_subtype_and_parent", f"{sorted(ai)} ⊂ {sorted(aj)}")
                elif all(is_subcode(b, a) for a in ai for b in aj):
                    rep.add("log_subtype_and_parent", f"{sorted(aj)} ⊂ {sorted(ai)}")

        sent_conj = [alts for alts, _k in conjuncts if alts <= set(SENTINELS)]
        spec_conj = [alts for alts, _k in conjuncts if alts and not (alts & set(SENTINELS))]
        if sent_conj and spec_conj:
            rep.add("log_sentinel_and_specific",
                    f"{sorted(sent_conj[0])} AND {sorted(spec_conj[0])}")

        pos_here = {c for alts, _k in conjuncts for c in alts}
        # Codes at ODD polarity inside each NOT() — walking from the Not node itself makes its child
        # negated and a nested NOT flip back, so a double negative is correctly read as positive.
        neg_here = {c for k in n.kids if isinstance(k, Not)
                    for c in _codes_of(k, table, negated=True)}
        both = pos_here & neg_here
        if both:
            rep.add("log_include_exclude_same", ", ".join(sorted(both)))
        for nc in neg_here - both:
            if any(is_subcode(pc, nc) for pc in pos_here):
                rep.add("log_exclude_ancestor", f"NOT({nc}) excludes an ancestor of {sorted(pos_here)}")
        if pos_here and neg_here:
            vac = sorted(nc for nc in neg_here
                         if nc not in SENTINELS and all(disjoint(nc, pc) for pc in pos_here))
            if vac:
                rep.add("log_vacuous_exclusion", ", ".join(vac))

    # ---- NOT(A AND B) over disjoint types ----
    for n, _neg, _d in walk(node):
        if isinstance(n, Not) and isinstance(n.child, Op) and n.child.op == "AND":
            per_kid = [_codes_of(k, table) for k in n.child.kids]
            per_kid = [s for s in per_kid if s]
            if len(per_kid) > 1 and all(
                all(disjoint(a, b) for a in per_kid[i] for b in per_kid[j])
                for i in range(len(per_kid)) for j in range(i + 1, len(per_kid))
            ):
                rep.add("log_disjoint_and_inside_not",
                        " AND ".join(" OR ".join(sorted(s)) for s in per_kid))

    # ---- OR-branch relationships ----
    for n, _neg, _d in walk(node):
        if not (isinstance(n, Op) and n.op == "OR"):
            continue
        branches = [(_codes_of(k, table, negated=False), _codes_of(k, table, negated=True))
                    for k in n.kids]
        for i, (pi, _ni) in enumerate(branches):
            for j, (pj, nj) in enumerate(branches):
                if i == j or not pi:
                    continue
                if pj and pi != pj and all(any(is_subcode(c, x) for x in pj) for c in pi):
                    rep.add("log_or_redundant_ancestor", f"{sorted(pi)} ⊂ {sorted(pj)}")


# --------------------------------------------------------------------------- #
# Cross-field: does oncotree_name mirror oncotree_code?
# --------------------------------------------------------------------------- #
def check_name_code(name_expr: str, code_expr: str) -> list[str]:
    """Return a problem detail if the NAME rendering does not mirror the CODE rendering."""
    if not (name_expr or "").strip() or not (code_expr or "").strip():
        return []
    try:
        n_node, n_tab = parse(name_expr)
        c_node, c_tab = parse(code_expr)
    except ParseError:
        return ["one side is unparseable"]
    n_codes = sorted(_codes_of(n_node, n_tab))
    c_codes = sorted(_codes_of(c_node, c_tab))
    if n_codes != c_codes:
        return [f"name resolves to {n_codes}, code to {c_codes}"]
    if _skeleton(n_node) != _skeleton(c_node):
        return [f"structure differs: {_skeleton(n_node)} vs {_skeleton(c_node)}"]
    return []


def _skeleton(node) -> str:
    if isinstance(node, Atom):
        return "."
    if isinstance(node, Not):
        return f"!({_skeleton(node.child)})"
    return "(" + node.op[0] + ":" + ",".join(sorted(_skeleton(k) for k in node.kids)) + ")"


# --------------------------------------------------------------------------- #
# Source-fidelity heuristics (flag for LLM/human review, never auto-repaired)
# --------------------------------------------------------------------------- #
_NON_TUMOUR_EXCLUSION = re.compile(
    r"(history of (malignan|cancer)|prior malignan|other malignan|second(ary)? (primary )?malignan|"
    r"previous (diagnosis of )?(malignan|cancer)|synchronous|concurrent malignan|brain metasta|"
    r"cns metasta|cns involve|leptomening|cns disease|cns only|carcinomatous meningitis|"
    r"central nervous system (involve|metasta|disease))", re.I)

_NOT_BODIES = re.compile(r"NOT\(([^()]*(?:\([^()]*\)[^()]*)*)\)")

# Deliberately TIGHT. The interpreted cell is meant to read as clinical prose, so stage / grade /
# "metastatic" / "relapsed" are CORRECT there and must not fire — dropping them is the MAPPER's job.
# What does not belong in a cancer_type cell at all is a non-tumour ENTITY or a numeric threshold
# standing as its own conjunct: `sarcomatoid histology <=30%`, `NOT(active brain metastases)`.
_NOT_A_TUMOUR_CONJUNCT = re.compile(
    r"(\d+\s*%|[<>]=?\s*\d|\bECOG\b|performance status|life expectancy|"
    r"(active|untreated|symptomatic|known)\s+(brain|CNS|cerebral)\s+metasta|"
    r"\bleptomening|carcinomatous meningitis|CNS involvement|CNS[- ]only disease|"
    r"history of (malignan|cancer)|prior malignan|second(ary)? primary|synchronous primary)", re.I)


def check_source_fidelity(source: str, code_expr: str) -> list[tuple[str, str]]:
    """Heuristic (source, code) checks. Returns [(defect_id, detail)] — all severity `review`."""
    out: list[tuple[str, str]] = []
    bodies = _NOT_BODIES.findall(source or "")
    if bodies and "NOT(" in (code_expr or ""):
        if all(_NON_TUMOUR_EXCLUSION.search(b) for b in bodies):
            out.append(("src_nontumour_exclusion",
                        "source exclusions: " + " | ".join(b[:50] for b in bodies[:3])))
    # An empty mapping is CORRECT for a non-cancer value, so only flag it when the source actually
    # names a malignancy in a POSITIVE position (a cell that is nothing but NOT(...) is not one).
    if not (code_expr or "").strip() and source.strip():
        positive = _NOT_BODIES.sub(" ", source)
        if _CANCER_WORD.search(positive):
            out.append(("src_empty_for_cancer", positive.strip()[:80]))
    return out


_CANCER_WORD = re.compile(
    r"\b(cancer|carcinoma|sarcoma|melanoma|lymphoma|leukemia|leukaemia|myeloma|glioma|glioblastoma|"
    r"blastoma|tumour|tumor|malignan|neoplas|mesothelioma|adenocarcinoma|myelodysplas)", re.I)


def check_interpreted_cell(interpreted: str) -> list[tuple[str, str]]:
    """The #14 class: a conjunct of the interpreted cancer_type cell that is not a tumour-type statement
    at all. It ships to the matching engine in the export's `cancer_type_interpreted` column, so it is a
    defect in the deliverable even when the mapped CODE correctly ignored it."""
    text = interpreted or ""
    offenders: list[str] = []
    for part in _conjuncts_of(text):
        for m in _NOT_A_TUMOUR_CONJUNCT.finditer(part):
            lo, hi = max(0, m.start() - 18), min(len(part), m.end() + 18)
            offenders.append(f"…{part[lo:hi].strip()}…")
    if offenders:
        return [("src_qualifier_in_interpreted", " | ".join(dict.fromkeys(offenders))[:160])]
    return []


def _conjuncts_of(text: str) -> list[str]:
    """Split the interpreted cell into its AND-conjuncts, keeping each NOT(...) body whole."""
    parts, depth, start, i = [], 0, 0, 0
    while i < len(text):
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and text.startswith(" AND ", i):
            parts.append(text[start:i])
            i += 5
            start = i
            continue
        i += 1
    parts.append(text[start:])
    return [p for p in parts if p.strip()]
