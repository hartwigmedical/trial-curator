# ANZCTR Eligibility Commands

Run ANZCTR eligibility commands from the repository root.

```bash
cd /Users/junrancao/WorkProjects/trial-curator_repo
conda activate trial_curator
export PYTHONPATH="$PWD"
```

## Trial Selection And Drug Annotation

Extract ANZCTR drug-intervention trials and selected fields:

```bash
/Users/junrancao/anaconda3/bin/python -m aus_trial_universe.anzctr.eligibility.i_download_trials_and_extract_eligibility.i_select_trials_and_fields
```

Annotate extracted trials with RxNorm-matched drugs:

```bash
/Users/junrancao/anaconda3/bin/python -m aus_trial_universe.anzctr.eligibility.i_download_trials_and_extract_eligibility.ii_extract_drugs
```

Optionally run the LLM drug review:

```bash
/Users/junrancao/anaconda3/bin/python -m aus_trial_universe.anzctr.eligibility.i_download_trials_and_extract_eligibility.ii_extract_drugs \
  --llm_review
```

## Curate Eligibility Criteria

Run the pydantic curator over ANZCTR inclusion and exclusion criteria:

```bash
/Users/junrancao/anaconda3/bin/python -m aus_trial_universe.anzctr.eligibility.i_download_trials_and_extract_eligibility.iii_pydantic_curator_batch_run
```

Overwrite existing curated `ACTRN*.py` files:

```bash
/Users/junrancao/anaconda3/bin/python -m aus_trial_universe.anzctr.eligibility.i_download_trials_and_extract_eligibility.iii_pydantic_curator_batch_run \
  --overwrite_existing
```

## Process Cancer Types

Run the first ANZCTR cancer-type processing pass:

```bash
/Users/junrancao/anaconda3/bin/python -m aus_trial_universe.anzctr.eligibility.ii_process_eligibility_criteria.cancer_types.cancer_type_pipeline
```

This writes:

```text
data/anzctr/eligibility/processed/cancer_type/01_health_condition_mapping.tsv
data/anzctr/eligibility/processed/cancer_type/02_primary_vs_health_condition.tsv
```

If any primary-tumour versus health-condition relations need manual review, the command also writes:

```text
data/anzctr/eligibility/processed/cancer_type/02a_primary_vs_health_condition_checks.tsv
```

Fill the `manual_overwrite` column in that 02a file, then rerun:

```bash
/Users/junrancao/anaconda3/bin/python -m aus_trial_universe.anzctr.eligibility.ii_process_eligibility_criteria.cancer_types.cancer_type_pipeline \
  --manual_overwrite_file data/anzctr/eligibility/processed/cancer_type/02a_primary_vs_health_condition_checks.tsv
```

The reviewed run then writes:

```text
data/anzctr/eligibility/processed/cancer_type/03_row_level_cancer_type.tsv
data/anzctr/eligibility/processed/cancer_type/04_trial_level_cancer_type.tsv
```
