from __future__ import annotations

"""Compare CTGov and Topograph drug vocabularies after RxNorm/ATC annotation.

The comparison is identity-based, not string-based and not ATC-based:

    input drug term -> RxNorm ingredient RXCUI

A CTGov term is considered present in Topograph if its resolved RxNorm ingredient
RXCUI is also present among the resolved Topograph terms. The reverse comparison
uses the same rule.

Rows that do not have a matched RxNorm ingredient are written to separate
"unresolved" files. They are not treated as definitively missing, because they
are not safely comparable.
"""

import argparse
import csv
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Sequence

INPUT_TERM_COLUMN_CANDIDATES = (
    "input_drug_name",
    "drug_name",
    "drug",
    "Drugs",
    "input_term",
    "ctgov_input_drug_name",
    "topograph_input_drug_name",
)

MATCH_STATUS_COLUMN_CANDIDATES = (
    "rxnorm_match_status",
    "match_status",
)

MATCH_STAGE_COLUMN_CANDIDATES = (
    "rxnorm_match_stage",
    "match_stage",
)

MATCHED_TERM_COLUMN_CANDIDATES = (
    "rxnorm_matched_term",
    "matched_term",
)

RXCUI_COLUMN_CANDIDATES = (
    "rxnorm_rxcui",
    "rxcui",
)

CANONICAL_NAME_COLUMN_CANDIDATES = (
    "rxnorm_canonical_name",
    "canonical_name",
)

INGREDIENT_RXCUI_COLUMN_CANDIDATES = (
    "rxnorm_ingredient_rxcui",
    "ingredient_rxcui",
)

INGREDIENT_NAME_COLUMN_CANDIDATES = (
    "rxnorm_ingredient_name",
    "ingredient_name",
)

ATC_CODE_COLUMN_CANDIDATES = (
    "atc_code",
    "ATC code",
)

ATC_NAME_COLUMN_CANDIDATES = (
    "atc_name",
    "ATC level name",
)

ATC_L1_COLUMN_CANDIDATES = (
    "atc_l1_name",
)

ATC_L2_COLUMN_CANDIDATES = (
    "atc_l2_name",
)

TRUE_VALUES = {"true", "t", "1", "yes", "y"}

TERM_MISSING_COLUMNS = [
    "source_name",
    "comparison_status",
    "input_drug_name",
    "rxnorm_match_status",
    "rxnorm_match_stage",
    "rxnorm_matched_term",
    "rxnorm_rxcui",
    "rxnorm_canonical_name",
    "rxnorm_ingredient_rxcui",
    "rxnorm_ingredient_name",
    "manual_review_needed",
    "atc_codes",
    "atc_names",
    "atc_l1_names",
    "atc_l2_names",
]

INGREDIENT_MISSING_COLUMNS = [
    "source_name",
    "comparison_status",
    "rxnorm_ingredient_rxcui",
    "rxnorm_ingredient_name",
    "source_terms",
    "source_term_count",
    "atc_codes",
    "atc_names",
    "atc_l1_names",
    "atc_l2_names",
]

SUMMARY_COLUMNS = ["metric", "value"]


@dataclass
class DrugRecord:
    source_name: str
    input_drug_name: str
    rxnorm_match_status: str = ""
    rxnorm_match_stage: str = ""
    rxnorm_matched_term: str = ""
    rxnorm_rxcui: str = ""
    rxnorm_canonical_name: str = ""
    rxnorm_ingredient_rxcui: str = ""
    rxnorm_ingredient_name: str = ""
    manual_review_needed: bool = False
    atc_codes: set[str] = field(default_factory=set)
    atc_names: set[str] = field(default_factory=set)
    atc_l1_names: set[str] = field(default_factory=set)
    atc_l2_names: set[str] = field(default_factory=set)

    @property
    def is_rxnorm_ingredient_comparable(self) -> bool:
        return (
            self.rxnorm_match_status.upper() == "MATCHED"
            and bool(self.rxnorm_ingredient_rxcui)
        )


def clean_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"", "nan", "none", "<na>"} else text


def normalize_key(value: object) -> str:
    return " ".join(clean_text(value).lower().split())


def is_true(value: object) -> bool:
    return clean_text(value).lower() in TRUE_VALUES


def ordered_join(values: Iterable[str]) -> str:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = clean_text(value)
        key = normalize_key(text)
        if text and key not in seen:
            seen.add(key)
            out.append(text)
    return " | ".join(out)


def first_existing_column(fieldnames: Sequence[str], candidates: Sequence[str]) -> str | None:
    fieldname_set = set(fieldnames)
    for candidate in candidates:
        if candidate in fieldname_set:
            return candidate
    return None


def require_column(fieldnames: Sequence[str], candidates: Sequence[str], path: Path, purpose: str) -> str:
    column = first_existing_column(fieldnames, candidates)
    if column is None:
        raise ValueError(
            f"Could not find a column for {purpose} in {path}. "
            f"Accepted names: {list(candidates)}. Available columns: {list(fieldnames)}"
        )
    return column


def read_annotated_drug_records(path: Path, source_name: str) -> list[DrugRecord]:
    if not path.exists():
        raise FileNotFoundError(f"Annotated TSV not found: {path}")
    if not path.is_file():
        raise IsADirectoryError(f"Annotated TSV path is not a file: {path}")

    grouped: dict[tuple[str, str], DrugRecord] = {}

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames is None:
            raise ValueError(f"Annotated TSV has no header row: {path}")

        fieldnames = [clean_text(field) for field in reader.fieldnames]
        reader.fieldnames = fieldnames

        term_col = require_column(fieldnames, INPUT_TERM_COLUMN_CANDIDATES, path, "input drug name")
        match_status_col = first_existing_column(fieldnames, MATCH_STATUS_COLUMN_CANDIDATES)
        match_stage_col = first_existing_column(fieldnames, MATCH_STAGE_COLUMN_CANDIDATES)
        matched_term_col = first_existing_column(fieldnames, MATCHED_TERM_COLUMN_CANDIDATES)
        rxcui_col = first_existing_column(fieldnames, RXCUI_COLUMN_CANDIDATES)
        canonical_name_col = first_existing_column(fieldnames, CANONICAL_NAME_COLUMN_CANDIDATES)
        ingredient_rxcui_col = first_existing_column(fieldnames, INGREDIENT_RXCUI_COLUMN_CANDIDATES)
        ingredient_name_col = first_existing_column(fieldnames, INGREDIENT_NAME_COLUMN_CANDIDATES)
        atc_code_col = first_existing_column(fieldnames, ATC_CODE_COLUMN_CANDIDATES)
        atc_name_col = first_existing_column(fieldnames, ATC_NAME_COLUMN_CANDIDATES)
        atc_l1_col = first_existing_column(fieldnames, ATC_L1_COLUMN_CANDIDATES)
        atc_l2_col = first_existing_column(fieldnames, ATC_L2_COLUMN_CANDIDATES)
        manual_review_col = first_existing_column(fieldnames, ("manual_review_needed",))

        for row_number, row in enumerate(reader, start=2):
            input_drug_name = clean_text(row.get(term_col, ""))
            if not input_drug_name:
                continue

            ingredient_rxcui = clean_text(row.get(ingredient_rxcui_col, "")) if ingredient_rxcui_col else ""
            # Group by input term plus ingredient anchor. This preserves rare cases where
            # one input term is represented by more than one ingredient-level concept.
            group_key = (normalize_key(input_drug_name), ingredient_rxcui)

            record = grouped.get(group_key)
            if record is None:
                record = DrugRecord(
                    source_name=source_name,
                    input_drug_name=input_drug_name,
                    rxnorm_match_status=clean_text(row.get(match_status_col, "")) if match_status_col else "",
                    rxnorm_match_stage=clean_text(row.get(match_stage_col, "")) if match_stage_col else "",
                    rxnorm_matched_term=clean_text(row.get(matched_term_col, "")) if matched_term_col else "",
                    rxnorm_rxcui=clean_text(row.get(rxcui_col, "")) if rxcui_col else "",
                    rxnorm_canonical_name=clean_text(row.get(canonical_name_col, "")) if canonical_name_col else "",
                    rxnorm_ingredient_rxcui=ingredient_rxcui,
                    rxnorm_ingredient_name=clean_text(row.get(ingredient_name_col, "")) if ingredient_name_col else "",
                    manual_review_needed=is_true(row.get(manual_review_col, "")) if manual_review_col else False,
                )
                grouped[group_key] = record
            else:
                # Keep the first spelling/status, but merge secondary evidence.
                record.manual_review_needed = record.manual_review_needed or (
                    is_true(row.get(manual_review_col, "")) if manual_review_col else False
                )

            atc_code = clean_text(row.get(atc_code_col, "")) if atc_code_col else ""
            atc_name = clean_text(row.get(atc_name_col, "")) if atc_name_col else ""
            atc_l1 = clean_text(row.get(atc_l1_col, "")) if atc_l1_col else ""
            atc_l2 = clean_text(row.get(atc_l2_col, "")) if atc_l2_col else ""

            if atc_code:
                record.atc_codes.add(atc_code)
            if atc_name:
                record.atc_names.add(atc_name)
            if atc_l1:
                record.atc_l1_names.add(atc_l1)
            if atc_l2:
                record.atc_l2_names.add(atc_l2)

    return sorted(grouped.values(), key=lambda r: normalize_key(r.input_drug_name))


def comparable_ingredient_keys(records: Sequence[DrugRecord]) -> set[str]:
    return {
        record.rxnorm_ingredient_rxcui
        for record in records
        if record.is_rxnorm_ingredient_comparable
    }


def missing_by_ingredient_key(
    source_records: Sequence[DrugRecord],
    other_source_ingredient_keys: set[str],
) -> list[DrugRecord]:
    return [
        record
        for record in source_records
        if record.is_rxnorm_ingredient_comparable
        and record.rxnorm_ingredient_rxcui not in other_source_ingredient_keys
    ]


def unresolved_records(records: Sequence[DrugRecord]) -> list[DrugRecord]:
    return [record for record in records if not record.is_rxnorm_ingredient_comparable]


def term_record_to_row(record: DrugRecord, comparison_status: str) -> dict[str, object]:
    return {
        "source_name": record.source_name,
        "comparison_status": comparison_status,
        "input_drug_name": record.input_drug_name,
        "rxnorm_match_status": record.rxnorm_match_status,
        "rxnorm_match_stage": record.rxnorm_match_stage,
        "rxnorm_matched_term": record.rxnorm_matched_term,
        "rxnorm_rxcui": record.rxnorm_rxcui,
        "rxnorm_canonical_name": record.rxnorm_canonical_name,
        "rxnorm_ingredient_rxcui": record.rxnorm_ingredient_rxcui,
        "rxnorm_ingredient_name": record.rxnorm_ingredient_name,
        "manual_review_needed": "true" if record.manual_review_needed else "false",
        "atc_codes": ordered_join(sorted(record.atc_codes)),
        "atc_names": ordered_join(sorted(record.atc_names)),
        "atc_l1_names": ordered_join(sorted(record.atc_l1_names)),
        "atc_l2_names": ordered_join(sorted(record.atc_l2_names)),
    }


def ingredient_rows(
    source_name: str,
    records: Sequence[DrugRecord],
    comparison_status: str,
) -> list[dict[str, object]]:
    grouped: dict[str, list[DrugRecord]] = defaultdict(list)
    for record in records:
        if record.rxnorm_ingredient_rxcui:
            grouped[record.rxnorm_ingredient_rxcui].append(record)

    rows: list[dict[str, object]] = []
    for ingredient_rxcui in sorted(grouped):
        members = grouped[ingredient_rxcui]
        ingredient_names = [member.rxnorm_ingredient_name for member in members]
        atc_codes: set[str] = set()
        atc_names: set[str] = set()
        atc_l1_names: set[str] = set()
        atc_l2_names: set[str] = set()
        for member in members:
            atc_codes.update(member.atc_codes)
            atc_names.update(member.atc_names)
            atc_l1_names.update(member.atc_l1_names)
            atc_l2_names.update(member.atc_l2_names)

        rows.append(
            {
                "source_name": source_name,
                "comparison_status": comparison_status,
                "rxnorm_ingredient_rxcui": ingredient_rxcui,
                "rxnorm_ingredient_name": ordered_join(ingredient_names),
                "source_terms": ordered_join(member.input_drug_name for member in members),
                "source_term_count": len({normalize_key(member.input_drug_name) for member in members}),
                "atc_codes": ordered_join(sorted(atc_codes)),
                "atc_names": ordered_join(sorted(atc_names)),
                "atc_l1_names": ordered_join(sorted(atc_l1_names)),
                "atc_l2_names": ordered_join(sorted(atc_l2_names)),
            }
        )

    return rows


def write_tsv(path: Path, rows: Sequence[Mapping[str, object]], fieldnames: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(fieldnames),
            delimiter="\t",
            lineterminator="\n",
            extrasaction="ignore",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def compare_ctgov_topograph_drugs(
    ctgov_annotated_tsv: Path,
    topograph_annotated_tsv: Path,
    output_dir: Path,
) -> dict[str, Path]:
    ctgov_records = read_annotated_drug_records(ctgov_annotated_tsv, source_name="ctgov")
    topograph_records = read_annotated_drug_records(topograph_annotated_tsv, source_name="topograph")

    ctgov_keys = comparable_ingredient_keys(ctgov_records)
    topograph_keys = comparable_ingredient_keys(topograph_records)

    ctgov_missing = missing_by_ingredient_key(ctgov_records, topograph_keys)
    topograph_missing = missing_by_ingredient_key(topograph_records, ctgov_keys)
    ctgov_unresolved = unresolved_records(ctgov_records)
    topograph_unresolved = unresolved_records(topograph_records)

    output_dir.mkdir(parents=True, exist_ok=True)

    paths = {
        "ctgov_missing_terms": output_dir / "ctgov_drugs_missing_from_topograph.tsv",
        "topograph_missing_terms": output_dir / "topograph_drugs_missing_from_ctgov.tsv",
        "ctgov_missing_ingredients": output_dir / "ctgov_ingredients_missing_from_topograph.tsv",
        "topograph_missing_ingredients": output_dir / "topograph_ingredients_missing_from_ctgov.tsv",
        "ctgov_unresolved": output_dir / "ctgov_drugs_unresolved_for_comparison.tsv",
        "topograph_unresolved": output_dir / "topograph_drugs_unresolved_for_comparison.tsv",
        "summary": output_dir / "ctgov_topograph_drug_comparison_summary.tsv",
    }

    write_tsv(
        paths["ctgov_missing_terms"],
        [term_record_to_row(record, "MISSING_FROM_TOPOGRAPH_BY_RXNORM_INGREDIENT") for record in ctgov_missing],
        TERM_MISSING_COLUMNS,
    )
    write_tsv(
        paths["topograph_missing_terms"],
        [term_record_to_row(record, "MISSING_FROM_CTGOV_BY_RXNORM_INGREDIENT") for record in topograph_missing],
        TERM_MISSING_COLUMNS,
    )
    write_tsv(
        paths["ctgov_missing_ingredients"],
        ingredient_rows("ctgov", ctgov_missing, "MISSING_FROM_TOPOGRAPH_BY_RXNORM_INGREDIENT"),
        INGREDIENT_MISSING_COLUMNS,
    )
    write_tsv(
        paths["topograph_missing_ingredients"],
        ingredient_rows("topograph", topograph_missing, "MISSING_FROM_CTGOV_BY_RXNORM_INGREDIENT"),
        INGREDIENT_MISSING_COLUMNS,
    )
    write_tsv(
        paths["ctgov_unresolved"],
        [term_record_to_row(record, "UNCOMPARABLE_NO_MATCHED_RXNORM_INGREDIENT") for record in ctgov_unresolved],
        TERM_MISSING_COLUMNS,
    )
    write_tsv(
        paths["topograph_unresolved"],
        [term_record_to_row(record, "UNCOMPARABLE_NO_MATCHED_RXNORM_INGREDIENT") for record in topograph_unresolved],
        TERM_MISSING_COLUMNS,
    )

    intersection_keys = ctgov_keys & topograph_keys
    summary_rows = [
        {"metric": "ctgov_unique_input_terms_after_annotation_grouping", "value": len(ctgov_records)},
        {"metric": "topograph_unique_input_terms_after_annotation_grouping", "value": len(topograph_records)},
        {"metric": "ctgov_comparable_rxnorm_ingredient_terms", "value": sum(r.is_rxnorm_ingredient_comparable for r in ctgov_records)},
        {"metric": "topograph_comparable_rxnorm_ingredient_terms", "value": sum(r.is_rxnorm_ingredient_comparable for r in topograph_records)},
        {"metric": "ctgov_unique_comparable_rxnorm_ingredients", "value": len(ctgov_keys)},
        {"metric": "topograph_unique_comparable_rxnorm_ingredients", "value": len(topograph_keys)},
        {"metric": "shared_unique_rxnorm_ingredients", "value": len(intersection_keys)},
        {"metric": "ctgov_missing_from_topograph_terms", "value": len(ctgov_missing)},
        {"metric": "topograph_missing_from_ctgov_terms", "value": len(topograph_missing)},
        {"metric": "ctgov_missing_from_topograph_ingredients", "value": len({r.rxnorm_ingredient_rxcui for r in ctgov_missing})},
        {"metric": "topograph_missing_from_ctgov_ingredients", "value": len({r.rxnorm_ingredient_rxcui for r in topograph_missing})},
        {"metric": "ctgov_unresolved_or_unmatched_terms", "value": len(ctgov_unresolved)},
        {"metric": "topograph_unresolved_or_unmatched_terms", "value": len(topograph_unresolved)},
    ]
    write_tsv(paths["summary"], summary_rows, SUMMARY_COLUMNS)

    return paths


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "List CTGov drugs missing from Topograph and Topograph drugs missing "
            "from CTGov using RxNorm ingredient RXCUI identity."
        )
    )
    parser.add_argument(
        "--ctgov-annotated-tsv",
        "--ctgov_annotated_tsv",
        dest="ctgov_annotated_tsv",
        type=Path,
        required=True,
        help="CTGov unique-drug ATC annotation TSV.",
    )
    parser.add_argument(
        "--topograph-annotated-tsv",
        "--topograph_annotated_tsv",
        dest="topograph_annotated_tsv",
        type=Path,
        required=True,
        help="Topograph unique-drug ATC annotation TSV.",
    )
    parser.add_argument(
        "--output-dir",
        "--output_dir",
        dest="output_dir",
        type=Path,
        required=True,
        help="Directory for comparison output TSVs.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        paths = compare_ctgov_topograph_drugs(
            ctgov_annotated_tsv=args.ctgov_annotated_tsv,
            topograph_annotated_tsv=args.topograph_annotated_tsv,
            output_dir=args.output_dir,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    for label, path in paths.items():
        print(f"Wrote {label}: {path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
