# ANZCTR Eligibility Path Data

ANZCTR eligibility-path inputs and outputs use the shared trial-input and
eligibility-path folders.

## Source Inputs

```text
data/trial_inputs/anzctr/input_trials/version_<ddmmyyyy>/01_initial_search_anzctr_input.xlsx
data/trial_inputs/anzctr/input_trials/version_<ddmmyyyy>/02_pottr_append_anzctr_input.xlsx
data/trial_inputs/anzctr/extracted_trials/anzctr_field_extractions.csv
data/trial_inputs/anzctr/eligibility_curations/ACTRN*.py
```

ANZCTR trial downloads are written under
`data/trial_inputs/anzctr/input_trials/version_<ddmmyyyy>`. The dated folder
keeps separate `01_initial_search` and `02_pottr_append` workbooks. When
multiple version folders exist, default extraction uses the newest version
folder. The extracted trial CSV is generated from one or both selected workbooks
and includes the deterministic drug columns. The `ACTRN*.py` files are pydantic
curator outputs and are treated as source inputs for downstream eligibility
processing.

Downloaded trial review pages and their manifest are kept alongside the dated
input version:

```text
data/trial_inputs/anzctr/raw_trials/version_<ddmmyyyy>/ACTRN*.html
data/trial_inputs/anzctr/input_trials/version_<ddmmyyyy>/anzctr_download_manifest_<ddmmyyyy>.tsv
```

If `anzctr_field_extractions.csv` contains completed LLM drug-review
annotations, keep that file when cleaning generated eligibility TSVs. The drug
annotation step preserves existing LLM review columns when rerun without
`--llm_review`, but deleting the file also deletes those annotations.

## Static Resources

```text
data/eligibility_path/resources/cancer_type
data/eligibility_path/resources/gene_alteration
data/eligibility_path/resources/molecular_signature
data/eligibility_path/resources/oncotree
```

These resources are manually maintained/static and shared with CTGov where the
logic is registry-independent.

## Generated Intermediates

```text
data/eligibility_path/exports/intermediates/anzctr/cancer_type
data/eligibility_path/exports/intermediates/anzctr/gene_alteration
data/eligibility_path/exports/intermediates/anzctr/molecular_signature
```

ANZCTR cancer-type processing uses the shared CTGov/ANZCTR cancer-type
resources and shared criterion-level determination logic. A reviewed
ANZCTR-specific manual overwrite file can be passed explicitly to the
cancer-type command, but the default production pipeline does not require a
separate manual-review stage.

## Final Exports

```text
data/eligibility_path/exports/final/anzctr/trial_resource_<ddmmyyyy>.tsv
data/eligibility_path/exports/final/anzctr/cohort_resource_<ddmmyyyy>.tsv
data/eligibility_path/exports/final/eligibility_trial_resource_<ddmmyyyy>.tsv
data/eligibility_path/exports/final/eligibility_cohort_resource_<ddmmyyyy>.tsv
```

The registry-specific files are supporting exports. The top-level
`eligibility_trial_resource_<ddmmyyyy>.tsv` and
`eligibility_cohort_resource_<ddmmyyyy>.tsv` files are the combined
cross-registry final files produced by `make eligibility-path-run-all`.
