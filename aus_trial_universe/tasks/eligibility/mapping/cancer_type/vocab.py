"""OncoTree reference vocabulary for the mapping task.

Loads the curated OncoTree hierarchy (the VITAL resource
`data/agentic/resources/eligibility/oncotree/current_version/oncotree.yaml`) — a nested tree of
``{code, name, level, children}`` — and derives everything the mapping stage needs from it:
(a) a ``{code: name}`` vocabulary, (b) the allowed code set (for validating that emitted codes are genuine),
(c) the ancestor sets (parent→child from the nesting, for granularity checks), and (d) a compact INDENTED
``Name (CODE)`` tree that grounds the mapper/reviewer prompt — indentation carries the subtype hierarchy the
granularity rules depend on, which a flat list cannot. The pseudo-codes below mirror the legacy sentinels for
broad / any-solid / non-cancer scopes. (The YAML is the single source; the sibling ``oncotree.csv`` is only used
by the legacy `eligibility_path` tree.)
"""
from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from pathlib import Path

from aus_trial_universe.core.paths import ONCOTREE_ROOT, current_version_dir


def _oncotree_yaml() -> Path:
    """Path to the live OncoTree YAML (resolved lazily so importing this module never requires the file)."""
    return current_version_dir(ONCOTREE_ROOT) / "oncotree.yaml"


PAN_CANCER = "Pan-cancer"                       # any cancer (solid + haematological)
SOLID_TUMOUR = "Solid tumour"                   # any solid tumour
HAEM_MALIGNANCY = "Haematological malignancy"   # any blood / lymphoid cancer
# The ONLY three permitted non-OncoTree terms. (No "[None]": a non-cancer value must not
# appear in cancer_type at all — leave it empty and let the extraction reviewer catch it.)
SENTINELS = (PAN_CANCER, SOLID_TUMOUR, HAEM_MALIGNANCY)


@functools.lru_cache(maxsize=1)
def _tree() -> list[dict]:
    """Parse the OncoTree YAML once (a list of nested ``{code, name, level, children}`` nodes)."""
    import yaml  # lazy: importing this module stays dependency-free until the vocab is actually used
    with open(_oncotree_yaml(), encoding="utf-8") as f:
        return yaml.safe_load(f)


def _iter_nodes(nodes: list[dict] | None = None, depth: int = 0, ancestors: tuple[str, ...] = ()):
    """Yield ``(code, name, depth, ancestor_codes)`` for every node, depth-first (parent BEFORE its children,
    siblings in file order) — so a rendering preserves the tree's natural top-down structure."""
    if nodes is None:
        nodes = _tree()
    for n in nodes:
        yield n["code"], n["name"], depth, ancestors
        kids = n.get("children")
        if kids:
            yield from _iter_nodes(kids, depth + 1, ancestors + (n["code"],))


@functools.lru_cache(maxsize=1)
def oncotree_vocab() -> dict[str, str]:
    """``{code: name}`` for every OncoTree node (~897)."""
    vocab: dict[str, str] = {}
    for code, name, _d, _a in _iter_nodes():
        vocab.setdefault(code, name)
    return vocab


@functools.lru_cache(maxsize=1)
def valid_codes() -> frozenset[str]:
    """Every real OncoTree code plus the sentinels — the allowed code set."""
    return frozenset(oncotree_vocab()) | frozenset(SENTINELS)


@functools.lru_cache(maxsize=1)
def name_to_code() -> dict[str, str]:
    """Reverse of ``oncotree_vocab``: ``{name: code}`` for every UNAMBIGUOUSLY-named node + the sentinels (which
    map to themselves). Used by the Step-2 name->code repair to fix a code expression where the mapper leaked a
    NAME into the code field (e.g. ``Diffuse Large B-Cell Lymphoma, NOS`` -> ``DLBCLNOS``).

    A name shared by several codes is OMITTED rather than resolved. 9 of the 897 names are germ-cell / teratoma
    entities that recur under different organ trees (``Choriocarcinoma`` is BCCA in BRAIN, TCCA in TESTIS, UCCA in
    UTERUS), so no code is inferable from the name alone. Picking one — this used to be "first name wins" — turned
    a flagged ``lex_leaked_name`` into a clean, WRONG-ORGAN code, and because the display name is rendered back
    FROM that code, the export then showed the very name the source used, so the substitution was invisible in the
    one column a human would check. Omitted, the operand is ``lex_unknown_operand``: still an error, still blocking
    at the stage-1 gate, but carrying NO suggested substitution — so the mapper is never TOLD to write the wrong
    organ (`checks.py` folds the resolved code into the `lex_leaked_name` message, which becomes the doer's
    revision feedback).
    """
    by_name: dict[str, list[str]] = {}
    for code, name in oncotree_vocab().items():
        by_name.setdefault(name, []).append(code)
    rev = {name: codes[0] for name, codes in by_name.items() if len(codes) == 1}
    for s in SENTINELS:
        rev.setdefault(s, s)
    return rev


@functools.lru_cache(maxsize=1)
def oncotree_ancestors() -> dict[str, frozenset[str]]:
    """``{code: ancestor codes}`` straight from the YAML nesting (a node's ancestors are the codes on the path
    from the root down to — but excluding — it)."""
    return {code: frozenset(anc) for code, _n, _d, anc in _iter_nodes()}


def is_subcode(a: str, b: str) -> bool:
    """True if OncoTree code ``a`` is a descendant (subtype) of code ``b``."""
    return b in oncotree_ancestors().get(a, frozenset())


@functools.lru_cache(maxsize=1)
def vocab_reference() -> str:
    """A compact INDENTED ``Name (CODE)`` tree for the mapper/reviewer prompt — indentation = the subtype
    hierarchy (a child node is a subtype of its parent), so granularity is visible at a glance."""
    return "\n".join(f"{'  ' * d}{name} ({code})" for code, name, d, _a in _iter_nodes())


# --------------------------------------------------------------------------- #
# EXPRESSION LAYER (2026-08-04)
# --------------------------------------------------------------------------- #
# `invalid_codes()` used to live here: a regex over ALL-CAPS runs. It was blind by construction — mixed case is
# skipped so the sentinels pass, but OncoTree NAMES are mixed-case too, so a leaked name like
# `Pancreatic Adenocarcinoma` returned no problems, and the Step-2 name->code repair (gated on it) never fired.
# It is replaced by a real parser. See `oncotree_expr` (grammar + canonical form) and `oncotree_checks`
# (the defect catalogue). Spec: docs/planning/archive/v2_oncotree_correction_spec.md.


@dataclass(frozen=True)
class Problem:
    """One catalogued defect in a code expression."""

    defect: str
    severity: str      # error | warn | review
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.defect}: {self.detail}" if self.detail else self.defect


def _checks():
    from aus_trial_universe.tasks.eligibility.mapping.cancer_type import checks as oncotree_checks
    return oncotree_checks


def _expr():
    from aus_trial_universe.tasks.eligibility.mapping.cancer_type import expr as oncotree_expr
    return oncotree_expr


def catalogue() -> dict[str, tuple[str, str, str]]:
    """{defect id -> (layer, severity, description)} — the closed set of things that can be wrong."""
    return _checks().CATALOGUE


def normalise_code_expression(expression: str) -> str:
    """Resolve every operand to an OncoTree CODE, structure untouched.

    Accepts a code, an OncoTree NAME, or a case-variant of either. This is the "if a name is chosen the code is
    selected too" half of the name/code invariant. An unresolvable operand is left verbatim so
    `expression_problems` reports it rather than it vanishing silently.
    """
    text = (expression or "").strip()
    if not text:
        return ""
    ex = _expr()
    try:
        node, table = ex.parse(text)
    except ex.ParseError:
        return text
    return ex.render(_to_codes(node, table))


def _to_codes(node, table):
    ex = _expr()
    if isinstance(node, ex.Atom):
        _kind, code = ex.classify(node.text, table)
        return ex.Atom(code or ex.unmask(node.text, table))
    if isinstance(node, ex.Not):
        return ex.Not(_to_codes(node.child, table))
    return ex.Op(node.op, [_to_codes(k, table) for k in node.kids])


def render_name_expression(code_expression: str) -> str:
    """Render a CODE expression as the matching NAME expression, structure preserved.

    The ONLY way an `oncotree_name` is produced anywhere in the pipeline — a name is never authored by the LLM,
    so the two cannot disagree (user requirement, 2026-08-03).
    """
    text = (code_expression or "").strip()
    if not text:
        return ""
    vocab = oncotree_vocab()
    if text in vocab:                        # single-code fast path (the common case)
        return vocab[text]
    ex = _expr()
    try:
        node, table = ex.parse(text)
    except ex.ParseError:
        return text
    return ex.render(_to_names(node, table, vocab))


def _to_names(node, table, vocab):
    ex = _expr()
    if isinstance(node, ex.Atom):
        _kind, code = ex.classify(node.text, table)
        shown = ex.unmask(node.text, table)
        return ex.Atom(vocab.get(code, shown) if code else shown)
    if isinstance(node, ex.Not):
        return ex.Not(_to_names(node.child, table, vocab))
    return ex.Op(node.op, [_to_names(k, table, vocab) for k in node.kids])


_MAX_REWRITE_PASSES = 5


def canonical_form(expression: str, *, drop_vacuous: bool = True) -> str:
    """The canonical rendering: operands as codes, `NOT(A) AND NOT(B)` factored to `NOT(A OR B)`, OR branches
    sorted, duplicates removed, nesting flattened, redundant parens stripped, non-whitelisted nested negation
    rewritten to the engine form, and (default) vacuous exclusions dropped.

    Iterates to a FIXED POINT: the rewrites expose work for each other (pruning a vacuous exclusion can reveal a
    flattenable nested NOT), and a fixed point is what makes the pass idempotent.
    """
    text = (expression or "").strip()
    if not text:
        return ""
    ex = _expr()
    try:
        node, table = ex.parse(text)
    except ex.ParseError:
        return text
    for _ in range(_MAX_REWRITE_PASSES):
        node = _resolve_double_negation(node)
        node = ex.flatten_nested_nots(node, table)
        node = ex.canonicalise(node, table, drop_vacuous=drop_vacuous)
        rendered = ex.render(node)
        if rendered == text:
            break
        text = rendered
        try:
            node, table = ex.parse(text)
        except ex.ParseError:
            break
    return text


def _resolve_double_negation(node):
    """`NOT(NOT(X))` -> `X`. Only the DIRECT case; `NOT(A AND NOT(B))` is a set difference, handled by
    `flatten_nested_nots`."""
    ex = _expr()
    if isinstance(node, ex.Not):
        inner = _resolve_double_negation(node.child)
        return inner.child if isinstance(inner, ex.Not) else ex.Not(inner)
    if isinstance(node, ex.Op):
        return ex.Op(node.op, [_resolve_double_negation(k) for k in node.kids])
    return node


def expression_problems(expression: str) -> list[Problem]:
    """Every defect in one code expression. THE shared gate — used by the mapper's check, the refinement's
    check, `qa/validate_output.py` and the `oncotree_expressions` production gate."""
    report = _checks().check_expression(expression or "")
    return [Problem(f.defect, f.severity, f.detail) for f in report.findings]


def source_problems(source: str, expression: str) -> list[Problem]:
    """Every defect visible only by comparing the mapping with the SOURCE value it was made from.

    Companion to `expression_problems`, which is deliberately expression-only so it can be reused where no source
    is at hand. Kept separate for that reason, not because the source is optional: wherever the source IS
    available — the stage-1 mapper's own gate above all — both should run. Until 2026-08-05 these checks ran only
    in `qa/gates.py`, i.e. after the value had already shipped.
    """
    report = _checks().check_against_source(source or "", expression or "")
    return [Problem(f.defect, f.severity, f.detail) for f in report.findings]


def has_error(problems: list[Problem]) -> bool:
    """True if any problem is severity `error` — the class that must never ship."""
    return any(p.severity == "error" for p in problems)
