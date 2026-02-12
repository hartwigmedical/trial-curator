"""
Resource CSV helpers for overwrite modules.

Scope:
- Load resource CSVs with consistent defaults (no implicit NA, mojibake fix)
- Discover suffix-style columns (*_lookup, *_curation, Move_to)
- Build lookup maps using criterion-provided key functions (policy lives in criterion modules)

Important:
- Required/optional lookup semantics are NOT handled here.
- Key construction and any fallback logic are NOT handled here.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Dict, Hashable, List, Optional, Tuple

import pandas as pd

from aus_trial_universe.ctgov.utils.ii_normalisation import fix_mojibake_df, is_effectively_empty

logger = logging.getLogger(__name__)

# Key builder signature:
# - row: pd.Series for a CSV row
# - lookup_cols: discovered lookup column names (in CSV order)
# - returns: hashable key or None (skip row)
KeyFn = Callable[[pd.Series, List[str]], Optional[Hashable]]


def load_resource_csv(csv_path: Path) -> pd.DataFrame:
    """
    Load a resource CSV using consistent defaults:
    - keep_default_na=False so blank cells stay as ''
    - mojibake fixes applied to object columns
    - column names stripped
    """
    df = pd.read_csv(
        csv_path,
        keep_default_na=False,
        na_values=[],
    )
    df.columns = [str(c).strip() for c in df.columns]
    return fix_mojibake_df(df)


def load_resources_by_prefix(
    resources_dir: Path,
    prefix_to_basename: Dict[str, str],
) -> Dict[str, pd.DataFrame]:
    """
    Load multiple resource CSVs from one directory.

    This is a lightweight loader helper so wrappers can load all resources consistently
    without embedding per-criterion policy (required/optional lookup etc stays in each
    criterion module).

    Example:
        dfs = load_resources_by_prefix(
            resources_dir,
            {"GeneAlterationCriterion": "GeneAlterationCurationResource.csv"},
        )
    """
    out: Dict[str, pd.DataFrame] = {}
    for prefix, basename in prefix_to_basename.items():
        path = resources_dir / basename
        if not path.exists():
            raise FileNotFoundError(f"Missing resource for {prefix}: {path}")
        out[prefix] = load_resource_csv(path)
    return out


def get_lookup_cols(df: pd.DataFrame) -> List[str]:
    """Return columns ending with '_lookup' (case-insensitive), preserving CSV order."""
    return [c for c in df.columns if str(c).lower().endswith("_lookup")]


def get_curation_cols(df: pd.DataFrame) -> List[str]:
    """Return columns ending with '_curation' (case-insensitive), preserving CSV order."""
    return [c for c in df.columns if str(c).lower().endswith("_curation")]


def get_move_to_col(df: pd.DataFrame) -> Optional[str]:
    """Return the Move_to column name (case-insensitive exact match), else None."""
    for c in df.columns:
        if str(c).strip().lower() == "move_to":
            return c
    return None


def base_from_suffix_col(col: str, suffix: str) -> str:
    """Strip a suffix (case-insensitive) from a column name if present."""
    if col.lower().endswith(suffix.lower()):
        return col[: -len(suffix)]
    return col


def build_resource_maps(
    df: pd.DataFrame,
    lookup_cols: List[str],
    curation_cols: List[str],
    key_fn: KeyFn,
    *,
    move_to_col: Optional[str] = None,
    move_to_key_fn: Optional[KeyFn] = None,
    keep_blank_curation: bool = False,
) -> Tuple[Dict[str, Dict[Hashable, str]], Dict[Hashable, str]]:
    """
    Build curation maps and Move_to map from a resource dataframe.

    Important:
    - This function does NOT decide required/optional lookup semantics.
    - It does NOT collapse or expand keys.
    - It ONLY calls key_fn (provided by the criterion script).

    Returns:
      (curation_maps, move_to_map)

    where:
      curation_maps = { <curation_col_name>: { key: curated_value, ... }, ... }
      move_to_map   = { key: Move_to_value, ... }  (key is from move_to_key_fn or key_fn)
    """
    curation_maps: Dict[str, Dict[Hashable, str]] = {c: {} for c in curation_cols}
    move_to_map: Dict[Hashable, str] = {}

    if move_to_key_fn is None:
        move_to_key_fn = key_fn

    for _, row in df.iterrows():
        key = key_fn(row, lookup_cols)
        if key is None:
            continue

        for cur_col in curation_cols:
            raw = row.get(cur_col)
            empty = is_effectively_empty(raw)
            if empty and not keep_blank_curation:
                continue
            curation_maps.setdefault(cur_col, {})[key] = "" if empty else str(raw).strip()

        if move_to_col:
            mv_raw = row.get(move_to_col)
            mv_empty = is_effectively_empty(mv_raw)
            if not mv_empty:
                mv_key = move_to_key_fn(row, lookup_cols)
                if mv_key is not None:
                    move_to_map[mv_key] = str(mv_raw).strip()

    # Remove empty maps for neatness
    curation_maps = {k: v for k, v in curation_maps.items() if v}
    return curation_maps, move_to_map
