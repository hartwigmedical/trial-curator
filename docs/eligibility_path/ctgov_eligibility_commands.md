# Eligibility Path Commands

Run from the repository root:

```bash
cd /Users/junrancao/WorkProjects/trial-curator_repo
conda activate trial_curator
export PYTHONPATH="$PWD"
```

## API Key

Most end-to-end eligibility-path runs below use existing pydantic curator `.py`
files and do not call the OpenAI API. If you do run curator commands, put keys
in `.env.local`:

```text
OPENAI_API_KEY=API_123

ELIGIBILITY_LOG_LEVEL=INFO
ELIGIBILITY_OUTPUT_FORMAT=tsv
ELIGIBILITY_EXPORT_DATE=
ELIGIBILITY_RUN_QA_DIFFS=0
ELIGIBILITY_SKIP_TESTS=0
```

Replace `API_123` with your real API key. `.env.local` is ignored by git and is
loaded automatically by `scripts/eligibility/pipeline.sh`.

## Data Layout

Static/source inputs:

```text
data/trial_inputs/ctgov/input_trials/version_13022026/ctgov_input.json
data/trial_inputs/ctgov/extracted_trials/ctgov_field_extractions.csv
data/trial_inputs/ctgov/eligibility_curations/NCT*.py
data/trial_inputs/anzctr/input_trials/version_10062026/anzctr_input.xlsx
data/trial_inputs/anzctr/extracted_trials/anzctr_field_extractions.csv
data/trial_inputs/anzctr/eligibility_curations/ACTRN*.py
data/eligibility_path/resources
```

Generated outputs:

```text
data/eligibility_path/exports/intermediates/<registry>/
data/eligibility_path/exports/final/<registry>/
data/eligibility_path/exports/final/eligibility_trial_resource_<ddmmyyyy>.tsv
data/eligibility_path/exports/final/eligibility_cohort_resource_<ddmmyyyy>.tsv
```

## End-To-End Runs

Run CTGov from raw trial input and existing curated `.py` files through final
trial/cohort resource exports:

```bash
make eligibility-path-ctgov
```

Run ANZCTR:

```bash
make eligibility-path-anzctr
```

Run both registries and write the single combined trial-resource TSV:

```bash
make eligibility-path-run-all
```

The final cross-registry files are:

```text
data/eligibility_path/exports/final/eligibility_trial_resource_<ddmmyyyy>.tsv
data/eligibility_path/exports/final/eligibility_cohort_resource_<ddmmyyyy>.tsv
```

Run both registries with the optional ANZCTR LLM drug review:

```bash
make eligibility-path-run-all-w-llm
```

The older aggregate target is kept as an alias for both registries:

```bash
make eligibility-extract-criteria
```

Set the dated export suffix:

```bash
ELIGIBILITY_EXPORT_DATE=22062026 make eligibility-path-run-all
```

Skip the preflight tests:

```bash
ELIGIBILITY_SKIP_TESTS=1 make eligibility-path-run-all
```

Run QA diffs after generating intermediates:

```bash
ELIGIBILITY_RUN_QA_DIFFS=1 make eligibility-path-run-all
```

Preview or remove generated eligibility-path TSV outputs:

```bash
make eligibility-path-clean-dry-run
make eligibility-path-clean
```

## Tests Only

```bash
make eligibility-tests
make eligibility-path-tests
```

## Direct Modules

CTGov trial extraction:

```bash
/Users/junrancao/anaconda3/bin/python -m aus_trial_universe.eligibility_path.ctgov.i_download_trials_and_extract_eligibility.ii_extract_fields
```

CTGov final trial resource:

```bash
/Users/junrancao/anaconda3/bin/python -m aus_trial_universe.eligibility_path.ctgov.ii_process_eligibility_criteria.trial_resource.trial_level_resource_pipeline
```

CTGov final cohort resource:

```bash
/Users/junrancao/anaconda3/bin/python -m aus_trial_universe.eligibility_path.ctgov.ii_process_eligibility_criteria.trial_resource.cohort_level_resource_pipeline
```
