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
ELIGIBILITY_SKIP_TESTS=0
```

Replace `API_123` with your real API key. `.env.local` is ignored by git and is
loaded automatically by `scripts/eligibility/pipeline.sh`.

## Data Layout

Static/source inputs:

```text
data/trial_inputs/ctgov/input_trials/version_<ddmmyyyy>/01_initial_search_ctgov_input.json
data/trial_inputs/ctgov/input_trials/version_<ddmmyyyy>/02_pottr_append_ctgov_input.json
data/trial_inputs/ctgov/input_trials/version_<ddmmyyyy>/03_merged_ctgov_input.json
data/trial_inputs/ctgov/download_state/ctgov_trials_latest.json
data/trial_inputs/ctgov/download_state/ctgov_trials_meta_<timestamp>.json
data/trial_inputs/ctgov/extracted_trials/ctgov_field_extractions.csv
data/trial_inputs/ctgov/eligibility_curations/NCT*.py
data/trial_inputs/anzctr/input_trials/version_<ddmmyyyy>/01_initial_search_anzctr_input.xlsx
data/trial_inputs/anzctr/input_trials/version_<ddmmyyyy>/02_pottr_append_anzctr_input.xlsx
data/trial_inputs/anzctr/input_trials/version_<ddmmyyyy>/03_merged_anzctr_input.xlsx
data/trial_inputs/anzctr/extracted_trials/anzctr_field_extractions.csv
data/trial_inputs/anzctr/eligibility_curations/ACTRN*.py
data/eligibility_path/resources
```

Each `version_<ddmmyyyy>` folder uses the same three-file input contract for
both registries: `01_initial_search_<registry>_input` is the initial
advanced-search cohort only, `02_pottr_append_<registry>_input` is only the
POTTR-appended trials (the delta), and `03_merged_<registry>_input` is the union
of `01` and `02`. `03_merged` is the single authoritative input the extract
steps read (it replaces the older `ctgov_trials_merged.json` name); it is
regenerated after each download phase (initial download sets `03` = `01`;
POTTR-append download sets `03` = `01 ∪ 02`).

When input paths are omitted, extraction commands use the newest
`version_<ddmmyyyy>` folder under each registry's `input_trials` directory,
preferring `03_merged` and falling back to `01`+`02` for older version folders.

`data/trial_inputs/ctgov/download_state` holds the persistent CTGov cache
(`ctgov_trials_latest.json`, unchanged across runs). Only the newest
`ctgov_trials_meta_*.json` snapshot is retained; older snapshots are pruned
automatically after each `--all`/`--incremental` download.

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

Run both registries from the newest existing input version folders and write
the combined trial/cohort resource TSVs:

```bash
make eligibility-path-run-all
```

Freshly download CTGov and ANZCTR initial trial inputs into dated
`version_<ddmmyyyy>` folders, then run the recursive no-LLM workflow:

```bash
make eligibility-path-run-all-trials-download
```

The final cross-registry files are:

```text
data/eligibility_path/exports/final/eligibility_trial_resource_<ddmmyyyy>.tsv
data/eligibility_path/exports/final/eligibility_cohort_resource_<ddmmyyyy>.tsv
```

Run the most complete workflow with fresh downloads and optional ANZCTR LLM
drug review:

```bash
make eligibility-path-run-all-trials-download-w-llm
```

Set the dated export suffix:

```bash
ELIGIBILITY_EXPORT_DATE=22062026 make eligibility-path-run-all
```

Skip the preflight tests:

```bash
ELIGIBILITY_SKIP_TESTS=1 make eligibility-path-run-all
```

Every run that rebuilds the final resources snapshots them (minute-stamped) under
`data/eligibility_path/exports/final/history/` and diffs the two most recent
snapshots, writing `data/eligibility_path/exports/final/final_resource_diff.tsv`
(trials added/removed and cells changed). Because the snapshots carry the hour
and minute, two runs on the same day are still comparable. Regenerate on demand:

```bash
python -m aus_trial_universe.eligibility_path.qa.final_resource_diff
```

Audit hand-curated resource coverage (accrete filled fill-ready templates into
new resource versions, then flag remaining gaps and refresh the templates):

```bash
make eligibility-path-resource-audit
```

This audit also runs automatically on every `ctgov`, `anzctr`, and run-all
pipeline invocation: accretion happens before processing and gap reporting
happens after, so the standalone target is mainly for an on-demand audit.

Preview or remove generated eligibility-path TSV outputs:

```bash
make eligibility-path-clean-dry-run
make eligibility-path-clean
```

## Tests Only

```bash
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
