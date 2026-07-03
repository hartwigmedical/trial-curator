# v2 Agentic Pipeline — Handover

- **As of:** 2026-07-03 (Fri). **Resume:** Mon 2026-07-06.
- **Branch:** `AUS-328-Aus-trial-universe-v2`
- **Pre-rewrite fallback tag:** `aus-trial-eligibility-path-resource-generation-v1` (code only — NOT data).
- **Full design:** `docs/v2_agentic_pipeline_spec.md` (read this for the "why").

## TL;DR
Slice 1 of the v2 rewrite is **built and verified**: a reusable agentic runtime (unified OpenAI client + Agent + Workflow engine) with one task on it — **eligibility extraction → a DNF table**, working for **both ctgov and anzctr**. 19 unit tests pass; live runs confirmed end-to-end. Everything is in the working tree, **uncommitted** (you commit). Nothing from the old eligibility path was changed except the already-committed `ui/` deletion.

## Quickstart (do this first on Monday)
Runs in the conda env `trial_curator` (auto-selected by the script; `OPENAI_API_KEY` auto-loaded from `.env`).

```bash
make agentic-eligibility-extract ID=NCT06881784                   # one ctgov trial
make agentic-eligibility-extract ID=ACTRN12605000108617 SOURCE=anzctr
make agentic-eligibility-extract SELECTED=6                       # 3 ctgov + 3 anzctr
make agentic-tests                                                # unit tests only (no API)
#   options: NO_JUDGE=1 (cheaper) · MODEL=<name>
```
Every extract run: **runs unit tests first** → extracts → writes a **timestamped log** to `data/agentic/logs/` → writes the DNF TSV to `data/agentic/eligibility_extraction/eligibility_dnf_<source>_<id> | _selected<N>.tsv`.

## What's built (slice 1)
```
aus_trial_universe/agentic/
  core/
    client.py       # unified OpenAI client: pydantic structured outputs, retries, response cache, tracing
    agent.py        # Agent = prompt + output schema + model config + client (one LLM job)
    workflow.py     # fan_out() + refine() (bounded check→repair loop). Orchestrator is plain Python.
    pipeline_io.py  # COPIED from eligibility_path (dated-file/version-dir selection standard)
  tasks/eligibility_extraction/
    schema.py       # DnfRow + agent I/O schemas
    agents.py       # extractor, cancer_type→OncoTree, faithfulness judge (prompts)
    workflow.py     # extract_eligibility(): extractor → fan-out OncoTree → consolidate → check+judge → refine
    run.py          # CLI: --id / --selected, ctgov+anzctr loaders, TSV writer
tests/agentic/...   # 19 tests, all fake-client (no API/network needed)
scripts/agentic/pipeline.sh   # make driver (python-picker, .env, tests-preflight, logging)
```
The design is **pattern B**: code owns the control flow; LLMs power the individual stages. Output is **DNF** (Disjunctive Normal Form): one row = one satisfiable conjunction; rows for a (trial, cohort) are ORed.

## Verified
- 19/19 unit tests green.
- Live ctgov (`NCT06881784`): 9 DNF rows (KRAS/NRAS/HRAS × codon 12/13/61) — DNF OR-expansion working; refine loop fires when the judge rejects.
- Live anzctr (`ACTRN12605000108617`): 4 rows (esophageal/GEJ tumour types → correct OncoTree codes), tolerant to typos in source data.
- `SELECTED=4`: 2 ctgov + 2 anzctr, source-tagged logs.

## Uncommitted → commit plan (you handle all commits)
Already committed: `a69dc69` (ui/ deletion). Working tree:
- **New:** everything under `aus_trial_universe/agentic/`, `tests/agentic/`, `scripts/agentic/pipeline.sh`, `docs/v2_agentic_pipeline_spec.md`, this handover.
- **Modified:** `Makefile` (agentic targets), `requirements.txt` (openai/pydantic/python-dotenv).
- Suggested single commit (your style): `aus_trial_universe: v2 agentic runtime + eligibility extraction slice 1 (ctgov & anzctr)`.
- `data/agentic/...` outputs/logs are gitignored (not committed).

## Open items / next steps
- **Task #6** — revisit whether `--selected` stays or is inspection-only (temporary).
- **Task #7** — before `eligibility_path` is removed, COPY into agentic: the selection filter (ctgov DRUG/BIOLOGICAL; anzctr "Treatment: Drugs" + POTTR exemption + manual-removal) and the downloaders. Agentic currently reads the versioned inputs + `*_field_extractions.csv` that eligibility_path produces.
- **Prompt tuning** — `cancer_type` cells sometimes carry staging/context ("locally advanced or metastatic NSCLC"); tighten to the tumour type. Minor OncoTree naming variance run-to-run (e.g. GEJ vs EGJA; codes stable) — the DiskCache + downstream curation are meant to tame this.
- **More columns** — add `molecular_signature`, `prior_therapy` as further specialists.
- **Slice 2** — free-text → structured **cohort alignment** (slice 1 treats each trial as one cohort=`all`).
- **Deferred design (spec §10)** — the new run-comparison method (replaces old `qa/`) and the final reproducibility stance.

## Key decisions & gotchas (don't re-learn these)
- **SDK:** `openai 1.60.1` predates the Responses API → the client uses `beta.chat.completions.parse(response_format=<pydantic>)`. `client.chat.completions.parse` does NOT exist at this version.
- **Env:** run/verify in conda `trial_curator` (`/opt/anaconda3/envs/trial_curator/bin/python`); the default shell `python3` lacks openai/pydantic.
- **Determinism:** the response **cache** is the deterministic layer; `temperature`/`seed` are omitted by default (gpt-5.x is a reasoning model and rejects `temperature`).
- **Copy, don't import** from `eligibility_path` — it will be removed; agentic owns copies (e.g. `pipeline_io.py`).
- **ANZCTR ids:** the CSV stores bare digits (`12605000108617`); `--id` accepts either bare or `ACTRN`-prefixed.
- **Data safety:** `data/` is gitignored (31 GB, untouched by branches). Precious derived outputs backed up at `~/WorkProjects/trial-curator-data-backup/derived-20260703/`. Never `git clean -fdx` here.

## Pointers
- Spec: `docs/v2_agentic_pipeline_spec.md`
- Data: raw inputs `data/trial_inputs/{ctgov,anzctr}/…`; v2 outputs `data/agentic/…`
- Old eligibility path remains in-tree as reference until superseded (retirement schedule in spec §9).
