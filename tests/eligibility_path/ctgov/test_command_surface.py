from __future__ import annotations

import importlib
from pathlib import Path


PIPELINE_MODULES = [
    "aus_trial_universe.eligibility_path.ctgov.i_download_trials_and_extract_eligibility.i_api_download",
    "aus_trial_universe.eligibility_path.ctgov.i_download_trials_and_extract_eligibility.ii_extract_fields",
    "aus_trial_universe.eligibility_path.ctgov.i_download_trials_and_extract_eligibility.iii_pydantic_curator_batch_run",
    "aus_trial_universe.eligibility_path.ctgov.ii_process_eligibility_criteria.cancer_types.cancer_type_pipeline",
    "aus_trial_universe.eligibility_path.ctgov.ii_process_eligibility_criteria.cancer_types.cohort_level_cancer_type",
    "aus_trial_universe.eligibility_path.ctgov.ii_process_eligibility_criteria.gene_alterations.gene_alteration_pipeline",
    "aus_trial_universe.eligibility_path.ctgov.ii_process_eligibility_criteria.gene_alterations.cohort_level_gene_alteration",
    "aus_trial_universe.eligibility_path.ctgov.ii_process_eligibility_criteria.molecular_signature.molecular_signature_pipeline",
    "aus_trial_universe.eligibility_path.ctgov.ii_process_eligibility_criteria.molecular_signature.cohort_level_molecular_signature",
    "aus_trial_universe.eligibility_path.qa.cancer_type_output_diff",
    "aus_trial_universe.eligibility_path.qa.gene_alteration_output_diff",
    "aus_trial_universe.eligibility_path.qa.molecular_signature_output_diff",
    "aus_trial_universe.eligibility_path.ctgov.ii_process_eligibility_criteria.trial_resource.trial_level_resource_pipeline",
    "aus_trial_universe.eligibility_path.ctgov.ii_process_eligibility_criteria.trial_resource.cohort_level_resource_pipeline",
    "aus_trial_universe.eligibility_path.shared.trial_resource.combined_trial_resource_export",
]

MAKE_TARGETS = [
    "eligibility-path-ctgov",
    "eligibility-path-anzctr",
    "eligibility-path-run-all",
    "eligibility-path-run-all-w-llm",
    "eligibility-path-clean",
    "eligibility-path-clean-dry-run",
    "eligibility-extract-criteria",
    "eligibility-tests",
    "eligibility-path-tests",
]

REMOVED_MAKE_TARGETS = [
    "eligibility-path-all",
    "eligibility-path-all-w-llm",
    "eligibility-pipeline",
    "eligibility-cancer-type",
    "eligibility-gene-alteration",
    "eligibility-molecular-signature",
    "eligibility-trial-resource",
    "eligibility-cohort-resource",
]

SCRIPT_COMMANDS = [
    "ctgov",
    "anzctr",
    "all",
    "all-w-llm",
    "tests",
]


def test_eligibility_pipeline_modules_import_and_expose_main():
    for module_name in PIPELINE_MODULES:
        module = importlib.import_module(module_name)

        assert callable(module.main)


def test_makefile_exposes_eligibility_targets():
    makefile = Path("Makefile").read_text(encoding="utf-8")
    make_targets = {
        line.split(":", 1)[0]
        for line in makefile.splitlines()
        if line and not line.startswith("\t") and ":" in line
    }

    for target in MAKE_TARGETS:
        assert target in make_targets


def test_makefile_does_not_expose_old_eligibility_targets():
    makefile = Path("Makefile").read_text(encoding="utf-8")
    make_targets = {
        line.split(":", 1)[0]
        for line in makefile.splitlines()
        if line and not line.startswith("\t") and ":" in line
    }

    for target in REMOVED_MAKE_TARGETS:
        assert target not in make_targets


def test_eligibility_commands_doc_lists_make_targets():
    doc = Path("docs/eligibility_path/ctgov_eligibility_commands.md").read_text(
        encoding="utf-8"
    )

    for target in MAKE_TARGETS:
        assert f"make {target}" in doc


def test_combined_run_doc_lists_public_workflows_and_output():
    doc = Path("docs/eligibility_path/combined_eligibility_run.md").read_text(
        encoding="utf-8"
    )

    assert "make eligibility-path-run-all" in doc
    assert "make eligibility-path-run-all-w-llm" in doc
    assert "data/eligibility_path/exports/final/eligibility_trial_resource_<ddmmyyyy>.tsv" in doc
    assert "data/eligibility_path/exports/final/eligibility_cohort_resource_<ddmmyyyy>.tsv" in doc
    assert "registry" in doc
    assert "trialId" in doc


def test_eligibility_script_exposes_only_primary_pipeline_commands():
    script = Path("scripts/eligibility/pipeline.sh").read_text(encoding="utf-8")

    for command in SCRIPT_COMMANDS:
        assert command in script

    assert "trial-resource" not in script.partition("validate_command()")[2]
    assert "cohort-resource" not in script.partition("validate_command()")[2]


def test_eligibility_clean_script_only_targets_generated_tsv_exports():
    script = Path("scripts/eligibility/clean_outputs.sh").read_text(
        encoding="utf-8"
    )

    assert "data/eligibility_path/exports/intermediates" in script
    assert "data/eligibility_path/exports/final" in script
    assert '-name "*.tsv"' in script
    assert "data/trial_inputs" not in script
    assert "rm --" in script


def test_eligibility_extract_criteria_includes_resource_exports_and_optional_qa():
    script = Path("scripts/eligibility/pipeline.sh").read_text(encoding="utf-8")

    assert "cancer_type_output_diff" in script
    assert "gene_alteration_output_diff" in script
    assert "trial_level_resource_pipeline" in script
    assert "cohort_level_resource_pipeline" in script
    assert "combined_trial_resource_export" in script
    assert "all-w-llm" in script
    assert "ELIGIBILITY_EXPORT_DATE" in script
    assert "ELIGIBILITY_RUN_QA_DIFFS" in script


def test_eligibility_secret_files_are_ignored_and_documented():
    gitignore = Path(".gitignore").read_text(encoding="utf-8")
    doc = Path("docs/eligibility_path/ctgov_eligibility_commands.md").read_text(
        encoding="utf-8"
    )

    assert ".env" in gitignore
    assert ".env.*" in gitignore
    assert "!/.env" not in gitignore
    assert "!.env\n" not in gitignore
    assert "!.env.local" not in gitignore
    assert "!.env.*" not in gitignore
    assert ".env.local" in doc
    assert "OPENAI_API_KEY=API_123" in doc


def test_docs_and_scripts_do_not_contain_openai_api_keys():
    paths = [
        Path("docs/eligibility_path/ctgov_eligibility_commands.md"),
        Path("scripts/eligibility/pipeline.sh"),
    ]

    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert "sk-" not in text
