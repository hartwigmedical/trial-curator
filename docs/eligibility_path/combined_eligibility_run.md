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

The final cross-registry outputs are:

```text
data/eligibility_path/exports/final/eligibility_trial_resource_<ddmmyyyy>.tsv
data/eligibility_path/exports/final/eligibility_cohort_resource_<ddmmyyyy>.tsv
```

The combined file uses:

```text
registry
trialId
```

as the leading identifier columns. CTGov `nctId` values are normalized into
`trialId`; ANZCTR `trialId` values are kept as `trialId`.

The cohort file uses the same schema with `cohort` added after `trialId`.

## Combined Schema

| Final column | CTGov source | ANZCTR source |
| --- | --- | --- |
| registry | fixed `ctgov` | fixed `anzctr` |
| trialId | `nctId` | `trialId` |
| title | `briefTitle` | `STUDY TITLE` |
| scientific_title | `officialTitle` | `SCIENTIFIC TITLE` |
| recruitment_status | `status` | `RECRUITMENT STATUS` |
| phase | `phases` | `PHASE` |
| primary_sponsor_name | `leadSponsor` | `PRIMARY SPONSOR NAME` |
| health_condition | `conditions` | `HEALTH CONDITION` |
| min_age | `minAge` | `MIN AGE` + `MIN AGE TYPE` |
| max_age | `maxAge` | `MAX AGE` + `MAX AGE TYPE` |
| recruitment_country | derived from `address` | `RECRUITMENT COUNTRY` |
| recruitment_state | derived from `address` | `RECRUITMENT STATE` |
| intervention_type_or_code | `interventionType` | `anzctr_intervention_codes` |
| intervention_name | `interventionName` | `DRUG_rxnorm_matched` |
| llm_drug_to_remove |  | `llm_drug_to_remove` |
| llm_drugs_to_add |  | `llm_drugs_to_add` |
| llm_drugs_to_correct |  | `llm_drugs_to_correct` |
| llm_reasoning |  | `llm_reasoning` |
| cancer_type_inclusive | `cancer_type_inclusive` | `cancer_type_inclusive` |
| cancer_type_exclusive | `cancer_type_exclusive` | `cancer_type_exclusive` |
| gene_alteration_inclusive | `gene_alteration_inclusive` | `gene_alteration_inclusive` |
| gene_alteration_exclusive | `gene_alteration_exclusive` | `gene_alteration_exclusive` |
| molecular_signature_inclusive | `molecular_signature_inclusive` | `molecular_signature_inclusive` |
| molecular_signature_exclusive | `molecular_signature_exclusive` | `molecular_signature_exclusive` |

## LLM Drug Review Run

```bash
make eligibility-path-run-all-w-llm
```

This runs the same combined workflow, but ANZCTR drug extraction also calls the
LLM review step before eligibility processing.

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
