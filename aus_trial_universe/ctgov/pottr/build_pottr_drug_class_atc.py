#!/usr/bin/env python3
"""Build file-based POTTR drug-class and RxNorm->ATC outputs.

This module is intentionally a thin file-export layer over the existing POTTR
classification logic in:

    aus_trial_universe.ctgov.drug_ontology.classification.pottr.pottr_drug_classes

It does not reimplement POTTR parsing. Instead it calls build_pottr_records(),
then writes standalone processed TSVs for the POTTR analysis package:

    POTTR drug aliases/classes
        -> existing POTTR RxNorm ingredient resolution
        -> RxNorm ingredient RXCUI to ATC
        -> ATC tree hierarchy enrichment

Default paths are aligned with the current trial-curator_repo layout.
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from collections import OrderedDict, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from aus_trial_universe.ctgov.drug_ontology.classification.atc.ingredient_to_atc import (
    RXNCONSO_FILENAME,
    AtcResolution,
    AtcTree,
    RxnConsoAtcAtom,
    clean_text as atc_clean_text,
    load_rxnconso_atc_index,
    resolve_atc_atom,
    resolve_ingredient_to_atc,
)
from aus_trial_universe.ctgov.drug_ontology.classification.pottr.pottr_drug_classes import (
    DRUG_CLASS_HIERARCHY_FILENAME,
    DRUG_DATABASE_FILENAME,
    PottrBuildResult,
    PottrDrugAlias,
    PottrDrugClassAssignment,
    PottrDrugConcept,
    build_pottr_records,
    clean_text,
    normalize_alias_key,
)
from aus_trial_universe.ctgov.drug_ontology.rxnorm.matcher import RxnConsoIndex, RxnRelIndex

logger = logging.getLogger(__name__)

DEFAULT_POTTR_ANALYSIS_DIR = Path("data/pottr_hartwig_version")
DEFAULT_POTTR_RAW_DIR = DEFAULT_POTTR_ANALYSIS_DIR / "raw"
DEFAULT_PROCESSED_DIR = DEFAULT_POTTR_ANALYSIS_DIR / "processed"
DEFAULT_EXPORTS_DIR = DEFAULT_POTTR_ANALYSIS_DIR / "exports"
DEFAULT_SUMMARY_DIR = DEFAULT_POTTR_ANALYSIS_DIR / "summary"

DEFAULT_RXNORM_RRF_DIR = Path("data/ctgov/drug_ontology/raw/RxNorm_full_03022026")
DEFAULT_ATC_TREE_TSV = Path("data/ctgov/drug_ontology/raw/ATC/version_25042026/atc_tree.tsv")

CONCEPTS_FILENAME = "pottr_concepts.tsv"
ALIASES_FILENAME = "pottr_drug_aliases.tsv"
UNIQUE_DRUGS_FILENAME = "pottr_unique_drugs.tsv"
CLASS_ASSIGNMENTS_FILENAME = "pottr_drug_class_assignments.tsv"
ALIAS_ATC_FILENAME = "pottr_unique_drugs.atc_annotated.tsv"
CLASS_ATC_FILENAME = "pottr_drug_class_atc_annotated.tsv"
SUMMARY_FILENAME = "pottr_build_summary.tsv"

ESSENTIAL_UNIQUE_ATC_FILENAME = "pottr_unique_drugs_atc_essential.tsv"
ESSENTIAL_CLASS_ATC_FILENAME = "pottr_drug_class_atc_essential.tsv"
EXPORT_MANIFEST_FILENAME = "pottr_export_manifest.tsv"

SUMMARY_DELIMITER = " | "

# WHO ATC 2026 reclassifications observed when moving from the older ATC tree.
# RxNorm ATC atoms can lag WHO ATC; these bridges prevent valid drugs from
# becoming ATC_CODE_NOT_IN_TREE solely because the ATC hierarchy moved.
DEFAULT_OBSOLETE_ATC_CODE_BRIDGE: dict[str, str] = {
    "L01EX17": "L01EP01",  # capmatinib
    "L01EX21": "L01EP02",  # tepotinib
    "L04AA58": "L04AL01",  # efgartigimod alfa
    "L04AG16": "L04AL02",  # rozanolixizumab
}

CONCEPT_COLUMNS = [
    "concept_key",
    "concept_index",
    "drug_raw",
    "canonical_drug_name",
    "aliases_raw",
    "direct_classes_raw",
]

ALIAS_COLUMNS = [
    "concept_key",
    "concept_index",
    "canonical_drug_name",
    "drug_raw",
    "alias_position",
    "alias",
    "alias_normalized",
    "rxnorm_match_status",
    "rxnorm_match_stage",
    "rxnorm_rxcui",
    "rxnorm_canonical_name",
    "rxnorm_term_type",
    "rxnorm_ingredient_rxcui",
    "rxnorm_ingredient_name",
    "rxnorm_ingredient_term_type",
    "rxnorm_ingredient_resolution_stage",
    "rxnorm_ingredient_path",
    "pottr_link_anchor_rxcui",
    "pottr_link_anchor_name",
    "pottr_link_anchor_term_type",
    "pottr_link_anchor_strategy",
    "pottr_link_anchor_path",
    "manual_review_needed",
]

UNIQUE_DRUG_COLUMNS = ["pottr_drug_name"]

CLASS_ASSIGNMENT_COLUMNS = [
    "pottr_class_assignment_key",
    "concept_key",
    "concept_index",
    "canonical_drug_name",
    "drug_raw",
    "direct_class_name",
    "pottr_class_name",
    "class_relation",
    "class_depth_from_direct",
    "class_path",
    "class_in_hierarchy",
]

ATC_COLUMNS = [
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

ALIAS_ATC_COLUMNS = [
    "source_name",
    *ALIAS_COLUMNS,
    *ATC_COLUMNS,
]

CLASS_ATC_COLUMNS = [
    "source_name",
    *ALIAS_COLUMNS,
    "pottr_class_assignment_key",
    "direct_class_name",
    "pottr_class_name",
    "class_relation",
    "class_depth_from_direct",
    "class_path",
    "class_in_hierarchy",
    *ATC_COLUMNS,
]

ESSENTIAL_UNIQUE_ATC_COLUMNS = [
    "pottr_drug_name",
    "pottr_canonical_drug_name",
    "pottr_concept_keys",
    "rxnorm_match_status",
    "rxnorm_match_stage",
    "manual_review_needed",
    "rxnorm_rxcui",
    "rxnorm_canonical_name",
    "rxnorm_term_type",
    "rxnorm_ingredient_rxcui",
    "rxnorm_ingredient_name",
    "rxnorm_ingredient_term_type",
    "ingredient_resolution_stage",
    "pottr_link_anchor_rxcui",
    "pottr_link_anchor_name",
    "pottr_link_anchor_term_type",
    "pottr_link_anchor_strategy",
    "atc_match_statuses",
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

ESSENTIAL_CLASS_ATC_COLUMNS = [
    "pottr_drug_name",
    "pottr_canonical_drug_name",
    "pottr_concept_key",
    "direct_class_name",
    "pottr_class_name",
    "class_relation",
    "class_depth_from_direct",
    "class_path",
    "class_in_hierarchy",
    "rxnorm_match_status",
    "manual_review_needed",
    "rxnorm_ingredient_rxcui",
    "rxnorm_ingredient_name",
    "pottr_link_anchor_rxcui",
    "pottr_link_anchor_name",
    "pottr_link_anchor_strategy",
    "atc_match_statuses",
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
]


@dataclass(frozen=True)
class PottrAtcBuildStats:
    concepts: int
    aliases: int
    unique_aliases: int
    class_assignments: int
    rxnorm_matched_aliases: int
    aliases_with_rxnorm_ingredient: int
    aliases_with_matched_atc: int
    aliases_without_matched_atc: int
    alias_atc_rows: int
    class_atc_rows: int
    obsolete_atc_bridges_applied: int


def load_rxnorm_indexes(rxnorm_rrf_dir: Path) -> tuple[RxnConsoIndex, RxnRelIndex]:
    logger.info("Loading RxNorm indexes from %s", rxnorm_rrf_dir)
    return RxnConsoIndex.from_rrf_dir(rxnorm_rrf_dir), RxnRelIndex.from_rrf_dir(rxnorm_rrf_dir)


def validate_inputs(pottr_raw_dir: Path, rxnorm_rrf_dir: Path, atc_tree_tsv: Path) -> None:
    required_files = [
        pottr_raw_dir / DRUG_DATABASE_FILENAME,
        pottr_raw_dir / DRUG_CLASS_HIERARCHY_FILENAME,
        rxnorm_rrf_dir / "RXNCONSO.RRF",
        rxnorm_rrf_dir / "RXNREL.RRF",
        atc_tree_tsv,
    ]
    missing = [path for path in required_files if not path.exists()]
    if missing:
        missing_text = "\n".join(f"  - {path}" for path in missing)
        raise FileNotFoundError(f"Required input files are missing:\n{missing_text}")


def write_tsv(path: Path, fieldnames: Sequence[str], rows: Iterable[Mapping[str, object]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
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
            writer.writerow(row)
            count += 1
    return count


def ordered_join(values: Iterable[object]) -> str:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = clean_text(value)
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key not in seen:
            seen.add(key)
            out.append(cleaned)
    return SUMMARY_DELIMITER.join(out)


def bool_join(values: Iterable[object]) -> str:
    cleaned_values = [clean_text(value).casefold() for value in values if clean_text(value)]
    if any(value == "true" for value in cleaned_values):
        return "True"
    if any(value == "false" for value in cleaned_values):
        return "False"
    return ""


def concept_indices(pottr: PottrBuildResult) -> dict[str, PottrDrugConcept]:
    return {concept.concept_key: concept for concept in pottr.concepts}


def class_assignments_by_concept(
    class_assignments: Sequence[PottrDrugClassAssignment],
) -> dict[str, list[PottrDrugClassAssignment]]:
    out: dict[str, list[PottrDrugClassAssignment]] = defaultdict(list)
    for assignment in class_assignments:
        out[assignment.concept_key].append(assignment)
    return out


def concept_record(concept: PottrDrugConcept) -> dict[str, object]:
    return asdict(concept)


def alias_record(alias: PottrDrugAlias, concept_by_key: Mapping[str, PottrDrugConcept]) -> dict[str, object]:
    concept = concept_by_key.get(alias.concept_key)
    return {
        "concept_key": alias.concept_key,
        "concept_index": concept.concept_index if concept else "",
        "canonical_drug_name": concept.canonical_drug_name if concept else "",
        "drug_raw": concept.drug_raw if concept else "",
        "alias_position": alias.alias_position,
        "alias": alias.alias,
        "alias_normalized": alias.alias_normalized,
        "rxnorm_match_status": alias.rxnorm_match_status,
        "rxnorm_match_stage": alias.rxnorm_match_stage,
        "rxnorm_rxcui": alias.rxnorm_rxcui,
        "rxnorm_canonical_name": alias.rxnorm_canonical_name,
        "rxnorm_term_type": alias.rxnorm_term_type,
        "rxnorm_ingredient_rxcui": alias.rxnorm_ingredient_rxcui,
        "rxnorm_ingredient_name": alias.rxnorm_ingredient_name,
        "rxnorm_ingredient_term_type": alias.rxnorm_ingredient_term_type,
        "rxnorm_ingredient_resolution_stage": alias.rxnorm_ingredient_resolution_stage,
        "rxnorm_ingredient_path": alias.rxnorm_ingredient_path,
        "pottr_link_anchor_rxcui": alias.pottr_link_anchor_rxcui,
        "pottr_link_anchor_name": alias.pottr_link_anchor_name,
        "pottr_link_anchor_term_type": alias.pottr_link_anchor_term_type,
        "pottr_link_anchor_strategy": alias.pottr_link_anchor_strategy,
        "pottr_link_anchor_path": alias.pottr_link_anchor_path,
        "manual_review_needed": alias.manual_review_needed,
    }


def class_assignment_record(
    assignment: PottrDrugClassAssignment,
    concept_by_key: Mapping[str, PottrDrugConcept],
) -> dict[str, object]:
    concept = concept_by_key.get(assignment.concept_key)
    return {
        "pottr_class_assignment_key": assignment.assignment_key,
        "concept_key": assignment.concept_key,
        "concept_index": concept.concept_index if concept else "",
        "canonical_drug_name": concept.canonical_drug_name if concept else "",
        "drug_raw": concept.drug_raw if concept else "",
        "direct_class_name": assignment.direct_class_name,
        "pottr_class_name": assignment.pottr_class_name,
        "class_relation": assignment.class_relation,
        "class_depth_from_direct": assignment.class_depth_from_direct,
        "class_path": assignment.class_path,
        "class_in_hierarchy": assignment.class_in_hierarchy,
    }


def unique_drug_rows(aliases: Sequence[PottrDrugAlias]) -> list[dict[str, str]]:
    first_seen: OrderedDict[str, str] = OrderedDict()
    for alias in aliases:
        name = clean_text(alias.alias)
        key = alias.alias_normalized or normalize_alias_key(name)
        if name and key and key not in first_seen:
            first_seen[key] = name
    return [{"pottr_drug_name": name} for name in first_seen.values()]


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
    """Resolve ATC rows and retain the original RxNorm ATC atom code."""
    rxcui = atc_clean_text(rxnorm_ingredient_rxcui)

    if not obsolete_atc_code_bridge:
        return [
            (resolution, resolution.atc_code, False)
            for resolution in resolve_ingredient_to_atc(rxcui, atc_by_rxcui, tree)
        ]

    atoms = list(atc_by_rxcui.get(rxcui, []))
    if not atoms:
        return [(blank_atc_resolution("NO_ATC_MATCH"), "", False)]

    out: list[tuple[AtcResolution, str, bool]] = []
    seen_output_codes: set[tuple[str, str]] = set()

    for atom in sorted(atoms, key=lambda value: (value.code, value.name, value.tty)):
        patched_atom, bridge_applied = bridged_atom(atom, obsolete_atc_code_bridge)
        key = (patched_atom.code, "BRIDGED" if bridge_applied else "DIRECT")
        if key in seen_output_codes:
            continue
        seen_output_codes.add(key)
        out.append((resolve_atc_atom(patched_atom, tree), atom.code, bridge_applied))

    return out


def atc_rows_for_alias(
    alias: PottrDrugAlias,
    atc_by_rxcui: Mapping[str, Sequence[RxnConsoAtcAtom]],
    tree: AtcTree,
    obsolete_atc_code_bridge: Mapping[str, str],
) -> list[tuple[AtcResolution, str, bool]]:
    # Mirror the CTGov/Topograph policy: only confident RxNorm MATCHED rows with
    # an ingredient anchor are allowed to drive ATC annotation. Manual-review
    # flags are retained as audit metadata but do not erase the ATC annotation.
    if alias.rxnorm_match_status != "MATCHED":
        return [(blank_atc_resolution("NO_CONFIDENT_RXNORM_MATCH"), "", False)]

    if not clean_text(alias.rxnorm_ingredient_rxcui):
        return [(blank_atc_resolution("NO_RXNORM_INGREDIENT"), "", False)]

    return resolve_ingredient_to_atc_with_audit(
        rxnorm_ingredient_rxcui=alias.rxnorm_ingredient_rxcui,
        atc_by_rxcui=atc_by_rxcui,
        tree=tree,
        obsolete_atc_code_bridge=obsolete_atc_code_bridge,
    )


def atc_record(resolution: AtcResolution, original_code: str, bridge_applied: bool) -> dict[str, object]:
    out = asdict(resolution)
    out["atc_original_code"] = original_code
    out["atc_bridge_applied"] = bridge_applied
    return out


def alias_atc_records(
    pottr: PottrBuildResult,
    atc_by_rxcui: Mapping[str, Sequence[RxnConsoAtcAtom]],
    tree: AtcTree,
    obsolete_atc_code_bridge: Mapping[str, str],
) -> tuple[list[dict[str, object]], int]:
    concept_by_key = concept_indices(pottr)
    rows: list[dict[str, object]] = []
    bridges_applied = 0

    for alias in pottr.aliases:
        base = alias_record(alias, concept_by_key)
        for resolution, original_code, bridge_applied in atc_rows_for_alias(
            alias,
            atc_by_rxcui=atc_by_rxcui,
            tree=tree,
            obsolete_atc_code_bridge=obsolete_atc_code_bridge,
        ):
            if bridge_applied:
                bridges_applied += 1
            rows.append(
                {
                    "source_name": "pottr",
                    **base,
                    **atc_record(resolution, original_code, bridge_applied),
                }
            )

    return rows, bridges_applied


def class_atc_records(
    pottr: PottrBuildResult,
    alias_atc_by_alias_key: Mapping[tuple[str, int], Sequence[Mapping[str, object]]],
) -> list[dict[str, object]]:
    assignments_by_concept = class_assignments_by_concept(pottr.class_assignments)
    rows: list[dict[str, object]] = []

    for alias in pottr.aliases:
        alias_key = (alias.concept_key, alias.alias_position)
        alias_atc_rows = list(alias_atc_by_alias_key.get(alias_key, []))
        assignments = list(assignments_by_concept.get(alias.concept_key, []))

        if not assignments:
            for alias_atc_row in alias_atc_rows:
                rows.append(
                    {
                        **alias_atc_row,
                        "pottr_class_assignment_key": "",
                        "direct_class_name": "",
                        "pottr_class_name": "",
                        "class_relation": "NO_CLASS_ASSIGNMENT",
                        "class_depth_from_direct": "",
                        "class_path": "",
                        "class_in_hierarchy": "",
                    }
                )
            continue

        for assignment in assignments:
            for alias_atc_row in alias_atc_rows:
                rows.append(
                    {
                        **alias_atc_row,
                        "pottr_class_assignment_key": assignment.assignment_key,
                        "direct_class_name": assignment.direct_class_name,
                        "pottr_class_name": assignment.pottr_class_name,
                        "class_relation": assignment.class_relation,
                        "class_depth_from_direct": assignment.class_depth_from_direct,
                        "class_path": assignment.class_path,
                        "class_in_hierarchy": assignment.class_in_hierarchy,
                    }
                )

    return rows


def index_alias_atc_rows(
    rows: Sequence[Mapping[str, object]],
) -> dict[tuple[str, int], list[Mapping[str, object]]]:
    out: dict[tuple[str, int], list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        concept_key = clean_text(row.get("concept_key", ""))
        alias_position = int(row.get("alias_position") or 0)
        out[(concept_key, alias_position)].append(row)
    return out


def aggregate_rows(
    rows: Sequence[Mapping[str, object]],
    group_keys: Sequence[str],
    scalar_columns: Sequence[str],
    aggregate_columns: Sequence[str],
) -> list[dict[str, object]]:
    grouped: OrderedDict[tuple[str, ...], list[Mapping[str, object]]] = OrderedDict()
    for row in rows:
        key = tuple(clean_text(row.get(column, "")) for column in group_keys)
        grouped.setdefault(key, []).append(row)

    out: list[dict[str, object]] = []
    for key, group_rows in grouped.items():
        first = group_rows[0]
        record = {column: first.get(column, "") for column in scalar_columns}
        for column in aggregate_columns:
            record[column] = ordered_join(row.get(column, "") for row in group_rows)
        out.append(record)
    return out


def essential_unique_atc_rows(alias_atc_rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    grouped: OrderedDict[str, list[Mapping[str, object]]] = OrderedDict()
    for row in alias_atc_rows:
        alias = clean_text(row.get("alias", ""))
        key = clean_text(row.get("alias_normalized", "")) or alias.casefold()
        if key:
            grouped.setdefault(key, []).append(row)

    out: list[dict[str, object]] = []
    for group_rows in grouped.values():
        first = group_rows[0]
        out.append(
            {
                "pottr_drug_name": first.get("alias", ""),
                "pottr_canonical_drug_name": ordered_join(row.get("canonical_drug_name", "") for row in group_rows),
                "pottr_concept_keys": ordered_join(row.get("concept_key", "") for row in group_rows),
                "rxnorm_match_status": first.get("rxnorm_match_status", ""),
                "rxnorm_match_stage": first.get("rxnorm_match_stage", ""),
                "manual_review_needed": bool_join(row.get("manual_review_needed", "") for row in group_rows),
                "rxnorm_rxcui": first.get("rxnorm_rxcui", ""),
                "rxnorm_canonical_name": first.get("rxnorm_canonical_name", ""),
                "rxnorm_term_type": first.get("rxnorm_term_type", ""),
                "rxnorm_ingredient_rxcui": first.get("rxnorm_ingredient_rxcui", ""),
                "rxnorm_ingredient_name": first.get("rxnorm_ingredient_name", ""),
                "rxnorm_ingredient_term_type": first.get("rxnorm_ingredient_term_type", ""),
                "ingredient_resolution_stage": first.get("rxnorm_ingredient_resolution_stage", ""),
                "pottr_link_anchor_rxcui": first.get("pottr_link_anchor_rxcui", ""),
                "pottr_link_anchor_name": first.get("pottr_link_anchor_name", ""),
                "pottr_link_anchor_term_type": first.get("pottr_link_anchor_term_type", ""),
                "pottr_link_anchor_strategy": first.get("pottr_link_anchor_strategy", ""),
                "atc_match_statuses": ordered_join(row.get("atc_match_status", "") for row in group_rows),
                "atc_codes": ordered_join(row.get("atc_code", "") for row in group_rows),
                "atc_names": ordered_join(row.get("atc_name", "") for row in group_rows),
                "atc_l1_codes": ordered_join(row.get("atc_l1_code", "") for row in group_rows),
                "atc_l1_names": ordered_join(row.get("atc_l1_name", "") for row in group_rows),
                "atc_l2_codes": ordered_join(row.get("atc_l2_code", "") for row in group_rows),
                "atc_l2_names": ordered_join(row.get("atc_l2_name", "") for row in group_rows),
                "atc_l3_codes": ordered_join(row.get("atc_l3_code", "") for row in group_rows),
                "atc_l3_names": ordered_join(row.get("atc_l3_name", "") for row in group_rows),
                "atc_l4_codes": ordered_join(row.get("atc_l4_code", "") for row in group_rows),
                "atc_l4_names": ordered_join(row.get("atc_l4_name", "") for row in group_rows),
                "atc_l5_codes": ordered_join(row.get("atc_l5_code", "") for row in group_rows),
                "atc_l5_names": ordered_join(row.get("atc_l5_name", "") for row in group_rows),
                "atc_bridge_applied": bool_join(row.get("atc_bridge_applied", "") for row in group_rows),
                "atc_original_codes": ordered_join(row.get("atc_original_code", "") for row in group_rows),
            }
        )
    return out


def essential_class_atc_rows(class_atc_rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    grouped: OrderedDict[tuple[str, ...], list[Mapping[str, object]]] = OrderedDict()
    group_keys = ["alias_normalized", "concept_key", "pottr_class_assignment_key"]
    for row in class_atc_rows:
        key = tuple(clean_text(row.get(column, "")) for column in group_keys)
        grouped.setdefault(key, []).append(row)

    out: list[dict[str, object]] = []
    for group_rows in grouped.values():
        first = group_rows[0]
        out.append(
            {
                "pottr_drug_name": first.get("alias", ""),
                "pottr_canonical_drug_name": first.get("canonical_drug_name", ""),
                "pottr_concept_key": first.get("concept_key", ""),
                "direct_class_name": first.get("direct_class_name", ""),
                "pottr_class_name": first.get("pottr_class_name", ""),
                "class_relation": first.get("class_relation", ""),
                "class_depth_from_direct": first.get("class_depth_from_direct", ""),
                "class_path": first.get("class_path", ""),
                "class_in_hierarchy": first.get("class_in_hierarchy", ""),
                "rxnorm_match_status": first.get("rxnorm_match_status", ""),
                "manual_review_needed": bool_join(row.get("manual_review_needed", "") for row in group_rows),
                "rxnorm_ingredient_rxcui": first.get("rxnorm_ingredient_rxcui", ""),
                "rxnorm_ingredient_name": first.get("rxnorm_ingredient_name", ""),
                "pottr_link_anchor_rxcui": first.get("pottr_link_anchor_rxcui", ""),
                "pottr_link_anchor_name": first.get("pottr_link_anchor_name", ""),
                "pottr_link_anchor_strategy": first.get("pottr_link_anchor_strategy", ""),
                "atc_match_statuses": ordered_join(row.get("atc_match_status", "") for row in group_rows),
                "atc_codes": ordered_join(row.get("atc_code", "") for row in group_rows),
                "atc_names": ordered_join(row.get("atc_name", "") for row in group_rows),
                "atc_l1_codes": ordered_join(row.get("atc_l1_code", "") for row in group_rows),
                "atc_l1_names": ordered_join(row.get("atc_l1_name", "") for row in group_rows),
                "atc_l2_codes": ordered_join(row.get("atc_l2_code", "") for row in group_rows),
                "atc_l2_names": ordered_join(row.get("atc_l2_name", "") for row in group_rows),
                "atc_l3_codes": ordered_join(row.get("atc_l3_code", "") for row in group_rows),
                "atc_l3_names": ordered_join(row.get("atc_l3_name", "") for row in group_rows),
                "atc_l4_codes": ordered_join(row.get("atc_l4_code", "") for row in group_rows),
                "atc_l4_names": ordered_join(row.get("atc_l4_name", "") for row in group_rows),
                "atc_l5_codes": ordered_join(row.get("atc_l5_code", "") for row in group_rows),
                "atc_l5_names": ordered_join(row.get("atc_l5_name", "") for row in group_rows),
            }
        )
    return out


def write_summary(path: Path, stats: PottrAtcBuildStats) -> None:
    rows = [{"metric": key, "value": value} for key, value in asdict(stats).items()]
    write_tsv(path, ["metric", "value"], rows)


def write_manifest(manifest_dir: Path, processed_dir: Path, exported_files: Sequence[Path]) -> None:
    rows = []
    for path in exported_files:
        rows.append(
            {
                "file": path.name,
                "path": str(path),
                "source_processed_dir": str(processed_dir),
            }
        )
    write_tsv(manifest_dir / EXPORT_MANIFEST_FILENAME, ["file", "path", "source_processed_dir"], rows)


def build_pottr_drug_class_atc_outputs(
    *,
    pottr_raw_dir: Path,
    rxnorm_rrf_dir: Path,
    atc_tree_tsv: Path,
    processed_dir: Path,
    exports_dir: Path | None = None,
    summary_dir: Path | None = None,
    obsolete_atc_code_bridge: Mapping[str, str] | None = None,
) -> PottrAtcBuildStats:
    validate_inputs(pottr_raw_dir, rxnorm_rrf_dir, atc_tree_tsv)
    obsolete_atc_code_bridge = dict(obsolete_atc_code_bridge or {})

    conso_index, rel_index = load_rxnorm_indexes(rxnorm_rrf_dir)
    pottr = build_pottr_records(pottr_raw_dir, conso_index, rel_index)
    concept_by_key = concept_indices(pottr)

    logger.info("Writing POTTR parsed concept/alias/class files to %s", processed_dir)
    write_tsv(processed_dir / CONCEPTS_FILENAME, CONCEPT_COLUMNS, (concept_record(concept) for concept in pottr.concepts))
    write_tsv(
        processed_dir / ALIASES_FILENAME,
        ALIAS_COLUMNS,
        (alias_record(alias, concept_by_key) for alias in pottr.aliases),
    )
    unique_rows = unique_drug_rows(pottr.aliases)
    write_tsv(processed_dir / UNIQUE_DRUGS_FILENAME, UNIQUE_DRUG_COLUMNS, unique_rows)
    write_tsv(
        processed_dir / CLASS_ASSIGNMENTS_FILENAME,
        CLASS_ASSIGNMENT_COLUMNS,
        (class_assignment_record(assignment, concept_by_key) for assignment in pottr.class_assignments),
    )

    target_ingredient_rxcuis = {
        alias.rxnorm_ingredient_rxcui
        for alias in pottr.aliases
        if alias.rxnorm_match_status == "MATCHED" and clean_text(alias.rxnorm_ingredient_rxcui)
    }
    logger.info("Resolving %d distinct POTTR RxNorm ingredient anchors to ATC", len(target_ingredient_rxcuis))
    tree = AtcTree.from_tsv(atc_tree_tsv)
    atc_by_rxcui = load_rxnconso_atc_index(
        rxnconso_path=rxnorm_rrf_dir / RXNCONSO_FILENAME,
        target_rxcuis=target_ingredient_rxcuis,
    )

    alias_atc, bridges_applied = alias_atc_records(
        pottr,
        atc_by_rxcui=atc_by_rxcui,
        tree=tree,
        obsolete_atc_code_bridge=obsolete_atc_code_bridge,
    )
    write_tsv(processed_dir / ALIAS_ATC_FILENAME, ALIAS_ATC_COLUMNS, alias_atc)

    alias_atc_by_alias_key = index_alias_atc_rows(alias_atc)
    class_atc = class_atc_records(pottr, alias_atc_by_alias_key)
    write_tsv(processed_dir / CLASS_ATC_FILENAME, CLASS_ATC_COLUMNS, class_atc)

    aliases_with_matched_atc = {
        (clean_text(row.get("concept_key", "")), int(row.get("alias_position") or 0))
        for row in alias_atc
        if row.get("atc_match_status") == "MATCHED_ATC"
    }

    stats = PottrAtcBuildStats(
        concepts=len(pottr.concepts),
        aliases=len(pottr.aliases),
        unique_aliases=len(unique_rows),
        class_assignments=len(pottr.class_assignments),
        rxnorm_matched_aliases=sum(1 for alias in pottr.aliases if alias.rxnorm_match_status == "MATCHED"),
        aliases_with_rxnorm_ingredient=sum(
            1
            for alias in pottr.aliases
            if alias.rxnorm_match_status == "MATCHED" and clean_text(alias.rxnorm_ingredient_rxcui)
        ),
        aliases_with_matched_atc=len(aliases_with_matched_atc),
        aliases_without_matched_atc=len(pottr.aliases) - len(aliases_with_matched_atc),
        alias_atc_rows=len(alias_atc),
        class_atc_rows=len(class_atc),
        obsolete_atc_bridges_applied=bridges_applied,
    )
    summary_output_dir = summary_dir if summary_dir is not None else processed_dir
    summary_path = summary_output_dir / SUMMARY_FILENAME
    logger.info("Writing POTTR summary to %s", summary_path)
    write_summary(summary_path, stats)

    if exports_dir is not None:
        logger.info("Writing compact POTTR exports to %s", exports_dir)
        unique_export = exports_dir / ESSENTIAL_UNIQUE_ATC_FILENAME
        class_export = exports_dir / ESSENTIAL_CLASS_ATC_FILENAME
        write_tsv(unique_export, ESSENTIAL_UNIQUE_ATC_COLUMNS, essential_unique_atc_rows(alias_atc))
        write_tsv(class_export, ESSENTIAL_CLASS_ATC_COLUMNS, essential_class_atc_rows(class_atc))
        write_manifest(summary_output_dir, processed_dir, [unique_export, class_export, summary_path])

    return stats


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build standalone POTTR drug-class and RxNorm->ATC TSV outputs."
    )
    parser.add_argument(
        "--pottr-raw-dir",
        "--pottr_raw_dir",
        dest="pottr_raw_dir",
        type=Path,
        default=DEFAULT_POTTR_RAW_DIR,
        help=f"Directory containing drug_database.txt and drug_class_hierarchy.txt. Default: {DEFAULT_POTTR_RAW_DIR}",
    )
    parser.add_argument(
        "--rxnorm-rrf-dir",
        "--rxnorm_rrf_dir",
        dest="rxnorm_rrf_dir",
        type=Path,
        default=DEFAULT_RXNORM_RRF_DIR,
        help=f"Directory containing RXNCONSO.RRF and RXNREL.RRF. Default: {DEFAULT_RXNORM_RRF_DIR}",
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
        "--processed-dir",
        "--processed_dir",
        dest="processed_dir",
        type=Path,
        default=DEFAULT_PROCESSED_DIR,
        help=f"Directory for detailed processed TSVs. Default: {DEFAULT_PROCESSED_DIR}",
    )
    parser.add_argument(
        "--exports-dir",
        "--exports_dir",
        dest="exports_dir",
        type=Path,
        default=DEFAULT_EXPORTS_DIR,
        help=f"Directory for compact export TSVs. Default: {DEFAULT_EXPORTS_DIR}",
    )
    parser.add_argument(
        "--summary-dir",
        "--summary_dir",
        dest="summary_dir",
        type=Path,
        default=DEFAULT_SUMMARY_DIR,
        help=f"Directory for summary and manifest TSVs. Default: {DEFAULT_SUMMARY_DIR}",
    )
    parser.add_argument(
        "--no-exports",
        "--no_exports",
        dest="no_exports",
        action="store_true",
        help="Only write detailed processed files; skip compact exports.",
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

    obsolete_bridge = {} if args.no_2026_obsolete_atc_bridge else DEFAULT_OBSOLETE_ATC_CODE_BRIDGE
    exports_dir = None if args.no_exports else args.exports_dir

    try:
        stats = build_pottr_drug_class_atc_outputs(
            pottr_raw_dir=args.pottr_raw_dir,
            rxnorm_rrf_dir=args.rxnorm_rrf_dir,
            atc_tree_tsv=args.atc_tree_tsv,
            processed_dir=args.processed_dir,
            exports_dir=exports_dir,
            summary_dir=args.summary_dir,
            obsolete_atc_code_bridge=obsolete_bridge,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"POTTR raw dir: {args.pottr_raw_dir}", file=sys.stderr)
    print(f"Processed dir: {args.processed_dir}", file=sys.stderr)
    if exports_dir is not None:
        print(f"Exports dir: {exports_dir}", file=sys.stderr)
    print(f"Summary dir: {args.summary_dir}", file=sys.stderr)
    for metric, value in asdict(stats).items():
        print(f"{metric}: {value}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
