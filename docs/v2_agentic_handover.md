# v2 Agentic Pipeline — Handover

## ▶ NEXT SESSION — START HERE (2026-08-06, end of session 12)

> **STATE: green and coherent.** 445 tests · 0 invariant violations · gates WARN / 0 FAIL · engine dry run
> OURS=0 · two clean e2e refreshes today. **UNCOMMITTED since the tidy-up** (the user does ALL commits).
>
> ### What is true now that was not this morning
>
> **1. All three vocabulary columns run ONE three-stage pipeline** — `1 translate` (LLM doer→reviewer) →
> `2 canonicalise` (deterministic) → `3 finalise` (approved register). cancer_type, gene_alteration and
> molecular_signature have each been refined, reviewed, approved and migrated.
>
> **2. So do the DRUG APPROVALS.** `map_approvals.py` used to pass `three_stage=False` and take a legacy
> reconciler with an LLM group adjudicator, while gene/signature got no stage 2 or 3 at all. Both sides now call
> the identical `reconcile_column`. Measured payoff: drug-side gene expressions matching a trial-side expression
> **verbatim rose 76/105 → 87/105**. These tables exist for SYMMETRIC MATCHING, so two logics over one vocabulary
> was a correctness bug, not just duplication.
>
> **3. There is no second mapping path left.** The legacy R0–R8 branch, `repair_value`, `reconcile_group`, the
> `three_stage` flag, four oncotree repair/reconcile agents and two orphaned output schemas are DELETED.
> `reconcile.py` went 427 → 217 lines. `test_reconcile.py` asserts the symbols do not exist — a structural
> guarantee, not a runtime tripwire.
>
> **4. Every column's tables come from one spec.** `mapping/stage_tables.py` owns names, accreting columns,
> writers, readers and legacy fallbacks for all three columns; the per-column `tables.py` modules and three
> duplicate filename dicts are gone. Store layout is uniform:
> `{cancer_type,gene_alteration,molecular_signature}_map_{initial,reconciled,finalised}.tsv`.
> molecular_signature's stage 2 is a declared PASS-THROUGH, so its three tables are identical — which is what a
> healthy column looks like.
>
> ### ▶ WHAT TO DO NEXT
> The mapping work is finished. Pick up the **A-group** below: **A2** (the `pipeline/` + `outputs/` regroup) →
> **A3** (legacy removal — `trialcurator/`, `pydantic_curator/`, `actin_curator/`, `utils/`, `sql/`; ~6,200 lines
> that nothing imports) → **A4/A4b/A5**. **F1** (curated masters into git) remains the highest-value quick win.
> Open mapping items are **B6** and **B4**.
>
> ### The deliverable
> `data/agentic/derived/export/` — **Set A** `trial_eligibility.tsv` (17,781 rows · 2,022 trials · 33 cols) ·
> **Set B** the drug 3NF tables referenced in place · **Set C** `drug_approvals_mapped.tsv` (947 rows · 26 cols,
> NEW today: every approval row with its mapping appended, directly comparable to Set A) · `MANIFEST.md`.
> Cols A–W of Set A are newline-free (a run of CR/LF becomes `". "`), so a naive line-splitting consumer is safe.
>
> ### Data layout — three roles, deliberately separate
> | | what | lifecycle |
> |---|---|---|
> | `masters/` | the live curated data | mutated by each run |
> | `stable_runs/` | curation states a HUMAN chose to keep (`20260728/`, `20260806/`) | kept until a person says otherwise |
> | `baselines/` | the reviewed "before" states the prompt harness grades against | never changes |
> | `backups/` | rollback points | pruned once a newer verified `known_good_*` exists |
>
> Conflating the last three cost real time: the harness read baselines out of `pre_*` snapshots, which made
> 674 MB undeletable and meant pruning a backup could silently break a hard gate (an empty baseline makes every
> value look FIXED). `backups/` went **1.7 GB → 134 MB** once separated. `stable_runs/` holds `current_version/`
> ONLY — copying `archive/` in would make each kept run drag along every run before it.
>
> ### 🔴 FIVE LESSONS THAT COST REAL TIME — do not rediscover
> 1. **"Named vs specific", not "specific vs general."** A first attempt omitted an actionability-qualified
>    exclusion whenever it named only a bare GENE. Wrong by 12 values — an enumerated gene list IS a named
>    referent, and dropping it matches the trial to the driver-positive patients it explicitly refuses. ~1,900
>    sunk API calls.
> 2. **Never answer a SEMANTIC question with string surgery** (user, standing). Substring keys for per-value
>    verdicts collided three times running; `concept_key`'s unbounded replace once inverted "ineligible"→"in".
>    Verdicts now come from an LLM judge; only *which* values differ is deterministic, via canonicalised ASTs.
> 3. **Never build a review set by INTERSECTING version key-sets.** It silently dropped the 4 values postdating
>    the oldest baseline — one of them changed. Only the migration guard noticed.
> 4. **The refine loop is NOT reproducible from cache.** A byte-identical prompt can produce different output.
>    APPLY the reviewed artifact (`apply_reviewed.py`); never re-derive it by re-running.
> 5. **Size runs to the measured headroom.** `make agentic-refresh` defaults to `--workers 8`. Probe the
>    `x-ratelimit` headers and use `WORKERS=80 MAX_CONCURRENCY=500` (gpt-5.5: 15k RPM / 40M TPM, re-confirmed
>    2026-08-06).
>
> ### Two small open items
> - `stable_runs/20260806` was captured just before the MANIFEST fix; its masters are unaffected (the manifest
>   lives in `derived/`), but re-capture if you want byte-identical correspondence.
> - The diagrams were updated for the three-stage mapping but predate the A2/A3 regroup — item **A4b** says
>   republish after those land. Overwrite the EXISTING artifact URLs, never mint new ones.
>
> Session-by-session narrative for sessions 8–12: `docs/planning/archive/handover_history_20260806.md`.

## ⏭ THE BACKLOG (dependency-ordered; the authoritative task list)

### A. Do next, in this order (structure + cleanup; nothing here needs an LLM run)
- **A1 ✅ e2e run done** (session 6) — the user inspects its output + `run_report/` first.
- **A2 — RESTRUCTURE `aus_trial_universe/`. ⚠ PARTIALLY DONE 2026-08-04:** the `mapping/` split by vocabulary
  column shipped with B1b (`cancer_type/`, `gene_alteration/`, `molecular_signature/`; `tools/` deleted;
  `is_oncotree` → `column`). The `pipeline/` + `outputs/` + `qa/` regroup below is STILL OPEN.
- **A2 (remaining) — the top-level regroup (layout APPROVED by the user).** Six loose modules sit next to `core/`
  + `tasks/`; group them so every top-level folder answers one question:
  - `pipeline/` = the runnable entry points — `ingest.py` · `run.py` · `refresh.py` · `demo.py`
  - `outputs/` = what a run emits — `export.py` · `trial_info.py` · `run_report.py`
  - `qa/` — **✅ DONE 2026-08-05.** `tasks/eligibility/qa/` collided with the top-level `qa/` and is now GONE:
    `arm_consistency.py` + `validate_output.py` moved UP to `qa/` (both are QA entry points, and `arm_consistency`
    checks BOTH paths' tables, so living under `tasks/eligibility/` was simply wrong), and `mapping_consistency.py`
    became `mapping/consistency.py` (a Step-2 library, not an entry point — so it stays in eligibility, just not
    under a `qa/` name). `make` commands unchanged; `qa/` also gained the `adjudications/` package (one register per
    vocabulary column).
  - Blast radius measured: **17 files, ~32 references**, plus the two `-m` invocations in `scripts/agentic/
    {pipeline,demo}.sh`. `make` commands stay identical for the user. Verify with the suite + `make agentic-demo`
    (~35 s, exercises extract → map → reconcile → drug → export for real).
- **A3 — REMOVE ALL LEGACY / NON-AGENTIC CODE (user: "basically anything that is not part of the current agentic
  workflow"). Scope measured: 74 tracked files, ~6,200 lines of Python.** `aus_trial_universe` imports NONE of it
  and it imports nothing from `aus_trial_universe` — every remaining mention is a docstring provenance note, so the
  removal is mechanical. Delete in reviewable groups, `make agentic-tests` after each: (i) `trialcurator/` +
  `pydantic_curator/` + `tests/trialcurator/` + `utils/`; (ii) `actin_curator/` + `Dockerfile` + `actin_curator.sh`;
  (iii) `sql/` + `docker-compose.postgres.yml` + `requirements-db.txt`; (iv) `docs/planning/archive/eligibility_path/` +
  `docs/planning/archive/drug_utility_path/` + `scripts/one_off/`.
  **⚠ `data/` trees stay for now — the user chose "code only" (2026-07-29).** (`data/{drug_utility_path 28 GB,
  eligibility_path 95 MB, matched_trials 41 MB, trial_inputs 65 MB}` are detached: nothing reads them, no symlinks,
  and the agentic RxNorm resource is a real 621 MB copy.)
- **A4 — tail cleanup, with A3:** ~~`PyYAML` missing from `requirements.txt`~~ **FIXED 2026-08-04** (added, along with `python-pptx` which
  `scripts/build_external_deck.py` needs — same latent fresh-install failure); still prune the rest of
  `requirements.txt`; rename `tests/agentic/` → `tests/` (free once `tests/trialcurator/` is gone);
  optionally promote `data/agentic/` → `data/` (one `DATA_ROOT` line).
- **A4b — REPUBLISH THE TWO WORKFLOW DIAGRAMS.** `docs/v2_{eligibility,drug}_workflow_diagram.html` predate
  `arm_scope`, the gates and the run report; the eligibility one still shows a 7-stage refresh. **Overwrite the
  EXISTING Artifact URLs — never mint new ones** (memory `workflow-diagram-artifact`;
  `scripts/publish_diagram_artifact.sh`). Do AFTER A2/A3 so the module paths shown are final.
- **A5 — HIGH-QUALITY `README.md` (user, 2026-07-29).** The current 33 lines are entirely about the retired ACTIN
  Docker flow. Benchmark: the READMEs at https://github.com/hartwigmedical/hmftools — **and do better than those**.
  Should cover: what the pipeline is and produces (Set A + Set B), the 9-stage refresh, how to run it, the data
  layout, the QA/gates story, and how to recover (`data/backups/RECOVERY.md`). Do it AFTER A2/A3 so paths are final.

### ✅ B1b — GENE_ALTERATION MAPPINGS — **DONE 2026-08-04, MIGRATED TO PRODUCTION.**

Reviewed, approved and shipped. Export carries **0 error-severity defects** across 862 distinct gene
expressions, guarded by the new `gene_alteration_expressions` gate; the engine dry run reports **OURS = 0**.
Full record incl. the six iterations, the endorsed biology and the five resolved content calls:
**`docs/planning/archive/v2_gene_alteration_correction_spec.md`**. The measured baseline that scoped it follows for
provenance.

#### (original scoping entry)

**Measured baseline (2026-08-04, over `finalised_gene_alteration_map.tsv`):**

| | |
|---|---:|
| rows / distinct non-empty expressions / empty | 909 / 469 / 54 |
| **SYNTAX defects** (`finding_model_problems`) | **0** |
| divergent consistency groups | 0 |
| **rows where Step-1 ≠ FINAL** | **1 of 909** |
| negation-only expressions (no positive term) | 14 |
| duplicate term within one expression | 2 |
| ≥10 OR'd terms (HRR/family expansions — expected, not defects) | 19 |
| nested `NOT()` | 0 |

**What this tells us — the problem is NOT the same shape as OncoTree's, and the scope is smaller.**
- The **syntax gate already works.** `finding_model_problems()` is a real field/enum/scope/HGVS-aware grammar
  validator, so gene_alteration has no equivalent of the `invalid_codes` blind spot that let leaked NAMES through.
  There is **no parser to build** — that half of B1 is already done here.
- **Reconciliation is effectively inert: Step 2 changes exactly ONE row in 909.** That is the same structural
  weakness B1 exposed (an adjudicator told to *preserve* structure, and grouping that never fires), just with a
  different surface. This is the main thing to fix.
- What is genuinely missing is a **SEMANTIC defect catalogue** — the `log_*` half of `tools/oncotree_checks.py`
  has no finding-model counterpart — and a **canonical form** (term ordering, dedup, negation factoring), so two
  expressions of the same alteration can still differ textually.

**Plan — reuse the B1 machinery; do not rebuild it.**
1. **Catalogue + validator.** Add a semantic layer beside the grammar validator in `tools/finding_model.py`,
   mirroring `oncotree_checks.CATALOGUE`: negation-only (14 live), duplicate term (2), term both asserted and
   negated, a gene both wild-type and mutated, an expansion that contradicts the source's specificity.
2. **Canonical form.** Sort OR'd terms, dedupe, factor `NOT(a) & NOT(b)` → `NOT(a | b)`, normalise field order
   inside `Class[...]`. Same role as `oncotree_expr.canonicalise`; same idempotence requirement.
3. **Wire the refinement.** `mapping/reconcile.py`'s R0–R8 already branches on `is_oncotree`; the gene path
   currently short-circuits (`refined[value] = code`). Give it the deterministic pass, the per-value repairer
   (R4) and the defect-aware group reconciler it does not yet have.
4. **Prompts.** Only after the deterministic layer, and only for defects that need source judgement.
5. **Gate.** Extend `qa/gates.py:_oncotree_expression_gate` into a shared expression gate covering the
   `*_findingmodel` export columns too.

**Reuse directly:** the review sandbox pattern (single side-by-side comparison file + per-value audit log +
`stable_across_runs`), the population-level review harness, and the Phase-0→C→D→E migration plan.

**Read `docs/planning/archive/v2_oncotree_correction_spec.md` first** — especially §"lessons", which cost several iterations to
learn: condensing a prompt deletes rules; a class docstring is hashed into the cache key; test the live
connection, not just the imports.

### B. Needs a DEDICATED SESSION — prompt/vocab work (sign-off → cache invalidation → full re-run)
**⚠ ORDER CHANGED 2026-07-30 — the user moved B1 (OncoTree) to TOP PRIORITY, ahead of the A-group restructure.**
B1 layer L1+L2 need no LLM at all and can ship first; B1-L3 and B3 share one cache invalidation and one re-run.

- **🔴 B6 — FIX THE QUALIFIER-VARIANT INCONSISTENCY AT ITS ROOT IN STAGE 1, THEN RETIRE 17 REGISTER ENTRIES
  (user, 2026-08-06: *"that is ok. it's temporary. we will go back to stage 1 and fix it at the root. Just not
  now."*).** Stage 2 is specced DETERMINISTIC-ONLY, which leaves cross-value consistency unowned. Of the 20
  divergent cancer_type groups, the deterministic pass dissolves 8 as pure OR-ordering noise; the surviving **12
  are held by 17 register entries** (see the "STAGE-2 CONSISTENCY RESIDUE" block in
  `qa/adjudications/cancer_type.py`). Verified end-to-end: **20 groups → 12 after stage 2 → 0 after stage 3.**
  **Eleven of the twelve are stage 1 answering the SAME question two ways on a qualifier variant** — `Recurrent
  Ewing Sarcoma` → `ES` but `Refractory Ewing Sarcoma` → `ES OR ESST`; `Recurrent Rhabdoid Tumor` → `MRT` but
  `Refractory` → `ATRT OR MRT OR MRTL`; `Stage III melanoma` → `MEL` but `Stage IV` → the 7-code enumeration. So
  the root fix is a single stage-1 rule: **a disease-state qualifier (recurrent / refractory / relapsed / advanced /
  metastatic / unresectable / newly diagnosed) never changes which node is chosen.** That is a principle, not a
  patch, so it passes the admission test in memory `feedback-prompt-change-regression-method`.
  ⚠ It re-fingerprints the mapper and re-rolls the column, so it must go through the review harness
  (`qa/prompt_harness/`) with the 28 Jul + 4 Aug baselines and a zero stage-1-regression gate. When it lands, the
  harness's `ruling_status` column reports the 17 as **retirable** automatically — that is the exit condition.
  One entry is a genuine clinical correction and must NOT be lost if the block is retired wholesale: `CM`
  (Conjunctival Melanoma) sits at `EYE > OM > CM`, so stage 1 including it in a *non-ocular* melanoma expression
  was self-contradicting. The canonical non-ocular set is
  `ARMM OR ESMM OR HNMUCM OR MEL OR PCNSM OR URMM OR VMM` — no `NOT()` needed, because the ocular nodes sit under
  `EYE` and nothing in the positive set, so an ocular exclusion is vacuous and stage 2 would drop it.
- **✅ B1 — ONCOTREE CODE-EXPRESSION DEFECTS — DONE 2026-08-04.** Reviewed, approved and migrated to
  production; see `docs/planning/archive/v2_oncotree_correction_spec.md`. Export now carries 0 error defects and a new
  `oncotree_expressions` gate keeps it that way. Backup: `data/backups/known_good_20260804_post_oncotree/`.
  *Original entry follows for provenance.*
- **✅ B1 — ONCOTREE CODE-EXPRESSION DEFECTS — DONE 2026-08-04.** Reviewed, approved and migrated to production.
  Export carries **0 error-severity defects** across 572 distinct code expressions, guarded by the new
  `oncotree_expressions` gate. Full record incl. the defect catalogue, the migration and the reusable playbook:
  **`docs/planning/archive/v2_oncotree_correction_spec.md`**. The pre-fix inventory is in
  `docs/planning/archive/handover_history_20260804.md`. Backups: `pre_oncotree_fix_20260804` +
  `known_good_20260804_post_oncotree`.
  ➜ **The successor task is `B1b` (gene_alteration), above.**

- **🔴 B4 — FIX AT THE ROOT THE DEFECTS THE ADJUDICATIONS ARE HOLDING (user, 2026-08-05).** `qa/adjudications/`
  now carries 30 rulings. **Every one is an `override`, i.e. an admission that a prompt or deterministic rule is
  still too weak** — the register's own docstring says the override count is the quality metric for the
  correction, and an entry that flips to `match` should be RETIRED. Work through them by class and decide what
  changes in the doer/reviewer prompts:
  - **the sentinel-broadening class (the majority)** — the mapper answers "gynaecological cancer" / "spine cancer"
    / "adenoid cystic carcinoma" with `Solid tumour`. The prompt needs an explicit rule that a sentinel is only
    permitted when the source states a pan-scope population, plus the organ-set idiom for regional wordings.
  - **`acronym_as_gene`** — the gene mapper read `AGA` as a symbol. Needs a rule that an all-caps token must be
    confirmed as a gene from context, not from shape.
  - **over-narrowing** — `MET fusion, including PTPRZ1-MET fusion` was restricted to the named example. Needs a
    rule that "including X" makes X an EXAMPLE, never the whole criterion.
  - **catch-all nodes** — now gated deterministically (`MT`), so probably no prompt work needed.
  ⚠ **Batch this with B3**, because each prompt change re-fingerprints the agent, prunes its cache and re-rolls
  ALL 4,971 values — the exact mechanism that produced the nine B1 regressions. Do NOT do it piecemeal.
  **Also decide the 7 churned values** listed in the session-10 START-HERE block: 2 of the 7 were arguably
  IMPROVEMENTS the re-roll produced (`DIFG`→`DMG` for a source that says "diffuse midline glioma";
  `+NOT(BREAST)` where the source excludes breast as well as prostate) and were reverted only to keep the change
  set equal to what was approved. Either adopt them as rulings or accept that they will churn on every reconcile.
- **🔴 B5 — BUILD THE PROMPT → RE-RUN → EVALUATION HARNESS (user, 2026-08-05). The prerequisite for B4.**
  B1's review artifact had `issues_fixed` and `issues_remaining` but **no DEGRADED bucket**, which is the single
  reason nine regressions shipped. The harness must classify every value in a before/after pair as
  **fixed · unchanged · degraded**, and *the degraded bucket must be empty before sign-off* — that, not care, is
  what makes prompt work safe. Most of the machinery already exists and should be reused rather than rebuilt:
  `qa/gates.py:_mapping_drift_gate` (the broadening/narrowing classifier), the `APPROVED_*` registers' match /
  override reporting, and `analysis/gene_alteration_comparison.tsv`'s column shape
  (`before_production` / `after_stage1` / `after_stage2_FINAL` / `regression_risk`). This also finally delivers the
  long-deferred **run-comparison method** (spec §12 / group E), so it retires that item too.
- **🔴 B3 — EXTRACTION MISSES ON SPECIFIC ARMS — 5 WAIVED, NEED A PROMPT FIX (user-approved waiver 2026-07-29).**
  Five arms produce ZERO interpreted eligibility even though the source states cancer-patient eligibility. Found by
  the new `arm_scope` mechanism (see below), confirmed by a full cache-bypass re-extraction at the signed-off
  prompts that left all five empty → **systematic, not sampling**. Registered in `aus_trial_universe/qa/waivers.py`
  (`WAIVED_EMPTY_ARMS`) so the gates FAIL only on NEW unexplained arms while surfacing these as a standing WARN.
  **To fix (needs a prompt change → user sign-off → invalidates the cache → full re-run; do it in the SAME session
  as the OncoTree item below):**
  - `NCT06400472` cohorts **A3 / A4 / A5** — the raw stage captures only the *Exclusion Criteria* block for these
    three cohorts; the "Have one of the following solid tumor cancers" inclusion list never reaches them (siblings
    A1/A2/A6/B1-B4 extract fine). Root cause is in the RAW sub-stage's per-cohort text assembly.
  - `NCT04419649::Long-term Extension Cohort` — no rows though the source states MDS (IPSS-R very low/low/
    intermediate) eligibility; the 12 sibling cohorts extract fine.
  - `NCT05538130::Phase 1a Monotherapy Dose Escalation` — no rows though the trial is "People With Advanced Solid
    Tumors" with BRAF-mutant melanoma in the official title; the Phase 1b arm carries both of the trial's rows.
  **Two arms of the same batch WERE repaired** (2026-07-29) by an **arm-surgical** re-extraction, no prompt change:
  `NCT06999980::Treatment Arm F` (+2 rows) and `NCT02637687::Phase 2: Bone health assessment_sub-cohort` (+14).
  ⚠ **LESSON — never adopt a whole-trial re-roll.** Re-running a trial re-extracts ALL its arms and a fresh sample
  can REGRESS the good ones: `NCT06999980`'s re-roll dropped the "fully resectable" criterion from all 19 existing
  rows (19 cancer_type + 12 prior_therapy cells → 0). The fix must merge in the repaired ARM only and restore the
  reviewed rows for every sibling — and the restore scope is EVERY re-extracted trial, not just the ones with a
  repaired arm.
- **(ex-B2, now FOLDED INTO B1) OncoTree code-rendering consistency — the De Morgan item.** Original finding kept
  for provenance: on the 2026-07-29 refresh's one new trial (`ACTRN12626000937314`, endometrial) the two arms
  produced `UCEC AND NOT(UCS) AND NOT(ESS)` and `UCEC AND NOT(UCS OR ESS)` — **logically identical by De Morgan,
  same three codes, two renderings**. Step-2 left both because `find_inconsistencies` groups by INPUT-value
  similarity and the two inputs genuinely differ ("advanced (stage III or IV)" vs "recurrent"), so the pair never
  forms a group. **Impact depends on the matching engine:** cosmetic if it parses the boolean expression, a REAL
  miss if it string- or set-compares. Now measured store-wide as B1 classes **C2/C4** (343 distinct / 1,620 export
  rows) with the fix specified as B1 layer **L2** (canonical form + group on it). The **11 cancer_type per-value
  LOGIC residuals** are the same family and are covered by the C5–C11 hand-review.

### C. NEW capabilities the user asked for (2026-07-29) — not started
- **C1 — CROSS-CHECK AGAINST THE OLD ELIGIBILITY-PATH OUTPUT.** Compare the v2 export against the **v1**
  `eligibility_path` trial-resource outputs (`data/eligibility_path/exports/final/eligibility_*_resource_*.tsv`,
  still on disk — A3 deletes only CODE, not `data/`). Per-trial / per-column agreement + a diff of disagreements, so
  v2-vs-v1 regressions are visible rather than assumed. NB the v1 grain differs (no `trial_arm_id` spine), so the
  join key needs deciding — probably (trialId, cancer_type) with manual adjudication of the tail.
- **C2 — CROSS-CHECK AGAINST POTTR / REGISTRY GROUND TRUTH.** For a trial present in **POTTR eligibility**, compare
  our curated eligibility against POTTR's; where POTTR has no entry, compare against the **registry files**
  themselves. This is the closest thing to an external gold standard the project has.
- **C3 — A DE-DUPLICATED JOINED TABLE (on MAPPED values).** An additional joined table at the very end that
  de-duplicates rows on the **mapped/FINAL** values (`oncotree_code_FINAL`, the finding-models) rather than on the
  free text. Rationale: two rows whose free text differs but whose mapped codes are identical are the SAME
  matchable row to the engine — collapsing them shrinks the deliverable and removes an arbitrary choice.
- **C4 — DNF-PRINCIPLE VIOLATIONS: deterministic detector + LLM fix.** Apply a **deterministic check** for rows that
  violate the DNF principle (a row must be ONE satisfiable conjunction — e.g. two positive tumour types ANDed,
  `X AND NOT(X)`, an OR left inside a cell) and then an **LLM repair** for the ones it flags. Runs on the joined
  table (or wherever the rows land). NB `qa/validate_output.py` already DETECTS several of these
  (unsatisfiable positive-AND-positive cancer_type, in-cell `X AND NOT(X)`, prior_therapy subsuming twins) and
  currently reports **1,047 flags across 186 trials** — start from that detector, then add the repair stage.

### D. Gates / QA hardening (small, production-integrity)
- **D1 — no gate for VOCAB-MAPPING COVERAGE.** Nothing asserts "every interpreted value has a mapping". Hit for
  real in session 6: an arm repaired outside per-trial curation left its new values unmapped until `--map-only` was
  run BY HAND (refresh has no Step-1 mapping stage — it relies on per-trial mapping during curation).
- **D2 — ANZCTR filter-drift signal is still LOG-ONLY.** `anzctr.py` warns "local filter kept 791 trials but the
  live search reported 776 — ANZCTR may have changed its export" to a human reading the log. Unattended, nobody
  reads it. Promote to a report line + a gate (it self-resolved between the two 2026-07-29 runs, which is exactly
  why it needs tracking rather than a one-off glance).
- **D3 — `output_validator`'s 1,047 flags are UNCHARACTERISED.** WARN-only today. Triage into (i) known validator
  crudeness (e.g. same-type histology+stage AND — satisfiable and faithful) vs (ii) real defects. Feeds C4.
- **D4 — reconcile + approval vocab re-run in FULL every cycle** (~10 of 17 min) even with zero churn. A
  fingerprint over the distinct-value set would skip them. Optimisation, not a defect.
- **D6 — `qa/invariants.py` — THE INVARIANT SWEEP (NEW 2026-08-05). Run it after any change to a mapping helper.**
  `python -m aus_trial_universe.qa.invariants` (exit 1 on any violation). Six invariant classes over the whole live
  corpus (4,978 cancer_type + 837 gene + 171 signature), deterministic, no LLM: **C1** a normaliser must not
  truncate a WORD · **C2** every deterministic transform is idempotent · **C3** canonicalisation must not INVENT a
  code/term · **C4** parse→render round-trips · **C5** the name mirrors `oncotree_code_FINAL` · **C6** two values
  sharing a concept key must map to the same code. Currently **0 violations**.
  **Why it exists:** every defect found on 2026-08-05 was in a *deterministic helper* inside stage 2, and each was
  found by accident. The helpers are pure functions over a known corpus, so they can be swept exhaustively instead.
  Its first run found 37 violations in 3 classes — 23 word-truncations in the gene `concept_key` (unbounded
  `str.replace`, including the meaning inversions "ineligible"→"in" and "unknown"→"un") and 13 non-idempotent keys
  (a normalisation pass exposing a pattern the previous pass could not see). Extend it whenever a new deterministic
  helper lands; a violation here is a bug in OUR code, never in the data.
- **D5 — REVIEW `qa/` AGAINST THE PORTED MATCHING-ENGINE LOGIC (user, 2026-08-05).** Revisit the whole `qa/`
  package now that `engine_port.py` + `engine_conformance.py` encode the engine's parser: which of our checks
  duplicate it, which contradict it, and which of its constraints we should NOT adopt. **Standing principle from
  the user: the matching engine is NOT ground truth** — it is mid-refactor, it has its own bugs, and *we must
  never degrade our output quality to satisfy it.* Concretely in scope: (i) the genetics port is pinned to
  `oncoact@a97142938` (May 2026) while the engine has moved on — re-pin and re-run its 12-test fidelity check
  first; (ii) there is **no oncology-side port**, though `TrialReader.parseOncology` is where a throw discards a
  whole trial — a transcription exists in scratchpad form from the 2026-08-05 investigation and should be
  promoted if we keep this line of QA; (iii) the engine-side oncology gaps found on 2026-08-05 are not yet in
  `docs/planning/matching_engine_capability_gaps.md` — chiefly that `parseOncology` is single-code-per-line with
  no boolean support, so it reads `NSCLC AND NOT(LUSC)` as **positive LUSC** (221 trials), silently inverting an
  exclusion into an inclusion, and drops all but the last code of any `A OR B`.

### E. Long-standing / previously deferred with the user's agreement
- **219 trials `faithful=False`** (hard multi-cohort extraction, best-of-6) · **weighted "best attempt"** in
  `refine()` (parked pending calibrated reviewer severity) · **extraction convergence** on hard trials ·
  **run-comparison method** (spec §12) · **`make` command-set review** (mostly moot: legacy targets gone,
  `agentic-gates` added).
- **Effectively CLOSED:** the SQL-for-joined-tables decision (the Python join builds the export fine; external
  querying was dropped) and session 5's Stage-I ingestion / legacy-retirement work.

### F. Data durability & storage strategy — NOT started. **F1 is the highest-value quick win in this doc.**
User's question (2026-07-29): *"the codes have git, but the data files are too large to git push to a remote repo,
so what is the best practice for data files?"*

**The reframe that decides everything — the irreplaceable data is TINY** (measured 2026-07-29):

| bucket | size | recreatable? |
|---|---:|---|
| `inputs/` | 2.0 GB | registry downloads: re-fetchable but **point-in-time** (yesterday's CTGov cannot be re-downloaded) · `resources/drug_utility/` **621 MB RxNorm — needs a UMLS licence/account to re-fetch** |
| `transient/` | 276 MB | wipeable cache, pure speed (`make agentic-clean`) |
| `masters/` | 74 MB (**28 MB** current TSVs) | **NO — this is the LLM-curated output**; **2.9 MB gzipped** |
| `derived/` | 48 MB | deterministic from masters (`make agentic-export`) |

So the problem is NOT "how do I version 2.4 GB". It is **"how do I durably version ~3 MB with provenance, and treat
the other 2.4 GB as a cache"**. The existing role-based layout (`inputs/ masters/ derived/ transient/`) already
encodes that split, so the policy falls straight out of it.

- **F1 — TIER 1: put the curated masters IN GIT, UNCOMPRESSED. Do this FIRST (≈an afternoon).**
  28 MB of TSVs costs ~3 MB in git, and because it is *text* git delta-compresses across versions — a refresh that
  changes 26 rows stores almost nothing. Do NOT pre-gzip: a gzipped blob is opaque to git and forces a full new
  object every version, throwing away the delta AND the diff.
  **The bonus is the thing no blob store gives you:** `git diff` on `interpreted_eligibility.tsv` shows exactly what
  a curation run changed. That is the long-deferred **run-comparison method** (spec §13 / group E) almost for free,
  and it directly serves **C1** (compare vs the v1 output) and **C3**.
  - Commit on **verified releases only** (gates PASS/WARN + report attached), NOT every run.
  - Location: either a sibling `trial-curator-data` repo, or a `data-releases/` directory in this repo that is NOT
    gitignored. (A sibling repo keeps the code history clean; same-repo keeps one clone. Either is defensible.)
  - Commit the run report + `MD5SUMS.txt` alongside the TSVs — **both are already generated**, so provenance is free.
- **F2 — TIER 2: the bulky point-in-time inputs → a versioned GCS bucket.** Hartwig is already a GCP shop (the ACTIN
  image lives in `europe-west4-docker.pkg.dev`, and `gcloud auth application-default login` is in the README), so
  auth/IAM already exist. A **versioned bucket + a lifecycle rule** replaces the hand-rolled keep-5 pruning;
  `gsutil -m rsync` is a one-liner to add to `refresh`. **Push the input snapshot that PRODUCED a release, keyed by
  the same release label** — that is what makes a curation run *reproducible* rather than merely *recorded*.
- **F3 — TIER 3: never store** `transient/cache/` (an optimisation) or `derived/` (regenerates deterministically;
  `make agentic-export SNAPSHOT=1` already mints an immutable bundle when a frozen deliverable is needed).
- **F4 — the ACTUAL URGENT GAP: everything is on ONE laptop.** The masters, the only backup, AND the licence-gated
  RxNorm resource all sit on the same disk — `data/backups/RECOVERY.md` documents restoring from a snapshot that
  lives on the very disk it is protecting. **A single disk failure costs ~2,000 trials of curation.** F1 (pushed to
  a remote) fixes the important half of this immediately, which is why it is first.
- **Rejected alternatives (do not re-litigate):** **Git LFS** — built for large binaries; our precious data is small
  text, so LFS surrenders diffs and adds quota cost for no benefit. **DVC** — would work and is the textbook
  "git for data", but it is a whole tool + workflow for a dataset that fits in git at 3 MB. **A database** — already
  ruled out for querying (`v2-joined-tables-sql-decision`) and it is the wrong shape for durability anyway.
- **Compliance note (why a cloud bucket is fine here):** this is **public trial-registry text + drug annotations —
  NO patient-level or clinical data**. Stating that explicitly removes the question that would otherwise stall the
  decision under Hartwig's data-handling rules.

---
## TL;DR
The v2 rewrite is a **two-domain agentic pipeline**: the ELIGIBILITY path (extract → the three-stage map) and
the DRUG UTILITY path (`make drug-ref-build`: a separate incremental drug-annotation build, now incl. Phase-2
main/aux roles). Both emit **3NF relational tables** that join on `trial_arm_id` (via the shared `trial_arms`
registry); the grand flat matching-engine file is built by `make agentic-export` → Set A `trial_eligibility.tsv`
(+ Set B = the drug tables referenced in place). Pattern B throughout: deterministic Python owns control flow
(parallelism, refine loop, per-item durable saves); the LLM fills the doer/reviewer slots. **445 unit tests pass**
(fake-client, no API). Eligibility output is a **DNF** table — one row = one satisfiable (trial, arm) conjunction;
rows ORed, cells ANDed, exclusions inline `NOT(...)`. Mapping is **THREE STAGES**, the same three for every
vocabulary column AND for the drug approvals: **1 translate** (`--map-only`; LLM doer→reviewer, one value at a
time) → **2 canonicalise** (`--reconcile`; deterministic, no LLM) → **3 finalise** (the approved register in
`mapping/adjudications/`). Every stage is a function of a SINGLE value, which is what makes churn structurally
impossible rather than merely mitigated. Eligibility curation
is COMPLETE; drug Phase-2 + the export are DONE.

## Quickstart
Conda env `trial_curator` (auto-selected); `OPENAI_API_KEY` auto-loaded from `.env`. Needs `openai>=2.x`.
**Wrap live runs in `caffeinate -i …`** so laptop sleep doesn't drop the connection (memory `feedback-caffeinate-long-jobs`).
```bash
make agentic-run ID=NCT06881784                 # one trial, full per-trial pipeline (source auto-detected)
make agentic-run IDS=NCT1,ACTRN2,NCT3           # a specific set
make agentic-run EXTRACT_ONLY=1 RESUME=1 WORKERS=80 MAX_CONCURRENCY=500   # the full-universe extract (2026-07-25 settings)
# MAPPING (over the frozen extract store; via python -m aus_trial_universe.run):
python -m ...run --map-only  --workers 500 --max-concurrency 500 --no-cache-prune   # STEP 1: per-value maps + joined/mapped_eligibility.tsv (500 = ~92% TPM; 2026-07-28)
python -m ...run --reconcile --workers 30  --max-concurrency 60                     # STEP 2: reconcile -> finalised_*_map.tsv (store) + joined/finalised_mapped_eligibility.tsv
make agentic-export                             # BUILD THE DELIVERABLE: Set A export/trial_eligibility.tsv + MANIFEST (Set B in place)
make agentic-export SNAPSHOT=1                  # + an immutable export/snapshot_<ts>/ bundle (Set A + frozen drug tables)
make agentic-demo                               # ISOLATED presentation demo: full pipeline over 2 trials -> data/agentic/demo/ (shared cache + seeded drug ref = 0 web search; ~35s). IDS=… to override; RESET=0 to keep.
make agentic-arm-consistency                    # referential-integrity check on the trial_arm_id join key (incl. role table)
make agentic-clean                              # wipe the transient data/agentic/{log,cache} only
make agentic-gates                              # PRODUCTION GATES on the current on-disk state (exit 1 on any FAIL). No API
make agentic-tests                              # 445 unit tests, no API
# run.py flags: --workers N · --max-concurrency N (global API cap; TPM-bound — probe x-ratelimit headers) ·
#   --map-only (stage 1: map distinct interpreted cells -> the *_map_initial tables + joined/mapped_eligibility.tsv) ·
#   --reconcile (stages 2+3: -> *_map_{reconciled,finalised}.tsv in the store + joined/finalised_mapped_eligibility.tsv; NO LLM) ·
#   --skip-drug · --no-cache · --no-cache-prune · --extract-only · --no-judge · --no-review · --store-root DIR · --max-attempts N
```
Eligibility store `data/agentic/masters/eligibility/current_version/` = **12 pure-3NF tables** (2 content + 9 stage maps + `arm_scope`); each per-trial run loads it, upserts, writes back in place (supersede by moving
`current_version/` → `archive/<date>/`). Denormalized Step-1/2 flat views live in **`data/agentic/derived/joined/`** (`mapped_eligibility.tsv`,
`finalised_mapped_eligibility.tsv`). The grand matching-engine flat file is **`data/agentic/derived/export/trial_eligibility.tsv`**
(built by `make agentic-export`, NOT `agentic-run`). Per-cycle records → `data/agentic/run_report/` (keep 5) +
`STATUS.json`. Log → `data/agentic/transient/log/…`. Drug reference is a SEPARATE build:
`make drug-ref-build …` → `data/agentic/masters/drug_annotations/current_version/` (6 core + 2 approval-vocab tables).
`make agentic-validate` now targets the Set-A export (repointed in session 6) and is also folded into the gates as a WARN.
Full detail: `docs/reference/combined_agentic_run.md`; drug path: `docs/reference/drug_ref_schema.md`;
eligibility design: memory `v2-eligibility-orchestration-model`.

## What's built
> **NOTE (session 5 flatten):** the package root is now **`aus_trial_universe/`** (the `agentic/` sub-layer was
> removed; every import is `aus_trial_universe.…`, no `.agentic.`). Read the paths below WITHOUT the `agentic/`
> segment. NEW in session 5 (not shown in the tree below): top-level **`ingest.py`** (Stage-I download CLI) +
> **`refresh.py`** (the `make agentic-refresh` end-to-end orchestrator), and **`tasks/ingestion/`** =
> `ctgov.py` (CT.gov API-v2 download+filters+POTTR), `anzctr.py` (curl_cffi all.xls download+filter),
> `pottr_ids.py` (POTTR trial-id/alias loaders + removal list), `expiry.py` (recoverable trial expiry/restore).
> Inputs are now versioned `inputs/trial_universe/<reg>/current_version/` (+ `archive/`). Legacy
> `eligibility_path`/`drug_utility_path` are DELETED.
```
aus_trial_universe/agentic/
  run.py                     # ORCHESTRATOR. Per-trial: extract -> map (lookup-first) -> checkpoint to current_output/; then drug top-up. STORE-WIDE mapping modes (work off the frozen extract): `--map-only` = _run_map_only() (Step 1: map_all_columns pools all 3 columns' distinct values in ONE concurrent pool -> 3 map tables in current_output/ + joined/mapped_eligibility.tsv; raw/interpreted NEVER re-persisted); `--reconcile` = _run_reconcile() (Step 2: reconcile maps -> finalised_*_map.tsv in current_output/ + joined/finalised_mapped_eligibility.tsv). DiskCache; a failing trial is logged & skipped. (The parked _build_combined was RETIRED 2026-07-28 -> export.py.)
  export.py                  # THE MATCHING-ENGINE EXPORT (make agentic-export; 2026-07-28). build_export_rows() joins interpreted ⋈ trial_arms ⋈ trial_info ⋈ FINAL vocab maps ⋈ per-arm raw intervention names (arm_intervention_names_raw, the Set-B join key) -> Set A export/trial_eligibility.tsv (33 cols, one row per (trial_arm_id, conj)) + MANIFEST (Set B = drug tables referenced in place). --snapshot -> immutable export/snapshot_<ts>/. Deterministic, no API
  demo.py                    # ISOLATED presentation demo (make agentic-demo; session 3). Re-roots ONLY the OUTPUT path constants in core.paths -> data/agentic/demo/ BEFORE importing run/build/export, then drives their real entry points as 5 banner-headed stages. Inputs + cache stay shared. NO core code touched. Nothing imports it.
  trial_info.py              # NEW trial-metadata master (2026-07-28): TrialInfo dataclass + ctgov/anzctr extraction (deterministic, from raw protocolSection / ANZCTR rows) + save/load -> data/agentic/masters/trial_info/current_version/trial_info.tsv
  core/
    paths.py                 # SINGLE relocatable DATA_ROOT (=data/agentic/) + all derived paths + current_version_dir()/archive_current_version()
    client.py                # LlmClient: .parse() (chat.completions) + .research() (Responses API web_search); cache, retries, tracing. NB: _fingerprint hashes the output SCHEMA into the cache key (so reviewer schemas stay stage-local — see review.py)
    agent.py                 # Agent = prompt + schema + model (+ web_search flag) bound to the client
    workflow.py              # generic fan_out() + refine() (bounded check->repair loop) + run_parallel (per-item durable sink)
    review.py                # SHARED doer->reviewer harness review_refine() (2026-07-27): the ONE loop mechanism for ALL stages (extraction, mapping, drug, ANZCTR arm-ID); verdict-AGNOSTIC. Memory v2-shared-loop-harness
    pipeline_io.py           # dated-file / version-dir selection (copied from eligibility_path); versioned datasets now read current_version/ via paths.py
    prompt_registry.py, cache_prune.py, logfmt.py  # live-agent enumeration + cache GC + shared run-log formatting
  tasks/shared/              # PATH-NEUTRAL arm identification (used by BOTH paths; 2026-07-25)
    cohorts.py               # Cohort, trial_arm_id() slug, anzctr_regimes / extract_anzctr_drugs (fresh derivation), resolve_cohorts
    agents.py                # ANZCTR drug doer + reviewer (DrugExtraction / RegimeVerdict) — moved here from eligibility
    schema.py, store.py      # TrialArm + TrialArmStore -> data/agentic/masters/trial_arms/current_version/trial_arms.tsv
  tasks/drug_utility/        # DRUG UTILITY PATH — 6 3NF tables; see docs/reference/drug_ref_schema.md
    schema.py, store.py      # 6-table schema (+ trial_arm_drug_role: (trial_arm_id, canonical_id)->role main|auxiliary, 2026-07-28) + DrugRefStore (roles/put_roles/remove_trial_roles/role_trial_arm_ids)
    rxnorm.py, pottr.py      # DETERMINISTIC offline lookups (rxcui+atc; POTTR class walk); pottr.refresh_pottr() downloads POTTR
    agents.py, workflow.py, build.py  # 3 web_search doer->reviewer pairs + the Phase-2 role classifier (classify_arm_roles + ArmContext, NO web_search) + build_drug_ref (+ writes trial_arms) + CLI (role pass runs after the drug build)
    migrate_trial_arms.py    # re-key trial_to_intervention to trial_arm_id + report add/deletes (make agentic-drug-migrate-trial-arms)
  tasks/eligibility/         # ELIGIBILITY PATH (2 content tables keyed by trial_arm_id + mapping + tools + qa)
    extraction/              # STAGE I: free text -> DNF eligibility rows
      loaders.py             # ctgov/anzctr assembly + cohort enumeration (arm_type, drug); load_trials(id/ids/all)
      agents.py              # cohort-aware extractor + 5-reviewer panel (raw + interpret sub-stages)
      schema.py, workflow.py # DnfRow (Cohort imported from tasks/shared); extract_trial() = raw -> interpret -> panel -> refine -> distribute
    mapping/                 # STAGE II: map the DNF cells -> vocab (doer -> reviewer, on core.review.review_refine). ALL 3 mappers SIGNED OFF (2026-07-28)
      agents.py, schema.py   # oncotree / gene / signature mappers + reviewers (all validated) + the Step-2 reconcile adjudicators (_ONCOTREE_RECONCILE_RULES + reviewer + finding-model analogs); schema has OncotreeMapping/FindingModelMapping/ReviewVerdict(+suggested_fix)/GroupReconciliation
      workflow.py            # map_cancer_types/gene_alterations/molecular_signatures + map_all_columns (ONE pool across all 3); _oncotree_logic_problems (parens/precedence via _top_level_has)
      reconcile.py           # STEP 2 (NEW 2026-07-28): detect(find_inconsistencies) -> deterministic pre-pass (repair_oncotree_code name->code + normalize_or_order) -> adjudicate_group (LLM) -> write_finalised_maps (3NF, to store) + write_finalised_mapped_eligibility (flat, to joined/)
    # ⚠ TREE BELOW IS PRE-2026-08-04. mapping/ is now split per column (cancer_type/ gene_alteration/
    #   molecular_signature/ + shared finding_model.py, schema.py, workflow.py, reconcile.py) and tools/ is GONE.
    schema.py, store.py      # ArmEligibilityRaw + InterpretedEligibility + 5 map dataclasses + MAPPED_ELIGIBILITY_COLUMNS; EligStore.save_maps() / save_mapped_eligibility() (write ONLY new files, never re-persist content tables)
    tools/
      oncotree.py            # oncotree.YAML vocab + valid_codes + ancestors + vocab_reference() (indented Name (CODE) tree) + name_to_code() (reverse, for Step-2 repair); 3 sentinels
      finding_model.py       # FULL grammar validator (2026-07-28): finding_model_problems() = field/enum/scope/HGVS-aware DSL parser (CLASS_SPEC) — the hard SYNTAX gate; + GRAMMAR_REFERENCE
    # ⚠ eligibility/qa/ IS GONE (2026-08-05, user: it collided with the top-level qa/). validate_output.py and
    #   arm_consistency.py moved to the top-level `qa/` (they are QA ENTRY POINTS, and arm_consistency checks BOTH
    #   paths' tables); mapping_consistency.py -> `mapping/consistency.py` (a Step-2 library, not an entry point).
tests/agentic/               # 162 tests (fake-client); mirrors tasks/ (core [+test_review], tasks/shared, tasks/eligibility [+ test_mapping_findingmodel, test_run_map_only, test_reconcile, mapping/test_consistency], tasks/drug_utility [+ test_drug_utility_roles]) + top-level test_export.py + test_trial_info.py
scripts/agentic/pipeline.sh  # driver: python-pick, .env, tests-preflight, log tee; subcommands run|validate|export|clean|tests|cache-prune|arm-consistency|drug-migrate-trial-arms|drug-ref-build|drug-ref-refresh-pottr
scripts/agentic/demo.sh      # STANDALONE demo driver (make agentic-demo; session 3): python-pick, .env, reset data/agentic/demo/, SEED demo drug ref from production (=> 0 web search), tee. Does not touch pipeline.sh.
docs/reference/combined_agentic_run.md   # run/setup guide
```

## Output schema (v2 — shared arm registry + 3NF stores + trial_info master + `joined/` views + the `export/`; updated 2026-07-28)
Arm identity lives once in the **shared `trial_arms` registry** `data/agentic/masters/trial_arms/current_version/trial_arms.tsv`
(`trial_arm_id → trialId, registry, arm, arm_type`; `trial_arm_id` = deterministic `{trialId}::{arm}` slug). The
eligibility store `data/agentic/masters/eligibility/current_version/` holds **12 pure-3NF tables**; the drug store holds **6**;
the new **`trial_info`** master holds trial-level metadata; Step-1/2 denormalized views live in
**`data/agentic/derived/joined/`**; the matching-engine deliverable lives in **`data/agentic/derived/export/`**. Eligibility tables
hold **NO drug info**; drugs join via `trial_arm_id` to the drug utility path.

**3NF store — `eligibility/current_output/`:**
- `arm_eligibility_raw.tsv` — `trial_arm_id` → 5 VERBATIM raw cells (inline `[source]`, `|`-delimited). Audit anchor.
- `interpreted_eligibility.tsv` — `(trial_arm_id, conj_id)` → 5 interpreted DNF cells (inline `NOT()`; rows sharing
  `trial_arm_id` are ORed). **The FROZEN source for mapping — never re-derived by Step 1/2.**
- **`<column>_map_{initial,reconciled,finalised}.tsv`** — THREE tables per vocabulary column, one per stage, so
  the outputs mirror the process. Columns ACCRETE, so `_finalised` carries the whole chain and a difference
  between `_reconciled` and `_finalised` IS an approved override. `_finalised` ships. All nine come from the one
  spec in `mapping/stage_tables.py`. The retired flat names are still READ as a fallback so old archives load.
- `finalised_cancer_type_map.tsv` — `cancer_type` → `oncotree_name, oncotree_code, oncotree_code_FINAL` (Step-2
  reconciled in the added `*_FINAL` col; Step-1 cols preserved). **Still 3NF (single-key lookup) → lives here.**
- `finalised_gene_alteration_map.tsv` / `finalised_molecular_signature_map.tsv` — value → `finding_model, finding_model_FINAL`.
- `arm_scope.tsv` (NEW, session 6) — `trial_arm_id` → `scope_verdict, scope_reason, source`. **Only arms with NO
  interpreted rows get a row.** Records WHY an arm is empty so "correctly out of scope" is distinguishable from
  "extraction missed it": `healthy_volunteers` · `not_oncology` · `population_not_cancer_selective` ·
  `no_eligibility_text` · `unexplained` (the miss signal — the `empty_output_reasons` gate FAILs on it unless the
  arm is registered in `qa/waivers.py`). Deterministic verdicts first (CTGov's structured `healthyVolunteers`
  field + tight healthy-volunteer text rules), LLM only for the residue. Built by refresh stage 7/9; idempotent.

**Trial metadata master — `trial_info/current_version/trial_info.tsv`** (NEW 2026-07-28): one row per trialId →
`official_title, phase, overall_status, study_type, lead_sponsor, min/max_age, sex, start/primary_completion/
completion_date, last_update_date, countries, has_AU_site, AU_site_status, AU_site_cities, trial_url` — deterministic
from raw CTGov `protocolSection` / ANZCTR rows (no LLM). Feeds the export's trial-info columns.

**Drug store — `drug_annotations/current_version/` (6 core tables + 2 symmetric-match maps):**
`intervention_to_canonical`, `trial_to_intervention`, `drug_annotations_core`, `drug_target_actions`,
`drug_regulatory_approvals`, + `trial_arm_drug_role` (`(trial_arm_id, canonical_id) → role` main|auxiliary; 12,102
rows); + the additive `approval_cancer_type_map` (385) / `approval_biomarker_map` (211) symmetric-match vocab maps
(session 4; `make drug-ref-map-approvals`). See `docs/reference/drug_ref_schema.md`.

**Denormalized views — `data/agentic/derived/joined/` (split per producing subsystem into `eligibility/` + `drug_annotations/`):**
- `joined/eligibility/mapped_eligibility.tsv` — Step-1 flat: interpreted ⋈ (Step-1 maps), 1:1 with interpreted; cols
  = the 2 keys + the 5 interpreted cells + `oncotree_name/code`, `gene_alteration_findingmodel`, `molecular_signature_findingmodel`.
- `joined/eligibility/finalised_mapped_eligibility.tsv` — Step-2 flat: the above + appended `oncotree_code_FINAL`,
  `gene_alteration_findingmodel_FINAL`, `molecular_signature_findingmodel_FINAL`. **The eligibility-side review artifact.**
- `joined/drug_annotations/mapped_drug_regulatory_approval.tsv` — the drug-side flat: one row per
  `(canonical_id, indication_id)` = each approval ⋈ its cancer_type/biomarker vocab maps (`oncotree_code`/`_FINAL`,
  gene & signature finding-models, the expression free text) + `tga_status`/`pbs_status`. **The drug-side review artifact.**

**The matching-engine EXPORT — `data/agentic/derived/export/` (the deliverable; `make agentic-export`):**
- `trial_eligibility.tsv` (**Set A**) — the grand join, **33 cols, one row per `(trial_arm_id, conjunction_index)`**:
  keys/arm ⋈ `trial_info` ⋈ interpreted eligibility ⋈ **FINAL** vocab (`oncotree_code/name`, `*_findingmodel`) ⋈
  the per-arm raw intervention names (`arm_intervention_names_raw`, the Set-B join key). 17,659 rows. (The 5
  denormalized drug rollups were dropped 2026-07-28 — reach them via Set B on `arm_intervention_names_raw` +
  `trial_arm_id`.)
- `MANIFEST.md` — documents both sets + points to **Set B** = the 6 drug tables **in place** at
  `drug_annotations/current_version/` (no duplicate). `SNAPSHOT=1` → `export/snapshot_<ts>/` = Set A + a frozen copy of Set B.
See `combined_agentic_run.md` §"The matching-engine export" for the full column list + per-column notes.

## Locked decisions (don't re-litigate)
- **THE DNF INVARIANT — precise statement (2026-08-04).** The loose phrasing "one row = one satisfiable
  conjunction" is only true of the FREE-TEXT layer, and read the wrong way it argues for exploding a gene-family OR
  into one row per gene, which would be a serious mistake. The invariant we actually maintain:
  > **One row = one conjunction of SOURCE CRITERIA. Within a cell, a disjunction is permitted ONLY where it
  > enumerates the vocabulary tokens of a SINGLE criterion.**
  Row grain belongs to EXTRACTION (`interpreted_eligibility` is keyed `(trial_arm_id, conjunction_index)`);
  mapping is a value→value lookup and structurally cannot add or remove rows (export rows 17,830 before and after
  the gene correction). An OR inside a MAPPED cell is an artifact of the target vocabulary being less expressive
  than English — "TP53 alteration" is one atom needing three terms, "RAS mutation" one atom needing three genes,
  "SWI/SNF complex alteration" one atom needing 93. Measured: only **3 of 900** interpreted cells hold a genuine
  positive OR (those 3 ARE violations → backlog C4); the other **132** OR-bearing expressions were introduced by
  mapping. Because a cell holds the mapping of exactly ONE free-text value, every disjunct in it came from one
  source concept **by construction**. Splitting them into rows would present one criterion as N cohorts, destroy
  the per-arm conjunction count, expand 132 values into 639 disjuncts, and hand the consumer a shape it models
  LESS naturally than the flat OR it already has. Full statement + measurements:
  `docs/planning/archive/v2_gene_alteration_correction_spec.md` §3b; short form in `mapping/gene_alteration/expr.py`.
- **gene_alteration vocabulary (2026-08-04, user-endorsed).** The finding-model grammar is grounded on the **Java
  datamodel**, not the curated spreadsheet: `transcriptImpact.effects` is a `VariantEffect` (SPLICE is NOT a member —
  it is a `CodingEffect`); `GainDeletion.type` includes `CN_NEUTRAL_LOH`; `affectedCodon` exists; HLA is
  `HlaAllele`, never `PharmocoGenotype`. The **EGFR sensitising class** = exon 19 deletion · L858R · G719X · L861Q ·
  S768I (T790M and exon 20 *insertions* are excluded as resistance/insensitive; note S768I is a point mutation in
  the same exon). **"Activating" ≡ "sensitising"** for EGFR, in every context, and neither is a droppable qualifier.
  Genes where an unspecified "alteration" must NOT include `type=GAIN`: **ALK, ROS1, RET, NTRK1/2/3, NRG1, FGFR3** —
  and explicitly NOT FGFR2 (amplification is actionable in gastric), FGFR1, MET, EGFR, ERBB2. ALK amplification IS a
  driver in neuroblastoma; the no-GAIN rule is NSCLC-facing.
- **Exclusions are judged by REFERENT, not by qualifier (2026-08-04).** Inside `NOT(...)`: a named SPECIFIC
  alteration is kept and excluded exactly; a named CLASS WITH ESTABLISHED MEMBERSHIP is kept and expanded; only an
  availability/actionability judgement over an UNSPECIFIED set is omitted. Dropping a qualifier broadens an
  INCLUSION (safe) but narrows an EXCLUSION — and an over-exclusion invisibly denies a patient a trial they
  qualify for, whereas an omitted exclusion is recoverable by the reviewing clinician.
- **`_GENE_ESCALATION` is pinned (2026-08-04).** `mapping/workflow.py` gives the gene column its own escalation
  suffix. Not style: the 909 approved mappings were produced with that exact wording, the reviewer input is hashed
  into the cache key, and changing it would re-roll reviewed answers. Same class of trap as pinning
  `--max-attempts` (which defaults to 6 in `run.py` but was **3** for the approved gene run).
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
- **Mapping — gene/signature + Step 2 (SIGNED OFF 2026-07-28; memory `v2-mapping-stage-decisions` §8/§9/§10):**
  finding-model SYNTAX is enforced by a **full deterministic grammar validator** (`finding_model_problems`,
  field/enum/scope/HGVS-aware) — the reviewer judges only semantics. **"X mutation" → `SmallVariant[gene=X]` ONLY**
  (expansion reserved for genuinely unspecified "X alteration"/"aberration"); **gene FAMILIES expand** (`RAS`→
  KRAS/NRAS/HRAS) and the **HRR panel** = the PROfound 15 genes; disease-`NOT()` **converted when definitional**
  (`NOT(BCR-ABL-positive leukemia)`→`NOT(Fusion[BCR::ABL1])`), open-ended/inexpressible-qualified NOT() **omitted**
  (never broadened → over-exclusion). Signature = 6 terms only, `""` is common+correct for non-signatures.
  **Step 2 reconciliation:** unify semantically-EQUIVALENT values to one code (most-specific covering); KEEP genuine
  grade/subtype/organ distinctions apart; deterministic name→code repair for leaked names + OR-order normalise.
- **3NF discipline (2026-07-28):** `eligibility/current_output/` + `drug_annotations/current_version/` hold ONLY 3NF
  tables (the `finalised_*_map.tsv` are 3NF single-key lookups → they live in the store); ALL denormalized/joined
  views (`mapped_eligibility`, `finalised_mapped_eligibility`, `combined`) live in top-level `data/agentic/derived/joined/`.
- **Drug (2026-07-10):** `main_drugs` = the **investigational agent(s) under study by judgement** (not the
  whole regimen — backbone/SoC/comparator/placebo go to `auxiliary_drugs`); `pottr_drug_class` + `drug_class`;
  `tga_status`/`pbs_status` **per main drug** (Approved/Not approved) + `tga_detail`/`pbs_detail`
  (year + evidence + official link), via web search.
- **Refine = incremental repair (2026-07-10):** on a gating FAIL the extractor gets its OWN prior table + only
  the flagged issues, and keeps unflagged rows verbatim (preserves correct work, aids convergence).
- **arm_type** per cohort from CTGov `armGroups[].type`.
- **One output + one log**, streamed per trial; `--selected` retired; modes = `ID` / `IDS` / all.

## Shelved / open
- **DRUG PATH Phase 2 — main vs auxiliary role. ✅ DONE 2026-07-28 (see START-HERE).** Shipped as the 6th 3NF table
  `trial_arm_drug_role` at (trial_arm_id, canonical_id) grain — NOT a column on `trial_to_intervention` (the
  original plan) because ~11% of input strings bundle mixed main+aux drugs; canonical grain is exact + joins to
  TGA/PBS. Per-arm classifier (no web_search) reusing the recovered `DRUG_CURATOR_INSTRUCTIONS`; populated over the
  universe; consumed via Set B (`trial_arm_drug_role`), joined from Set A on `trial_arm_id` (the export no longer
  denormalizes the role-split drug names). (Original deferred note preserved below for context.) *Was: deferred
  2026-07-20 as not on the critical path to the over-enumeration work.*
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
  `aus_trial_universe.ctgov.drug_ontology.*` module path. Benign leftover: `data/agentic/inputs/trial_universe/ctgov/
  extracted_trials/ctgov_field_extractions.csv` is not read by agentic (CTGov reads `input_trials/` + `download_state/`).

## Gotchas
- **⚠ `tests/agentic/test_refresh_exit_codes.py` DRIVES `refresh.main()` FOR REAL and only monkeypatches the stages
  it knows about — so ANY new side effect added to `refresh.main()` silently mutates PRODUCTION data when the test
  suite runs.** Found the hard way 2026-08-05: adding the stage-0 baseline snapshot made `make agentic-tests` write
  **nine** spurious `masters/eligibility/archive/05082026*` dirs into the live store (removed after verifying each
  was byte-identical to `current_version`). If you add a stage to `refresh.main()`, patch it in that test's fixture
  in the same commit.
- **A hand-edited map TSV bypasses `CancerTypeMap.__post_init__`,** so the derived `oncotree_name` goes stale while
  the code changes. `qa/invariants.py` C5 is the backstop; if you must edit a map directly, re-render the name.
- **SDK:** `openai 2.44.0`. `.parse()` uses `chat.completions.parse`; `.research()` uses the Responses API
  `web_search` tool (`client.responses.parse(tools=[{"type":"web_search"}], text_format=<pydantic>)`).
- **Env:** conda `trial_curator` (`/opt/anaconda3/envs/trial_curator/bin/python`); default `python3` lacks the deps.
- **Determinism:** the response cache is the deterministic layer; `temperature`/`seed` omitted (gpt-5.x rejects them).
- **Copy, don't import** from `eligibility_path`; agentic owns its copies (e.g. `pipeline_io.py`, the resource reads).
- **Paths:** import all data paths from `core/paths.py` — never hard-code `data/agentic/...`. Versioned datasets
  (resources + drug_annotations) live under `current_version/` (date in a metadata file) with prior sets in
  `archive/`; loaders use `current_version_dir()`, NOT `latest_version_dir` (which now serves only ctgov `input_trials`).
- **Data safety:** `data/` is gitignored (~31 GB). Never `git clean -fdx`. Layout is grouped by role
  (`inputs/ masters/ derived/ transient/` + `analysis/` + `demo/`). `make agentic-clean` is scoped to the wipeable
  `data/agentic/transient/{cache,log}` ONLY — it never touches `inputs/`, `masters/` (incl. the curated eligibility
  store — a safety fix; it used to wipe eligibility), `derived/`, or `analysis/`.


---

## History

Session-by-session narrative, superseded START-HERE blocks and completed-work detail are archived, newest
extraction first:

- [`planning/archive/handover_history_20260804.md`](planning/archive/handover_history_20260804.md) — sessions up
  to and including session 8 (the OncoTree correction shipping to production).

They are provenance, not a to-do list. Decisions that must not be re-litigated stay above, under
**Locked decisions**; traps that will bite again stay under **Gotchas**.
