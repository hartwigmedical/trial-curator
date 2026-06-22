#!/usr/bin/env python3
from __future__ import annotations

"""Build a raw-to-final CTGov -> POTTR RxNorm/ATC crosswalk.

Output grain
------------
One row per unique CTGov drug term extracted directly from raw ctgov_input.json.

Inputs
------
* raw CTGov JSON/NDJSON
* raw POTTR drug_database.txt and drug_class_hierarchy.txt
* RxNorm RRF directory
* ATC tree TSV

No persistent intermediate TSV is read as an input. The module reuses the
existing CTGov extraction, POTTR RxNorm matching, and RxNorm->ATC functions
directly in memory.

Identity/link rule
------------------
CTGov and POTTR are linked by RxNorm IDs. The primary intended link is the
RxNorm ingredient RXCUI, but the link index deliberately includes both
rxnorm_ingredient_rxcui(s) and rxnorm_rxcui(s), because some POTTR compact
annotations expose the usable link ID in rxnorm_rxcuis.

ATC is carried through as annotation, not as the identity key.
"""

import argparse
import csv
import logging
import sys
import tempfile
from collections import Counter, OrderedDict, defaultdict
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from aus_trial_universe.drug_utility_path.ctgov.drug_ontology.sources.atc.ingredient_to_atc import (
    RXNCONSO_FILENAME,
    AtcTree,
    load_rxnconso_atc_index,
)
from aus_trial_universe.drug_utility_path.ctgov.drug_ontology.sources.pottr.drug_classes import (
    build_pottr_records,
)
from aus_trial_universe.drug_utility_path.ctgov.drug_ontology.ctgov.interventions import (
    build_interventions_dataframe,
    clean_text as ctgov_extract_clean_text,
)
from aus_trial_universe.drug_utility_path.ctgov.drug_ontology.ctgov.unique_terms import (
    UNIQUE_DRUG_COLUMN,
    build_intervention_drug_link_dataframe,
    build_unique_drug_dataframe,
    filter_target_drug_interventions,
)
from aus_trial_universe.drug_utility_path.ctgov.drug_ontology.analysis.pottr.mapping_helpers import (
    DEFAULT_OBSOLETE_ATC_CODE_BRIDGE,
    alias_atc_records,
    group_concepts_by_canonical,
    hierarchy_paths_for_direct_classes,
    load_hierarchy_paths_by_class,
    load_pottr_concepts,
    load_rxnorm_indexes,
)
from aus_trial_universe.drug_utility_path.ctgov.drug_ontology.shared.text import (
    clean_text,
    normalize_key,
    ordered_join,
    ordered_unique,
    split_display_values,
)
from aus_trial_universe.drug_utility_path.ctgov.drug_ontology.shared.tsv import write_tsv as write_rows_to_tsv

logger = logging.getLogger(__name__)

DEFAULT_CTGOV_INPUT_JSON = Path("data/ctgov/trials/version_13022026/ctgov_input.json")
DEFAULT_POTTR_RAW_DIR = Path("data/ctgov/drug_ontology/raw_inputs/POTTR/version_29052026")
DEFAULT_RXNORM_RRF_DIR = Path("data/ctgov/drug_ontology/raw_inputs/RxNorm/version_03022026")
DEFAULT_ATC_TREE_TSV = Path("data/ctgov/drug_ontology/processed_inputs/analysis/ATC/version_25042026/atc_tree.tsv")
DEFAULT_OUTPUT_TSV = Path("data/ctgov/drug_ontology/analysis_outputs/pottr/version_29052026/ctgov_vs_pottr_drugs.tsv")
DEFAULT_SUMMARY_TSV = Path("data/ctgov/drug_ontology/analysis_outputs/pottr/version_29052026/ctgov_vs_pottr_drugs_summary.tsv")

# Temporary POTTR-shaped files used only inside tempfile.TemporaryDirectory().
# This lets us reuse the existing POTTR RxNorm matcher for CTGov terms without
# reading/writing persistent intermediate TSVs.
POTTR_DRUG_DATABASE_FILENAME = "drug_database.txt"
POTTR_DRUG_CLASS_HIERARCHY_FILENAME = "drug_class_hierarchy.txt"
CTGOV_PSEUDO_CLASS = "ctgov_input_drug"

RXNORM_MATCH_STATUS_COLUMNS = ("rxnorm_match_status", "rxnorm_match_statuses", "match_status")
RXNORM_MATCH_STAGE_COLUMNS = ("rxnorm_match_stage", "rxnorm_match_stages", "match_stage")
RXNORM_MATCHED_TERM_COLUMNS = ("rxnorm_matched_term", "rxnorm_matched_terms", "matched_term")
RXNORM_RXCUI_COLUMNS = ("rxnorm_rxcui", "rxnorm_rxcuis", "rxcui", "rxcuis")
RXNORM_CANONICAL_NAME_COLUMNS = (
    "rxnorm_canonical_name",
    "rxnorm_canonical_names",
    "canonical_name",
)
RXNORM_TERM_TYPE_COLUMNS = ("rxnorm_term_type", "rxnorm_term_types")
RXNORM_INGREDIENT_RXCUI_COLUMNS = (
    "rxnorm_ingredient_rxcui",
    "rxnorm_ingredient_rxcuis",
    "ingredient_rxcui",
    "ingredient_rxcuis",
)
RXNORM_INGREDIENT_NAME_COLUMNS = (
    "rxnorm_ingredient_name",
    "rxnorm_ingredient_names",
    "ingredient_name",
    "ingredient_names",
)
RXNORM_INGREDIENT_TERM_TYPE_COLUMNS = (
    "rxnorm_ingredient_term_type",
    "rxnorm_ingredient_term_types",
)
INGREDIENT_RESOLUTION_STAGE_COLUMNS = (
    "rxnorm_ingredient_resolution_stage",
    "rxnorm_ingredient_resolution_stages",
    "ingredient_resolution_stage",
    "ingredient_resolution_stages",
)
MANUAL_REVIEW_COLUMNS = ("manual_review_needed",)

# Important: this is the actual CTGov <-> POTTR join key set.
# Ingredient RXCUIs are preferred by design, but direct RXCUIs are included
# because POTTR annotation exports may expose the link ID as rxnorm_rxcuis.
RXNORM_LINK_RXCUI_COLUMNS = (
    *RXNORM_INGREDIENT_RXCUI_COLUMNS,
    *RXNORM_RXCUI_COLUMNS,
    "pottr_link_anchor_rxcui",
    "pottr_link_anchor_rxcuis",
)

ATC_MATCH_STATUS_COLUMNS = ("atc_match_status", "atc_match_statuses")
ATC_CODE_COLUMNS = ("atc_code", "atc_codes", "ATC code")
ATC_NAME_COLUMNS = ("atc_name", "atc_names", "ATC level name")
ATC_L1_CODE_COLUMNS = ("atc_l1_code", "atc_l1_codes")
ATC_L1_NAME_COLUMNS = ("atc_l1_name", "atc_l1_names")
ATC_L2_CODE_COLUMNS = ("atc_l2_code", "atc_l2_codes")
ATC_L2_NAME_COLUMNS = ("atc_l2_name", "atc_l2_names")
ATC_L3_CODE_COLUMNS = ("atc_l3_code", "atc_l3_codes")
ATC_L3_NAME_COLUMNS = ("atc_l3_name", "atc_l3_names")
ATC_L4_CODE_COLUMNS = ("atc_l4_code", "atc_l4_codes")
ATC_L4_NAME_COLUMNS = ("atc_l4_name", "atc_l4_names")
ATC_L5_CODE_COLUMNS = ("atc_l5_code", "atc_l5_codes")
ATC_L5_NAME_COLUMNS = ("atc_l5_name", "atc_l5_names")
ATC_BRIDGE_APPLIED_COLUMNS = ("atc_bridge_applied",)
ATC_ORIGINAL_CODE_COLUMNS = ("atc_original_code", "atc_original_codes")

POTTR_CANONICAL_COLUMNS = ("pottr_canonical_drug_name", "pottr_canonical_drug_names", "canonical_drug_name")
POTTR_ALIAS_COLUMNS = ("pottr_drug_aliases", "drug_aliases", "aliases")
POTTR_DIRECT_CLASS_COLUMNS = ("pottr_direct_class_names", "direct_class_name", "direct_class_names")
POTTR_HIERARCHY_COLUMNS = ("pottr_class_hierarchy_paths", "class_path", "class_paths")
POTTR_LOOKUP_TERM_COLUMNS = ("rxnorm_lookup_term", "rxnorm_lookup_terms")
POTTR_LOOKUP_SOURCE_COLUMNS = ("rxnorm_lookup_term_source", "rxnorm_lookup_term_sources")
POTTR_LOOKUP_TERMS_TRIED_COLUMNS = ("rxnorm_lookup_terms_tried",)
POTTR_LINK_ANCHOR_RXCUI_COLUMNS = ("pottr_link_anchor_rxcui", "pottr_link_anchor_rxcuis")
POTTR_LINK_ANCHOR_NAME_COLUMNS = ("pottr_link_anchor_name", "pottr_link_anchor_names")
POTTR_LINK_ANCHOR_STRATEGY_COLUMNS = ("pottr_link_anchor_strategy", "pottr_link_anchor_strategies")

OUTPUT_COLUMNS = [
    "ctgov_input_drug_name",
    "pottr_presence_status",
    "is_missing_from_pottr_by_rxnorm_id",
    "ctgov_rxnorm_link_rxcuis",
    "ctgov_rxnorm_link_rxcuis_present_in_pottr",
    "ctgov_rxnorm_link_rxcuis_absent_from_pottr",
    "pottr_rxnorm_link_rxcuis",
    "pottr_match_count",

    # CTGov RxNorm/ATC annotation.
    "ctgov_rxnorm_match_statuses",
    "ctgov_rxnorm_match_stages",
    "ctgov_rxnorm_matched_terms",
    "ctgov_rxnorm_rxcuis",
    "ctgov_rxnorm_canonical_names",
    "ctgov_rxnorm_term_types",
    "ctgov_rxnorm_ingredient_rxcuis",
    "ctgov_rxnorm_ingredient_names",
    "ctgov_rxnorm_ingredient_term_types",
    "ctgov_ingredient_resolution_stages",
    "ctgov_manual_review_needed",
    "ctgov_atc_match_statuses",
    "ctgov_atc_codes",
    "ctgov_atc_names",
    "ctgov_atc_l1_codes",
    "ctgov_atc_l1_names",
    "ctgov_atc_l2_codes",
    "ctgov_atc_l2_names",
    "ctgov_atc_l3_codes",
    "ctgov_atc_l3_names",
    "ctgov_atc_l4_codes",
    "ctgov_atc_l4_names",
    "ctgov_atc_l5_codes",
    "ctgov_atc_l5_names",
    "ctgov_atc_bridge_applied",
    "ctgov_atc_original_codes",

    # Required POTTR columns brought across by RxNorm ID.
    "pottr_canonical_drug_name",
    "pottr_drug_aliases",
    "pottr_direct_class_names",
    "pottr_class_hierarchy_paths",

    # Additional POTTR RxNorm/ATC audit columns.
    "pottr_rxnorm_lookup_terms",
    "pottr_rxnorm_lookup_term_sources",
    "pottr_rxnorm_lookup_terms_tried",
    "pottr_rxnorm_match_statuses",
    "pottr_rxnorm_match_stages",
    "pottr_rxnorm_rxcuis",
    "pottr_rxnorm_canonical_names",
    "pottr_rxnorm_term_types",
    "pottr_rxnorm_ingredient_rxcuis",
    "pottr_rxnorm_ingredient_names",
    "pottr_rxnorm_ingredient_term_types",
    "pottr_ingredient_resolution_stages",
    "pottr_link_anchor_rxcuis",
    "pottr_link_anchor_names",
    "pottr_link_anchor_strategies",
    "pottr_manual_review_needed",
    "pottr_atc_match_statuses",
    "pottr_atc_codes",
    "pottr_atc_names",
    "pottr_atc_l1_codes",
    "pottr_atc_l1_names",
    "pottr_atc_l2_codes",
    "pottr_atc_l2_names",
    "pottr_atc_l3_codes",
    "pottr_atc_l3_names",
    "pottr_atc_l4_codes",
    "pottr_atc_l4_names",
    "pottr_atc_l5_codes",
    "pottr_atc_l5_names",
    "pottr_atc_bridge_applied",
    "pottr_atc_original_codes",
    "pottr_exact_casefold_term_match",
    "pottr_exact_casefold_canonical_drug_name",
]

SUMMARY_COLUMNS = ["metric", "value"]
TRUE_VALUES = {"true", "t", "1", "yes", "y"}
FALSE_VALUES = {"false", "f", "0", "no", "n"}


def ordered_unique_split(values: Iterable[object]) -> list[str]:
    return ordered_unique(
        part
        for value in values
        for part in split_display_values(value, delimiter="|")
    )


def bool_any(values: Iterable[object]) -> str:
    cleaned_values = [clean_text(value).casefold() for value in values if clean_text(value)]
    if any(value in TRUE_VALUES for value in cleaned_values):
        return "true"
    if any(value in FALSE_VALUES for value in cleaned_values):
        return "false"
    return ""


def write_tsv(path: Path, rows: Sequence[Mapping[str, object]], fieldnames: Sequence[str]) -> None:
    write_rows_to_tsv(
        path,
        [{field: row.get(field, "") for field in fieldnames} for row in rows],
        fieldnames,
    )


def collect_from_rows(rows: Sequence[Mapping[str, object]], columns: Sequence[str]) -> list[str]:
    return ordered_unique_split(row.get(column, "") for row in rows for column in columns)


def collect_bool_from_rows(rows: Sequence[Mapping[str, object]], columns: Sequence[str]) -> str:
    return bool_any(row.get(column, "") for row in rows for column in columns)


def has_exact_value(values: Iterable[object], target: str) -> bool:
    target_norm = target.casefold()
    return any(clean_text(value).casefold() == target_norm for value in values)


def row_has_confident_rxnorm_match(row: Mapping[str, object]) -> bool:
    return has_exact_value(
        (
            part
            for column in RXNORM_MATCH_STATUS_COLUMNS
            for part in split_display_values(row.get(column, ""), delimiter="|")
        ),
        "MATCHED",
    )


def rxnorm_link_rxcuis_from_row(row: Mapping[str, object]) -> list[str]:
    """Return RxNorm IDs usable for CTGov <-> POTTR linkage."""
    return collect_from_rows([row], RXNORM_LINK_RXCUI_COLUMNS)


def summarize_annotation_rows(rows: Sequence[Mapping[str, object]]) -> dict[str, str]:
    return {
        "rxnorm_match_statuses": ordered_join(collect_from_rows(rows, RXNORM_MATCH_STATUS_COLUMNS)),
        "rxnorm_match_stages": ordered_join(collect_from_rows(rows, RXNORM_MATCH_STAGE_COLUMNS)),
        "rxnorm_matched_terms": ordered_join(collect_from_rows(rows, RXNORM_MATCHED_TERM_COLUMNS)),
        "rxnorm_rxcuis": ordered_join(collect_from_rows(rows, RXNORM_RXCUI_COLUMNS)),
        "rxnorm_canonical_names": ordered_join(collect_from_rows(rows, RXNORM_CANONICAL_NAME_COLUMNS)),
        "rxnorm_term_types": ordered_join(collect_from_rows(rows, RXNORM_TERM_TYPE_COLUMNS)),
        "rxnorm_ingredient_rxcuis": ordered_join(collect_from_rows(rows, RXNORM_INGREDIENT_RXCUI_COLUMNS)),
        "rxnorm_ingredient_names": ordered_join(collect_from_rows(rows, RXNORM_INGREDIENT_NAME_COLUMNS)),
        "rxnorm_ingredient_term_types": ordered_join(collect_from_rows(rows, RXNORM_INGREDIENT_TERM_TYPE_COLUMNS)),
        "ingredient_resolution_stages": ordered_join(collect_from_rows(rows, INGREDIENT_RESOLUTION_STAGE_COLUMNS)),
        "manual_review_needed": collect_bool_from_rows(rows, MANUAL_REVIEW_COLUMNS),
        "atc_match_statuses": ordered_join(collect_from_rows(rows, ATC_MATCH_STATUS_COLUMNS)),
        "atc_codes": ordered_join(collect_from_rows(rows, ATC_CODE_COLUMNS)),
        "atc_names": ordered_join(collect_from_rows(rows, ATC_NAME_COLUMNS)),
        "atc_l1_codes": ordered_join(collect_from_rows(rows, ATC_L1_CODE_COLUMNS)),
        "atc_l1_names": ordered_join(collect_from_rows(rows, ATC_L1_NAME_COLUMNS)),
        "atc_l2_codes": ordered_join(collect_from_rows(rows, ATC_L2_CODE_COLUMNS)),
        "atc_l2_names": ordered_join(collect_from_rows(rows, ATC_L2_NAME_COLUMNS)),
        "atc_l3_codes": ordered_join(collect_from_rows(rows, ATC_L3_CODE_COLUMNS)),
        "atc_l3_names": ordered_join(collect_from_rows(rows, ATC_L3_NAME_COLUMNS)),
        "atc_l4_codes": ordered_join(collect_from_rows(rows, ATC_L4_CODE_COLUMNS)),
        "atc_l4_names": ordered_join(collect_from_rows(rows, ATC_L4_NAME_COLUMNS)),
        "atc_l5_codes": ordered_join(collect_from_rows(rows, ATC_L5_CODE_COLUMNS)),
        "atc_l5_names": ordered_join(collect_from_rows(rows, ATC_L5_NAME_COLUMNS)),
        "atc_bridge_applied": collect_bool_from_rows(rows, ATC_BRIDGE_APPLIED_COLUMNS),
        "atc_original_codes": ordered_join(collect_from_rows(rows, ATC_ORIGINAL_CODE_COLUMNS)),
    }


def validate_inputs(
    *,
    ctgov_input_json: Path,
    pottr_raw_dir: Path,
    rxnorm_rrf_dir: Path,
    atc_tree_tsv: Path,
) -> None:
    required_files = [
        ctgov_input_json,
        pottr_raw_dir / POTTR_DRUG_DATABASE_FILENAME,
        pottr_raw_dir / POTTR_DRUG_CLASS_HIERARCHY_FILENAME,
        rxnorm_rrf_dir / "RXNCONSO.RRF",
        rxnorm_rrf_dir / "RXNREL.RRF",
        atc_tree_tsv,
    ]
    missing = [path for path in required_files if not path.exists()]

    if missing:
        missing_text = "\n".join(f"  - {path}" for path in missing)
        raise FileNotFoundError(f"Required input files are missing:\n{missing_text}")


def build_ctgov_unique_terms_from_raw(
    *,
    ctgov_input_json: Path,
    sort_terms: bool = False,
) -> list[str]:
    """Extract the CTGov unique drug row universe directly from raw CTGov input."""
    logger.info("Extracting CTGov interventions from %s", ctgov_input_json)

    all_interventions = build_interventions_dataframe(ctgov_input_json)
    filtered_interventions = filter_target_drug_interventions(all_interventions)
    intervention_drug_links = build_intervention_drug_link_dataframe(filtered_interventions)
    unique_drugs = build_unique_drug_dataframe(
        intervention_drug_links,
        sort_unique_drugs=sort_terms,
    )

    terms: list[str] = []
    seen: set[str] = set()

    for value in unique_drugs[UNIQUE_DRUG_COLUMN].tolist():
        term = ctgov_extract_clean_text(value)
        key = normalize_key(term)
        if term and key not in seen:
            seen.add(key)
            terms.append(term)

    logger.info("Extracted %d unique CTGov drug terms", len(terms))
    return terms


def write_ctgov_terms_as_temporary_pottr_raw(raw_dir: Path, ctgov_terms: Sequence[str]) -> None:
    """Create a temporary POTTR-shaped raw directory for CTGov terms."""
    raw_dir.mkdir(parents=True, exist_ok=True)

    drug_database_path = raw_dir / POTTR_DRUG_DATABASE_FILENAME
    with drug_database_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["drug", "drug_class"],
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        for term in ctgov_terms:
            cleaned = clean_text(term)
            if cleaned:
                writer.writerow({"drug": cleaned, "drug_class": CTGOV_PSEUDO_CLASS})

    hierarchy_path = raw_dir / POTTR_DRUG_CLASS_HIERARCHY_FILENAME
    hierarchy_path.write_text(f"{CTGOV_PSEUDO_CLASS}\n", encoding="utf-8")


def build_ctgov_pseudo_pottr_records(
    *,
    ctgov_terms: Sequence[str],
    conso_index: object,
    rel_index: object,
) -> object:
    """Build CTGov RxNorm records using existing POTTR matcher logic.

    This uses a temporary raw directory only while build_pottr_records() runs.
    No temporary TSV is used as a pipeline input or retained on disk.
    """
    with tempfile.TemporaryDirectory(prefix="ctgov_terms_as_pottr_") as tmp:
        raw_dir = Path(tmp)
        write_ctgov_terms_as_temporary_pottr_raw(raw_dir, ctgov_terms)
        return build_pottr_records(raw_dir, conso_index, rel_index)


def matched_link_rxcuis_from_pottr_build_result(pottr_build_result: object) -> set[str]:
    """Collect all matched RxNorm IDs from a PottrBuildResult-like object."""
    out: set[str] = set()

    for alias in pottr_build_result.aliases:
        if clean_text(alias.rxnorm_match_status) != "MATCHED":
            continue
        for value in (
            getattr(alias, "rxnorm_ingredient_rxcui", ""),
            getattr(alias, "rxnorm_rxcui", ""),
            getattr(alias, "pottr_link_anchor_rxcui", ""),
        ):
            cleaned = clean_text(value)
            if cleaned:
                out.add(cleaned)

    return out


def summarize_ctgov_alias_atc_rows(rows: Sequence[Mapping[str, object]]) -> dict[str, dict[str, str]]:
    grouped: OrderedDict[str, list[Mapping[str, object]]] = OrderedDict()
    first_seen_term: dict[str, str] = {}

    for row in rows:
        term = clean_text(row.get("alias", ""))
        key = normalize_key(term)
        if not key:
            continue
        grouped.setdefault(key, []).append(row)
        first_seen_term.setdefault(key, term)

    out: dict[str, dict[str, str]] = {}
    for key, group_rows in grouped.items():
        summary = summarize_annotation_rows(group_rows)
        summary["input_drug_name"] = first_seen_term[key]
        summary["has_annotation_row"] = "true"
        out[key] = summary

    return out


def blank_ctgov_summary(term: str) -> dict[str, str]:
    return {
        "input_drug_name": term,
        "has_annotation_row": "false",
        "rxnorm_match_statuses": "",
        "rxnorm_match_stages": "",
        "rxnorm_matched_terms": "",
        "rxnorm_rxcuis": "",
        "rxnorm_canonical_names": "",
        "rxnorm_term_types": "",
        "rxnorm_ingredient_rxcuis": "",
        "rxnorm_ingredient_names": "",
        "rxnorm_ingredient_term_types": "",
        "ingredient_resolution_stages": "",
        "manual_review_needed": "",
        "atc_match_statuses": "",
        "atc_codes": "",
        "atc_names": "",
        "atc_l1_codes": "",
        "atc_l1_names": "",
        "atc_l2_codes": "",
        "atc_l2_names": "",
        "atc_l3_codes": "",
        "atc_l3_names": "",
        "atc_l4_codes": "",
        "atc_l4_names": "",
        "atc_l5_codes": "",
        "atc_l5_names": "",
        "atc_bridge_applied": "",
        "atc_original_codes": "",
    }


def build_pottr_canonical_rows_from_alias_atc(
    *,
    pottr_raw_dir: Path,
    alias_atc_rows: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Build one POTTR row per canonical drug, carrying all alias RxNorm IDs.

    This is the row set used for CTGov -> POTTR linkage. The four key POTTR
    columns are emitted exactly as:
        pottr_canonical_drug_name
        pottr_drug_aliases
        pottr_direct_class_names
        pottr_class_hierarchy_paths
    """
    concepts = load_pottr_concepts(pottr_raw_dir)
    concepts_by_canonical = group_concepts_by_canonical(concepts)
    hierarchy_paths_by_class = load_hierarchy_paths_by_class(pottr_raw_dir)

    alias_rows_by_concept_key: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in alias_atc_rows:
        concept_key = clean_text(row.get("concept_key", ""))
        if concept_key:
            alias_rows_by_concept_key[concept_key].append(row)

    canonical_rows: list[dict[str, object]] = []

    for row_index, (_canonical_key, canonical_concepts) in enumerate(concepts_by_canonical.items()):
        canonical_name = clean_text(canonical_concepts[0].get("canonical_drug_name", ""))

        all_aliases = ordered_unique(
            alias
            for concept in canonical_concepts
            for alias in concept.get("aliases", [])
        )
        noncanonical_aliases = [
            alias
            for alias in all_aliases
            if normalize_key(alias) != normalize_key(canonical_name)
        ]

        direct_classes = ordered_unique(
            direct_class
            for concept in canonical_concepts
            for direct_class in concept.get("direct_classes", [])
        )
        hierarchy_paths = hierarchy_paths_for_direct_classes(direct_classes, hierarchy_paths_by_class)

        concept_keys = ordered_unique(concept.get("concept_key", "") for concept in canonical_concepts)
        concept_alias_atc_rows = [
            row
            for concept_key in concept_keys
            for row in alias_rows_by_concept_key.get(concept_key, [])
        ]

        rxnorm_summary = summarize_annotation_rows(concept_alias_atc_rows)

        canonical_rows.append(
            {
                "__row_index": str(row_index),
                "pottr_canonical_drug_name": canonical_name,
                "pottr_drug_aliases": ordered_join(noncanonical_aliases),
                "pottr_direct_class_names": ordered_join(direct_classes),
                "pottr_class_hierarchy_paths": ordered_join(hierarchy_paths),
                "pottr_concept_keys": ordered_join(concept_keys),
                "rxnorm_lookup_terms": ordered_join(collect_from_rows(concept_alias_atc_rows, ("alias",))),
                "rxnorm_lookup_term_sources": "alias_or_canonical",
                "rxnorm_lookup_terms_tried": ordered_join(collect_from_rows(concept_alias_atc_rows, ("alias",))),
                "rxnorm_match_statuses": rxnorm_summary["rxnorm_match_statuses"],
                "rxnorm_match_stages": rxnorm_summary["rxnorm_match_stages"],
                "rxnorm_matched_terms": rxnorm_summary["rxnorm_matched_terms"],
                "rxnorm_rxcuis": rxnorm_summary["rxnorm_rxcuis"],
                "rxnorm_canonical_names": rxnorm_summary["rxnorm_canonical_names"],
                "rxnorm_term_types": rxnorm_summary["rxnorm_term_types"],
                "rxnorm_ingredient_rxcuis": rxnorm_summary["rxnorm_ingredient_rxcuis"],
                "rxnorm_ingredient_names": rxnorm_summary["rxnorm_ingredient_names"],
                "rxnorm_ingredient_term_types": rxnorm_summary["rxnorm_ingredient_term_types"],
                "ingredient_resolution_stages": rxnorm_summary["ingredient_resolution_stages"],
                "pottr_link_anchor_rxcuis": ordered_join(
                    collect_from_rows(concept_alias_atc_rows, POTTR_LINK_ANCHOR_RXCUI_COLUMNS)
                ),
                "pottr_link_anchor_names": ordered_join(
                    collect_from_rows(concept_alias_atc_rows, POTTR_LINK_ANCHOR_NAME_COLUMNS)
                ),
                "pottr_link_anchor_strategies": ordered_join(
                    collect_from_rows(concept_alias_atc_rows, POTTR_LINK_ANCHOR_STRATEGY_COLUMNS)
                ),
                "manual_review_needed": rxnorm_summary["manual_review_needed"],
                "atc_match_statuses": rxnorm_summary["atc_match_statuses"],
                "atc_codes": rxnorm_summary["atc_codes"],
                "atc_names": rxnorm_summary["atc_names"],
                "atc_l1_codes": rxnorm_summary["atc_l1_codes"],
                "atc_l1_names": rxnorm_summary["atc_l1_names"],
                "atc_l2_codes": rxnorm_summary["atc_l2_codes"],
                "atc_l2_names": rxnorm_summary["atc_l2_names"],
                "atc_l3_codes": rxnorm_summary["atc_l3_codes"],
                "atc_l3_names": rxnorm_summary["atc_l3_names"],
                "atc_l4_codes": rxnorm_summary["atc_l4_codes"],
                "atc_l4_names": rxnorm_summary["atc_l4_names"],
                "atc_l5_codes": rxnorm_summary["atc_l5_codes"],
                "atc_l5_names": rxnorm_summary["atc_l5_names"],
                "atc_bridge_applied": rxnorm_summary["atc_bridge_applied"],
                "atc_original_codes": rxnorm_summary["atc_original_codes"],
            }
        )

    return canonical_rows


def pottr_names_from_row(row: Mapping[str, object]) -> list[str]:
    names: list[str] = []
    for column in POTTR_CANONICAL_COLUMNS:
        names.extend(split_display_values(row.get(column, ""), delimiter="|"))
    for column in POTTR_ALIAS_COLUMNS:
        names.extend(split_display_values(row.get(column, ""), delimiter="|"))
    return ordered_unique(names)


def build_pottr_indexes(
    pottr_rows: Sequence[Mapping[str, object]],
) -> tuple[dict[str, list[Mapping[str, object]]], dict[str, list[Mapping[str, object]]]]:
    """Index POTTR canonical rows by RxNorm ID and by exact drug/alias name."""
    by_rxnorm_id: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    by_exact_name: dict[str, list[Mapping[str, object]]] = defaultdict(list)

    for row_index, row in enumerate(pottr_rows):
        row_with_index = dict(row)
        row_with_index["__row_index"] = clean_text(row.get("__row_index", "")) or str(row_index)

        for name in pottr_names_from_row(row_with_index):
            key = normalize_key(name)
            if key:
                by_exact_name[key].append(row_with_index)

        # Link by RxNorm IDs. Do not require status == MATCHED here; populated
        # RxNorm ID fields are enough for index construction.
        for rxnorm_id in rxnorm_link_rxcuis_from_row(row_with_index):
            if rxnorm_id:
                by_rxnorm_id[rxnorm_id].append(row_with_index)

    return by_rxnorm_id, by_exact_name


def unique_pottr_rows(rows: Iterable[Mapping[str, object]]) -> list[Mapping[str, object]]:
    out: list[Mapping[str, object]] = []
    seen: set[str] = set()

    for row in rows:
        key = clean_text(row.get("__row_index", ""))
        if not key:
            key = "\x1f".join(
                [
                    ordered_join(collect_from_rows([row], POTTR_CANONICAL_COLUMNS)),
                    ordered_join(rxnorm_link_rxcuis_from_row(row)),
                ]
            )

        if key not in seen:
            seen.add(key)
            out.append(row)

    return out


def summarize_pottr_rows(rows: Sequence[Mapping[str, object]]) -> dict[str, str]:
    return {
        "canonical_drug_name": ordered_join(collect_from_rows(rows, POTTR_CANONICAL_COLUMNS)),
        "drug_aliases": ordered_join(collect_from_rows(rows, POTTR_ALIAS_COLUMNS)),
        "direct_class_names": ordered_join(collect_from_rows(rows, POTTR_DIRECT_CLASS_COLUMNS)),
        "class_hierarchy_paths": ordered_join(collect_from_rows(rows, POTTR_HIERARCHY_COLUMNS)),
        "rxnorm_lookup_terms": ordered_join(collect_from_rows(rows, POTTR_LOOKUP_TERM_COLUMNS)),
        "rxnorm_lookup_term_sources": ordered_join(collect_from_rows(rows, POTTR_LOOKUP_SOURCE_COLUMNS)),
        "rxnorm_lookup_terms_tried": ordered_join(collect_from_rows(rows, POTTR_LOOKUP_TERMS_TRIED_COLUMNS)),
        "rxnorm_match_statuses": ordered_join(collect_from_rows(rows, RXNORM_MATCH_STATUS_COLUMNS)),
        "rxnorm_match_stages": ordered_join(collect_from_rows(rows, RXNORM_MATCH_STAGE_COLUMNS)),
        "rxnorm_rxcuis": ordered_join(collect_from_rows(rows, RXNORM_RXCUI_COLUMNS)),
        "rxnorm_canonical_names": ordered_join(collect_from_rows(rows, RXNORM_CANONICAL_NAME_COLUMNS)),
        "rxnorm_term_types": ordered_join(collect_from_rows(rows, RXNORM_TERM_TYPE_COLUMNS)),
        "rxnorm_ingredient_rxcuis": ordered_join(collect_from_rows(rows, RXNORM_INGREDIENT_RXCUI_COLUMNS)),
        "rxnorm_ingredient_names": ordered_join(collect_from_rows(rows, RXNORM_INGREDIENT_NAME_COLUMNS)),
        "rxnorm_ingredient_term_types": ordered_join(collect_from_rows(rows, RXNORM_INGREDIENT_TERM_TYPE_COLUMNS)),
        "ingredient_resolution_stages": ordered_join(collect_from_rows(rows, INGREDIENT_RESOLUTION_STAGE_COLUMNS)),
        "link_anchor_rxcuis": ordered_join(collect_from_rows(rows, POTTR_LINK_ANCHOR_RXCUI_COLUMNS)),
        "link_anchor_names": ordered_join(collect_from_rows(rows, POTTR_LINK_ANCHOR_NAME_COLUMNS)),
        "link_anchor_strategies": ordered_join(collect_from_rows(rows, POTTR_LINK_ANCHOR_STRATEGY_COLUMNS)),
        "manual_review_needed": collect_bool_from_rows(rows, MANUAL_REVIEW_COLUMNS),
        "atc_match_statuses": ordered_join(collect_from_rows(rows, ATC_MATCH_STATUS_COLUMNS)),
        "atc_codes": ordered_join(collect_from_rows(rows, ATC_CODE_COLUMNS)),
        "atc_names": ordered_join(collect_from_rows(rows, ATC_NAME_COLUMNS)),
        "atc_l1_codes": ordered_join(collect_from_rows(rows, ATC_L1_CODE_COLUMNS)),
        "atc_l1_names": ordered_join(collect_from_rows(rows, ATC_L1_NAME_COLUMNS)),
        "atc_l2_codes": ordered_join(collect_from_rows(rows, ATC_L2_CODE_COLUMNS)),
        "atc_l2_names": ordered_join(collect_from_rows(rows, ATC_L2_NAME_COLUMNS)),
        "atc_l3_codes": ordered_join(collect_from_rows(rows, ATC_L3_CODE_COLUMNS)),
        "atc_l3_names": ordered_join(collect_from_rows(rows, ATC_L3_NAME_COLUMNS)),
        "atc_l4_codes": ordered_join(collect_from_rows(rows, ATC_L4_CODE_COLUMNS)),
        "atc_l4_names": ordered_join(collect_from_rows(rows, ATC_L4_NAME_COLUMNS)),
        "atc_l5_codes": ordered_join(collect_from_rows(rows, ATC_L5_CODE_COLUMNS)),
        "atc_l5_names": ordered_join(collect_from_rows(rows, ATC_L5_NAME_COLUMNS)),
        "atc_bridge_applied": collect_bool_from_rows(rows, ATC_BRIDGE_APPLIED_COLUMNS),
        "atc_original_codes": ordered_join(collect_from_rows(rows, ATC_ORIGINAL_CODE_COLUMNS)),
    }


def determine_presence_status(
    *,
    ctgov_has_annotation: bool,
    ctgov_has_confident_rxnorm_match: bool,
    ctgov_link_rxcuis: Sequence[str],
    present_link_rxcuis: Sequence[str],
    absent_link_rxcuis: Sequence[str],
) -> tuple[str, str]:
    if not ctgov_has_annotation:
        return "NO_CTGOV_RXNORM_ATC_ANNOTATION_ROW", ""

    if not ctgov_link_rxcuis:
        return "UNCOMPARABLE_NO_CTGOV_RXNORM_ID", ""

    if not ctgov_has_confident_rxnorm_match:
        return "UNCOMPARABLE_NO_CONFIDENT_CTGOV_RXNORM_MATCH", ""

    if present_link_rxcuis and absent_link_rxcuis:
        return "PARTIAL_POTTR_RXNORM_ID_MATCH", "partial"

    if present_link_rxcuis:
        return "POTTR_RXNORM_ID_MATCH", "false"

    return "MISSING_FROM_POTTR_BY_RXNORM_ID", "true"


def build_crosswalk_rows(
    *,
    ctgov_terms: Sequence[str],
    ctgov_summaries_by_key: Mapping[str, Mapping[str, str]],
    pottr_by_rxnorm_id: Mapping[str, Sequence[Mapping[str, object]]],
    pottr_by_exact_name: Mapping[str, Sequence[Mapping[str, object]]],
    sort_output: bool = False,
) -> list[dict[str, object]]:
    terms = list(ctgov_terms)
    if sort_output:
        terms = sorted(terms, key=normalize_key)

    rows: list[dict[str, object]] = []

    for term in terms:
        ctgov = dict(ctgov_summaries_by_key.get(normalize_key(term), blank_ctgov_summary(term)))

        ctgov_link_rxcuis = rxnorm_link_rxcuis_from_row(ctgov)
        ctgov_has_annotation = ctgov.get("has_annotation_row") == "true"

        ctgov_match_statuses = split_display_values(
            ctgov.get("rxnorm_match_statuses", ""),
            delimiter="|",
        )
        ctgov_has_confident_match = has_exact_value(ctgov_match_statuses, "MATCHED")

        # If populated RxNorm IDs exist but the source lacks a status column,
        # the row is still linkable.
        if not ctgov_match_statuses and ctgov_link_rxcuis:
            ctgov_has_confident_match = True

        present_link_rxcuis = [
            rxcui for rxcui in ctgov_link_rxcuis if rxcui in pottr_by_rxnorm_id
        ]
        absent_link_rxcuis = [
            rxcui for rxcui in ctgov_link_rxcuis if rxcui not in pottr_by_rxnorm_id
        ]

        pottr_matches = unique_pottr_rows(
            row
            for rxcui in present_link_rxcuis
            for row in pottr_by_rxnorm_id.get(rxcui, [])
        )
        pottr_summary = summarize_pottr_rows(pottr_matches)

        pottr_link_rxcuis = ordered_join(
            rxnorm_id
            for pottr_row in pottr_matches
            for rxnorm_id in rxnorm_link_rxcuis_from_row(pottr_row)
        )

        exact_matches = unique_pottr_rows(pottr_by_exact_name.get(normalize_key(term), []))
        exact_summary = summarize_pottr_rows(exact_matches)

        presence_status, missing_flag = determine_presence_status(
            ctgov_has_annotation=ctgov_has_annotation,
            ctgov_has_confident_rxnorm_match=ctgov_has_confident_match,
            ctgov_link_rxcuis=ctgov_link_rxcuis,
            present_link_rxcuis=present_link_rxcuis,
            absent_link_rxcuis=absent_link_rxcuis,
        )

        rows.append(
            {
                "ctgov_input_drug_name": term,
                "pottr_presence_status": presence_status,
                "is_missing_from_pottr_by_rxnorm_id": missing_flag,
                "ctgov_rxnorm_link_rxcuis": ordered_join(ctgov_link_rxcuis),
                "ctgov_rxnorm_link_rxcuis_present_in_pottr": ordered_join(present_link_rxcuis),
                "ctgov_rxnorm_link_rxcuis_absent_from_pottr": ordered_join(absent_link_rxcuis),
                "pottr_rxnorm_link_rxcuis": pottr_link_rxcuis,
                "pottr_match_count": len(pottr_matches),

                # CTGov RxNorm/ATC annotation.
                "ctgov_rxnorm_match_statuses": ctgov.get("rxnorm_match_statuses", ""),
                "ctgov_rxnorm_match_stages": ctgov.get("rxnorm_match_stages", ""),
                "ctgov_rxnorm_matched_terms": ctgov.get("rxnorm_matched_terms", ""),
                "ctgov_rxnorm_rxcuis": ctgov.get("rxnorm_rxcuis", ""),
                "ctgov_rxnorm_canonical_names": ctgov.get("rxnorm_canonical_names", ""),
                "ctgov_rxnorm_term_types": ctgov.get("rxnorm_term_types", ""),
                "ctgov_rxnorm_ingredient_rxcuis": ctgov.get("rxnorm_ingredient_rxcuis", ""),
                "ctgov_rxnorm_ingredient_names": ctgov.get("rxnorm_ingredient_names", ""),
                "ctgov_rxnorm_ingredient_term_types": ctgov.get("rxnorm_ingredient_term_types", ""),
                "ctgov_ingredient_resolution_stages": ctgov.get("ingredient_resolution_stages", ""),
                "ctgov_manual_review_needed": ctgov.get("manual_review_needed", ""),
                "ctgov_atc_match_statuses": ctgov.get("atc_match_statuses", ""),
                "ctgov_atc_codes": ctgov.get("atc_codes", ""),
                "ctgov_atc_names": ctgov.get("atc_names", ""),
                "ctgov_atc_l1_codes": ctgov.get("atc_l1_codes", ""),
                "ctgov_atc_l1_names": ctgov.get("atc_l1_names", ""),
                "ctgov_atc_l2_codes": ctgov.get("atc_l2_codes", ""),
                "ctgov_atc_l2_names": ctgov.get("atc_l2_names", ""),
                "ctgov_atc_l3_codes": ctgov.get("atc_l3_codes", ""),
                "ctgov_atc_l3_names": ctgov.get("atc_l3_names", ""),
                "ctgov_atc_l4_codes": ctgov.get("atc_l4_codes", ""),
                "ctgov_atc_l4_names": ctgov.get("atc_l4_names", ""),
                "ctgov_atc_l5_codes": ctgov.get("atc_l5_codes", ""),
                "ctgov_atc_l5_names": ctgov.get("atc_l5_names", ""),
                "ctgov_atc_bridge_applied": ctgov.get("atc_bridge_applied", ""),
                "ctgov_atc_original_codes": ctgov.get("atc_original_codes", ""),

                # Required POTTR fields, linked by RxNorm ID.
                "pottr_canonical_drug_name": pottr_summary["canonical_drug_name"],
                "pottr_drug_aliases": pottr_summary["drug_aliases"],
                "pottr_direct_class_names": pottr_summary["direct_class_names"],
                "pottr_class_hierarchy_paths": pottr_summary["class_hierarchy_paths"],

                # POTTR RxNorm/ATC audit fields.
                "pottr_rxnorm_lookup_terms": pottr_summary["rxnorm_lookup_terms"],
                "pottr_rxnorm_lookup_term_sources": pottr_summary["rxnorm_lookup_term_sources"],
                "pottr_rxnorm_lookup_terms_tried": pottr_summary["rxnorm_lookup_terms_tried"],
                "pottr_rxnorm_match_statuses": pottr_summary["rxnorm_match_statuses"],
                "pottr_rxnorm_match_stages": pottr_summary["rxnorm_match_stages"],
                "pottr_rxnorm_rxcuis": pottr_summary["rxnorm_rxcuis"],
                "pottr_rxnorm_canonical_names": pottr_summary["rxnorm_canonical_names"],
                "pottr_rxnorm_term_types": pottr_summary["rxnorm_term_types"],
                "pottr_rxnorm_ingredient_rxcuis": pottr_summary["rxnorm_ingredient_rxcuis"],
                "pottr_rxnorm_ingredient_names": pottr_summary["rxnorm_ingredient_names"],
                "pottr_rxnorm_ingredient_term_types": pottr_summary["rxnorm_ingredient_term_types"],
                "pottr_ingredient_resolution_stages": pottr_summary["ingredient_resolution_stages"],
                "pottr_link_anchor_rxcuis": pottr_summary["link_anchor_rxcuis"],
                "pottr_link_anchor_names": pottr_summary["link_anchor_names"],
                "pottr_link_anchor_strategies": pottr_summary["link_anchor_strategies"],
                "pottr_manual_review_needed": pottr_summary["manual_review_needed"],
                "pottr_atc_match_statuses": pottr_summary["atc_match_statuses"],
                "pottr_atc_codes": pottr_summary["atc_codes"],
                "pottr_atc_names": pottr_summary["atc_names"],
                "pottr_atc_l1_codes": pottr_summary["atc_l1_codes"],
                "pottr_atc_l1_names": pottr_summary["atc_l1_names"],
                "pottr_atc_l2_codes": pottr_summary["atc_l2_codes"],
                "pottr_atc_l2_names": pottr_summary["atc_l2_names"],
                "pottr_atc_l3_codes": pottr_summary["atc_l3_codes"],
                "pottr_atc_l3_names": pottr_summary["atc_l3_names"],
                "pottr_atc_l4_codes": pottr_summary["atc_l4_codes"],
                "pottr_atc_l4_names": pottr_summary["atc_l4_names"],
                "pottr_atc_l5_codes": pottr_summary["atc_l5_codes"],
                "pottr_atc_l5_names": pottr_summary["atc_l5_names"],
                "pottr_atc_bridge_applied": pottr_summary["atc_bridge_applied"],
                "pottr_atc_original_codes": pottr_summary["atc_original_codes"],
                "pottr_exact_casefold_term_match": "true" if exact_matches else "false",
                "pottr_exact_casefold_canonical_drug_name": exact_summary["canonical_drug_name"],
            }
        )

    return rows


def build_summary_rows(
    *,
    output_rows: Sequence[Mapping[str, object]],
    ctgov_input_json: Path,
    pottr_raw_dir: Path,
    rxnorm_rrf_dir: Path,
    atc_tree_tsv: Path,
    output_tsv: Path,
    ctgov_unique_terms: int,
    ctgov_alias_atc_rows: int,
    pottr_alias_atc_rows: int,
    pottr_canonical_rows: int,
    ctgov_atc_bridges_applied: int,
    pottr_atc_bridges_applied: int,
) -> list[dict[str, object]]:
    status_counts = Counter(clean_text(row.get("pottr_presence_status", "")) for row in output_rows)

    def count_rows_with(column: str) -> int:
        return sum(1 for row in output_rows if clean_text(row.get(column, "")))

    rows: list[dict[str, object]] = [
        {"metric": "ctgov_input_json", "value": str(ctgov_input_json)},
        {"metric": "pottr_raw_dir", "value": str(pottr_raw_dir)},
        {"metric": "rxnorm_rrf_dir", "value": str(rxnorm_rrf_dir)},
        {"metric": "atc_tree_tsv", "value": str(atc_tree_tsv)},
        {"metric": "output_tsv", "value": str(output_tsv)},
        {"metric": "ctgov_unique_terms_extracted_from_raw", "value": ctgov_unique_terms},
        {"metric": "ctgov_alias_atc_rows_built_in_memory", "value": ctgov_alias_atc_rows},
        {"metric": "pottr_alias_atc_rows_built_in_memory", "value": pottr_alias_atc_rows},
        {"metric": "pottr_canonical_rows_built_in_memory", "value": pottr_canonical_rows},
        {"metric": "ctgov_atc_bridges_applied", "value": ctgov_atc_bridges_applied},
        {"metric": "pottr_atc_bridges_applied", "value": pottr_atc_bridges_applied},
        {"metric": "ctgov_unique_drug_output_rows", "value": len(output_rows)},
        {"metric": "ctgov_rows_with_rxnorm_link_id", "value": count_rows_with("ctgov_rxnorm_link_rxcuis")},
        {"metric": "ctgov_rows_with_rxnorm_ingredient", "value": count_rows_with("ctgov_rxnorm_ingredient_rxcuis")},
        {"metric": "ctgov_rows_with_ctgov_atc_code", "value": count_rows_with("ctgov_atc_codes")},
        {"metric": "ctgov_rows_with_pottr_rxnorm_id_match", "value": status_counts["POTTR_RXNORM_ID_MATCH"]},
        {"metric": "ctgov_rows_with_partial_pottr_rxnorm_id_match", "value": status_counts["PARTIAL_POTTR_RXNORM_ID_MATCH"]},
        {"metric": "ctgov_rows_missing_from_pottr_by_rxnorm_id", "value": status_counts["MISSING_FROM_POTTR_BY_RXNORM_ID"]},
        {"metric": "ctgov_rows_uncomparable_no_confident_rxnorm_match", "value": status_counts["UNCOMPARABLE_NO_CONFIDENT_CTGOV_RXNORM_MATCH"]},
        {"metric": "ctgov_rows_uncomparable_no_rxnorm_id", "value": status_counts["UNCOMPARABLE_NO_CTGOV_RXNORM_ID"]},
        {"metric": "ctgov_rows_without_annotation_row", "value": status_counts["NO_CTGOV_RXNORM_ATC_ANNOTATION_ROW"]},
        {
            "metric": "ctgov_missing_from_pottr_rows_with_ctgov_atc_code",
            "value": sum(
                1
                for row in output_rows
                if row.get("pottr_presence_status") == "MISSING_FROM_POTTR_BY_RXNORM_ID"
                and clean_text(row.get("ctgov_atc_codes", ""))
            ),
        },
        {
            "metric": "ctgov_rows_with_exact_casefold_pottr_term_match",
            "value": sum(1 for row in output_rows if row.get("pottr_exact_casefold_term_match") == "true"),
        },
        {"metric": "rows_with_pottr_canonical_drug_name", "value": count_rows_with("pottr_canonical_drug_name")},
        {"metric": "rows_with_pottr_direct_class_names", "value": count_rows_with("pottr_direct_class_names")},
        {"metric": "rows_with_pottr_class_hierarchy_paths", "value": count_rows_with("pottr_class_hierarchy_paths")},
    ]

    for status, count in sorted(status_counts.items()):
        rows.append({"metric": f"pottr_presence_status__{status}", "value": count})

    return rows


def build_ctgov_pottr_crosswalk_raw_to_final(
    *,
    ctgov_input_json: Path,
    pottr_raw_dir: Path,
    rxnorm_rrf_dir: Path,
    atc_tree_tsv: Path,
    output_tsv: Path,
    summary_tsv: Path | None,
    sort_output: bool = False,
    obsolete_atc_code_bridge: Mapping[str, str] | None = None,
) -> tuple[int, int | None]:
    validate_inputs(
        ctgov_input_json=ctgov_input_json,
        pottr_raw_dir=pottr_raw_dir,
        rxnorm_rrf_dir=rxnorm_rrf_dir,
        atc_tree_tsv=atc_tree_tsv,
    )
    obsolete_atc_code_bridge = dict(obsolete_atc_code_bridge or {})

    ctgov_terms = build_ctgov_unique_terms_from_raw(
        ctgov_input_json=ctgov_input_json,
        sort_terms=sort_output,
    )

    logger.info("Loading RxNorm indexes from %s", rxnorm_rrf_dir)
    conso_index, rel_index = load_rxnorm_indexes(rxnorm_rrf_dir)

    logger.info("Building CTGov RxNorm records in memory")
    ctgov_as_pottr = build_ctgov_pseudo_pottr_records(
        ctgov_terms=ctgov_terms,
        conso_index=conso_index,
        rel_index=rel_index,
    )
    ctgov_target_rxcuis = matched_link_rxcuis_from_pottr_build_result(ctgov_as_pottr)

    logger.info("Building POTTR RxNorm records in memory")
    pottr = build_pottr_records(pottr_raw_dir, conso_index, rel_index)
    pottr_target_rxcuis = matched_link_rxcuis_from_pottr_build_result(pottr)

    target_rxcuis = ctgov_target_rxcuis | pottr_target_rxcuis
    logger.info("Loading ATC index for %d unique RxNorm IDs", len(target_rxcuis))
    tree = AtcTree.from_tsv(atc_tree_tsv)
    atc_by_rxcui = load_rxnconso_atc_index(
        rxnconso_path=rxnorm_rrf_dir / RXNCONSO_FILENAME,
        target_rxcuis=target_rxcuis,
    )

    logger.info("Annotating CTGov terms to ATC in memory")
    ctgov_alias_atc_rows, ctgov_bridges_applied = alias_atc_records(
        ctgov_as_pottr,
        atc_by_rxcui=atc_by_rxcui,
        tree=tree,
        obsolete_atc_code_bridge=obsolete_atc_code_bridge,
    )
    ctgov_summaries = summarize_ctgov_alias_atc_rows(ctgov_alias_atc_rows)

    logger.info("Annotating POTTR aliases/classes to ATC in memory")
    pottr_alias_atc_rows, pottr_bridges_applied = alias_atc_records(
        pottr,
        atc_by_rxcui=atc_by_rxcui,
        tree=tree,
        obsolete_atc_code_bridge=obsolete_atc_code_bridge,
    )

    pottr_canonical_rows = build_pottr_canonical_rows_from_alias_atc(
        pottr_raw_dir=pottr_raw_dir,
        alias_atc_rows=pottr_alias_atc_rows,
    )
    pottr_by_rxnorm_id, pottr_by_exact_name = build_pottr_indexes(pottr_canonical_rows)

    output_rows = build_crosswalk_rows(
        ctgov_terms=ctgov_terms,
        ctgov_summaries_by_key=ctgov_summaries,
        pottr_by_rxnorm_id=pottr_by_rxnorm_id,
        pottr_by_exact_name=pottr_by_exact_name,
        sort_output=sort_output,
    )
    write_tsv(output_tsv, output_rows, OUTPUT_COLUMNS)

    summary_count: int | None = None
    if summary_tsv is not None:
        summary_rows = build_summary_rows(
            output_rows=output_rows,
            ctgov_input_json=ctgov_input_json,
            pottr_raw_dir=pottr_raw_dir,
            rxnorm_rrf_dir=rxnorm_rrf_dir,
            atc_tree_tsv=atc_tree_tsv,
            output_tsv=output_tsv,
            ctgov_unique_terms=len(ctgov_terms),
            ctgov_alias_atc_rows=len(ctgov_alias_atc_rows),
            pottr_alias_atc_rows=len(pottr_alias_atc_rows),
            pottr_canonical_rows=len(pottr_canonical_rows),
            ctgov_atc_bridges_applied=ctgov_bridges_applied,
            pottr_atc_bridges_applied=pottr_bridges_applied,
        )
        write_tsv(summary_tsv, summary_rows, SUMMARY_COLUMNS)
        summary_count = len(summary_rows)

    return len(output_rows), summary_count


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build one raw-to-final CTGov -> POTTR crosswalk file: every unique "
            "CTGov drug from raw CTGov input, CTGov RxNorm/ATC annotations, and "
            "POTTR drug/class/hierarchy matches via shared RxNorm ID."
        )
    )
    parser.add_argument(
        "--ctgov-input-json",
        "--ctgov_input_json",
        dest="ctgov_input_json",
        type=Path,
        default=DEFAULT_CTGOV_INPUT_JSON,
        help=f"Raw CTGov JSON/NDJSON input. Default: {DEFAULT_CTGOV_INPUT_JSON}",
    )
    parser.add_argument(
        "--pottr-raw-dir",
        "--pottr_raw_dir",
        dest="pottr_raw_dir",
        type=Path,
        default=DEFAULT_POTTR_RAW_DIR,
        help=(
            "Raw POTTR directory containing drug_database.txt and "
            f"drug_class_hierarchy.txt. Default: {DEFAULT_POTTR_RAW_DIR}"
        ),
    )
    parser.add_argument(
        "--rxnorm-rrf-dir",
        "--rxnorm_rrf_dir",
        dest="rxnorm_rrf_dir",
        type=Path,
        default=DEFAULT_RXNORM_RRF_DIR,
        help=f"RxNorm RRF directory. Default: {DEFAULT_RXNORM_RRF_DIR}",
    )
    parser.add_argument(
        "--atc-tree-tsv",
        "--atc_tree_tsv",
        dest="atc_tree_tsv",
        type=Path,
        default=DEFAULT_ATC_TREE_TSV,
        help=f"ATC tree TSV. Default: {DEFAULT_ATC_TREE_TSV}",
    )
    parser.add_argument(
        "--output-tsv",
        "--output_tsv",
        dest="output_tsv",
        type=Path,
        default=DEFAULT_OUTPUT_TSV,
        help=f"Main output TSV. Default: {DEFAULT_OUTPUT_TSV}",
    )
    parser.add_argument(
        "--summary-tsv",
        "--summary_tsv",
        dest="summary_tsv",
        type=Path,
        default=DEFAULT_SUMMARY_TSV,
        help=f"Optional summary TSV. Default: {DEFAULT_SUMMARY_TSV}",
    )
    parser.add_argument(
        "--no-summary",
        "--no_summary",
        dest="no_summary",
        action="store_true",
        help="Do not write the summary TSV.",
    )
    parser.add_argument(
        "--sort",
        action="store_true",
        help="Sort output rows by CTGov input drug name instead of preserving first-seen CTGov order.",
    )
    parser.add_argument(
        "--no-2026-obsolete-atc-bridge",
        "--no_2026_obsolete_atc_bridge",
        dest="no_2026_obsolete_atc_bridge",
        action="store_true",
        help="Disable bridges for old ATC codes reclassified in the 2026 ATC tree.",
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

    obsolete_bridge = (
        {}
        if args.no_2026_obsolete_atc_bridge
        else DEFAULT_OBSOLETE_ATC_CODE_BRIDGE
    )

    try:
        summary_tsv = None if args.no_summary else args.summary_tsv
        output_rows, summary_rows = build_ctgov_pottr_crosswalk_raw_to_final(
            ctgov_input_json=args.ctgov_input_json,
            pottr_raw_dir=args.pottr_raw_dir,
            rxnorm_rrf_dir=args.rxnorm_rrf_dir,
            atc_tree_tsv=args.atc_tree_tsv,
            output_tsv=args.output_tsv,
            summary_tsv=summary_tsv,
            sort_output=args.sort,
            obsolete_atc_code_bridge=obsolete_bridge,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote CTGov -> POTTR crosswalk rows: {output_rows}", file=sys.stderr)
    print(f"Output TSV: {args.output_tsv}", file=sys.stderr)
    if summary_tsv is not None:
        print(f"Wrote summary rows: {summary_rows}", file=sys.stderr)
        print(f"Summary TSV: {summary_tsv}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
