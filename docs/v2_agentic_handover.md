# v2 Agentic Pipeline — Handover

## ▶ NEXT SESSION — START HERE (updated 2026-07-29, end of session 6)

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
> **Order the user set:** (1) inspect the e2e run's output + run report → (2) **A3** file restructure
> (`pipeline/` + `outputs/` + `qa/`) → (3) **A4** legacy module removal → then the rest of the backlog.
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
- **A2 — RESTRUCTURE `aus_trial_universe/` (layout APPROVED by the user).** Six loose modules sit next to `core/`
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
  (iii) `sql/` + `docker-compose.postgres.yml` + `requirements-db.txt`; (iv) `docs/eligibility_path/` +
  `docs/drug_utility_path/` + `scripts/one_off/`.
  **⚠ `data/` trees stay for now — the user chose "code only" (2026-07-29).** (`data/{drug_utility_path 28 GB,
  eligibility_path 95 MB, matched_trials 41 MB, trial_inputs 65 MB}` are detached: nothing reads them, no symlinks,
  and the agentic RxNorm resource is a real 621 MB copy.)
- **A4 — tail cleanup, with A3:** **`PyYAML` is MISSING from `requirements.txt`** (a latent fresh-install failure —
  `tools/oncotree.py` reads `oncotree.yaml` and it only works because the conda env happens to have it); prune the
  rest of `requirements.txt`; rename `tests/agentic/` → `tests/` (free once `tests/trialcurator/` is gone);
  optionally promote `data/agentic/` → `data/` (one `DATA_ROOT` line).
- **A4b — REPUBLISH THE TWO WORKFLOW DIAGRAMS.** `docs/v2_{eligibility,drug}_workflow_diagram.html` predate
  `arm_scope`, the gates and the run report; the eligibility one still shows a 7-stage refresh. **Overwrite the
  EXISTING Artifact URLs — never mint new ones** (memory `workflow-diagram-artifact`;
  `scripts/publish_diagram_artifact.sh`). Do AFTER A2/A3 so the module paths shown are final.
- **A5 — HIGH-QUALITY `README.md` (user, 2026-07-29).** The current 33 lines are entirely about the retired ACTIN
  Docker flow. Benchmark: the READMEs at https://github.com/hartwigmedical/hmftools — **and do better than those**.
  Should cover: what the pipeline is and produces (Set A + Set B), the 9-stage refresh, how to run it, the data
  layout, the QA/gates story, and how to recover (`data/backups/RECOVERY.md`). Do it AFTER A2/A3 so paths are final.

### B. Needs a DEDICATED SESSION — prompt/vocab work (sign-off → cache invalidation → full re-run)
Do B1 + B2 + B3 together: one cache invalidation, one re-run.
- **🔴 B1 — ONCOTREE CODE-FIELD DEFECTS REPORTED BY THE MATCHING ENGINE (user, 2026-07-29). "Find all such
  instances & fix them."** The four reported values:
  1. `Solid tumour AND NOT((NSCLC AND NOT(LUSC)) OR HGSOC OR STAD OR ESCA OR GEJ OR COADREAD OR PANCREAS)`
  2. `Haematological malignancy AND NOT(APLPMLRARA) AND NOT(MDS) AND NOT(MS)`
  3. `Diffuse Glioma AND NOT(DMG) AND NOT(HGGNOS)`
  4. `Pancreatic Adenocarcinoma AND NOT(Pancreatic Neuroendocrine Tumor)`
  **Deterministic scan over the 4,969 distinct FINAL codes (2026-07-29) → three classes:**
  - **(a) NAMES leaked into the code field** (#3, #4) — **ROOT CAUSE FOUND: a hole in the deterministic validator.**
    `tools/oncotree.py:invalid_codes` only checks tokens matching `_TOKEN_RE = [A-Z][A-Z0-9_]+`, i.e. ALL-CAPS
    tokens — mixed-case is skipped ON PURPOSE so the sentinels (`Solid tumour`, `Pan-cancer`) pass. But OncoTree
    NAMES are mixed-case too, so `invalid_codes("Pancreatic Adenocarcinoma")` returns `[]` and every leaked name
    sails through the hard gate. In the export: **45 rows** carry `Pancreatic Adenocarcinoma AND NOT(...)`, **8**
    carry `Diffuse Glioma AND NOT(...)`. **FIXABLE WITHOUT ANY PROMPT CHANGE:** make the validator operand-level
    (split on AND/OR/NOT/parens; every operand must be a valid code or one of the 3 sentinels), then re-run the
    EXISTING deterministic name→code repair (`reconcile.repair_oncotree_code` + `tools/oncotree.name_to_code`).
    No LLM, no cache invalidation. **Do this part first and separately.**
  - **(b) Multiple separate `NOT()` clauses instead of one factored `NOT(A OR B)` — 480 values.** Same family as
    the De Morgan item (B2): `OCSC AND NOT(NPC) AND NOT(SNSC) AND NOT(HNSCUP)` should be
    `OCSC AND NOT(NPC OR SNSC OR HNSCUP)`. Deterministic to normalise.
  - **(c) Nested / double negation — 18 values.** `HGSOC AND NOT(UCEC AND NOT(UEC))` — a `NOT()` containing a
    `NOT()`. Needs a decision on the canonical form (probably: resolve to the intended positive/negative set, or
    forbid nesting and re-map), then a rule in the mapper + a deterministic checker.
  - Also verify the sentinel-plus-exclusion pattern in #1 is intended (`Solid tumour AND NOT(<7 types>)`), and that
    `GEJ` / `MS` / `PANCREAS` are the codes actually meant (all three ARE valid OncoTree codes — confirmed).
- **🔴 B2 — ONCOTREE CODE-RENDERING CONSISTENCY (the De Morgan item).** See the detailed entry below; same family
  as B1(b)/(c). Fold the 11 cancer_type per-value LOGIC residuals in here too.
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
- **(B2 detail) 🔴 ONCOTREE CODE-RENDERING CONSISTENCY — NEEDS A DEDICATED SESSION (user, 2026-07-29).** The FINAL vocab can
  express the SAME logic two ways, and Step-2 does not catch it. Found live on the 2026-07-29 refresh's one new
  trial (`ACTRN12626000937314`, endometrial): its two arms produced
  `UCEC AND NOT(UCS) AND NOT(ESS)` and `UCEC AND NOT(UCS OR ESS)` — **logically identical by De Morgan, same three
  codes, two renderings**. Step-2 left both because `find_inconsistencies` groups by INPUT-value similarity and the
  two inputs genuinely differ ("advanced (stage III or IV)" vs "recurrent"), so the pair never forms a group. The
  deterministic pre-pass normalises OR-branch ORDER (`normalize_or_order`) but has no De Morgan / negation-form
  normalisation. **Impact depends on the matching engine:** cosmetic if it parses the boolean expression, a REAL
  miss if it string- or set-compares. Scope for the session: a canonical negation form (probably factor to
  `NOT(A OR B)`), applied deterministically in `mapping/reconcile.py`'s pre-pass + asserted by a checker, then a
  full-store sweep for how many existing FINAL values are affected. Related: the **11 cancer_type per-value LOGIC
  residuals** below are the same family (valid codes, imperfect structure) — do both in that session.

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
(`docs/v2_eligibility_workflow_diagram.html`, `docs/v2_drug_workflow_diagram.html`) predate `arm_scope`, the gates,
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

**Specs:** overall = `docs/v2_agentic_pipeline_spec.md` (§9 = the export); drug schema = `docs/agentic/drug_ref_schema.md`
(6 core tables incl. `trial_arm_drug_role` + the 2 symmetric-match maps `approval_cancer_type_map`/`approval_biomarker_map`);
run/setup guide = `docs/agentic/combined_agentic_run.md` (the Export section);
mapping = `docs/v2_mapping_and_shared_loop_plan.md`. Decisions in memory: `v2-drug-ref-table`, `v2-next-priorities`,
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
  - **5 3NF tables** (spec §6.1; layout `docs/agentic/drug_ref_schema.md`): `intervention_to_canonical`,
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
Full detail: `docs/agentic/combined_agentic_run.md`; drug path: `docs/agentic/drug_ref_schema.md`;
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
  tasks/drug_utility/        # DRUG UTILITY PATH — 6 3NF tables; see docs/agentic/drug_ref_schema.md
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
    schema.py, store.py      # ArmEligibilityRaw + InterpretedEligibility + 5 map dataclasses + MAPPED_ELIGIBILITY_COLUMNS; EligStore.save_maps() / save_mapped_eligibility() (write ONLY new files, never re-persist content tables)
    tools/
      oncotree.py            # oncotree.YAML vocab + valid_codes + ancestors + vocab_reference() (indented Name (CODE) tree) + name_to_code() (reverse, for Step-2 repair); 3 sentinels
      finding_model.py       # FULL grammar validator (2026-07-28): finding_model_problems() = field/enum/scope/HGVS-aware DSL parser (CLASS_SPEC) — the hard SYNTAX gate; + GRAMMAR_REFERENCE
    qa/                      # validate_output.py (make agentic-validate) + arm_consistency.py (make agentic-arm-consistency) + mapping_consistency.py (cross-value: canonical_key/find_inconsistencies — used by Step 2)
tests/agentic/               # 162 tests (fake-client); mirrors tasks/ (core [+test_review], tasks/shared, tasks/eligibility [+ test_mapping_findingmodel, test_run_map_only, test_reconcile, qa/test_mapping_consistency], tasks/drug_utility [+ test_drug_utility_roles]) + top-level test_export.py + test_trial_info.py
scripts/agentic/pipeline.sh  # driver: python-pick, .env, tests-preflight, log tee; subcommands run|validate|export|clean|tests|cache-prune|arm-consistency|drug-migrate-trial-arms|drug-ref-build|drug-ref-refresh-pottr
scripts/agentic/demo.sh      # STANDALONE demo driver (make agentic-demo; session 3): python-pick, .env, reset data/agentic/demo/, SEED demo drug ref from production (=> 0 web search), tee. Does not touch pipeline.sh.
docs/agentic/combined_agentic_run.md   # run/setup guide
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
(session 4; `make drug-ref-map-approvals`). See `docs/agentic/drug_ref_schema.md`.

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
