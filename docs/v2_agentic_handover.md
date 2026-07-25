# v2 Agentic Pipeline — Handover

## ▶ NEXT SESSION — START HERE (2026-07-25)
**Where we are (DONE + committed `b9e0202`):** the `trial_arm_id` restructure + the **full 1,999-trial EXTRACT**
are done. Eligibility store = shared `trial_arms` registry (5,224 arms) + `arm_eligibility_raw` +
`interpreted_eligibility` (**17,659** conjunctions), all keyed by `trial_arm_id`. Drug store migrated + pruned to
**1,275** drugs. `make agentic-arm-consistency` ✓ · **134 tests pass**. ⚠ **The vocab-map tables and `combined.tsv`
are NOT built yet** — the run was `--extract-only`.

**THE TO-DO** — the user's 3 milestones (① mapping · ③ the join · plus their **review + sign-off at every stage**)
merged with the coupled work, dependency-ordered. **Every stage GATES on the user's review + sign-off.**
1. **MAPPING (Stage II — the last extraction-side piece).** [user milestone] Map interpreted cells → vocab:
   `cancer_type` → OncoTree (name+code); `gene_alteration` + `molecular_signature` → finding-model syntax.
   (`molecular_biomarker` + `prior_therapy` stay free-text — no mapper.) **The stage is already BUILT**
   (`tasks/eligibility/mapping/`, doer→reviewer, lookup-first) — the action is RUNNING it over the full extract-only
   store. **Decision needed:** re-run `make agentic-run` (NON-extract-only, NO `--resume` — `--resume` would skip the
   already-extracted trials and map nothing) leaning on the DiskCache so extraction re-serves near-instant and only
   mapping is new LLM work; OR add a lighter map-only pass that reads `interpreted_eligibility` and maps its distinct
   cells. Then **manual QA** per the hold-out rule (curated resources are held-out verification data, esp.
   `gene_alteration` — memory `v2-mapping-stage-decisions`). Live API run (cost-aware) — set concurrency via the
   RPM/TPM probe (`feedback-max-allowable-concurrency`).
2. **DRUG PHASE 2 — main vs auxiliary role (enables #3's full contract, so do it before/with the join).** Cheap
   per-arm classifier → a `role` (main|auxiliary) column on `trial_to_intervention`; main = investigational agent(s),
   aux = backbone/SoC/comparator. Judgement rules exist in git history (deleted `DRUG_CURATOR_INSTRUCTIONS`,
   ≤ 52417bc). Without it `combined.tsv` can't carry main/aux or per-main-drug TGA/PBS. (handover "Shelved / open".)
3. **THE JOIN → `combined.tsv` (the matching engine's flat file).** [user milestone] `run._build_combined` already
   joins interpreted ⋈ `trial_arms` ⋈ vocab-maps ⋈ drug annotations on `trial_arm_id` (currently PARKED / 16 cols).
   Needs #1's maps + #2's drug role. Finalize the **flat-file contract** (backlog A): add the missing **TGA/PBS** +
   **main/aux role** columns. **Open decision:** the join engine — in-process **SQLite/DuckDB** vs keep the Python
   join (Postgres ruled out; memory `v2-joined-tables-sql-decision`).
4. **SYMMETRIC-MATCH vocab for drug approvals (deeper, for the engine).** Map each drug indication's free-text
   `cancer_type`/`biomarker` (in `drug_regulatory_approvals`) into the SAME OncoTree + finding-model vocab, so the
   engine can match trial-eligibility ↔ drug-approval symmetrically. Parked; comes with the drug↔trial link.
5. **SELF-CONTAINED PIPELINE (backlog C/D) — Stage-I ingestion + legacy retirement.** The pipeline still reads
   legacy-produced inputs (`trial_universe/`); moving download → drug-filter → POTTR-append → retire-missing into
   agentic (+ retiring legacy `eligibility_path`/`drug_utility_path`) is the biggest piece for a periodic run.

**Known quality item (will surface in the review):** 219 trials finished `faithful=False` (best-effort hard
multi-cohort — the extractor exhausts the 6-attempt refine budget; output still written). Full detail below +
memory `v2-next-priorities`, `v2-trial-arm-id-architecture`.

---

- **⏩ 2026-07-25 — SHARED `trial_arms` registry + `trial_arm_id` FK (major restructure).** Arm identity is now a
  first-class **shared** thing, not embedded per-path. Four decisions (all user-approved):
  1. **ANZCTR cohort identification is a path-neutral SHARED module** — `tasks/shared/` now owns `Cohort`, the
     `trial_arm_id()` slug, the ANZCTR drug-extractor agents + `DrugExtraction`/`RegimeVerdict`,
     `extract_anzctr_drugs`, `anzctr_regimes`, and `TrialArm` + `TrialArmStore`. Both paths import it; the old
     backwards drug→eligibility import is gone.
  2. **New central table `data/agentic/trial_arms/trial_arms.tsv`** (`TrialArmStore`, current_version/ + archive/):
     `trial_arm_id, trialId, registry, arm, arm_type`, **both registries**. `trial_arm_id` = a **deterministic
     slug** `{trialId}::{arm}` (chosen over an auto-increment surrogate: reproducible with no sequence authority —
     safe for parallel workers + idempotent re-runs — and human-readable). Written by whichever path processes a
     trial (per-trial checkpoint in `run.py`; the drug build also populates it).
  3. **Drug `trial_to_intervention` → `(trial_arm_id, input_intervention_name)`** (dropped trialId/registry/arm/
     arm_type, which now live once in `trial_arms`). This is the ONLY drug_annotations file whose SHAPE changed.
  4. **Eligibility = 2 content tables** (`arm_eligibility_raw`, `interpreted_eligibility`), each keyed by
     `trial_arm_id`; `trial_arms` left the eligibility store.
  - **ANZCTR arms are re-derived FRESH** by the shared module (not adopted from the frozen drug store), so ANZCTR
     arms may **drift** vs the frozen store — that drift IS the migration's diff (mostly the drug-token noise below
     getting auto-cleaned).
  - **✅ FULL RUN COMPLETE (2026-07-25):** `make agentic-run EXTRACT_ONLY=1 RESUME=1` over **all 1,999 trials**
     (1,495 CTGov + 504 ANZCTR) → `trial_arms` (**5,224 arms**) + `arm_eligibility_raw` (5,224) +
     `interpreted_eligibility` (**17,659** conjunctions), all keyed by `trial_arm_id`. **0 failures**; 219 trials
     `faithful=False` (best-effort hard-trial residual — the output-quality-review item). `make agentic-arm-consistency`
     → CONSISTENT ✓.
  - **✅ DRUG MIGRATION + ORPHAN PRUNE DONE (2026-07-25).** `make agentic-drug-migrate-trial-arms APPLY=1` re-keyed
     `trial_to_intervention` to `trial_arm_id` (12,846→**12,492** rows; 354 dropped = ANZCTR drug-token noise +
     closed CTGov arms) — ONLY that drug file changed (md5-verified). Then (user-approved) the flow-on was pruned:
     **7 orphaned canonicals** (all supportive-care: antiemetics/PPI/H2/opioid/emollient) + their 7 target & 9
     approval rows + 43 unused input mappings → **1,275 fully-referenced drugs**. `RESOURCE_INFO.md` updated.
     Snapshots: `drug_annotations/archive/{pre_trial_arms_migration,pre_orphan_prune}_20260725/` +
     `eligibility/archive/pre_trial_arms_migration_20260725/` (fully reversible).
  - **Concurrency lesson (memory `feedback-max-allowable-concurrency`):** probed the account (gpt-5.5 = **15k RPM /
     40M TPM**) and ran at **80 workers / `--max-concurrency 500`** → ~5× throughput, 0 rate-limit pushback.
  - `arm_consistency` was repurposed to a **referential-integrity** check (every eligibility/drug `trial_arm_id`
     exists in the registry). Supersedes the old `DrugRefStore.anzctr_arms` adoption path (removed).
  - **Committed:** the whole restructure landed in `b9e0202` (40 files, +1791/−523; data is gitignored).

- **As of:** 2026-07-24. **Branch:** `AUS-328-Aus-trial-universe-v2`. **BOTH paths are built.** The DRUG UTILITY
  PATH was signed off (2026-07-20); the ELIGIBILITY PATH v2 was rewritten + validated (2026-07-21): decoupled from
  drug enrichment, reshaped into 3NF relational tables, parallelised + cached + per-item-durable, over-enumeration
  fixed (SIOPEN `NCT04221035` 218→45 rows, no explosion anywhere). **113 unit tests pass.** Validated on 10 complex
  + 50 standard trials → a unified 60-trial store at `data/agentic/eligibility/current_output/`.
- **3NF-purity relocation (2026-07-24):** the store dirs now hold ONLY pure-3NF tables. The denormalized joined
  view `combined.tsv` was moved OUT of `eligibility/current_output/` to **`eligibility/combined/combined.tsv`**
  (`combined_dir = store_root / COMBINED` in `run.py`; `COMBINED_OUTPUT`/`COMBINED`/`COMBINED_FILE` in
  `core/paths.py`; validator + 2 tests repointed; docs updated). The drug `current_version/` was already pure 3NF.
  Easily hoisted to a top-level `data/agentic/combined/` if preferred (one line).
- **Prunable response cache (2026-07-24):** the shared `DiskCache` (`data/agentic/cache/`, used by BOTH paths)
  now stores each entry as a provenance envelope — agent `name` + `prompt_sha` (sha256 of `instructions`) — with
  back-compat reads of legacy bare-JSON entries. New `core/prompt_registry.py` enumerates live agents offline;
  new `core/cache_prune.py` GCs entries from **outdated prompts** (agent's `prompt_sha` changed / agent removed).
  `make agentic-cache-prune` (dry-run; `APPLY=1`, `PURGE_UNKNOWN=1`) + auto-prune (stale-only) at the start of
  every `agentic-run` / `drug-ref-build` (`--no-cache-prune` to skip). Corrections doctrine documented: fix the
  **prompt** (fingerprint changes → live recompute + auto-prune) or edit a **map table** (lookup-first honours
  it); hand-edits to extraction/regime tables do NOT survive a trial re-run. Touched `core/client.py`,
  `core/agent.py`, `run.py`, `tasks/drug_utility/build.py`, Makefile, `pipeline.sh`, + tests; docs (spec §4.1,
  `combined_agentic_run.md`) + both diagrams (republished to the same artifact URLs). **113 unit tests pass.**
- **⛔ SUPERSEDED — the 2026-07-24 "ANZCTR adopts cohorts from the drug registry" approach** (`DrugRefStore.anzctr_arms`,
  the 6-trial re-align, the abandoned reconcile-the-store idea) was **replaced** by the 2026-07-25 `trial_arm_id`
  restructure above: arms are now derived FRESH by the shared module and are the authority (drug adopts via the
  migration, not the reverse). The old **drug-token noise** (`HA`/`Ig`/`Surgery`/regimen acronyms) is what the
  migration's 354 deletions **auto-cleaned** — so that open item is now largely resolved. Memory
  `v2-anzctr-cohort-alignment` rewritten accordingly.
- **THE FOCUS NOW — the user's eligibility-output quality review** of the full 1,999-trial `current_output/` (run
  DONE 2026-07-25). Known soft spot: the **219 `faithful=False`** hard multi-cohort trials (best-effort). The
  standing backlog behind it:
  - **A. Finalize the flat-file contract** — the exact columns the matching engine needs, produced robustly.
    `combined.tsv` is currently **16 columns** and is **missing TGA/PBS + the main/aux role**. The through-line.
  - **B. Drug Phase 2 — main vs auxiliary role** (cheap per-arm classifier + `role` column). Prereq for A's
    main/aux distinction; details in "Shelved / open".
  - **C. Stage-I ingestion into agentic** (download → drug-filter → POTTR-append → retire-missing) — the pipeline
    still reads legacy-produced inputs; the biggest piece for a self-contained periodic run.
  - **D. Legacy-path retirement** (coupled to C).
  - **Decision — SQL for the joined tables?** Settled 2026-07-24: the matching engine consumes ONLY the flat file
    (external user-querying was DROPPED), so any SQL engine is a purely INTERNAL batch compute step → in-process
    **SQLite/DuckDB, NOT Postgres** (or keep the current Python join). Decide when tackling A. Memory
    `v2-joined-tables-sql-decision`.
  Full detail: memory `v2-eligibility-orchestration-model` + `v2-next-priorities`.
- **Drug path final state (2026-07-20):**
  - **5 3NF tables** (spec §6.1; layout `docs/agentic/drug_ref_schema.md`): `intervention_to_canonical`,
    `trial_to_intervention` (now keyed by `trial_arm_id` → shared `trial_arms` registry; 2026-07-25),
    `drug_annotations_core`, `drug_target_actions`, `drug_regulatory_approvals` (the old
    `drug_ref`/`drug_target`/`drug_indication` names were retired).
  - **Consolidated data structure** under a single relocatable `DATA_ROOT` (`core/paths.py`; = `data/agentic/`,
    promotable to `data/`): `trial_universe/` · `resources/{drug_utility,eligibility}/…/current_version/` ·
    `drug_annotations/current_version/` (+ `archive/`) · `eligibility/` · `log/` · `analysis/`. Loaders read
    `current_version/` (not `latest_version_dir`). `make drug-ref-refresh-pottr` refreshes POTTR from GitHub.
  - **Current build:** `data/agentic/drug_annotations/current_version/` (**1275 drugs** after the 2026-07-25
    migration + orphan prune; metadata in `RESOURCE_INFO.md`).
  - **Doc-currency pass (2026-07-20):** re-verified tests + docs + diagram at sign-off. Fixed stale references left
    over from the data restructure — `drug_ref_schema.md` `trial_to_intervention` was missing the `arm`/`arm_type`
    columns; `store.py`/`build.py`/`__init__.py`/`Makefile` still pointed at the retired
    `resources/drug_ref/version_<ddmmyyyy>/` path (now `drug_annotations/current_version/`); `schema.py` cited a
    non-existent `atc.py` (ATC is in `rxnorm.py`); `rxnorm.py` still described reading the RRF "in place" from the
    legacy tree (it reads the agentic resource dir). Diagram + `combined_agentic_run.md` were already current.
- **Pre-rewrite fallback tag:** `aus-trial-eligibility-path-resource-generation-v1` (code only — NOT data).
- **Run/setup guide:** `docs/agentic/combined_agentic_run.md` (all make commands + environment).
- **Design + fields + schema:** `docs/v2_agentic_pipeline_spec.md` (single spec) + `docs/agentic/drug_ref_schema.md`
  (drug tables + data layout). **Diagrams** (each overwrites its own Artifact URL on change — see memory
  `workflow-diagram-artifact`): eligibility `docs/v2_eligibility_workflow_diagram.html`
  (https://claude.ai/code/artifact/671df104-6474-4c32-b78c-45f4b65d063f) · drug `docs/v2_drug_workflow_diagram.html`
  (https://claude.ai/code/artifact/6c944fe1-2df6-4641-9ed2-7dca1db03b60).
- **Decisions (memory):** `v2-agentic-rewrite-ground-rules`, `v2-stage2-extraction-decisions`, `v2-mapping-stage-decisions`,
  `v2-drug-regime-axis`, `v2-drug-ref-table`, `feedback-max-allowable-concurrency`.
- **Git:** the user makes all commits. The **full `trial_arm_id` restructure is committed** in `b9e0202`
  (40 files, +1791/−523: `tasks/shared/`, rewired stores/schemas/`run.py`, `migrate_trial_arms.py`, reworked
  `arm_consistency`, docs, both diagrams, all tests). Data (stores + `archive/` snapshots) is gitignored.
  (A subsequent doc/memory currency pass may leave fresh uncommitted doc edits — not code.)

## ✅ DRUG UTILITY PATH — SIGNED OFF (2026-07-13 → 07-20). Not the focus of the new chat.
The drug-regime axis (CTGov `armGroups`) is the locked output **spine**; eligibility is *assigned* to it (9 locked
decisions, spec §6.1, memory `v2-drug-regime-axis`). The standalone **drug utility path is signed off** — 5 3NF
tables, consolidated relocatable data structure, refresh command, docs/tests/diagram. Full detail: the header
above + `docs/agentic/drug_ref_schema.md` + memory `v2-drug-ref-table` / `agentic-data-root-temporary`. **Nothing
drug-side is outstanding** except two PARKED integration pieces to revisit AFTER the eligibility work:
- (a) map each drug indication's free-text `cancer_type`/`biomarker` into the eligibility vocabulary (OncoTree +
  finding-model) — the *symmetric-match* representation;
- (b) join `drug_annotations` back into the trial `combined` output (by canonical_id + approval-for-this-cancer);
  this also lets the per-trial run **look up** the drug reference instead of a live per-trial `web_search` (a ⓿ speed win).

## ✅ ELIGIBILITY PATH v2 — DONE + VALIDATED (2026-07-21). Full detail: memory `v2-eligibility-orchestration-model`.
The per-trial pipeline is now **extract → map (lookup-first) → grand-flat join**; drug enrichment is a SEPARATE
incremental build that joins in via `(trialId, arm)`. `make agentic-run` (see Quickstart). Output: the accumulating
`data/agentic/eligibility/current_output/` store — 5 3NF tables + `combined.tsv` (see "Output schema"). The three
originally-outstanding issues were all addressed:

**⓿ SPEED — done.** Root cause measured: extraction is ~85% of wall-clock (a hard trial ≈ 1000s at the 6-attempt
cap; standard ≈ 30–150s). Levers applied: (1) **trial-level parallelism** (`run_parallel`, `--workers` default 8);
(2) **`DiskCache`** under `data/agentic/cache/` — run-to-run reuse makes re-runs near-instant (proven: 9/10 complex
re-ran in ~1s); (3) drug enrichment removed from the per-trial path (no per-trial `web_search`); (4) enum reviewer
folded into the panel `fan_out`. Nested-concurrency sweep (trials × 6-reviewer fan_out) still owed — see memory
`feedback-max-allowable-concurrency`.

**① Over-enumeration — FIXED (the vantage-point lesson applied).** The durable architectural fix: an in-loop
**enumeration reviewer** (`tasks/eligibility/extraction/agents.build_enumeration_reviewer`) sees the ASSEMBLED,
de-duplicated DNF (via `_distribute` run inside `check()`), counts alternatives-vs-source, and flags OR→AND
fabrication — the panel never saw the assembled set. Plus a deterministic **commutative-duplicate dedup** (`A AND
B` == `B AND A`) in `_dedup_rows`. Verified: SIOPEN `NCT04221035` 218-row MYCN∧MYCL fabrication → 45 clean rows,
the 3 amplifications as separate OR-values; no explosion on any of the 60 validated trials (max ~45).

**② Convergence — addressed.** `refine()` now returns the **BEST** attempt (fewest gating problems) + stops on an
exact problem-set repeat (cycling), so the cap is no longer arbitrary; reviewers were made **lenient** (gate only
on material errors) and the cap raised to **6** → emergent 1–2 attempts for standard trials, up to 6 for hard ones.
Hard multi-subcohort trials (`NCT05009992`) still finish `faithful=False` (best-of-6) — inherent; see Residual.

**③ Fresh-eyes validation — done.** Ran 10 complex (`analysis/complex_trials_ids.txt`) + 50 standard
(`analysis/standard_50_ids.txt`) → unified 60-trial `current_output/`. `make agentic-validate` → ~43/50
validator-clean; remaining flags are mostly VALIDATOR crudeness (same-type histology+stage ANDs like "clear cell
RCC AND pT2 RCC" — satisfiable/faithful). Fixed en route: closed-arm leakage (`loaders._CLOSED_ARM_RE` drops
`NOT CURRENTLY ENROLLING`/withdrawn arms — `NCT05009992` 6→2 arms), OncoTree `NOT(A OR B)` validator false-positive
(paren-aware OR-split), and a store-accumulation bug (load before creating run_dir).

**Durability (user requirement):** per-item save on BOTH paths via `core/workflow.run_parallel` — a finished
trial/drug is written to disk immediately; a crash loses only in-flight items. `current_output/` accumulates
in-place; supersede by moving it to `archive/<date>/`.

### Residual / deferred (eligibility) — for the review chat
- **Hard-trial non-convergence** — deep sub-cohort trials return best-of-6 (`faithful=False`); reviewers flag real
  gaps (missing cohorts, over-scoping) the extractor doesn't fully repair. A "patience" early-stop (stop after K
  non-improving attempts) would trim the wasteful tail + speed.
- **`cancer_type` purity** — occasionally ANDs a same-type refinement (histology+stage) or a specimen/status
  criterion into `cancer_type` (schema-fit edge; the 5-column schema has no home for e.g. a tissue-availability
  rule). Partly non-deterministic. Not the enumeration failure — that's resolved.
- **Verbose `prior_therapy`** — many shared exclusions ANDed into each row (faithful but heavy).
- **Weighted "best attempt"** in `refine()` (currently raw gating-problem count) — parked (see Shelved/open).
- **Drug join in `combined.tsv`** carries `arm_drugs`/`drug_class`/`pottr_drug_class` only; TGA/PBS + main/auxiliary
  role come with drug Phase 2.

   *(The `H3K27-altered` mapping + the acceptable-simplification rule are done; see "Ticked off".)*

## Design discussion — over-enumeration & the doer/reviewer vantage point
**✅ RESOLVED 2026-07-21** (see "ELIGIBILITY PATH v2" §①: in-loop enumeration reviewer + commutative-dedup; SIOPEN
verified 218→45, no fabrication). Kept below as the REASONING behind the fix — a durable lesson on why moving the
aggregate-output-vs-source vantage point into the loop matters.
*(Captured 2026-07-12/13 from a live source-grounded review of `NCT04221035` (SIOPEN HR-NBL2).)*

**The serious errors found (source-confirmed):**
1. **OR-alternatives fabricated into AND-combinations.** Source states the gene criterion as *"MYCN
   amplification, **or** focal high level MYC **or** MYCL amplification"* — **3 OR-alternatives** (a patient needs
   ONE). The output produced **9 gene values**, incl. **6 spurious AND-pairs** (e.g. `MYCN amp AND focal MYCL
   amp`) AND both orderings of each pair. **84/218 rows (38%)** carried a fabricated conjunction. It is
   **satisfiable** (a patient *could* have two amplifications), so the **deterministic validator can't catch it**
   — only comparison to the source reveals it.
2. **Enumeration-granularity non-determinism.** Same trial, same code: **32 rows** in one run, **218** in another.
   Volatile, and inflated by (1). "Deterministically clean" ≠ "faithful".
3. **Secondary:** commutative-duplicate rows (`A AND B` + `B AND A` survive string-order dedup); `cancer_type`
   over-fragmentation (grouped "L2, M or Ms" split into separate rows *and* recombined → 8 cancer variants).
   Cross-multiplied: 8 cancer × 9 gene × … → 161 rows in ONE cohort of a protocol whose true structure is a
   handful of OR-paths.

**Why the review caught it but the extractor (even with a sterner prompt) couldn't — 5 structural reasons:**
1. **A prior was handed in.** "200+ combinations seems impossible" is a sharp, falsifiable hypothesis that
   directed attention to one number and to the source. The extractor is told "extract into DNF rows", never "is
   this row count plausible?" — nobody asks it the question that makes the error visible.
2. **Verification ≪ generation in difficulty.** Checking "are these 9 values really 3 alternatives?" is far
   cheaper than *constructing* the correct 3 while also expanding staging/cohort/prior-therapy/provenance/
   negation. (This is spec principle #5, and the NP check-vs-solve asymmetry.)
3. **The reviewer sees the aggregate; the generator never does.** Laying out the *deduplicated set* of gene
   values makes `MYCN AND MYCL` next to `MYCL AND MYCN` obviously absurd. The extractor emits rows **forward,
   sequentially**, and never views its own finished output as a set to notice duplicates or a count mismatch.
4. **The DNF task itself is the trap.** It is asked to produce *disjunctive normal form* — to multiply a boolean
   expression out into one-row-per-combination. `(MYCN|MYC|MYCL) & (staging₁|staging₂)` has a tiny logical form
   but a large DNF; expansion is exactly where it loses "which ORs are independent axes vs. mutually-substitutable
   alternatives within one criterion" and cross-multiplies. Over-enumeration is the *natural failure mode* of the
   task, not a random slip.
5. **Focus + tools + slow reading.** One trial, one anomaly, set/count ops, source read carefully — vs. the
   extractor doing all 5 columns × cohort × provenance in one fast pass under ~40 competing prompt rules.

**Key takeaway — a prompt alone won't fully fix it; the fix is architectural.** Commutative-duplicate and
count-vs-source checks are inherently **post-hoc, whole-output** operations a forward generator can't do on
itself; each new prompt rule competes for attention; the failure is a **global** property ("the DNF must not
exceed the source's true alternative structure"). Tellingly, the **reviewer panel missed this too** — the
molecular/structural reviewers judge per-dimension faithfulness of individual cells; none was given the
enumeration-plausibility lens and none sees the deduplicated set. **The lesson: move the vantage point (aggregate
output vs. source) INTO the loop.** Concrete fixes:
- **Extractor (prompt):** never AND the OR-alternatives of a single criterion; normalise commutative AND.
- **Validator (deterministic, cheap):** flag commutative-duplicate AND-cells (`A AND B` + `B AND A`).
- **Reviewer (new lens):** count the distinct alternatives the source states per criterion; if the output's
  enumeration exceeds that, an OR was fabricated into an AND. Give a reviewer the *aggregate* view.

## TL;DR
The v2 rewrite is a **two-domain agentic pipeline**: the ELIGIBILITY path (`make agentic-run`: extract → map, per
trial) and the DRUG UTILITY path (`make drug-ref-build`: a separate incremental drug-annotation build). Both emit
**3NF relational tables**; they join on `trial_arm_id` (via the shared `trial_arms` registry), and `combined.tsv`
is the grand flat view. Pattern B throughout: deterministic Python owns control flow (parallelism, refine loop,
per-item durable saves); the LLM fills the doer/reviewer slots. **134 unit tests pass** (fake-client, no API).
Eligibility output is a **DNF** table — one row = one satisfiable (trial, arm) conjunction; rows ORed, cells ANDed,
exclusions inline `NOT(...)`.

## Quickstart
Conda env `trial_curator` (auto-selected); `OPENAI_API_KEY` auto-loaded from `.env`. Needs `openai>=2.x`.
**Wrap live runs in `caffeinate -i …`** so laptop sleep doesn't drop the connection (memory `feedback-caffeinate-long-jobs`).
```bash
make agentic-run ID=NCT06881784                 # one trial (source auto-detected)
make agentic-run IDS=NCT1,ACTRN2,NCT3           # a specific set
make agentic-run                                # ALL trials
make agentic-run EXTRACT_ONLY=1 RESUME=1 WORKERS=80 MAX_CONCURRENCY=500   # the full-universe extract (2026-07-25 settings)
make agentic-arm-consistency                    # referential-integrity check on the trial_arm_id join key
make agentic-drug-migrate-trial-arms            # re-key drug t2i to trial_arm_id + report (APPLY=1 to write)
make agentic-clean                              # wipe the transient data/agentic/{log,cache} only
make agentic-tests                              # 134 unit tests, no API
make agentic-validate                           # QA the newest combined.tsv (review-of-the-reviewers)
# run.py flags (via `python -m aus_trial_universe.agentic.run`): --workers N (parallel trials, default 8) ·
#   --skip-drug (skip the drug top-up; still joins existing drug data) · --no-cache · --extract-only ·
#   --no-judge (skip extraction panel) · --no-review (skip mapping reviewers) · --store-root DIR · --max-attempts N
```
Eligibility output → the accumulating `data/agentic/eligibility/current_output/` store (5 3NF tables +
`combined.tsv`); each run loads it, upserts the run's trials, and writes it back in place (supersede by moving
`current_output/` → `archive/<date>/`). Log → `data/agentic/log/…`. Drug reference is a SEPARATE build:
`make drug-ref-build …` → `data/agentic/drug_annotations/current_version/`.
Full detail: `docs/agentic/combined_agentic_run.md`; drug path: `docs/agentic/drug_ref_schema.md`;
eligibility design: memory `v2-eligibility-orchestration-model`.

## What's built
```
aus_trial_universe/agentic/
  run.py                     # ELIGIBILITY ORCHESTRATOR: trials in PARALLEL (run_parallel, --workers) — per trial extract -> map (lookup-first) -> per-trial checkpoint to current_output/; then optional incremental drug top-up + grand combined.tsv join. DiskCache; a failing trial is logged & skipped
  core/
    paths.py                 # SINGLE relocatable DATA_ROOT (=data/agentic/) + all derived paths + current_version_dir()/archive_current_version()
    client.py                # LlmClient: .parse() (chat.completions) + .research() (Responses API web_search); cache, retries, tracing
    agent.py                 # Agent = prompt + schema + model (+ web_search flag) bound to the client
    workflow.py              # generic fan_out() + refine() (bounded check->repair loop)
    pipeline_io.py           # dated-file / version-dir selection (copied from eligibility_path); versioned datasets now read current_version/ via paths.py
    prompt_registry.py, cache_prune.py, logfmt.py  # live-agent enumeration + cache GC + shared run-log formatting
  tasks/shared/              # PATH-NEUTRAL arm identification (used by BOTH paths; 2026-07-25)
    cohorts.py               # Cohort, trial_arm_id() slug, anzctr_regimes / extract_anzctr_drugs (fresh derivation), resolve_cohorts
    agents.py                # ANZCTR drug doer + reviewer (DrugExtraction / RegimeVerdict) — moved here from eligibility
    schema.py, store.py      # TrialArm + TrialArmStore -> data/agentic/trial_arms/current_version/trial_arms.tsv
  tasks/drug_utility/        # DRUG UTILITY PATH — 5 3NF tables; see docs/agentic/drug_ref_schema.md
    schema.py, store.py      # 5-table schema + DrugRefStore (trial_to_intervention keyed by trial_arm_id)
    rxnorm.py, pottr.py      # DETERMINISTIC offline lookups (rxcui+atc; POTTR class walk); pottr.refresh_pottr() downloads POTTR
    agents.py, workflow.py, build.py  # 3 LLM doer->reviewer pairs (web_search) + build_drug_ref (+ writes trial_arms) + CLI
    migrate_trial_arms.py    # re-key trial_to_intervention to trial_arm_id + report add/deletes (make agentic-drug-migrate-trial-arms)
  tasks/eligibility/         # ELIGIBILITY PATH (2 content tables keyed by trial_arm_id + mapping + tools + qa)
    extraction/              # STAGE I: free text -> DNF eligibility rows
      loaders.py             # ctgov/anzctr assembly + cohort enumeration (arm_type, drug); load_trials(id/ids/all)
      agents.py              # cohort-aware extractor + 5-reviewer panel (raw + interpret sub-stages)
      schema.py, workflow.py # DnfRow (Cohort imported from tasks/shared); extract_trial() = raw -> interpret -> panel -> refine -> distribute
    mapping/                 # STAGE II: map the DNF cells -> vocab (LLM mapper -> reviewer each)
      agents.py, schema.py   # oncotree / gene / signature mappers + reviewers
      workflow.py            # map_cancer_types, map_gene_alterations, map_molecular_signatures
    schema.py, store.py      # ArmEligibilityRaw + InterpretedEligibility (keyed by trial_arm_id) + 3 map tables; EligStore
    tools/
      oncotree.py            # OncoTree vocab + code validator + hierarchy (ancestors/is_subcode); 3 sentinels
      finding_model.py       # finding-model grammar + syntax/logic validator (dup + self-contradiction)
    qa/                      # validate_output.py (make agentic-validate) + arm_consistency.py (referential-integrity; make agentic-arm-consistency)
tests/agentic/               # 134 tests (fake-client); mirrors tasks/ layout (core, tasks/shared, tasks/eligibility, tasks/drug_utility)
scripts/agentic/pipeline.sh  # driver: python-pick, .env, tests-preflight, log tee; subcommands run|validate|clean|tests|cache-prune|arm-consistency|drug-migrate-trial-arms|drug-ref-build|drug-ref-refresh-pottr
docs/agentic/combined_agentic_run.md   # run/setup guide
```

## Output schema (v2 — shared arm registry + 3NF content tables + a grand flat view; arm registry extracted 2026-07-25)
Arm identity lives once in the **shared `trial_arms` registry** `data/agentic/trial_arms/current_version/trial_arms.tsv`
(`trial_arm_id, trialId, registry, arm, arm_type`; both registries; `trial_arm_id` = deterministic `{trialId}::{arm}`
slug), written by whichever path processes a trial. The eligibility store `data/agentic/eligibility/current_output/`
holds only its **2 content masters + 3 map tables**, each keyed by `trial_arm_id` (re-running a trial replaces its
rows). The denormalized **grand flat view `combined.tsv` lives OUTSIDE the store**, at
`data/agentic/eligibility/combined/combined.tsv`. Eligibility tables hold **NO drug info**; drugs join via
`trial_arm_id` to the drug utility path. Tables + the view:
- `trial_arms.tsv` (SHARED) — `trial_arm_id` → `trialId, registry, arm, arm_type`. The arm spine; the single join key.
- `arm_eligibility_raw.tsv` — `trial_arm_id` → the 5 VERBATIM raw cells (`cancer_type, gene_alteration,
  molecular_signature, molecular_biomarker, prior_therapy`; inline `[source]`, `|`-delimited). The audit anchor.
- `interpreted_eligibility.tsv` — `(trial_arm_id, conj_id)` → the 5 interpreted DNF cells (inline `NOT()`; rows
  sharing `trial_arm_id` are ORed).
- `cancer_type_map.tsv` — `cancer_type` value → `oncotree_name, oncotree_code` (deduped; lookup-first cache).
- `gene_alteration_map.tsv` / `molecular_signature_map.tsv` — value → `finding_model` (deduped).
- `combined.tsv` — the grand flat join (interpreted ⋈ trial_arms ⋈ maps ⋈ drug annotations on `trial_arm_id`): the
  eligibility columns + `oncotree_name/code`, `*_findingmodel`, and `arm_drugs, drug_class, pottr_drug_class` from the
  drug store. (TGA/PBS + main/auxiliary role come with Phase 2/finalization.)
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
- `NCT05009992` (DMG, 6 cohorts — the hard case, 2026-07-10): fresh full run + a **comprehensive per-row check
  (OncoTree logic + finding-model validity + drug columns): PASS, 0 problems / 55 rows** — no `[None]`, no gene
  `A AND NOT(A)`/dupes, clean OncoTree, per-drug TGA/PBS + detail links, `main_drugs` = investigational agents
  only. (Extraction still `faithful=False` — convergence limit; mapping degrades gracefully.) NB: that ad-hoc
  validator was a session script, not committed — re-derive from the validators in `tools/` if needed.
- **10 typical CTGov trials** (`data/agentic/analysis/typical_trials_ids.txt`, 2026-07-10): comprehensive validator
  **PASS, 0 problems / 135 rows** — fixes hold with no regressions on standard trials (RCC→CCRCC/PRCC, endometrial
  carve-outs, `Solid tumour`, CML→CMLBCRABL1, per-drug TGA/PBS). One trial hit a transient API error and was
  recovered via the batch-resilience skip + a single-id re-run.
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
- **Refine → incremental repair** (prior table + flagged issues only). Verified live on `NCT05009992`.
- **Batch resilience** — a trial that errors mid-pipeline is logged (`FAILED · <id> … skipped; continuing`) and the
  run continues to the next trial; the final summary reports `N ok, M failed` (previously one flaky API call crashed
  the whole batch). `run.py` + `tests/agentic/tasks/test_run_resilience.py`.
- **`H3K27-altered` ≡ `H3K27M`** — bare `H3K27-altered` (and its `AND <other>` conjunctions) previously mapped
  inconsistently (empty / H3-block dropped / `p.K27M` vs `p.K28M` / 1–4 genes). Now one canonical rendering
  anchored in the **shared** grammar (mapper + reviewer): K27M small-variant OR `H3F3A | HIST1H3B | HIST1H3C`,
  strict-HGVS **`p.K28M`**, block never dropped in a conjunction. Verified live — all 9 H3 cells of `NCT05009992`
  consistent, attempt 1. (`H3F3B` intentionally dropped; legacy gene symbols — flag for the user's manual check.)
- **Acceptable-simplification rule** — mapper + reviewer now treat dropping a qualifier finding-model has no field
  for (copy-number count/threshold, quantitative level, VAF, anatomic location, tumour context) as **correct**:
  map to the closest term (`≥5 copies` → `type=GAIN`) and drop the qualifier; reviewer no longer fails it.
  Prompt-only (shared `GRAMMAR_REFERENCE` + `_GENE_RULES` + gene-reviewer clause). Added a prompt-decision guard
  test `test_locked_prompt_decisions_present`.
- **DNF cross-product blow-up fixed** — `NCT04221035` (SIOPEN HR-NBL2) produced **680 rows from 3 cohorts, 86%
  unsatisfiable** (`Stage A AND Stage B`). Root cause: the extractor restated the disease-staging axis in BOTH
  `trial-wide` (17 OR-rows) and each cohort, and `_distribute` cross-producted + blind-ANDed them (`17 × 40 =
  680`). Fix — (1) **extractor scope contract**: cohorts are a fixed known set; assign each criterion to exactly
  ONE scope, never restate a single-valued axis (cancer_type/stage) across scopes (`agents.py` +
  `structural` reviewer now flags it); (2) **distribution safety net**: `_merge_cell` — cohort value **wins** on
  `cancer_type` (never `X AND Y`); `_dedup_rows` after merge; `_CROSS_PRODUCT_WARN` logs oversized products.
  Each output row stays **self-contained** per the downstream matching engine's needs. Regression test
  `test_cohort_wins_on_cancer_type_and_dedup_prevents_blowup`. Verified: `NCT04221035` **680 → 32 rows, 587 → 0
  impossible conjunctions**; no cross-product WARN on any of the 5 complex trials. (User to verify closely.)
- **prior_therapy over-enumeration (LLM judgement)** — `NCT05417594` emitted every `(cancer × gene)` profile
  TWICE, differing only by an extra `AND refractory to standard therapy` (a strict superset → the stricter row
  is logically subsumed). Not solvable programmatically (can't know if the clause is required); the extractor
  now applies **judgement**: never emit near-duplicate/subsuming OR rows — decide whether the extra clause is
  required and keep the SINGLE version applying to the majority of patients. prior_therapy + structural
  reviewers flag the pattern. Verified: **108 → 47 rows, 25 → 0 subsumed pairs** (legit basket enumeration
  preserved).
- **cancer_type tumour-type EXCLUSIONS restored** — a prior run silently dropped `NCT05009992`'s stated
  exclusions (thalamic/cerebellar DMG carve-out, histone-H3-wildtype astrocytoma, etc.). Extractor prompt now
  mandates capturing "except/excluding/other than" tumour carve-outs as same-cell `NOT()` and never dropping
  them ("losing a stated tumour-type exclusion is a serious error"); cancer_type reviewer flags a missing one.
  Defensive: `_merge_cell` cohort-wins now **preserves trial-wide `NOT()` exclusions** (cohort's positive type
  wins, but a shared exclusion is never dropped by the merge; `_top_level_and` splitter). Regression test
  `test_cohort_wins_preserves_trialwide_cancer_type_exclusion`. Verified: NOT() carve-outs **0 → 53** on
  `NCT05009992`.
- **Independent output validator — a "review of the reviewer agents"** (`make agentic-validate`,
  `aus_trial_universe/agentic/qa/validate_output.py`). Deterministic, runs OUTSIDE the workflow to catch what
  the in-loop reviewers let through (mapping degrades gracefully — output is written even at `faithful=False`).
  Re-runs the pipeline's own OncoTree + finding-model validators on the final cells, plus cross-row DNF/cohort/
  exclusion checks nothing else does (unsatisfiable `A AND B` cancer_type, all-empty rows, exact-dup rows,
  in-cell `X AND NOT(X)`, prior_therapy subsuming-twin over-enumeration). **Testing-period QA only — NOT the
  production path; always run it on a fresh output while iterating and keep its checks in sync with the
  pipeline.** Also fixed the duplicate `agentic-clean` Makefile target (was warning on every run). Tests
  `tests/agentic/qa/test_validate_output.py`. **64 tests pass.**

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
- **DRUG PATH Phase 2 — main vs auxiliary role (POSTPONED; the main deferred feature).** Per trial-arm, distinguish
  the **investigational/defining** drug(s) (`main`) from backbone / standard-of-care / comparator / placebo
  (`auxiliary`). Plan: add a `role` (`main` | `auxiliary`) column to the drug path's `trial_to_intervention`
  (keyed `trialId, registry, arm, input_intervention_name`), populated by a **cheap per-arm classifier** (judge
  from the arm title/description — **no web_search**). Touch: `tasks/drug_utility/{schema,store,build,agents}.py`,
  `RESOURCE_INFO.md`, the drug tests, `docs/agentic/drug_ref_schema.md` + the drug diagram. The main/aux
  **judgement rules already exist in git history** — the old eligibility `tasks/eligibility/mapping/agents.py
  DRUG_CURATOR_INSTRUCTIONS` (deleted when drug enrichment was decoupled, ≤ commit 52417bc) — reuse them. Once
  landed, the eligibility `combined.tsv` drug join can surface the role, and the drug reference is complete.
  Deliberately deferred (2026-07-20) as not on the critical path to the eligibility over-enumeration work.
- **Weighted "best attempt" in `refine()` (future).** `refine()` currently returns the attempt with the fewest
  **gating** problems — a raw COUNT (all material issues weigh the same; ties → earliest). A severity- or
  dimension-weighted measure would be more principled, but the hard part is getting the LLM reviewers to emit a
  *calibrated, reliable* per-problem severity (a great deal of work to teach the model what's severe vs minor);
  parked until then. `core/workflow.py:refine`. (User-agreed 2026-07-21: keep raw count for now.)
- **Stage-I ingestion** — download → drug-filter + POTTR-append → retire-missing is **not yet in agentic**; the
  pipeline currently reads the versioned inputs the legacy path produces. (Biggest remaining piece.)
- **ANZCTR `DRUG_rxnorm_matched`** shelved — ANZCTR drug is now LLM-extracted; revisit RxNorm as a cross-check.
- **OncoTree granularity** — SCLC-subtype trials occasionally flag "unfaithful" (output still written); tune vs
  the manual review of the 10-trial set (`data/agentic/analysis/review_trials_ids.txt`).
- **Extraction convergence** — hard multi-subcohort trials (`NCT05009992`) still exhaust 3 attempts
  `faithful=False` even with incremental repair. Deeper fix pending (more attempts / per-dimension resolved /
  up-front subcohort split).
- **Run-comparison method** (spec §12) — still deferred; verification of the mapping is currently manual.
- **`make` command-set review (user, deferred 2026-07-24).** Review the current targets: the active v2 set
  (`agentic-run`, `agentic-cache-prune`, `agentic-arm-consistency`, `agentic-validate`, `agentic-tests`,
  `agentic-clean`, `drug-ref-build`, `drug-ref-refresh-pottr`) and whether the legacy `eligibility-path-*` /
  `drug-ontology-*` targets should be retired (ties into legacy-path retirement below). Also confirm whether any
  new command is needed for the two-stage / 3-table eligibility schema. Fold into the full doc/diagram/Makefile pass.
- **Legacy-path retirement — DEFERRED to the eligibility-path work (decided 2026-07-20, user-approved).** Shared
  inputs/resources are consolidated under `data/agentic/` (memory `agentic-data-root-temporary`), and `data/agentic/`
  is a TEMPORARY root — promotable to top-level `data/` by changing the one `DATA_ROOT` line in `core/paths.py`.
  BUT the legacy `drug_utility_path` is **NOT a pure remnant**: the still-live `eligibility_path` (the next focus)
  has a hard dependency on it — `eligibility_path/anzctr/.../iii_extract_drugs.py` (run by `make
  eligibility-path-anzctr`, `pipeline.sh:80`) **imports** `aus_trial_universe.drug_utility_path.ctgov.drug_ontology.
  identity.rxnorm.matcher` and, together with `eligibility_path/shared/workflow/recursive_end_to_end_workflow.py`,
  **reads** `data/drug_utility_path/drug_ontology/raw_inputs/RxNorm`. So removing `aus_trial_universe/drug_utility_path/`
  (+ `tests/drug_utility_path/`) or `data/drug_utility_path/` now would break `make eligibility-path-anzctr`. The
  agentic drug path itself is fully decoupled (imports nothing from the legacy tree). Retire the whole legacy
  `drug_utility_path` (code + data + tests) together with the eligibility rewrite — either drop the RxNorm-matcher
  dependency (agentic already does ANZCTR drugs via LLM) or copy `matcher.py` (self-contained, stdlib-only) into an
  agentic-owned home per the copy-don't-import rule. Also already dead (fix or drop when convenient): the Makefile
  `drug-ontology-pipeline-tsvs` / `drug-ontology-analysis-tsvs` targets point at a non-existent
  `aus_trial_universe.ctgov.drug_ontology.*` module path. Benign leftover: `data/agentic/trial_universe/ctgov/
  extracted_trials/ctgov_field_extractions.csv` is not read by agentic (CTGov reads `input_trials/` + `download_state/`).

## Gotchas
- **SDK:** `openai 2.44.0`. `.parse()` uses `chat.completions.parse`; `.research()` uses the Responses API
  `web_search` tool (`client.responses.parse(tools=[{"type":"web_search"}], text_format=<pydantic>)`).
- **Env:** conda `trial_curator` (`/opt/anaconda3/envs/trial_curator/bin/python`); default `python3` lacks the deps.
- **Determinism:** the response cache is the deterministic layer; `temperature`/`seed` omitted (gpt-5.x rejects them).
- **Copy, don't import** from `eligibility_path`; agentic owns its copies (e.g. `pipeline_io.py`, the resource reads).
- **Paths:** import all data paths from `core/paths.py` — never hard-code `data/agentic/...`. Versioned datasets
  (resources + drug_annotations) live under `current_version/` (date in a metadata file) with prior sets in
  `archive/`; loaders use `current_version_dir()`, NOT `latest_version_dir` (which now serves only ctgov `input_trials`).
- **Data safety:** `data/` is gitignored (~31 GB). Never `git clean -fdx`. `make agentic-clean` is scoped to the
  transient `data/agentic/{eligibility,log,cache}` only — it never touches `trial_universe/`, `resources/`, or `drug_annotations/`.
