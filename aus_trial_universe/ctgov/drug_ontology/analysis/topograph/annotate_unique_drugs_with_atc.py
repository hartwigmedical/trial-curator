#!/usr/bin/env python3
"""Annotate a one-column unique-drug TSV with RxNorm and ATC metadata.

This is intentionally source-agnostic. It can be used for either:

    * CTGov unique drug terms, e.g. ctgov_unique_drugs.tsv
    * Topograph unique drug terms, e.g. topograph_unique_drugs.tsv

Resolution path
---------------

    input drug term
        -> RxNorm term match
        -> RxNorm ingredient RXCUI
        -> RXNCONSO.RRF ATC atoms for that ingredient RXCUI
        -> WHO ATC tree enrichment

The RxNorm and ATC logic is delegated to the existing project modules:

    aus_trial_universe.ctgov.drug_ontology.identity.rxnorm.matcher
    aus_trial_universe.ctgov.drug_ontology.sources.atc.ingredient_to_atc

Output grain
------------

One row per input drug term x ATC row.

If a drug does not confidently resolve to RxNorm, or resolves to RxNorm but not
an ingredient, or resolves to an ingredient with no ATC atom, one retained row is
still written with an explanatory atc_match_status.
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from collections import OrderedDict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Sequence

from aus_trial_universe.ctgov.drug_ontology.identity.rxnorm.matcher import (
    IngredientResolution,
    RxnConsoIndex,
    RxnRelIndex,
    TermResolution,
    resolve_term,
)
from aus_trial_universe.ctgov.drug_ontology.sources.atc.ingredient_to_atc import (
    RXNCONSO_FILENAME,
    AtcResolution,
    AtcTree,
    RxnConsoAtcAtom,
    clean_text,
    load_rxnconso_atc_index,
    resolve_atc_atom,
    resolve_ingredient_to_atc,
)
from aus_trial_universe.ctgov.drug_ontology.shared.text import normalize_key as normalise_key
from aus_trial_universe.ctgov.drug_ontology.shared.tsv import (
    detect_delimiter,
    write_tsv as write_rows_to_tsv,
)

logger = logging.getLogger(__name__)

DEFAULT_CTGOV_UNIQUE_DRUGS = Path(
    "data/ctgov/drug_ontology/processed_inputs/analysis/ctgov/version_13022026/ctgov_unique_drugs.tsv"
)
DEFAULT_TOPOGRAPH_UNIQUE_DRUGS = Path(
    "data/ctgov/drug_ontology/processed_inputs/analysis/Topograph/version_13052026/topograph_unique_drugs.tsv"
)
DEFAULT_ATC_TREE_TSV = Path("data/ctgov/drug_ontology/processed_inputs/analysis/ATC/version_25042026/atc_tree.tsv")

DEFAULT_OUTPUT_SUFFIX = ".atc_annotated.tsv"

# WHO ATC 2026 reclassifications observed when moving from the older ATC tree.
# RxNorm ATC atoms can lag WHO ATC; these bridges prevent otherwise-valid drugs
# from becoming ATC_CODE_NOT_IN_TREE solely because the ATC hierarchy moved.
DEFAULT_OBSOLETE_ATC_CODE_BRIDGE: dict[str, str] = {
    "L01EX17": "L01EP01",  # capmatinib
    "L01EX21": "L01EP02",  # tepotinib
    "L04AA58": "L04AL01",  # efgartigimod alfa
    "L04AG16": "L04AL02",  # rozanolixizumab
}

AUTO_DRUG_COLUMN_CANDIDATES = (
    "input_drug_name",  # CTGov unique file
    "drug_name",
    "drug",             # Topograph unique file
    "Drugs",
)

OUTPUT_COLUMNS = [
    "source_name",
    "input_drug_name",
    "rxnorm_match_status",
    "rxnorm_match_stage",
    "rxnorm_matched_term",
    "rxnorm_rxcui",
    "rxnorm_canonical_name",
    "rxnorm_term_type",
    "rxnorm_candidate_count",
    "rxnorm_candidate_summary",
    "manual_review_needed",
    "rxnorm_ingredient_rxcui",
    "rxnorm_ingredient_name",
    "rxnorm_ingredient_term_type",
    "ingredient_resolution_stage",
    "ingredient_path",
    "atc_match_status",
    "atc_original_code",
    "atc_bridge_applied",
    "atc_code",
    "atc_name",
    "atc_level",
    "atc_l1_code",
    "atc_l1_name",
    "atc_l2_code",
    "atc_l2_name",
    "atc_l3_code",
    "atc_l3_name",
    "atc_l4_code",
    "atc_l4_name",
    "atc_l5_code",
    "atc_l5_name",
]


@dataclass(frozen=True)
class DrugTermRxNormResolution:
    input_drug_name: str
    term_resolution: TermResolution
    ingredient_resolution: IngredientResolution


@dataclass(frozen=True)
class AnnotationStats:
    input_terms: int
    unique_terms: int
    rxnorm_matched_terms: int
    rxnorm_unmatched_or_review_terms: int
    matched_ingredient_terms: int
    terms_with_matched_atc: int
    terms_without_atc: int
    output_rows: int
    obsolete_atc_bridges_applied: int


def resolve_drug_column(fieldnames: Sequence[str], requested_column: str | None) -> str:
    cleaned_fieldnames = [name.strip() for name in fieldnames]

    if requested_column:
        if requested_column not in cleaned_fieldnames:
            raise ValueError(
                f"Requested drug column {requested_column!r} not found. "
                f"Available columns: {cleaned_fieldnames}"
            )
        return requested_column

    for candidate in AUTO_DRUG_COLUMN_CANDIDATES:
        if candidate in cleaned_fieldnames:
            return candidate

    if len(cleaned_fieldnames) == 1:
        return cleaned_fieldnames[0]

    raise ValueError(
        "Could not infer the drug column. Pass --drug-column explicitly. "
        f"Available columns: {cleaned_fieldnames}"
    )


def read_unique_drug_terms(input_tsv: Path, drug_column: str | None = None) -> list[str]:
    """Read and de-duplicate drug terms while preserving first-seen spelling/order."""
    if not input_tsv.exists():
        raise FileNotFoundError(f"Input drug list not found: {input_tsv}")
    if not input_tsv.is_file():
        raise IsADirectoryError(f"Input drug list is not a file: {input_tsv}")

    delimiter = detect_delimiter(input_tsv)
    out: OrderedDict[str, str] = OrderedDict()

    with input_tsv.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        if reader.fieldnames is None:
            raise ValueError(f"Input drug list has no header row: {input_tsv}")

        reader.fieldnames = [field.strip() for field in reader.fieldnames]
        resolved_column = resolve_drug_column(reader.fieldnames, drug_column)

        for row in reader:
            term = clean_text(row.get(resolved_column, ""))
            if not term:
                continue
            key = normalise_key(term)
            if key and key not in out:
                out[key] = term

    return list(out.values())


def load_rxnorm_indexes(rxnorm_rrf_dir: Path) -> tuple[RxnConsoIndex, RxnRelIndex]:
    logger.info("Loading RxNorm RXNCONSO/RXNREL from %s", rxnorm_rrf_dir)
    conso_index = RxnConsoIndex.from_rrf_dir(rxnorm_rrf_dir)
    rel_index = RxnRelIndex.from_rrf_dir(rxnorm_rrf_dir)
    logger.info("Loaded RxNorm indexes")
    return conso_index, rel_index


def resolve_terms_to_rxnorm(
    terms: Sequence[str],
    conso_index: RxnConsoIndex,
    rel_index: RxnRelIndex,
) -> list[DrugTermRxNormResolution]:
    out: list[DrugTermRxNormResolution] = []

    for index, term in enumerate(terms, start=1):
        if index % 1000 == 0:
            logger.info("Resolved %d/%d drug terms to RxNorm", index, len(terms))

        term_resolution = resolve_term(term, conso_index)
        ingredient_resolution = rel_index.resolve_ingredient(
            matched_rxcui=term_resolution.rxcui,
            conso_index=conso_index,
            source_term=term,
        )
        out.append(
            DrugTermRxNormResolution(
                input_drug_name=term,
                term_resolution=term_resolution,
                ingredient_resolution=ingredient_resolution,
            )
        )

    return out


def blank_atc_resolution(status: str) -> AtcResolution:
    return AtcResolution(
        atc_match_status=status,
        atc_code="",
        atc_name="",
        atc_level=None,
        atc_l1_code="",
        atc_l1_name="",
        atc_l2_code="",
        atc_l2_name="",
        atc_l3_code="",
        atc_l3_name="",
        atc_l4_code="",
        atc_l4_name="",
        atc_l5_code="",
        atc_l5_name="",
    )


def bridged_atom(
    atom: RxnConsoAtcAtom,
    obsolete_atc_code_bridge: Mapping[str, str],
) -> tuple[RxnConsoAtcAtom, bool]:
    replacement_code = obsolete_atc_code_bridge.get(atom.code)
    if not replacement_code:
        return atom, False

    return (
        RxnConsoAtcAtom(
            rxcui=atom.rxcui,
            code=replacement_code,
            name=atom.name,
            tty=atom.tty,
        ),
        True,
    )


def resolve_ingredient_to_atc_with_audit(
    rxnorm_ingredient_rxcui: str,
    atc_by_rxcui: Mapping[str, Sequence[RxnConsoAtcAtom]],
    tree: AtcTree,
    obsolete_atc_code_bridge: Mapping[str, str],
) -> list[tuple[AtcResolution, str, bool]]:
    """Resolve ATC rows and retain original RxNorm ATC code + bridge flag."""
    rxcui = clean_text(rxnorm_ingredient_rxcui)

    if not obsolete_atc_code_bridge:
        return [(resolution, resolution.atc_code, False) for resolution in resolve_ingredient_to_atc(rxcui, atc_by_rxcui, tree)]

    atoms = list(atc_by_rxcui.get(rxcui, []))
    if not atoms:
        return [(blank_atc_resolution("NO_ATC_MATCH"), "", False)]

    out: list[tuple[AtcResolution, str, bool]] = []
    seen_output_codes: set[tuple[str, str]] = set()

    for atom in sorted(atoms, key=lambda a: (a.code, a.name, a.tty)):
        patched_atom, bridge_applied = bridged_atom(atom, obsolete_atc_code_bridge)
        key = (patched_atom.code, "BRIDGED" if bridge_applied else "DIRECT")
        if key in seen_output_codes:
            continue
        seen_output_codes.add(key)
        out.append((resolve_atc_atom(patched_atom, tree), atom.code, bridge_applied))

    return out


def atc_resolutions_for_drug(
    drug_resolution: DrugTermRxNormResolution,
    atc_by_rxcui: Mapping[str, Sequence[RxnConsoAtcAtom]],
    tree: AtcTree,
    obsolete_atc_code_bridge: Mapping[str, str],
) -> list[tuple[AtcResolution, str, bool]]:
    term_resolution = drug_resolution.term_resolution
    ingredient_resolution = drug_resolution.ingredient_resolution

    # Match the current CTGov ATC logic: only confident RxNorm MATCHED rows are
    # allowed to drive ATC annotation. MULTIPLE_CANDIDATES_REVIEW is retained but
    # not used as a confident identity/ATC mapping.
    if term_resolution.match_status != "MATCHED":
        return [(blank_atc_resolution("NO_CONFIDENT_RXNORM_MATCH"), "", False)]

    if not clean_text(ingredient_resolution.ingredient_rxcui):
        return [(blank_atc_resolution("NO_RXNORM_INGREDIENT"), "", False)]

    return resolve_ingredient_to_atc_with_audit(
        rxnorm_ingredient_rxcui=ingredient_resolution.ingredient_rxcui,
        atc_by_rxcui=atc_by_rxcui,
        tree=tree,
        obsolete_atc_code_bridge=obsolete_atc_code_bridge,
    )


def output_record(
    source_name: str,
    drug_resolution: DrugTermRxNormResolution,
    atc_resolution: AtcResolution,
    *,
    atc_original_code: str,
    atc_bridge_applied: bool,
) -> dict[str, object]:
    term_resolution = drug_resolution.term_resolution
    ingredient_resolution = drug_resolution.ingredient_resolution

    record = {
        "source_name": source_name,
        "input_drug_name": drug_resolution.input_drug_name,
        "rxnorm_match_status": term_resolution.match_status,
        "rxnorm_match_stage": term_resolution.match_stage,
        "rxnorm_matched_term": term_resolution.matched_term,
        "rxnorm_rxcui": term_resolution.rxcui,
        "rxnorm_canonical_name": term_resolution.canonical_name,
        "rxnorm_term_type": term_resolution.canonical_tty,
        "rxnorm_candidate_count": term_resolution.candidate_count,
        "rxnorm_candidate_summary": term_resolution.candidate_summary,
        "manual_review_needed": term_resolution.manual_review_needed,
        "rxnorm_ingredient_rxcui": ingredient_resolution.ingredient_rxcui,
        "rxnorm_ingredient_name": ingredient_resolution.ingredient_name,
        "rxnorm_ingredient_term_type": ingredient_resolution.ingredient_tty,
        "ingredient_resolution_stage": ingredient_resolution.ingredient_resolution_stage,
        "ingredient_path": ingredient_resolution.ingredient_path,
        "atc_original_code": atc_original_code,
        "atc_bridge_applied": atc_bridge_applied,
    }
    record.update(asdict(atc_resolution))
    return record


def write_tsv(path: Path, records: Sequence[Mapping[str, object]]) -> None:
    write_rows_to_tsv(path, records, OUTPUT_COLUMNS)


def annotate_unique_drug_list_with_atc(
    input_tsv: Path,
    output_tsv: Path,
    rxnorm_rrf_dir: Path,
    atc_tree_tsv: Path,
    *,
    source_name: str,
    drug_column: str | None = None,
    obsolete_atc_code_bridge: Mapping[str, str] | None = None,
) -> AnnotationStats:
    terms = read_unique_drug_terms(input_tsv, drug_column=drug_column)
    obsolete_atc_code_bridge = dict(obsolete_atc_code_bridge or {})

    conso_index, rel_index = load_rxnorm_indexes(rxnorm_rrf_dir)
    drug_resolutions = resolve_terms_to_rxnorm(terms, conso_index, rel_index)

    target_ingredient_rxcuis = {
        resolution.ingredient_resolution.ingredient_rxcui
        for resolution in drug_resolutions
        if resolution.term_resolution.match_status == "MATCHED"
        and clean_text(resolution.ingredient_resolution.ingredient_rxcui)
    }

    tree = AtcTree.from_tsv(atc_tree_tsv)
    atc_by_rxcui = load_rxnconso_atc_index(
        rxnconso_path=rxnorm_rrf_dir / RXNCONSO_FILENAME,
        target_rxcuis=target_ingredient_rxcuis,
    )

    records: list[dict[str, object]] = []
    terms_with_matched_atc: set[str] = set()
    terms_without_atc: set[str] = set()
    bridges_applied = 0

    for drug_resolution in drug_resolutions:
        atc_rows = atc_resolutions_for_drug(
            drug_resolution,
            atc_by_rxcui=atc_by_rxcui,
            tree=tree,
            obsolete_atc_code_bridge=obsolete_atc_code_bridge,
        )

        has_matched_atc = False
        for atc_resolution, atc_original_code, atc_bridge_applied in atc_rows:
            if atc_resolution.atc_match_status == "MATCHED_ATC":
                has_matched_atc = True
            if atc_bridge_applied:
                bridges_applied += 1

            records.append(
                output_record(
                    source_name,
                    drug_resolution,
                    atc_resolution,
                    atc_original_code=atc_original_code,
                    atc_bridge_applied=atc_bridge_applied,
                )
            )

        if has_matched_atc:
            terms_with_matched_atc.add(drug_resolution.input_drug_name)
        else:
            terms_without_atc.add(drug_resolution.input_drug_name)

    write_tsv(output_tsv, records)

    rxnorm_matched_terms = sum(
        1 for resolution in drug_resolutions if resolution.term_resolution.match_status == "MATCHED"
    )
    matched_ingredient_terms = sum(
        1
        for resolution in drug_resolutions
        if resolution.term_resolution.match_status == "MATCHED"
        and clean_text(resolution.ingredient_resolution.ingredient_rxcui)
    )

    return AnnotationStats(
        input_terms=len(terms),
        unique_terms=len(terms),
        rxnorm_matched_terms=rxnorm_matched_terms,
        rxnorm_unmatched_or_review_terms=len(terms) - rxnorm_matched_terms,
        matched_ingredient_terms=matched_ingredient_terms,
        terms_with_matched_atc=len(terms_with_matched_atc),
        terms_without_atc=len(terms_without_atc),
        output_rows=len(records),
        obsolete_atc_bridges_applied=bridges_applied,
    )


def default_output_path(input_tsv: Path) -> Path:
    return input_tsv.with_name(input_tsv.stem + DEFAULT_OUTPUT_SUFFIX)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Annotate a CTGov or Topograph unique-drug TSV with RxNorm and ATC metadata."
    )
    parser.add_argument(
        "--input-tsv",
        "--input_tsv",
        dest="input_tsv",
        required=True,
        type=Path,
        help="One-column unique-drug TSV, e.g. ctgov_unique_drugs.tsv or topograph_unique_drugs.tsv.",
    )
    parser.add_argument(
        "--output-tsv",
        "--output_tsv",
        dest="output_tsv",
        default=None,
        type=Path,
        help="Annotated output TSV. Defaults to <input stem>.atc_annotated.tsv.",
    )
    parser.add_argument(
        "--drug-column",
        "--drug_column",
        dest="drug_column",
        default=None,
        help="Drug column name. If omitted, auto-detects input_drug_name, drug_name, drug, or the sole column.",
    )
    parser.add_argument(
        "--source-name",
        "--source_name",
        dest="source_name",
        required=True,
        choices=["ctgov", "topograph"],
        help="Source label to write into the output.",
    )
    parser.add_argument(
        "--rxnorm-rrf-dir",
        "--rxnorm_rrf_dir",
        dest="rxnorm_rrf_dir",
        required=True,
        type=Path,
        help="Directory containing RXNCONSO.RRF and RXNREL.RRF.",
    )
    parser.add_argument(
        "--atc-tree-tsv",
        "--atc_tree_tsv",
        dest="atc_tree_tsv",
        type=Path,
        default=DEFAULT_ATC_TREE_TSV,
        help=f"Legacy-format ATC tree TSV. Default: {DEFAULT_ATC_TREE_TSV}",
    )
    parser.add_argument(
        "--no-2026-obsolete-atc-bridge",
        "--no_2026_obsolete_atc_bridge",
        dest="no_2026_obsolete_atc_bridge",
        action="store_true",
        help="Disable bridges for old ATC codes reclassified in the 2026 tree.",
    )
    parser.add_argument(
        "--log-level",
        "--log_level",
        dest="log_level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    output_tsv = args.output_tsv if args.output_tsv is not None else default_output_path(args.input_tsv)
    obsolete_bridge = {} if args.no_2026_obsolete_atc_bridge else DEFAULT_OBSOLETE_ATC_CODE_BRIDGE

    try:
        stats = annotate_unique_drug_list_with_atc(
            input_tsv=args.input_tsv,
            output_tsv=output_tsv,
            rxnorm_rrf_dir=args.rxnorm_rrf_dir,
            atc_tree_tsv=args.atc_tree_tsv,
            source_name=args.source_name,
            drug_column=args.drug_column,
            obsolete_atc_code_bridge=obsolete_bridge,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Input TSV: {args.input_tsv}", file=sys.stderr)
    print(f"Output TSV: {output_tsv}", file=sys.stderr)
    print(f"Input terms: {stats.input_terms}", file=sys.stderr)
    print(f"Unique terms: {stats.unique_terms}", file=sys.stderr)
    print(f"RxNorm MATCHED terms: {stats.rxnorm_matched_terms}", file=sys.stderr)
    print(f"RxNorm unmatched/review terms: {stats.rxnorm_unmatched_or_review_terms}", file=sys.stderr)
    print(f"Matched ingredient terms: {stats.matched_ingredient_terms}", file=sys.stderr)
    print(f"Terms with matched ATC: {stats.terms_with_matched_atc}", file=sys.stderr)
    print(f"Terms without matched ATC: {stats.terms_without_atc}", file=sys.stderr)
    print(f"Output rows: {stats.output_rows}", file=sys.stderr)
    print(f"Obsolete ATC bridges applied: {stats.obsolete_atc_bridges_applied}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
