from __future__ import annotations

import importlib

import pytest


SHARED_MODULES = [
    "aus_trial_universe.ctgov.eligibility.shared.general.csv_mapping_file",
    "aus_trial_universe.ctgov.eligibility.shared.general.load_curated_rules",
    "aus_trial_universe.ctgov.eligibility.shared.general.prune_criteria_nodes",
    "aus_trial_universe.ctgov.eligibility.shared.general.prune_curation_tree",
    "aus_trial_universe.ctgov.eligibility.shared.general.text_normalisation",
    "aus_trial_universe.ctgov.eligibility.shared.general.traverse_curation_tree",
    "aus_trial_universe.ctgov.eligibility.shared.general.write_curated_rules",
    "aus_trial_universe.ctgov.eligibility.shared.ctgov_trials_to_remove",
    "aus_trial_universe.ctgov.eligibility.shared.oncotree.traverse_oncotree",
    "aus_trial_universe.ctgov.eligibility.shared.debug.retrieve_rule_text",
]


@pytest.mark.parametrize("module_name", SHARED_MODULES)
def test_eligibility_shared_modules_import(module_name: str):
    importlib.import_module(module_name)


def test_old_ctgov_utils_package_is_removed():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("aus_trial_universe.ctgov.utils")


def test_stage_local_download_utils_package_is_removed():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(
            "aus_trial_universe.ctgov.eligibility.i_download_trials_and_extract_eligibility.utils"
        )
