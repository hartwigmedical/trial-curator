# ANZCTR Eligibility Workflow Commands

Run commands from the repository root:

```bash
cd /Users/junrancao/WorkProjects/trial-curator_repo
```

## i. Extract Drug-Intervention Trials

Extract ANZCTR trials whose intervention code includes `Treatment: Drugs`.

```bash
/Users/junrancao/anaconda3/bin/python -m aus_trial_universe.anzctr.eligibility.i_download_trials_and_extract_eligibility.i_extract_trials_and_fields
```

Default input:

```text
data/anzctr/trials/version_10062026/anzctr_input.xlsx
```

Default output:

```text
data/anzctr/eligibility/trials/anzctr_field_extractions.csv
```

## ii. Annotate Extracted Trials With Drugs

Run deterministic RxNorm/curated-alias drug extraction only:

```bash
/Users/junrancao/anaconda3/bin/python -m aus_trial_universe.anzctr.eligibility.i_download_trials_and_extract_eligibility.ii_extract_drugs
```

Run deterministic extraction plus optional LLM review:

```bash
/Users/junrancao/anaconda3/bin/python -m aus_trial_universe.anzctr.eligibility.i_download_trials_and_extract_eligibility.ii_extract_drugs \
  --llm_review
```

For a full LLM review run with conservative retry settings:

```bash
/Users/junrancao/anaconda3/bin/python -m aus_trial_universe.anzctr.eligibility.i_download_trials_and_extract_eligibility.ii_extract_drugs \
  --llm_review \
  --llm_workers 10 \
  --llm_max_retries 10 \
  --llm_retry_initial_delay 2 \
  --llm_retry_max_delay 60
```

Default input:

```text
data/anzctr/eligibility/trials/anzctr_field_extractions.csv
```

Default output:

```text
data/anzctr/eligibility/trials/anzctr_field_extractions_w_drugs.csv
```

## iii. Curate Eligibility With Pydantic Curator

Run the pydantic curator over all ANZCTR drug-intervention trials:

```bash
/Users/junrancao/anaconda3/bin/python -m aus_trial_universe.anzctr.eligibility.i_download_trials_and_extract_eligibility.iii_pydantic_curator_batch_run
```

Re-run and overwrite existing per-trial curation files:

```bash
/Users/junrancao/anaconda3/bin/python -m aus_trial_universe.anzctr.eligibility.i_download_trials_and_extract_eligibility.iii_pydantic_curator_batch_run \
  --overwrite_existing
```

Smoke test one trial:

```bash
/Users/junrancao/anaconda3/bin/python -m aus_trial_universe.anzctr.eligibility.i_download_trials_and_extract_eligibility.iii_pydantic_curator_batch_run \
  --limit 1
```

Default input:

```text
data/anzctr/eligibility/trials/anzctr_field_extractions.csv
```

Default output directory:

```text
data/anzctr/eligibility/trials/original_curations
```

## Future Pipeline Command

Once the remaining ANZCTR eligibility workflow is implemented, add one Make target
or shell entrypoint that runs the full pipeline from raw ANZCTR input onward.
