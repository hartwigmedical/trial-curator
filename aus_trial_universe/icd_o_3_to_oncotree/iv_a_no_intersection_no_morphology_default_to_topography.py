from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import List, Optional, Set

import pandas as pd

logger = logging.getLogger(__name__)


def clean_str(x) -> str:
    if x is None or pd.isna(x):
        return ""
    return str(x).strip()


def is_blank(x) -> bool:
    return clean_str(x) == ""


def require_columns(df: pd.DataFrame, required: Set[str], label: str) -> None:
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{label} missing required columns: {sorted(missing)}")


def process_csv(
    input_csv: Path,
    output_csv: Path,
) -> None:
    df = pd.read_csv(input_csv)

    required_cols = {
        "topography_oncotree_lvl1_exact_match",
        "morphology_oncotree_exact_matches_by_lvl1",
        "morphology_oncotree_synonym_matches_by_lvl1",
        "topography_morphology_exact_match",
        "topography_morphology_synonym_match",
    }
    require_columns(df, required_cols, "Input CSV")

    out = df.copy()
    out["fallback_topography_lvl1_no_morph_or_intersection"] = ""

    has_topography_exact = ~out["topography_oncotree_lvl1_exact_match"].apply(is_blank)

    has_morphology_exact = ~out["morphology_oncotree_exact_matches_by_lvl1"].apply(
        is_blank
    )
    has_morphology_synonym = ~out["morphology_oncotree_synonym_matches_by_lvl1"].apply(
        is_blank
    )

    has_any_morphology_match = has_morphology_exact | has_morphology_synonym
    has_no_morphology_match = ~has_any_morphology_match

    has_no_intersection = (
        out["topography_morphology_exact_match"].apply(is_blank)
        & out["topography_morphology_synonym_match"].apply(is_blank)
    )

    mask = has_topography_exact & (has_no_morphology_match | has_no_intersection)

    out.loc[mask, "fallback_topography_lvl1_no_morph_or_intersection"] = out.loc[
        mask, "topography_oncotree_lvl1_exact_match"
    ]

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output_csv, index=False)

    n_total = len(out)
    n_topo = int(has_topography_exact.sum())
    n_no_morph = int((has_topography_exact & has_no_morphology_match).sum())
    n_no_intersection = int((has_topography_exact & has_no_intersection).sum())
    n_eligible = int(mask.sum())
    n_populated = int(
        (~out["fallback_topography_lvl1_no_morph_or_intersection"].apply(is_blank)).sum()
    )

    logger.info("Wrote %d rows to %s", n_total, output_csv)
    logger.info("Rows with exact topography: %d", n_topo)
    logger.info("Rows with no morphology match: %d", n_no_morph)
    logger.info("Rows with no intersection: %d", n_no_intersection)
    logger.info("Eligible rows for fallback: %d", n_eligible)
    logger.info(
        "fallback_topography_lvl1_no_morph_or_intersection populated: %d",
        n_populated,
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input_csv", required=True, type=Path)
    parser.add_argument("--output_csv", required=True, type=Path)
    parser.add_argument("--log_level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    process_csv(
        input_csv=args.input_csv,
        output_csv=args.output_csv,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())