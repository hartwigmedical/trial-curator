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
- **End-to-end smoke test (sign-off):** the FULL pipeline (extract → map → drug top-up → combined) run **fully live**
  for `NCT07099898` in a completely ISOLATED scratch DATA_ROOT (inputs/resources read-real; all outputs + a fresh
  cache → scratch) → **rc=0**, every refactored loop exercised live (extraction raw+interpret; drug
  canonicalize/annotate/approvals via web_search), sane output at every stage. Confirmed **zero existing files
  touched** (real stores' mtimes unchanged, real cache count unchanged at 41,796; the 28 live-call entries landed in
  the scratch cache).
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

## 8. `gene_alteration → finding-model` — mapper spec (design LOCKED 2026-07-27; prompts pending user sign-off)

The second mapper (after `cancer_type`, signed off). Workload: **859 distinct gene values** in the frozen store;
**35% of distinct values (26% of cells) contain `NOT()`**. Head is dominated by clean point mutations
(`KRAS G12C/D/A/S/V`). Same standing anti-overfit method as cancer_type (live-test 50 → self-review →
principle-level fixes → disjoint-validate 100 → lock with the user).

### 8.1 The four decisions the user made (2026-07-27)
| # | Decision | Chosen |
|---|----------|--------|
| G1 | **Deterministic syntax gate** | **Full grammar validator** — `finding_model_problems()` rewritten to a field/enum/scope/HGVS-aware parser (see §8.2). Hard gate in the loop; the reviewer then judges only SEMANTICS. |
| G2 | **Whole-gene wild-type** | Use the **`Wildtype[gene=X]` class** (the curated resource's full-negation expansion is *wrong* per the user). A codon-specific wild-type (`KRAS G12/G13 wild-type`) stays expressible as `NOT(SmallVariant[gene=X & p.<codon>X])`. |
| G3 | **Disease-phrased `NOT()`** | **Convert when definitional** — `NOT(BCR-ABL-positive leukemia)` / `NOT(Ph+ ALL)` → `NOT(Fusion[geneStart=BCR & geneEnd=ABL1])`. **Omit** open-ended clinical/actionability exclusions (`NOT(any actionable alteration…)`) and location/context-qualified ones. (NOT the aggressive per-gene expansion of actionability lists.) |
| G4 | **Gene families / pathway tokens** | **Expand to member genes** — `RAS` → `KRAS | NRAS | HRAS`, same variant applied to each. A vague pathway with no specific gene → `""`. |

### 8.2 The full grammar validator (G1) — `tools/finding_model.py`
`finding_model_problems(expr) -> list[str]` (stable signature; callers in mapping/workflow + qa/validate_output
unchanged) is now a real DSL validator driven by a `CLASS_SPEC` table (one entry per finding-model class). It
enforces, deterministically: known class · **known fields per class** · **enum values** (`type` ∈ GAIN/HOM_DEL/
HET_DEL, `effects`, `codingEffect`, `PurpleMicrosatelliteStatus`, `ChordStatus`, `Status`, `arm` p/q, `type`
ARM_GAIN/ARM_LOSS, `Virus.name` HPV/EBV/HHV8) · **required scope** (SmallVariant/GainDeletion/Disruption/Wildtype
need `gene=`; GainDeletion needs `type=`; Fusion needs `geneStart`/`geneEnd`; Arm needs chromosome+arm+type) ·
**HGVS shape** (`p.` + residue + number, `X` wildcard allowed) · balanced `[]`/`()` · **top-level OR/AND
parenthesisation** (`A | B & C` is ambiguous) · idempotency + `X & NOT(X)` self-contradiction. Validated against
the curated resource's 263 `Mapping_args`: the 55 it flags are all the resource using *superseded* conventions our
grammar replaced (bare `GainDeletion[gene=X]` without a type; ungrounded `SmallVariant[inSpliceRegion]`) — i.e. the
validator correctly enforces our locked grammar, not false positives. Tests:
`test_finding_model_validator_grammar_valid` + `_rejects`.

### 8.3 Folded-in rules (recommendations, following prior convention — in the doer prompt)
- **Drop non-expressible qualifiers**, map the underlying alteration: functional/significance
  (`activating`/`actionable`/`oncogenic`/`pathogenic`/`deleterious or suspected deleterious`/`sensitising`),
  origin (`germline`/`somatic`), detection/assay/sample/timing (`ctDNA`/`central NGS`/`per local testing`/
  `in tumour and/or blood`/`after progression on <drug>`), quantitative (copy count/level, VAF).
- **Notation → canonical**: HGVS `p.` always (`G12C`≡`p.G12C`); codon-only / unstated residue → the **`X`
  wildcard** (`IDH1 R132`→`p.R132X`, `Q61`→`p.Q61X`); exon indels → `affectedExon` + `effects`.
- **Word→class vocabulary**: amplification→GAIN; homozygous/deep/unspecified deletion→HOM_DEL; heterozygous→HET_DEL;
  rearrangement/translocation/fusion→Fusion (single-gene unknown-orientation `geneStart=X | geneEnd=X`, `A::B`
  pair, NTRK-style family OR'd); MET exon-14 skip→`affectedExon=14 & effects=SPLICE`; FLT3-ITD→exon-14
  INFRAME_INSERTION, FLT3-TKD→exon 20; chromosome arm→`Arm[...]`, codeletion→two Arm terms ANDed.
- **Bare "mutation"/"alteration"** → the grammar's TSG-vs-oncogene expansion (unchanged).
- **Anti-lazy `""`** — empty ONLY for a value with no molecular content (pure clinical/risk/phenotype descriptor).
- **Logic** — parenthesise OR-groups before ANDing; order SmallVariant→GainDeletion→Disruption→Fusion; no dup, no `X & NOT(X)`.

### 8.4 Reviewer strategy (as cancer_type: NOT the old disjoint-examples rule)
Reviewer gets the **doer's full correct reference examples + a block of FAIL counter-examples** (lost variant,
un-expanded family, un-converted disease-NOT, lazy empty, lost exon, wild-type-as-negation, dropped-qualifier
false-positive), ~1/3 PASS balance, a "Do NOT fail for dropped inexpressible qualifiers" calibration para, and the
escalation `suggested_fix` clause. The reviewer judges semantics only (syntax is the validator's job).

### 8.5 Status — VALIDATED (2026-07-27), awaiting user sign-off
Prompts (`_GENE_RULES` + `GENE_REVIEWER_INSTRUCTIONS` in `mapping/agents.py`) are BAKED (uncommitted; user commits).
Autonomous iteration DONE: nothing under `/data` touched (scratch cache + read-only store).
- **Set A (50 trials / 330 distinct values):** iter1 328/330 → **iter3 330/330 faithful (100%)**.
- **Disjoint set B (100 trials / 242 values):** **iter3 239/242 (98.8%)**. Combined **569/572 = 99.5%**; 0 invalid-syntax,
  0 lost-protein, 0 family-not-expanded on both.
- **5 principle-level fixes made + generalised** (no set-A regression): (A) FLT3-ITD → `effects=INFRAME_INSERTION`
  (drop inferred exon), FLT3-TKD → `SmallVariant[gene=FLT3]` (TKD = inexpressible domain); (B) an EXCLUSION with an
  inexpressible qualifier (domain/location/context/significance) is OMITTED, never broadened (over-exclusion) —
  e.g. `NOT(bZIP CEBPA)` omitted; (C) rearrangement/translocation → Fusion (never Disruption), incl. inside NOT();
  (D) an unspecified "deletion"/"loss" → `type=HOM_DEL` (mandated default; reviewer aligned); (E) a cytoband/
  segmental loss/gain → the ARM-level `Arm[...]` (band range dropped), kept not omitted.
- **3 residuals (NOT implementation defects):** 2× HRR gene-set (doer→empty, reviewer→wants the HRR panel — the
  gene-pathway decision below), 1× borderline `NOT(EGFR oncogenic-driver mutation)` (reviewer prefers omit).
- **TWO decisions deferred to the user (I did NOT change them autonomously — both reverse/extend signed-off scope):**
  1. **Bare "X mutation" → full gene-type EXPANSION (incl. amplification), or `SmallVariant` only?** Current
     convention expands; evidence shows it yields clinically-odd mappings (`IDH1`/`NPM1 mutation` → amplification)
     AND is applied INCONSISTENTLY run-to-run (a consistency-requirement concern). Recommendation: "mutation" =
     sequence variant → `SmallVariant`; reserve expansion for genuinely unspecified terms ("alteration"/"aberration").
  2. **Gene-pathway tokens (`HRR gene alteration`, `HRR-mutated`) → `""` (current, per G4 "vague pathway") or expand
     to the HRR gene panel?** Recurs (HRR ~21 cells + HRD/HRR variants). Needs a domain call on the canonical gene set.
- Harness + frozen A/B trial/value lists + result TSVs (`setA_iter3.tsv`, `setB_iter3.tsv`) in `scratchpad/gene/`.

## 9. `molecular_signature → finding-model` — mapper spec (VALIDATED 2026-07-27, awaiting sign-off)

The third and final mapper. Workload: **167 distinct values** in the frozen store; the column is dominated by
NON-signature values (risk/prognostic scores, expression subtypes, MRD, cytogenetic-risk, gene/chromosomal
alterations, receptor biomarkers), so a **large legit-empty fraction is expected and correct**.

### 9.1 Output vocabulary — EXACTLY SIX terms (the controlled output space; fair to expose in full)
`MicrosatelliteStability[PurpleMicrosatelliteStatus=MSI|MSS]`, `homologousRecombination[ChordStatus=HR_DEFICIENT|
HR_PROFICIENT]`, `tumorMutationBurden[Status=HIGH]`, `tumorMutationLoad[Status=HIGH]`. Synonyms baked into the doer
from the curated `MolecularSignatureCurationResource` (MSI ← dMMR/MMRd/Lynch/HNPCC/MSI-L; MSS ← pMMR/normal MMR;
HR_DEFICIENT ← HRD/HRR-deficiency/FH-/SDH-deficient; TMB "burden" vs "load"/TML kept distinct; only Status=HIGH exists).

### 9.2 Design (mirrors the gene mapper; opposite empty-emphasis)
- The dominant failure mode here is **hallucination** (forcing a non-signature into a term), not lazy-empty — so the
  doer/reviewer emphasise "`""` is CORRECT and COMMON for the many non-signature values" while still forbidding a
  GENUINE signature dropped to empty (dMMR IS MSI). Drop inexpressible qualifiers (thresholds/levels/assay/TILs);
  negations wrap the term in `NOT(...)`.
- **Cross-column routing:** a gene/chromosomal alteration that lands in this column ("1p/19q-codeletion", "HPV",
  "Ph+", "del17p") → `""` here (it belongs to gene_alteration). Notably **"HRR gene mutation" → `""`** (a gene
  mutation), while **"HRR deficiency"/HRD → HR_DEFICIENT** (the functional signature) — the same HRR distinction as
  the gene mapper's deferred pathway question, handled consistently.
- Reviewer strategy identical to gene: accept + FAIL counter-examples, "do not demand a mapping for non-signatures",
  escalation clause.

### 9.3 Status — VALIDATED, no fixes needed
Prompts (`_SIGNATURE_RULES` + `SIGNATURE_REVIEWER_INSTRUCTIONS`) BAKED (uncommitted). **First draft was clean:**
- **Set A (50 trials / 107 values):** 107/107 faithful; **Disjoint set B (82 trials / 66 values):** 66/66 faithful.
  Combined **173/173 = 100%**, HAND-VERIFIED (all non-empties correct; every empty a genuine non-signature).
- 0 invalid syntax, 0 hallucinations, 0 lazy empties; correct negations, qualifier-dropping, TMB/TML distinction,
  and cross-column routing. No iteration required.
- Harness + frozen A/B lists + `setA_iter1.tsv`/`setB_iter1.tsv` in `scratchpad/sig/`.
  (Only open item shared with gene: the HRR gene-SET decision — does "HRR gene mutation" stay `""` or expand?)
