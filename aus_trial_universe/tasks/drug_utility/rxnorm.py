"""Deterministic RxNorm + ATC lookup (spec §6.1) — offline reuse of the RxNorm data, no judgement.

Resolves a canonical drug name to its RxNorm ingredient RXCUI and its WHO ATC code, straight from
RXNCONSO.RRF (ATC is integrated into RxNorm as the ``SAB=ATC`` source, so both come from one file). The
*judgement* — what canonical drug a raw name refers to — stays with the LLM; this only does the deterministic
identity lookup on the canonical name the LLM produced.

The RRF is read from the agentic-owned resource dir (`RXNORM_ROOT/current_version/RXNCONSO.RRF`, a
UMLS-licensed drop-in). Parsed once and cached (RXNCONSO is ~1.2M rows).
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from aus_trial_universe.core.paths import RXNORM_ROOT, current_version_dir

# RXNCONSO.RRF is pipe-delimited; 0-indexed columns used here:
_RXCUI, _LAT, _SAB, _TTY, _CODE, _STR = 0, 1, 11, 12, 13, 14
_INGREDIENT_TTYS = {"IN", "PIN"}   # ingredient / precise ingredient
_WS_RE = re.compile(r"\s+")


def _norm(name: str) -> str:
    return _WS_RE.sub(" ", (name or "").strip().lower())


def _parse_rrf(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Pure parse of an RXNCONSO.RRF -> (name -> ingredient RXCUI, RXCUI -> ATC code)."""
    name_to_rxcui: dict[str, str] = {}
    rxcui_to_atc: dict[str, str] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            c = line.split("|")
            if len(c) <= _STR:
                continue
            sab = c[_SAB]
            if sab == "ATC" and c[_CODE]:
                rxcui_to_atc.setdefault(c[_RXCUI], c[_CODE])
            elif sab == "RXNORM" and c[_LAT] == "ENG" and c[_TTY] in _INGREDIENT_TTYS:
                key = _norm(c[_STR])
                if key:
                    name_to_rxcui.setdefault(key, c[_RXCUI])
    return name_to_rxcui, rxcui_to_atc


@lru_cache(maxsize=1)
def _index() -> tuple[dict[str, str], dict[str, str]]:
    """(name -> RXCUI, RXCUI -> ATC) from the newest RXNCONSO.RRF. Cached (parsed once)."""
    return _parse_rrf(current_version_dir(RXNORM_ROOT) / "RXNCONSO.RRF")


def resolve_rxcui(name: str) -> str:
    """RxNorm ingredient RXCUI for a canonical drug name, or "" if not in RxNorm."""
    return _index()[0].get(_norm(name), "")


def atc_code_for(name: str) -> str:
    """WHO ATC code for a canonical drug name (via its RXCUI), or "" if none."""
    rxcui = resolve_rxcui(name)
    return _index()[1].get(rxcui, "") if rxcui else ""
