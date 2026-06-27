from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Dict, Optional, Sequence

from aus_trial_universe.eligibility_path.qa.tabular_output_diff import (
    create_snapshot_and_diffs,
    default_snapshot_dir,
)

LOGGER = logging.getLogger(__name__)

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
    parser = argparse.ArgumentParser(
        description="Snapshot and diff gene-alteration eligibility outputs."
    )
    parser.add_argument("--registry", choices=["anzctr", "ctgov"], default="ctgov")
    parser.add_argument("--processed_dir", type=Path, default=None)
    parser.add_argument("--baseline_dir", type=Path, default=None)
    parser.add_argument("--snapshot_dir", type=Path, default=None)
    parser.add_argument("--snapshot_label", default=None)
    parser.add_argument(
        "--log_level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    processed_dir = args.processed_dir or default_processed_dir(args.registry)
    snapshot_dir = args.snapshot_dir or default_snapshot_dir(
        processed_dir,
        args.snapshot_label,
    )

    summary_df = create_snapshot_and_diffs(
        processed_dir=processed_dir,
        baseline_dir=args.baseline_dir,
        snapshot_dir=snapshot_dir,
        files=OUTPUT_FILES,
        key_columns_by_file=KEY_COLUMNS_BY_FILE,
        fallback_key_columns=(("trial_id",), ("nct_id",), ("ACTRN",), ("trialId",)),
    )

    LOGGER.info("QA diff complete.")
    LOGGER.info("snapshot_dir: %s", snapshot_dir)
    LOGGER.info("\n%s", summary_df.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
