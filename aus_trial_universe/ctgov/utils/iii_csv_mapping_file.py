from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Dict, Hashable, List, Optional, Tuple

import pandas as pd

from aus_trial_universe.ctgov.utils.ii_normalisation import fix_mojibake_df, is_effectively_empty

logger = logging.getLogger(__name__)


KeyFn = Callable[[pd.Series, List[str]], Optional[Hashable]]


def load_resource_csv(csv_path: Path) -> pd.DataFrame:
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
    out: Dict[str, pd.DataFrame] = {}
    for prefix, basename in prefix_to_basename.items():
        path = resources_dir / basename
        if not path.exists():
            raise FileNotFoundError(f"Missing resource for {prefix}: {path}")
        out[prefix] = load_resource_csv(path)
    return out


def get_lookup_cols(df: pd.DataFrame) -> List[str]:
    return [c for c in df.columns if str(c).lower().endswith("_lookup")]


def get_curation_cols(df: pd.DataFrame) -> List[str]:
    return [c for c in df.columns if str(c).lower().endswith("_curation")]


def get_move_to_col(df: pd.DataFrame) -> Optional[str]:
    for c in df.columns:
        if str(c).strip().lower() == "move_to":
            return c
    return None


def base_from_suffix_col(col: str, suffix: str) -> str:
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

    curation_maps = {k: v for k, v in curation_maps.items() if v}
    return curation_maps, move_to_map
