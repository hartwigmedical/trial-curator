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

## Dependencies

Install Python dependencies with `pip install -r requirements.txt`. The ANZCTR
initial download uses `curl_cffi` to clear the registry's Cloudflare challenge
(a plain HTTP client or headless browser is blocked).

## Run

Run all eligibility-path tests:

```bash
make eligibility-path-tests
```

Run CTGov, ANZCTR, or both:

```bash
make eligibility-path-ctgov
make eligibility-path-anzctr
make eligibility-path-run-all-trials-download-w-llm
make eligibility-path-run-all-trials-download
make eligibility-path-run-all
```

`make eligibility-path-run-all-trials-download-w-llm` is the most complete
workflow: it creates fresh CTGov and ANZCTR `version_<ddmmyyyy>` input folders,
runs ANZCTR LLM drug review, curates only missing per-trial `.py` files, writes
final resources, and recursively appends missing POTTR trials until convergence.

`make eligibility-path-run-all-trials-download` is the same fresh-download
recursive workflow without ANZCTR LLM drug review.

`make eligibility-path-run-all` uses the newest existing
`data/trial_inputs/<registry>/input_trials/version_<ddmmyyyy>` folders without
refreshing the initial downloads, then runs the same no-LLM recursive workflow.
It writes the combined final trial and cohort resources:

```text
data/eligibility_path/exports/final/eligibility_trial_resource_<ddmmyyyy>.tsv
data/eligibility_path/exports/final/eligibility_cohort_resource_<ddmmyyyy>.tsv
```

The combined export computes missing POTTR AU trials in memory and logs them.
`make eligibility-path-run-all` downloads those missing trials as
`02_pottr_append` inputs and reruns until no new missing POTTR trials remain.

POTTR-listed trials must always appear in the final output, so the extract/select
steps exempt them from the cohort (drug-intervention) filter that otherwise keeps
only `Treatment: Drugs` (ANZCTR) / `DRUG`/`BIOLOGICAL` (CTGov) trials. The POTTR
trial set is loaded from the POTTR `trial_eligibility.AU` / `trial_registry.AU`
sources (best-effort: if unreachable, the run proceeds without exemption). POTTR
trials still respect the manual-removal list. If a residual set of POTTR trials
cannot be resolved by downloading (e.g. withdrawn IDs), the recursive workflow
converges with a warning instead of failing.

The pipeline runs unit tests before generating files unless
`ELIGIBILITY_SKIP_TESTS=1` is set.

The optional ANZCTR LLM drug review is not part of `make eligibility-path-run-all`
or `make eligibility-path-run-all-trials-download`. Use the LLM-enabled fresh
download workflow when those columns should be regenerated:

```bash
make eligibility-path-run-all-trials-download-w-llm
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
Diagnostic reports live under a `diagnostics/` subfolder and start their own
local numbering, for example
`gene_alteration/diagnostics/01a_gene_alteration_conflicts_trial_level.tsv`.

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
