# CTGov Eligibility Path Data

CTGov eligibility-path inputs and outputs use the shared trial-input and
eligibility-path folders.

## Source Inputs

```text
data/trial_inputs/ctgov/input_trials/version_<ddmmyyyy>/01_initial_search_ctgov_input.json
data/trial_inputs/ctgov/input_trials/version_<ddmmyyyy>/02_pottr_append_ctgov_input.json
data/trial_inputs/ctgov/extracted_trials/ctgov_field_extractions.csv
data/trial_inputs/ctgov/eligibility_curations/NCT*.py
```

The raw JSON files are the source registry inputs. When multiple
`version_<ddmmyyyy>` folders exist, default extraction uses the newest version
folder. The extracted trial CSV is generated from the selected JSON files. The
`NCT*.py` files are pydantic curator outputs and are treated as source inputs
for the downstream eligibility processing stages.

## Static Resources

```text
data/eligibility_path/resources/cancer_type
data/eligibility_path/resources/gene_alteration
data/eligibility_path/resources/molecular_signature
data/eligibility_path/resources/oncotree
```

These resources are manually maintained/static. They are read by both CTGov and
ANZCTR where applicable.

## Generated Intermediates

```text
data/eligibility_path/exports/intermediates/ctgov/cancer_type
data/eligibility_path/exports/intermediates/ctgov/gene_alteration
data/eligibility_path/exports/intermediates/ctgov/molecular_signature
```

The downstream resource exporters consume trial-level and cohort-level outputs
from these folders.

## Final Exports

```text
data/eligibility_path/exports/final/ctgov/trial_resource_<ddmmyyyy>.tsv
data/eligibility_path/exports/final/ctgov/cohort_resource_<ddmmyyyy>.tsv
data/eligibility_path/exports/final/eligibility_trial_resource_<ddmmyyyy>.tsv
data/eligibility_path/exports/final/eligibility_cohort_resource_<ddmmyyyy>.tsv
```

The registry-specific files are supporting exports. The top-level
`eligibility_trial_resource_<ddmmyyyy>.tsv` and
`eligibility_cohort_resource_<ddmmyyyy>.tsv` files are the combined
cross-registry final files produced by `make eligibility-path-run-all`.
