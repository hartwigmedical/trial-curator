# v2 Agentic Pipeline — Handover

- **As of:** 2026-07-10. **Branch:** `AUS-328-Aus-trial-universe-v2`.
- **Pre-rewrite fallback tag:** `aus-trial-eligibility-path-resource-generation-v1` (code only — NOT data).
- **Run/setup guide:** `docs/agentic/combined_agentic_run.md` (all make commands + environment).
- **Design + fields + schema:** `docs/v2_agentic_pipeline_spec.md` (single spec); **diagram:** `docs/v2_workflow_diagram.html`
  (published Artifact: https://claude.ai/code/artifact/671df104-6474-4c32-b78c-45f4b65d063f — on any diagram change,
  **overwrite** that URL via `scripts/publish_diagram_artifact.sh`; see memory `workflow-diagram-artifact`).
- **Decisions (memory):** `v2-agentic-rewrite-ground-rules`, `v2-stage2-extraction-decisions`, `v2-mapping-stage-decisions`.

## Next up (start here)
The user is reviewing output quality and will bring **more changes tomorrow (from 2026-07-11)**. Known items:

1. **Extraction convergence on hard trials.** `NCT05009992` still finishes `faithful=False` after 3 attempts —
   deep sub-cohort scoping (1A/1B/2A/2B…). Incremental repair helped (feeds the doer its own prior table +
   only the flagged issues) but didn't fully converge. Consider more attempts, per-dimension "resolved"
   tracking, or splitting sub-cohorts up front.
2. **`H3K27-altered` → empty finding-model nuance.** A bare `H3K27-altered` cell mapped to `""` while
   `H3K27M` and `H3K27-altered AND BRAF V600E` mapped correctly — inconsistent drop. Gene-mapper judgement
   tune (held-out manual-verification territory).
3. **Two run sets awaiting review:** the hard set (`data/agentic/analysis/review_trials_ids.txt`) and a new
   **typical/standard set** (`data/agentic/analysis/typical_trials_ids.txt`, 10 average CTGov cancer trials).
   Re-run either with `make agentic-run IDS=$(cat <that file>)`.

   *(Run-log rework and the 2026-07-10 output-quality fixes are done; see "Ticked off".)*

## TL;DR
The v2 rewrite is a **complete two-stage agentic pipeline**, end-to-end verified on ctgov + anzctr:
**extract → map → drug enrichment**, in one streamed pass, via a single command (`make agentic-run`).
Pattern B throughout: deterministic Python owns control flow; the LLM fills the doer/reviewer slots.
**54 unit tests pass** (all fake-client, no API). Output is a **DNF (disjunctive normal form)** table — one row =
one satisfiable (trial, cohort) conjunction; rows are ORed, cells within a row ANDed, exclusions inline `NOT(...)`.

## Quickstart
Conda env `trial_curator` (auto-selected); `OPENAI_API_KEY` auto-loaded from `.env`. Needs `openai>=2.x`.
```bash
make agentic-run ID=NCT06881784                 # one trial (source auto-detected)
make agentic-run IDS=NCT1,ACTRN2,NCT3           # a specific set
make agentic-run                                # ALL trials
make agentic-clean                              # wipe data/agentic/{output,log,cache}
make agentic-tests                              # 54 unit tests, no API
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
  core/logfmt.py             # shared run-log formatting (stage banners + doer/reviewer blocks)
  tools/
    oncotree.py              # OncoTree vocab + code validator + hierarchy (ancestors/is_subcode); 3 sentinels
    finding_model.py         # finding-model grammar + syntax/logic validator (dup + self-contradiction)
tests/agentic/               # 54 tests (fake-client)
scripts/agentic/pipeline.sh  # driver: python-pick, .env, tests-preflight, log tee; subcommands run|clean|tests
docs/agentic/combined_agentic_run.md   # run/setup guide
```

## Output schema (21 columns)
`trialId, cohort, arm_type, cancer_type, oncotree_name, oncotree_code, gene_alteration,
gene_alteration_findingmodel, molecular_signature, molecular_signature_findingmodel, molecular_biomarker,
prior_therapy, drug, main_drugs, auxiliary_drugs, pottr_drug_class, drug_class, tga_status, pbs_status,
tga_detail, pbs_detail`.
`tga_status`/`pbs_status` are now **per main drug** (`<drug>: Approved` / `<drug>: Not approved`);
`tga_detail`/`pbs_detail` (new) carry the **year + evidence + official source link** per drug.
See `combined_agentic_run.md` §Output Schema for per-column notes.

## Locked decisions (don't re-litigate)
- **Extraction (memory `v2-stage2-extraction-decisions`):** 5 eligibility columns + drug; taxonomy from
  `pydantic_curator/criterion_schema.py`; multi-source `[a; b]` provenance; inline `NOT()`; cohort Option A
  (one per arm, cohort-aware, trial-wide ∧ cohort-specific distribution); 5-reviewer panel (drug advisory).
- **Mapping (memory `v2-mapping-stage-decisions`):** every procedure is an LLM mapper/curator → reviewer.
  **Hold-out rule:** prompts carry grammar/ontology + ~8–12 examples only; the curated resources are
  **held-out verification data**, checked **manually** later (esp. gene_alteration) — not ingested wholesale.
- **OncoTree logic (2026-07-10):** exactly **3 permitted non-OncoTree terms** — `Pan-cancer`, `solid tumour`,
  `Haematological malignancy` (no `[None]`; a non-cancer value maps to empty). Deterministic validator +
  prompts forbid `X AND X`, `X AND NOT(X)`, a broad term ANDed with its own subtype (→ OR / drop umbrella),
  and a subtype ANDed with its OncoTree parent (`tools/oncotree.py:is_subcode`); `NOT(cancer type)` kept minimal.
- **Cancer_type (2026-07-10):** CONDITIONS is authoritative; **never AND two different cancer types**; drop a
  broad umbrella when the trial is clearly one specific type; genuinely different types are separate OR rows.
- **gene finding-model (2026-07-10):** never `X & NOT(X)` or duplicate terms; a NOT() qualified by something
  finding-model can't express (e.g. anatomic location) is **omitted**. Inclusion+exclusion of the same
  alteration across cohorts must be **split into rows** upstream (extraction molecular/structural reviewers).
- **Drug (2026-07-10):** `main_drugs` = the **investigational agent(s) under study by judgement** (not the
  whole regimen — backbone/SoC/comparator/placebo go to `auxiliary_drugs`); `pottr_drug_class` + `drug_class`;
  `tga_status`/`pbs_status` **per main drug** (Approved/Not approved) + `tga_detail`/`pbs_detail`
  (year + evidence + official link), via web search.
- **Refine = incremental repair (2026-07-10):** on a gating FAIL the extractor gets its OWN prior table + only
  the flagged issues, and keeps unflagged rows verbatim (preserves correct work, aids convergence).
- **arm_type** per cohort from CTGov `armGroups[].type`.
- **One output + one log**, streamed per trial; `--selected` retired; modes = `ID` / `IDS` / all.

## Verified (live)
- `NCT05009992` (DMG, 6 cohorts — the hard case, 2026-07-10): fresh full run + **comprehensive validator
  (`scratchpad/check_output.py`) PASS, 0 problems / 55 rows** — no `[None]`, no gene `A AND NOT(A)`/dupes,
  clean OncoTree, per-drug TGA/PBS + detail links, `main_drugs` = investigational agents only. (Extraction
  still `faithful=False` — convergence limit; mapping degrades gracefully.)
- `NCT07099898` (SCLC, 2 arms): main=`risvutatug rezetecan` (investigational, TGA `Not approved`),
  auxiliary=`Topotecan`; POTTR + drug_class + PBS populated via web search; arm_type EXPERIMENTAL/ACTIVE_COMPARATOR.
- `NCT05417594` (BRCA basket, 83 rows): oncotree + `SmallVariant[gene=BRCA1 & …]` finding-model.
- Legacy comparison spot-checks: BREAST=BREAST, MTAP `HOM_DEL` matches; deviations marginal.

## Ticked off (2026-07-10) — output-quality fixes
Acting on the user's review of the DNF output:
- **OncoTree logic guards** — 3 sentinels (dropped `[None]`), and deterministic rejection of `X AND X`,
  `X AND NOT(X)`, broad-AND-subtype, and subtype-AND-parent (hierarchy from OncoTree levels).
- **Cancer_type** — CONDITIONS authoritative, never-AND different types, drop umbrella when one specific type.
- **gene finding-model** — validator now catches duplicate terms + self-contradiction; mapper omits
  unrepresentable (e.g. location-qualified) `NOT()`; extraction reviewers flag `X AND NOT(X)` cells to split.
- **Drug** — `main_drugs` by judgement (investigational only); per-drug `tga_status`/`pbs_status` +
  `tga_detail`/`pbs_detail` (year + evidence + link). **+2 output columns.**
- **Refine → incremental repair** (prior table + flagged issues only). Verified live on `NCT05009992`. 54 tests pass.

## Ticked off (2026-07-08)
Former TODOs now closed:
- **Run log reworked for clarity, no fluff.** Dropped the per-line `HH:MM:SS INFO` prefix (format is now
  `%(message)s`) and the non-essential char-count; silenced the `NumExpr defaulting…` import line. Added
  per-trial `▶ EXTRACTION / ▶ MAPPING / ▶ DRUG` stage banners and a fixed-width left **role gutter**
  (`doer` / `reviewer` / `result` / `cohorts` / `rules`) so doer-vs-reviewer is scannable — including the
  **mapping & drug reviewer verdicts** (✓/✗ per value + `↳ reason`), which were previously never shown.
  Shared formatting lives in `agentic/core/logfmt.py` (`stage()` / `role()` / `cont()` + `OK/FAIL/WARN`
  marks). Verdict marks: `✓` faithful · `✗` gating fail · `⚠` advisory. Touched `run.py` +
  `tasks/{extraction,mapping}/workflow.py`; 53 tests still pass.
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
- **Extraction convergence** — hard multi-subcohort trials (`NCT05009992`) still exhaust 3 attempts
  `faithful=False` even with incremental repair. Deeper fix pending (more attempts / per-dimension resolved /
  up-front subcohort split).
- **`H3K27-altered` → empty finding-model** — bare `H3K27-altered` inconsistently maps to `""`; gene-mapper tune.
- **Run-comparison method** (spec §12) — still deferred; verification of the mapping is currently manual.
- **`eligibility_path` retirement** — legacy stays in-tree as the resource source + reference until superseded.

## Gotchas
- **SDK:** `openai 2.44.0`. `.parse()` uses `chat.completions.parse`; `.research()` uses the Responses API
  `web_search` tool (`client.responses.parse(tools=[{"type":"web_search"}], text_format=<pydantic>)`).
- **Env:** conda `trial_curator` (`/opt/anaconda3/envs/trial_curator/bin/python`); default `python3` lacks the deps.
- **Determinism:** the response cache is the deterministic layer; `temperature`/`seed` omitted (gpt-5.x rejects them).
- **Copy, don't import** from `eligibility_path`; agentic owns its copies (e.g. `pipeline_io.py`, the resource reads).
- **Data safety:** `data/` is gitignored (~31 GB). Never `git clean -fdx`. `make agentic-clean` is scoped to `data/agentic/`.
