# v2 Agentic Pipeline — Handover

## ▶ NEXT SESSION — START HERE (updated 2026-08-04, end of session 9)

> **WHERE THINGS STAND (2026-08-04, session 9).** **`B1b` (gene_alteration mapping) is DONE, reviewed, approved and
> MIGRATED TO PRODUCTION**, together with the `mapping/` restructure the user asked for. **350 tests green.
> Gates WARN · 0 FAIL. Everything is UNCOMMITTED — the user does ALL commits.**
>
> **What shipped:**
> - `tasks/eligibility/mapping/` is now split **per vocabulary column** — `cancer_type/`, `gene_alteration/`,
>   `molecular_signature/`, with the shared `finding_model.py` grammar at the `mapping/` level and `schema.py` /
>   `workflow.py` / `reconcile.py` as the shared driver. **`tasks/eligibility/tools/` is GONE** (its four modules
>   moved into the owning column). `reconcile_column`'s `is_oncotree: bool` became a `column:` discriminator.
> - **gene_alteration now HAS a stage 2** (`mapping/gene_alteration/reconcile.py`) — it previously short-circuited.
> - The finding-model grammar was **re-grounded on the Java datamodel** (`hmftools/finding-datamodel`), which fixed
>   `transcriptImpact.effects=SPLICE` (not a `VariantEffect` member — it is a `CodingEffect`), added
>   `affectedCodon` / `CN_NEUTRAL_LOH` / the full effect enums, and moved HLA to `HlaAllele`.
> - New **`gene_alteration_expressions` gate** (0 errors across 862 distinct expressions; it FAILed with 32 before).
> - New permanent QA: **`qa/engine_port.py` + `qa/engine_conformance.py`** — a transcription of the matching
>   engine's parser, pinned to `oncoact@a97142938` and validated against all 12 of its own tests. Run
>   `python -m aus_trial_universe.qa.engine_conformance` to grade the shipped export and split failures into
>   **OURS vs ENGINE**. Currently **OURS = 0**, ENGINE = 311.
> - **`docs/planning/matching_engine_capability_gaps.md`** — 7 engine-side gaps (G1-G8) with file:line references,
>   ready to send to the matching-engine team. **Not yet sent.**
>
> **▶ YOUR NEXT TASK: the A-group, now unblocked** — **A2** is partially done (the `mapping/` split); the remaining
> `pipeline/` + `outputs/` + `qa/` regroup is still open → **A3** legacy removal → **A4/A4b/A5**. Then the C/D/F
> backlog. **F1 (curated masters into git) remains the highest-value quick win in this doc.**
>
> Record: `docs/planning/archive/v2_gene_alteration_correction_spec.md`. Backups:
> `data/backups/pre_gene_alteration_20260804_2327/` + `known_good_20260804_post_gene_alteration/`.
>
> ---
> *Historical context from earlier sessions follows.*

## ▶ SESSION 8 (superseded, kept for provenance)

> **WHERE THINGS STAND (2026-08-04).** The pipeline is self-contained and periodically runnable end-to-end
> (`make agentic-refresh`), it verifies itself and fails loud, and the **OncoTree mapping correction has shipped**:
> the export carries **0 error-severity defects** across 572 distinct code expressions, guarded by a new
> `oncotree_expressions` gate. **261 tests green. Everything is UNCOMMITTED — the user does ALL commits.**
>
> **▶ YOUR NEXT TASK is `B1b`: refine the GENE_ALTERATION mappings exactly as B1 did OncoTree.** The measured
> baseline, the reusable machinery and the plan are in the **B1b** entry below; the playbook and the lessons that
> cost several iterations are in `docs/planning/archive/v2_oncotree_correction_spec.md` §10. Read that before touching prompts.
>
> After B1b: **A2** file restructure → **A3** legacy code removal → the rest of the backlog below.
>
> ---
> *Historical context from earlier sessions follows.*
>
> **▶ SESSION 6 = PRODUCTION HARDENING (unattended operation) + a surgical extraction repair.** The user's framing:
> *"in production this e2e pipeline runs on its own — without an AI monitoring its progress & applying fixes, so we
> can NOT move on until this is completely verified."* Everything below is UNCOMMITTED (the user does ALL commits).
>
> **What changed (detail in "Session 6" further down):** the pipeline now **verifies itself and fails loud** —
> `qa/gates.py` (8 deterministic gates run as refresh stage 9/9; any FAIL → **non-zero exit**, which `refresh.py`
> previously never returned), a per-run **`run_report/`** record + machine-readable **`STATUS.json`**, a new
> `arm_scope` table recording **why** an arm is empty (so "out of scope" ≠ "extraction missed it"), a waiver
> register (`qa/waivers.py`), and keep-5 retention for input archives + run reports. **212 tests green.**
>
> **The immediate to-do list is the "⏭ RESUME AT" block below** — it is the authoritative task list.
>
> **✅ 2026-08-04 — `B1` (OncoTree mapping) is DONE: reviewed, approved and migrated to production.** Export now
> carries **0 error defects, 0 warnings, 0 name/code mismatches** across 572 distinct code expressions, and a new
> `oncotree_expressions` gate keeps it that way. Full record: `docs/planning/archive/v2_oncotree_correction_spec.md`.
>
> **▶ NEXT: `B1b` — do the same for GENE_ALTERATION mappings** (user, 2026-08-04). Scoped and measured in the
> B1b entry below; the OncoTree work is the playbook and most of the machinery is reusable.
>
> *Then the previously agreed order:* (1) **A2** file restructure (`pipeline/` + `outputs/` + `qa/`) →
> (2) **A3** legacy module removal → the rest of the backlog.
> Safety: **`data/backups/known_good_20260729_post_e2e/`** = the verified end-state of session 6 (all masters +
> export + report + `MD5SUMS.txt`) + **`data/backups/RECOVERY.md`** — read that FIRST if anything looks wrong; it
> has the exact copy-back commands and the pruning rule (**always keep ≥1 `known_good_*`**: the masters are
> LLM-curated, gitignored, and cost hours to re-derive, so it is the only real fallback).
>
> **FINAL e2e OF SESSION 6 (2026-07-29 22:31, 29 min, exit 0):** `gates=WARN` (6 PASS · 2 WARN · **0 FAIL**) ·
> expired 0 · restored 0 · **curated 2** (`NCT06652438`, `NCT07524140` — both PRE-EXISTING trials that entered the
> universe because their records gained AU sites that day, not new registrations) · export **17,830 rows · 2,023
> trials · 5,284 arms · 33 cols** · FK integrity CONSISTENT (0 dangling) · additive-safety trial-count identity
> holds exactly at 2,041 · every delta reconciles (+9 arms = 1+8, +26 conjunctions = 2+24, +9 role rows for those
> 9 arms) · **0 unmapped cancer_type cells** for the new trials (the normal per-trial path maps correctly — the
> mapping gap in D1 only bites values changed OUTSIDE curation). The 2 WARNs are the intended ones: the 5 waived
> extraction misses, and `output_validator` at 1,046 flags / 1,838-of-2,023 trials clean.
> Report: `data/agentic/run_report/refresh_20260729_223139.md`; monitor file: `run_report/STATUS.json`
> (`status: ok`, `gates_verdict: WARN`).

**CURRENT STATE — the pipeline is now SELF-CONTAINED, periodically runnable end-to-end, and FLATTENED.** One command
`make agentic-refresh` does the whole loop: **ingest (download → filter → POTTR → version, both registries) → expire
trials that fell out of the kept universe (recoverable) → curate ONLY new trials (eligibility + drug) → reconcile →
approval vocab → export**. Stage-I ingestion now lives IN the package (`tasks/ingestion/`), so the legacy
`eligibility_path`/`drug_utility_path` trees were DELETED, and the `agentic/` sub-layer was flattened away —
**everything now lives directly under `aus_trial_universe/`** (import prefix `aus_trial_universe.` — no `.agentic.`).
Data stays under `data/agentic/` (grouped `inputs/ masters/ derived/ transient/`; DATA_ROOT not yet promoted). Prior
state is unchanged underneath: eligibility curation COMPLETE, drug utility signed off with roles + symmetric-match
vocab, the matching-engine EXPORT built. **`make agentic-tests` = 179 green.** **Everything session 5 is UNCOMMITTED**
(sessions 1–3 committed through `bae9e7a`; the user does ALL git commits).

**Latest full refresh (2026-07-29, acceptance run, CLEAN):** re-downloaded ctgov 1523 + anzctr 515; **expired 32**
(recoverable in `masters/{eligibility,trial_arms}/expired/`; 0 restored); **curated 71 new** trials e2e (drug
researched=34 / **reused_ref=73 = NO web search** / 0 failed; roles 123); export **2,020 trials · 17,786 rows**;
**arm-consistency CONSISTENT**; additive-safe (0 drugs lost 1275→1309, 0 trials vanished). Backups:
`data/backups/pre_stage1_*` + `pre_refresh_*`.

**✅ DONE THIS SESSION (2026-07-29, session 5 — self-contained Stage-I ingestion + legacy retirement + flatten; UNCOMMITTED):**
See memory `v2-stage1-ingestion` for the full phase-by-phase detail. Summary: `tasks/ingestion/{ctgov,anzctr,pottr_ids,
expiry}.py` (CTGov API-v2 + ANZCTR curl_cffi downloads ported in; POTTR loaders; recoverable expiry with POTTR-never-
expire + fraction guard); inputs now use `current_version/`+`archive/`; new `ingest.py`/`refresh.py` + `make
agentic-ingest`/`agentic-refresh`; loaders/trial_info read `current_version/`; legacy trees + tests + scripts + make
targets DELETED; `aus_trial_universe/agentic/*` → `aus_trial_universe/*` (331 import refs rewritten). RxNorm dep on
`drug_utility_path` severed (ANZCTR `iii_extract_drugs` dropped — agentic re-derives ANZCTR drugs via the LLM cohort step).

**⏭ RESUME AT — THE AUTHORITATIVE TO-DO LIST (dependency-ordered; agreed with the user 2026-07-29).**

### A. Do next, in this order (structure + cleanup; nothing here needs an LLM run)
- **A1 ✅ e2e run done** (session 6) — the user inspects its output + `run_report/` first.
- **A2 — RESTRUCTURE `aus_trial_universe/`. ⚠ PARTIALLY DONE 2026-08-04:** the `mapping/` split by vocabulary
  column shipped with B1b (`cancer_type/`, `gene_alteration/`, `molecular_signature/`; `tools/` deleted;
  `is_oncotree` → `column`). The `pipeline/` + `outputs/` + `qa/` regroup below is STILL OPEN.
- **A2 (remaining) — the top-level regroup (layout APPROVED by the user).** Six loose modules sit next to `core/`
  + `tasks/`; group them so every top-level folder answers one question:
  - `pipeline/` = the runnable entry points — `ingest.py` · `run.py` · `refresh.py` · `demo.py`
  - `outputs/` = what a run emits — `export.py` · `trial_info.py` · `run_report.py`
  - `qa/` **IS IN SCOPE (user, 2026-07-29)** — it already holds `gates.py` + `waivers.py`; ALSO move in
    `tasks/eligibility/qa/{arm_consistency,validate_output}.py` (`arm_consistency` checks BOTH paths' tables, so
    living under `tasks/eligibility/` is simply wrong). Leave `mapping_consistency.py` in eligibility — it is a
    library `reconcile` uses, not a QA entry point.
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
The v2 rewrite is a **two-domain agentic pipeline**: the ELIGIBILITY path (extract → map Step 1 → map Step 2) and
the DRUG UTILITY path (`make drug-ref-build`: a separate incremental drug-annotation build, now incl. Phase-2
main/aux roles). Both emit **3NF relational tables** that join on `trial_arm_id` (via the shared `trial_arms`
registry); the grand flat matching-engine file is built by `make agentic-export` → Set A `trial_eligibility.tsv`
(+ Set B = the drug tables referenced in place). Pattern B throughout: deterministic Python owns control flow
(parallelism, refine loop, per-item durable saves); the LLM fills the doer/reviewer slots. **212 unit tests pass**
(fake-client, no API). Eligibility output is a **DNF** table — one row = one satisfiable (trial, arm) conjunction;
rows ORed, cells ANDed, exclusions inline `NOT(...)`. Mapping is TWO steps: **Step 1** (`--map-only`) maps each
distinct value to vocab; **Step 2** (`--reconcile`) reconciles equivalent values to one code. Eligibility curation
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
make agentic-tests                              # 212 unit tests, no API
# run.py flags: --workers N · --max-concurrency N (global API cap; TPM-bound — probe x-ratelimit headers) ·
#   --map-only (Step 1: map distinct interpreted cells -> 3 map tables + joined/mapped_eligibility.tsv) ·
#   --reconcile (Step 2: reconcile maps -> finalised_*_map.tsv in the store + joined/finalised_mapped_eligibility.tsv) ·
#   --skip-drug · --no-cache · --no-cache-prune · --extract-only · --no-judge · --no-review · --store-root DIR · --max-attempts N
```
Eligibility store `data/agentic/masters/eligibility/current_version/` = **9 pure-3NF tables** (2 content + 3 Step-1 maps +
3 finalised maps + `arm_scope`); each per-trial run loads it, upserts, writes back in place (supersede by moving
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
    qa/                      # validate_output.py (make agentic-validate) + arm_consistency.py (make agentic-arm-consistency) + mapping_consistency.py (cross-value: canonical_key/find_inconsistencies — used by Step 2)
tests/agentic/               # 162 tests (fake-client); mirrors tasks/ (core [+test_review], tasks/shared, tasks/eligibility [+ test_mapping_findingmodel, test_run_map_only, test_reconcile, qa/test_mapping_consistency], tasks/drug_utility [+ test_drug_utility_roles]) + top-level test_export.py + test_trial_info.py
scripts/agentic/pipeline.sh  # driver: python-pick, .env, tests-preflight, log tee; subcommands run|validate|export|clean|tests|cache-prune|arm-consistency|drug-migrate-trial-arms|drug-ref-build|drug-ref-refresh-pottr
scripts/agentic/demo.sh      # STANDALONE demo driver (make agentic-demo; session 3): python-pick, .env, reset data/agentic/demo/, SEED demo drug ref from production (=> 0 web search), tee. Does not touch pipeline.sh.
docs/reference/combined_agentic_run.md   # run/setup guide
```

## Output schema (v2 — shared arm registry + 3NF stores + trial_info master + `joined/` views + the `export/`; updated 2026-07-28)
Arm identity lives once in the **shared `trial_arms` registry** `data/agentic/masters/trial_arms/current_version/trial_arms.tsv`
(`trial_arm_id → trialId, registry, arm, arm_type`; `trial_arm_id` = deterministic `{trialId}::{arm}` slug). The
eligibility store `data/agentic/masters/eligibility/current_version/` holds **9 pure-3NF tables**; the drug store holds **6**;
the new **`trial_info`** master holds trial-level metadata; Step-1/2 denormalized views live in
**`data/agentic/derived/joined/`**; the matching-engine deliverable lives in **`data/agentic/derived/export/`**. Eligibility tables
hold **NO drug info**; drugs join via `trial_arm_id` to the drug utility path.

**3NF store — `eligibility/current_output/`:**
- `arm_eligibility_raw.tsv` — `trial_arm_id` → 5 VERBATIM raw cells (inline `[source]`, `|`-delimited). Audit anchor.
- `interpreted_eligibility.tsv` — `(trial_arm_id, conj_id)` → 5 interpreted DNF cells (inline `NOT()`; rows sharing
  `trial_arm_id` are ORed). **The FROZEN source for mapping — never re-derived by Step 1/2.**
- `cancer_type_map.tsv` — `cancer_type` → `oncotree_name, oncotree_code` (Step-1, per-value).
- `gene_alteration_map.tsv` / `molecular_signature_map.tsv` — value → `finding_model` (Step-1, per-value).
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
