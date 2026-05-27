from __future__ import annotations

import argparse
import csv
import logging
from collections import OrderedDict
from pathlib import Path
from typing import Iterable, List

import pandas as pd

logger = logging.getLogger(__name__)


DEFAULT_TOPOGRAPH_TSV = Path(
    "/Users/junrancao/WorkProjects/trial-curator_repo/data/ctgov/drug_ontology/raw/Topograph_13052026/TOPOGRAPH-master.tsv"
)
DEFAULT_OUTPUT_CSV = Path(
    "/data/ctgov/drug_ontology/Topograph_hartwig_version/cancertype_to_oncotree/cancertype_to_oncotree.csv"
)


# ---------------------------
# Helpers
# ---------------------------


def _normalize(s: str) -> str:
    if s is None:
        return ""
    return str(s).strip()


def _normalize_header(s: str) -> str:
    """Normalize a dataframe header for case/spacing-insensitive matching."""
    return " ".join(_normalize(s).lower().split())


def _split_tumour_types(raw: str) -> List[str]:
    """
    Split a TOPOGRAPH tumour-type cell into individual tumour types.

    TOPOGRAPH rows may contain multiple tumour types separated by ';'.
    Example:
        "Acute lymphoblastic leukaemia; Chronic myelogenous leukaemia"
    """
    if not raw:
        return []

    terms = []
    for term in str(raw).split(";"):
        term = _normalize(term)
        if term:
            terms.append(term)

    return terms


def _find_column(columns: Iterable[str], requested_col: str) -> str:
    requested_key = _normalize_header(requested_col)

    for col in columns:
        if _normalize_header(col) == requested_key:
            return col

    available = ", ".join(str(c) for c in columns)
    raise ValueError(
        f"Could not find tumour type column '{requested_col}'. Available columns: {available}"
    )


# ---------------------------
# Core logic
# ---------------------------


def extract_unique_tumour_types(
    topograph_tsv: Path,
    tumour_type_col: str = "Tumour Type",
    sort_values: bool = True,
) -> List[str]:
    """
    Read TOPOGRAPH and return the unique tumour types from the tumour-type column.

    Uniqueness is exact after trimming surrounding whitespace. This intentionally keeps
    case variants separate for now, because they are distinct source strings in TOPOGRAPH.
    """
    df = pd.read_csv(topograph_tsv, sep="\t", dtype=str, keep_default_na=False)
    resolved_col = _find_column(df.columns, tumour_type_col)

    unique_terms: OrderedDict[str, None] = OrderedDict()

    for raw_value in df[resolved_col]:
        for term in _split_tumour_types(raw_value):
            unique_terms.setdefault(term, None)

    terms = list(unique_terms.keys())
    if sort_values:
        terms = sorted(terms, key=lambda x: x.casefold())

    return terms


def process(
    topograph_tsv: Path,
    output_csv: Path,
    tumour_type_col: str = "Tumour Type",
    sort_values: bool = True,
) -> None:
    tumour_types = extract_unique_tumour_types(
        topograph_tsv=topograph_tsv,
        tumour_type_col=tumour_type_col,
        sort_values=sort_values,
    )

    output_csv.parent.mkdir(parents=True, exist_ok=True)

    with output_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "topograph_cancer_type",
                "oncotree_curation",
            ],
        )
        writer.writeheader()
        for tumour_type in tumour_types:
            writer.writerow(
                {
                    "topograph_cancer_type": tumour_type,
                    "oncotree_curation": "",
                }
            )

    logger.info(
        "Wrote %s unique tumour types to %s",
        len(tumour_types),
        output_csv,
    )


# ---------------------------
# CLI
# ---------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract unique TOPOGRAPH tumour types for future OncoTree curation"
    )

    parser.add_argument(
        "--topograph_tsv",
        default=DEFAULT_TOPOGRAPH_TSV,
        type=Path,
        help="Path to TOPOGRAPH-master.tsv",
    )
    parser.add_argument(
        "--output_csv",
        default=DEFAULT_OUTPUT_CSV,
        type=Path,
        help="Path to write the cancer-type → OncoTree curation CSV",
    )
    parser.add_argument(
        "--tumour_type_col",
        default="Tumour Type",
        help="Name of the tumour type column in TOPOGRAPH",
    )
    parser.add_argument(
        "--preserve_source_order",
        action="store_true",
        help="Preserve first-seen TOPOGRAPH order instead of sorting alphabetically",
    )
    parser.add_argument("--log_level", default="INFO")

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    process(
        topograph_tsv=args.topograph_tsv,
        output_csv=args.output_csv,
        tumour_type_col=args.tumour_type_col,
        sort_values=not args.preserve_source_order,
    )


if __name__ == "__main__":
    main()
