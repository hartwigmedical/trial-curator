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
make eligibility-path-run-all-trials-download-w-llm
```

## Direct Modules

Run the standard ANZCTR initial search from the reviewed advanced-search
parameters. This is a pure HTTP flow built on `curl_cffi` (no browser): it
impersonates a current Chrome TLS fingerprint to clear the registry's Cloudflare
challenge, replays the ASP.NET advanced-search POST against `TrialSearch.aspx`,
and triggers the results-page `DOWNLOAD` control for the whole-registry Excel
export. The export is cached once as `anzctr_all_trials.zip` (so the POTTR-append
run can reuse it instead of pulling ~90 MB again) and is filtered locally to the
advanced-search cohort, which becomes the canonical input workbook:

```bash
/Users/junrancao/anaconda3/bin/python -m aus_trial_universe.eligibility_path.anzctr.i_download_trials_and_extract_eligibility.i_download_trials \
  --initial_search
```

Download POTTR-append ANZCTR trials by ACTRN into the same dated version folder.
The trial-ID file can be a plain text file or a CSV/TSV with `trial_id`,
`trialId`, `ACTRN`, or `actrn`:

```bash
/Users/junrancao/anaconda3/bin/python -m aus_trial_universe.eligibility_path.anzctr.i_download_trials_and_extract_eligibility.i_download_trials \
  --trial_ids path/to/missing_anzctr_trial_ids.tsv \
  --output_dir data/trial_inputs/anzctr/input_trials/version_<ddmmyyyy>
```

This writes, by default:

```text
data/trial_inputs/anzctr/input_trials/version_<ddmmyyyy>/01_initial_search_anzctr_input.xlsx
data/trial_inputs/anzctr/input_trials/version_<ddmmyyyy>/02_pottr_append_anzctr_input.xlsx
data/trial_inputs/anzctr/input_trials/version_<ddmmyyyy>/anzctr_download_manifest_<ddmmyyyy>.tsv
data/trial_inputs/anzctr/raw_trials/version_<ddmmyyyy>/anzctr_all_trials.zip
```

The only raw output is the cached whole-registry export
`anzctr_all_trials.zip`; no per-trial HTML pages are produced.

If ANZCTR serves a Cloudflare challenge instead of the search form, the download
fails (it raises `AnzctrCloudflareChallengeError`) and the run must be retried
later. The download attempts are bounded by `ELIGIBILITY_ANZCTR_SEARCH_RETRIES`
and each request honours `ELIGIBILITY_ANZCTR_TIMEOUT_MS`.

Then refresh the canonical extracted-trials CSV from that appended workbook:

```bash
/Users/junrancao/anaconda3/bin/python -m aus_trial_universe.eligibility_path.anzctr.i_download_trials_and_extract_eligibility.iii_extract_drugs \
  --refresh_input_csv \
  --input_xlsx \
    data/trial_inputs/anzctr/input_trials/version_<ddmmyyyy>/01_initial_search_anzctr_input.xlsx \
    data/trial_inputs/anzctr/input_trials/version_<ddmmyyyy>/02_pottr_append_anzctr_input.xlsx
```

Extract drug-intervention trials and annotate them with RxNorm-matched drugs:

```bash
/Users/junrancao/anaconda3/bin/python -m aus_trial_universe.eligibility_path.anzctr.i_download_trials_and_extract_eligibility.iii_extract_drugs \
  --refresh_input_csv
```

By default, RxNorm matching resolves the latest valid `version_*` folder under:

```text
data/drug_utility_path/drug_ontology/raw_inputs/RxNorm
```

Override that root or a concrete version directory with `--rxnorm_rrf_dir`.

Pass `--no_ingredient_resolution` to skip RXNREL ingredient resolution and
output the best matched RxNorm names instead:

```bash
/Users/junrancao/anaconda3/bin/python -m aus_trial_universe.eligibility_path.anzctr.i_download_trials_and_extract_eligibility.iii_extract_drugs \
  --refresh_input_csv \
  --no_ingredient_resolution
```

Optionally run the LLM drug review:

```bash
/Users/junrancao/anaconda3/bin/python -m aus_trial_universe.eligibility_path.anzctr.i_download_trials_and_extract_eligibility.iii_extract_drugs \
  --refresh_input_csv \
  --llm_review
```

For a full LLM review from the raw workbook, use:

```bash
/Users/junrancao/anaconda3/bin/python -m aus_trial_universe.eligibility_path.anzctr.i_download_trials_and_extract_eligibility.iii_extract_drugs \
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
