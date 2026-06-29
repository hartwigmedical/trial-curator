from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional, Sequence

from aus_trial_universe.eligibility_path.qa.tabular_output_diff import run_diff_cli

REGISTRY_FILES: Dict[str, Sequence[str]] = {
    "ctgov": (
        "01_conditions_mapping.tsv",
        "02_primary_vs_conditions.tsv",
        "03_row_level_cancer_type.tsv",
        "04a_cancer_type_trial_level.tsv",
        "04b_cancer_type_cohort_level.tsv",
    ),
    "anzctr": (
        "01_health_condition_mapping.tsv",
        "02_primary_vs_health_condition.tsv",
        "03_row_level_cancer_type.tsv",
        "04a_cancer_type_trial_level.tsv",
        "04b_cancer_type_cohort_level.tsv",
    ),
}

KEY_COLUMNS_BY_FILE: Dict[str, Sequence[str]] = {
    "01_conditions_mapping.tsv": ("trial_id",),
    "01_health_condition_mapping.tsv": ("trial_id",),
    "02_primary_vs_conditions.tsv": (
        "nct_id",
        "primary_tumor_type",
        "primary_tumor_location",
        "conditions_original",
        "rule_text",
        "ancestor_chain",
        "siblings_summary",
    ),
    "02_primary_vs_health_condition.tsv": (
        "trial_id",
        "primary_tumor_type",
        "primary_tumor_location",
        "conditions_original",
        "rule_text",
        "ancestor_chain",
        "siblings_summary",
    ),
    "03_row_level_cancer_type.tsv": (
        "trial_id",
        "nct_id",
        "primary_tumor_type",
        "primary_tumor_location",
        "conditions_original",
        "rule_text",
        "ancestor_chain",
        "siblings_summary",
    ),
    "04a_cancer_type_trial_level.tsv": ("trial_id", "nct_id"),
    "04b_cancer_type_cohort_level.tsv": ("trial_id", "nct_id", "cohort"),
}


def default_processed_dir(registry: str) -> Path:
    return Path("data/eligibility_path/exports/intermediates") / registry / "cancer_type"


def main(argv: Optional[list[str]] = None) -> int:
    return run_diff_cli(
        description="Snapshot and diff cancer-type eligibility outputs.",
        resolve_files=lambda registry: REGISTRY_FILES[registry],
        key_columns_by_file=KEY_COLUMNS_BY_FILE,
        default_processed_dir=default_processed_dir,
        registry_choices=tuple(REGISTRY_FILES),
        argv=argv,
    )


if __name__ == "__main__":
    raise SystemExit(main())
