#!/usr/bin/env python3
"""Create compact export-ready TSVs from the CTGov/Topograph drug ontology outputs.

Inputs are the processed files produced by the earlier pipeline steps:

    ctgov_unique_drugs.atc_annotated.tsv
    topograph_unique_drugs.atc_annotated.tsv
    ctgov_topograph_drug_crosswalk.tsv
    ctgov_drugs_missing_from_topograph.tsv
    topograph_drugs_missing_from_ctgov.tsv
    ctgov_ingredients_missing_from_topograph.tsv
    topograph_ingredients_missing_from_ctgov.tsv
    ctgov_topograph_drug_comparison_summary.tsv

Outputs are written into an exports directory, defaulting to data/topograph_hartwig_version/exports.
The ATC-annotated files are compacted to one row per input drug term, with
multi-valued ATC fields joined by " | ".
"""

from __future__ import annotations

import argparse
import csv
import shutil
import sys
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

DISPLAY_DELIMITER = " | "

DEFAULT_PROCESSED_DIR = Path("data/topograph_hartwig_version/processed")
DEFAULT_EXPORTS_DIR = Path("data/topograph_hartwig_version/exports")

CTGOV_ANNOTATED_FILENAME = "ctgov_unique_drugs.atc_annotated.tsv"
TOPOGRAPH_ANNOTATED_FILENAME = "topograph_unique_drugs.atc_annotated.tsv"
CROSSWALK_FILENAME = "ctgov_topograph_drug_crosswalk.tsv"
CTGOV_MISSING_TERMS_FILENAME = "ctgov_drugs_missing_from_topograph.tsv"
TOPOGRAPH_MISSING_TERMS_FILENAME = "topograph_drugs_missing_from_ctgov.tsv"
CTGOV_MISSING_INGREDIENTS_FILENAME = "ctgov_ingredients_missing_from_topograph.tsv"
TOPOGRAPH_MISSING_INGREDIENTS_FILENAME = "topograph_ingredients_missing_from_ctgov.tsv"
COMPARISON_SUMMARY_FILENAME = "ctgov_topograph_drug_comparison_summary.tsv"
CTGOV_UNRESOLVED_FILENAME = "ctgov_drugs_unresolved_for_comparison.tsv"
TOPOGRAPH_UNRESOLVED_FILENAME = "topograph_drugs_unresolved_for_comparison.tsv"

CTGOV_COMPACT_ATC_EXPORT_FILENAME = "ctgov_unique_drugs_atc_essential.tsv"
TOPOGRAPH_COMPACT_ATC_EXPORT_FILENAME = "topograph_unique_drugs_atc_essential.tsv"
CROSSWALK_ESSENTIAL_EXPORT_FILENAME = "ctgov_topograph_drug_crosswalk_essential.tsv"
MANIFEST_FILENAME = "export_manifest.tsv"

COMPACT_ANNOTATED_COLUMNS = [
    "source_name",
    "input_drug_name",
    "rxnorm_match_status",
    "rxnorm_match_stage",
    "manual_review_needed",
    "rxnorm_matched_term",
    "rxnorm_rxcui",
    "rxnorm_canonical_name",
    "rxnorm_term_type",
    "rxnorm_ingredient_rxcui",
    "rxnorm_ingredient_name",
    "rxnorm_ingredient_term_type",
    "ingredient_resolution_stage",
    "atc_match_status",
    "atc_codes",
    "atc_names",
    "atc_l1_codes",
    "atc_l1_names",
    "atc_l2_codes",
    "atc_l2_names",
    "atc_l3_codes",
    "atc_l3_names",
    "atc_l4_codes",
    "atc_l4_names",
    "atc_l5_codes",
    "atc_l5_names",
    "atc_bridge_applied",
    "atc_original_codes",
]

CROSSWALK_ESSENTIAL_COLUMNS = [
    "ctgov_input_drug_name",
    "equivalence_status",
    "rxnorm_ingredient_rxcui",
    "ctgov_rxnorm_ingredient_name",
    "ctgov_rxnorm_match_status",
    "ctgov_rxnorm_matched_term",
    "ctgov_rxnorm_canonical_name",
    "topograph_terms",
    "topograph_term_count",
    "topograph_rxnorm_ingredient_names",
    "has_exact_casefold_term_match",
    "ctgov_atc_codes",
    "ctgov_atc_l5_names",
    "topograph_atc_codes",
    "topograph_atc_l5_names",
]

MANIFEST_COLUMNS = [
    "export_file",
    "source_file",
    "row_count",
    "description",
]

TRUE_VALUES = {"true", "t", "1", "yes", "y"}


@dataclass(frozen=True)
class ExportRecord:
    export_file: Path
    source_file: Path
    row_count: int
    description: str


def clean_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"", "nan", "none", "<na>"} else text


def normalize_key(value: object) -> str:
    return " ".join(clean_text(value).casefold().split())


def is_true(value: object) -> bool:
    return clean_text(value).lower() in TRUE_VALUES


def ordered_join(values: Iterable[object]) -> str:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = clean_text(value)
        key = normalize_key(text)
        if text and key not in seen:
            seen.add(key)
            out.append(text)
    return DISPLAY_DELIMITER.join(out)


def read_tsv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.exists():
        raise FileNotFoundError(f"Required input file not found: {path}")
    if not path.is_file():
        raise IsADirectoryError(f"Required input path is not a file: {path}")

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames is None:
            raise ValueError(f"TSV has no header row: {path}")
        fieldnames = [clean_text(field) for field in reader.fieldnames]
        reader.fieldnames = fieldnames
        rows = [{key: clean_text(value) for key, value in row.items()} for row in reader]
    return fieldnames, rows


def write_tsv(path: Path, rows: Sequence[Mapping[str, object]], fieldnames: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(fieldnames),
            delimiter="\t",
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def require_columns(fieldnames: Sequence[str], required: Sequence[str], path: Path) -> None:
    missing = [column for column in required if column not in fieldnames]
    if missing:
        raise ValueError(
            f"{path} is missing required columns: {missing}. Available columns: {list(fieldnames)}"
        )


def compact_atc_annotated_file(input_path: Path, output_path: Path) -> int:
    """Collapse long ATC annotation rows to one row per input drug term."""
    fieldnames, rows = read_tsv(input_path)
    require_columns(
        fieldnames,
        [
            "source_name",
            "input_drug_name",
            "rxnorm_match_status",
            "rxnorm_ingredient_rxcui",
            "atc_match_status",
        ],
        input_path,
    )

    grouped: OrderedDict[str, dict[str, object]] = OrderedDict()
    for row in rows:
        input_drug_name = clean_text(row.get("input_drug_name"))
        if not input_drug_name:
            continue

        key = normalize_key(input_drug_name)
        record = grouped.get(key)
        if record is None:
            record = {
                "source_name": row.get("source_name", ""),
                "input_drug_name": input_drug_name,
                "rxnorm_match_status": row.get("rxnorm_match_status", ""),
                "rxnorm_match_stage": row.get("rxnorm_match_stage", ""),
                "manual_review_needed": row.get("manual_review_needed", ""),
                "rxnorm_matched_term": row.get("rxnorm_matched_term", ""),
                "rxnorm_rxcui": row.get("rxnorm_rxcui", ""),
                "rxnorm_canonical_name": row.get("rxnorm_canonical_name", ""),
                "rxnorm_term_type": row.get("rxnorm_term_type", ""),
                "rxnorm_ingredient_rxcui": row.get("rxnorm_ingredient_rxcui", ""),
                "rxnorm_ingredient_name": row.get("rxnorm_ingredient_name", ""),
                "rxnorm_ingredient_term_type": row.get("rxnorm_ingredient_term_type", ""),
                "ingredient_resolution_stage": row.get("ingredient_resolution_stage", ""),
                "_manual_review_any": is_true(row.get("manual_review_needed")),
                "_atc_match_status": [],
                "_atc_codes": [],
                "_atc_names": [],
                "_atc_l1_codes": [],
                "_atc_l1_names": [],
                "_atc_l2_codes": [],
                "_atc_l2_names": [],
                "_atc_l3_codes": [],
                "_atc_l3_names": [],
                "_atc_l4_codes": [],
                "_atc_l4_names": [],
                "_atc_l5_codes": [],
                "_atc_l5_names": [],
                "_atc_bridge_any": is_true(row.get("atc_bridge_applied")),
                "_atc_original_codes": [],
            }
            grouped[key] = record
        else:
            record["_manual_review_any"] = bool(record["_manual_review_any"]) or is_true(row.get("manual_review_needed"))
            record["_atc_bridge_any"] = bool(record["_atc_bridge_any"]) or is_true(row.get("atc_bridge_applied"))

        list_fields = {
            "_atc_match_status": "atc_match_status",
            "_atc_codes": "atc_code",
            "_atc_names": "atc_name",
            "_atc_l1_codes": "atc_l1_code",
            "_atc_l1_names": "atc_l1_name",
            "_atc_l2_codes": "atc_l2_code",
            "_atc_l2_names": "atc_l2_name",
            "_atc_l3_codes": "atc_l3_code",
            "_atc_l3_names": "atc_l3_name",
            "_atc_l4_codes": "atc_l4_code",
            "_atc_l4_names": "atc_l4_name",
            "_atc_l5_codes": "atc_l5_code",
            "_atc_l5_names": "atc_l5_name",
            "_atc_original_codes": "atc_original_code",
        }
        for out_key, in_key in list_fields.items():
            value = clean_text(row.get(in_key, ""))
            if value:
                cast_list = record[out_key]
                assert isinstance(cast_list, list)
                cast_list.append(value)

    compact_rows: list[dict[str, object]] = []
    for record in grouped.values():
        compact_rows.append(
            {
                "source_name": record["source_name"],
                "input_drug_name": record["input_drug_name"],
                "rxnorm_match_status": record["rxnorm_match_status"],
                "rxnorm_match_stage": record["rxnorm_match_stage"],
                "manual_review_needed": "true" if record["_manual_review_any"] else "false",
                "rxnorm_matched_term": record["rxnorm_matched_term"],
                "rxnorm_rxcui": record["rxnorm_rxcui"],
                "rxnorm_canonical_name": record["rxnorm_canonical_name"],
                "rxnorm_term_type": record["rxnorm_term_type"],
                "rxnorm_ingredient_rxcui": record["rxnorm_ingredient_rxcui"],
                "rxnorm_ingredient_name": record["rxnorm_ingredient_name"],
                "rxnorm_ingredient_term_type": record["rxnorm_ingredient_term_type"],
                "ingredient_resolution_stage": record["ingredient_resolution_stage"],
                "atc_match_status": ordered_join(record["_atc_match_status"]),
                "atc_codes": ordered_join(record["_atc_codes"]),
                "atc_names": ordered_join(record["_atc_names"]),
                "atc_l1_codes": ordered_join(record["_atc_l1_codes"]),
                "atc_l1_names": ordered_join(record["_atc_l1_names"]),
                "atc_l2_codes": ordered_join(record["_atc_l2_codes"]),
                "atc_l2_names": ordered_join(record["_atc_l2_names"]),
                "atc_l3_codes": ordered_join(record["_atc_l3_codes"]),
                "atc_l3_names": ordered_join(record["_atc_l3_names"]),
                "atc_l4_codes": ordered_join(record["_atc_l4_codes"]),
                "atc_l4_names": ordered_join(record["_atc_l4_names"]),
                "atc_l5_codes": ordered_join(record["_atc_l5_codes"]),
                "atc_l5_names": ordered_join(record["_atc_l5_names"]),
                "atc_bridge_applied": "true" if record["_atc_bridge_any"] else "false",
                "atc_original_codes": ordered_join(record["_atc_original_codes"]),
            }
        )

    write_tsv(output_path, compact_rows, COMPACT_ANNOTATED_COLUMNS)
    return len(compact_rows)


def select_columns(input_path: Path, output_path: Path, columns: Sequence[str]) -> int:
    fieldnames, rows = read_tsv(input_path)
    require_columns(fieldnames, columns, input_path)
    selected_rows = [{column: row.get(column, "") for column in columns} for row in rows]
    write_tsv(output_path, selected_rows, columns)
    return len(selected_rows)


def copy_tsv(input_path: Path, output_path: Path) -> int:
    _, rows = read_tsv(input_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(input_path, output_path)
    return len(rows)


def write_manifest(exports_dir: Path, export_records: Sequence[ExportRecord]) -> None:
    rows = [
        {
            "export_file": str(record.export_file),
            "source_file": str(record.source_file),
            "row_count": record.row_count,
            "description": record.description,
        }
        for record in export_records
    ]
    write_tsv(exports_dir / MANIFEST_FILENAME, rows, MANIFEST_COLUMNS)


def export_essential_files(
    processed_dir: Path,
    exports_dir: Path,
    *,
    include_unresolved_qc: bool = False,
) -> list[ExportRecord]:
    processed_dir = processed_dir.resolve()
    exports_dir = exports_dir.resolve()
    exports_dir.mkdir(parents=True, exist_ok=True)

    records: list[ExportRecord] = []

    ctgov_annotated = processed_dir / CTGOV_ANNOTATED_FILENAME
    ctgov_annotated_export = exports_dir / CTGOV_COMPACT_ATC_EXPORT_FILENAME
    row_count = compact_atc_annotated_file(ctgov_annotated, ctgov_annotated_export)
    records.append(
        ExportRecord(
            export_file=ctgov_annotated_export,
            source_file=ctgov_annotated,
            row_count=row_count,
            description="Unique CTGov drug terms compactly annotated with RxNorm ingredient identity and ATC classes.",
        )
    )

    topograph_annotated = processed_dir / TOPOGRAPH_ANNOTATED_FILENAME
    topograph_annotated_export = exports_dir / TOPOGRAPH_COMPACT_ATC_EXPORT_FILENAME
    row_count = compact_atc_annotated_file(topograph_annotated, topograph_annotated_export)
    records.append(
        ExportRecord(
            export_file=topograph_annotated_export,
            source_file=topograph_annotated,
            row_count=row_count,
            description="Unique Topograph drug terms compactly annotated with RxNorm ingredient identity and ATC classes.",
        )
    )

    crosswalk = processed_dir / CROSSWALK_FILENAME
    crosswalk_export = exports_dir / CROSSWALK_ESSENTIAL_EXPORT_FILENAME
    row_count = select_columns(crosswalk, crosswalk_export, CROSSWALK_ESSENTIAL_COLUMNS)
    records.append(
        ExportRecord(
            export_file=crosswalk_export,
            source_file=crosswalk,
            row_count=row_count,
            description="CTGov drug terms with Topograph equivalents by shared RxNorm ingredient RXCUI.",
        )
    )

    copy_specs = [
        (
            CTGOV_MISSING_TERMS_FILENAME,
            "CTGov source drug terms whose RxNorm ingredient is absent from Topograph.",
        ),
        (
            TOPOGRAPH_MISSING_TERMS_FILENAME,
            "Topograph source drug terms whose RxNorm ingredient is absent from CTGov.",
        ),
        (
            CTGOV_MISSING_INGREDIENTS_FILENAME,
            "CTGov RxNorm ingredients absent from Topograph, collapsed to ingredient level.",
        ),
        (
            TOPOGRAPH_MISSING_INGREDIENTS_FILENAME,
            "Topograph RxNorm ingredients absent from CTGov, collapsed to ingredient level.",
        ),
        (
            COMPARISON_SUMMARY_FILENAME,
            "Summary counts for CTGov versus Topograph drug vocabulary comparison.",
        ),
    ]

    if include_unresolved_qc:
        copy_specs.extend(
            [
                (
                    CTGOV_UNRESOLVED_FILENAME,
                    "CTGov source terms without a comparable matched RxNorm ingredient; QC only.",
                ),
                (
                    TOPOGRAPH_UNRESOLVED_FILENAME,
                    "Topograph source terms without a comparable matched RxNorm ingredient; QC only.",
                ),
            ]
        )

    for filename, description in copy_specs:
        source_path = processed_dir / filename
        export_path = exports_dir / filename
        row_count = copy_tsv(source_path, export_path)
        records.append(
            ExportRecord(
                export_file=export_path,
                source_file=source_path,
                row_count=row_count,
                description=description,
            )
        )

    write_manifest(exports_dir, records)
    records.append(
        ExportRecord(
            export_file=exports_dir / MANIFEST_FILENAME,
            source_file=processed_dir,
            row_count=len(records),
            description="Manifest describing the essential export files.",
        )
    )
    return records


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Export compact user-facing Topograph/CTGov drug ontology outputs "
            "from processed intermediate files."
        )
    )
    parser.add_argument(
        "--processed-dir",
        "--processed_dir",
        dest="processed_dir",
        type=Path,
        default=DEFAULT_PROCESSED_DIR,
        help=(
            "Directory containing processed Topograph/CTGov outputs. "
            f"Default: {DEFAULT_PROCESSED_DIR}"
        ),
    )
    parser.add_argument(
        "--exports-dir",
        "--exports_dir",
        dest="exports_dir",
        type=Path,
        default=DEFAULT_EXPORTS_DIR,
        help=(
            "Directory where compact export files should be written. "
            f"Default: {DEFAULT_EXPORTS_DIR}"
        ),
    )
    parser.add_argument(
        "--include-unresolved-qc",
        "--include_unresolved_qc",
        dest="include_unresolved_qc",
        action="store_true",
        help="Also export unresolved CTGov/Topograph terms that could not be compared safely.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    try:
        records = export_essential_files(
            processed_dir=args.processed_dir,
            exports_dir=args.exports_dir,
            include_unresolved_qc=args.include_unresolved_qc,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    for record in records:
        print(f"Wrote {record.row_count} rows: {record.export_file}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
