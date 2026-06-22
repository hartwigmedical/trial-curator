#!/usr/bin/env python3
"""
Build the processed ATC tree TSV from a raw WHO ATC/DDD CSV export.

The legacy file format is:

    ATC code\tATC level name\tDDD\tUnit\tAdm.R\tComment

This module intentionally preserves the tree rows and row order from the newer
WHO file. It does not collapse duplicate ATC codes, because duplicate rows are
used for multiple DDD / route entries in the legacy file as well.

It also applies the known WHO 2026-04-25 row-shift patch:

    B01AF03,edoxaban,NA,60,mg,O

becomes, in legacy TSV terms:

    B01AF03\tedoxaban\t60\tmg\tO\t

Usage:

    python -m aus_trial_universe.eligibility_path.ctgov.drug_ontology.preprocessing.atc_tree \
        --input_dir data/ctgov/drug_ontology/raw_inputs/ATC/version_25042026 \
        --output_file data/ctgov/drug_ontology/processed_inputs/pipeline/ATC/version_25042026/atc_tree.tsv
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence


NEW_HEADER: tuple[str, ...] = ("atc_code", "atc_name", "ddd", "uom", "adm_r", "note")
LEGACY_HEADER: tuple[str, ...] = (
    "ATC code",
    "ATC level name",
    "DDD",
    "Unit",
    "Adm.R",
    "Comment",
)

OPTIONAL_NEW_COLUMNS: frozenset[str] = frozenset({"ddd", "uom", "adm_r", "note"})

ATC_CODE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^[A-Z]$"),                     # level 1, e.g. A
    re.compile(r"^[A-Z][0-9]{2}$"),             # level 2, e.g. A01
    re.compile(r"^[A-Z][0-9]{2}[A-Z]$"),        # level 3, e.g. A01A
    re.compile(r"^[A-Z][0-9]{2}[A-Z]{2}$"),     # level 4, e.g. A01AA
    re.compile(r"^[A-Z][0-9]{2}[A-Z]{2}[0-9]{2}$"),  # level 5, e.g. A01AA01
)


@dataclass(frozen=True)
class ConversionStats:
    """Summary of a completed conversion."""

    input_rows: int
    output_rows: int
    unique_atc_codes: int
    duplicate_atc_codes: int
    patched_edoxaban_rows: int
    blanked_na_cells: int


def parent_atc_code(atc_code: str) -> str | None:
    """Return the immediate parent code for an ATC code, or None for level 1."""

    code = atc_code.strip()
    if len(code) == 1:
        return None
    if len(code) == 3:
        return code[:1]
    if len(code) == 4:
        return code[:3]
    if len(code) == 5:
        return code[:4]
    if len(code) == 7:
        return code[:5]
    raise ValueError(f"Invalid ATC code length for {atc_code!r}")


def is_valid_atc_code(atc_code: str) -> bool:
    """Return True if the ATC code has one of the five standard ATC shapes."""

    code = atc_code.strip()
    return any(pattern.fullmatch(code) for pattern in ATC_CODE_PATTERNS)


def normalize_optional_cell(value: str | None) -> tuple[str, bool]:
    """
    Normalize optional DDD fields.

    The newer WHO CSV uses literal 'NA' for missing optional fields. The legacy
    TSV uses empty cells. Returns (normalized_value, was_blanked_from_na).
    """

    if value is None:
        return "", False

    stripped = value.strip()
    if stripped.upper() == "NA":
        return "", True
    return stripped, False


def patch_known_row_shifts(row: dict[str, str]) -> bool:
    """
    Patch known shifted rows in-place.

    Returns True when a patch is applied.
    """

    # Known malformed WHO.ATC-DDD.2026-04-25.csv row:
    # B01AF03,edoxaban,NA,60,mg,O
    # Intended columns: ddd=60, uom=mg, adm_r=O, note=<empty>
    if (
        row.get("atc_code", "").strip() == "B01AF03"
        and row.get("atc_name", "").strip().lower() == "edoxaban"
        and row.get("ddd", "").strip().upper() in {"", "NA"}
        and row.get("uom", "").strip() == "60"
        and row.get("adm_r", "").strip() == "mg"
        and row.get("note", "").strip() == "O"
    ):
        row["ddd"] = "60"
        row["uom"] = "mg"
        row["adm_r"] = "O"
        row["note"] = ""
        return True

    return False


def new_row_to_legacy_row(row: Mapping[str, str]) -> tuple[list[str], int]:
    """
    Convert one newer WHO CSV row into one legacy TSV row.

    Returns (legacy_row, number_of_optional_cells_blanked_from_literal_NA).
    """

    atc_code = (row.get("atc_code") or "").strip()
    atc_name = (row.get("atc_name") or "").strip()

    normalized_optional: dict[str, str] = {}
    blanked_na_cells = 0
    for column in ("ddd", "uom", "adm_r", "note"):
        normalized, was_blanked = normalize_optional_cell(row.get(column))
        normalized_optional[column] = normalized
        blanked_na_cells += int(was_blanked)

    return [
        atc_code,
        atc_name,
        normalized_optional["ddd"],
        normalized_optional["uom"],
        normalized_optional["adm_r"],
        normalized_optional["note"],
    ], blanked_na_cells


def validate_legacy_rows(rows: Sequence[Sequence[str]]) -> None:
    """
    Validate ATC code shape and parent hierarchy.

    Duplicate ATC codes are allowed because multiple DDD rows for the same code
    are valid in both the newer WHO file and the legacy file.
    """

    if not rows:
        raise ValueError("No data rows were produced.")

    invalid_codes: list[str] = []
    for row in rows:
        code = row[0]
        if not is_valid_atc_code(code):
            invalid_codes.append(code)

    if invalid_codes:
        preview = ", ".join(repr(code) for code in invalid_codes[:20])
        suffix = "" if len(invalid_codes) <= 20 else f" ... +{len(invalid_codes) - 20} more"
        raise ValueError(f"Invalid ATC code(s): {preview}{suffix}")

    all_codes = {row[0] for row in rows}
    missing_parents: list[tuple[str, str]] = []
    for row in rows:
        code = row[0]
        parent = parent_atc_code(code)
        if parent is not None and parent not in all_codes:
            missing_parents.append((code, parent))

    if missing_parents:
        preview = ", ".join(f"{code}->{parent}" for code, parent in missing_parents[:20])
        suffix = "" if len(missing_parents) <= 20 else f" ... +{len(missing_parents) - 20} more"
        raise ValueError(f"Missing parent ATC code(s): {preview}{suffix}")

    # Enforce legacy tree ordering: a child should not appear before its parent.
    # For duplicate DDD rows, this only matters for the first occurrence.
    seen: set[str] = set()
    parent_after_child: list[tuple[str, str]] = []
    for row in rows:
        code = row[0]
        parent = parent_atc_code(code)
        if parent is not None and parent not in seen:
            parent_after_child.append((code, parent))
        seen.add(code)

    if parent_after_child:
        preview = ", ".join(f"{code} before {parent}" for code, parent in parent_after_child[:20])
        suffix = "" if len(parent_after_child) <= 20 else f" ... +{len(parent_after_child) - 20} more"
        raise ValueError(f"ATC hierarchy is not parent-before-child ordered: {preview}{suffix}")


def _quote_legacy_tsv_cell(value: str) -> str:
    """
    Quote a cell the way the legacy TSV appears to have been emitted.

    Even though commas do not require quoting in a TSV, the old file quotes many
    comma-containing cells. Keeping that behavior makes the generated file closer
    to a byte-level legacy style while remaining valid TSV.
    """

    if any(ch in value for ch in (",", "\t", "\r", "\n", '"')):
        return '"' + value.replace('"', '""') + '"'
    return value


def write_legacy_tsv(path: str | Path, rows: Iterable[Sequence[str]]) -> None:
    """Write rows to a legacy TSV with CRLF line endings."""

    output_path = Path(path)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        handle.write("\t".join(LEGACY_HEADER) + "\r\n")
        for row in rows:
            if len(row) != len(LEGACY_HEADER):
                raise ValueError(f"Expected {len(LEGACY_HEADER)} columns, got {len(row)}: {row!r}")
            handle.write("\t".join(_quote_legacy_tsv_cell(str(cell)) for cell in row) + "\r\n")


def convert_atc_csv_to_legacy_tsv(
    input_csv: str | Path,
    output_tsv: str | Path,
    *,
    strict: bool = True,
) -> ConversionStats:
    """
    Convert a newer WHO ATC/DDD CSV into the legacy ATC tree TSV format.

    Parameters
    ----------
    input_csv:
        Path to the newer WHO CSV with columns:
        atc_code, atc_name, ddd, uom, adm_r, note.
    output_tsv:
        Path where the legacy TSV should be written.
    strict:
        When True, validate ATC code shape, parent presence, and parent-before-
        child row order before writing.
    """

    input_path = Path(input_csv)
    output_path = Path(output_tsv)

    legacy_rows: list[list[str]] = []
    patched_edoxaban_rows = 0
    blanked_na_cells = 0
    input_rows = 0

    with input_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = tuple(reader.fieldnames or ())
        missing_columns = [column for column in NEW_HEADER if column not in fieldnames]
        if missing_columns:
            raise ValueError(
                f"Input CSV is missing required column(s): {missing_columns}. "
                f"Found columns: {list(fieldnames)}"
            )

        for line_number, raw_row in enumerate(reader, start=2):
            if None in raw_row:
                raise ValueError(
                    f"Line {line_number} has too many CSV fields. Extra values: {raw_row[None]!r}"
                )

            row = {column: (raw_row.get(column) or "") for column in NEW_HEADER}
            input_rows += 1

            if patch_known_row_shifts(row):
                patched_edoxaban_rows += 1

            legacy_row, row_blanked_na_cells = new_row_to_legacy_row(row)
            blanked_na_cells += row_blanked_na_cells
            legacy_rows.append(legacy_row)

    if strict:
        validate_legacy_rows(legacy_rows)

    write_legacy_tsv(output_path, legacy_rows)

    code_counts = Counter(row[0] for row in legacy_rows)
    duplicate_atc_codes = sum(1 for count in code_counts.values() if count > 1)

    return ConversionStats(
        input_rows=input_rows,
        output_rows=len(legacy_rows),
        unique_atc_codes=len(code_counts),
        duplicate_atc_codes=duplicate_atc_codes,
        patched_edoxaban_rows=patched_edoxaban_rows,
        blanked_na_cells=blanked_na_cells,
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert newer WHO ATC/DDD CSV to legacy atc_tree.tsv format."
    )
    parser.add_argument(
        "--input_dir",
        type=Path,
        required=True,
        help=(
            "Directory containing the newer WHO ATC/DDD CSV, "
            "e.g. data/ctgov/drug_ontology/raw_inputs/ATC/version_25042026"
        ),
    )
    parser.add_argument(
        "--output_file",
        type=Path,
        default=None,
        help=(
            "Optional output legacy-formatted TSV path. "
            "If omitted, writes <input_dir>/atc_tree.tsv. "
            "If a directory is provided, writes <directory>/atc_tree.tsv."
        ),
    )
    parser.add_argument(
        "--no-strict",
        action="store_true",
        help="Skip ATC code and hierarchy validation before writing.",
    )
    return parser


def find_who_atc_ddd_csv(input_dir: Path) -> Path:
    """
    Find the main WHO ATC/DDD CSV in a version directory.

    This intentionally excludes the combinations file:
        WHO.ATC-DDD-combinations....
    """
    if not input_dir.exists():
        raise FileNotFoundError(f"Input directory does not exist: {input_dir}")

    if not input_dir.is_dir():
        raise NotADirectoryError(f"--input_dir must be a directory: {input_dir}")

    candidates = sorted(
        path
        for path in input_dir.glob("WHO.ATC-DDD*.csv")
        if "combination" not in path.name.lower()
    )

    if not candidates:
        raise FileNotFoundError(
            f"No WHO ATC/DDD CSV found in {input_dir}. "
            "Expected something like WHO.ATC-DDD.2026-04-25.csv"
        )

    if len(candidates) > 1:
        candidate_list = "\n".join(f"  - {path}" for path in candidates)
        raise ValueError(
            "Found multiple possible WHO ATC/DDD CSV files. "
            "Please remove ambiguity or pass an explicit input file.\n"
            f"{candidate_list}"
        )

    return candidates[0]


def resolve_output_path(input_dir: Path, output_file: Path | None) -> Path:
    """
    Resolve where to write the legacy atc_tree.tsv file.
    """
    if output_file is None:
        return input_dir / "atc_tree.tsv"

    # Allows:
    #   --output_file /some/directory/
    # to mean:
    #   /some/directory/atc_tree.tsv
    if output_file.exists() and output_file.is_dir():
        return output_file / "atc_tree.tsv"

    return output_file


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    input_dir = args.input_dir
    input_file = find_who_atc_ddd_csv(input_dir)
    output_path = resolve_output_path(input_dir, args.output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    stats = convert_atc_csv_to_legacy_tsv(
        input_file,
        output_path,
        strict=not args.no_strict,
    )

    print(f"Input directory: {input_dir}", file=sys.stderr)
    print(f"Input file: {input_file}", file=sys.stderr)
    print(f"Wrote: {output_path}", file=sys.stderr)
    print(f"Input rows: {stats.input_rows}", file=sys.stderr)
    print(f"Output rows: {stats.output_rows}", file=sys.stderr)
    print(f"Unique ATC codes: {stats.unique_atc_codes}", file=sys.stderr)
    print(f"Duplicate ATC codes with multiple rows: {stats.duplicate_atc_codes}", file=sys.stderr)
    print(f"Patched edoxaban shifted rows: {stats.patched_edoxaban_rows}", file=sys.stderr)
    print(f"Literal NA optional cells blanked: {stats.blanked_na_cells}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
