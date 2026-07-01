# Combined Eligibility Run

This is the main eligibility-path workflow for producing the cross-registry
trial resource across CTGov and ANZCTR.

## Make Commands

Every eligibility-path `make` target (run from the repo root; details in the sections below):

| Command | Purpose |
| --- | --- |
| `make eligibility-path-run-all-trials-download` | Fresh CTGov + ANZCTR download, then the recursive no-LLM workflow to the combined resources. |
| `make eligibility-path-run-all-trials-download-w-llm` | As above, plus ANZCTR LLM drug review. |
| `make eligibility-path-run-all` | Recursive workflow from the newest existing inputs (no download). |
| `make eligibility-path-pottr-comparison` | POTTR ↔ Hartwig eligibility comparison over the newest outputs (read-only). |
| `make eligibility-path-resource-audit` | Accrete filled gap templates into new resource versions, then report remaining coverage gaps. |
| `make eligibility-path-tests` | Run the eligibility-path unit tests. |
| `make eligibility-path-clean-dry-run` | Preview the generated TSVs that a clean would remove. |
| `make eligibility-path-clean` | Remove generated TSV outputs (preserves inputs and `resource_gaps/`). |
| `make eligibility-path-ctgov` | CTGov-only end-to-end run. Rarely needed — prefer the combined targets above. |
| `make eligibility-path-anzctr` | ANZCTR-only end-to-end run. Rarely needed — prefer the combined targets above. |

Run from the repository root:

```bash
cd /Users/junrancao/WorkProjects/trial-curator_repo
conda activate trial_curator
export PYTHONPATH="$PWD"
```

## Environment and API keys

Most runs reuse existing pydantic-curator `.py` files and do not call the OpenAI API. If you run
curator commands or the ANZCTR LLM drug review (`-w-llm`), put keys and settings in `.env.local`
(ignored by git, loaded automatically by `scripts/eligibility/pipeline.sh`):

```text
OPENAI_API_KEY=API_123

ELIGIBILITY_LOG_LEVEL=INFO
ELIGIBILITY_OUTPUT_FORMAT=tsv
ELIGIBILITY_EXPORT_DATE=
ELIGIBILITY_SKIP_TESTS=0
```

Replace `API_123` with your real key.

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

The fresh-download workflows additionally, immediately after the initial
download, retire stale curations: any curated `.py` file whose trial id is no
longer in the latest download (the `03_merged` set) is moved into an
`eligibility_curations/expired_trials/` subfolder, which excludes it from all
downstream processing. Files are moved, not deleted, and POTTR-listed trials are
never expired. `make eligibility-path-run-all` reuses existing inputs and does
not run this expiry step.

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
into `version_<ddmmyyyy>` folders. ANZCTR initial acquisition is a pure
`curl_cffi` HTTP flow (no browser): it impersonates a current Chrome TLS
fingerprint to clear Cloudflare, replays the ASP.NET advanced-search POST,
triggers the results-page `DOWNLOAD` control for the whole-registry Excel
export, caches it once as
`data/trial_inputs/anzctr/raw_trials/version_<ddmmyyyy>/anzctr_all_trials.zip`,
and filters it locally into `01_initial_search_anzctr_input.xlsx`. If ANZCTR
serves a Cloudflare challenge instead of the search form, the download fails
(it raises `AnzctrCloudflareChallengeError`) and the run must be retried later.
`make eligibility-path-run-all` instead uses the newest existing
`version_<ddmmyyyy>` folder for each registry.

Each `version_<ddmmyyyy>` folder uses the same three-file input contract for
both registries: `01_initial_search_<registry>_input` is the initial cohort
only, `02_pottr_append_<registry>_input` is only the POTTR-appended trials (the
delta), and `03_merged_<registry>_input` is the union of `01` and `02`.
`03_merged` is the single authoritative input the extract/select steps read; it
is regenerated after each download phase (initial download sets `03` = `01`;
POTTR-append download sets `03` = `01 ∪ 02`). All three workflows run extraction
and pydantic curator only for trials without existing `.py` curations, write
final combined resources, compute and log missing POTTR trials, download those
trials as the `02_pottr_append` delta (refreshing `03_merged`), and repeat until
no new missing POTTR trials remain.

## POTTR Comparison Analysis

After a run rebuilds the final trial resource, compare Hartwig's curated eligibility
criteria against the POTTR AU eligibility file, per trial and per criterion:

```bash
make eligibility-path-pottr-comparison
```

This is a read-only analysis over the newest existing outputs, so it skips the preflight
unit tests and the resource accrete/report steps. It auto-selects the newest final trial
resource, POTTR snapshot, and CTGov POTTR-id alias table (no dates to edit) and writes:

```text
data/eligibility_path/analysis/eligibility_vs_pottr_comparison_<ddmmyyyy>.tsv
```

To do a full refresh and then the comparison in one go, chain the two targets. `make` runs
them left to right and stops if the download run fails, so the comparison only runs on a
good rebuild — this is preferred over a bespoke combined target (no duplicated recipe, and
the comparison target stays reusable after any run):

```bash
make eligibility-path-run-all-trials-download eligibility-path-pottr-comparison
```

The output is a two-header-row TSV (read with `skiprows=1` in pandas). See
`aus_trial_universe/eligibility_path/analysis/README.md` for the column layout, verdict
vocabulary, and reconciliation rules.

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

Every run that rebuilds the final resources snapshots them (minute-stamped) under
`data/eligibility_path/exports/final/history/` and diffs the two most recent
snapshots, writing `data/eligibility_path/exports/final/final_resource_diff.tsv`
(trials added/removed and cells changed). Because the snapshots carry the hour
and minute, two runs on the same day are still comparable. Regenerate on demand:

```bash
python -m aus_trial_universe.eligibility_path.qa.final_resource_diff
```

Tune the ANZCTR download if the registry is slow or you need more attempts:

```text
ELIGIBILITY_ANZCTR_TIMEOUT_MS=120000
ELIGIBILITY_ANZCTR_SEARCH_RETRIES=3
```

`ELIGIBILITY_ANZCTR_TIMEOUT_MS` bounds each HTTP request and
`ELIGIBILITY_ANZCTR_SEARCH_RETRIES` bounds the number of advanced-search +
export attempts. If ANZCTR serves a Cloudflare challenge instead of the search
form, the download fails (it raises `AnzctrCloudflareChallengeError`) and the
run must be retried later; there is no manual-download or browser fallback.

On success the workflow caches the whole-registry export as
`anzctr_all_trials.zip`, filters it into `01_initial_search_anzctr_input.xlsx`,
then continues with extraction, POTTR append, and final resource generation.

## Clean-Run Inputs

Safe generated folders to delete before a clean run:

```text
data/eligibility_path/exports/intermediates
data/eligibility_path/exports/final
```

Exception: `data/eligibility_path/exports/intermediates/resource_gaps/` holds
curator-filled gap templates (human input that accretes into new resource
versions) and must be preserved. `make eligibility-path-clean` skips it
automatically; do not `rm -rf exports/intermediates`.

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
