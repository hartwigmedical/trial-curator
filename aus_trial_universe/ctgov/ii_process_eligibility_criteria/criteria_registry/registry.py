from __future__ import annotations

from typing import Dict, Set

SUPPORTED_CRITERIA: Set[str] = {
    "primary_tumour",
    "gene_alteration",
}

CRITERION_TO_CLASSNAME: Dict[str, str] = {
    "primary_tumour": "PrimaryTumorCriterion",
    "gene_alteration": "GeneAlterationCriterion",
}

CURATED_FIELD_BY_CLASSNAME: Dict[str, str] = {
    "PrimaryTumorCriterion": "Oncotree_curation",
    "GeneAlterationCriterion": "gene_alteration_curation",
}


def get_classname_for_criterion(criterion: str) -> str:
    try:
        return CRITERION_TO_CLASSNAME[criterion]
    except KeyError as exc:
        raise ValueError(f"Unsupported criterion: {criterion}") from exc


def get_curated_field_for_criterion(criterion: str) -> str:
    classname = get_classname_for_criterion(criterion)
    try:
        return CURATED_FIELD_BY_CLASSNAME[classname]
    except KeyError as exc:
        raise ValueError(
            f"No curated field registered for criterion class: {classname}"
        ) from exc
