# Combined Eligibility Run

This is the main eligibility-path workflow for producing the cross-registry
trial resource across CTGov and ANZCTR.

Run from the repository root:

```bash
cd /Users/junrancao/WorkProjects/trial-curator_repo
conda activate trial_curator
export PYTHONPATH="$PWD"
```

## Standard Run

Run the most complete workflow, including fresh CTGov/ANZCTR trial downloads
and ANZCTR LLM drug review:

```bash
make eligibility-path-run-all-trials-download-w-llm
```

Run the fresh-download recursive workflow without ANZCTR LLM drug review:

```bash
make eligibility-path-run-all-trials-download
```

Run the recursive workflow from the newest existing trial-input version folders,
without refreshing initial downloads and without ANZCTR LLM drug review:

```bash
make eligibility-path-run-all
```

This runs:

1. Eligibility-path unit tests.
2. CTGov trial extraction and eligibility processing.
3. ANZCTR trial extraction and deterministic drug annotation.
4. ANZCTR eligibility processing.
5. Registry-specific trial/cohort support exports.
6. Final combined trial/cohort resource exports.
7. POTTR AU missing-trial coverage check.

The final cross-registry outputs are:

```text
data/eligibility_path/exports/final/eligibility_trial_resource_<ddmmyyyy>.tsv
data/eligibility_path/exports/final/eligibility_cohort_resource_<ddmmyyyy>.tsv
```

Missing POTTR trials are computed after the final resources and logged rather
than written as a final TSV. The check compares POTTR's
`trial_eligibility.AU.tsv` and `trial_registry.AU.tsv` against the combined
final trial/cohort resources. The default POTTR sources are the public POTTR
GitHub TSVs; pass `--pottr_trial_eligibility_file` or
`--pottr_trial_registry_file` to
`aus_trial_universe.eligibility_path.shared.trial_resource.combined_trial_resource_export`
to override them.

The combined file uses:

```text
trialId
registry
```

as the leading identifier columns. CTGov `nctId` values are normalized into
`trialId`; ANZCTR `trialId` values are kept as `trialId`.

The cohort file uses the same schema with `cohort` added after `registry`.

The fresh-download workflows download CTGov and ANZCTR initial-search inputs
into `version_<ddmmyyyy>` folders. ANZCTR initial acquisition opens the registry
search page, clicks `DOWNLOAD`, keeps the raw `TrialDetails.zip`, and imports
the xlsx inside it as `01_initial_search_anzctr_input.xlsx`. If ANZCTR blocks
automation with Cloudflare, the same command can wait for a manually downloaded
zip/xlsx in the watched downloads folder and import it automatically.
`make eligibility-path-run-all` instead uses the newest existing
`version_<ddmmyyyy>` folder for each registry. All three workflows run
extraction and pydantic curator only for trials without existing `.py`
curations, write final combined resources, compute and log missing POTTR
trials, download those trials into `02_pottr_append` inputs, and repeat until no
new missing POTTR trials remain.

## Combined Schema

| Final column | CTGov source | ANZCTR source |
| --- | --- | --- |
| trialId | `nctId` | `trialId` |
| registry | fixed `ctgov` | fixed `anzctr` |
| title | `briefTitle` | `STUDY TITLE` |
| scientific_title | `officialTitle` | `SCIENTIFIC TITLE` |
| primary_sponsor | `leadSponsor` | `PRIMARY SPONSOR NAME` |
| recruitment_status | `status` | `RECRUITMENT STATUS` |
| phase | `phases` | `PHASE` |
| country | derived from `address` | `RECRUITMENT COUNTRY` |
| original_health_conditions | `conditions` | `HEALTH CONDITION` |
| intervention_type | `interventionType` | `anzctr_intervention_codes` |
| intervention_name | `interventionName` | `DRUG_rxnorm_matched` |
| cancer_type_inclusive | `cancer_type_inclusive` | `cancer_type_inclusive` |
| cancer_type_exclusive | `cancer_type_exclusive` | `cancer_type_exclusive` |
| gene_alteration_inclusive | `gene_alteration_inclusive` | `gene_alteration_inclusive` |
| gene_alteration_exclusive | `gene_alteration_exclusive` | `gene_alteration_exclusive` |
| molecular_signature_inclusive | `molecular_signature_inclusive` | `molecular_signature_inclusive` |
| molecular_signature_exclusive | `molecular_signature_exclusive` | `molecular_signature_exclusive` |

OncoTree-mapped values in `cancer_type_inclusive` and
`cancer_type_exclusive` are displayed as OncoTree codes in the combined files.
Drug review columns are resolved upstream and are not included in the final
combined resources.

## LLM Drug Review Run

```bash
make eligibility-path-run-all-trials-download-w-llm
```

This runs the fresh-download recursive workflow, but ANZCTR drug extraction also
calls the LLM review step before eligibility processing.

The LLM-enabled run uses these environment variables:

```text
ELIGIBILITY_LLM_WORKERS=10
ELIGIBILITY_LLM_MAX_RETRIES=10
ELIGIBILITY_LLM_RETRY_INITIAL_DELAY=2
ELIGIBILITY_LLM_RETRY_MAX_DELAY=60
ELIGIBILITY_LLM_LIMIT=
ELIGIBILITY_LLM_MODEL=
```

ANZCTR RxNorm matching uses `ANZCTR_RXNORM_RRF_DIR`, which may point either
to a concrete RRF version directory or to a root containing `version_*`
subdirectories. The default root is:

```text
data/drug_utility_path/drug_ontology/raw_inputs/RxNorm
```

## Optional Settings

Set the dated export suffix:

```bash
ELIGIBILITY_EXPORT_DATE=22062026 make eligibility-path-run-all
```

Skip the preflight unit tests only when intentionally debugging:

```bash
ELIGIBILITY_SKIP_TESTS=1 make eligibility-path-run-all
```

Run QA diffs after generating intermediates:

```bash
ELIGIBILITY_RUN_QA_DIFFS=1 make eligibility-path-run-all
```

Tune ANZCTR browser download settings if the registry is slow or you want to
debug the browser visibly:

```text
ELIGIBILITY_ANZCTR_TIMEOUT_MS=120000
ELIGIBILITY_ANZCTR_SEARCH_RETRIES=3
ELIGIBILITY_ANZCTR_HEADED=0
```

If ANZCTR blocks automation with Cloudflare, the workflow relaunches in headed
mode. Once a visible browser is opened after Cloudflare, complete the ANZCTR
search manually in that browser and click `DOWNLOAD`; the script stops trying to
click `SEARCH` itself and only watches for the downloaded file.

```bash
ELIGIBILITY_ANZCTR_HEADED=1 ELIGIBILITY_ANZCTR_SEARCH_RETRIES=1 make eligibility-path-run-all-trials-download
```

The workflow watches `${HOME}/Downloads` for a new ANZCTR zip/xlsx and imports
it automatically. Override that watch folder or wait time with:

```text
ELIGIBILITY_ANZCTR_MANUAL_DOWNLOAD_DIR=/path/to/downloads
ELIGIBILITY_ANZCTR_MANUAL_DOWNLOAD_WAIT_MS=600000
```

The workflow imports the workbook from the zip as
`01_initial_search_anzctr_input.xlsx`, then continues with extraction,
POTTR append, and final resource generation.

## Clean-Run Inputs

Safe generated folders to delete before a clean run:

```text
data/eligibility_path/exports/intermediates
data/eligibility_path/exports/final
```

Intermediate TSVs use numbered stage prefixes. Distinct stages use `01_`,
`02_`, and so on; paired trial/cohort outputs share the same number and use
`a` for trial level and `b` for cohort level, for example
`04a_cancer_type_trial_level.tsv` and `04b_cancer_type_cohort_level.tsv`.
Diagnostic reports live under a `diagnostics/` subfolder and start their own
local numbering, for example
`gene_alteration/diagnostics/01a_gene_alteration_conflicts_trial_level.tsv`.

Use the cleanup targets to preview or remove generated TSV files from those
folders while preserving source inputs:

```bash
make eligibility-path-clean-dry-run
make eligibility-path-clean
```

Keep source inputs and manually maintained resources:

```text
data/trial_inputs/<registry>/input_trials
data/trial_inputs/<registry>/eligibility_curations
data/eligibility_path/resources
```

For ANZCTR, keep:

```text
data/trial_inputs/anzctr/extracted_trials/anzctr_field_extractions.csv
```

if it contains completed LLM drug-review annotations. Deleting it deletes those
annotations.

## Expected Supporting Outputs

The combined run also creates registry-specific support exports:

```text
data/eligibility_path/exports/final/ctgov/trial_resource_<ddmmyyyy>.tsv
data/eligibility_path/exports/final/ctgov/cohort_resource_<ddmmyyyy>.tsv
data/eligibility_path/exports/final/anzctr/trial_resource_<ddmmyyyy>.tsv
data/eligibility_path/exports/final/anzctr/cohort_resource_<ddmmyyyy>.tsv
```

These are useful for debugging registry-specific processing, but the primary
combined deliverables are:

```text
data/eligibility_path/exports/final/eligibility_trial_resource_<ddmmyyyy>.tsv
data/eligibility_path/exports/final/eligibility_cohort_resource_<ddmmyyyy>.tsv
```
