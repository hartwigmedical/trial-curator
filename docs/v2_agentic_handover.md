# v2 Agentic Pipeline — Handover

- **As of:** 2026-07-08. **Branch:** `AUS-328-Aus-trial-universe-v2`.
- **Pre-rewrite fallback tag:** `aus-trial-eligibility-path-resource-generation-v1` (code only — NOT data).
- **Run/setup guide:** `docs/agentic/combined_agentic_run.md` (all make commands + environment).
- **Design + fields + schema:** `docs/v2_agentic_pipeline_spec.md` (single spec); **diagram:** `docs/v2_workflow_diagram.html`.
- **Decisions (memory):** `v2-agentic-rewrite-ground-rules`, `v2-stage2-extraction-decisions`, `v2-mapping-stage-decisions`.

## Next up (start here)
Two tasks, in priority order:

1. **[BIG] Act on the user's review of the 10-complex-trial run.** The user is reviewing the output of the
   complex set (`data/agentic/analysis/review_trials_ids.txt`, 10 trials with hard cohort × cancer × gene
   combos) and will bring **specific feedback** into the next session. Wait for it; that feedback drives the
   next round of prompt/logic tuning. Re-run with
   `make agentic-run IDS=$(cat data/agentic/analysis/review_trials_ids.txt)`.

2. **Rework the run log — clarity, no fluff.** The user's requirements: the log must clearly show (a) the
   **stage** (EXTRACTION / MAPPING / DRUG), (b) the **trial** being processed, and (c) a clear attribution of
   **what the doer agent said vs what the reviewer agent(s) said**. Strip anything not needed.
   - **Where it's emitted:** `run.py:60` sets the format (`%(asctime)s %(levelname)s %(message)s` — the
     per-line timestamp+level is likely the "fluff" to drop) and per-trial header logger `agentic.pipeline`
     (`run.py:94`); final summary is `print()` at `run.py:148,150`. Stage lines come from
     `tasks/extraction/workflow.py` (tags `COHORTS/EXTRACT/REVIEW/REFINE/RULE-CHECK/CONSOLIDATE/RESULT`) and
     `tasks/mapping/workflow.py` (`ONCOTREE/DRUG` + finding-model). `core/client.py` `_emit_trace` is the
     LLM-call tracer. `scripts/agentic/pipeline.sh:102` tees stdout+stderr to the one log file.
   - **Gaps to close:** no top-level STAGE banner (tags are per-line, not grouped); doer vs reviewer isn't
     visually distinct (EXTRACT = doer, REVIEW symbols = reviewer, but mapping shows only the mapper, not the
     reviewer verdict); every line carries a redundant timestamp+LEVEL prefix. Keep it lean.

## TL;DR
The v2 rewrite is a **complete two-stage agentic pipeline**, end-to-end verified on ctgov + anzctr:
**extract → map → drug enrichment**, in one streamed pass, via a single command (`make agentic-run`).
Pattern B throughout: deterministic Python owns control flow; the LLM fills the doer/reviewer slots.
**53 unit tests pass** (all fake-client, no API). Output is a DNF table (one row = one satisfiable
(trial, cohort) conjunction; rows ORed, cells ANDed, exclusions inline `NOT(...)`).

## Quickstart
Conda env `trial_curator` (auto-selected); `OPENAI_API_KEY` auto-loaded from `.env`. Needs `openai>=2.x`.
```bash
make agentic-run ID=NCT06881784                 # one trial (source auto-detected)
make agentic-run IDS=NCT1,ACTRN2,NCT3           # a specific set
make agentic-run                                # ALL trials
make agentic-clean                              # wipe data/agentic/{output,log,cache}
make agentic-tests                              # 53 unit tests, no API
#   options: MODEL=<name>  NO_JUDGE=1 (skip extraction panel)  NO_REVIEW=1 (skip mapping/drug reviewers)
```
One run → **one output** `data/agentic/output/trial_resource_<id|timestamp>.tsv` + **one log** `data/agentic/log/…`.
Full detail: `docs/agentic/combined_agentic_run.md`.

## What's built
```
aus_trial_universe/agentic/
  run.py                     # PIPELINE ORCHESTRATOR: per trial extract -> map -> drug, stream one output
  core/
    client.py                # LlmClient: .parse() (chat.completions) + .research() (Responses API web_search); cache, retries, tracing
    agent.py                 # Agent = prompt + schema + model (+ web_search flag) bound to the client
    workflow.py              # generic fan_out() + refine() (bounded check->repair loop)
    pipeline_io.py           # dated-file / version-dir selection (copied from eligibility_path)
  tasks/extraction/          # STAGE I: free text -> DNF eligibility rows
    loaders.py               # ctgov/anzctr assembly + cohort enumeration (arm_type, drug); load_trials(id/ids/all)
    agents.py                # cohort-aware extractor + 5-reviewer panel + anzctr drug/cohort agents
    schema.py, workflow.py   # DnfRow / Cohort; extract_trial() = extract -> panel -> refine -> distribute
  tasks/mapping/             # STAGE II: enrich the DNF rows (LLM mapper -> reviewer each)
    agents.py, schema.py     # oncotree / gene / signature mappers + drug curator (web search) + reviewers
    workflow.py              # map_cancer_types, map_gene_alterations, map_molecular_signatures, curate_drugs
  tools/
    oncotree.py              # OncoTree vocab + code validator
    finding_model.py         # finding-model grammar + syntax validator
tests/agentic/               # 53 tests (fake-client)
scripts/agentic/pipeline.sh  # driver: python-pick, .env, tests-preflight, log tee; subcommands run|clean|tests
docs/agentic/combined_agentic_run.md   # run/setup guide
```

## Output schema (19 columns)
`trialId, cohort, arm_type, cancer_type, oncotree_name, oncotree_code, gene_alteration,
gene_alteration_findingmodel, molecular_signature, molecular_signature_findingmodel, molecular_biomarker,
prior_therapy, drug, main_drugs, auxiliary_drugs, pottr_drug_class, drug_class, tga_status, pbs_status`.
See `combined_agentic_run.md` §Output Schema for per-column notes.

## Locked decisions (don't re-litigate)
- **Extraction (memory `v2-stage2-extraction-decisions`):** 5 eligibility columns + drug; taxonomy from
  `pydantic_curator/criterion_schema.py`; multi-source `[a; b]` provenance; inline `NOT()`; cohort Option A
  (one per arm, cohort-aware, trial-wide ∧ cohort-specific distribution); 5-reviewer panel (drug advisory).
- **Mapping (memory `v2-mapping-stage-decisions`):** every procedure is an LLM mapper/curator → reviewer.
  **Hold-out rule:** prompts carry grammar/ontology + ~8–12 examples only; the curated resources are
  **held-out verification data**, checked **manually** later (esp. gene_alteration) — not ingested wholesale.
- **Drug:** `main_drugs`/`auxiliary_drugs` (trial-level, main first, regimen allowed); `pottr_drug_class` +
  `drug_class` + `tga_status` (Approved+year) + `pbs_status` for the **main drug(s)**, via web search.
- **arm_type** per cohort from CTGov `armGroups[].type`.
- **One output + one log**, streamed per trial; `--selected` retired; modes = `ID` / `IDS` / all.

## Verified (live)
- `NCT07099898` (SCLC, 2 arms): main=`risvutatug rezetecan` (investigational, TGA `Not approved`),
  auxiliary=`Topotecan`; POTTR + drug_class + PBS populated via web search; arm_type EXPERIMENTAL/ACTIVE_COMPARATOR.
- `NCT05417594` (BRCA basket, 83 rows): oncotree + `SmallVariant[gene=BRCA1 & …]` finding-model.
- Legacy comparison spot-checks: BREAST=BREAST, MTAP `HOM_DEL` matches; deviations marginal.

## Ticked off (2026-07-08)
Former TODOs now closed:
- `--selected` retired → one unified `make agentic-run` (`ID` / `IDS` / all), **one output + one log**, streamed per trial.
- All **5 eligibility columns** (was cancer_type + gene_alteration only) + **arm_type**.
- **Cohort-aware extraction** (the former "cohort alignment / slice 2"): one cohort per arm, trial-wide ∧ cohort-specific.
- **Mapping stage built:** OncoTree + finding-model (gene & signature) with grammar/validators.
- **Drug enrichment built:** main/auxiliary, POTTR + non-POTTR `drug_class`, TGA (+year) + PBS via **web search**.
- **Negation mechanism** decided → inline `NOT(...)`.
- **openai → 2.x** (Responses API `web_search`), enabling the drug research; parse path verified unbroken.
- **Docs consolidated** → one spec (`v2_agentic_pipeline_spec.md`, absorbed the field-source audit); diagram rebuilt.

## Shelved / open
- **Stage-I ingestion** — download → drug-filter + POTTR-append → retire-missing is **not yet in agentic**; the
  pipeline currently reads the versioned inputs the legacy path produces. (Biggest remaining piece.)
- **ANZCTR `DRUG_rxnorm_matched`** shelved — ANZCTR drug is now LLM-extracted; revisit RxNorm as a cross-check.
- **OncoTree granularity** — SCLC-subtype trials occasionally flag "unfaithful" (output still written); tune vs
  the manual review of the 10-trial set (`data/agentic/analysis/review_trials_ids.txt`).
- **Run-comparison method** (spec §12) — still deferred; verification of the mapping is currently manual.
- **`eligibility_path` retirement** — legacy stays in-tree as the resource source + reference until superseded.

## Gotchas
- **SDK:** `openai 2.44.0`. `.parse()` uses `chat.completions.parse`; `.research()` uses the Responses API
  `web_search` tool (`client.responses.parse(tools=[{"type":"web_search"}], text_format=<pydantic>)`).
- **Env:** conda `trial_curator` (`/opt/anaconda3/envs/trial_curator/bin/python`); default `python3` lacks the deps.
- **Determinism:** the response cache is the deterministic layer; `temperature`/`seed` omitted (gpt-5.x rejects them).
- **Copy, don't import** from `eligibility_path`; agentic owns its copies (e.g. `pipeline_io.py`, the resource reads).
- **Data safety:** `data/` is gitignored (~31 GB). Never `git clean -fdx`. `make agentic-clean` is scoped to `data/agentic/`.
