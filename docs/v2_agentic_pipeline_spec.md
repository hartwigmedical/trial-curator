# Trial Curator v2 — Agentic Pipeline Spec

Single source of truth for the v2 agentic pipeline (`aus_trial_universe/agentic/`): why it exists, how it's
built, the field sources, and the output schema. For **how to run it**, see `docs/agentic/combined_agentic_run.md`.

- **Status:** built and verified end-to-end (ctgov + anzctr). Living document.
- **Branch:** `AUS-328-Aus-trial-universe-v2`. **Fallback tag:** `aus-trial-eligibility-path-resource-generation-v1` (code only).
- **LLM provider:** OpenAI only (no Claude API access in this org). SDK `openai>=2.x` (Responses API `web_search`).

---

## 1. Motivation

Every hard problem here — eligibility and drug-utility alike — is the same problem: turning **large, messy free
text into structured, normalized data**. v2 replaces the old scatter of bespoke LLM scripts + `.py` curations +
separate client layers with **one reusable agentic machinery**. The behaviour we want is a Claude-Code-style
session (analyse → dispatch specialists → consolidate → check → loop on error), but reproduced with a
**deterministic, code-driven orchestrator** rather than an autonomous LLM planner — so it is testable,
reproducible, cheap, and debuggable across thousands of trials.

## 2. Principles

1. **Clean slate.** Nothing from the old setup is a fixed point; legacy is mined for *learnings* only. Agentic
   owns copies of anything it reuses (e.g. `pipeline_io.py`, resource reads) — never import from `eligibility_path`.
2. **Schema-first.** Every agent pins its exact pydantic output schema; the framework never negotiates shape.
   This makes each step unit-testable with a fake client, independent of the LLM.
3. **Deterministic orchestration (pattern B).** Control flow is plain Python. LLMs power the *stages*, never the *plan*.
4. **One agent per independent task.** Split work into a separate agent only when the sub-tasks are genuinely
   independent; keep jointly-decided outputs in one agent (see §3). This governs the whole agent inventory.
5. **Doer → reviewer — the reviewer only advises; the doer is the sole writer.** Each generative step is paired
   with an adversarial reviewer on a bounded refine loop (evaluator–optimizer). Verification is an easier task
   than generation, so a reviewer catches what the doer missed. **The reviewer can only return a verdict +
   problems, never a corrected artifact** — `ReviewVerdict` (`tasks/*/schema.py`) carries no output field, and
   `refine()` (`core/workflow.py`) feeds the critique *back to the doer*, which re-generates (Reflexion-style,
   **not** an editor). The deterministic validators are feedback-only too. One writer ⇒ no unchecked edit ever
   reaches the output, and every accepted value was re-checked *after* it was (re)written. Trade-off: it pays
   full re-generation and can stall convergence on the hardest trials (see §12).
6. **Determinism via cache.** The response cache is the deterministic layer (identical request → identical output).
   `temperature`/`seed` are omitted by default (current reasoning models reject `temperature`).
7. **Model-agnostic.** One client; the model is configuration, swappable per agent.

## 3. Architecture

Two layers. The orchestrator is **code, not an LLM**.

|                | What it is | Decides what runs next? | Deterministic? |
|----------------|------------|-------------------------|----------------|
| **Orchestrator** (`agentic/run.py` + task workflows) | plain Python | **Yes** — fixed steps | Yes |
| **Agent** (a specialist) | one OpenAI call + pinned schema | No — does its one job | No (cache tightens) |

**Guiding principle — one agent per *independent* task.** Split by independence, not by field count:
- **Keep jointly-decided outputs in one agent.** A DNF row is a conjunction *across* columns (which cancer type
  co-occurs with which gene), so one **extractor** emits whole rows — splitting per column would lose the pairing.
- **Split genuinely-independent checks into separate agents.** Faithfulness of each column is independent, so
  **review** is a parallel panel of focused reviewers.
- **Deterministic work gets zero agents** (e.g. CTGov cohort/drug read straight from JSON).

## 4. Runtime — `agentic/core/` + `agentic/tools/`

- **`client.py` (`LlmClient`)** — the single door to OpenAI.
  - `.parse(schema, …)` → validated pydantic via `chat.completions.parse` (retries, response cache, tracing).
  - `.research(schema, …)` → same, but via the **Responses API `web_search` tool** for live-web research (TGA/PBS).
- **`agent.py` (`Agent`)** — `prompt + output schema + model (+ web_search flag)` bound to the client; `__call__`
  returns the validated object. `web_search=True` routes to `.research()`.
- **`workflow.py`** — generic `fan_out()` (parallel) + `refine()` (bounded check→repair loop; `max_attempts≈3`).
- **`tools/`** — reference data + validators agents lean on: `oncotree.py` (code vocab + validator),
  `finding_model.py` (grammar + syntax validator).

## 5. The pipeline (one command, one output)

`make agentic-run` runs, per trial, in **one streamed pass** (partial results survive an interrupt); one output
TSV + one log per run. No intermediate files.

```
SELECT/ASSEMBLE ─▶ COHORTS ─▶ EXTRACT ─▶ (rule-check + REVIEW panel) ─refine▶ MAP ─▶ DRUG ─▶ stream one row-set
   loaders          §7.3       §7 (LLM)         §7.5                       §8.1-2   §8.3
```

Run modes: `ID=<id>` (one) · `IDS=<a,b,c>` (a set) · no arg = ALL trials. Output name: single → `trial_resource_<id>.tsv`;
multiple/all → `trial_resource_<YYYYMMDD_HHMMSS>.tsv`, under `data/agentic/output/`.

## 6. The DNF (disjunctive normal form) model

The output table is in **disjunctive normal form**: a set of rows ORed together, each row a conjunction of ANDed cells.
- **One row = one satisfiable conjunction** (cells ANDed). Rows sharing a `(trialId, cohort)` are **ORed**.
- **Conditionals become co-occurrence:** "if cancer A then mutation X; if cancer B then mutation Y" → two rows.
- **Exclusions are inline `NOT(...)`.** A cell holds the full requirement for its criterion in that row (several
  ANDed terms allowed, e.g. `solid tumour AND NOT(melanoma)`); only genuine OR-alternatives split into rows, so
  every row stays a pure conjunction. (This is the human-readable, pre-curation layer; the finding-model columns
  are the matcher-encoded form alongside it.)
- **Provenance:** every extracted cell = `value [SRC1; SRC2]` — all contributing source sections, `;`-delimited.

## 7. Extraction stage (Stage I)

### 7.1 Input assembly — all relevant sections
The extractor and reviewers see the **same** assembled document (title/conditions/description are where cancer
type + molecular selection often live). Assembly is per-source.

**CTGov raw field inventory** (`protocolSection.<module>.<field>`):

| Field | Used as / note |
|---|---|
| `identificationModule.briefTitle` / `officialTitle` | TITLE / OFFICIAL TITLE |
| `conditionsModule.conditions` / `keywords` | CONDITIONS / KEYWORDS |
| `descriptionModule.briefSummary` / `detailedDescription` | BRIEF SUMMARY / DETAILED DESCRIPTION (~half of trials) |
| `eligibilityModule.eligibilityCriteria` | ELIGIBILITY CRITERIA (incl + excl in one field) |
| `armsInterventionsModule.interventions[]` | `.type` (DRUG/BIOLOGICAL/…), `.name`, `.otherNames`, `.description` |
| `armsInterventionsModule.armGroups[]` | `.label`, `.type`, `.interventionNames` — the cohort/arm structure |
| `eligibilityModule.sex/minimumAge/…`, `designModule.phases/studyType`, `statusModule.overallStatus`, `contactsLocationsModule` | out of extraction scope (gates / housekeeping) |

**ANZCTR** (`anzctr_field_extractions.csv`): `STUDY TITLE`, `SCIENTIFIC TITLE`, `HEALTH CONDITION`, `INTERVENTIONS`,
`INCLUSIVE CRITERIA` → INCLUSION CRITERIA, `EXCLUSIVE CRITERIA` → EXCLUSION CRITERIA. (`ACTRN` stored as bare digits;
display id is `ACTRN`-prefixed. `COMPARATOR`/`CONTROL`, age/phase/status/geography = gates/housekeeping.)

### 7.2 The five eligibility columns + taxonomy
Definitions mirror `pydantic_curator/criterion_schema.py`:

| Column | Captures | Examples |
|---|---|---|
| `cancer_type` | tumour type/site/histology/stage under study (holistic) | "metastatic NSCLC" |
| `gene_alteration` | specific gene + alteration (DNA/mRNA) | "EGFR exon 19 deletion", "KRAS G12C" |
| `molecular_signature` | composite/genomic signature (not one gene's variant) | "MSI-H", "TMB-high", "HRD" |
| `molecular_biomarker` | expression-based, protein/IHC | "PD-L1 ≥1% (IHC)", "HER2 IHC 3+", "ER+" |
| `prior_therapy` | required/excluded prior treatment (not permissive) | "≥1 prior platinum line", "NOT(prior anti-PD-1)" |

Edge rules baked into prompts: HER2/ERBB2 — expression→biomarker, amplification→gene_alteration; MMR — dMMR/pMMR
IHC→biomarker, MSI-H→signature; histology folds into `cancer_type`; permitted (non-restricting) prior therapies are
omitted; out-of-scope criteria (age/labs/PS/comorbidity/other-malignancy/reproductive) ignored.

**Per-criterion source sections** (which assembled sections feed each): `cancer_type` — all sections
(titles/conditions/keywords/summary/description/eligibility). `gene_alteration` / `molecular_signature` /
`molecular_biomarker` / `prior_therapy` — the same set, chiefly the eligibility/inclusion+exclusion criteria.

### 7.3 Cohorts
One cohort per arm. **CTGov:** deterministic from `armGroups` (`label`, `type` → `arm_type`, `interventionNames`
→ drug). **ANZCTR:** a conservative **cohort-detection agent** (default single cohort; only splits on explicit
distinct-eligibility groups) + an **LLM drug agent** (from `INTERVENTIONS`). The extractor is **cohort-aware**: it
assigns each criterion to a specific cohort or "trial-wide"; a cohort's effective eligibility =
`trial-wide ∧ cohort-specific` (cross-product distribution), so each output row is self-contained with its own drug.

**Scope-assignment contract (2026-07-10).** The COHORTS list is a **fixed, known set** (CTGov: from `armGroups`);
the extractor's job is to **assign** each criterion to exactly ONE scope — never state the same criterion in two
scopes. `trial-wide` = shared identically by ALL cohorts, stated once; a cohort id = defining/specific/varying
for that cohort (a subset-shared criterion is emitted once per applicable cohort). This prevents the
cross-product **blow-up** where a criterion duplicated across scopes multiplies out: e.g. `NCT04221035` (SIOPEN
HR-NBL2) restated its neuroblastoma staging in both trial-wide (17 OR-rows) and each cohort → `17 × 40 = 680`
rows, 86% of them unsatisfiable `Stage A AND Stage B` conjunctions. Deterministic **distribution safety net**:
(1) single-valued axes (`cancer_type`) are never ANDed across scopes — the **cohort value wins** (`_merge_cell`);
(2) exact-duplicate rows are **de-duplicated** after merge; (3) a `trial-wide × cohort-specific` product beyond a
threshold logs a `WARN` (never silently emitted). The `structural` reviewer flags cross-scope duplication so
refine can fix it upstream.

### 7.4 Provenance vocabulary (the `[source]` tags)
- **CTGov:** `TITLE`, `OFFICIAL TITLE`, `CONDITIONS`, `KEYWORDS`, `BRIEF SUMMARY`, `DETAILED DESCRIPTION`, `ELIGIBILITY CRITERIA`, `INTERVENTIONS MODULE`.
- **ANZCTR:** `STUDY TITLE`, `SCIENTIFIC TITLE`, `HEALTH CONDITION`, `INCLUSION CRITERIA`, `EXCLUSION CRITERIA`, `INTERVENTIONS`.

Cite all sections a value came from, `;`-delimited (CTGov bundles incl+excl in one `ELIGIBILITY CRITERIA` field;
ANZCTR keeps them separate, so a negated criterion may cite `EXCLUSION CRITERIA`).

### 7.5 Review panel
One cohort-aware **extractor** (whole rows) checked by a rule-check then a **5-agent parallel panel**:
`cancer_type` (strengthened: reject false-positive tumours seen only in prior-therapy/history/exclusion context),
`molecular` (faithfulness + correct column per taxonomy), `prior_therapy`, `structural` (DNF integrity + cohort
scope), and `drug` (**advisory** — doesn't gate). The bounded refine loop re-runs the extractor on the four gating
reviewers' feedback (max ~3 attempts). **Incremental repair:** on a gating failure the extractor receives its
OWN previous table + only the flagged issues and keeps unflagged rows verbatim (preserves correct work, aids
convergence). The drug reviewer is reported but non-blocking. Also enforced: cancer_type never ANDs two
different tumour types (CONDITIONS is authoritative; a broad umbrella is dropped when the trial is clearly one
specific type; different types are OR rows), and no cell holds `X AND NOT(X)` (split across DNF rows instead).

**Extraction judgement rules (2026-07-10).** Two failure modes need LLM interpretation, not a deterministic
guard:
- **Capture tumour-type EXCLUSIONS.** An "except / excluding / other than" carve-out is a real criterion —
  encoded as a same-cell `NOT()` and never dropped (e.g. DMG trial excluding thalamic/cerebellar DMG →
  `DMG AND NOT(thalamic and cerebellar DMG)`). The `cancer_type` reviewer flags a dropped exclusion. Defensive:
  `_merge_cell` cohort-wins preserves any trial-wide `NOT()` exclusion (the cohort's positive type wins, but a
  shared exclusion is never lost in the merge).
- **No over-enumeration of subsuming rows.** The extractor must not emit near-duplicate OR rows differing only
  by a trivial/subsuming variation of one criterion (e.g. one row adds `AND refractory to standard therapy`, a
  strict superset of another's prior_therapy — the stricter row is logically subsumed). By judgement it keeps
  the SINGLE version applicable to the majority of patients. The `prior_therapy` + `structural` reviewers flag
  it. (Distinct from the deterministic cross-product guard in §7.3: that kills *unsatisfiable* explosions; this
  removes *redundant-but-satisfiable* duplicates that no code rule can safely collapse.)

## 8. Mapping stage (Stage II)

Enriches the DNF rows. Every procedure is an LLM **mapper/curator → reviewer** (§2.5), grounded in the legacy
resources per the hold-out rule (§8.4).

### 8.1 cancer_type → OncoTree
Mapper → `oncotree_name` + `oncotree_code`, mapping tumour terms; grounded in the OncoTree ontology
(`tools/oncotree.py`). Only **three permitted non-OncoTree terms**: `Pan-cancer`, `solid tumour`,
`Haematological malignancy` (no `[None]` — a non-cancer value maps to empty). A deterministic validator
(`_oncotree_logic_problems`) rejects any `[None]`, and — per OR-alternative — `X AND X`, `X AND NOT(X)`, a broad
term ANDed with a specific code under it, and a subtype ANDed with its OncoTree parent (hierarchy from the CSV
levels via `is_subcode`). `NOT(cancer type)` is kept minimal. The reviewer audits the same.

### 8.2 gene_alteration / molecular_signature → finding-model syntax
Mapper → Hartwig finding-model syntax (`tools/finding_model.py` grammar): `SmallVariant[gene=… & …]`,
`GainDeletion[… & type=GAIN|HOM_DEL|HET_DEL]`, `Fusion[geneStart/geneEnd]`, `Disruption`, `Arm[…]`, `Wildtype`,
`MicrosatelliteStability[…]`, `homologousRecombination[…]`, `tumorMutationBurden/Load[…]`. A validator
(balanced brackets, known classes, gene-scoped `SmallVariant`, **no duplicate terms, no `X & NOT(X)`
self-contradiction**) runs before the reviewer. A `NOT()` qualified by something finding-model can't express
(e.g. an anatomic location) is **omitted** — never emitted as `NOT(same-variant)`.

**Diagnostic-category gene terms (2026-07-10):** `H3K27-altered` (the WHO category, also caused by
non-expressible EZHIP/EGFR mechanisms) maps **identically to `H3K27M`** — the K27M small-variant OR'd across the
canonical H3 genes `H3F3A | HIST1H3B | HIST1H3C`, using the **strict-HGVS coordinate `p.K28M`** (the initiator
Met is residue 1, so histone "K27" = protein K28). In a conjunction the whole H3 OR-block is parenthesised and
never dropped. Anchored by a note in the shared `GRAMMAR_REFERENCE` (seen by mapper **and** reviewer) + worked
examples in `_GENE_RULES`.

**Acceptable simplification (2026-07-10):** when the source carries a qualifier finding-model has **no field**
for — a copy-number **count/threshold** (`amplification with ≥5 copies` → just `type=GAIN`), a quantitative
level, a VAF threshold, an anatomic location, a tumour context — the mapper maps to the closest expressible term
and **drops** the qualifier. This loss of specificity is **correct, not a fault**: neither mapper nor reviewer
may flag a dropped unrepresentable qualifier.

### 8.3 Drug enrichment (trial-level, one web-search curator → reviewer)
Sources: CTGov `interventions[].name/otherNames/description`; ANZCTR `INTERVENTIONS` (RxNorm cross-check
**shelved**). One web-search curator produces, for the trial:
- `main_drugs` / `auxiliary_drugs` — the **investigational agent(s) under study by judgement** (NOT the whole
  regimen) vs comparators/backbone/supportive/placebo;
- `pottr_drug_class` — POTTR class hierarchy of the main drug(s); `drug_class` — general (non-POTTR) class (web search);
- `tga_status` / `pbs_status` — **per main drug**, `<drug>: Approved` / `<drug>: Not approved`;
- `tga_detail` / `pbs_detail` — per main drug, the **year + evidence + official source link** (tga.gov.au ARTG / pbs.gov.au).

### 8.4 Hold-out / anti-overfitting rule
Prompts teach **grammar/ontology + a few (~8–12) diverse examples only**. The hand-curated resources
(`ConditionsCurationResource`, `GeneAlterationCurationResource`, `MolecularSignatureCurationResource`, manual
overwrites) are **held-out verification data**, checked **manually** later (esp. `gene_alteration`) against the
legacy `eligibility_*_resource_*.tsv` — never ingested wholesale (which would degenerate into a mechanical vlookup).
The OncoTree ontology and finding-model grammar are the controlled *output vocabulary*, so they are fair to expose.

## 9. Output schema (21 columns)
`trialId, cohort, arm_type, cancer_type, oncotree_name, oncotree_code, gene_alteration,
gene_alteration_findingmodel, molecular_signature, molecular_signature_findingmodel, molecular_biomarker,
prior_therapy, drug, main_drugs, auxiliary_drugs, pottr_drug_class, drug_class, tga_status, pbs_status,
tga_detail, pbs_detail`.
`arm_type` + `drug` are per cohort; `main_drugs`/classes/regulatory are trial-level (repeated across the trial's rows).
`tga_status`/`pbs_status` are per main drug (`<drug>: Approved/Not approved`); `tga_detail`/`pbs_detail` carry the evidence (year + link).

## 10. Repo layout & retirement

```
aus_trial_universe/
  eligibility_path/  drug_utility_path/   # legacy — reference (resource source) until superseded
  agentic/
    run.py                               # pipeline orchestrator
    core/    client.py agent.py workflow.py pipeline_io.py
    tasks/   extraction/ (loaders,agents,schema,workflow)  mapping/ (agents,schema,workflow)
    tools/   oncotree.py  finding_model.py
    qa/      validate_output.py            # independent output validator (review of the reviewers)
```
Shared identity/reference code belongs in `agentic/tools/` (do not reintroduce the old drug_utility→eligibility
RxNorm coupling). `ui/` deleted. `actin_curator/`, `pydantic_curator/`, `trialcurator/`, `qa/` retire as v2
supersedes each (legacy still supplies resources + is imported by the old paths).

## 11. Verification
- **Code (orchestration/consolidate/validators):** unit-tested with fake clients — no API (64 tests).
- **Agents:** the schema contract is tested with fakes; live behaviour is verified by real runs on sample trials.
- **Independent output validator (`make agentic-validate`, `agentic/qa/validate_output.py`):** a deterministic
  *review of the reviewer agents* — runs OUTSIDE the workflow on a finished output TSV to catch what the in-loop
  reviewers let through (mapping degrades gracefully, so flagged values can still reach the output). Re-runs the
  pipeline's own OncoTree + finding-model validators on the final cells, plus cross-row DNF/cohort/exclusion
  checks nothing else does (unsatisfiable `A AND B` cancer_type, all-empty rows, exact-dup rows, in-cell
  `X AND NOT(X)`, prior_therapy subsuming-twin over-enumeration). **Testing-period QA step, not the production
  path;** always run it while iterating, and keep its checks in sync with the pipeline's validators.
- **Mapping accuracy:** **manual** hold-out comparison against the legacy trial-resource + curated resources
  (review sets: `data/agentic/analysis/complex_trials_ids.txt` + `typical_trials_ids.txt`). An automated
  run-comparison method is deferred (§12).

## 12. Open / deferred
- **Run-comparison method** — replacement for `qa/final_resource_diff.py`; how it treats residual LLM variance is TBD.
- **RxNorm cross-check** for ANZCTR drugs (currently LLM-only) — shelved; may return as a validation layer.
- **OncoTree granularity** — occasional over-strict "unfaithful" on subtype-heavy trials; tune against the manual review.
- **Stage-I ingestion** (download → drug-filter + POTTR-append → retire-missing) is not yet in agentic; the pipeline
  currently reads the versioned inputs the legacy path produces.

## 13. Non-goals
- No autonomous LLM orchestrator (pattern A). No `pydantic_curator`-style `.py` curation output.
- No preservation of the old `qa/` run-to-run diff. No new cross-path coupling.
