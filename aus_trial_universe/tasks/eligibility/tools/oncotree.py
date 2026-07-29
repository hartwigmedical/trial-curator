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
    """Reverse of ``oncotree_vocab``: ``{name: code}`` for every node + the sentinels (which map to themselves).
    Used by the Step-2 name->code repair to fix a code expression where the mapper leaked a NAME into the code
    field (e.g. ``Diffuse Large B-Cell Lymphoma, NOS`` -> ``DLBCLNOS``). First name wins on the rare name collision."""
    rev: dict[str, str] = {}
    for code, name in oncotree_vocab().items():
        rev.setdefault(name, code)
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


_TOKEN_RE = re.compile(r"[A-Z][A-Z0-9_]+")
_KEYWORDS = {"AND", "OR", "NOT"}


def invalid_codes(code_expr: str) -> list[str]:
    """Uppercase code-like tokens in ``code_expr`` that are NOT real OncoTree codes.

    Catches hallucinated codes. Sentinels ('Pan-cancer', 'Solid tumour') are mixed-case
    so they never match the token regex; boolean keywords are excluded explicitly.
    """
    allowed = valid_codes()
    seen: set[str] = set()
    bad: list[str] = []
    for tok in _TOKEN_RE.findall(code_expr or ""):
        if tok in _KEYWORDS or tok in allowed or tok in seen:
            continue
        seen.add(tok)
        bad.append(tok)
    return bad
