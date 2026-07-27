# v2 — Mapping Stage (Step 1) + Shared Loop Harness — Plan

*Status: DESIGN LOCKED (2026-07-27), pre-implementation. This is the spec we agreed before coding.*
*Branch: `AUS-328-Aus-trial-universe-v2`. Extends `docs/v2_agentic_pipeline_spec.md` §8 (mapping) and §5 (workflow).*

## 0. Objective

Two coupled pieces of work, agreed in the 2026-07-27 design discussion:

1. **Shared loop harness** — lift the duplicated *doer→reviewer refine wiring* out of each stage into ONE
   shared `core/` driver, and put extraction, mapping, and drug all on it. No bespoke per-stage loop designs.
2. **Mapping Stage (Step 1)** — run the (now-upgraded) mapping over the frozen extract store to build the three
   value→vocabulary map tables (`cancer_type→OncoTree`, `gene_alteration→finding-model`,
   `molecular_signature→finding-model`), as correct as we can get them from the doer/reviewer alone.

The extraction + interpretation output is **frozen and unchanged** — we map from it, we do not re-derive it.

## 1. Locked decisions (this discussion)

| # | Decision |
|---|----------|
| D1 | **Map-only pass** over the frozen `interpreted_eligibility.tsv`; the two content tables are never touched. |
| D2 | **Step 1 of a multi-step mapping.** Per-distinct-value only. A later **Step 2** reconciles across values (e.g. `LUNG` + `NSCLC` → `NSCLC`). No cross-value/cross-row reconciliation here. |
| D3 | **Map everything anew.** Old cache/map tables do not apply (the prompt changes below invalidate them anyway). |
| D4 | **Shared loop mechanism across ALL stages.** One `review_refine` driver + one `ReviewVerdict` schema. The only stage-specific exception is drug enrichment's *doer* being a `web_search` (Responses API) call — that's the agent call type, not the loop. |
| D5 | **Unify all at once** — build the driver, refactor extraction + mapping + drug onto it in one behavior-preserving pass; verify extraction output is byte-identical. |
| D6 | **Few-shot = static hand-picked subset** from the curated resource files (~8–12 per mapper), baked into the prompt modules. The rest of each resource stays held out. |
| D7 | **Reviewer gets its own examples**, disjoint from the doer's, **including counter-examples** (wrong-mapping → correction). Reviewers do discrimination, not generation. |
| D8 | **OncoTree reviewer must be vocab-grounded** (it currently is not) so it can validate codes AND check granularity. |
| D9 | **`""` is a genuine last resort.** Return empty ONLY when the value is truly outside the domain; difficulty is never a reason to bail. Reviewer flags a *lazy* empty; still accepts legitimately-empty values. |
| D10 | **OncoTree granularity:** map to the *most granular* node that still fully and correctly covers the stated type — no broader, no narrower. |
| D11 | **QA is the responsibility of the doer prompts + reviewer specs, not a manual step.** Production has no human gate. The comparison report (§4) is a *testing instrument* to expose weak prompts/specs so we harden them. |

## 2. Part 1 — the shared loop harness

### 2.1 What is already shared vs. bespoke
- **Already shared** (`core/workflow.py`): `refine()` (best-of-attempts, cycle detection, optional `stuck_repair`),
  `fan_out()`, `run_parallel()`. Every stage already calls the same `refine()`.
- **Bespoke / duplicated today** (re-implemented in extraction, mapping, drug): the verdict schema, REVISION MODE
  (hand the doer its prior answer + only the flagged issues), ESCALATION MODE (last-resort re-review that asks the
  reviewer to fill `suggested_fix`), and the doer/reviewer logging convention. Divergence already bit: mapping's
  verdict has no `suggested_fix` and no escalation at all.

### 2.2 The driver (`core/review.py` — BUILT + verified in M1)
A thin driver that owns the loop mechanism; stages supply two callables with a **uniform signature**.

```
review_refine(produce, check, *, max_attempts=6, escalate=False) -> RefineResult:
    # produce(feedback: str = "", prior=None) -> candidate
    #     stage-owned; owns its doer prompt + how it renders the prior answer for REVISION MODE
    # check(candidate, escalate: bool = False) -> CheckResult
    #     stage-owned; owns its deterministic validators + reviewer(s)/panel; when escalate=True it re-reviews
    #     asking for suggested_fix and folds those into problems
    #
    # the driver owns (once, for everyone):
    #   repair       = lambda prev, probs: produce(feedback=fmt(probs), prior=prev)
    #   stuck_repair = lambda prev, probs: produce(feedback=fmt(check(prev, escalate=True).problems or probs), prior=prev)
    #   the refine() call, the last-resort banner, the '- '-bulleted feedback. Wired only when escalate=True.
```

- **DESIGN REFINEMENT (M1) — the driver is VERDICT-AGNOSTIC; there is NO shared `ReviewVerdict` BaseModel.**
  Reason: `core/client._fingerprint` hashes `output_schema.model_json_schema()` (which embeds the class name as its
  `title`) into the response-cache key. Renaming/merging a reviewer schema (`JudgeVerdict` → a shared `ReviewVerdict`)
  would change the cache keys of the **signed-off** extraction/drug reviewers → live recompute → non-identical output.
  So each stage keeps its own reviewer output schema; the driver only ever sees `produce`/`check`/`CheckResult`, and
  the stage's `check` adapts its verdict (faithful/problems[/suggested_fix]) into a `CheckResult`. **This still fully
  unifies the loop MECHANISM — which is what the directive asked for.** A stage whose reviewer has no `suggested_fix`
  channel passes `escalate=False` (plain critique-only, exactly as before).
- Everything genuinely stage-specific stays in the stage: extraction's 5-reviewer panel + enumeration-aggregate
  view; mapping's single vocab-grounded reviewer; the deterministic validators (`_oncotree_logic_problems`,
  `finding_model_problems`, extraction `_rule_problems`); the input renderers.
- `DEFAULT_MAX_ATTEMPTS=6` is the driver fallback, but **each call site keeps passing its existing cap explicitly**
  (drug/arm-ID = 3, extraction = 6 via `run.py`) — so no stage's attempt budget changed in M1 (behavior-preserving).
  Harmonising everything to 6 is a deliberate later choice, not a side effect.

### 2.3 Refactor targets — ALL DONE in M1 (behavior-preserving)
- `core/review.py` — NEW: `review_refine` (verdict-agnostic; no shared `ReviewVerdict`). + `tests/agentic/core/test_review.py`.
- `tasks/eligibility/extraction/workflow.py` — `_extract_raw`, `_interpret` now call `review_refine`; local
  `repair`/`stuck_repair` glue dropped; produce/check closures (prompts, panel, escalation text) unchanged.
- `tasks/drug_utility/workflow.py` — `canonicalize`/`annotate`/`approvals` on `review_refine` (escalate=False).
- `tasks/shared/cohorts.py` — `extract_anzctr_drugs` (ANZCTR arm-ID) on `review_refine` (escalate=False).
- Mapping (`tasks/eligibility/mapping/*`) still uses the old `refine` — it's rebuilt on the driver in **M2**.
- **Verification:** `make agentic-tests` → **138 passed** (134 + 4 driver tests). Extraction **byte-identical** on a
  5-trial sample (incl. `NCT04221035`/`NCT05914116` at faithful=False/6 attempts — the escalation path — and an
  ANZCTR fresh-derived-arms trial), compared against the frozen `current_output/` with the warm cache.
- `tasks/drug_utility/workflow.py`/`agents.py` — doer→reviewer onto the driver; the `web_search` doer stays a
  different agent call but uses the same loop.
- `tests/agentic/*` — new driver tests; existing stage tests updated to the shared verdict.

### 2.4 Behavior-preservation guarantee (extraction is signed-off)
No prompt, escalation text, revision-table rendering, or input string changes → the DiskCache keys
`(agent name, prompt_sha, input)` are unchanged → identical cached responses → **byte-identical extraction
output**. Verified by (a) `make agentic-tests` green (134), and (b) a **sample-trial diff**: re-run a handful of
representative trials (incl. a hard multi-cohort one) with the cache warm and confirm `arm_eligibility_raw` +
`interpreted_eligibility` are identical to the frozen `current_output/`.

## 3. Part 2 — Mapping Stage (Step 1)

### 3.1 Run mechanism (D1, D3)
A **`--map-only`** mode on `run.py` (keeps `make` working; no new entry point):
1. Load `EligStore` from `current_output/` (has `interpreted_eligibility` for all trials).
2. Collect the **distinct provenance-stripped values** per mapped column across the whole store.
3. Map each with the upgraded mapper→reviewer (fresh — do not lean on any pre-existing map table).
4. Write ONLY the three map tables into `current_output/`; **never call `set_trial`** (raw + interpreted untouched).
5. Do **not** build `combined.tsv` (the join is milestone #3; needs drug role too). Out of scope here.

Workload (measured on the frozen store): **cancer_type 4,853 · gene_alteration 859 · molecular_signature 167**
distinct values (~5,880 total). Concurrency sized from the RPM/TPM probe (memory `feedback-max-allowable-concurrency`).

### 3.2 The three mappers (targets unchanged; internals upgraded)
| Column | Target | Grounding | Deterministic validator |
|--------|--------|-----------|-------------------------|
| `cancer_type` | OncoTree `Name (CODE)` (+3 sentinels) | 865-code vocab | `invalid_codes` + `_oncotree_logic_problems` |
| `gene_alteration` | finding-model syntax | grammar | `finding_model_problems` |
| `molecular_signature` | finding-model (6 signature terms) | grammar | `finding_model_problems` |

`molecular_biomarker` and `prior_therapy` stay **free-text — no mapper** (locked).

### 3.3 Prompt upgrades (the accuracy levers)
- **Static hand-picked few-shot (D6)** from the curated resources, baked into the prompt modules:
  - Conditions → OncoTree: format is already `Name (CODE)`, `|` = OR (e.g. `Acute Myeloid Leukemia (AML) | Myelodysplastic Syndromes (MDS)`); `[None]` → `""`.
  - GeneAlteration → `Mapping_args` (e.g. `Arm[chromosome=1 & arm=p & type=ARM_LOSS]`); include non-obvious ones.
  - MolecularSignature → `Findings_curation` (e.g. `dMMR → MSI`, `FH deficient`/`SDH deficient → HR_DEFICIENT`).
- **Reviewer examples (D7)** — a *disjoint* set that includes **counter-examples**, e.g.:
  - oncotree: `"non-small cell lung cancer"` → proposed `Lung (LUNG)` → **FAIL** *under-granular; NSCLC exists* → fix `Non-Small Cell Lung Cancer (NSCLC)`.
  - gene: `"EGFR exon 20 insertion"` → proposed `SmallVariant[gene=EGFR]` → **FAIL** *lost exon+insertion* → fix `SmallVariant[gene=EGFR & transcriptImpact.affectedExon=20 & transcriptImpact.effects=INFRAME_INSERTION]`.
  - lazy-empty: `"cholangiocarcinoma"` → proposed `""` → **FAIL** *bailed though CHOL exists* → fix `Cholangiocarcinoma (CHOL)`.
  - legit-empty (accept): `"adverse biology"` → `""` → **PASS**.
- **Vocab-ground the OncoTree reviewer (D8)** — attach `vocab_reference()` so it can validate codes and assess
  granularity. (Gene/signature reviewers already carry the grammar.)
- **Granularity rule (D10)** — doer instruction + examples: most granular node that still fully covers
  (`non-small cell lung cancer → NSCLC` not `LUNG`; `lung adenocarcinoma → LUAD`; but `Eyelid SCC → Skin (SKIN)`
  when OncoTree has no finer node). Reviewer checks "is a more specific valid node available that still matches?".
- **Anti-lazy-`""` (D9)** — doer: exhaust the vocabulary (a real cancer always has at least a sentinel) before
  returning empty. Reviewer flags an empty where a valid mapping exists; accepts genuinely out-of-domain empties.

### 3.4 Loop (D4/D5)
All three mappers run on the shared `review_refine` driver: unified `ReviewVerdict` (now with `suggested_fix`),
REVISION MODE (doer sees its prior mapping + only the flaw), ESCALATION MODE last resort, `max_attempts=6`,
best-of + cycle detection inherited.

## 4. Part 3 — the QA comparison instrument (testing tool, D11)

Standalone script (like `qa/validate_output.py`), e.g. `qa/compare_mapping.py` + `make agentic-mapping-compare`.
Its role is to **drive prompt/spec improvement during testing**, not to gate production.
- For each mapped value, join to the curated resource answer for the matching key; bucket **agree / disagree /
  novel / unmatched**. `gene_alteration` prioritised.
- Key matching needs a **normalisation layer** (curated keys are raw condition strings; ours are interpreted
  values) — exact-normalised first, leave the rest in `unmatched`. Reconcile legacy `[None]` → `""`.
- The held-out resources are read ONLY here — never fed to the mappers beyond the hand-picked few-shot.
- Loop: report → find weak patterns → harden doer prompt / reviewer spec → re-map → re-report.

## 5. Sequencing / milestones
1. **M1 — shared harness. ✅ DONE (2026-07-27).** `core/review.py` (`review_refine`, verdict-agnostic — see §2.2 for
   why there's no shared `ReviewVerdict`); extraction + drug + shared ANZCTR arm-ID refactored onto it. Verified:
   `make agentic-tests` 138 green (134 + 4 driver tests) + extraction byte-identical on a 5-trial sample.
2. **M2 — mapping on the harness.** Rebuild the mapping workflow on `review_refine`; apply the §3.3 prompt
   upgrades; add the `--map-only` run mode. Unit tests (fake client).
3. **M3 — QA instrument.** `qa/compare_mapping.py` + make target + tests.
4. **M4 — live build + iterate.** Run `--map-only` over the frozen store (cost-aware, concurrency from the probe),
   run the comparison, harden prompts/specs, repeat until the human reviewer + the report agree it's as good as
   Step 1 gets.

## 6. Out of scope (later)
- **Step 2** cross-value reconciliation (D2).
- `combined.tsv` / the grand join (milestone #3; needs drug main/aux role too).
- Symmetric-match vocab for drug approvals; Stage-I ingestion; legacy retirement.

## 7. Verification checklist
- `make agentic-tests` green throughout.
- Extraction output byte-identical (sample-trial diff vs frozen `current_output/`).
- `make agentic-run ID=<one>` and `--map-only` both run OOTB (CLI/signatures preserved).
- Map tables populate; empty-rate on `molecular_signature` is reported (a large legit-empty fraction is expected).
- Comparison report produces agree/disagree/novel/unmatched buckets; disagreements are reviewable.
