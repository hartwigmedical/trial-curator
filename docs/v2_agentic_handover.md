# v2 Agentic Pipeline — Handover

## ▶ NEXT SESSION — START HERE (updated 2026-07-28)

**CURRENT STATE: ELIGIBILITY CURATION IS COMPLETE + user-reviewed.** Extraction (Stage I) → mapping Step 1
(per-value) → mapping Step 2 (cross-value reconciliation) are all DONE, validated, and signed off. The drug utility
path is signed off. **All code from this session is UNCOMMITTED in the working tree — the user does all git commits.**
`make agentic-tests` = **145 green**.

**⏭ RESUME AT: the INTEGRATION backlog (NOT curation — that's done). In dependency order:**
1. **Drug Phase 2 — main vs auxiliary role. ✅ DONE (2026-07-28, UNCOMMITTED).** Built as a NEW 6th 3NF table
   `trial_arm_drug_role` (`trial_arm_id, canonical_id, role`) — NOT a column on `trial_to_intervention` (grain:
   ~11% of input strings bundle mixed main+aux drugs, so string-grain is lossy; canonical grain joins cleanly to
   `drug_regulatory_approvals` for per-main TGA/PBS). Additive — the existing 5 drug tables are byte-unchanged. A
   cheap **per-arm** doer→reviewer classifier (`classify_arm_roles`, NO web_search) on the shared `review_refine`
   harness; folds in the recovered `DRUG_CURATOR_INSTRUCTIONS` rules. `make agentic-arm-consistency` now checks the
   new table's FK (`role_dangling`). `make agentic-tests` = **154 green** (145 + 9 new). Touched (drug path only):
   `schema.py` (dataclass + `ROLE_*` consts + `ArmRoleClassification`), `agents.py` (classifier+reviewer),
   `workflow.py` (`classify_arm_roles`+`ArmContext`), `store.py` (6th-table I/O + `remove_trial_roles`), `build.py`
   (arm-context capture + role pass; `--from-trials` drops stale roles), `qa/arm_consistency.py`, tests, docs+diagram.
   **NB: the role table itself is not yet POPULATED over the universe** — run `make drug-ref-build ALL_TRIALS=1`
   (or the smoke set) to build it; RESOURCE_INFO row count is TBD until then.
2. **THE GRAND JOIN → a fresh `combined.tsv`** (the matching-engine flat file): interpreted ⋈ `trial_arms` ⋈ the
   **FINAL vocab maps** (`finalised_*_map.tsv`, use the `*_FINAL` column) ⋈ drug annotations, on `trial_arm_id`; add
   TGA/PBS + the main/aux role. `run._build_combined` exists (PARKED, 16 cols) — extend it. **Open decision:** join
   engine — in-process SQLite/DuckDB vs keep the Python join (Postgres ruled out; memory `v2-joined-tables-sql-decision`).
   The old stale `combined.tsv` was DELETED — build it anew here.
3. **Symmetric-match vocab for drug approvals** — map each `drug_regulatory_approvals` free-text cancer_type/biomarker
   into the SAME OncoTree + finding-model vocab (reuse the mappers), so trial-eligibility ↔ drug-approval match symmetrically.
4. **Self-contained pipeline — Stage-I ingestion + legacy retirement.** Move download → drug-filter → POTTR-append →
   retire-missing into agentic; retire legacy `eligibility_path`/`drug_utility_path`. Biggest piece for a periodic run.
- **Optional cleanup:** the **11 cancer_type per-value LOGIC residuals** (valid codes, imperfect structure, e.g.
  subtype-ANDed-with-parent) in the signed-off oncotree mapper's hard-cell tail — a small manual/targeted pass.

**Specs:** mapping = `docs/v2_mapping_and_shared_loop_plan.md` (§8 gene · §9 signature · §10 Step-2 reconciliation).
Overall = `docs/v2_agentic_pipeline_spec.md`; drug = `docs/agentic/drug_ref_schema.md`; run guide =
`docs/agentic/combined_agentic_run.md`. Decisions in memory: `v2-mapping-stage-decisions`, `v2-shared-loop-harness`,
`v2-drug-regime-axis`, `v2-eligibility-orchestration-model`, `v2-joined-tables-sql-decision`.

---
### What this session did (2026-07-27 → 28) — detail

**DONE overnight (2026-07-27, autonomous — all UNCOMMITTED in the working tree; nothing under `/data` touched):**
1. **Full finding-model grammar validator** — `tools/finding_model.py` `finding_model_problems()` rewritten from
   bracket-counting into a field/enum/scope/HGVS-aware DSL validator (`CLASS_SPEC` table), the hard SYNTAX gate so
   the reviewer judges only semantics. +2 tests; **143 unit tests green**.
2. **`gene_alteration` mapper — VALIDATED (99.5%).** Doer+reviewer upgraded (4 user decisions: full validator ·
   `Wildtype` class · convert definitional disease-`NOT()` · expand gene families) + folded rules. Live-iterated:
   set A 50 trials → 5 principle-level fixes → **330/330**; disjoint set B 100 trials → **239/242**; combined
   **569/572 faithful**, 0 syntax/lost-detail/family errors. Fixes: FLT3-ITD/TKD de-over-specified · exclusion with
   inexpressible qualifier OMITTED not broadened · rearrangement→Fusion · unspecified loss→HOM_DEL · segmental
   loss→ARM level. Spec §8.
3. **`molecular_signature` mapper — VALIDATED (100%).** Doer+reviewer upgraded (6-term vocab + resource synonyms +
   strong legit-empty/anti-hallucination). First draft clean: set A **107/107** + disjoint set B **66/66** =
   **173/173 hand-verified**, no fixes needed. Spec §9.

**✅ TWO DECISIONS — RESOLVED by the user (2026-07-28) + applied (gene iter4, re-validated):**
- **(a) Bare "X mutation" → `SmallVariant[gene=X]` ONLY** (NOT expanded to amp/del/fusion); full expansion reserved
  for genuinely UNSPECIFIED terms ("X alteration"/"aberration"/"X-altered"). Baked in doer+reviewer+GRAMMAR_REFERENCE.
- **(b) "HRR gene(s)" → the PROfound/FDA 15-gene panel** OR'd (BRCA1, BRCA2, ATM, BARD1, BRIP1, CDK12, CHEK1, CHEK2,
  FANCL, PALB2, PPP2R2A, RAD51B, RAD51C, RAD51D, RAD54L), per-gene depth by the decision-(a) word logic. "HRD"/"HRR
  deficiency" stays the `HR_DEFICIENT` signature. Baked in doer+reviewer.
- **iter4 result:** set A 330/330, set B 239/242; HER2-non-synonymous + both HRR residuals cleared; no regression.
  Result TSVs `scratchpad/gene/setA_iter4.tsv` / `setB_iter4.tsv`.

**✅ FULL-STORE STEP-1 BUILD DONE (2026-07-28).** `run.py --map-only` rebuilt for a full build: `map_all_columns`
maps all 3 columns' DISTINCT values in ONE concurrent pool; writes the 3 map tables + the new per-row
`mapped_eligibility.tsv` (interpreted ⋈ vocab maps, 1:1 rows) into `current_output/`; **NEVER re-persists
raw/interpreted** (new `save_maps` / `save_mapped_eligibility`; byte-identity md5-verified). Step-2 output will be
`finalised_eligibility.tsv` (same shape). Ran at **500 workers / 500 max-concurrency** (probed: 15k RPM / 40M TPM,
TPM-bound; 120 was latency-bound at ~20% TPM, 500 hit ~92% TPM, 0 rate-limit errors, ~4.6× faster). Result over
5,879 distinct values:
- **cancer_type** 4,853 distinct · 4,834 mapped · 19 empty · 79 unfaithful; **gene** 859 · 808 · 51 empty · 11
  unfaithful; **signature** 167 · 46 · 121 empty (72%, expected) · 0 unfaithful.
- **Independent review:** gene + signature CLEAN (0 invalid syntax/non-vocab). cancer_type ~99.5% clean but **~24
  hard residuals** (13 invalid-code + 11 logic) in the most complex heme/CNS/multi-subtype cells — the mapper leaks
  NAMES into the code field on long multi-NOT() cells (e.g. `NOT(APL with PML-RARA)` should be code `APLPMLRARA`).
  These sit in the SIGNED-OFF oncotree mapper → NOT reopened; hand to Step-2 + a targeted cleanup.
- **Consistency (Step-2 targets):** 25 cancer_type + 1 gene groups (same concept → different code). Review script:
  `scratchpad/review_full.py`.

**✅ MAPPING STEP 2 — cross-value reconciliation DONE (2026-07-28). ELIGIBILITY CURATION IS COMPLETE (Step 1 + 2).**
`run.py --reconcile` (`mapping/reconcile.py`): detect (`find_inconsistencies`) → deterministic pre-pass (name→code
repair for leaked names + OR-branch order-normalise) → LLM adjudicator (doer→reviewer, approved prompts in
`agents.py`) on the remaining SEMANTIC groups → FINAL value per input. Engine + prompts + output shape all
user-approved. Result (3NF store byte-identical / untouched):
- **cancer_type:** 25→**4** inconsistent groups (21 unified; the **4 remaining are genuine GRADE distinctions the
  adjudicator correctly KEPT apart** — ASTR3 vs ASTR4, LGGNOS vs HGGNOS — the blunt key over-groups them, keeping
  distinct is correct); **all 13 invalid-code residuals fixed** (name→code repair, 0 unresolved); 181 values changed.
- **gene:** 1→0; **signature:** 0. Remaining: **11 per-value LOGIC residuals** (valid codes, imperfect structure e.g.
  subtype-ANDed-with-parent) in the SIGNED-OFF oncotree mapper's hard-cell tail — out of Step-2 scope; manual-review
  candidates, NOT a defect.
- **3NF DISCIPLINE (user requirement):** `eligibility/current_output/` = **8 pure-3NF tables** — 2 content + the 3
  Step-1 maps + the **3 `finalised_*_map.tsv`** (Step-1 cols + a `*_FINAL` col; these ARE 3NF single-key lookups, so
  they live in the store, NOT joined/). `drug_annotations/current_version/` = 5 pure-3NF. Only the DENORMALIZED flat
  views live in top-level **`data/agentic/joined/`**: `mapped_eligibility.tsv` (Step-1) + `finalised_mapped_eligibility.tsv`
  (Step-2, +`*_FINAL`). The stale `combined.tsv` was DELETED (rebuild fresh in the grand-join step). `make
  agentic-tests` = **145 green**. Step-2 code UNCOMMITTED (user commits): `mapping/reconcile.py`, `agents.py`,
  `run.py --reconcile`, `core/paths.py` (JOINED_ROOT), `schema.py`/`store.py`, `tools/oncotree.py` (name_to_code), 2 tests.

**⏭ IMMEDIATE NEXT (not eligibility curation — the integration backlog):** (1) **drug Phase 2** — main/aux role on
`trial_to_intervention`; (2) **the grand join** → `combined.tsv` (eligibility ⋈ drug ⋈ trial_arms on `trial_arm_id`,
using the FINAL vocab + TGA/PBS + role) = the matching-engine flat file; (3) **Stage-I ingestion** into agentic +
**legacy retirement**; (4) optional: targeted cleanup of the 11 cancer logic residuals. Plus the drug-indication
symmetric-match vocab mapping (parked).

**HOW TO REPRODUCE / RE-RUN:** live-test harnesses + frozen 50/100 (gene) & 50/82 (signature) trial+value lists +
result TSVs are in the session scratchpad (`scratchpad/gene/`, `scratchpad/sig/`; non-persistent — recreate from the
method in `v2-mapping-stage-decisions`). Each maps distinct store cells via `map_gene_alterations` /
`map_molecular_signatures` with a SCRATCH `DiskCache` (never `data/agentic/cache`). After sign-off: run the real
`--map-only` over the full frozen store to build the 3 map tables, then **Step 2** cross-value reconciliation.

---
## (prior START-HERE, superseded by the block above)
**Was: RESUME AT `gene_alteration` mapper.** `cancer_type → OncoTree` SIGNED OFF; gene + signature were next — now done (above).

**DONE THIS SESSION (2026-07-27):** M1 is **COMMITTED** (`21dbb73` "refactor loop mechanism to be shared across all
stages"). Everything after it — the M2 mapping work (`run.py --map-only`, `mapping/*`, `tools/oncotree.py` YAML,
`qa/mapping_consistency.py`) + the tests + these doc updates — is **UNCOMMITTED in the working tree** (user commits).
- **M1 — shared loop harness ✅ (committed `21dbb73`).** `core/review.py` `review_refine` (verdict-agnostic — the response-cache key hashes
  the reviewer schema, so schemas stay stage-local; only the LOOP is unified). Extraction + drug + shared ANZCTR
  arm-ID all refactored onto it; behavior-preserving (extraction byte-identical on a 5-trial sample incl. the
  escalation path; full live end-to-end smoke test rc=0, zero existing files touched). Only mapping-was-bespoke; now
  rebuilt on it in M2.
- **M2 scaffolding ✅.** `ReviewVerdict.suggested_fix`; 3 mappers on `review_refine` (REVISION + ESCALATION);
  configurable fan-out concurrency; **`run.py --map-only`** (maps the store's interpreted cells → the 3 map tables,
  skips extraction/drug/combined, content tables untouched).
- **OncoTree grounding → YAML ✅.** `tools/oncotree.py` now reads `oncotree.yaml` (single source): `vocab_reference()`
  emits an **indented `Name (CODE)` tree** (hierarchy → helps granularity), ancestors from the nesting. Identical
  897-code set + 0 ancestor-mismatches vs the old CSV. (Legacy `eligibility_path` still uses `oncotree.csv`.)
- **`cancer_type` mapper — SIGNED OFF ✅.** Doer + vocab-grounded reviewer baked into `agents.py`. Live-tested on 50
  trials, self-reviewed, **4 principle-level fixes** applied, then **validated on 100 DISJOINT trials (288 values,
  99.7% faithful)** to guard against overfitting — all fixes generalised. Fixes: (1) BREAST convention (generic
  breast → BREAST; subtype only when ductal→IDC/lobular→ILC); (2) parenthesise OR-groups before AND (rule **+ a
  deterministic `_oncotree_logic_problems` checker**); (3) inexpressible anatomic scope → sentinel ALONE (drop
  scope-relative NOT()); (4) broad-category exclusion → broad node(s) not a NOS subtype (`NOT(sarcomas)` →
  `NOT(SOFT_TISSUE) AND NOT(BONE)`). Detail: memory `v2-mapping-stage-decisions`.
- **`qa/mapping_consistency.py` (NEW) ✅.** Deterministic cross-value consistency checker — the user's requirement
  that the FINAL resource must NOT map semantically-equivalent inputs to different codes. Catches qualifier-noise
  divergence; synonym-histology is handled by the prompt; the **full guarantee is Step-2 reconciliation (now a firm
  requirement, not optional)**. **141 tests pass.**

**STANDING METHOD for the remaining mappers (anti-overfit):** live-test → self-review → **principle-level fixes only**
(a rule + ≤1 example; never a bespoke patch per rare case) → **validate on a DISJOINT larger sample** before locking.
Prompts are finalised WITH the user and only baked into `agents.py` after sign-off.

**gene_alteration — prep already done** (before we paused it for the cancer_type test): frequency analysis (KRAS
G12x dominates the head; **26% of cells contain `NOT()`**), GeneAlteration resource mined across finding-model
classes. Proposed doer additions awaiting the user's input: notation normalisation (`G12C` == `p.G12C`); drop
functional/origin qualifiers (`activating`/`actionable`/`deleterious`/`germline or somatic`/`confirmed`);
disease-phrased `NOT()` → the underlying alteration (`NOT(BCR-ABL-positive leukemia)` → `NOT(Fusion[BCR::ABL1])`);
anti-lazy-`""`. Then reviewer, then `molecular_signature`.

**HOW TO WORK A MAPPER (operational — reproduce this for gene_alteration + molecular_signature):**
- Input = the FROZEN store `data/agentic/eligibility/current_output/interpreted_eligibility.tsv` — DO NOT rebuild it.
- Prompts live in `tasks/eligibility/mapping/agents.py`: gene = `_GENE_RULES` (doer) + `GENE_REVIEWER_INSTRUCTIONS`
  + the SHARED `GRAMMAR_REFERENCE` (both agents; edit shared grammar there). signature = `_SIGNATURE_RULES` +
  `SIGNATURE_REVIEWER_INSTRUCTIONS`. Finalise WITH the user, bake in ONLY after sign-off.
- Curated resources to mine for few-shot / hold-out (NOT gold — correct errors, memory `feedback-curated-files-not-infallible`):
  `data/eligibility_path/resources/gene_alteration/GeneAlterationCurationResource_*.xlsx` (Mapping_args = answer),
  `.../molecular_signature/MolecularSignatureCurationResource_*.xlsx`.
- LIVE-TEST (fully live, touches no store): a scratchpad script that reads the store, collects a trial-sample's
  distinct cells, calls `map_gene_alterations(client, cells, use_reviewer=True, workers=N, max_attempts=6)` with
  `client = LlmClient(cache=DiskCache(<fresh scratch dir>), max_concurrency=~80)`; wrap in `caffeinate -i`; dump
  `input → finding_model, faithful, problems` to a TSV. SELF-REVIEW first, then bring fixes to the user.
- Then DISJOINT-VALIDATE on a larger NON-overlapping trial sample to confirm the fixes generalise (not overfit).
- Test scripts + result TSVs live in the session scratchpad (non-persistent — recreate them). The cancer_type harness
  pattern: `scratchpad/test_oncotree_map.py` (gone after this session; the pattern is above).
- Whole-store build (after both mappers are locked): `make agentic-run … --map-only` (writes the 3 map tables into
  `current_output/`; content tables untouched). `make agentic-tests` = **141 tests**, no API.

**THE TO-DO after mapping** (the user's milestones + coupled work, dependency-ordered; every stage GATES on the
user's review + sign-off):
0. **Finish MAPPING** — gene_alteration + molecular_signature (as above), then run `--map-only` over the full store
   to build the 3 map tables, then **Step 2** cross-value reconciliation (enforces the no-inconsistency requirement).
1. **DRUG PHASE 2 — main vs auxiliary role.** Cheap per-arm classifier → a `role` (main|auxiliary) column on
   `trial_to_intervention`; main = investigational agent(s), aux = backbone/SoC/comparator. Judgement rules in git
   history (deleted `DRUG_CURATOR_INSTRUCTIONS`, ≤ 52417bc). Prereq for `combined.tsv`'s main/aux + per-main TGA/PBS.
2. **THE JOIN → `combined.tsv` (the matching engine's flat file).** `run._build_combined` already joins interpreted ⋈
   `trial_arms` ⋈ vocab-maps ⋈ drug annotations on `trial_arm_id` (PARKED / 16 cols). Needs mapping + drug role.
   Finalize the **flat-file contract**: add **TGA/PBS** + **main/aux role**. **Open decision:** join engine —
   in-process **SQLite/DuckDB** vs keep the Python join (Postgres ruled out; memory `v2-joined-tables-sql-decision`).
3. **SYMMETRIC-MATCH vocab for drug approvals.** Map each drug indication's free-text `cancer_type`/`biomarker` (in
   `drug_regulatory_approvals`) into the SAME OncoTree + finding-model vocab, so the engine matches trial-eligibility
   ↔ drug-approval symmetrically. Parked; comes with the drug↔trial link.
4. **SELF-CONTAINED PIPELINE — Stage-I ingestion + legacy retirement.** The pipeline still reads legacy-produced
   inputs (`trial_universe/`); moving download → drug-filter → POTTR-append → retire-missing into agentic (+ retiring
   legacy `eligibility_path`/`drug_utility_path`) is the biggest piece for a periodic run.

**Known quality item (deferred — user is happy with extraction for now):** 219 trials finished `faithful=False`
(best-effort hard multi-cohort — the extractor exhausts the 6-attempt refine budget; output still written). We are
NOT touching extraction during the mapping work. Full detail below + memory `v2-next-priorities`.

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
- **Git:** the user makes all commits. Commit trail: `b9e0202` (trial_arm_id restructure) → `2b1dd97` (extraction/DNF
  sign-off) → **`21dbb73` (2026-07-27: M1 shared loop harness — `core/review.py` + refactor of extraction/drug/ANZCTR
  arm-ID onto it)**. The **M2 mapping work is UNCOMMITTED** in the working tree (`run.py --map-only`, `mapping/*`,
  `tools/oncotree.py` YAML migration, `qa/mapping_consistency.py`, tests, docs). Data (stores + `archive/`) is gitignored.

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
The v2 rewrite is a **two-domain agentic pipeline**: the ELIGIBILITY path (extract → map Step 1 → map Step 2) and
the DRUG UTILITY path (`make drug-ref-build`: a separate incremental drug-annotation build). Both emit **3NF
relational tables** that join on `trial_arm_id` (via the shared `trial_arms` registry); the grand flat `combined.tsv`
is built in the (still-to-do) join step. Pattern B throughout: deterministic Python owns control flow (parallelism,
refine loop, per-item durable saves); the LLM fills the doer/reviewer slots. **145 unit tests pass** (fake-client, no
API). Eligibility output is a **DNF** table — one row = one satisfiable (trial, arm) conjunction; rows ORed, cells
ANDed, exclusions inline `NOT(...)`. Mapping is TWO steps: **Step 1** (`--map-only`) maps each distinct value to
vocab; **Step 2** (`--reconcile`) reconciles equivalent values to one code. Eligibility curation is COMPLETE.

## Quickstart
Conda env `trial_curator` (auto-selected); `OPENAI_API_KEY` auto-loaded from `.env`. Needs `openai>=2.x`.
**Wrap live runs in `caffeinate -i …`** so laptop sleep doesn't drop the connection (memory `feedback-caffeinate-long-jobs`).
```bash
make agentic-run ID=NCT06881784                 # one trial, full per-trial pipeline (source auto-detected)
make agentic-run IDS=NCT1,ACTRN2,NCT3           # a specific set
make agentic-run EXTRACT_ONLY=1 RESUME=1 WORKERS=80 MAX_CONCURRENCY=500   # the full-universe extract (2026-07-25 settings)
# MAPPING (over the frozen extract store; via python -m aus_trial_universe.agentic.run):
python -m ...run --map-only  --workers 500 --max-concurrency 500 --no-cache-prune   # STEP 1: per-value maps + joined/mapped_eligibility.tsv (500 = ~92% TPM; 2026-07-28)
python -m ...run --reconcile --workers 30  --max-concurrency 60                     # STEP 2: reconcile -> finalised_*_map.tsv (store) + joined/finalised_mapped_eligibility.tsv
make agentic-arm-consistency                    # referential-integrity check on the trial_arm_id join key
make agentic-clean                              # wipe the transient data/agentic/{log,cache} only
make agentic-tests                              # 145 unit tests, no API
# run.py flags: --workers N · --max-concurrency N (global API cap; TPM-bound — probe x-ratelimit headers) ·
#   --map-only (Step 1: map distinct interpreted cells -> 3 map tables + joined/mapped_eligibility.tsv) ·
#   --reconcile (Step 2: reconcile maps -> finalised_*_map.tsv in the store + joined/finalised_mapped_eligibility.tsv) ·
#   --skip-drug · --no-cache · --no-cache-prune · --extract-only · --no-judge · --no-review · --store-root DIR · --max-attempts N
```
Eligibility store `data/agentic/eligibility/current_output/` = **8 pure-3NF tables** (2 content + 3 Step-1 maps +
3 finalised maps); each per-trial run loads it, upserts, writes back in place (supersede by moving `current_output/`
→ `archive/<date>/`). Denormalized flat views live in **`data/agentic/joined/`** (`mapped_eligibility.tsv`,
`finalised_mapped_eligibility.tsv`; the grand `combined.tsv` will be rebuilt in the join step). Log →
`data/agentic/log/…`. Drug reference is a SEPARATE build: `make drug-ref-build …` →
`data/agentic/drug_annotations/current_version/`. (NB: `make agentic-validate` targets `combined.tsv` — not present
until the join step is rebuilt.)
Full detail: `docs/agentic/combined_agentic_run.md`; drug path: `docs/agentic/drug_ref_schema.md`;
eligibility design: memory `v2-eligibility-orchestration-model`.

## What's built
```
aus_trial_universe/agentic/
  run.py                     # ORCHESTRATOR. Per-trial: extract -> map (lookup-first) -> checkpoint to current_output/; then drug top-up + combined join. STORE-WIDE mapping modes (work off the frozen extract): `--map-only` = _run_map_only() (Step 1: map_all_columns pools all 3 columns' distinct values in ONE concurrent pool -> 3 map tables in current_output/ + joined/mapped_eligibility.tsv; raw/interpreted NEVER re-persisted); `--reconcile` = _run_reconcile() (Step 2: reconcile maps -> finalised_*_map.tsv in current_output/ + joined/finalised_mapped_eligibility.tsv). DiskCache; a failing trial is logged & skipped
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
    mapping/                 # STAGE II: map the DNF cells -> vocab (doer -> reviewer, on core.review.review_refine). ALL 3 mappers SIGNED OFF (2026-07-28)
      agents.py, schema.py   # oncotree / gene / signature mappers + reviewers (all validated) + the Step-2 reconcile adjudicators (_ONCOTREE_RECONCILE_RULES + reviewer + finding-model analogs); schema has OncotreeMapping/FindingModelMapping/ReviewVerdict(+suggested_fix)/GroupReconciliation
      workflow.py            # map_cancer_types/gene_alterations/molecular_signatures + map_all_columns (ONE pool across all 3); _oncotree_logic_problems (parens/precedence via _top_level_has)
      reconcile.py           # STEP 2 (NEW 2026-07-28): detect(find_inconsistencies) -> deterministic pre-pass (repair_oncotree_code name->code + normalize_or_order) -> adjudicate_group (LLM) -> write_finalised_maps (3NF, to store) + write_finalised_mapped_eligibility (flat, to joined/)
    schema.py, store.py      # ArmEligibilityRaw + InterpretedEligibility + 5 map dataclasses + MAPPED_ELIGIBILITY_COLUMNS; EligStore.save_maps() / save_mapped_eligibility() (write ONLY new files, never re-persist content tables)
    tools/
      oncotree.py            # oncotree.YAML vocab + valid_codes + ancestors + vocab_reference() (indented Name (CODE) tree) + name_to_code() (reverse, for Step-2 repair); 3 sentinels
      finding_model.py       # FULL grammar validator (2026-07-28): finding_model_problems() = field/enum/scope/HGVS-aware DSL parser (CLASS_SPEC) — the hard SYNTAX gate; + GRAMMAR_REFERENCE
    qa/                      # validate_output.py (make agentic-validate) + arm_consistency.py (make agentic-arm-consistency) + mapping_consistency.py (cross-value: canonical_key/find_inconsistencies — used by Step 2)
tests/agentic/               # 145 tests (fake-client); mirrors tasks/ (core [+test_review], tasks/shared, tasks/eligibility [+ test_mapping_findingmodel (grammar validator), test_run_map_only, test_reconcile, qa/test_mapping_consistency], tasks/drug_utility)
scripts/agentic/pipeline.sh  # driver: python-pick, .env, tests-preflight, log tee; subcommands run|validate|clean|tests|cache-prune|arm-consistency|drug-migrate-trial-arms|drug-ref-build|drug-ref-refresh-pottr
docs/agentic/combined_agentic_run.md   # run/setup guide
```

## Output schema (v2 — shared arm registry + 3NF stores + a top-level `joined/` for flat views; updated 2026-07-28)
Arm identity lives once in the **shared `trial_arms` registry** `data/agentic/trial_arms/current_version/trial_arms.tsv`
(`trial_arm_id → trialId, registry, arm, arm_type`; `trial_arm_id` = deterministic `{trialId}::{arm}` slug). The
eligibility store `data/agentic/eligibility/current_output/` holds **8 pure-3NF tables**; ALL denormalized/joined
views live in the top-level **`data/agentic/joined/`** (keeps the stores strictly 3NF). Eligibility tables hold **NO
drug info**; drugs join via `trial_arm_id` to the drug utility path.

**3NF store — `eligibility/current_output/`:**
- `arm_eligibility_raw.tsv` — `trial_arm_id` → 5 VERBATIM raw cells (inline `[source]`, `|`-delimited). Audit anchor.
- `interpreted_eligibility.tsv` — `(trial_arm_id, conj_id)` → 5 interpreted DNF cells (inline `NOT()`; rows sharing
  `trial_arm_id` are ORed). **The FROZEN source for mapping — never re-derived by Step 1/2.**
- `cancer_type_map.tsv` — `cancer_type` → `oncotree_name, oncotree_code` (Step-1, per-value).
- `gene_alteration_map.tsv` / `molecular_signature_map.tsv` — value → `finding_model` (Step-1, per-value).
- `finalised_cancer_type_map.tsv` — `cancer_type` → `oncotree_name, oncotree_code, oncotree_code_FINAL` (Step-2
  reconciled in the added `*_FINAL` col; Step-1 cols preserved). **Still 3NF (single-key lookup) → lives here.**
- `finalised_gene_alteration_map.tsv` / `finalised_molecular_signature_map.tsv` — value → `finding_model, finding_model_FINAL`.

**Denormalized flat views — `data/agentic/joined/`:**
- `mapped_eligibility.tsv` — Step-1 flat: interpreted ⋈ (Step-1 maps), 1:1 with interpreted; cols = the 2 keys + the
  5 interpreted cells + `oncotree_name/code`, `gene_alteration_findingmodel`, `molecular_signature_findingmodel`.
- `finalised_mapped_eligibility.tsv` — Step-2 flat: the above + appended `oncotree_code_FINAL`,
  `gene_alteration_findingmodel_FINAL`, `molecular_signature_findingmodel_FINAL`. **The eligibility-side review artifact.**
- `combined.tsv` — the grand join (interpreted ⋈ trial_arms ⋈ FINAL maps ⋈ drug annotations on `trial_arm_id`) +
  TGA/PBS + main/aux role. **NOT built yet** — the stale one was deleted; rebuild it in the join milestone.
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
  views (`mapped_eligibility`, `finalised_mapped_eligibility`, `combined`) live in top-level `data/agentic/joined/`.
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
