# Eligibility Path

This package builds registry-level eligibility resources for CTGov and ANZCTR.
It starts from trial input files plus pydantic-curator `.py` outputs, applies
shared cancer type, gene alteration, and molecular signature processing, then
writes dated combined trial-level and cohort-level TSVs across both registries.

## Layout

```text
aus_trial_universe/eligibility_path/
  ctgov/     # CTGov-specific input adaptation and resource exports
  anzctr/    # ANZCTR-specific input adaptation and resource exports
  shared/    # Registry-independent eligibility logic
  qa/        # Optional output-diff/audit utilities
```

The data layout is:

```text
data/trial_inputs/<registry>/input_trials
data/trial_inputs/<registry>/extracted_trials
data/trial_inputs/<registry>/eligibility_curations
data/eligibility_path/resources
data/eligibility_path/exports/intermediates/<registry>
data/eligibility_path/exports/final/<registry>
data/eligibility_path/exports/final/eligibility_trial_resource_<ddmmyyyy>.tsv
data/eligibility_path/exports/final/eligibility_cohort_resource_<ddmmyyyy>.tsv
```

`data/trial_inputs` and `data/eligibility_path/resources` are inputs. The
`data/eligibility_path/exports` tree is generated.

## Run

Run all eligibility-path tests:

```bash
make eligibility-path-tests
```

Run CTGov, ANZCTR, or both:

```bash
make eligibility-path-ctgov
make eligibility-path-anzctr
make eligibility-path-run-all
```

`make eligibility-path-run-all` runs both registries and writes the combined final
trial and cohort resources:

```text
data/eligibility_path/exports/final/eligibility_trial_resource_<ddmmyyyy>.tsv
data/eligibility_path/exports/final/eligibility_cohort_resource_<ddmmyyyy>.tsv
```

The pipeline runs unit tests before generating files unless
`ELIGIBILITY_SKIP_TESTS=1` is set.

The optional ANZCTR LLM drug review is not part of `make eligibility-path-run-all`.
Use the LLM-enabled combined workflow when those columns should be regenerated:

```bash
make eligibility-path-run-all-w-llm
```

## Cleaning Generated Outputs

It is safe to delete generated files under:

```text
data/eligibility_path/exports/intermediates
data/eligibility_path/exports/final
```

Intermediate TSV filenames are stage-numbered. Distinct stages use `01_`,
`02_`, and so on; paired trial/cohort outputs share the same number with
`a` for trial level and `b` for cohort level, for example
`04a_gene_alteration_trial_level.tsv` and
`04b_gene_alteration_cohort_level.tsv`.

Preview or remove generated TSV files from those folders:

```bash
make eligibility-path-clean-dry-run
make eligibility-path-clean
```

Do not delete source inputs under:

```text
data/trial_inputs/<registry>/input_trials
data/trial_inputs/<registry>/eligibility_curations
data/eligibility_path/resources
```

For ANZCTR, also keep:

```text
data/trial_inputs/anzctr/extracted_trials/anzctr_field_extractions.csv
```

if it contains completed LLM drug-review annotations. Rerunning deterministic
drug annotation preserves existing LLM review columns by `ACTRN`, but deleting
that file deletes the annotations.
