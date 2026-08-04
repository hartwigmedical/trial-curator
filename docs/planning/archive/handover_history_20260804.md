# Handover history — extracted 2026-08-04

The narrative record pulled out of `docs/v2_agentic_handover.md` when it reached ~1,500 lines. **Nothing here is
a to-do.** It is provenance: how each design got to where it is, what was tried and rejected, and the incidents
whose lessons are now encoded as rules elsewhere.

Kept because it has repeatedly proved load-bearing — e.g. the `NCT06999980` whole-trial re-roll (why a re-run
must never silently rewrite human-approved values) and the `arm_scope` rationale (why an empty arm is not
automatically an extraction miss).

**What did NOT move, and why:** "Locked decisions (don't re-litigate)" and "Gotchas" stay in the handover even
though they describe finished work — their purpose is to stop a future session redoing settled questions or
re-hitting known traps. The filter for this split was *"does it change what the next session does?"*, not
*"is the task finished?"*.

Covers sessions up to and including **session 8 (2026-08-04)**, newest first. Later extractions get their own
datestamped file (`handover_history_<YYYYMMDD>.md`) rather than being appended here.

---

### Session 8 (2026-08-04) — THE ONCOTREE CORRECTION SHIPPED TO PRODUCTION. All UNCOMMITTED.

Reviewed and approved by the user, then migrated. Full record + rollback: `docs/planning/archive/v2_oncotree_correction_spec.md`
§Phase 2. Outcome: store == the approved artifact on all 4,971 rows · export 17,830 rows / 572 distinct codes /
**0 error defects, 0 warnings, 0 name-code mismatches** · gates **WARN (0 FAIL)** · **261 tests green**.

**End state (as agreed):** `aus_trial_universe/oncotree_review/` and `data/agentic/analysis/oncotree_review/` are
GONE. Their content lives in `mapping/{schema,agents,reconcile,workflow}.py`, `tools/oncotree.py` +
`tools/oncotree_{expr,checks}.py`, and `qa/adjudications.py` (beside `waivers.py`). Step-1 holds the mapper's
FAITHFUL translation and FINAL the approved refined value — **1,060 rows differ, which is the audit trail**.

**Durable additions:** an `oncotree_expressions` GATE on the export (any error-severity defect fails the run) ·
the name/code invariant is now STRUCTURAL (`CancerTypeMap.__post_init__` derives the name, so a disagreeing row
cannot be constructed) · `tests/agentic/core/test_prompt_registry.py`.

**Six wiring defects that unit tests could not catch** — all found by testing the LIVE connection:
1. the refine loop still read the retired `prior.oncotree_name` (crash on any 2nd attempt);
2. `_ONCOTREE_REPAIR_RULES` was never spliced (`NameError` on first R4 use);
3. **a class docstring is hashed into the response-cache key** — pydantic folds it into the JSON schema, so an
   "improved" docstring silently orphans every cached decision. `mapping/schema.py` now warns about this;
4. the new gate was scoped to a hardcoded store path, so it graded the live store while every other gate graded
   the test fixture — a gate that lies. Re-scoped to the export;
5. `prompt_registry.py` omitted the new agents, and `cache_prune` deletes unregistered pairs as STALE;
6. the registry-completeness test written for (5) then found **two more pre-existing instances** —
   `approval_biomarker_split*` (497 entries) and `drug_role_classifier`/`drug_role_reviewer`. Prune now
   reports **stale=0**.

**Cleanup:** `tests/trialcurator/` deleted (its code went in session 5; the tests were orphaned and outside
`make agentic-tests`).

---
### Session 7 (2026-07-30 → 08-03) — THE ONCOTREE CORRECTION. All UNCOMMITTED; NOTHING in `masters/` touched.

**Why:** the user's eligibility-output review arrived as 17 further wrong curations on top of the original 4, plus
"the whole reconciliation step needs to be expanded & strengthened". Measured: only **35 % of the 1,185 distinct
FINAL code expressions were clean**.

**The constraint that shaped everything (user, 2026-08-03):** *"during this testing & improvement stage … no
existing data is to be modified"* until sign-off, and the updated modules must live **inside
`aus_trial_universe/oncotree_review/`** rather than replacing the production ones. Verified after every run that
`masters/` and `derived/` were byte-untouched.

**Spec:** `docs/planning/archive/v2_oncotree_correction_spec.md` — changes A–E, the defect→layer table, files touched, the 3-phase
rerun plan. Inventory + taxonomy: §B1 above.

**What exists now (all isolated):**
- `oncotree_review/{expr,checks}.py` — operand-level parser (masks OncoTree NAMES before parsing, because names
  contain parens and commas) + the 30-entry defect `CATALOGUE`.
- `oncotree_review/updated/` — the four modules staged to replace their production counterparts on approval:
  `oncotree.py` (name/code invariant + validator), `schema.py` (`OncotreeMapping` loses `oncotree_name`),
  `agents.py` (D1–D10 mapper/reviewer + the NEW per-value repairer + the reconciler told to REPAIR structure),
  `reconcile.py` (R0–R8).
- `oncotree_review/updated/adjudications.py` — approved hand-rulings, **expectation-first**: the comparison file
  reports whether the pipeline reached the approved answer unaided (`match`) or had to be overridden
  (`override`). An override is a quality metric on the correction, not a success.
- `run_review.py` (Phase 0, deterministic, no API) and `run_phase1.py` (live, sandboxed, own cache).
- `tests/agentic/oncotree_review/` — 33 tests. **Full suite 247 green.**

**Decisions the user settled:** vacuous exclusions DROP, but in reconciliation not the mapper (the mapper's job is
a faithful translation; cleanup is the next step) · nested-NOT "unless"-clauses flatten · **two whitelisted nested
idioms** the matching engine will special-case, `SKIN AND NOT(MEL)` and `NSCLC AND NOT(LUSC)`, implemented as a
CLOSED set so a third fails loudly · `MBN OR BL` → `MBN` is sound, but WHICH term survives needs the source, so
19 of 23 such values route to the LLM repairer and only the 4 sentinel cases absorb deterministically ·
`PCM AND NOT(MDS OR BLL OR (MBN AND NOT(PCM)))` → `PCM`.

**Phase 0 result (deterministic only, no API):** 1,185 → 635 distinct expressions, 1,324 of 4,971 map rows
changed, 4,899 clean, 45 left for the LLM, idempotence verified.

**Two bugs found by testing rather than assumed — both mine:**
1. Condensing the reviewer's `MAPPINGS YOU MUST FAIL` list dropped four load-bearing counter-examples, and the
   40-value smoke test immediately produced `metastatic invasive breast carcinoma` → `BRCA` (which is NAMED
   "Invasive Breast Carcinoma" — the exact trap the original prompt warned about). **A counter-example removed
   from the reviewer is a rule deleted** — the same doer/reviewer asymmetry this correction is about.
2. The nested-NOT whitelist was specified in the prompts but never implemented in the validator, so the repairer
   would have "fixed" the two idioms the user approved.

**Concurrency:** rate limits probed live from `x-ratelimit-*` (15,000 RPM / 40,000,000 TPM) → 500/500. The run
pinned TPM at the ceiling (`Used 40000000 / Limit 40000000`), ~15 % 429s each with a ~17 ms retry-after absorbed
by the client's backoff. That is the account ceiling, not a misconfiguration.

---
### Session 6 (2026-07-29) — PRODUCTION HARDENING + the arm-surgical repair. All UNCOMMITTED.

**Why:** the pipeline will run unattended, with no AI watching the log. Session 6 made it verify itself.

**1. It now FAILS LOUD.** The break was at the top of the chain: `run.py` correctly returns **rc=3** when trials are
missing, `pipeline.sh` correctly propagates exit codes (`set -euo pipefail`; verified empirically that an exit 7
survives the `tee`) — but **`refresh.py` returned 0 unconditionally**, so a partially-curated store reported success
and still shipped an export. A scheduler could not tell a broken cycle from a clean one. `refresh` now returns
non-zero on any gate FAIL. Contract pinned by `tests/agentic/test_refresh_exit_codes.py`.

**2. `qa/gates.py` — 8 deterministic gates, run as refresh stage 9/9 and standalone (`make agentic-gates`).**
FK integrity · expiry completeness (no orphans in ANY master, `trial_info` tracks the registry exactly) · additive
safety (reference tables may never shrink; trial tables only as expiry explains; plus the EXACT identity
`after == before − expired + curated`) · curation completeness (kept ⊆ store, `elig_rc == 0`) · empty-output
reasons · export integrity (shape + coverage) · universe swing (WARN) · expiry-guard trip · `output_validator`
(WARN). Any FAIL ⇒ non-zero exit + `status: fail`. Gate tests assert the FAILURE directions — a gate that cannot
fail is decoration.

**3. `run_report/` + `STATUS.json` (the user asked for a per-run report).** `outputs`-side module `run_report.py`
writes `data/agentic/run_report/refresh_<ts>.md` per cycle: fresh universe per registry · churn WITH trial ids ·
a before→after delta table over all 15 master tables · export shape · the integrity block · the gate table.
`STATUS.json` is the machine-readable last-run status an external monitor polls (the user chose "exit code + a
status file" over email/Slack) and also supplies `previous_kept` for the universe-swing gate. **Retention: keep 5**
per-run reports (by mtime, same rule + count as the input archives); `STATUS.json` is exempt — it is the single
current-status file, overwritten by design, with history living in the timestamped reports. Pruning happens AFTER
the current report + status are on disk.

**4. `arm_scope` — the 6th eligibility table: WHY an arm is empty.** An arm with an empty DNF contributes no export
row, so a trial can be fully curated and still be absent from the deliverable — and "correctly out of scope" looked
IDENTICAL to "extraction missed it". Two tiers: deterministic (CTGov's structured `eligibilityModule.
healthyVolunteers`, tight healthy-volunteer text rules) then an LLM verdict for the residue. Verdicts:
`healthy_volunteers` · `not_oncology` · `population_not_cancer_selective` · `no_eligibility_text` ·
`unexplained` (= the miss signal; the gate FAILs on it). Idempotent + lookup-first, so it back-fills and maintains
itself; stale verdicts for repaired/expired arms are pruned. **Design lesson baked in:** "the arm's raw row is
empty" is deliberately NOT a deterministic excuse — that restates the problem instead of explaining it, and while
it WAS one, it auto-absolved 7 arms of which **2 turned out to be real misses**.
- Result over the universe: **29 empty arms** = 17 healthy-volunteer · 7 population-not-cancer-selective ·
  5 waived misses. Investigated separately: the **18 trials entirely absent from the export are ALL correctly
  empty** (10 healthy-volunteer PK/bioavailability studies of oncology drugs, 5 non-cancer-selective populations,
  3 not oncology at all — male contraceptive, paediatric pancreatitis, STAREE statins).

**5. `qa/waivers.py` — known-accepted findings.** A gate that fails every cycle for a known reason trains people to
ignore it. Waived items are surfaced as a standing **WARN** (`waived_findings`), never silently absolved, and every
waiver must name its follow-up. Deliberately a code constant, not a data file: `data/` is gitignored, so a waiver is
visible in review and `git log`.

**6. Fixes + retention.** Same-day **archive-label collision** — `archive_current_version` labels by `ddmmyyyy` and
`rename()` fails on a non-empty dir (OSError 66), so ANY second run on one calendar day died in ingest before
downloading a byte; a taken label now falls through to `_2`, `_3`, … (proven live: `archive/29072026_2`).
**Input-archive retention: keep 5** (~230 MB/run was unbounded → ~12 GB/yr). `make agentic-validate` **repointed**
from the retired `combined.tsv` to the Set-A export (column aliasing in the READER so the signed-off check logic is
untouched) and folded into the gates as a WARN.

**7. Arm-surgical extraction repair — 2 of 7 arms fixed, no prompt touched.** See B3 above for the arms, the
still-broken five, and the ⚠ never-adopt-a-whole-trial-re-roll lesson (the `NCT06999980` resectability regression).

**8. Data safety (user asked explicitly).** All snapshots verified to parse; drug md5 record 0 mismatches. The
current good state had been split across two snapshots, so it was consolidated into
**`data/backups/known_good_20260729_post_refresh/`** (all masters + export + report + `MD5SUMS.txt` over 75 TSVs)
with **`data/backups/RECOVERY.md`** giving the exact copy-back commands, what each of the five snapshots covers, and
what needs no backup (inputs re-fetch via `make agentic-ingest`; the LLM cache is a speed optimisation, not truth).
`pre_stage1_*` is the ONLY backup of the raw registry downloads.

**Concurrency note (the user pushed on this).** A 30-minute repair run was NOT unused headroom: 5 trials at
`--workers 5 --max-concurrency 40` peaked at ~35 in-flight against a cap of 40. The cost is per-trial SERIAL
latency — up to 6 refine attempts in sequence, each an extractor call plus a 6-reviewer panel (`NCT02637687` alone
took 2,229 s and burned all 6). More workers compresses MANY trials, not one hard trial. Everything since ran at
maximum (`--map-only` 500/500, reconcile 200/400, refresh 80/500).

**9. Late session-6 fixes.** Stage banners were left reading `1/7 … 6/7` alongside the new `7/9, 8/9, 9/9` — now
consistently `/9`. And the report's `newly curated` list could not distinguish a brand-new registration from an
EXISTING trial whose record changed to match our filters (AU/NZ sites added, status flipped into scope) — it now
labels each id **new registration** vs **newly matching** with `first posted` / `last update` from the raw registry
inputs (`NEW_REGISTRATION_DAYS = 30`; falls back to a flat list above 200 ids and never reads the registry then).
Both trials of the final e2e turned out to be *newly matching*, which is the common case and the reason the
distinction matters. Unit tests never read the real registry inputs (patched).

**⚠ STILL OUTSTANDING from the session-6 doc pass:** the **two workflow diagrams**
(`docs/diagrams/v2_eligibility_workflow_diagram.html`, `docs/diagrams/v2_drug_workflow_diagram.html`) predate `arm_scope`, the gates,
and the run report, and the eligibility one still shows a 7-stage refresh. They must be re-published to their
EXISTING Artifact URLs (never mint new ones) — see memory `workflow-diagram-artifact` +
`scripts/publish_diagram_artifact.sh`.

**Files touched (session 6):** NEW `qa/{gates,waivers}.py`, `run_report.py`, `tasks/eligibility/scope.py`;
CHANGED `refresh.py` (stages 7-9 + exit codes), `core/paths.py` (`RUN_REPORT_DIR`, `ARCHIVE_KEEP`, `prune_archive`,
collision-safe `archive_current_version`), `tasks/eligibility/{schema,store}.py` (`ArmScope` + `arm_scope` table +
`save_scope`/`empty_arms`), `tasks/eligibility/extraction/loaders.py` (`ctgov_healthy_volunteer_flags`),
`tasks/eligibility/qa/validate_output.py` (repoint + `load_output_rows`), `tasks/ingestion/{ctgov,anzctr}.py`
(archive pruning), `Makefile` + `scripts/agentic/pipeline.sh` (`agentic-gates`). NEW tests
`tests/agentic/{test_run_report,test_refresh_exit_codes}.py`, `tests/agentic/qa/test_gates.py`,
`tests/agentic/tasks/eligibility/test_scope.py`. **212 green.**

---
### (prior START-HERE — session 4, symmetric-match vocab; superseded by the block above)

**✅ DONE THIS SESSION (2026-07-28, session 4 — symmetric-match vocab; UNCOMMITTED):**
- **Drug-approval symmetric-match vocab (the handover's TOP backlog item).** Two NEW additive 3NF tables in
  `drug_annotations/current_version/`: `approval_cancer_type_map` (`cancer_type → oncotree_name/code`, 385 rows) +
  `approval_biomarker_map` (`biomarker` split into gene/signature/expression + gene & signature finding-models, 211
  rows). New `make drug-ref-map-approvals` (module `tasks/drug_utility/map_approvals.py`, mirrors `run.py --map-only`).
  **REUSES the signed-off eligibility mappers** (`map_all_columns`) + a NEW per-value biomarker **splitter**
  (doer→reviewer on `core/review.py`, NO web search; `agents.build_biomarker_splitter`) that divides each biomarker
  into the trial side's 3 buckets via the COPIED `_COLUMN_TAXONOMY` (copy-not-import → signed-off extractor
  untouched). Expression/IHC (PD-L1, CD20, hormone-receptor, HER2-overexpression) stays FREE TEXT — symmetric with
  the trial side. **SEEDS** from the trial FINAL maps (exact-match → reuse the FINAL code, no LLM) for guaranteed
  cross-domain code identity. **ADDITIVE:** writes only the 2 tables (`store.save_approval_maps`); the 6 core tables
  are md5 byte-unchanged (snapshot `archive/pre_approval_map_28072026/`). **Full live run:** cancer_type 385 (47
  seeded · 338 mapped · 2 empty=non-cancer) · biomarker 211 (102 gene · 12 signature · 102 free-text · 25 seeded fm).
  Self-reviewed → high quality + symmetric (Ph+→Fusion[BCR::ABL1], ROS1/RET/NTRK→Fusion, MSI-H→sig / dMMR→expr,
  composites split); one ALK-`-positive` routing wobble fixed with a splitter edge rule + re-run (user-approved).
  Export MANIFEST regenerated (8 Set-B tables + the symmetric-match join). Files: `tasks/drug_utility/{schema,store,
  agents,map_approvals}.py`, `export.py` (MANIFEST text), `Makefile`+`pipeline.sh`, `tests/agentic/tasks/drug_utility/
  test_map_approvals.py`, docs (`drug_ref_schema.md` "Symmetric-match vocab", `combined_agentic_run.md`). Memory
  `v2-drug-ref-table`.
- **cancer_type Step-2 reconciliation on the drug side + `joined/` reorg (user follow-up, same session).** The
  drug-approval `cancer_type` now gets the SAME Step-2 reconciliation as the trial side by **REUSING the eligibility
  `mapping.reconcile.reconcile_column`** (no duplicated logic — the user's explicit requirement): a new
  `oncotree_code_FINAL` column on `approval_cancer_type_map` (still 3NF single-key, mirroring
  `finalised_cancer_type_map`). Folded into `make drug-ref-map-approvals`. Live re-run: **1 LLM-adjudicated group**
  (`carcinoma of the ovary` OVARY→OVT) + **43 deterministic OR-order normalisations**; 6 core tables still byte-identical.
  Gene/signature are NOT reconciled (the consistency detector is cancer_type-tuned; seeding already gives cross-domain
  identity). **`joined/` reorganised into per-subsystem subfolders** — `joined/eligibility/`
  (`mapped_eligibility.tsv` + `finalised_mapped_eligibility.tsv`) and `joined/drug_annotations/`
  (`mapped_drug_regulatory_approval.tsv` — NEW, each approval ⋈ its vocab maps incl. `oncotree_code_FINAL` +
  TGA/PBS). Touched `core/paths.py` (JOINED subfolders + `MAPPED_APPROVALS_FILE`), `run.py` (elig joined → subfolder),
  `map_approvals.py` (reconcile + joined writer), 2 eligibility joined-path tests. **`make agentic-tests` = 173 green.**

**✅ DONE THIS SESSION (2026-07-28, session 3 — SIGNED OFF; all UNCOMMITTED):**
- **Isolated live-demo command `make agentic-demo`** (for a presentation — walk an audience through the logs). Runs
  the FULL pipeline over 2 picked trials, one readable stage at a time: extraction → map Step 1 (`--map-only`) → map
  Step 2 (`--reconcile`) → drug ref + main/aux role (`build --from-trials`) → export. **Trials:** `NCT02393625`
  (ALK+ NSCLC, solid — ALK rearrangement→Fusion, ceritinib+nivolumab main) + `NCT05453903` (AML, heme —
  KMT2A/NPM1/NUP98/NUP214 "alteration"→expansion, bleximenib main + chemo backbone aux); override with `IDS=`.
  **Wholly isolated — NO core code changed** (user requirement): new files ONLY = `aus_trial_universe/agentic/demo.py`
  (re-roots ONLY the OUTPUT path constants in `core.paths` → `data/agentic/demo/…` BEFORE importing run/build/export,
  then drives their real entry points) + `scripts/agentic/demo.sh` (standalone driver; reset + tee) + an additive
  `agentic-demo` Makefile target. INPUTS (`trial_universe`, `resources`) + `CACHE_DIR` are NOT re-rooted → reuses the
  ingested trials + shared LLM cache. **0 drug web search** — `demo.sh` seeds the demo drug store from the production
  `drug_annotations/current_version/` so every drug is a pure lookup. **≈35s / 2 trials** (target ≤2 min/trial).
  `RESET=0` keeps the previous `demo/`. Memory `agentic-demo-command`.
- **Export Set A slimmed 38→33 cols + REGENERATED.** Dropped the 5 denormalized drug rollups
  (`arm_canonical_ids`/`arm_main_drugs`/`arm_auxiliary_drugs`/`arm_main_drug_classes`/`arm_main_pottr_classes`) — all
  reachable through Set B on `arm_intervention_names_raw` + `trial_arm_id`, so redundant in Set A. Kept
  `arm_intervention_names_raw` (the Set-B join key). Touched `export.py` (`EXPORT_COLUMNS` + `_arm_drug_facts` +
  manifest) + `test_export.py`; docs updated (`combined_agentic_run.md`, this doc). `make agentic-export` re-run →
  production `export/trial_eligibility.tsv` = **17,659 rows · 5,195 arms · 1,983 trials · 33 cols** + fresh MANIFEST
  (3NF masters read-only, untouched). 162 tests green.

**✅ DONE THIS SESSION (2026-07-28, session 2 — all UNCOMMITTED):**
- **Drug Phase 2 — main vs auxiliary role.** A NEW 6th drug 3NF table `trial_arm_drug_role`
  (`trial_arm_id, canonical_id, role∈{main,auxiliary}`) — canonical grain, NOT a column on `trial_to_intervention`
  (~11% of input strings bundle mixed main+aux, so string-grain is lossy; canonical grain joins to
  `drug_regulatory_approvals` for per-main TGA/PBS). **Purely additive** — the existing 5 drug tables + the
  `trial_arms` registry are byte-unchanged (md5-proven). A cheap **per-arm** doer→reviewer classifier
  (`workflow.classify_arm_roles` + `ArmContext`, NO web_search) on the shared `core/review.py` harness; folds in the
  recovered `DRUG_CURATOR_INSTRUCTIONS` rules. `make agentic-arm-consistency` now checks its FK (`role_dangling`).
  **POPULATED over the universe** (a scratch runner over the FROZEN store — NOT `make drug-ref-build ALL_TRIALS=1`,
  which re-derives ANZCTR arms and would drift `trial_to_intervention`/`trial_arms`; the role-only pass is
  guaranteed additive; snapshot `drug_annotations/archive/pre_role_build_28072026/`): **4,990 arms · 0 failures ·
  12,102 rows (main 5,780 · aux 6,322) · 92 no-drug arms skipped**, at 800 workers / 800 max-concurrency (RPM-bound;
  0 rate-limit pushback). Files: `tasks/drug_utility/{schema,agents,workflow,store,build}.py`, `qa/arm_consistency.py`.
- **The matching-engine EXPORT** (the grand join, delivered as the user's TWO sets). **Set A** =
  `data/agentic/derived/export/trial_eligibility.tsv` — a wide flat file, **33 cols, one row per
  `(trial_arm_id, conjunction_index)`** = trial info (a new `trial_info` master) ⋈ interpreted DNF eligibility ⋈ the
  **FINAL** vocab codes ⋈ the per-arm raw intervention names (`arm_intervention_names_raw`, the Set-B join key).
  **17,659 rows · 5,195 arms · 1,983 trials.** **Set B** = the 6 drug 3NF tables **referenced in place** (a
  `MANIFEST.md` points at `drug_annotations/current_version/` — NO duplicate). `make agentic-export`; `SNAPSHOT=1`
  also mints an immutable self-contained `export/snapshot_<ts>/` bundle. NEW top-level modules `agentic/export.py` +
  `agentic/trial_info.py` (deterministic trial-metadata master from raw CTGov/ANZCTR). Retired the parked
  `run._build_combined`/`COMBINED_*`. `trial_eligibility.tsv` name locked with the user; `min/max_age`+`sex` IN;
  `*_raw`+`faithful` OUT. (The column set was later slimmed 38→33 in session 3 — see the session-3 block above.)
- **Both workflow diagrams re-published** to their existing Artifact URLs (drug = role stage + six tables;
  eligibility = Step-2 reconciliation). Links unchanged (memory `workflow-diagram-artifact`).

**⏭ RESUME AT — remaining integration backlog, dependency-ordered:**
- ~~**Symmetric-match vocab for drug approvals (was TOP).**~~ **✅ DONE session 4** (see the session-4 block above):
  `approval_cancer_type_map` + `approval_biomarker_map`, `make drug-ref-map-approvals`, reusing the eligibility
  mappers + a biomarker splitter, seeded from the trial FINAL maps. The export MANIFEST now documents the symmetric
  join. *Optional follow-on:* a within-drug Step-2 reconciliation pass (unify equivalent drug-side values to one
  code) — the same mechanism the trial side uses; not built (cross-domain consistency is covered by seeding + shared
  prompts/cache, so this is a polish item, not a defect).
- ~~**AUDIT `data/agentic/` subfolder organisation (user, 2026-07-28).**~~ **✅ DONE (2026-07-28, full role-based
   grouping).** The flat top level is now grouped by role: `inputs/` (trial_universe, resources) · `masters/`
   (trial_arms, trial_info, drug_annotations, eligibility — all `current_version/`) · `derived/` (joined, export) ·
   `transient/` (cache, log) · plus top-level `analysis/` + the `demo/` sandbox. **(a)** naming standardised —
   eligibility `current_output/` → `current_version/` (retired the `ELIG_CURRENT_OUTPUT` constant). **(b)** `joined/`
   kept + split per subsystem (`joined/{eligibility,drug_annotations}/`) — a review-artifact area, not redundant.
   **(c)** role grouping done. **(d)** stray `.DS_Store` removed (all `data/` is gitignored). All Python paths derive
   from the bucket roots in `core/paths.py`; bash hardcodes in `pipeline.sh`/`demo.sh` updated. `make agentic-clean`
   now wipes ONLY `transient/` (no longer destroys the eligibility store — a safety fix). 173 tests green; data
   moved by `mv` (rename, no copy; verified no loss). UNCOMMITTED.
1. **Self-contained pipeline — Stage-I ingestion + legacy retirement.** Move download → drug-filter → POTTR-append →
   retire-missing into agentic; retire legacy `eligibility_path`/`drug_utility_path`. Biggest piece for a periodic run.
- **Optional cleanups:** (a) the **11 cancer_type per-value LOGIC residuals** (valid codes, imperfect structure e.g.
  subtype-ANDed-with-parent) in the signed-off oncotree mapper's hard-cell tail — a small manual/targeted pass; (b)
  **repoint `make agentic-validate`** (legacy QA "review of the reviewers") from the retired `combined.tsv` path to
  the new `export/trial_eligibility.tsv` (note the column names differ — it expects plain `cancer_type` etc., the
  export uses `*_interpreted`; small adaptation).

**Specs:** overall = `docs/v2_agentic_pipeline_spec.md` (§9 = the export); drug schema = `docs/reference/drug_ref_schema.md`
(6 core tables incl. `trial_arm_drug_role` + the 2 symmetric-match maps `approval_cancer_type_map`/`approval_biomarker_map`);
run/setup guide = `docs/reference/combined_agentic_run.md` (the Export section);
mapping = `docs/planning/archive/v2_mapping_and_shared_loop_plan.md`. Decisions in memory: `v2-drug-ref-table`, `v2-next-priorities`,
`v2-joined-tables-sql-decision` (RESOLVED), `feedback-additive-safety-first`, `feedback-max-allowable-concurrency`,
`v2-mapping-stage-decisions`, `v2-shared-loop-harness`, `v2-eligibility-orchestration-model`.

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
  views live in top-level **`data/agentic/derived/joined/`**: `mapped_eligibility.tsv` (Step-1) + `finalised_mapped_eligibility.tsv`
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
- Input = the FROZEN store `data/agentic/masters/eligibility/current_output/interpreted_eligibility.tsv` — DO NOT rebuild it.
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
  2. **New central table `data/agentic/masters/trial_arms/trial_arms.tsv`** (`TrialArmStore`, current_version/ + archive/):
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
  + 50 standard trials → a unified 60-trial store at `data/agentic/masters/eligibility/current_output/`.
- **3NF-purity relocation (2026-07-24):** the store dirs now hold ONLY pure-3NF tables. The denormalized joined
  view `combined.tsv` was moved OUT of `eligibility/current_output/` to **`eligibility/combined/combined.tsv`**
  (`combined_dir = store_root / COMBINED` in `run.py`; `COMBINED_OUTPUT`/`COMBINED`/`COMBINED_FILE` in
  `core/paths.py`; validator + 2 tests repointed; docs updated). The drug `current_version/` was already pure 3NF.
  Easily hoisted to a top-level `data/agentic/combined/` if preferred (one line).
- **Prunable response cache (2026-07-24):** the shared `DiskCache` (`data/agentic/transient/cache/`, used by BOTH paths)
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
  - **5 3NF tables** (spec §6.1; layout `docs/reference/drug_ref_schema.md`): `intervention_to_canonical`,
    `trial_to_intervention` (now keyed by `trial_arm_id` → shared `trial_arms` registry; 2026-07-25),
    `drug_annotations_core`, `drug_target_actions`, `drug_regulatory_approvals` (the old
    `drug_ref`/`drug_target`/`drug_indication` names were retired).
  - **Consolidated data structure** under a single relocatable `DATA_ROOT` (`core/paths.py`; = `data/agentic/`,
    promotable to `data/`): `trial_universe/` · `resources/{drug_utility,eligibility}/…/current_version/` ·
    `drug_annotations/current_version/` (+ `archive/`) · `eligibility/` · `log/` · `analysis/`. Loaders read
    `current_version/` (not `latest_version_dir`). `make drug-ref-refresh-pottr` refreshes POTTR from GitHub.
  - **Current build:** `data/agentic/masters/drug_annotations/current_version/` (**1275 drugs** after the 2026-07-25
    migration + orphan prune; metadata in `RESOURCE_INFO.md`).
  - **Doc-currency pass (2026-07-20):** re-verified tests + docs + diagram at sign-off. Fixed stale references left
    over from the data restructure — `drug_ref_schema.md` `trial_to_intervention` was missing the `arm`/`arm_type`
    columns; `store.py`/`build.py`/`__init__.py`/`Makefile` still pointed at the retired
    `resources/drug_ref/version_<ddmmyyyy>/` path (now `drug_annotations/current_version/`); `schema.py` cited a
    non-existent `atc.py` (ATC is in `rxnorm.py`); `rxnorm.py` still described reading the RRF "in place" from the
    legacy tree (it reads the agentic resource dir). Diagram + `combined_agentic_run.md` were already current.
- **Pre-rewrite fallback tag:** `aus-trial-eligibility-path-resource-generation-v1` (code only — NOT data).
- **Run/setup guide:** `docs/reference/combined_agentic_run.md` (all make commands + environment).
- **Design + fields + schema:** `docs/v2_agentic_pipeline_spec.md` (single spec) + `docs/reference/drug_ref_schema.md`
  (drug tables + data layout). **Diagrams** (each overwrites its own Artifact URL on change — see memory
  `workflow-diagram-artifact`): eligibility `docs/diagrams/v2_eligibility_workflow_diagram.html`
  (https://claude.ai/code/artifact/671df104-6474-4c32-b78c-45f4b65d063f) · drug `docs/diagrams/v2_drug_workflow_diagram.html`
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
above + `docs/reference/drug_ref_schema.md` + memory `v2-drug-ref-table` / `agentic-data-root-temporary`. **Nothing
drug-side is outstanding** except two PARKED integration pieces to revisit AFTER the eligibility work:
- (a) map each drug indication's free-text `cancer_type`/`biomarker` into the eligibility vocabulary (OncoTree +
  finding-model) — the *symmetric-match* representation;
- (b) join `drug_annotations` back into the trial `combined` output (by canonical_id + approval-for-this-cancer);
  this also lets the per-trial run **look up** the drug reference instead of a live per-trial `web_search` (a ⓿ speed win).

## ✅ ELIGIBILITY PATH v2 — DONE + VALIDATED (2026-07-21). Full detail: memory `v2-eligibility-orchestration-model`.
The per-trial pipeline is now **extract → map (lookup-first) → grand-flat join**; drug enrichment is a SEPARATE
incremental build that joins in via `(trialId, arm)`. `make agentic-run` (see Quickstart). Output: the accumulating
`data/agentic/masters/eligibility/current_output/` store — 5 3NF tables + `combined.tsv` (see "Output schema"). The three
originally-outstanding issues were all addressed:

**⓿ SPEED — done.** Root cause measured: extraction is ~85% of wall-clock (a hard trial ≈ 1000s at the 6-attempt
cap; standard ≈ 30–150s). Levers applied: (1) **trial-level parallelism** (`run_parallel`, `--workers` default 8);
(2) **`DiskCache`** under `data/agentic/transient/cache/` — run-to-run reuse makes re-runs near-instant (proven: 9/10 complex
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

---

### (from the to-do list) B1 — the OncoTree defect inventory as it stood before the fix

The measured taxonomy and the 18 reported values that drove the correction. Superseded by the full record
in `v2_oncotree_correction_spec.md`; kept here because it is the *before* picture the spec's results are
measured against.

- **(done) B1 — ONCOTREE CODE-EXPRESSION DEFECTS. (was TOP PRIORITY 2026-07-30).** "Find all such instances &
  fix them", at the **LLM-prompt**, **reconciliation** and **deterministic-verification** levels. **B2 (De Morgan)
  and the 11 cancer_type LOGIC residuals are FOLDED IN HERE** — the reported values prove they are one family.

  **THE REPORTED VALUES (4 from 2026-07-29 + 17 added by the user 2026-07-30; 18 unique).** All were located in
  `finalised_cancer_type_map.tsv` and traced to their source cell — see the per-value trace in the taxonomy below.
  ```
   1  (Solid tumour AND NOT(BRAIN)) OR GB
   2  Pancreatic Adenocarcinoma AND NOT(Pancreatic Neuroendocrine Tumor)
   3  HGSOC AND NOT(UCEC AND NOT(UEC))
   4  Endometrial Carcinoma AND NOT(UCS OR USARC)
   5  Diffuse Glioma AND NOT(DMG) AND NOT(HGGNOS)
   6  Solid tumour AND NOT((NSCLC AND NOT(LUSC)) OR HGSOC OR STAD OR ESCA OR GEJ OR COADREAD OR PANCREAS)
   7  Chronic Lymphocytic Leukemia/Small Lymphocytic Lymphoma AND NOT(B-Cell Prolymphocytic Leukemia)
   8  NOT(MLYM) AND NOT(DLBCLNOS AND EMALT) AND NOT(HL AND NHL) AND NOT(DLBCLNOS AND CLLSLL) AND NOT(HGBCL OR HGBCLMYCBCL2) AND NOT(PMBL)
   9  Mature B-Cell Neoplasms AND NOT(PCNSL)
  10  Breast AND NOT(BRAIN)
  11  B-Lymphoblastic Leukemia/Lymphoma AND NOT(BL) AND NOT(ALAL)
  12  Pan-cancer AND NOT(SKIN AND NOT(MEL))
  13  Neuroblastoma AND NOT(LGGNOS)
  14  CCRCC AND NOT(CDRCC) AND NOT(MRC) AND sarcomatoid histology <=30% AND NOT(active brain metastases)
  15  NOT(Pan-cancer AND NOT(SKIN AND NOT(MEL)))
  16  SEM OR (NSGCT AND NOT(TT) AND NOT(GCTSTM)) OR (OGCT AND NOT(OIMT OR OMT)) OR (VGCT AND NOT(VIMT OR VMT)) OR (BGCT AND NOT(BIMT OR BMT OR BMGT)) OR EGCT
  17  (EGCT OR (NSGCT AND NOT(TT) AND NOT(GCTSTM)) OR SEM OR (OGCT AND NOT(OIMT) AND NOT(OMT)) OR (BGCT AND NOT(BIMT) AND NOT(BMGT) AND NOT(BMT)) OR (VGCT AND NOT(VIMT) AND NOT(VMT)))
  18  Haematological malignancy AND NOT(APLPMLRARA) AND NOT(MDS) AND NOT(MS)
  ```
  ⚠ **#14 is NOT a code value** — it is the *interpreted* `cancer_type` cell (free text). Its mapped code is
  `CCRCC AND NOT(CDRCC) AND NOT(MRC)`, which is CORRECT (the mapper properly dropped `sarcomatoid histology <=30%`
  and `NOT(active brain metastases)`). What #14 actually reports is that **the interpreted cell keeps inexpressible
  clinical qualifiers and ships them to the engine in the export's `cancer_type_interpreted` column** — an
  EXTRACTION-side item, not a mapping one. Kept in the list as the exemplar of that class.
  ⚠ **#16 vs #17 are the SAME trial, same logic, two renderings** — the De Morgan/ordering item (ex-B2) at full size.

  **THE FULL DEFECT CATALOGUE (2026-08-03).** Built by the standalone review sandbox (below), which parses every
  expression operand-by-operand instead of regex-scanning it. The catalogue is an explicit table in
  `oncotree_review/checks.py:CATALOGUE`, so *"have we captured all the issues?"* is answerable: a defect either
  has an id there or it does not exist yet. **All 18 user-reported values are detected** (verified value-by-value)
  — the reported list was the seed, not the scope. Over 1,185 distinct FINAL expressions / 4,971 map rows: only
  **419 (35 %) are clean**.

  | defect id | layer | sev | occurrences | map rows |
  |---|---|---|---:|---:|
  | `log_vacuous_exclusion` — excluded code disjoint from every positive code, a no-op | logic | warn | 1,309 | 1,069 |
  | `src_qualifier_in_interpreted` — a non-tumour conjunct in the *interpreted* cell (value #14) | fidelity | review | 1,057 | — |
  | `syn_multi_not_clauses` — `NOT(A) AND NOT(B)` unfactored | syntax | warn | 699 | 465 |
  | `syn_or_order_noncanonical` — OR branches out of canonical order | syntax | warn | 401 | 205 |
  | `log_or_redundant_ancestor` — an OR branch subsumed by another (`MBN OR BL OR DLBCLNOS`) | logic | warn | 75 | 29 |
  | `lex_leaked_name` — an OncoTree NAME in the code field | lexical | error | 69 | 9 |
  | `xf_name_code_mismatch` — `oncotree_name` does not mirror `oncotree_code_FINAL` | cross-field | error | 46 | — |
  | `src_nontumour_exclusion` — every source exclusion was non-tumour-type yet a `NOT()` survived | fidelity | review | 37 | — |
  | `log_disjoint_and` — two mutually exclusive types ANDed (**the user's `DLBCLNOS AND CLLSLL`**) | logic | error | 28 | 14 |
  | `syn_nested_not` — a `NOT()` inside a `NOT()` | syntax | error | 23 | 18 |
  | `log_negation_only` — no positive term at all | logic | error | 16 | 8 |
  | `log_negated_sentinel` — `NOT(Pan-cancer)` | logic | error | 14 | 7 |
  | `log_disjoint_and_inside_not` — `NOT(A AND B)` over disjoint types | logic | error | 6 | 1 |
  | `src_empty_for_cancer` · `syn_redundant_outer_parens` · `log_or_branch_excluded` · `log_exclude_ancestor` | mixed | mixed | 4 · 2 · 2 · 2 | — |

  **Catalogued but ABSENT from the store** (checked, zero occurrences — worth keeping as gates so they stay absent):
  `lex_case_variant_code/name/sentinel` · `lex_unknown_operand` · `lex_whitespace_noise` · `syn_unparseable` ·
  `syn_empty_not` · `syn_ambiguous_precedence` · `log_include_exclude_same` · `log_duplicate_operand` ·
  `log_subtype_and_parent` · `log_sentinel_and_specific` · `cov_unmapped_value`.
  ➜ **On the user's "lower case (breast instead of BREAST)" report:** there are **no** genuine case-variant
  operands anywhere in the store. Every instance is `lex_leaked_name` — the mixed-case token is the OncoTree
  **NAME** (`Breast` IS the name of `BREAST`, `Diffuse Glioma` of `DIFG`), which is why it reads as "lower case"
  in the export. Same defect, and the case-variant gates above stop the real thing from ever appearing.

  **WHAT EACH LAYER BUYS (measured, both modes run):**

  | | canonical form only | canonical + drop vacuous |
  |---|---:|---:|
  | values auto-fixed, nothing left open | 491 | **1,106** |
  | distinct renderings collapsed as duplicates | 57 | **544** |
  | equivalence groups found | 49 | 168 |
  | values still needing a human/LLM decision | **35** (46 map rows, 109 arm-uses) | **79** |

  The 35-value review queue is small enough to adjudicate in one sitting, and it is dominated by exactly six
  patterns: the `UCEC AND NOT(UEC)` unless-clause family (6 values, one trial family), `NOT(Haematological
  malignancy)` second-malignancy exclusions, negation-only prior-malignancy cells, `DLBCLNOS AND CLLSLL`-style
  disjoint ANDs, the `SKIN AND NOT(MEL)` non-melanoma idiom, and `(Solid tumour AND NOT(BRAIN)) OR GB`.

  **ROOT CAUSES (all confirmed, not inferred):**
  - **C1 — a hole in the hard gate.** `tools/oncotree.py:invalid_codes` only inspects tokens matching
    `_TOKEN_RE = [A-Z][A-Z0-9_]+`, i.e. ALL-CAPS. Mixed case is skipped ON PURPOSE so the sentinels pass — but
    OncoTree NAMES are mixed-case too, so `invalid_codes("Pancreatic Adenocarcinoma")` returns `[]` and a leaked
    name sails through. `reconcile.repair_oncotree_code` then never fires, because it is gated on
    `if invalid_codes(...)`. Every one of the 9 leaked names resolves cleanly via `name_to_code()` — the existing
    repair works, it is simply never invoked.
  - **C2/C3/C4 — no structural canonical form.** `normalize_or_order` bails on anything containing
    `AND` / `NOT(` / `(`, i.e. on every expression that could actually diverge.
  - **C6–C11 — the mapper is faithfully transcribing exclusions that are NOT tumour-type statements.** Traced to
    source: `NOT(history of breast cancer)` → `NOT(BREAST)`; `NOT(current or history of malignancy disease)` →
    `NOT(Pan-cancer)`; `NOT(CNS only disease)` → `NOT(BRAIN)`; `NOT(any hematologic malignancies)` →
    `BREAST AND NOT(Haematological malignancy)`; `NOT(synchronous NSCLC disease)` → `NSCLC AND NOT(LUSC)`.
    These are **prior/second-malignancy history, CNS-involvement and metastasis-site exclusions** — none of them
    constrains the arm's tumour TYPE, and several are trial-wide exclusions that leaked onto one cohort
    (#13 `NOT(LGGNOS)` on a neuroblastoma cohort). The prompts have a rule for inexpressible *qualifiers* but
    **no rule for an exclusion that is about the wrong THING**.
  - **C6 specifically** is the "unless" construction: `NOT(UCEC AND NOT(UEC))` faithfully encodes "exclude
    endometrial cancer *unless* endometrioid" — but the unless-condition is stage/grade/age, which OncoTree cannot
    express, so the project's own inexpressible-exclusion doctrine says OMIT the whole thing.
    NB `NOT(UCEC AND UEC)` is **NOT** the intent: `UEC ⊂ UCEC`, so it collapses to `NOT(UEC)` — the exact inverse.

  **THE REVIEW SANDBOX — `make oncotree-review` (built 2026-08-03; user requirement: "a standalone subfolder
  that allows the comparison of current vs updated oncotree mapping side-by-side … until we complete this
  correction with my sign-off, no existing data is to be modified").**
  Package `aus_trial_universe/oncotree_review/` — deliberately SEPARATE from the signed-off
  `tasks/eligibility/mapping/`, so production prompts and code stay untouched while corrections are designed.
  - `expr.py` — the operand-level parser + canonical form. Masks OncoTree NAMES longest-first BEFORE parsing,
    because names legitimately contain parens and commas (`Primary Mediastinal (Thymic) Large B-Cell Lymphoma`,
    `Oligodendroglioma, IDH-mutant, and 1p/19q-Codeleted` — note the lowercase `and`). A parser that splits first
    shreds them; this is why the earlier regex approach could never work.
  - `checks.py` — the `CATALOGUE` (one row per defect id: layer, severity, description) + every detector.
  - `run_review.py` — READ-ONLY over the store; `_assert_safe()` refuses to write anywhere except the workspace.
  - Output `data/agentic/analysis/oncotree_review/<label>/`: `summary.md` · `findings.tsv` (complete inventory,
    one row per value×defect) · `side_by_side.tsv` (current → proposed, resolved, still-open, verdict, source) ·
    `review_queue.tsv` (only what needs judgement, sorted by arm-count) · `equivalence_groups.tsv`.
  - `make oncotree-review` / `make oncotree-review DROP_VACUOUS=1` — the two policy modes, side by side.
  - Verified: `masters/` and `derived/` were byte-untouched across every run.
  - It has NO unit tests yet — deliberately, while the catalogue is still moving. Its evidence is empirical: all
    18 reported values detected, and every new detector spot-checked against real store values (two over-firing
    heuristics were found and fixed that way). Tests land when L1 moves into `tools/oncotree.py`.

  **➜ THE IMPLEMENTATION SPEC IS `docs/planning/archive/v2_oncotree_correction_spec.md` (2026-08-03, awaiting sign-off)** — code
  changes A–E, the defect→layer assignment table, files touched, and the 3-phase rerun plan (deterministic →
  sandboxed live via `--store-root` → production). The three layers below are the summary it expands.

  **THE FIX — THREE LAYERS (do them in this order; layer 1 alone needs no LLM and no cache invalidation).**
  - **L1 · DETERMINISTIC VERIFICATION (`tools/oncotree.py`, no prompt change, no re-run).** Replace the token-regex
    check with an **operand-level parser**:
    `expr := or; or := and (' OR ' and)*; and := unary (' AND ' unary)*; unary := 'NOT(' expr ')' | '(' expr ')' | ATOM`,
    with OncoTree NAMES masked longest-first BEFORE parsing (names contain parens/commas —
    `Primary Mediastinal (Thymic) Large B-Cell Lymphoma` — so a naive parser splits them). Every operand must then
    be a valid code or one of the 3 sentinels. Promote every `error`-severity id in the CATALOGUE to a hard check
    so it can never be emitted again, keep `warn` ids as warnings. This one function becomes the shared gate for
    the mapper's `check()`, the reconciler's `check()`, and a new QA gate — port `oncotree_review/{expr,checks}.py`
    across wholesale and add its unit tests then.
  - **L2 · RECONCILIATION — EXPAND AND STRENGTHEN THE WHOLE STEP (user, 2026-08-03), not just the pre-pass.**
    Today Step 2 is: repair-leaked-names (which never fires, see root cause) → sort a flat OR → group by
    input-text similarity → LLM-adjudicate. Four changes:
    1. **Canonical form** replaces the two-trick pre-pass: names→codes, case-normalise, factor
       `NOT(A) AND NOT(B)` → `NOT(A OR B)`, sort OR branches, dedupe, flatten same-operator nesting, strip
       redundant parens. Assert the result with L1.
    2. **Group on the CANONICAL FORM as well as the input-text key.** This is what makes #16 and #17 meet — today
       they never do, because the detector groups by INPUT similarity and those two inputs genuinely differ.
       Measured: +49 equivalence groups / 57 renderings collapsed (168 / 544 if vacuous exclusions are dropped).
    3. **Feed the adjudicator the DEFECT LIST, not just the group.** The reconciler prompt currently says
       "preserve each member's AND/OR/NOT structure" — which is precisely the instruction that preserved every
       structural defect in the table above. It must instead receive the L1 findings for each member and be told
       to repair them.
    4. **Reconcile the `oncotree_name` column too.** 46 rows have a name that no longer mirrors the FINAL code
       (Step 2 rewrites the code and leaves the name behind). It does not reach the export — `export.py` renders
       the name from the code — but it makes the store's own map table untrustworthy to any human reviewing it.
  - **L3 · PROMPT (mapper + reviewer; needs user sign-off → cache invalidation → full re-run).** Three new rules:
    (i) **an exclusion must be a tumour-TYPE carve-out from the positive scope** — prior/second-malignancy history,
    CNS involvement, metastasis site, and synchronous second primaries are NOT tumour types: drop them (with
    counter-examples from the traces above); (ii) **the positive scope is mandatory** — never emit a
    negation-only expression, never negate a sentinel, never `NOT(A AND B)` over two disjoint types; (iii)
    **no nested NOT()** — a set difference has ONE level (`NSCLC AND NOT(LUSC)` is fine, `NOT(UCEC AND NOT(UEC))`
    is not); if the carve-out condition is inexpressible, OMIT the exclusion rather than broaden it (the doctrine
    the gene-alteration mapper already uses). Add the canonical negation form to the rules so the doer emits it
    directly instead of relying on L2 to repair it.
  **THE FIX-AND-REVIEW WORKFLOW (systematic; nothing lands in the store until the user signs off).**
  | step | what | who | gate to the next step |
  |---|---|---|---|
  | 0 | `make oncotree-review` on the untouched store — the baseline inventory | done 2026-08-03 | — |
  | 1 | **Decide the two policies** (vacuous exclusions; nested-NOT canonical form) | **user** | both answered |
  | 2 | Implement the canonical form in the sandbox; re-run both modes | code | 0 `error`-severity findings remain outside the review queue |
  | 3 | User reads `side_by_side.tsv` — every `changed=yes` row, current → proposed | **user** | sign-off on the deterministic layer |
  | 4 | Adjudicate `review_queue.tsv` (35 values / 46 map rows / 109 arm-uses) against their source cells; each gets a hand-written FINAL | **user + LLM** | queue empty |
  | 5 | Draft the L3 prompt rules from the adjudications — every step-4 decision becomes a counter-example | code | **user signs off the prompt diff** |
  | 6 | Re-run the review over a cache-bypassed sample to confirm the prompts emit the canonical form unaided | code | sample clean |
  | 7 | **Only now** apply: L1+L2 into production, invalidate the cache, full re-run (shared with B3) | code | gates PASS + a fresh `make oncotree-review` shows 0 errors |
  | 8 | Snapshot to `data/backups/known_good_*` before and after | code | md5 recorded |
  **Regression rule:** step 7's re-run must be diffed against the step-3 side-by-side. Any value that changes in a
  way step 3 did not authorise is a regression — this is the same lesson as the `NCT06999980` whole-trial re-roll
  (never let a re-run silently rewrite values a human already approved).

  **TWO OPEN DECISIONS (needed before step 2):**
  1. **Vacuous exclusions — drop or keep?** 1,069 map rows / 3,289 export rows. `SKCM AND NOT(UM)` is a no-op to a
     matching engine (a patient has one tumour type, and UM is not under SKCM), and these no-ops are the main
     reason two mappings of one concept diverge. But they do record a real protocol carve-out. **Both modes have
     been run** — dropping them raises auto-fixed values 491 → 1,106, collapses 544 duplicate renderings instead
     of 57, and shrinks the human review queue from 35 values to… 79 (it rises, because dropping an exclusion
     exposes values whose remaining defects were previously masked by the vacuous one). Recommendation:
     **drop them in the canonical form, keep the un-normalised Step-1 code in its existing column** (the store
     already keeps `oncotree_code` alongside `oncotree_code_FINAL`, so nothing is lost).
  2. **C6 nested NOT — canonical form?** Recommendation: **forbid nesting; a set difference is one level deep.**
     Where the carve-out is inexpressible (the `UCEC/UEC` family, 6 values, all one trial family), OMIT the
     exclusion → `HGSOC`. Where it is expressible (#6's `NSCLC AND NOT(LUSC)` = non-squamous NSCLC, and #12's
     `SKIN AND NOT(MEL)` = non-melanoma skin cancer), the nesting is only there because the term sits INSIDE
     another `NOT()`; those need a named decision — either allow depth-2 in that one position or pre-resolve the
     inner difference to an explicit OR of sibling codes.
