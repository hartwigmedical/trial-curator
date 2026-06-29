"""CTGov molecular-signature pipeline.

Thin registry adapter over the shared, registry-agnostic core
(:mod:`aus_trial_universe.eligibility_path.shared.molecular_signature.molecular_signature_pipeline_core`).
This module binds the CTGov :class:`MolecularSignatureRegistrySpec` and re-exports
the public surface that ``cohort_level_molecular_signature`` and the tests import;
the ``python -m`` CLI entry point is preserved for the recursive workflow.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import pandas as pd

from aus_trial_universe.eligibility_path.shared.utils.curated_files import (
    has_curated_py_files,
)
from aus_trial_universe.eligibility_path.shared.molecular_signature import (
    molecular_signature_pipeline_core as _core,
)
from aus_trial_universe.eligibility_path.shared.molecular_signature.molecular_signature_pipeline_core import (
    DEFAULT_MOLECULAR_SIGNATURE_RESOURCE_SUBDIR,
    DEFAULT_OUTPUT_FORMAT,
    DEFAULT_PROCESSED_SUBDIR,
    DEFAULT_SHARED_RESOURCES_DIR,
    MAPPED_CRITERIA_STEM,
    MAPPING_RESOURCE_STEM,
    SUPPORTED_OUTPUT_FORMATS,
    SUPPORTED_RESOURCE_SUFFIXES,
    TRIAL_LEVEL_STEM,
    MolecularSignaturePipelineInputs,
    MolecularSignaturePipelineOutputs,
    MolecularSignatureRegistrySpec,
    _output_path,
    _polarity_from_rule_and_not,
    _read_tabular_file,
    _resolve_path,
    _write_tabular_file,
)

DEFAULT_ELIGIBILITY_DATA_DIR = Path("data/eligibility_path/exports/intermediates/ctgov")
DEFAULT_CURATED_DIR = Path("data/trial_inputs/ctgov/eligibility_curations")

_SPEC = MolecularSignatureRegistrySpec(
    registry_label="CTGov",
    trial_id_column="nct_id",
    trial_id_prefix="NCT",
    default_eligibility_data_dir=DEFAULT_ELIGIBILITY_DATA_DIR,
    default_curated_dir=DEFAULT_CURATED_DIR,
    normalize_trial_id_from_stem=lambda stem: str(stem).upper(),
)


def _contains_nct_py_files(path: Path) -> bool:
    return has_curated_py_files(path, trial_id_prefix="NCT")


def collapse_to_trial_level(mapped_df: pd.DataFrame) -> pd.DataFrame:
    return _core.collapse_to_trial_level(mapped_df, trial_id_column=_SPEC.trial_id_column)


def discover_pipeline_inputs(**kwargs) -> MolecularSignaturePipelineInputs:
    return _core.discover_pipeline_inputs(spec=_SPEC, **kwargs)


def main(argv: Optional[List[str]] = None) -> int:
    return _core.main(_SPEC, argv)


if __name__ == "__main__":
    raise SystemExit(main())
