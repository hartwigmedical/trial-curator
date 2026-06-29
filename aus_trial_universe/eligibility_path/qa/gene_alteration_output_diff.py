from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional, Sequence

from aus_trial_universe.eligibility_path.qa.tabular_output_diff import run_diff_cli

OUTPUT_FILES: Sequence[str] = (
    "01_gene_alteration_mapping_resource.tsv",
    "02a_gene_alteration_manual_filter_trial_level.tsv",
    "02b_gene_alteration_manual_filter_cohort_level.tsv",
    "03_gene_alteration_mapped_criteria.tsv",
    "04a_gene_alteration_trial_level.tsv",
    "04b_gene_alteration_cohort_level.tsv",
    "diagnostics/01a_gene_alteration_conflicts_trial_level.tsv",
    "diagnostics/01b_gene_alteration_conflicts_cohort_level.tsv",
)

KEY_COLUMNS_BY_FILE: Dict[str, Sequence[str]] = {
    "01_gene_alteration_mapping_resource.tsv": (
        "Gene_lookup",
        "Alteration_lookup",
        "Variant_lookup",
    ),
    "02a_gene_alteration_manual_filter_trial_level.tsv": (
        "TrialId",
        "source_file",
        "rule_index",
        "criterion_index",
        "criterion_path",
    ),
    "02b_gene_alteration_manual_filter_cohort_level.tsv": (
        "TrialId",
        "source_file",
        "rule_index",
        "criterion_index",
        "criterion_path",
    ),
    "03_gene_alteration_mapped_criteria.tsv": (
        "trial_id",
        "nct_id",
        "source_file",
        "rule_index",
        "criterion_index",
        "criterion_path",
    ),
    "04a_gene_alteration_trial_level.tsv": ("trial_id", "nct_id"),
    "04b_gene_alteration_cohort_level.tsv": ("trial_id", "nct_id", "cohort"),
    "diagnostics/01a_gene_alteration_conflicts_trial_level.tsv": (
        "trial_id",
        "nct_id",
        "positive_term",
        "negative_term",
    ),
    "diagnostics/01b_gene_alteration_conflicts_cohort_level.tsv": (
        "trial_id",
        "nct_id",
        "cohort",
        "positive_term",
        "negative_term",
    ),
}


def default_processed_dir(registry: str) -> Path:
    return Path("data/eligibility_path/exports/intermediates") / registry / "gene_alteration"


def main(argv: Optional[list[str]] = None) -> int:
    return run_diff_cli(
        description="Snapshot and diff gene-alteration eligibility outputs.",
        resolve_files=lambda _registry: OUTPUT_FILES,
        key_columns_by_file=KEY_COLUMNS_BY_FILE,
        default_processed_dir=default_processed_dir,
        argv=argv,
    )


if __name__ == "__main__":
    raise SystemExit(main())
