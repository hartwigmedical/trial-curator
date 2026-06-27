# Handover — AUS-328 eligibility path (2026-06-27)

Branch: `AUS-328-Aus-trial-universe`. **All work below is uncommitted** in the working
tree (nothing committed this session). Test env is `/opt/anaconda3/envs/trial_curator`
(Python 3.13) — NOT `~/anaconda3` (3.10). Run tests with
`/opt/anaconda3/envs/trial_curator/bin/python -m pytest tests/eligibility_path/ -q`.

Durable facts are already in auto-loading memory (`MEMORY.md` index). This doc adds the
transient session state + next steps. See memory files: `pottr-trials-exempt-from-cohort-filters`,
`resource-coverage-audit`, `anzctr-download-curl-cffi`, `feedback-tight-scope-keep-make-working`,
`repo-restructuring-in-progress`.

## What this session delivered (most recent first)

### 1. POTTR trials exempt from the cohort (drug) filter — DONE, tested
Requirement (user): "all pottr trials should be included in the final output" — POTTR
trials must not be subject to the same filters as the initial download.
- Diagnosis: the ONLY drop points in the whole eligibility path are the two extract/select
  steps (drug-intervention filter + manual-removal). Everything downstream is left-joins,
  no drops. POTTR trials were downloaded but dropped at extract.
- Fix: POTTR-listed trials now bypass the drug filter at both extract steps.
  - `shared/trial_resource/combined_trial_resource_export.py`: new `load_pottr_trial_ids(registry=...)`
    + `load_pottr_trial_ids_best_effort(...)` (unions POTTR `trial_eligibility.AU` +
    `trial_registry.AU`, normalizes, filters by registry).
  - Injected at the CLI boundary only (`ctgov/.../ii_extract_fields.py:main`,
    `anzctr/.../iii_extract_drugs.py:main`, `anzctr/.../ii_select_trials_and_fields.py:main`)
    and threaded down via a `pottr_trial_ids` param. Pure functions default `None`/no-network
    so unit tests stay offline.
  - Keep condition: non-drug trial kept iff its id is POTTR-listed (CTGov `nctId`; ANZCTR
    **ACTRN**, not the internal "TRIAL ID").
- Real POTTR set verified: 515 CTGov + 66 ANZCTR = 581, all NCT/ACTRN (0 unsupported
  registries). None overlap the 4-entry manual-removal list, so removal stays in force and
  blocks zero POTTR trials in practice.
- Tests added: `test_ctgov_field_extractions.py`, `test_extract_fields.py` (anzctr exemption
  + removal-interaction guard), `test_combined_trial_resource_export.py::test_load_pottr_trial_ids_*`.
  Updated two `test_extract_drugs.py` mocks for the new kwarg. README updated.

### 2. Recursive workflow converges instead of crashing — DONE, tested
`shared/workflow/recursive_end_to_end_workflow.py:run_recursive_workflow` previously raised
`RuntimeError` when POTTR trials stayed missing. Now warns + returns the unresolved set so
`make` succeeds. Test: `test_recursive_end_to_end_workflow.py::test_recursive_workflow_converges_without_raising_on_unresolvable_missing`.
(After fix #1 the residual should be ~0 except genuinely un-downloadable IDs.)

### 3. Resource coverage/gap audit + accretion — DONE earlier this session (user to test)
New `shared/resource_curation/{coverage,audit}.py`. Flags fresh-data-vs-curated-resource gaps
as fill-ready templates that accrete into NEW timestamped resource versions (never overwrites).
`make eligibility-path-resource-audit` standalone; auto-runs each pipeline command in two phases
(`--phase accrete` before processing, `--phase report` after) via `scripts/eligibility/pipeline.sh`.
Domains: cancer-type, gene-alteration (regenerates `Mapping_args` from `_curation` cols),
molecular-signature. See memory `resource-coverage-audit` for the full design. User said they
will test this themselves.

### Also from the summarized earlier part of this session
- ANZCTR downloader rewritten to `curl_cffi` (Cloudflare bypass, all.xls + local filter) —
  `anzctr/.../i_download_trials.py` (untracked new file). See memory `anzctr-download-curl-cffi`.
- Cancer-type manual-overwrite changed from positional concat to key-based left join
  (`shared/cancer_types/final_determination/primary_vs_conditions.py`).
- Run logging to file; pydantic-curator logs quieted to one-line-per-trial.

## Test status
`154 passed` for `tests/eligibility_path/` (last run this window). One pre-existing pandas
FutureWarning in `combined_trial_resource_export.py:327` (fillna downcast) — not from this work.

## Pending / next steps
1. **Real end-to-end run** (user's job): `make eligibility-path-run-all-trials-download` and
   confirm the missing-POTTR count drops toward 0. This is the only thing that proves #1 e2e —
   unit tests can't (POTTR load is network).
2. **Open decision** (do NOT change without user confirmation): POTTR trials currently still
   respect the manual-removal list. It excludes zero POTTR trials today, but if the user wants
   POTTR to override removal too, it's a one-line change in both extract steps.
3. User to test the resource-audit feature (#3).
4. **Nothing is committed.** When the user asks, commit on this branch (not master). Co-author
   trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

## Gotchas
- POTTR loading is best-effort: if the GitHub source is unreachable, the run proceeds WITHOUT
  exemption (logs a warning) — POTTR trials would then be dropped + reported missing, and the
  workflow converges gracefully. Don't mistake "no network" for a logic bug.
- `make` uses `/opt/anaconda3/envs/trial_curator`. Installing into the wrong conda env was a
  real past failure (curl_cffi ModuleNotFoundError).
- User preference: tight scope — confine edits to the module + its tests, preserve CLI/signatures
  so `make` runs out of the box.
- Big repo restructuring is in progress (as of 2026-06-22); some hard-coded paths in older
  memories may be stale — verify before relying on them.
