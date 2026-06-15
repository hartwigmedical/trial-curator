from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from aus_trial_universe.trial_drug_curation.utils.constants import (
    REGISTRY_LABELS,
    SUPPORTED_REGISTRIES,
)
from aus_trial_universe.trial_drug_curation.enrichment.oncotree import (
    ONCOTREE_MAPPING_PROMPT,
)
from aus_trial_universe.trial_drug_curation.enrichment.pbs import PBS_INDICATION_PROMPT
from aus_trial_universe.trial_drug_curation.enrichment.pottr import POTTR_CLASS_PROMPT
from aus_trial_universe.trial_drug_curation.enrichment.tga import TGA_STATUS_PROMPT
from aus_trial_universe.trial_drug_curation.prompts.anzctr_prompt import (
    ANZCTR_DRUG_CURATION_PROMPT,
    ANZCTR_SOURCE_PROMPT,
)
from aus_trial_universe.trial_drug_curation.prompts.base_prompt import (
    CORE_EXTRACTION_PROMPT,
)
from aus_trial_universe.trial_drug_curation.prompts.ctgov_prompt import (
    CTGOV_DRUG_CURATION_PROMPT,
    CTGOV_SOURCE_PROMPT,
)
from aus_trial_universe.trial_drug_curation.utils.resources import build_resource_context


def prompt_for_registries(registries: Sequence[str]) -> str:
    registry_set = set(registries)
    if registry_set == {"ctgov"}:
        return CTGOV_DRUG_CURATION_PROMPT
    if registry_set == {"anzctr"}:
        return ANZCTR_DRUG_CURATION_PROMPT
    if registry_set == {"ctgov", "anzctr"}:
        return "\n\n".join(
            (
                CORE_EXTRACTION_PROMPT,
                CTGOV_SOURCE_PROMPT,
                ANZCTR_SOURCE_PROMPT,
                ONCOTREE_MAPPING_PROMPT,
                POTTR_CLASS_PROMPT,
                TGA_STATUS_PROMPT,
                PBS_INDICATION_PROMPT,
            )
        )
    raise ValueError(f"Unsupported registry set: {sorted(registry_set)!r}")


def build_prompt(
    *,
    resources: dict[str, str],
    records_by_registry: dict[str, list[dict[str, Any]]],
) -> str:
    registries = [
        registry
        for registry in SUPPORTED_REGISTRIES
        if records_by_registry.get(registry)
    ]
    prompt = prompt_for_registries(registries)
    trial_id_lines = build_trial_id_lines(records_by_registry, registries)
    resource_context = "\n".join(build_resource_context(resources))
    trial_data_context = build_trial_data_context(records_by_registry)
    return (
        f"{prompt}\n\n"
        f"{resource_context}\n\n"
        f"Trial IDs:\n{trial_id_lines}\n\n"
        f"{trial_data_context}"
    )


def build_trial_id_lines(
    records_by_registry: dict[str, list[dict[str, Any]]],
    registries: Sequence[str],
) -> str:
    sections: list[str] = []
    for registry in registries:
        records = records_by_registry.get(registry, [])
        if not records:
            continue
        sections.append(f"{REGISTRY_LABELS[registry]}:")
        sections.extend(f"- {record['trialId']}" for record in records)
    return "\n".join(sections)


def build_trial_data_context(records_by_registry: dict[str, list[dict[str, Any]]]) -> str:
    sections = ["Local trial records:"]
    if records_by_registry.get("ctgov"):
        sections.append(
            "CTGOV records:\n"
            + json.dumps(records_by_registry["ctgov"], ensure_ascii=False, indent=2)
        )
    if records_by_registry.get("anzctr"):
        sections.append(
            "ANZCTR records:\n"
            + json.dumps(records_by_registry["anzctr"], ensure_ascii=False, indent=2)
        )
    return "\n\n".join(sections)
