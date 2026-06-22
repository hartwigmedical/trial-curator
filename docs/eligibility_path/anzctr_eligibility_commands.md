# ANZCTR Eligibility Commands

Run from the repository root:

```bash
cd /Users/junrancao/WorkProjects/trial-curator_repo
conda activate trial_curator
export PYTHONPATH="$PWD"
```

## End-To-End

Run ANZCTR from the raw workbook and existing `ACTRN*.py` curations through
dated trial/cohort resource exports:

```bash
make eligibility-path-anzctr
```

This target reruns deterministic drug annotation. If
`data/trial_inputs/anzctr/extracted_trials/anzctr_field_extractions.csv`
already contains completed LLM review columns, keep that file; rerunning without
`--llm_review` preserves those columns by `ACTRN`.

The generated final files are written to:

```text
data/eligibility_path/exports/final/anzctr/trial_resource_<ddmmyyyy>.tsv
data/eligibility_path/exports/final/anzctr/cohort_resource_<ddmmyyyy>.tsv
```

For the cross-registry final output, run:

```bash
make eligibility-path-run-all
```

which writes:

```text
data/eligibility_path/exports/final/eligibility_trial_resource_<ddmmyyyy>.tsv
data/eligibility_path/exports/final/eligibility_cohort_resource_<ddmmyyyy>.tsv
```

To include ANZCTR LLM drug review in the cross-registry workflow:

```bash
make eligibility-path-run-all-w-llm
```

## Direct Modules

Extract drug-intervention trials and annotate them with RxNorm-matched drugs:

```bash
/Users/junrancao/anaconda3/bin/python -m aus_trial_universe.eligibility_path.anzctr.i_download_trials_and_extract_eligibility.ii_extract_drugs \
  --refresh_input_csv
```

By default, RxNorm matching resolves the latest valid `version_*` folder under:

```text
data/drug_utility_path/drug_ontology/raw_inputs/RxNorm
```

Override that root or a concrete version directory with `--rxnorm_rrf_dir`.

Optionally run the LLM drug review:

```bash
/Users/junrancao/anaconda3/bin/python -m aus_trial_universe.eligibility_path.anzctr.i_download_trials_and_extract_eligibility.ii_extract_drugs \
  --refresh_input_csv \
  --llm_review
```

For a full LLM review from the raw workbook, use:

```bash
/Users/junrancao/anaconda3/bin/python -m aus_trial_universe.eligibility_path.anzctr.i_download_trials_and_extract_eligibility.ii_extract_drugs \
  --refresh_input_csv \
  --llm_review \
  --llm_workers 10 \
  --llm_max_retries 10 \
  --llm_retry_initial_delay 2 \
  --llm_retry_max_delay 60
```

Run ANZCTR cancer-type processing:

```bash
/Users/junrancao/anaconda3/bin/python -m aus_trial_universe.eligibility_path.anzctr.ii_process_eligibility_criteria.cancer_types.cancer_type_pipeline
```

If a reviewed ANZCTR-specific cancer-type override file is deliberately created,
pass it explicitly:

```bash
/Users/junrancao/anaconda3/bin/python -m aus_trial_universe.eligibility_path.anzctr.ii_process_eligibility_criteria.cancer_types.cancer_type_pipeline \
  --manual_overwrite_file path/to/anzctr_manual_overwrite.tsv
```
