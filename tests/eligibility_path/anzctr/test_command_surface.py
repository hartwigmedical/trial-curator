from __future__ import annotations

import importlib
from pathlib import Path


PIPELINE_MODULES = [
    "aus_trial_universe.eligibility_path.anzctr.i_download_trials_and_extract_eligibility.i_download_trials",
    "aus_trial_universe.eligibility_path.anzctr.i_download_trials_and_extract_eligibility.ii_select_trials_and_fields",
    "aus_trial_universe.eligibility_path.anzctr.i_download_trials_and_extract_eligibility.iii_extract_drugs",
    "aus_trial_universe.eligibility_path.anzctr.i_download_trials_and_extract_eligibility.iv_pydantic_curator_batch_run",
    "aus_trial_universe.eligibility_path.anzctr.ii_process_eligibility_criteria.cancer_types.cancer_type_pipeline",
    "aus_trial_universe.eligibility_path.anzctr.ii_process_eligibility_criteria.cancer_types.cohort_level_cancer_type",
    "aus_trial_universe.eligibility_path.anzctr.ii_process_eligibility_criteria.gene_alterations.gene_alteration_pipeline",
    "aus_trial_universe.eligibility_path.anzctr.ii_process_eligibility_criteria.gene_alterations.cohort_level_gene_alteration",
    "aus_trial_universe.eligibility_path.anzctr.ii_process_eligibility_criteria.molecular_signature.molecular_signature_pipeline",
    "aus_trial_universe.eligibility_path.anzctr.ii_process_eligibility_criteria.molecular_signature.cohort_level_molecular_signature",
    "aus_trial_universe.eligibility_path.anzctr.ii_process_eligibility_criteria.trial_resource.trial_level_resource_pipeline",
    "aus_trial_universe.eligibility_path.anzctr.ii_process_eligibility_criteria.trial_resource.cohort_level_resource_pipeline",
]


def test_anzctr_eligibility_pipeline_modules_import_and_expose_main():
    for module_name in PIPELINE_MODULES:
        module = importlib.import_module(module_name)

        assert callable(module.main)


def test_anzctr_docs_use_current_module_paths():
    docs = [
        Path("docs/eligibility_path/anzctr_eligibility_commands.md"),
        Path("aus_trial_universe/eligibility_path/README.md"),
    ]

    for path in docs:
        text = path.read_text(encoding="utf-8")
        assert "aus_trial_universe.eligibility_path.anzctr.eligibility" not in text

    command_doc = docs[0].read_text(encoding="utf-8")
    assert (
        "aus_trial_universe.eligibility_path.anzctr."
        "i_download_trials_and_extract_eligibility"
    ) in command_doc


def test_anzctr_docs_warn_not_to_delete_completed_llm_drug_reviews():
    doc = Path("docs/eligibility_path/anzctr_eligibility_data.md").read_text(
        encoding="utf-8"
    )

    assert "anzctr_field_extractions.csv" in doc
    assert "anzctr_field_extractions_w_drugs.csv" not in doc
    assert "LLM review columns" in doc or "LLM drug-review annotations" in doc


def test_anzctr_pipeline_uses_single_canonical_extracted_trials_file():
    script = Path("scripts/eligibility/pipeline.sh").read_text(encoding="utf-8")
    command_doc = Path("docs/eligibility_path/anzctr_eligibility_commands.md").read_text(
        encoding="utf-8"
    )

    assert "anzctr_field_extractions_w_drugs.csv" not in script
    assert "anzctr_field_extractions_w_drugs.csv" not in command_doc
    assert "--refresh_input_csv" in script
    assert "i_select_trials_and_fields \\" not in script
