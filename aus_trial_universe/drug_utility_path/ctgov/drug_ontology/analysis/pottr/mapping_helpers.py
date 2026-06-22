from __future__ import annotations

"""Small helpers needed to build the CTGov -> POTTR mapping output."""

import csv
import logging
import re
from collections import OrderedDict, defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Mapping, Sequence

from aus_trial_universe.drug_utility_path.ctgov.drug_ontology.identity.rxnorm.matcher import RxnConsoIndex, RxnRelIndex
from aus_trial_universe.drug_utility_path.ctgov.drug_ontology.shared.keys import stable_key
from aus_trial_universe.drug_utility_path.ctgov.drug_ontology.shared.text import (
    clean_text,
    normalize_key,
    ordered_unique,
)
from aus_trial_universe.drug_utility_path.ctgov.drug_ontology.sources.atc.ingredient_to_atc import (
    AtcResolution,
    AtcTree,
    RxnConsoAtcAtom,
    clean_text as atc_clean_text,
    resolve_atc_atom,
    resolve_ingredient_to_atc,
)
from aus_trial_universe.drug_utility_path.ctgov.drug_ontology.sources.pottr.drug_classes import (
    DRUG_CLASS_HIERARCHY_FILENAME,
    DRUG_DATABASE_FILENAME,
    PottrBuildResult,
    PottrDrugAlias,
    PottrDrugConcept,
)

logger = logging.getLogger(__name__)

DIRECT_CLASS_SPLIT_RE = re.compile(r"\s*;\s*")

# WHO ATC 2026 reclassifications observed when moving from the older ATC tree.
# RxNorm ATC atoms can lag WHO ATC; these bridges prevent valid drugs from
# becoming ATC_CODE_NOT_IN_TREE solely because the ATC hierarchy moved.
DEFAULT_OBSOLETE_ATC_CODE_BRIDGE: dict[str, str] = {
    "L01EX17": "L01EP01",  # capmatinib
    "L01EX21": "L01EP02",  # tepotinib
    "L04AA58": "L04AL01",  # efgartigimod alfa
    "L04AG16": "L04AL02",  # rozanolixizumab
}


class PottrConceptRecord(dict):
    """Dictionary subclass used only for clearer type intent."""


def load_rxnorm_indexes(rxnorm_rrf_dir: Path) -> tuple[RxnConsoIndex, RxnRelIndex]:
    logger.info("Loading RxNorm indexes from %s", rxnorm_rrf_dir)
    return RxnConsoIndex.from_rrf_dir(rxnorm_rrf_dir), RxnRelIndex.from_rrf_dir(rxnorm_rrf_dir)


def split_aliases(drug_raw: object) -> list[str]:
    return ordered_unique(part for part in clean_text(drug_raw).split("|") if clean_text(part))


def split_direct_classes(class_raw: object) -> list[str]:
    # POTTR class names commonly contain commas. Only semicolon separates
    # multiple direct class assignments in drug_database.txt.
    return ordered_unique(part for part in DIRECT_CLASS_SPLIT_RE.split(clean_text(class_raw)) if clean_text(part))


def load_pottr_concepts(pottr_raw_dir: Path) -> list[PottrConceptRecord]:
    path = pottr_raw_dir / DRUG_DATABASE_FILENAME
    if not path.exists():
        raise FileNotFoundError(f"POTTR drug database not found: {path}")

    concepts: list[PottrConceptRecord] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames is None:
            raise ValueError(f"POTTR drug database has no header row: {path}")

        required = {"drug", "drug_class"}
        missing = required - set(reader.fieldnames)
        if missing:
            raise ValueError(f"POTTR drug database missing columns {sorted(missing)}: {path}")

        for row_index, row in enumerate(reader, start=1):
            drug_raw = clean_text(row.get("drug", ""))
            class_raw = clean_text(row.get("drug_class", ""))
            aliases = split_aliases(drug_raw)
            canonical = aliases[0] if aliases else drug_raw
            concept_key = stable_key(drug_raw, class_raw, prefix="pottr_concept_")
            concepts.append(
                PottrConceptRecord(
                    row_index=row_index,
                    concept_key=concept_key,
                    canonical_drug_name=canonical,
                    canonical_key=normalize_key(canonical),
                    aliases=aliases,
                    direct_classes=split_direct_classes(class_raw),
                    drug_raw=drug_raw,
                    class_raw=class_raw,
                )
            )

    return concepts


def group_concepts_by_canonical(concepts: Sequence[PottrConceptRecord]) -> OrderedDict[str, list[PottrConceptRecord]]:
    grouped: OrderedDict[str, list[PottrConceptRecord]] = OrderedDict()
    for concept in concepts:
        key = clean_text(concept.get("canonical_key", ""))
        if key:
            grouped.setdefault(key, []).append(concept)
    return grouped


def load_hierarchy_paths_by_class(pottr_raw_dir: Path) -> dict[str, list[str]]:
    path = pottr_raw_dir / DRUG_CLASS_HIERARCHY_FILENAME
    if not path.exists():
        raise FileNotFoundError(f"POTTR class hierarchy file not found: {path}")

    paths_by_class: dict[str, list[str]] = defaultdict(list)
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            parts = [clean_text(part) for part in line.split("\t")]
            parts = [part for part in parts if part]
            if not parts:
                continue

            path_text = " > ".join(parts)
            for class_name in parts:
                paths_by_class[class_name].append(path_text)

    return {key: ordered_unique(paths) for key, paths in paths_by_class.items()}


def hierarchy_paths_for_direct_classes(
    direct_classes: Sequence[str],
    hierarchy_paths_by_class: Mapping[str, Sequence[str]],
) -> list[str]:
    paths: list[str] = []
    for direct_class in direct_classes:
        paths.extend(hierarchy_paths_by_class.get(direct_class, []))
    return ordered_unique(paths)


def concept_indices(pottr: PottrBuildResult) -> dict[str, PottrDrugConcept]:
    return {concept.concept_key: concept for concept in pottr.concepts}


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
