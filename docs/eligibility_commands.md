# Eligibility Commands

Run eligibility commands from the repository root.

```bash
cd /Users/junrancao/WorkProjects/trial-curator_repo
conda activate trial_curator
export PYTHONPATH="$PWD"
```

Every Make command below runs the eligibility unit tests first.

## API Key

Put your API key in:

```text
.env.local
```

The file has already been created with placeholder values:

```text
OPENAI_API_KEY=API_123

ELIGIBILITY_DATA_DIR=data/ctgov/eligibility
ELIGIBILITY_MAX_WORKERS=1
ELIGIBILITY_CURATOR_LIMIT=2000
ELIGIBILITY_TRIAL_ID=
ELIGIBILITY_LOG_LEVEL=DEBUG
ELIGIBILITY_OUTPUT_FORMAT=tsv
ELIGIBILITY_EXPORT_DATE=
ELIGIBILITY_RUN_QA_DIFFS=0
ELIGIBILITY_QA_SNAPSHOT_DATE=
ELIGIBILITY_CANCER_TYPE_BASELINE_DIR=data/ctgov/eligibility/processed/cancer_type/baseline
ELIGIBILITY_GENE_ALTERATION_BASELINE_DIR=data/ctgov/eligibility/processed/gene_alteration/baseline
```

Replace `API_123` with your real API key. `.env.local` is ignored by git and is loaded automatically by the Make commands.

If a key has ever been pasted into a chat, script, shell history, or committed file, rotate or revoke it before using it again.

## Download And Curate New Trials

Download only new or updated CTGov trials since the last successful run, then run the LLM curator on those trials:

```bash
make eligibility-curate-new
```

Useful overrides:

```bash
ELIGIBILITY_MAX_WORKERS=4 make eligibility-curate-new
ELIGIBILITY_CURATOR_LIMIT=20 make eligibility-curate-new
ELIGIBILITY_TRIAL_ID=NCT01234567 make eligibility-curate-new
```

## Download And Curate All Trials

Download all CTGov trials matching the project search criteria, refresh the selected drug-trial field table, and re-run the LLM curator for all selected trials:

```bash
make eligibility-curate-all
```

## Extract Criteria And Export Resources

Extract cancer type, gene alteration, and molecular signature criteria from the curated `NCT*.py` files, then generate trial-level and cohort-level eligibility resource exports:

```bash
make eligibility-extract-criteria
```

This runs:

1. Cancer-type trial-level and cohort-level processing.
2. Gene-alteration trial-level and cohort-level processing.
3. Molecular-signature trial-level and cohort-level processing.
4. Optional cancer-type and gene-alteration QA diffs, when enabled.
5. Trial-level and cohort-level resource exports.

Set the export date suffix in `ddmmyyyy` format:

```bash
ELIGIBILITY_EXPORT_DATE=25052026 make eligibility-extract-criteria
```

Enable the optional QA diffs:

```bash
ELIGIBILITY_RUN_QA_DIFFS=1 \
ELIGIBILITY_QA_SNAPSHOT_DATE=15052026 \
make eligibility-extract-criteria
```

## Tests Only

Run only the eligibility unit tests:

```bash
make eligibility-tests
```
