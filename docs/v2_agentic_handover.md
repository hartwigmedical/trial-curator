# v2 Agentic Pipeline — Handover

- **As of:** 2026-07-06 (Mon).
- **Branch:** `AUS-328-Aus-trial-universe-v2`
- **Pre-rewrite fallback tag:** `aus-trial-eligibility-path-resource-generation-v1` (code only — NOT data).
- **Full design:** `docs/v2_agentic_pipeline_spec.md` (the "why"); **field/column spec of record:** `docs/v2_field_source_audit.md`.

## Locked decisions — stage-II extraction rework (2026-07-06)
Full detail in `docs/v2_field_source_audit.md` + memory `v2-stage2-extraction-decisions`. Summary:
- **Scope = extraction only.** No OncoTree mapping, no finding-model conversion (later stages). The slice-1 `oncotree_name`/`oncotree_code` columns + OncoTree agent are **removed**.
- **Six columns:** `cancer_type`, `gene_alteration`, `molecular_signature`, `molecular_biomarker`, `prior_therapy`, `drug`. Definitions from `pydantic_curator/criterion_schema.py` (see audit §2 + memory for the taxonomy + edge rules: HER2/MMR, histology-in-cancer_type, out-of-scope criteria ignored).
- **Provenance:** every cell = `value [SRC1; SRC2]` — cite ALL sources, `;`-delimited (audit §3 vocab; CTGov adds `INTERVENTIONS MODULE`, ANZCTR adds `EXCLUSION CRITERIA` + `INTERVENTIONS`).
- **Negation = inline `NOT(...)`**; same-column carve-outs become one ANDed cell (`solid tumour AND NOT(melanoma)`); only OR-alternatives split rows (DNF stays pure conjunctions).
- **Cohort = Option A, cohort-AWARE extraction:** CTGov cohorts from `armGroups` (deterministic); ANZCTR via a conservative **cohort-detection agent** (default single; only splits on explicit distinct-eligibility groups). The extractor **assigns each criterion to a cohort or "trial-wide"**; a cohort's effective eligibility = trial-wide ∧ cohort-specific (cross-product distribution, representation A). Each output row is self-contained and carries its cohort's drug.
- **Drug:** RAW names, **no normalization** yet. CTGov = deterministic arm `interventionNames`. **ANZCTR = LLM drug agent** (reads `INTERVENTIONS` text).
- **Reviewer = 5-agent parallel panel** (cancer_type [strengthened: reject false-positive tumours mentioned only in prior-therapy/history/exclusion context], molecular [+ correct column per taxonomy], prior_therapy, drug [advisory], structural [DNF + cohort assignment]). Refine gates on the 4 eligibility/structural reviewers (what re-extraction can fix); drug reviewer is advisory.
- **Agent inventory:** CTGov extraction = 1 (eligibility); ANZCTR extraction = 3 (eligibility · drug · cohort-detection); + shared 5-reviewer panel.

## Shelved for later review
- **ANZCTR `DRUG_rxnorm_matched` (+ `llm_drugs_*`) columns** — the pre-computed RxNorm drug approach is shelved; ANZCTR drug is now LLM-extracted from `INTERVENTIONS`. Revisit whether to fold RxNorm back in (e.g. as a cross-check or the normalization stage).

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
Every extract run: **runs unit tests first** → extracts → writes a **timestamped log** to `data/agentic/log/` → streams the DNF TSV (per-trial) to `data/agentic/output/eligibility_dnf_<source>_<id> | _selected<N>.tsv`.

## What's built (slice 1)
```
aus_trial_universe/agentic/
  core/
    client.py       # unified OpenAI client: pydantic structured outputs, retries, response cache, tracing
    agent.py        # Agent = prompt + output schema + model config + client (one LLM job)
    workflow.py     # fan_out() + refine() (bounded check→repair loop). Orchestrator is plain Python.
    pipeline_io.py  # COPIED from eligibility_path (dated-file/version-dir selection standard)
  tasks/extraction/
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
- **SDK:** targets `openai >= 2.x` (env has 2.44.0). The client uses the stable `chat.completions.parse(response_format=<pydantic>)`, falling back to `beta.chat.completions.parse` on older SDKs. The Responses API is available at 2.x but the client deliberately stays on Chat Completions parse (proven, sufficient).
- **Env:** run/verify in conda `trial_curator` (`/opt/anaconda3/envs/trial_curator/bin/python`); the default shell `python3` lacks openai/pydantic.
- **Determinism:** the response **cache** is the deterministic layer; `temperature`/`seed` are omitted by default (gpt-5.x is a reasoning model and rejects `temperature`).
- **Copy, don't import** from `eligibility_path` — it will be removed; agentic owns copies (e.g. `pipeline_io.py`).
- **ANZCTR ids:** the CSV stores bare digits (`12605000108617`); `--id` accepts either bare or `ACTRN`-prefixed.
- **Data safety:** `data/` is gitignored (31 GB, untouched by branches). Precious derived outputs backed up at `~/WorkProjects/trial-curator-data-backup/derived-20260703/`. Never `git clean -fdx` here.

## Pointers
- Spec: `docs/v2_agentic_pipeline_spec.md`
- Data: raw inputs `data/trial_inputs/{ctgov,anzctr}/…`; v2 outputs `data/agentic/…`
- Old eligibility path remains in-tree as reference until superseded (retirement schedule in spec §9).
