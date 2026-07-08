"""OncoTree reference vocabulary for the mapping task.

Loads the curated OncoTree hierarchy (the VITAL resource
`data/eligibility_path/resources/oncotree/oncotree.csv`) into a ``{code: name}``
vocabulary used to (a) ground the OncoTree mapper prompt with the real, valid code
set and (b) validate that emitted codes are genuine. The pseudo-codes below mirror
the legacy sentinels for broad / any-solid / non-cancer scopes.
"""
from __future__ import annotations

import csv
import functools
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ONCOTREE_CSV = REPO_ROOT / "data/eligibility_path/resources/oncotree/oncotree.csv"

_CODE_RE = re.compile(r"\(([^()]+)\)\s*$")   # trailing "(CODE)" in a "Name (CODE)" cell
_LEVELS = [f"level_{i}" for i in range(1, 8)]

PAN_CANCER = "Pan-cancer"       # any cancer (solid + haematological)
SOLID_TUMOUR = "Solid tumour"   # any solid tumour
NONE_SENTINEL = "[None]"        # non-cancer / not a tumour type
SENTINELS = (PAN_CANCER, SOLID_TUMOUR, NONE_SENTINEL)


@functools.lru_cache(maxsize=1)
def oncotree_vocab() -> dict[str, str]:
    """``{code: name}`` for every OncoTree node (~865)."""
    vocab: dict[str, str] = {}
    with open(ONCOTREE_CSV, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            for lvl in _LEVELS:
                cell = (row.get(lvl) or "").strip()
                m = _CODE_RE.search(cell)
                if m:
                    vocab.setdefault(m.group(1), cell[: m.start()].strip())
    return vocab


@functools.lru_cache(maxsize=1)
def valid_codes() -> frozenset[str]:
    """Every real OncoTree code plus the sentinels — the allowed code set."""
    return frozenset(oncotree_vocab()) | frozenset(SENTINELS)


@functools.lru_cache(maxsize=1)
def vocab_reference() -> str:
    """A compact ``CODE<TAB>Name`` block for the mapper prompt (the authoritative codes)."""
    return "\n".join(f"{code}\t{name}" for code, name in sorted(oncotree_vocab().items()))


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
