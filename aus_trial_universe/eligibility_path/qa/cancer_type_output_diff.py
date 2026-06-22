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
    parser = argparse.ArgumentParser(
        description="Snapshot and diff cancer-type eligibility outputs."
    )
    parser.add_argument("--registry", choices=sorted(REGISTRY_FILES), default="ctgov")
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
        files=REGISTRY_FILES[args.registry],
        key_columns_by_file=KEY_COLUMNS_BY_FILE,
        fallback_key_columns=(("trial_id",), ("nct_id",), ("ACTRN",), ("trialId",)),
    )

    LOGGER.info("QA diff complete.")
    LOGGER.info("snapshot_dir: %s", snapshot_dir)
    LOGGER.info("\n%s", summary_df.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
