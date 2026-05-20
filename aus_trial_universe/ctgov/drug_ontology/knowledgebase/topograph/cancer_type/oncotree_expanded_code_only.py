from __future__ import annotations

import argparse
import logging
import re
from pathlib import Path
from typing import Iterable, List

import pandas as pd

logger = logging.getLogger(__name__)


TRAILING_CODE_RE = re.compile(r"^.*\(([^()]*)\)\s*$")


# ---------------------------
# Helpers
# ---------------------------


def _normalize(s: object) -> str:
    if s is None:
        return ""
    return str(s).strip()


def _extract_terminal_parenthetical_code(value: object) -> str:
    """
    Convert values like:
        "Adrenal Gland (ADRENAL_GLAND)" -> "ADRENAL_GLAND"
        "Extranodal ... (MALT lymphoma) (EMALT)" -> "EMALT"

    If the value does not end with a parenthesized code, return the value unchanged
    apart from surrounding whitespace normalization.
    """
    text = _normalize(value)
    if not text:
        return ""

    match = TRAILING_CODE_RE.match(text)
    if not match:
        return text

    code = match.group(1).strip()
    return code if code else text


def _select_columns_by_prefix(columns: Iterable[str], prefix: str) -> List[str]:
    prefix_norm = prefix.lower()
    return [c for c in columns if str(c).lower().startswith(prefix_norm)]


# ---------------------------
# Core logic
# ---------------------------


def process(
    input_csv: Path,
    output_csv: Path,
    column_prefix: str = "level_",
) -> None:
    """
    Read an expanded OncoTree CSV and replace hierarchy labels of the form
    "description (CODE)" with "CODE" in columns matching `column_prefix`.

    All row order, column order, and non-matching columns are preserved.
    """
    df = pd.read_csv(input_csv, dtype=str, keep_default_na=False)

    target_columns = _select_columns_by_prefix(df.columns, column_prefix)
    if not target_columns:
        available = ", ".join(str(c) for c in df.columns)
        raise ValueError(
            f"No columns found with prefix '{column_prefix}'. Available columns: {available}"
        )

    changed_cells = 0

    for col in target_columns:
        before = df[col].copy()
        df[col] = df[col].map(_extract_terminal_parenthetical_code)
        changed_cells += int((before != df[col]).sum())

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_csv, index=False, encoding="utf-8-sig", lineterminator="\r\n")

    logger.info(
        "Wrote %s rows and %s columns to %s; converted %s cells across %s target columns",
        len(df),
        len(df.columns),
        output_csv,
        changed_cells,
        len(target_columns),
    )


# ---------------------------
# CLI
# ---------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert expanded OncoTree hierarchy labels from 'description (CODE)' to 'CODE'"
    )

    parser.add_argument(
        "--input_csv",
        required=True,
        type=Path,
        help="Path to the expanded OncoTree CSV",
    )
    parser.add_argument(
        "--output_csv",
        required=True,
        type=Path,
        help="Path to write the cleaned CSV",
    )
    parser.add_argument(
        "--column_prefix",
        default="level_",
        help="Only clean columns with this prefix. Default: level_",
    )
    parser.add_argument("--log_level", default="INFO")

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    process(
        input_csv=args.input_csv,
        output_csv=args.output_csv,
        column_prefix=args.column_prefix,
    )


if __name__ == "__main__":
    main()
