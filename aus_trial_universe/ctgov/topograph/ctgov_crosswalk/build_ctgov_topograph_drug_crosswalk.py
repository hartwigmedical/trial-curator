#!/usr/bin/env python3
"""Build a CTGov <-> Topograph drug-term crosswalk from ATC-annotated drug files.

Input files are the outputs from annotate_unique_drugs_with_atc.py.

Equivalence rule
----------------

Use RxNorm ingredient RXCUI as the identity key.

ATC is carried through for context and classification, but ATC is not used as
the primary identity key because multiple ingredients can share higher-level ATC
classes and some RxNorm ingredients have multiple ATC codes.

Output grain
------------

One row per CTGov input_drug_name.

A CTGov term may map to zero, one, or multiple Topograph terms via the shared
RxNorm ingredient RXCUI.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

CTGOV_DEFAULT_ANNOTATED = Path("data/ctgov/drug_ontology/processed/ctgov_unique_drugs.atc_annotated.tsv")
TOPOGRAPH_DEFAULT_ANNOTATED = Path("data/topograph_hartwig_version/processed/topograph_unique_drugs.atc_annotated.tsv")
DEFAULT_OUTPUT = Path("data/ctgov/drug_ontology/processed/ctgov_topograph_drug_crosswalk.tsv")

DISPLAY_DELIMITER = " | "

OUTPUT_COLUMNS = [
    "ctgov_input_drug_name",
    "equivalence_status",
    "rxnorm_ingredient_rxcui",
    "ctgov_rxnorm_ingredient_name",
    "ctgov_rxnorm_match_status",
    "ctgov_rxnorm_matched_term",
    "ctgov_rxnorm_canonical_name",
    "ctgov_atc_codes",
    "ctgov_atc_l5_names",
    "topograph_terms",
    "topograph_term_count",
    "topograph_rxnorm_ingredient_names",
    "topograph_atc_codes",
    "topograph_atc_l5_names",
    "has_exact_casefold_term_match",
]


@dataclass(frozen=True)
class AnnotatedTermSummary:
    input_drug_name: str
    rxnorm_match_status: str
    rxnorm_matched_term: str
    rxnorm_canonical_name: str
    rxnorm_ingredient_rxcui: str
    rxnorm_ingredient_name: str
    atc_codes: tuple[str, ...]
    atc_l5_names: tuple[str, ...]


def clean_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"", "nan", "none", "<na>"} else text


def norm_key(value: object) -> str:
    return " ".join(clean_text(value).casefold().split())


def ordered_unique(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = clean_text(value)
        key = norm_key(cleaned)
        if cleaned and key not in seen:
            seen.add(key)
            out.append(cleaned)
    return out


def join_values(values: Iterable[str]) -> str:
    return DISPLAY_DELIMITER.join(ordered_unique(values))


def read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Annotated TSV not found: {path}")
    if not path.is_file():
        raise IsADirectoryError(f"Annotated TSV is not a file: {path}")

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames is None:
            raise ValueError(f"Annotated TSV has no header: {path}")
        return [dict(row) for row in reader]


def summarize_annotated_terms(path: Path) -> dict[str, AnnotatedTermSummary]:
    """Collapse long annotated rows to one summary per input_drug_name."""
    rows = read_tsv(path)
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)

    for row in rows:
        term = clean_text(row.get("input_drug_name", ""))
        if term:
            grouped[term].append(row)

    summaries: dict[str, AnnotatedTermSummary] = {}
    for term, term_rows in grouped.items():
        first = term_rows[0]
        summaries[term] = AnnotatedTermSummary(
            input_drug_name=term,
            rxnorm_match_status=clean_text(first.get("rxnorm_match_status", "")),
            rxnorm_matched_term=clean_text(first.get("rxnorm_matched_term", "")),
            rxnorm_canonical_name=clean_text(first.get("rxnorm_canonical_name", "")),
            rxnorm_ingredient_rxcui=clean_text(first.get("rxnorm_ingredient_rxcui", "")),
            rxnorm_ingredient_name=clean_text(first.get("rxnorm_ingredient_name", "")),
            atc_codes=tuple(
                ordered_unique(
                    row.get("atc_code", "")
                    for row in term_rows
                    if clean_text(row.get("atc_match_status", "")) == "MATCHED_ATC"
                )
            ),
            atc_l5_names=tuple(
                ordered_unique(
                    row.get("atc_l5_name", "")
                    for row in term_rows
                    if clean_text(row.get("atc_match_status", "")) == "MATCHED_ATC"
                )
            ),
        )

    return summaries


def build_ingredient_to_terms(
    summaries: Mapping[str, AnnotatedTermSummary],
) -> dict[str, list[AnnotatedTermSummary]]:
    ingredient_to_terms: dict[str, list[AnnotatedTermSummary]] = defaultdict(list)
    for summary in summaries.values():
        if summary.rxnorm_match_status != "MATCHED":
            continue
        if not summary.rxnorm_ingredient_rxcui:
            continue
        ingredient_to_terms[summary.rxnorm_ingredient_rxcui].append(summary)
    return ingredient_to_terms


def equivalence_status(
    ctgov_summary: AnnotatedTermSummary,
    topograph_matches: Sequence[AnnotatedTermSummary],
) -> str:
    if ctgov_summary.rxnorm_match_status != "MATCHED":
        return "NO_CONFIDENT_CTGOV_RXNORM_MATCH"
    if not ctgov_summary.rxnorm_ingredient_rxcui:
        return "NO_CTGOV_RXNORM_INGREDIENT"
    if not topograph_matches:
        return "NO_TOPOGRAPH_TERM_FOR_INGREDIENT"
    return "TOPOGRAPH_INGREDIENT_MATCH"


def crosswalk_record(
    ctgov_summary: AnnotatedTermSummary,
    topograph_matches: Sequence[AnnotatedTermSummary],
) -> dict[str, object]:
    ctgov_term_key = norm_key(ctgov_summary.input_drug_name)
    topograph_term_keys = {norm_key(match.input_drug_name) for match in topograph_matches}

    return {
        "ctgov_input_drug_name": ctgov_summary.input_drug_name,
        "equivalence_status": equivalence_status(ctgov_summary, topograph_matches),
        "rxnorm_ingredient_rxcui": ctgov_summary.rxnorm_ingredient_rxcui,
        "ctgov_rxnorm_ingredient_name": ctgov_summary.rxnorm_ingredient_name,
        "ctgov_rxnorm_match_status": ctgov_summary.rxnorm_match_status,
        "ctgov_rxnorm_matched_term": ctgov_summary.rxnorm_matched_term,
        "ctgov_rxnorm_canonical_name": ctgov_summary.rxnorm_canonical_name,
        "ctgov_atc_codes": join_values(ctgov_summary.atc_codes),
        "ctgov_atc_l5_names": join_values(ctgov_summary.atc_l5_names),
        "topograph_terms": join_values(match.input_drug_name for match in topograph_matches),
        "topograph_term_count": len(topograph_matches),
        "topograph_rxnorm_ingredient_names": join_values(match.rxnorm_ingredient_name for match in topograph_matches),
        "topograph_atc_codes": join_values(code for match in topograph_matches for code in match.atc_codes),
        "topograph_atc_l5_names": join_values(name for match in topograph_matches for name in match.atc_l5_names),
        "has_exact_casefold_term_match": ctgov_term_key in topograph_term_keys,
    }


def write_tsv(path: Path, records: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=OUTPUT_COLUMNS,
            delimiter="\t",
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(records)


def build_ctgov_topograph_drug_crosswalk(
    ctgov_annotated_tsv: Path,
    topograph_annotated_tsv: Path,
    output_tsv: Path,
) -> dict[str, int]:
    ctgov_summaries = summarize_annotated_terms(ctgov_annotated_tsv)
    topograph_summaries = summarize_annotated_terms(topograph_annotated_tsv)
    topograph_by_ingredient = build_ingredient_to_terms(topograph_summaries)

    records: list[dict[str, object]] = []
    for ctgov_term in sorted(ctgov_summaries, key=lambda value: value.casefold()):
        ctgov_summary = ctgov_summaries[ctgov_term]
        topograph_matches = sorted(
            topograph_by_ingredient.get(ctgov_summary.rxnorm_ingredient_rxcui, []),
            key=lambda summary: summary.input_drug_name.casefold(),
        )
        records.append(crosswalk_record(ctgov_summary, topograph_matches))

    write_tsv(output_tsv, records)

    matched = sum(1 for row in records if row["equivalence_status"] == "TOPOGRAPH_INGREDIENT_MATCH")
    no_topograph = sum(1 for row in records if row["equivalence_status"] == "NO_TOPOGRAPH_TERM_FOR_INGREDIENT")
    no_ctgov_rxnorm = sum(
        1
        for row in records
        if row["equivalence_status"] in {"NO_CONFIDENT_CTGOV_RXNORM_MATCH", "NO_CTGOV_RXNORM_INGREDIENT"}
    )

    return {
        "ctgov_terms": len(ctgov_summaries),
        "topograph_terms": len(topograph_summaries),
        "ctgov_terms_with_topograph_ingredient_match": matched,
        "ctgov_terms_without_topograph_ingredient_match": no_topograph,
        "ctgov_terms_without_confident_rxnorm_identity": no_ctgov_rxnorm,
        "output_rows": len(records),
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a CTGov-to-Topograph drug crosswalk from ATC-annotated drug TSVs."
    )
    parser.add_argument(
        "--ctgov-annotated-tsv",
        "--ctgov_annotated_tsv",
        dest="ctgov_annotated_tsv",
        type=Path,
        default=CTGOV_DEFAULT_ANNOTATED,
        help=f"CTGov annotated TSV. Default: {CTGOV_DEFAULT_ANNOTATED}",
    )
    parser.add_argument(
        "--topograph-annotated-tsv",
        "--topograph_annotated_tsv",
        dest="topograph_annotated_tsv",
        type=Path,
        default=TOPOGRAPH_DEFAULT_ANNOTATED,
        help=f"Topograph annotated TSV. Default: {TOPOGRAPH_DEFAULT_ANNOTATED}",
    )
    parser.add_argument(
        "--output-tsv",
        "--output_tsv",
        dest="output_tsv",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Crosswalk output TSV. Default: {DEFAULT_OUTPUT}",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    try:
        stats = build_ctgov_topograph_drug_crosswalk(
            ctgov_annotated_tsv=args.ctgov_annotated_tsv,
            topograph_annotated_tsv=args.topograph_annotated_tsv,
            output_tsv=args.output_tsv,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"CTGov annotated TSV: {args.ctgov_annotated_tsv}", file=sys.stderr)
    print(f"Topograph annotated TSV: {args.topograph_annotated_tsv}", file=sys.stderr)
    print(f"Output TSV: {args.output_tsv}", file=sys.stderr)
    for key, value in stats.items():
        print(f"{key}: {value}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
