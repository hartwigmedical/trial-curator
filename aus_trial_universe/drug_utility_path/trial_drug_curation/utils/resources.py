from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from aus_trial_universe.drug_utility_path.ctgov.drug_ontology.shared.paths import (
    VersionSortKey,
    iter_version_dirs,
    parse_version_sort_key,
)
from aus_trial_universe.drug_utility_path.trial_drug_curation.utils.constants import (
    ANZCTR_INPUT_FILENAME,
    ANZCTR_TRIALS_DIR,
    CTGOV_INPUT_FILENAME,
    CTGOV_TRIALS_DIR,
    DEFAULT_ONCOTREE_YAML,
)
from aus_trial_universe.drug_utility_path.trial_drug_curation.enrichment.pottr import (
    POTTR_DRUG_CLASS_HIERARCHY_URL,
    POTTR_DRUG_DATABASE_URL,
)


def latest_input_file(version_root: str | Path, filename: str) -> Path:
    candidates: list[tuple[VersionSortKey, Path]] = []

    for version_dir in iter_version_dirs(Path(version_root)):
        sort_key = parse_version_sort_key(version_dir.name)
        input_file = version_dir / filename
        if sort_key is not None and input_file.is_file():
            candidates.append((sort_key, input_file))

    if not candidates:
        raise FileNotFoundError(
            f"No {filename} files found under sortable version_* directories "
            f"in {version_root}."
        )

    return max(candidates, key=lambda item: item[0])[1]


def latest_ctgov_input_file(trials_dir: str | Path = CTGOV_TRIALS_DIR) -> Path:
    return latest_input_file(trials_dir, CTGOV_INPUT_FILENAME)


def latest_anzctr_input_file(trials_dir: str | Path = ANZCTR_TRIALS_DIR) -> Path:
    return latest_input_file(trials_dir, ANZCTR_INPUT_FILENAME)


def resolve_resource_paths(
    *,
    registries: Sequence[str],
    ctgov_input: str | Path | None,
    anzctr_input: str | Path | None,
    oncotree_yaml: str = DEFAULT_ONCOTREE_YAML,
    pottr_drug_class_hierarchy_url: str = POTTR_DRUG_CLASS_HIERARCHY_URL,
    pottr_drug_database_url: str = POTTR_DRUG_DATABASE_URL,
) -> dict[str, str]:
    resources: dict[str, str] = {
        "oncotree_yaml": str(oncotree_yaml),
        "pottr_drug_class_hierarchy_url": pottr_drug_class_hierarchy_url,
        "pottr_drug_database_url": pottr_drug_database_url,
    }
    registry_set = set(registries)
    if "ctgov" in registry_set:
        resolved_input = (
            Path(ctgov_input) if ctgov_input is not None else latest_ctgov_input_file()
        )
        resources["ctgov_input"] = str(resolved_input)
    if "anzctr" in registry_set:
        resolved_input = (
            Path(anzctr_input) if anzctr_input is not None else latest_anzctr_input_file()
        )
        resources["anzctr_input"] = str(resolved_input)
    return resources


def build_resource_context(resources: dict[str, str]) -> list[str]:
    lines = ["Resource context:"]
    if "ctgov_input" in resources:
        lines.append(f"- CTGOV input file: {resources['ctgov_input']}")
    if "anzctr_input" in resources:
        lines.append(f"- ANZCTR input workbook: {resources['anzctr_input']}")
    lines.extend(
        (
            f"- OncoTree YAML file: {resources['oncotree_yaml']}",
            f"- POTTR drug class hierarchy URL: {resources['pottr_drug_class_hierarchy_url']}",
            f"- POTTR drug database URL: {resources['pottr_drug_database_url']}",
        )
    )
    return lines
