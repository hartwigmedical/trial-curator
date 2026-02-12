from __future__ import annotations

import argparse
import ast
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from aus_trial_universe.ctgov.utils.ii_normalisation import (
    fix_mojibake_str,
    is_effectively_empty,
    norm,
)
from aus_trial_universe.ctgov.utils.iii_csv_mapping_file import load_resource_csv
from aus_trial_universe.ctgov.utils.v_traverse_oncotree import (
    build_term_to_level_index,
    levels_for_terms,
    split_or_terms,
)

logger = logging.getLogger(__name__)

TRIAL_ID_COL = "nctId"
CONDITIONS_COL = "conditions"

CONDITIONS_LOOKUP_COL = "Conditions_lookup"
ONCOTREE_CURATION_COL = "Oncotree_curation"


# =============================================================================
# Normalisation helpers (style-aligned with primary tumour overwrite)
# =============================================================================

def _norm_cell(x: Any) -> str:
    if is_effectively_empty(x):
        return ""
    return norm(fix_mojibake_str(str(x)))


def _is_blank_or_none_marker(x: Any) -> bool:
    """
    Treat empty-ish or literal '[None]' (case/space tolerant) as blank.
    """
    if is_effectively_empty(x):
        return True
    return fix_mojibake_str(str(x)).strip().lower() == "[none]"


# =============================================================================
# Mapping construction
# =============================================================================

def build_conditions_map(mapping_csv: Path) -> Dict[str, str]:
    """
    Build lookup:
        norm(Conditions_lookup) -> raw Oncotree_curation (stripped, mojibake-fixed)

    Notes:
    - last-one-wins on duplicate keys (warn)
    """
    df = load_resource_csv(mapping_csv)
    required = [CONDITIONS_LOOKUP_COL, ONCOTREE_CURATION_COL]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Mapping CSV missing required columns: {missing}")

    out: Dict[str, str] = {}
    for _, row in df.iterrows():
        key = _norm_cell(row.get(CONDITIONS_LOOKUP_COL))
        if not key:
            continue

        raw = row.get(ONCOTREE_CURATION_COL)
        cur_str = "" if is_effectively_empty(raw) else fix_mojibake_str(str(raw)).strip()

        if key in out and out[key] != cur_str:
            logger.warning(
                "Duplicate Conditions_lookup %r with differing curation; last-one-wins",
                key,
            )

        out[key] = cur_str

    return out


# =============================================================================
# Conditions parsing
# =============================================================================

def parse_conditions_cell(cell: Any) -> List[str]:
    """
    Parse ctgov_field_extractions 'conditions' cell into list[str].

    Expected format: Python literal list string, e.g. "['A', 'B']".
    On failure, returns [] (warns).
    """
    if is_effectively_empty(cell):
        return []

    if isinstance(cell, list):
        return [fix_mojibake_str(str(x)) for x in cell if not is_effectively_empty(x)]

    s = fix_mojibake_str(str(cell)).strip()
    try:
        parsed = ast.literal_eval(s)
    except Exception:
        logger.warning("Failed to parse conditions cell: %r", cell)
        return []

    if not isinstance(parsed, list):
        logger.warning("Conditions cell is not a list after parsing: %r", cell)
        return []

    out: List[str] = []
    for x in parsed:
        if is_effectively_empty(x):
            continue
        out.append(fix_mojibake_str(str(x)))
    return out


# =============================================================================
# Core mapping logic (primary-tumour-identical mapping; flattened outputs)
# =============================================================================

def map_condition_to_oncotree(
    condition: str,
    *,
    mapping: Dict[str, str],
    term_to_level: Dict[str, int],
) -> Tuple[Optional[List[str]], Optional[List[int]]]:
    """
    Map a single condition string -> (terms, levels).

    Mapping semantics aligned with overwrite_primary_tumour.py:
    - lookup by normalized key
    - blank or '[None]' => (None, None)
    - split on '|' into terms
    - compute levels; if any term fails => (None, None)

    NOTE: This function returns lists; caller decides whether to nest or flatten.
    """
    key = _norm_cell(condition)
    mapped = mapping.get(key)

    if mapped is None or _is_blank_or_none_marker(mapped):
        return None, None

    terms = split_or_terms(mapped)
    if not terms:
        return None, None

    levels = levels_for_terms(terms, term_to_level)
    if levels is None:
        return None, None

    return terms, levels


def _no_oncotree_message(conditions: List[str]) -> str:
    if len(conditions) == 1:
        original = conditions[0]
    else:
        original = str(conditions)
    return f"No oncotree term. Original condition is {original}"


def build_conditions_output_df(
    ctgov_field_extractions_csv: Path,
    *,
    mapping: Dict[str, str],
    term_to_level: Dict[str, int],
) -> pd.DataFrame:
    """
    Build output DataFrame (one row per trial):

        trialId | conditions | Oncotree_term | Oncotree_level

    Updated output shape:
    - conditions: list[str] (as parsed from input)
    - Oncotree_term: list[str] (flattened; OR terms expanded; unmapped removed)
    - Oncotree_level: list[int] (flattened; aligned to Oncotree_term)

    Special case:
    - if nothing maps for a trial (i.e., Oncotree_term would be []),
      then:
        Oncotree_term  = ["No oncotree term. Original condition is ..."]
        Oncotree_level = [None]
    """
    df = pd.read_csv(ctgov_field_extractions_csv, keep_default_na=False)

    if TRIAL_ID_COL not in df.columns:
        raise ValueError(f"Input CSV missing required column: {TRIAL_ID_COL}")
    if CONDITIONS_COL not in df.columns:
        raise ValueError(f"Input CSV missing required column: {CONDITIONS_COL}")

    out_rows: List[Dict[str, Any]] = []

    for _, row in df.iterrows():
        trial_id = row.get(TRIAL_ID_COL)
        conditions = parse_conditions_cell(row.get(CONDITIONS_COL))

        flat_terms: List[str] = []
        flat_levels: List[int] = []

        for cond in conditions:
            terms, levels = map_condition_to_oncotree(
                cond,
                mapping=mapping,
                term_to_level=term_to_level,
            )
            if terms is None or levels is None:
                continue

            # terms/levels are parallel lists (including OR expansions)
            flat_terms.extend(terms)
            flat_levels.extend(levels)

        if not flat_terms:
            out_terms: List[Any] = [_no_oncotree_message(conditions)]
            out_levels: List[Any] = [None]
        else:
            out_terms = flat_terms
            out_levels = flat_levels

        out_rows.append(
            {
                "trialId": trial_id,
                "conditions": conditions,
                "Oncotree_term": out_terms,
                "Oncotree_level": out_levels,
            }
        )

    return pd.DataFrame(out_rows)


# =============================================================================
# CLI
# =============================================================================

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Map trial conditions to OncoTree terms/levels (CSV → CSV)."
    )
    parser.add_argument(
        "--ctgov_field_extractions_csv",
        required=True,
        type=Path,
        help="CSV with columns: nctId, conditions",
    )
    parser.add_argument(
        "--mapping_csv",
        required=True,
        type=Path,
        help="ConditionsCurationResource_*.csv",
    )
    parser.add_argument(
        "--oncotree_csv",
        required=True,
        type=Path,
        help="OncoTree hierarchy CSV (same as primary tumour overwrite)",
    )
    parser.add_argument(
        "--out_csv",
        required=True,
        type=Path,
        help="Output CSV path",
    )
    parser.add_argument("--log_level", default="INFO", help="Logging level (INFO/DEBUG/...)")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    mapping = build_conditions_map(args.mapping_csv)
    term_to_level = build_term_to_level_index(args.oncotree_csv)

    df_out = build_conditions_output_df(
        args.ctgov_field_extractions_csv,
        mapping=mapping,
        term_to_level=term_to_level,
    )

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    df_out.to_csv(args.out_csv, index=False)
    logger.info("Wrote %s", args.out_csv)

    return 0


if __name__ == "__main__":
    main()
