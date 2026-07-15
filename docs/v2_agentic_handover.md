# v2 Agentic Pipeline — Handover

- **As of:** 2026-07-15. **Branch:** `AUS-328-Aus-trial-universe-v2`. **NEW CHAT — the drug-ref build + the
  combination-split fix are DONE (full run at 100%, corrected set produced; see "drug-reference subsystem" below).
  The user is manually reviewing the corrected outputs. Next: (1) finalise the canonical go-forward workflow
  [task #8], (2) max-concurrency sweep [#19]. The speed / over-enumeration pipeline work (⓿/①) remains the larger agenda.**
- **Pre-rewrite fallback tag:** `aus-trial-eligibility-path-resource-generation-v1` (code only — NOT data).
- **Run/setup guide:** `docs/agentic/combined_agentic_run.md` (all make commands + environment).
- **Design + fields + schema:** `docs/v2_agentic_pipeline_spec.md` (single spec); **diagram:** `docs/v2_workflow_diagram.html`
  (published Artifact: https://claude.ai/code/artifact/671df104-6474-4c32-b78c-45f4b65d063f — on any diagram change,
  **overwrite** that URL via `scripts/publish_diagram_artifact.sh`; see memory `workflow-diagram-artifact`).
- **Decisions (memory):** `v2-agentic-rewrite-ground-rules`, `v2-stage2-extraction-decisions`, `v2-mapping-stage-decisions`,
  `v2-drug-regime-axis`, `v2-drug-ref-table`, `feedback-max-allowable-concurrency`.
- **Git:** the user makes all commits. Uncommitted in the tree: the #18 combination-split + `patient_population`
  fixes (drug_ref `schema.py`/`agents.py`/`store.py`/`workflow.py`/`build.py` + tests) and the doc/memory updates.
  The one-off drivers used this session (`finish_research.py`, `remediate_combos.py`, `build_flat_file.py`,
  `milestone_watch.py`) are in the session **scratchpad** — home them into the repo as part of task #8.

## Next up (start here) — for the NEW chat, in this order

**✅ DONE (2026-07-13): Relational contract — the drug-regime axis + normalized output.** A design review found
the output conflated two axes; the **drug regime (CTGov `armGroups`) is now the locked spine**, eligibility is
*assigned* to it. Contract + 9 locked decisions: `v2_agentic_pipeline_spec.md` §6.1 (memory `v2-drug-regime-axis`).
Shipped + verified extraction-only on 5 trials (NCT05009992/04221035 complex, 2 typical, 1 ANZCTR): (1) CTGov
regime filter `{Drug, Biological}` (biologicals kept, radiation/placebo dropped) + arm descriptions; (2)
eligibility→regime assignment incl. **drop closed-cohort criteria** (NCT05009992: closed 1A/1B/2A/2B dropped, 6
regimes, 34 rows, no blow-up) + the `structural` reviewer auditing assignment; (3) ANZCTR → single eligibility
cohort, regimes from INTERVENTIONS/COMPARATOR gated by CONTROL; (4) **output = 3NF masters + combined view** —
each run writes `data/agentic/output/<ts>/{regime,eligibility,combined}.tsv` (`run.py` `_write_trial`; `--out-dir`,
`--extract-only` added; validator reads newest `*/combined.tsv`). 70 tests pass.
*Next here:* the deferred **`drug_ref`** table (global, datestamped, per-drug web-search once → lookup) makes drug
enrichment per-regime + is the drug-stage speed win (⓿).
*Observed but out of scope (→ ①):* complex trials still `faithful=False` (convergence); within-regime
over-enumeration (NCT04221035 induction 66 rows; NCT05009992 Cohort 5 `H3K27-altered AND <target>`); `prior_therapy`
absorbing washout/concomitant-med noise.

**✅ DONE (2026-07-13 → 07-15): drug-reference subsystem — 4 3NF tables, full run to 100%, combination fix applied.**
`aus_trial_universe/agentic/tasks/drug_ref/`: `drug_alias` (raw→**namespaced** `canonical_id` = `rxcui:<n>` |
`name:<x>`; a combination/regimen token → **N** component canonicals, `1 raw → N`) / `drug_ref` (intrinsic facts) /
`drug_target` ((target,action) pairs = the mechanism) /
`drug_indication` (TGA/PBS, indication-specific, comprehensive static fields incl. combination/prior_therapy/
setting/population). Spec §6.1 (memory `v2-drug-ref-table`). **Division of labour (user):** LLM doer→reviewer for
judgement (canonicalize / annotate modality+targets+class+FDA/EMA / approvals); **deterministic offline lookups**
for the no-judgement facts — `rxcui`+`atc_code` (RxNorm RXNCONSO.RRF) and `pottr_drug_class` (POTTR ontology walk)
via `rxnorm.py`/`pottr.py` (reuse the data, not the Postgres pipeline). Incremental + **batched with checkpoint
save** + **soft-fail per drug** (one bad drug can't kill a long run). `make drug-ref-build DRUGS=.. | IDS=.. |
ALL_TRIALS=1`. 85 tests pass. 5-drug re-run verified: namespaced ids, correct POTTR hierarchies + ATC,
drug_target pairs (ADC antigen+payload, dordaviprone 3 targets), per-agency TGA/PBS + real ARTG/PBS links, 0 for
investigational agents. (`rxcui` = "in RxNorm / standard identity", NOT an approval flag — documented.)
**✅ FULL run complete (2026-07-15): 1417 canonicals, 100% researched, 0 failures.** 2442 raw tokens (1743 CTGov +
699 ANZCTR via `extract_anzctr_drugs`) → 1417 distinct canonicals. The run halted once at 89% (2026-07-14) on an
OpenAI `insufficient_quota` (a **billing** limit, not a rate-limit); after the budget was topped up, a targeted
completion pass finished the remaining 155 — scratchpad `finish_research.py` researches only the unresearched cids
and skips re-deriving the universe (no ANZCTR re-extraction), the token-lean resume. Live resource: `version_15072026/`.

**✅ [#18] combination-split fix — DONE + applied.** `canonicalize` now returns COMPONENTS (`Canonicalization` →
`list[CanonicalComponent]`): a genuine multi-agent regimen splits into its component drugs (FOLFOX/R-CHOP expanded,
OR-alternatives unioned) while a single engineered molecule (ADC / bispecific / fusion) stays ONE. The `store` alias
table is now **1 raw → N** (`set_alias` / `canonical_ids_for`, multi-row), with a deterministic `_COMBO_LEFTOVER`
backstop rejecting an unsplit component. Also done: `patient_population` "patients"→"" (`_clean_pp`). The already-built
data was remediated **without re-running the universe** — scratchpad `remediate_combos.py --apply --out-dir` re-canonicalizes
the combo raws → atoms, researches only NEW atoms (reuses the rest), then purges the combo pseudo-rows.

**Deliverables (under `data/agentic/resources/drug_ref/`):**
- `100pct_initial_curation/` — pre-fix baseline: 4 tables + `drug_ref_combined.tsv` (**1417 drugs**; 1193 clean + 224 combos flagged).
- `100pct_corrected_curation/` — corrected set: 4 tables + `drug_ref_combined.tsv` (**1286 standalone drugs, 0 combinations**).
- Split math: 224 combos → 219 atoms (126 reused, 93 newly researched); 1417 − 224 + 93 = 1286. Verified: 0 combos, 0 dangling aliases, 0 seed-only rows.
- Preserved intact (nothing overwritten): `version_14072026/` (89%), `version_15072026/` (100% raw), `snapshot_89pct_14072026/`. Run log: `data/agentic/log/drug_ref_full_run_14072026.log`.
- **⚠ The approval/mechanism data is LLM + live-web-search derived — treat as draft. The user is manually reviewing the corrected outputs (in progress 2026-07-15).**

**NEXT (in order):**
1. **[#8] Finalise the canonical go-forward workflow (with the user).** Package completion + combo-fix + flat-file:
   fold the scratchpad drivers (`finish_research.py`, `remediate_combos.py`, `build_flat_file.py`) into the repo / `make`
   targets; decide run-to-run reproducibility (a **DiskCache** would make re-runs near-instant + deterministic — the
   response cache is in-memory only today). NB a fresh full `make drug-ref-build ALL_TRIALS=1` now splits combos
   natively (`canonicalize` is fixed), so it would never create combo pseudo-drugs — the one-off remediation existed
   only to fix the *already-built* data. User deferred this until after the manual output review.
2. **[#19] Max allowable concurrency** (memory `feedback-max-allowable-concurrency`): OpenAI rate-limit docs + a
   concurrency sweep for the ceiling; 16 is confirmed safe; `--workers` sets fan_out concurrency + checkpoint batch.

*Parked (unchanged):* (a) map each indication's free-text `cancer_type`/`biomarker` into the eligibility vocabulary
(OncoTree + finding-model) — the *symmetric-match* representation; (b) link `drug_ref` back into the trial `combined`
view (join by canonical + approval-for-this-cancer). User parked both until the standalone tables were right.

**⓿ FIRST: SPEED / EFFICIENCY (do this before anything else).** A complex trial currently takes **>20 min**
end-to-end — far too slow to run the full universe (thousands of trials). Attack throughput/latency BEFORE the
correctness work below. Likely levers (investigate, don't assume): the bounded-refine loop re-generates the
WHOLE table each attempt (up to 3× full extraction) even when only a few cells are flagged; the 5-reviewer panel
+ mapping reviewers add many sequential-ish LLM round-trips per trial; trials run **sequentially** (only
within-trial `fan_out` is parallel — `DEFAULT_MAX_WORKERS=8`); the drug stage does live `web_search` (slow); the
default cache is **in-memory only** (no run-to-run reuse — a `DiskCache` would make re-runs near-instant); model
choice / `max_completion_tokens`. Measure first (add per-stage timing), then decide: parallelise across trials,
DiskCache, cheaper/faster model per stage, fewer/thinner reviewer calls, or partial re-gen on refine. Keep the
lean/Pattern-B ethos. **This is the agreed first task in the new window.**

**① Over-enumeration + the doer/reviewer VANTAGE-POINT lesson (the big correctness item).** See the dedicated
section **“Design discussion — over-enumeration & the doer/reviewer vantage point”** below. A source-grounded
review found a *systematic* extraction error (OR-alternatives fabricated into AND-combinations) that the
in-loop reviewers AND the deterministic validator both missed. The durable fix is architectural (move the
“aggregate-output-vs-source” vantage point INTO the loop), not just a sterner extractor prompt. Concrete fixes
are listed there. Pick this up after speed.

**② Extraction convergence on hard trials.** `NCT05009992` still finishes `faithful=False` after 3 attempts —
deep sub-cohort scoping (1A/1B/2A/2B…). Incremental repair helped but didn't fully converge. Consider more
attempts, per-dimension "resolved" tracking, or splitting sub-cohorts up front. (Related: enumeration-granularity
non-determinism — same trial/code gives 32 vs 218 rows across runs; see the design section.)

**③ Fresh-eyes review still owed** (was in progress when we stopped). Compare highly-granular cases against the
ORIGINAL input text for systematic errors (not just the programmatic validator — it can't see faithfulness).
Sets: `data/agentic/analysis/complex_trials_ids.txt` + `typical_trials_ids.txt`. The last run FAILED 16/20 on a
transient API rate/capacity wall (batch-resilience skipped + continued; API healthy again). Deliverable = one
merged 20-trial TSV, reviewed. Run: `make agentic-run IDS=$(cat …/complex_trials_ids.txt),$(cat …/typical_trials_ids.txt)`
then `make agentic-validate`.

   *(The `H3K27-altered` mapping + the acceptable-simplification rule are done; see "Ticked off".)*

## Design discussion — over-enumeration & the doer/reviewer vantage point
*(Captured 2026-07-12/13 from a live source-grounded review of `NCT04221035` (SIOPEN HR-NBL2). Preserve for the
new chat — this is the reasoning behind priority ① above, not just a bug list.)*

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
The v2 rewrite is a **complete two-stage agentic pipeline**, end-to-end verified on ctgov + anzctr:
**extract → map → drug enrichment**, in one streamed pass, via a single command (`make agentic-run`).
Pattern B throughout: deterministic Python owns control flow; the LLM fills the doer/reviewer slots.
**64 unit tests pass** (all fake-client, no API). Output is a **DNF (disjunctive normal form)** table — one row =
one satisfiable (trial, cohort) conjunction; rows are ORed, cells within a row ANDed, exclusions inline `NOT(...)`.

## Quickstart
Conda env `trial_curator` (auto-selected); `OPENAI_API_KEY` auto-loaded from `.env`. Needs `openai>=2.x`.
```bash
make agentic-run ID=NCT06881784                 # one trial (source auto-detected)
make agentic-run IDS=NCT1,ACTRN2,NCT3           # a specific set
make agentic-run                                # ALL trials
make agentic-clean                              # wipe data/agentic/{output,log,cache}
make agentic-tests                              # 64 unit tests, no API
#   options: MODEL=<name>  NO_JUDGE=1 (skip extraction panel)  NO_REVIEW=1 (skip mapping/drug reviewers)
```
One run → **one output** `data/agentic/output/trial_resource_<id|timestamp>.tsv` + **one log** `data/agentic/log/…`.
Full detail: `docs/agentic/combined_agentic_run.md`.

## What's built
```
aus_trial_universe/agentic/
  run.py                     # PIPELINE ORCHESTRATOR: per trial extract -> map -> drug, stream one output; a failing trial is logged & skipped (batch continues)
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
  qa/
    validate_output.py       # INDEPENDENT output validator ("review of the reviewers"); make agentic-validate
tests/agentic/               # 64 tests (fake-client)
scripts/agentic/pipeline.sh  # driver: python-pick, .env, tests-preflight, log tee; subcommands run|validate|clean|tests
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
- **Stage-I ingestion** — download → drug-filter + POTTR-append → retire-missing is **not yet in agentic**; the
  pipeline currently reads the versioned inputs the legacy path produces. (Biggest remaining piece.)
- **ANZCTR `DRUG_rxnorm_matched`** shelved — ANZCTR drug is now LLM-extracted; revisit RxNorm as a cross-check.
- **OncoTree granularity** — SCLC-subtype trials occasionally flag "unfaithful" (output still written); tune vs
  the manual review of the 10-trial set (`data/agentic/analysis/review_trials_ids.txt`).
- **Extraction convergence** — hard multi-subcohort trials (`NCT05009992`) still exhaust 3 attempts
  `faithful=False` even with incremental repair. Deeper fix pending (more attempts / per-dimension resolved /
  up-front subcohort split).
- **Run-comparison method** (spec §12) — still deferred; verification of the mapping is currently manual.
- **`eligibility_path` retirement** — legacy stays in-tree as the resource source + reference until superseded.

## Gotchas
- **SDK:** `openai 2.44.0`. `.parse()` uses `chat.completions.parse`; `.research()` uses the Responses API
  `web_search` tool (`client.responses.parse(tools=[{"type":"web_search"}], text_format=<pydantic>)`).
- **Env:** conda `trial_curator` (`/opt/anaconda3/envs/trial_curator/bin/python`); default `python3` lacks the deps.
- **Determinism:** the response cache is the deterministic layer; `temperature`/`seed` omitted (gpt-5.x rejects them).
- **Copy, don't import** from `eligibility_path`; agentic owns its copies (e.g. `pipeline_io.py`, the resource reads).
- **Data safety:** `data/` is gitignored (~31 GB). Never `git clean -fdx`. `make agentic-clean` is scoped to `data/agentic/`.
