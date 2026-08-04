# Trial Curator v2 — Agentic Pipeline Spec

Single source of truth for the v2 agentic pipeline (`aus_trial_universe/`): why it exists, how it's
built, the field sources, and the output schema. For **how to run it**, see `docs/reference/combined_agentic_run.md`.

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
   full re-generation and can stall convergence on the hardest trials (see §13).
6. **Determinism via cache.** The response cache is the deterministic layer (identical request → identical output).
   `temperature`/`seed` are omitted by default (current reasoning models reject `temperature`).
7. **Model-agnostic.** One client; the model is configuration, swappable per agent.

## 3. Architecture

Two layers. The orchestrator is **code, not an LLM**.

|                | What it is | Decides what runs next? | Deterministic? |
|----------------|------------|-------------------------|----------------|
| **Orchestrator** (`run.py` + task workflows) | plain Python | **Yes** — fixed steps | Yes |
| **Agent** (a specialist) | one OpenAI call + pinned schema | No — does its one job | No (cache tightens) |

**Guiding principle — one agent per *independent* task.** Split by independence, not by field count:
- **Keep jointly-decided outputs in one agent.** A DNF row is a conjunction *across* columns (which cancer type
  co-occurs with which gene), so one **extractor** emits whole rows — splitting per column would lose the pairing.
- **Split genuinely-independent checks into separate agents.** Faithfulness of each column is independent, so
  **review** is a parallel panel of focused reviewers.
- **Deterministic work gets zero agents** (e.g. CTGov cohort/drug read straight from JSON).

## 4. Runtime — `core/` + `tools/`

- **`client.py` (`LlmClient`)** — the single door to OpenAI.
  - `.parse(schema, …)` → validated pydantic via `chat.completions.parse` (retries, response cache, tracing).
  - `.research(schema, …)` → same, but via the **Responses API `web_search` tool** for live-web research (TGA/PBS).
- **`agent.py` (`Agent`)** — `prompt + output schema + model (+ web_search flag)` bound to the client; `__call__`
  returns the validated object. `web_search=True` routes to `.research()`.
- **`workflow.py`** — generic `fan_out()` (parallel) + `refine()` (bounded check→repair loop; `max_attempts≈3`).
- **`tools/`** — reference data + validators agents lean on: `oncotree.py` (code vocab + validator),
  `finding_model.py` (grammar + syntax validator).

### 4.1 Response cache — determinism, provenance & pruning
One content-addressed `DiskCache` (`data/agentic/cache/`) is shared by **both** paths. The key is a SHA-256 of
the full request `{model, instructions (the prompt), input, schema, …}` (identical request → cache hit → no API
call); this **is** the determinism layer (principle #6) and the fast-re-run mechanism. The cache only grows
(content-addressed, no eviction), so changing a prompt/schema orphans its old entries. To make those prunable,
each entry is a self-describing envelope recording provenance — the agent `name` and `prompt_sha` (SHA-256 of
its `instructions`); legacy bare-JSON entries still read fine (treated as unknown). `core/prompt_registry.py`
enumerates the live agents offline (construction never calls the LLM), and `core/cache_prune.py`
(`make agentic-cache-prune`; auto-runs at the start of every run/build, `--no-cache-prune` to skip) deletes
entries whose prompt is **outdated** (agent's `prompt_sha` changed, or the agent was removed), keeping live and
(unless `--purge-unknown`) legacy entries. Corrections flow: fix the **prompt** (fingerprint changes → live
recompute + old entry auto-pruned) or hand-edit a **map table** (lookup-first honours it); hand-edits to the
extraction/regime tables do **not** survive a re-run of that trial. See `combined_agentic_run.md` §"Response
cache, corrections & pruning".

## 5. The pipeline (one command, one run directory)

`make agentic-run` runs, per trial, in **one streamed pass** (partial results survive an interrupt); one
timestamped run directory + one log per run. No intermediate files.

```
SELECT/ASSEMBLE ─▶ ARMS ─▶ EXTRACT ─▶ (rule-check + REVIEW panel) ─refine▶ MAP ─▶ DRUG ─▶ split → trial_arms + eligibility masters
   loaders        §7.3    §7 (LLM)         §7.5                      §8.1-2   §8.3     §9      (export = separate: make agentic-export)
```

Run modes: `ID=<id>` (one) · `IDS=<a,b,c>` (a set) · no arg = ALL trials. Options: `MODEL=` · `NO_JUDGE=1` ·
`NO_REVIEW=1` · `EXTRACT_ONLY=1` (skip map+drug). Output: the shared `trial_arms` registry + the 2 eligibility
content masters (+ vocab maps) in `data/agentic/eligibility/current_output/`, all keyed by `trial_arm_id`. The
matching-engine flat file (`export/trial_eligibility.tsv`) is built separately by `make agentic-export` (§9).

## 6. The relational data model

### 6.1 The drug-regime axis (locked 2026-07-13)
**The drug regime is the spine of the output** — the axis every row hangs off. A patient is matched to a
*treatment they could receive*; eligibility exists only to say *who qualifies for that treatment*. So
eligibility is **not a first-class entity** — it is **assigned to** a regime. An "eligibility cohort" (a text
label such as `Cohort 1A`) has no standing on its own; it matters only as a routing label naming *which
regime* those criteria attach to.

- **CTGov — the regime axis is given, deterministically**, by `armsInterventionsModule.armGroups`. Each
  drug-bearing armGroup = one regime (its `type` → `arm_type`; its interventions → the drug set). There are
  **exactly as many regimes as drug-bearing armGroups — the LLM never invents or drops one.**
- **ANZCTR — same principle, messier input.** No clean arm structure, so (a deliberate simplification)
  **every ANZCTR trial has a single eligibility cohort**: all eligibility is trial-wide. Regimes come from the
  drugs — `INTERVENTIONS` (experimental) and `COMPARATOR` (control), gated by `CONTROL` (Active → a real
  comparator regime; Placebo/Uncontrolled → none). The LLM's *only* ANZCTR job is to name the drugs; it does no
  cohort reasoning.
- **Arm identification is a path-neutral SHARED module (`tasks/shared`, 2026-07-25).** The SAME code identifies a
  trial's arms for BOTH paths (CTGov deterministic from armGroups; ANZCTR via the shared drug doer→reviewer,
  derived **fresh** per run, flag-independent + pinned to the shared cache). Arms are written once to the central
  **`trial_arms` registry** (`trial_arm_id, trialId, registry, arm, arm_type`; both registries); the drug
  `trial_to_intervention` and the eligibility content tables both link to it by `trial_arm_id`, so the
  `(trialId, arm)` split — the join key — is identical across the two paths by construction.

The normalized relations (the flat TSV is their **materialized join**):

| Relation | Key | Attributes | Derivation |
|---|---|---|---|
| **regime** (the axis) | (trialId, regime_id) | arm_type (flags control) | CTGov: `armGroups` filtered to `{Drug, Biological}` · ANZCTR: INTERVENTIONS/COMPARATOR |
| **regime_drug** | (trialId, regime_id, drug) | role: main (investigational) / auxiliary (backbone/SoC) | within-regime split, judged from title/description |
| **drug_annotations_core** (global)* | canonical drug | class / POTTR / modality / mechanism / ATC / FDA / EMA + **researched_on** | web search once per unique drug, **datestamped** (`tasks/drug_utility/`, `make drug-ref-build`); trial curation is then a LOOKUP (re-research only on `--refresh-drugs`). |

\* **Built standalone (2026-07-13; table 1 split into 3NF 2026-07-17; role table added 2026-07-28).** The drug
dimension is **six** 3NF tables in `aus_trial_universe/tasks/drug_utility/`:
- `intervention_to_canonical` (input intervention name → namespaced `canonical_id`(s): `rxcui:<n>` else `name:<x>`;
  a combination/regimen token splits into its component drugs, so **`1 input → N` canonicals** — a single engineered
  molecule like an ADC/bispecific stays one; carries `raw_name_to_map` = the input fragment each canonical came from),
- `trial_to_intervention` (`trial_arm_id` → input intervention name — the **provenance / traceability** record:
  which trial ARM used each name; links to the shared `trial_arms` registry (arm identity lives there once, keyed by
  the deterministic `trial_arm_id = {trialId}::{arm}` slug); deterministic, populated at collection time),
- `drug_annotations_core` (canonical → intrinsic facts), `drug_target_actions` (canonical → (target, action) pairs — the mechanism),
- `drug_regulatory_approvals` (canonical → TGA/PBS approval, **indication-specific**, verified against the live TGA/PBS sites),
- `trial_arm_drug_role` (**Phase 2**, 2026-07-28: `(trial_arm_id, canonical_id) → role` = `main` (investigational/
  defining) / `auxiliary` (backbone/SoC/comparator/placebo) — the within-arm role split #3 below, at canonical grain
  so it joins to `drug_regulatory_approvals` for per-main TGA/PBS; a cheap per-arm doer→reviewer classifier, no web search).

**Division of labour:** LLM doer→reviewer does the *judgement* (canonical identity, modality/target/class, approvals);
the *deterministic* facts — `rxcui` + `atc_code` (RxNorm RRF), `pottr_drug_class` (POTTR ontology walk), and the
trial→intervention provenance — are offline (`rxnorm.py` / `pottr.py` / collection), not LLM guesses.
Built via `make drug-ref-build DRUGS=.. | IDS=.. | ALL_TRIALS=1`; incremental + batched-with-checkpoint;
`--refresh-drugs` to re-research. The build **logs per-trial drug attribution** (traceability) and persists at
`<DATA_ROOT>/drug_annotations/current_version/` (6 TSVs; superseded builds under `archive/`). All data paths derive
from one relocatable `DATA_ROOT` (`core/paths.py`; = `data/agentic/` now → `data/` later). Reference data lives under
`resources/drug_utility/{pottr,rxnorm}/current_version/`; refresh POTTR with `make drug-ref-refresh-pottr`. Full
layout: `docs/reference/drug_ref_schema.md`.
*Deferred (user):* mapping each indication's free-text cancer/biomarker into the eligibility vocabulary
(OncoTree + finding-model) and joining `drug_annotations_core` back into `combined` — done *after* the standalone tables.
| **eligibility** (assigned to a regime) | (trialId, regime_id, conj_id) | 5 eligibility columns (+prov, inline NOT()) | LLM extract; trial-wide by default |

**Row grain = (trialId, regime_id, conj_id).** For a given `(trialId, regime_id)` the drug columns are
constant — a functional dependency, by design (not an accident). Mapping/annotation/enrichment are *just more
columns* hanging off this grain (§8) — keyed by the source cell (mapping) or by `drug` (drug_annotations_core).

**Output = a shared arm registry + eligibility content masters + a combined view (locked 2026-07-13; decoupled
2026-07-21; arm registry extracted 2026-07-25).** The arm spine lives once in the **shared `trial_arms` registry**
(`trial_arms/current_version/trial_arms.tsv`: `trial_arm_id, trialId, registry, arm, arm_type`; both registries),
written by whichever path processes a trial. Each run writes eligibility's **2 content masters** to
`eligibility/current_output/`, each keyed by `trial_arm_id` — `arm_eligibility_raw.tsv` (`trial_arm_id → 5 verbatim
raw cells`) and `interpreted_eligibility.tsv` (`(trial_arm_id, conj_id) → 5 interpreted cells`) — plus the three
`*_map.tsv` vocab lookups, and `combined.tsv` (their materialized join with `trial_arms` + drug annotations on
`trial_arm_id`; the flat rows the matching engine reads), written **outside the store** at
`eligibility/combined/combined.tsv` (§9). `drug_annotations_core` is a *further* separate, **persisted, datestamped**
table (global drug facts, built once per unique drug, looked up by name — the drug-stage throughput win); it is
**built standalone**, and only its *join* into `combined` remains deferred. Full column lists in §9.

**Locked design decisions (2026-07-13) — do not re-litigate:**
1. **Regime membership** = any armGroup with ≥1 pharmacological agent `{Drug, Biological}`; exclude
   placebo-only / pure-radiation / procedure-only arms (no drug ⇒ not a drug regime).
2. `arm_type` is carried onto every regime and **flags control** arms (`ACTIVE_COMPARATOR`, `PLACEBO_COMPARATOR`).
3. **main vs auxiliary is a WITHIN-regime split** — main = the investigational/defining agent(s) (judged from
   title / description / etc.), auxiliary = backbone/SoC in the *same* regime. Not a cross-regime notion.
4. A comparator armGroup carrying a real drug is **its own regime** (its `arm_type` marks it control).
5. **Eligibility→regime assignment — the make-or-break rule.** Trial-wide by default (applies to every regime);
   assign a criterion to a specific regime ONLY when the text clearly ties it there; **DROP** eligibility for
   text-cohorts that correspond to NO regime (closed/retired cohorts). *If this is wrong, everything is wrong.*
6. CTGov and ANZCTR **share one data structure**; ANZCTR is the single-eligibility-cohort special case.
7. Drug enrichment is **per regime**, not per trial (fixes the bug where one trial-level curation was stamped
   onto every row regardless of arm).
8. Drug identity = raw/normalized name for now; **RxNorm matching deferred**.

### 6.2 The DNF within a regime
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
| `armsInterventionsModule.armGroups[]` | `.label`, `.type`, `.interventionNames`, **`.description`** — the regime/arm structure (description aids eligibility→regime assignment) |
| `eligibilityModule.sex/minimumAge/…`, `designModule.phases/studyType`, `statusModule.overallStatus`, `contactsLocationsModule` | out of extraction scope (gates / housekeeping) |

**ANZCTR** (`anzctr_field_extractions.csv`): `STUDY TITLE`, `SCIENTIFIC TITLE`, `HEALTH CONDITION`, `INTERVENTIONS`,
`INCLUSIVE CRITERIA` → INCLUSION CRITERIA, `EXCLUSIVE CRITERIA` → EXCLUSION CRITERIA, **`COMPARATOR`** (comparator-arm
drugs) + **`CONTROL`** (Active/Placebo/Uncontrolled/… — the deterministic signal for whether a comparator regime
exists). (`ACTRN` stored as bare digits; display id is `ACTRN`-prefixed. Age/phase/status/geography = gates/housekeeping.)

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

### 7.3 Cohorts = drug regimes (see §6.1)
A cohort **is** a drug regime; the regime is the fixed axis and eligibility is assigned to it.
- **CTGov:** deterministic from `armGroups` — one regime per **drug-bearing** armGroup, filtered to
  pharmacological agents `{Drug, Biological}` (a `Drug:`-only filter would drop the many investigational
  *biologicals*, e.g. `NCT07099898`'s experimental arm *Ris-Rez*). Placebo-only / pure-radiation / procedure-only
  arms carry no drug and are **dropped**. Each regime carries `label`, `type` → `arm_type` (flags control),
  `interventionNames` → drug set, **and `armGroups[].description`** (the best signal for eligibility→regime
  assignment — e.g. *"Cohort 5 … targeted agents selected on molecular data"*).
- **ANZCTR:** **single eligibility cohort** (all criteria trial-wide — no cohort-detection agent); regimes come
  from the drugs — an LLM drug agent names `INTERVENTIONS` (experimental) and `COMPARATOR` (control) drugs, gated
  by `CONTROL` (Active → a comparator regime; Placebo/Uncontrolled → none).

The extractor is **regime-aware**: it assigns each criterion to a specific regime or "trial-wide"; a regime's
effective eligibility = `trial-wide ∧ regime-specific` (cross-product distribution), so each output row is
self-contained with its own drug.

**Scope-assignment contract (2026-07-10, extended 2026-07-13 — the make-or-break rule, §6.1 #5).** The COHORTS
list is a **fixed, known set of regimes** (CTGov: from `armGroups`); the extractor **assigns**, never invents:
- assign each criterion to exactly ONE scope — never state the same criterion in two scopes;
- `trial-wide` (the DEFAULT) = shared identically by ALL regimes, stated once; a regime id = defining/specific/
  varying for that regime (a subset-shared criterion is emitted once per applicable regime);
- **DROP** eligibility for text-cohorts that correspond to NO regime in the list (closed/retired cohorts) — e.g.
  `NCT05009992`'s eligibility text spells out `COHORT 1A/1B, 2A/2B, 3A/3B` all marked CLOSED, none of which is an
  armGroup; those criteria are discarded, only regimes 4/5/6 are populated.

This also prevents the cross-product **blow-up** where a criterion duplicated across scopes multiplies out: e.g.
`NCT04221035` (SIOPEN HR-NBL2) restated its neuroblastoma staging in both trial-wide (17 OR-rows) and each cohort
→ `17 × 40 = 680` rows, 86% of them unsatisfiable `Stage A AND Stage B` conjunctions. Deterministic
**distribution safety net**: (1) single-valued axes (`cancer_type`) are never ANDed across scopes — the **regime
value wins** (`_merge_cell`); (2) exact-duplicate rows are **de-duplicated** after merge; (3) a
`trial-wide × regime-specific` product beyond a threshold logs a `WARN` (never silently emitted). The
`structural` reviewer audits the assignment (right scope, closed-cohort criteria dropped, no cross-scope
duplication) so refine can fix it upstream.

### 7.4 Provenance vocabulary (the `[source]` tags)
- **CTGov:** `TITLE`, `OFFICIAL TITLE`, `CONDITIONS`, `KEYWORDS`, `BRIEF SUMMARY`, `DETAILED DESCRIPTION`, `ELIGIBILITY CRITERIA`, `INTERVENTIONS MODULE`.
- **ANZCTR:** `STUDY TITLE`, `SCIENTIFIC TITLE`, `HEALTH CONDITION`, `INCLUSION CRITERIA`, `EXCLUSION CRITERIA`, `INTERVENTIONS`, `COMPARATOR` (comparator-regime drug source).

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

## 9. Output — shared arm registry + eligibility content masters + the matching-engine export (§6.1; decoupled 2026-07-21; arm registry extracted 2026-07-25; export 2026-07-28)
Arm identity lives once in the **shared `trial_arms` registry** `data/agentic/trial_arms/current_version/trial_arms.tsv`
— `trial_arm_id, trialId, registry, arm, arm_type` (both registries; `trial_arm_id` = the deterministic
`{trialId}::{arm}` slug), written by whichever path processes a trial. The eligibility store
`data/agentic/masters/eligibility/current_version/` holds its **2 content masters + 3 Step-1 map tables + 3
finalised (Step-2) map tables + `arm_scope` (§12.1) = 9 pure-3NF tables**, each keyed by
`trial_arm_id` (re-running a trial replaces its rows; superseded stores → `archive/`). Eligibility masters hold
**NO drug facts** — drugs join in via `trial_arm_id` to the drug utility path (§6.1):

- **`trial_arms.tsv`** (SHARED registry) — PK `trial_arm_id` → `trialId, registry, arm, arm_type`. The arm spine;
  the single join key both paths link to.
- **`arm_eligibility_raw.tsv`** — PK `trial_arm_id` → the 5 VERBATIM raw cells (`cancer_type, gene_alteration,
  molecular_signature, molecular_biomarker, prior_therapy`; each with inline `[source]`, `|`-delimited; trial-wide
  criteria replicated onto each arm). The audit anchor.
- **`interpreted_eligibility.tsv`** — PK `(trial_arm_id, conj_id)` → the 5 interpreted DNF cells (inline `NOT()`;
  rows sharing `trial_arm_id` are ORed). `conj_id` numbers the OR-conjunctions within an arm.
- **`cancer_type_map.tsv`** — `cancer_type` value → `oncotree_name, oncotree_code` (deduped, universe-wide cache);
  Step-2 adds **`finalised_cancer_type_map.tsv`** (+ an `oncotree_code_FINAL` col). Analogous `finalised_*` for gene/signature.
- **`gene_alteration_map.tsv`** / **`molecular_signature_map.tsv`** — value → `finding_model` (deduped).

**The matching-engine EXPORT (2026-07-28)** is built SEPARATELY by `export.py` (`make agentic-export`), not by
`run.py`. TWO sets: **Set A** = the denormalized flat **`data/agentic/export/trial_eligibility.tsv`** (38 cols, one
row per `(trial_arm_id, conj_id)`) = a new deterministic **`trial_info`** master (raw CTGov/ANZCTR basic info) ⋈
interpreted eligibility ⋈ **FINAL** vocab maps ⋈ per-arm intervention rollups (role-split main/aux from
`trial_arm_drug_role`); **Set B** = the 6 drug 3NF tables **referenced in place** (a `MANIFEST.md` points at
`drug_annotations/current_version/` — no duplicate). `SNAPSHOT=1` mints an immutable `export/snapshot_<ts>/` bundle
(Set A + a frozen copy of Set B). The old parked `combined.tsv` / `run._build_combined` were RETIRED.

The raw text (grain: arm) and the interpreted conjunctions (grain: arm × conjunction) are separate 3NF entities;
trial-wide criteria are AND-combined into each arm by `_distribute` upstream. **Owed** (top follow-up): drug-side
`drug_regulatory_approvals` free-text cancer_type/biomarker mapped into the same vocab, so TGA/PBS
(indication-specific) matches trial-eligibility ↔ drug-approval symmetrically.

## 10. Stage-I ingestion + periodic refresh (self-contained, 2026-07-29)

The pipeline now **downloads and versions its own trial universe** — no legacy/external step. Ingestion lives
in-package at `aus_trial_universe/tasks/ingestion/` and produces the versioned inputs the rest of the pipeline reads.

**The periodic refresh is 9 stages** (`make agentic-refresh`): ingest → expire/restore → curate new trials →
Step-2 reconcile → drug utility → approval vocab → **scope verdicts** (§12.1) → export → **gates** (§12.0).
A gate FAIL makes the whole refresh exit non-zero.

**Ingestion** (`make agentic-ingest REGISTRY=ctgov|anzctr|all`, module `aus_trial_universe.ingest`, default `all`).
Each run does a FULL download, archives the previous input set (`current_version/` → `archive/<label>/`, retaining
the newest **5**; a label already taken by an earlier run on the same day falls through to `_2`, `_3`, …), and writes
the fresh one to `data/agentic/inputs/trial_universe/<registry>/current_version/` — input versioning now mirrors the
masters (`current_version/` + `archive/`).
- **`ctgov.py`** — paged CT.gov **API-v2** download with Essie filters: recruitment status
  `RECRUITING / NOT_YET_RECRUITING / ACTIVE_NOT_RECRUITING / ENROLLING_BY_INVITATION`, country Australia / New
  Zealand, a `DRUG` intervention + `Neoplasms` condition core, plus a POTTR broad-net query and an explicit
  id-append so every POTTR trial is included.
- **`anzctr.py`** — a **curl_cffi** Cloudflare bypass + DevExpress `all.xls` download, filtered LOCALLY to the same
  cohort → a 24-column CSV; POTTR trials **bypass the filter** so they always survive.
- **`pottr_ids.py`** — best-effort GitHub loaders for the POTTR trial-id + alias lists, plus a manual removal list.

**Recoverable expiry** (`expiry.py`; standalone `python -m aus_trial_universe.tasks.ingestion.expiry [--apply]`).
After a refresh, `kept = fresh-download ∪ POTTR` and `expired = stored − kept`. Expired trials are **MOVED** (not
deleted) to a recoverable `masters/{eligibility,trial_arms}/expired/` area, and **RESTORED** if they reappear in a
later download. **POTTR trials NEVER expire.** A `max_fraction=0.5` guard aborts if a run would expire more than
half the universe, and a POTTR-fetch precondition must succeed first; `--dry-run` previews.

**Periodic refresh** (`make agentic-refresh`, module `aus_trial_universe.refresh`) chains the whole loop: ingest all
→ expire/restore → `run --resume --skip-drug` (eligibility extract+map) → `run --reconcile` (Step-2) →
`drug-ref-build --from-trials <new/restored>` (drug facts + occurrences + main/aux roles; existing drugs are pure
lookups, no web search) → `map_approvals` → `export`. **Incremental by construction:** unchanged trials re-hit the
response cache (no API) and known drugs skip the web-search step. Latest acceptance run (2026-07-29): expired 32,
curated 71 new trials, drug researched=34 / reused=73, export 2,020 trials, arm-consistency CONSISTENT, 0 failures;
179 unit tests green.

The package was also **flattened**: `aus_trial_universe/agentic/*` moved up to `aus_trial_universe/*` (no more
`agentic/` layer — imports are now `aus_trial_universe.run` / `aus_trial_universe.tasks.…`; the DATA root stays
`data/agentic/`), and the legacy `eligibility_path/` / `drug_utility_path/` / `trials_to_remove` were DELETED with
the RxNorm coupling severed (§11).

## 11. Repo layout & retirement

The package was **flattened (2026-07-29)** — `agentic/*` moved up to `aus_trial_universe/*`, so there is no
`agentic/` layer (imports are `aus_trial_universe.run`, `aus_trial_universe.tasks.…`; data root stays `data/agentic/`):

```
aus_trial_universe/
  run.py                                 # ELIGIBILITY orchestrator (extract → map → drug top-up); export is separate
  ingest.py  refresh.py                  # Stage-I ingestion CLI (§10) + end-to-end periodic refresh loop
  export.py  trial_info.py               # matching-engine export (Set A trial_eligibility.tsv) + trial_info master
  core/    client.py agent.py workflow.py pipeline_io.py paths.py cache_prune.py prompt_registry.py logfmt.py
  tasks/
    ingestion/     ctgov.py anzctr.py pottr_ids.py expiry.py   # self-contained download+filter+POTTR+expiry (§10)
    shared/        cohorts.py agents.py schema.py store.py  # PATH-NEUTRAL arm identification: Cohort,
    #                trial_arm_id() slug, ANZCTR drug agents, anzctr_regimes, TrialArm + TrialArmStore
    eligibility/   extraction/ (loaders,agents,schema,workflow)  mapping/ (agents,schema,workflow)
    #              tools/ (oncotree.py finding_model.py)  qa/ (validate_output.py, arm_consistency.py)
    drug_utility/  schema.py store.py rxnorm.py pottr.py agents.py workflow.py build.py migrate_trial_arms.py
```
Arm identification is the ONE thing genuinely shared by both paths → it lives in `tasks/shared/` (both paths import
it; do NOT reintroduce a backwards drug→eligibility import). The legacy `eligibility_path/`, `drug_utility_path/`,
and `trials_to_remove` were **DELETED** and the old drug_utility→eligibility RxNorm coupling severed (ANZCTR arm
drugs are now re-derived by the shared LLM cohort step, §6.1). `ui/` deleted; `actin_curator/`, `pydantic_curator/`,
`trialcurator/`, `qa/` retire as v2 supersedes each.

## 12. Verification

### 12.0 Unattended operation — the pipeline verifies ITSELF (added 2026-07-29, session 6)
In production the refresh runs on a schedule with no one reading the log, and nothing downstream can tell a good
cycle from a bad one by looking at the output — a partially-curated store still yields a plausible export. Three
mechanisms close that:

1. **Fail-loud exit codes.** `run.py` returns rc=3 when requested trials are still missing; `refresh.py` propagates
   a non-zero exit on **any gate FAIL** (it previously returned 0 unconditionally, which made a broken cycle
   indistinguishable from a clean one); `scripts/agentic/pipeline.sh` runs `set -euo pipefail` so the code survives
   the `tee` and reaches the scheduler.
2. **`qa/gates.py` — the deterministic trust decision** (refresh stage 9/9; also `make agentic-gates`). No LLM, no
   network: the same bytes always give the same verdict. PASS / WARN / FAIL per check, FAIL = "do not trust this
   cycle". Checks: FK integrity · expiry completeness (no orphans in ANY master; `trial_info` tracks the registry) ·
   additive safety (reference tables never shrink; trial tables only as expiry explains; plus the exact identity
   `after == before − expired + curated`) · curation completeness · **empty-output reasons** (§12.1) · export
   integrity · `output_validator` (WARN) · `waived_findings` (WARN) · universe swing (WARN) · expiry-guard trip.
   **Known-accepted findings are waived** in `qa/waivers.py` — a waiver removes the FAIL but keeps a standing WARN
   and must name its follow-up, because a gate that fails every cycle for a known reason gets ignored.
3. **A durable per-cycle record.** `run_report/refresh_<ts>.md` (keep 5) + a machine-readable `STATUS.json` for an
   external monitor. Masters are mutated in place and the export overwritten, so without this a completed cycle
   left no trace but its log.

### 12.1 Why an arm is EMPTY — the `arm_scope` table
An arm whose interpreted DNF is empty contributes no export row, so a trial can be fully curated and still be absent
from the deliverable — and **"correctly out of scope" is indistinguishable from "extraction missed it"** without
reading the source. Unattended, nobody reads it, so a genuine miss would only nudge a row count. `arm_scope` records
a verdict per empty arm: `healthy_volunteers` · `not_oncology` · `population_not_cancer_selective` ·
`no_eligibility_text` · **`unexplained`** (the miss signal the gate fails on). Deterministic rules first (CTGov's
structured `eligibilityModule.healthyVolunteers`; tight healthy-volunteer text patterns), LLM verdict only for the
residue. **Deliberately NOT a deterministic excuse: "the arm's raw row is empty"** — that restates the problem
rather than explaining it, and while it was a rule it auto-absolved 7 arms of which 2 were real misses.

### 12.2 Test + validator layers
- **Code (orchestration/consolidate/validators/gates):** unit-tested with fake clients — no API (**212 tests**).
  The gate tests assert the FAILURE directions specifically: a gate that cannot fail is decoration.
- **Agents:** the schema contract is tested with fakes; live behaviour is verified by real runs on sample trials.
- **Independent output validator (`make agentic-validate`, `tasks/eligibility/qa/validate_output.py`):** a deterministic
  *review of the reviewer agents* — runs OUTSIDE the workflow on a finished output TSV to catch what the in-loop
  reviewers let through (mapping degrades gracefully, so flagged values can still reach the output). Re-runs the
  pipeline's own OncoTree + finding-model validators on the final cells, plus cross-row DNF/cohort/exclusion
  checks nothing else does (unsatisfiable `A AND B` cancer_type, all-empty rows, exact-dup rows, in-cell
  `X AND NOT(X)`, prior_therapy subsuming-twin over-enumeration). **Testing-period QA step, not the production
  path;** always run it while iterating, and keep its checks in sync with the pipeline's validators.
- **Mapping accuracy:** **manual** hold-out comparison against the legacy trial-resource + curated resources
  (review sets: `data/agentic/analysis/complex_trials_ids.txt` + `typical_trials_ids.txt`). An automated
  run-comparison method is deferred (§13).

## 13. Open / deferred
> The authoritative, prioritised to-do list lives in `docs/v2_agentic_handover.md` ("⏭ RESUME AT"). Highlights:
> OncoTree code-field defects reported by the matching engine (leaked NAMES — root-caused to a hole in
> `invalid_codes`, which only checks ALL-CAPS tokens; multiple un-factored `NOT()` clauses; nested negation) ·
> 5 waived extraction misses · cross-checks against the v1 output and POTTR/registry ground truth · a
> mapped-value-deduplicated joined table · a deterministic DNF-violation detector + LLM repair.
- **Run-comparison method** — replacement for `qa/final_resource_diff.py`; how it treats residual LLM variance is TBD.
- **RxNorm cross-check** for ANZCTR drugs (currently LLM-only) — shelved; may return as a validation layer.
- **OncoTree granularity** — occasional over-strict "unfaithful" on subtype-heavy trials; tune against the manual review.

## 14. Non-goals
- No autonomous LLM orchestrator (pattern A). No `pydantic_curator`-style `.py` curation output.
- No preservation of the old `qa/` run-to-run diff. No new cross-path coupling.
