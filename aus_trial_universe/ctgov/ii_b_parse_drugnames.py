from __future__ import annotations

import argparse
import ast
import logging
from pathlib import Path
from typing import List

import pandas as pd

logger = logging.getLogger(__name__)


def _parse_intervention_cell(cell: object) -> List[str]:
    if cell is None or (isinstance(cell, float) and pd.isna(cell)):
        return []

    if isinstance(cell, list):
        items = cell
    else:
        s = str(cell).strip()
        if not s or s.lower() in {"nan", "none", "null"}:
            return []
        if s.startswith("[") and s.endswith("]"):
            try:
                parsed = ast.literal_eval(s)
                items = parsed if isinstance(parsed, list) else [s]
            except (SyntaxError, ValueError):
                items = [s]
        else:
            items = [s]

    out: List[str] = []
    for it in items:
        if it is None:
            continue
        t = str(it).strip()
        if t:
            out.append(t)
    return out


def extract_unique_interventions(df: pd.DataFrame, col: str = "interventionName") -> List[str]:
    if col not in df.columns:
        raise ValueError(f"Missing required column '{col}'. Available columns: {list(df.columns)}")

    seen = set()
    out: List[str] = []

    for cell in df[col].tolist():
        for term in _parse_intervention_cell(cell):
            if term not in seen:
                seen.add(term)
                out.append(term)

    out = sorted(out, key=lambda x: x.casefold())
    return out


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract unique intervention (drug) names from ctgov field extractions."
    )
    parser.add_argument(
        "--input_csv",
        type=Path,
        help="Path to ctgov_field_extractions.csv (or equivalent).",
        required=True,
    )
    parser.add_argument(
        "--output_dir",
        type=Path,
        help="Output directory to save the summary CSVs.",
        required=True,
    )
    parser.add_argument(
        "--log_level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging level",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Reading input CSV: %s", args.input_csv)
    df = pd.read_csv(args.input_csv)

    logger.info("Extracting unique intervention terms from column: interventionName")
    terms = extract_unique_interventions(df, col="interventionName")
    logger.info("Found %d unique intervention terms.", len(terms))

    out_csv = args.output_dir / "unique_drug_names.csv"
    logger.info("Writing output CSV: %s", out_csv)
    pd.DataFrame({"drugName": terms}).to_csv(out_csv, index=False)

    logger.info("Done.")


if __name__ == "__main__":
    main()
