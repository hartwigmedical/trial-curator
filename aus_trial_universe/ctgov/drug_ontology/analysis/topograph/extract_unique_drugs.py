#!/usr/bin/env python3
"""
Extract unique individual drug names from a Topograph TSV.

Input
-----
A Topograph TSV with a column named exactly "Drugs" by default.

Parsing rules
-------------
1. Semicolon separates substitutable drug alternatives.
   Example:
       Afatinib; Dacomitinib; Pyrotinib; Poziotinib
   yields:
       Afatinib
       Dacomitinib
       Pyrotinib
       Poziotinib

2. Plus sign separates components of a combination therapy.
   Example:
       Dabrafenib + Trametinib
   yields:
       Dabrafenib
       Trametinib

3. If both delimiters occur in one cell, semicolon groups are handled first,
   then plus components are split inside each group.
   Example:
       A; B + C
   yields:
       A
       B
       C
   while the expanded audit output records that only B and C are combination
   components.

Outputs
-------
By default, writes two TSV files next to the input file, or inside --output-dir:

    topograph_unique_drugs.tsv
        One column: drug

    topograph_drugs_expanded.tsv
        One row per extracted drug mention before deduplication, preserving the
        source row, original Drugs cell, and delimiter context.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import unicodedata
from collections import OrderedDict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence


DRUGS_COLUMN = "Drugs"
UNIQUE_OUTPUT_FILENAME = "topograph_unique_drugs.tsv"
EXPANDED_OUTPUT_FILENAME = "topograph_drugs_expanded.tsv"

MISSING_DRUG_VALUES = {"", "na", "n/a", "nan", "none", "null", "."}

SEMICOLON_SPLIT_RE = re.compile(r"\s*;\s*")
PLUS_SPLIT_RE = re.compile(r"\s*\+\s*")
WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class ExpandedDrugMention:
    """One extracted drug mention before deduplication."""

    source_line_number: int
    source_row_index: int
    source_drugs_text: str
    semicolon_group_index: int
    semicolon_group_text: str
    combination_component_index: int
    is_substitutable_alternative: bool
    is_combination_component: bool
    split_context: str
    drug: str


@dataclass(frozen=True)
class TopographDrugExtractionStats:
    """Summary of a completed Topograph drug extraction."""

    input_rows: int
    rows_with_nonempty_drugs: int
    rows_with_semicolon: int
    rows_with_plus: int
    rows_with_both_semicolon_and_plus: int
    expanded_drug_mentions: int
    unique_drugs: int
    duplicate_mentions_removed: int
    blank_split_tokens_dropped: int


def normalize_drug_name(value: str | None) -> str:
    """
    Clean a drug token while preserving its visible source spelling.

    This intentionally does not lowercase, remove punctuation, or apply synonym
    normalization. Those operations belong in the later ontology/RxNorm matching
    stage. Here we only normalize unicode width, strip wrapping quotes, and
    collapse whitespace.
    """
    if value is None:
        return ""

    text = unicodedata.normalize("NFKC", str(value))
    text = text.replace("\u00a0", " ")
    text = WHITESPACE_RE.sub(" ", text).strip()

    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1].strip()

    return text


def is_missing_drug_value(value: str | None) -> bool:
    """Return True when a Drugs-cell value should be ignored."""
    return normalize_drug_name(value).casefold() in MISSING_DRUG_VALUES


def dedupe_key(drug_name: str) -> str:
    """
    Return the key used for de-duplicating drug names.

    The output keeps the first observed display form, but de-duplication is
    case-insensitive and whitespace-normalized.
    """
    return normalize_drug_name(drug_name).casefold()


def context_for_component(
    *,
    is_substitutable_alternative: bool,
    is_combination_component: bool,
) -> str:
    """Classify the delimiter context for one extracted drug component."""
    if is_substitutable_alternative and is_combination_component:
        return "substitutable_alternative_and_combination_component"
    if is_substitutable_alternative:
        return "substitutable_alternative"
    if is_combination_component:
        return "combination_component"
    return "single_drug"


def split_drugs_cell(
    drugs_text: str | None,
    *,
    source_line_number: int,
    source_row_index: int,
) -> tuple[list[ExpandedDrugMention], int]:
    """
    Split one Topograph Drugs cell into individual drug mentions.

    Returns
    -------
    mentions:
        Extracted individual drug mentions.
    blank_tokens_dropped:
        Number of empty tokens produced by malformed delimiters such as
        trailing semicolons, doubled semicolons, or doubled plus signs.
    """
    source_text = normalize_drug_name(drugs_text)
    if is_missing_drug_value(source_text):
        return [], 0

    raw_semicolon_parts = SEMICOLON_SPLIT_RE.split(source_text)
    semicolon_groups = [normalize_drug_name(part) for part in raw_semicolon_parts if normalize_drug_name(part)]
    blank_tokens_dropped = len(raw_semicolon_parts) - len(semicolon_groups)

    is_substitutable_alternative = len(semicolon_groups) > 1
    mentions: list[ExpandedDrugMention] = []

    for semicolon_group_index, semicolon_group_text in enumerate(semicolon_groups, start=1):
        raw_plus_parts = PLUS_SPLIT_RE.split(semicolon_group_text)
        components = [normalize_drug_name(part) for part in raw_plus_parts if normalize_drug_name(part)]
        blank_tokens_dropped += len(raw_plus_parts) - len(components)

        is_combination_component = len(components) > 1

        for combination_component_index, drug in enumerate(components, start=1):
            split_context = context_for_component(
                is_substitutable_alternative=is_substitutable_alternative,
                is_combination_component=is_combination_component,
            )
            mentions.append(
                ExpandedDrugMention(
                    source_line_number=source_line_number,
                    source_row_index=source_row_index,
                    source_drugs_text=source_text,
                    semicolon_group_index=semicolon_group_index,
                    semicolon_group_text=semicolon_group_text,
                    combination_component_index=combination_component_index,
                    is_substitutable_alternative=is_substitutable_alternative,
                    is_combination_component=is_combination_component,
                    split_context=split_context,
                    drug=drug,
                )
            )

    return mentions, blank_tokens_dropped


def detect_delimiter_for_required_column(
    input_path: Path,
    required_column: str,
) -> str:
    """
    Detect whether a Topograph file is comma- or tab-delimited.

    The Topograph export may have a .tsv suffix while actually being
    comma-delimited, so do not trust the file extension.
    """
    with input_path.open("r", encoding="utf-8-sig", newline="") as handle:
        header_line = handle.readline()

    if not header_line:
        raise ValueError(f"Input file is empty: {input_path}")

    delimiter_candidates = ["\t", ","]
    parsed_headers_by_delimiter: dict[str, list[str]] = {}

    for delimiter in delimiter_candidates:
        parsed_header = next(csv.reader([header_line], delimiter=delimiter))
        parsed_header = [column.strip() for column in parsed_header]
        parsed_headers_by_delimiter[delimiter] = parsed_header

        if required_column in parsed_header:
            return delimiter

    tab_columns = parsed_headers_by_delimiter["\t"]
    comma_columns = parsed_headers_by_delimiter[","]

    raise ValueError(
        f"Required column {required_column!r} not found in {input_path} "
        "using either tab or comma delimiter.\n"
        f"Tab-parsed columns: {tab_columns}\n"
        f"Comma-parsed columns: {comma_columns}"
    )


def read_topograph_drug_mentions(input_tsv: str | Path, drug_column: str = DRUGS_COLUMN) -> tuple[list[ExpandedDrugMention], TopographDrugExtractionStats]:
    """Read a Topograph TSV and expand the Drugs column into individual mentions."""
    input_path = Path(input_tsv)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file does not exist: {input_path}")
    if not input_path.is_file():
        raise IsADirectoryError(f"Input path is not a file: {input_path}")

    input_rows = 0
    rows_with_nonempty_drugs = 0
    rows_with_semicolon = 0
    rows_with_plus = 0
    rows_with_both_semicolon_and_plus = 0
    blank_split_tokens_dropped = 0
    mentions: list[ExpandedDrugMention] = []

    delimiter = detect_delimiter_for_required_column(input_path, drug_column)

    with input_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)

        if reader.fieldnames is None:
            raise ValueError(f"Input file has no header row: {input_path}")
        if drug_column not in reader.fieldnames:
            available = ", ".join(reader.fieldnames)
            raise ValueError(
                f"Required column {drug_column!r} not found in {input_path}. "
                f"Available columns: {available}"
            )

        for source_row_index, row in enumerate(reader, start=1):
            input_rows += 1
            source_line_number = source_row_index + 1  # header is line 1
            drugs_text = row.get(drug_column)
            normalized_drugs_text = normalize_drug_name(drugs_text)

            if is_missing_drug_value(normalized_drugs_text):
                continue

            rows_with_nonempty_drugs += 1
            has_semicolon = ";" in normalized_drugs_text
            has_plus = "+" in normalized_drugs_text
            if has_semicolon:
                rows_with_semicolon += 1
            if has_plus:
                rows_with_plus += 1
            if has_semicolon and has_plus:
                rows_with_both_semicolon_and_plus += 1

            row_mentions, row_blank_tokens = split_drugs_cell(
                normalized_drugs_text,
                source_line_number=source_line_number,
                source_row_index=source_row_index,
            )
            mentions.extend(row_mentions)
            blank_split_tokens_dropped += row_blank_tokens

    unique_count = len(unique_drugs_first_seen(mentions))
    stats = TopographDrugExtractionStats(
        input_rows=input_rows,
        rows_with_nonempty_drugs=rows_with_nonempty_drugs,
        rows_with_semicolon=rows_with_semicolon,
        rows_with_plus=rows_with_plus,
        rows_with_both_semicolon_and_plus=rows_with_both_semicolon_and_plus,
        expanded_drug_mentions=len(mentions),
        unique_drugs=unique_count,
        duplicate_mentions_removed=len(mentions) - unique_count,
        blank_split_tokens_dropped=blank_split_tokens_dropped,
    )
    return mentions, stats


def unique_drugs_first_seen(mentions: Iterable[ExpandedDrugMention]) -> list[str]:
    """
    Return unique drug names in first-seen order.

    De-duplication is case-insensitive via dedupe_key(), while the retained drug
    string is the first observed display form from Topograph.
    """
    unique_by_key: OrderedDict[str, str] = OrderedDict()
    for mention in mentions:
        key = dedupe_key(mention.drug)
        if key and key not in unique_by_key:
            unique_by_key[key] = mention.drug
    return list(unique_by_key.values())


def write_unique_drugs_tsv(output_tsv: str | Path, unique_drugs: Sequence[str]) -> None:
    """Write the one-column unique-drug TSV."""
    output_path = Path(output_tsv)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["drug"])
        for drug in unique_drugs:
            writer.writerow([drug])


def write_expanded_mentions_tsv(output_tsv: str | Path, mentions: Sequence[ExpandedDrugMention]) -> None:
    """Write the expanded, pre-deduplication audit TSV."""
    output_path = Path(output_tsv)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "source_line_number",
        "source_row_index",
        "source_drugs_text",
        "semicolon_group_index",
        "semicolon_group_text",
        "combination_component_index",
        "is_substitutable_alternative",
        "is_combination_component",
        "split_context",
        "drug",
    ]

    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for mention in mentions:
            writer.writerow(asdict(mention))


def extract_topograph_unique_drugs(
    input_tsv: str | Path,
    unique_output_tsv: str | Path,
    *,
    expanded_output_tsv: str | Path | None = None,
    drug_column: str = DRUGS_COLUMN,
    sort_unique_drugs: bool = False,
) -> TopographDrugExtractionStats:
    """
    Extract individual Topograph drugs and write a deduplicated unique-drug file.
    """
    mentions, stats = read_topograph_drug_mentions(input_tsv, drug_column=drug_column)
    unique_drugs = unique_drugs_first_seen(mentions)
    if sort_unique_drugs:
        unique_drugs = sorted(unique_drugs, key=lambda value: value.casefold())

    write_unique_drugs_tsv(unique_output_tsv, unique_drugs)

    if expanded_output_tsv is not None:
        write_expanded_mentions_tsv(expanded_output_tsv, mentions)

    return stats


def find_topograph_input_file(input_dir: Path) -> Path:
    """Find the Topograph master TSV in a directory."""
    if not input_dir.exists():
        raise FileNotFoundError(f"Input directory does not exist: {input_dir}")
    if not input_dir.is_dir():
        raise NotADirectoryError(f"--input-dir must be a directory: {input_dir}")

    candidates = sorted(input_dir.glob("TOPOGRAPH-master.tsv"))
    if not candidates:
        raise FileNotFoundError(
            f"No Topograph master TSV found in {input_dir}. "
            "Expected TOPOGRAPH-master.tsv"
        )
    if len(candidates) > 1:
        candidate_list = "\n".join(f"  - {path}" for path in candidates)
        raise ValueError(
            "Found multiple possible Topograph input files. "
            "Use --input-file to remove ambiguity.\n"
            f"{candidate_list}"
        )
    return candidates[0]


def resolve_input_file(input_file: Path | None, input_dir: Path | None) -> Path:
    """Resolve CLI input from either --input-file or --input-dir."""
    if (input_file is None) == (input_dir is None):
        raise ValueError("Specify exactly one of --input-file/--input_file or --input-dir/--input_dir.")

    if input_file is not None:
        if not input_file.exists():
            raise FileNotFoundError(f"Input file does not exist: {input_file}")
        if not input_file.is_file():
            raise IsADirectoryError(f"--input-file must be a file: {input_file}")
        return input_file

    assert input_dir is not None
    return find_topograph_input_file(input_dir)


def resolve_output_file(path: Path | None, base_dir: Path, default_filename: str) -> Path:
    """Resolve a file argument, accepting either a file path or an existing directory."""
    if path is None:
        return base_dir / default_filename
    if path.exists() and path.is_dir():
        return path / default_filename
    return path


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract a unique individual-drug list from a Topograph TSV."
    )
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        "--input-file",
        "--input_file",
        dest="input_file",
        type=Path,
        help="Topograph TSV path, e.g. TOPOGRAPH-master.tsv.",
    )
    input_group.add_argument(
        "--input-dir",
        "--input_dir",
        dest="input_dir",
        type=Path,
        help="Directory containing TOPOGRAPH-master.tsv.",
    )
    parser.add_argument(
        "--drug-column",
        "--drug_column",
        dest="drug_column",
        default=DRUGS_COLUMN,
        help="Column to extract. Default: Drugs.",
    )
    parser.add_argument(
        "--output-dir",
        "--output_dir",
        dest="output_dir",
        type=Path,
        default=None,
        help="Output directory. Defaults to the input file directory.",
    )
    parser.add_argument(
        "--unique-output-file",
        "--unique_output_file",
        dest="unique_output_file",
        type=Path,
        default=None,
        help=(
            f"Output path for unique drugs. Defaults to <output-dir>/{UNIQUE_OUTPUT_FILENAME}. "
            "If an existing directory is provided, writes the default filename inside it."
        ),
    )
    parser.add_argument(
        "--expanded-output-file",
        "--expanded_output_file",
        dest="expanded_output_file",
        type=Path,
        default=None,
        help=(
            f"Output path for the expanded audit table. Defaults to <output-dir>/{EXPANDED_OUTPUT_FILENAME}. "
            "If an existing directory is provided, writes the default filename inside it."
        ),
    )
    parser.add_argument(
        "--no-expanded",
        "--no_expanded",
        dest="no_expanded",
        action="store_true",
        help="Write only the unique-drug file, not the expanded audit table.",
    )
    parser.add_argument(
        "--sort",
        action="store_true",
        help="Sort the final unique drug list case-insensitively instead of preserving first-seen order.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        input_file = resolve_input_file(args.input_file, args.input_dir)
        base_output_dir = args.output_dir if args.output_dir is not None else input_file.parent

        unique_output_file = resolve_output_file(
            args.unique_output_file,
            base_output_dir,
            UNIQUE_OUTPUT_FILENAME,
        )
        expanded_output_file = None
        if not args.no_expanded:
            expanded_output_file = resolve_output_file(
                args.expanded_output_file,
                base_output_dir,
                EXPANDED_OUTPUT_FILENAME,
            )

        stats = extract_topograph_unique_drugs(
            input_tsv=input_file,
            unique_output_tsv=unique_output_file,
            expanded_output_tsv=expanded_output_file,
            drug_column=args.drug_column,
            sort_unique_drugs=args.sort,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Input file: {input_file}", file=sys.stderr)
    print(f"Wrote unique drugs: {unique_output_file}", file=sys.stderr)
    if expanded_output_file is not None:
        print(f"Wrote expanded audit table: {expanded_output_file}", file=sys.stderr)
    print(f"Input rows: {stats.input_rows}", file=sys.stderr)
    print(f"Rows with nonempty Drugs: {stats.rows_with_nonempty_drugs}", file=sys.stderr)
    print(f"Rows with semicolon delimiter: {stats.rows_with_semicolon}", file=sys.stderr)
    print(f"Rows with plus delimiter: {stats.rows_with_plus}", file=sys.stderr)
    print(f"Rows with both delimiters: {stats.rows_with_both_semicolon_and_plus}", file=sys.stderr)
    print(f"Expanded drug mentions: {stats.expanded_drug_mentions}", file=sys.stderr)
    print(f"Unique drugs: {stats.unique_drugs}", file=sys.stderr)
    print(f"Duplicate mentions removed: {stats.duplicate_mentions_removed}", file=sys.stderr)
    print(f"Blank split tokens dropped: {stats.blank_split_tokens_dropped}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
