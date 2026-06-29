from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional, Sequence

from aus_trial_universe.eligibility_path.qa.tabular_output_diff import run_diff_cli

OUTPUT_FILES: Sequence[str] = (
    "01_molecular_signature_mapping_resource.tsv",
    "02_molecular_signature_mapped_criteria.tsv",
    "03a_molecular_signature_trial_level.tsv",
    "03b_molecular_signature_cohort_level.tsv",
)

KEY_COLUMNS_BY_FILE: Dict[str, Sequence[str]] = {
    "01_molecular_signature_mapping_resource.tsv": (
        "Signature_lookup",
    ),
    "02_molecular_signature_mapped_criteria.tsv": (
        "trial_id",
        "nct_id",
        "source_file",
        "rule_index",
        "criterion_index",
        "criterion_path",
    ),
    "03a_molecular_signature_trial_level.tsv": ("trial_id", "nct_id"),
    "03b_molecular_signature_cohort_level.tsv": ("trial_id", "nct_id", "cohort"),
}


def default_processed_dir(registry: str) -> Path:
    return Path("data/eligibility_path/exports/intermediates") / registry / "molecular_signature"


def main(argv: Optional[list[str]] = None) -> int:
    return run_diff_cli(
        description="Snapshot and diff molecular-signature eligibility outputs.",
        resolve_files=lambda _registry: OUTPUT_FILES,
        key_columns_by_file=KEY_COLUMNS_BY_FILE,
        default_processed_dir=default_processed_dir,
        argv=argv,
    )


if __name__ == "__main__":
    raise SystemExit(main())
