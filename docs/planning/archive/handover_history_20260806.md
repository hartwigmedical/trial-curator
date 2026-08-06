# Handover history — sessions 8 to 12 (to 2026-08-06)

Session-by-session narrative, moved out of `docs/v2_agentic_handover.md` on 2026-08-06 so the live handover
carries only the CURRENT state and the open backlog. Nothing here is actionable; it is kept because several
entries record *why* a decision was made, and the reasoning outlived the task.

The one-line arc: sessions 8-9 corrected the OncoTree and gene_alteration mappings; sessions 10-11 re-specced
mapping into three explicit stages after the user lost confidence in the old reconciler; session 12 finished all
three vocabulary columns, unified the stage tables, and put the DRUG APPROVALS on the same mapping logic.

## ▶ SESSION 12 · GENE_ALTERATION (shipped) — updated 2026-08-06

> **✅ gene_alteration stage-1 refinement is REVIEWED, APPROVED and MIGRATED TO PRODUCTION.**
> **418 tests green · 0 invariant violations · gates WARN / 0 FAIL · engine dry run OURS=0 · UNCOMMITTED.**
>
> ### ▶ YOUR NEXT TASK: repeat this for **molecular_signature** (the user, 2026-08-06: *"After this we will move
> to molecular signature"*). Measured baseline, already taken: **171 values · only 48 non-empty · 8 distinct
> expressions · a CLOSED 6-term vocabulary · stage 2 completely inert (0 canonicalisation changes, 0 groups,
> 0 rulings) · stage1 == FINAL for all 171.** So it needs **NO stage 2** — it currently rides the LEGACY R0-R8
> branch in the shared reconciler, which still holds a cross-value grouping detector and an LLM adjudicator that
> could fire on the next new value. That is a DELETION, not a build: give it `stage1` + `stage3` only, and retire
> `build_findingmodel_reconciler` + its reviewer (molecular_signature is their last consumer). The column is small
> enough to hand-review 100%, and its two failure modes are exactly: a genuine signature dropped to `""`, and a
> non-signature forced into one of the six terms. NB its 51 `empty_for_named_gene` warns are the GENE catalogue
> misapplied — signature has no catalogue of its own. One real finding to fix: both compound values are
> `MSS & NOT(MSI)`, where `NOT(MSI)` is vacuous (mutually exclusive values of one field).
>
> ### What shipped for gene_alteration
> - **Stage 2 is deterministic-only.** R2 (per-value LLM repair) + R4 (LLM group adjudication) and their four
>   agents DELETED, −306 net lines. Measured contribution before removal: ZERO. Verified byte-identical.
>   Detection survives — divergent groups are logged as residue. ⚠ `concept_key` still backs `qa/invariants.py`
>   C1/C2/C6; do not delete it.
> - **Stage 1 prompts refined and folded into production** (`mapping/gene_alteration/agents.py`). Provenance copy
>   The candidate was expressed as ANCHORED EDITS to the production prompt — the form to reuse next time.
> - **Stage 3 register: 2 → 9 rulings** (`qa/adjudications/gene_alteration.py`). Its docstring records THREE
>   values the review judge called regressions where the judge was WRONG and the new prompt is right (FGFR3,
>   NTRK3, KIT/PDGFRA-wild-type-in-GIST) — deliberately absent, so nobody "fixes" them later.
> - **7 bugs in our OWN checks/resources**, each pinned by a test: `fusion_driver_with_gain` misfiring on a stated
>   and on a negated amplification; `arm_terms_not_compound` on del17p + monosomy 17; and MAPK3, PIK3CB, PIK3CD,
>   PIK3CG missing from `known_genes.txt`.
> - **The harness is column-generic and LLM-judged**: `qa/prompt_harness/{columns,remap,compare,export_gene_review,
>   gene_judgements}.py`.
>
> ### Stage tables unified across ALL THREE columns (user, 2026-08-06: *"the idea is not to have duplicate code"*)
> `tasks/eligibility/mapping/stage_tables.py` is now the SINGLE definition of every column's stage tables — names,
> accreting columns, writers, readers and legacy fallbacks. `cancer_type/tables.py` and `gene_alteration/tables.py`
> are DELETED, and `paths.CANCER_TYPE_STAGE_FILES` / `paths.FINALISED_MAP_FILES` / the three
> `schema.TABLE_FILES` map entries are GONE — they were second copies of the same names, which is how a reader
> gets left behind. Only four things differ per column and they are DATA, not code: key column, value stem, stage
> count, and whether there is a derived column.
>
> The store now holds a consistent set:
> `{cancer_type,gene_alteration}_map_{initial,reconciled,finalised}.tsv` + `molecular_signature_map_{initial,
> finalised}.tsv` (two stages — it provably needs no stage 2). Retired flat files →
> `data/backups/retired_legacy_gene_signature_maps_20260806/`; the specs still READ those names so pre-restructure
> archives load.
> Verified: the shared module reproduces cancer_type's three shipped tables **byte-identically**, and the
> shipping values are unchanged by the rename. **`tests/.../mapping/test_stage_tables.py` is the PLUMBING test** —
> it asserts no module hard-codes a stage-table filename, because a renamed table FAILS OPEN (a reader on an old
> name finds nothing, returns `{}` and ships blanks with every gate still green).
>
> ### Migration result
> stage-1 rows changed **84** · FINAL rows changed **35** · **0 unexplained** · export rebuilt
> (17,778 rows · 2,021 trials) · `gene_alteration_expressions` gate 847 distinct expressions / **0 defects**.
> Backup: `data/backups/pre_gene_stage1_fold_20260806/`.
>
> ### 🔴 THREE LESSONS THAT COST REAL TIME — do not rediscover
> 1. **"Named vs specific", not "specific vs general."** My first G1 candidate omitted an actionability-qualified
>    exclusion whenever it named only a bare GENE. Wrong, by 12 values — the user caught it: *"I don't think this
>    should disappear actually. This should be a series of exclusions."* An enumerated gene list IS a named
>    referent; dropping it matches the trial to the driver-positive patients it explicitly refuses, and nothing
>    downstream recovers that. ~1,900 API calls sunk re-running.
> 2. **Do not answer semantic questions with string surgery** (user, standing): *"avoid regex in all these
>    processing and comparisons. just llm judgement instead."* Substring keys for per-value verdicts collided
>    three times running. Verdicts now come from an LLM judge; only *which* values differ stays deterministic, via
>    canonicalisation over parsed ASTs.
> 3. **Never build a review set by intersecting version key-sets.** It silently dropped the 4 values that postdate
>    the 28 July baseline — one of them changed by the run — so the review covered 909 of 913. Only the migration
>    guard ("every changed row must be explained") caught it. The universe is the CURRENT corpus.
>
> ---
> *The mid-flight block from earlier in session 12 follows.*

## ▶ SESSION 12 (mid-flight snapshot, superseded) — updated 2026-08-06

> **STATE: the gene_alteration stage-1 refinement is DONE AND AWAITING THE USER'S REVIEW. Nothing is migrated.
> Production prompts, the eligibility store and the export are UNTOUCHED. All work is UNCOMMITTED.**
>
> The user asked for the OncoTree treatment to be repeated on **gene_alteration first, then molecular_signature**
> (scoped to gene_alteration only for now). Their plan, in their words: *"refine stage 1 … first look through the
> current mappings, identify mistakes & then apply PRINCIPLE-based corrections to the prompts. Rerun - review -
> iterate … when you get to a point when you believe this is as good as the prompts can be made, then assign the
> rest of the issues to stage 3 … stage 2: I believe nothing changes here right? … during the testing part, make
> sure the current code & data are isolated. Once the testing is done & I have reviewed the 3 way output, then
> proceed with a safe migration. After this we will move to molecular signature."*
>
> ### ▶ THE IMMEDIATE NEXT STEP
> The user was about to review `data/agentic/analysis/gene_alteration_stage1_review/three_way_review.tsv`
> (start at that directory's `README.md`). **The open question put to them, unanswered:** should the 19
> `CHANGED_LATERAL` rows be hand-annotated with a per-value verdict first (as the OncoTree review's `my_reason`
> column did), or is the file readable as-is? Then: stage 3 register → safe migration → molecular_signature.
>
> ### What shipped this session (all verified, all uncommitted)
> - **Stage 2 is DETERMINISTIC-ONLY.** Deleted R2 (per-value LLM repair), R4 (LLM group adjudication) and their
>   four agents — **−306 net lines**. Measured contribution before removal: **ZERO** (R2 fired once, on a value the
>   register overrode anyway; R4 found 0 groups). Re-running the stripped stage over the frozen stage-1 store
>   reproduces the shipped corpus **BYTE-IDENTICALLY**. Detection survives: divergent groups are logged as residue.
>   ⚠ `concept_key` is load-bearing for `qa/invariants.py` C1/C2/C6 — do not delete it.
> - **Four bugs in OUR OWN checks**, each pinned by a test to the live value that exposed it: `fusion_driver_with_gain`
>   misfired on a stated amplification (`ROS1 amplification`) and on a NEGATED gain; `arm_terms_not_compound`
>   misfired on `del17p` + `monosomy 17` (two distinct events); `MAPK3` (ERK1) missing from `known_genes.txt`.
>   Corpus warns **21 → 15**, all 15 remaining genuine. **415 tests green.**
> - **The prompt harness is now column-generic**: `qa/prompt_harness/columns.py` (per-column spec + a
>   POLARITY-AWARE direction classifier), `remap.py` (isolated candidate re-map), `compare.py` (gate + review file),
>   `candidate_gene_prompts.py` (the candidate, as ANCHORED EDITS to production — the diff IS the change, and it
>   raises if an anchor moves). The old `compare_runs.py`/`export_comparison.py` stay for the OncoTree review's
>   1,015 lines of recorded hand judgements.
>
> ### The defect classes found, and the one that mattered
> A full audit of all 913 live mappings (the deterministic catalogue was already nearly clean — 1 error — so the
> signal came from comparing each mapping against its OWN SOURCE):
>
> | id | defect | population | principle |
> |---|---|---:|---|
> | **G1** | actionability-qualified exclusions: of 23 values naming a CLOSED gene list, 12 kept it and **11 dropped it** | 23 (+18 resistance) | **keep what is named or has established membership; omit only what is genuinely unnamed** |
> | G2 | `NOT(KIT and PDGFRA wild-type)` left double-negated | 1 | Wildtype never inside `NOT()` |
> | G3 | `eligible without documented PIK3CA mutation…` → `""` | 1 | administrative framing is packaging; map the criterion inside |
> | G4 | `Wildtype[X] & NOT(SmallVariant[X])` | 1 | Wildtype already implies it; canonicalisation cannot see this |
>
> G1's root cause was a **contradiction inside the prompt**: rule 3 said "omit only when no member is named" while a
> note under the gene-family section said "inside NOT(), naming example genes does NOT make it expressible". Both
> fired on the same population; the mapper arbitrated arbitrarily. The fix **repeals the note**.
>
> ### 🔴 A WRONG TURN — DO NOT REDISCOVER IT
> My first G1 candidate keyed on the **specificity** of what was named (keep an EVENT, omit a bare GENE). **It was
> wrong and would have made 12 values worse.** The user caught it on
> `NOT(documented actionable mutations or genomic alterations in EGFR, ALK, ROS1, HER2, MET, BRAF, RET, or NTRK)`:
> *"I don't think this should disappear actually. This should be a series of exclusions."* A bare gene under an
> actionability judgement is **still a named referent** — dropping it matches the trial to the driver-positive
> patients it explicitly refuses, and no downstream review recovers that. **THE TEST IS WHETHER ANYTHING IS NAMED,
> NOT HOW SPECIFIC THE NAME IS.** Recorded in `candidate_gene_prompts.py`'s docstring. ~1,900 API calls were sunk
> re-running after the correction. The same mistake in miniature was in the harness (`LOST_EXCLUSION` auto-failed);
> it is now review-required, not gated.
>
> Family **b (resistance mechanisms, 18 values)** needs no rule of its own — it falls out of the existing
> established-membership test, and the mapper splits it correctly unprompted: `RB1 … conferring resistance to
> CDK4/6i` KEPT (RB1 loss is the known mechanism), `known MET kinase inhibitor resistance mutation` OMITTED. The
> MET case has a hard proof: those trials REQUIRE a MET alteration, so a whole-gene MET exclusion is unsatisfiable
> and canonicalisation collapses it to empty.
>
> ### Results of the full re-map (913 values, 1,880 API calls, isolated)
> **0 error-severity defects** in what would ship (baseline had 18) · **18 FIXED** · **40 of 909 values changed vs
> live** · **201 `PRIOR_APPROVED`** (B1b's approved work, faithfully reproduced).
> **Hard gate: 1 regression**, which I judge an IMPROVEMENT — `EGFR activating mutation AND NOT(eligible for Stage 2
> Cohort 4 EGFR uncommon mutations…)` narrows to the two common classical variants *because the source excludes the
> uncommon cohort*. The gate cannot see the source.
> **One value genuinely uncertain, for the user:** `BRAF V600E … NOT(known activating AR-V7 or ESR1 alterations …
> in prostate, breast, or gynecologic cancers)` dropped the AR/ESR1 exclusions — G1 says keep (named), the
> tumour-context rule says omit. Two rules collide. Recommend a register entry, not another prompt edit.
> `AGA negative` is `still_needed` in the register (the prompt alone does not reach it).
>
> ### ⚠ Traps that still apply
> 1. **The refine loop is NOT reproducible from cache** — APPLY `remap_raw_output.tsv` to the store at migration;
>    never re-derive it by re-running.
> 2. **Scope every store command with `--columns gene_alteration`.**
> 3. **The harness must never prune the cache** (a candidate fingerprint would GC production's answers).
> 4. Wrap live runs in `caffeinate -i`. Python: `/opt/anaconda3/envs/trial_curator/bin/python`, `PYTHONPATH=.`.
>
> ### Uncommitted files (the user does ALL commits)
> Modified: `core/prompt_registry.py` · `mapping/gene_alteration/{agents,checks,reconcile}.py` ·
> `mapping/gene_alteration/known_genes.txt` · `tests/…/gene_alteration/test_expr_and_checks.py`
> New: `qa/prompt_harness/{candidate_gene_prompts,columns,compare,remap}.py` ·
> (review artifacts since cleared)
>
> ---
> *Session 11's START-HERE (OncoTree, complete) follows.*

## ▶ SESSION 11 (OncoTree — COMPLETE) — updated 2026-08-06

> **✅ ONCOTREE (cancer_type) MAPPING IS COMPLETE — ALL THREE STAGES, restructured and shipped.** The stage that
> made the user distrust the mapping ("*the deterministic parts are particularly brittle… the reconciliation stage
> is actually making me less confident*") has been re-specced from first principles and is now **deterministic
> only**. **412 tests green · 0 defects · 0 divergent groups · gates WARN / 0 FAIL · UNCOMMITTED (the user commits).**
>
> **▶ YOUR NEXT TASK: repeat this whole treatment for `gene_alteration`, then `molecular_signature`** (user,
> 2026-08-06, after a `/clear`). Read this block and to-do **B6** first; then the playbook below.
>
> ### The three-stage architecture (the thing to copy)
> `tasks/eligibility/mapping/cancer_type/` now owns every OncoTree-specific stage, with genuinely shared machinery
> left in `mapping/workflow.py` for the other two columns:
>
> | stage | module | what it does | live volume |
> |---|---|---|---:|
> | 1 · translate | `stage1.py` (moved from `workflow.py`) | LLM mapper + reviewer, faithful to the source's own shape | 4,978 values |
> | 2 · canonicalise | `stage2.py` | **deterministic only** — De Morgan/distribute/absorb, factor NOTs, sort, dedupe, drop vacuous NOT() | changes 424 |
> | 3 · finalise | `stage3.py` | approved rulings from `qa/adjudications/cancer_type.py`, applied LAST | overrides 37 |
>
> Outputs mirror the process, columns accrete (`tables.py`), so a reader sees the whole chain per value:
> `cancer_type_map_initial.tsv` → `_reconciled.tsv` → `_finalised.tsv` (**what ships**). The old
> `cancer_type_map.tsv` + `finalised_cancer_type_map.tsv` are retired to
> `data/backups/retired_legacy_cancer_type_maps_20260806/`; `EligStore.load` still falls back to the old names so
> pre-restructure ARCHIVES load.
>
> ### The decision that mattered most
> Stage 2 was going to include an LLM cross-value consistency pass. It was **dropped**, and that is the key insight
> to carry into gene_alteration: *a function of a SINGLE value cannot be perturbed by other values entering the
> corpus, so churn becomes structurally impossible rather than merely mitigated.* The old reconciler re-decided
> whole groups when membership shifted, which silently re-rolled 7 unrelated values. Measured before dropping it:
> of **20** divergent groups, the deterministic pass dissolves **8** as pure OR-ordering noise, and the surviving
> **12** are held by 17 rulings → **20 → 12 → 0**. Eleven of the twelve are stage 1 answering `Recurrent X` and
> `Refractory X` differently, so the root fix is stage 1's — to-do **B6**.
>
> ### Results
> Reviewed against **two** baselines (28 Jul + 4 Aug), 4,978 values, every changed value judged by hand:
> **226 improvements · 0 stage-1-owned regressions · 0 deterministic defects** (28 Jul had 51, 4 Aug 5, live 1).
> Register **41 → 58**: 21 retired because the refined prompt reaches them unaided, 17 added for B6. Record:
> `data/agentic/analysis/cancer_type_stage1_review/` (start at its `README.md`; the deliverable is
> `three_way_review.tsv`).
>
> ### Four traps found the hard way — do NOT rediscover these
> 1. **The refine loop is NOT reproducible from cache.** A byte-identical prompt does not give identical output: one
>    missed cache entry mid-chain diverges every call after it. **179 of 4,978** values differed on a re-run of an
>    unchanged prompt, and 3 gene values drifted the same way (one badly: `PIK3CA` → the class-II `PIK3C2A|2B|2G`).
>    **So APPLY a reviewed artifact to the store; never re-derive it by re-running and hoping it matches.**
> 2. **`--map-only` / `--reconcile` were whole-store commands.** A cancer-type-only migration re-rolled
>    gene_alteration. Fixed: **`--columns cancer_type,…`** now scopes both. Use it.
> 3. **The drug path shares the cancer_type reconciler.** `map_approvals.py` calls
>    `reconcile_column(column=CANCER_TYPE, three_stage=False)` — pinned to the LEGACY path on purpose, because
>    `approval_cancer_type_map` is a different reviewed table. Do the same for gene_alteration if it shares anything.
> 4. **A renamed table fails OPEN.** A reader left on an old filename reads nothing and ships blanks. Every consumer
>    is now asserted by `tests/agentic/tasks/eligibility/mapping/test_oncotree_three_stages.py` — the PLUMBING test,
>    which also drives interpreted text → all three stages → export end to end. Copy that file for the next column.
>
> ### Two bugs fixed on the way (both pre-existing)
> - **`export.py` joined the cancer_type map on the RAW interpreted cell**, not the provenance-stripped key. Two
>   rows shipped `oncotree_code=""` for values that HAD a mapping — the cell ends `… (SCLC) [T any, N any, M1 a/b/c]`
>   where the bracket is TNM detail, not a provenance tag.
> - **`vocab.name_to_code()` guessed** when an OncoTree NAME maps to several codes (9 names, 24 codes — the
>   germ-cell/teratoma entities). It resolved `Choriocarcinoma` to *brain* by YAML order, turning a flagged
>   `lex_leaked_name` error into a clean WRONG-ORGAN code. Now omitted, so the operand reports as unresolvable.
>
> ### Loose ends
> - `mapping_drift` reports *"no archived version contains cancer_type_map_finalised.tsv"* — correct but inert for
>   one cycle, until the next refresh archives a version under the new name.
> - The 49 values whose reviewer never signed off (`faithful=False`) still ship **unmarked**: the map tables carry no
>   verdict column. All 37 cancer_type ones were checked and are correct — the reviewer was over-strict, not the
>   mapper wrong — but persisting the stage-1 verdict remains the right fix.
> - Backups: `pre_oncotree_stage1_migration_20260805_2257` · `pre_stage23_build_20260806_0135`.
>
> ---
> *Session 10's START-HERE follows.*

## ▶ SESSION 10 (superseded, kept for provenance) — updated 2026-08-05

> **WHERE THINGS STAND (2026-08-05, session 10).** Matching-engine feedback named **26 trials** it could not parse.
> Investigated, then audited **ALL 4,971 cancer_type + 909 gene_alteration mappings**. Result: the engine's own
> complaints were already fixed by B1 (leaked OncoTree NAMES, 8 of the 26), but the audit found **29 genuine
> defects of ours**, now corrected via the `qa/adjudications/` registers. A full **e2e refresh then ran clean
> (exit 0)**. **402 tests green · gates WARN · 0 FAIL. UNCOMMITTED — the user does ALL commits.**
>
> **🔴 READ THIS BEFORE TOUCHING STAGE 2 — A REDESIGN CONVERSATION IS OPEN AND UNRESOLVED (user, 2026-08-05).**
> The user's position, in their words: *"I am not convinced the current reconciliation workflow from R0 to R8 is
> the best arrangement. It seems to me the deterministic parts are particularly brittle and prone to error. The net
> effect is that the reconciliation stage is actually making me less confident on the quality and accuracy of the
> mapping outputs."* Their framing for the redesign is to **first spec out what reconciliation is for**, in two
> categories — (a) error spotting and fixing, (b) syntax manipulations — with the guiding principle that stage 2
> should take **only** what the stage-1 doer/reviewer genuinely cannot or should not do (stage 1 should translate
> the raw text as faithfully as possible and not be burdened with boolean algebra; anything needing a check ACROSS
> several mappings belongs to stage 2). Stage 1 itself is assessed as *"working reasonably well"*. Scope: cancer
> type first, then extend the same treatment to gene_alteration.
> ⚠ **An earlier attempt to answer this by restating the user's categories in different terminology was rejected —
> do not do that. Let the user drive the spec.** Evidence gathered so far that the discussion may want (facts, not
> conclusions): of 4,978 cancer_type values, **FINAL differs from Step-1 in 1,083**, of which the deterministic
> rewrite alone accounts for **1,043** — R0 operand-normalise fires on **1** value, R1 structural-canonical on
> **599**, R2 drop-vacuous-exclusion on **820**. Also relevant: `oncotree_name` is a **derived property** of
> `oncotree_code` (`CancerTypeMap.__post_init__`, made structural 2026-08-03 after 46 mismatched rows), so
> name/code cannot decouple unless a writer bypasses the dataclass.
>
> **The engine is NOT ground truth (user, standing).** It is mid-refactor and has its own bugs; we must never
> degrade our output to satisfy it. Its biggest hole: `TrialReader.parseOncology` is single-code-per-line with NO
> boolean support, so it reads `NSCLC AND NOT(LUSC)` as **positive LUSC** — silently inverting an exclusion into an
> inclusion (221 trials) — and drops all but the last code of any `A OR B`. Not yet in the capability-gaps doc; see
> to-do **D5**.
>
> **What shipped:**
> - **`qa/adjudications/` is now a PACKAGE, one register per vocabulary column** (user, 2026-08-05):
>   `cancer_type.py` (**29** rulings, applied at `reconcile.py` R7) · `gene_alteration.py` (**2**, applied at
>   `gene_alteration/reconcile.py` R5) · `molecular_signature.py` (empty, but it EXISTS so `for_column()` needs no
>   special case). One shared `Adjudication(value, final, rationale, approved)`; resolve with
>   **`adjudications.for_column(column)`**. `""` is a LEGITIMATE ruling, so test `is not None`, never truthiness.
>   R7 now applies rulings for EVERY column, not just oncotree.
> - **Three new checks**, each pinned to a real defect by `tests/agentic/qa/test_audit_20260805_checks.py`:
>   `log_unjustified_sentinel` (cancer_type, error — needs the source, so it lives in the new source-aware
>   `checks.check_against_source()`), `MT` added to `CATCHALL_NODES` (a paediatric brain tumour was mapped to
>   OncoTree's "Malignant Tumor" catch-all and nothing fired), and `acronym_as_gene` (error — `AGA negative` in an
>   NSCLC trial means *actionable genomic alteration*, but AGA is also a real HGNC symbol, so no vocabulary check
>   could catch it).
> - **New `mapping_drift` gate** (`qa/gates.py`) — diffs each finalised map against the newest ARCHIVED version.
>   Severity tracks CERTAINTY, via the shared `expr.broadening()` predicate: **FAIL** on `BROADEN_SENTINEL`
>   (specific codes → a sentinel — never a legitimate unification), **WARN** on `BROADEN_ANCESTOR` and on narrowing
>   (`IDC` → `BREAST` is the correct repair of an over-specification, and unifying members that differ in
>   specificity necessarily lands on a parent, so it cannot be blocked). Adjudicated values are exempt.
>   This closes the class B1's review artifact could not see: it reported defects FIXED and REMAINING but never
>   quality **LOST**, so a value that changed while fixing nothing was invisible across 4,971 rows.
> - **Anti-broadening GUARD at R6** (`mapping/reconcile.py`) — rejects a group adjudication that would broaden a
>   member to a sentinel, keeping its prior value. Same predicate as the gate, so enforcement and checking cannot
>   drift apart. Scoped to the sentinel kind only, for the reason above.
> - **`paths.snapshot_current_version()`** — a COPY-based per-cycle baseline for the eligibility store, taken as
>   refresh stage 0. Copy, not move (`archive_current_version`), because the store ACCUMULATES: moving it would
>   leave `EligStore.load()` with no `current_version/` and it would silently fall through to an old snapshot dir.
>   This is what makes `mapping_drift` a genuine run-to-run diff instead of a comparison against an ever-staler
>   state, and it gives the LLM-curated masters a rollback point per cycle. Deliberately NOT pruned.
> - **Two bugs fixed in `mapping/consistency.py:canonical_key`** — it stripped numeric GRADE (so `WHO grade 2/3/4
>   glioma` collapsed to one key and the adjudicator was asked to give ASTR2/ASTR3/glioblastoma one code — it did
>   once), and its stage pattern `[0-9ivabc/,\-\s]*` ate the next word's leading letter (`stage III breast cancer`
>   → `reast cancer`, **775 of 4,978 values mangled**, silently preventing the very comparison it exists for).
>   Divergent groups went **4 → 0**, all four having been grade false positives.
> - **Two bugs fixed in `gene_alteration/reconcile.py:concept_key`** — unbounded `str.replace` per droppable, which
>   mangled 23 values including two MEANING INVERSIONS (`ineligible`→`in`, `unknown`→`un`); now word-bounded,
>   longest-alternative-first. Both keys now iterate to a **fixed point** (a pass can expose a pattern the previous
>   pass could not see, e.g. punctuation removal turning `stage <=2` into `stage 2`), so both are idempotent.
> - Export re-built and then refreshed: see the refresh line below. The adjudication pass alone moved **71 rows /
>   24 trials, 0 rows unexplained by a ruling**, with row count unchanged.
>
> **E2E REFRESH (2026-08-05 02:08, 21.4 min, exit 0, gates WARN).** **8 trials expired · 6 new curated** · universe
> 2,041 → **2,039** · export **17,778 rows · 2,021 trials · 33 cols** · `status: ok`. The drift triage came back
> clean: 1,433 existing rows changed and every one was trial METADATA from the fresh download (dates, statuses,
> sites, titles) — **no curated column moved**. Report: `run_report/refresh_20260805_020805.md`.
>
> **⚠ THE LESSON THAT COST THE MOST — `--reconcile` CHURNS.** Applying the rulings changed the grouping, which
> re-rolled 7 unrelated values; two of them **reverted genuine B1 improvements** (graded glioma codes
> `ASTR2 OR ASTR3 OR ODG2 OR ODG3` collapsed back to `ASTR OR ODG`). The new gate caught it. All 7 were reverted to
> their pre-run reviewed state so the change set is EXACTLY the 29 approved rulings, and they are listed in **B4**
> for a decision. **Never assume a store-wide re-run is inert: snapshot, then diff and require every changed row
> to be explained.**
>
> **▶ YOUR NEXT TASK: the STAGE-2 REDESIGN CONVERSATION above — it is open and the user is driving it.** Do not
> start implementing, and do not restate their spec in other words. After that: the A-group (A2 remaining
> `pipeline/` + `outputs/` regroup → A3 legacy removal → A4/A4b/A5), then B4/B5, then the C/D/F backlog.
> **F1 (curated masters into git) remains the highest-value quick win in this doc.**
>
> Backups: `data/backups/pre_adjudications_20260805_0132/` (store + export + joined) and
> `data/backups/pre_refresh_20260805_0206/` (masters + export + MD5SUMS). Per-cycle store baseline now also at
> `masters/eligibility/archive/05082026/`.
>
> ---
> *Historical context from earlier sessions follows.*

## ▶ SESSION 9 (superseded, kept for provenance) — updated 2026-08-04

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

