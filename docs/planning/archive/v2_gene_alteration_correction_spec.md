# gene_alteration mapping correction — SPEC (B1b)

**Status:** awaiting sign-off. No existing code, prompt or output has been touched.
**Date:** 2026-08-04 · **Supersedes** the B1b plan sketched in `docs/v2_agentic_handover.md`.

## 0. Constraints set by the user (2026-08-04)

1. **Isolated package.** New code + prompts live in a sandbox; production modules are edited only on sign-off.
2. **Separate data root** so before/after are directly comparable.
3. **No live runs and no changes to existing code/prompts/outputs until the results are signed off.**
4. **Do not degrade mapping quality to suit the matching engine.** Where the engine genuinely lacks a capability,
   ask for the capability. Post-process only where a rewrite is *lossless and reasonable*.
5. Two stages, as with OncoTree: **Stage 1** = faithful, literal translation of free text into the controlled
   vocabulary; **Stage 2** = reconciliation, part deterministic and part LLM, producing the final form.
6. **DNF principle throughout.**

## 1. What the measurement changed about this task

The handover assumed B1b would mirror B1: build a semantic defect catalogue, a canonical form, and a
reconciliation refinement. Measuring against the **real** grammar — the Java `finding-datamodel` and the
`oncoact/trial-matching` parser, not the curated spreadsheet — shows the diagnosis was wrong in a useful way.

Running all 469 distinct expressions through a faithful port of the engine's parser (validated 12/12 against
`TrialGeneticsParserTest.kt`):

| | distinct | rows |
|---|---:|---:|
| parse exactly as intended | 268 | 4,010 |
| mis-handled | ~190 | ~1,310 |
| ⤷ **engine-side cause only** | **170** | **1,247** |
| ⤷ **carries a defect of ours** | **17** | **66** |

**Our gene_alteration mapping is in far better shape than OncoTree's was.** 0 syntax defects, and the defects that
do exist are four small, well-defined classes totalling 66 rows. **95% of the downstream breakage is the engine's**,
and is escalated in `matching_engine_capability_gaps.md` — not worked around here.

So B1b is **smaller than B1**, and it splits into: a short list of genuine mapping fixes (§2), a grammar reference
that is currently wrong (§3), and a Stage-1/Stage-2 restructure that enforces the DNF principle and adds a
conformance gate (§4–§6).

⚠ **Scope note — `molecular_signature` is OUT OF SCOPE** (user, 2026-08-04: *"the matching engine has not been
coded properly to handle signatures. This is not our problem."*). Measured and recorded here only so it is not
re-investigated as if it were ours. `MolecularCriterionParser.kt` matches `Regex("""(\w+)\[\w+=(\w+)]""")`, so:

- it **ignores the field name entirely** — our unusual `PurpleMicrosatelliteStatus` / `ChordStatus` / `Status`
  spellings are harmless, even though they do not match the Java records;
- its `when` accepts only `MicrosatelliteStability`, `homologousRecombination` and **`tumorMutationStatus`** — we
  emit `tumorMutationBurden` and `tumorMutationLoad` (the datamodel's own record names), so those fall to
  `else -> null` and are **silently dropped: 2 values · 54 export rows · 8 trials**;
- `Regex.find` returns only the FIRST match, so a cell with two criteria loses the rest (**2 values · 20 rows**).

Engine-side items. Not fixed here, and our mapping is not degraded to suit them.

## 2. Bucket A — our defects. This is the actual work.

| id | defect | scale | fix layer |
|---|---|---:|---|
| **A1** | `transcriptImpact.effects=SPLICE` — not a member of `VariantEffect` | 8 expr / 46 rows | grammar + prompt + re-map |
| **A2** | absorption / tautological conjunct: `A & (A\|B\|C)` ≡ `A`; general term ANDed with its own specialisation | 3 expr | deterministic (Stage 2) + prompt |
| **A3** | `NOT(A & B)` where `B` is also a positive conjunct ≡ `B & NOT(A)` | 5 expr / 14 rows | deterministic (Stage 2) |
| ~~**A4**~~ | ~~three renderings of "gene X wild-type"~~ — **WITHDRAWN 2026-08-04, not a defect. See below.** | 0 | — |
| **A5** | HLA eligibility emitted as `PharmocoGenotype[gene=HLA-A & allele=*02:01]`; the datamodel has a dedicated `HlaAllele` record | 1 expr / 3 rows | grammar + prompt |
| **A6** | "actionable alteration" in a canonical fusion driver (ALK, ROS1, RET, NTRK1-3) expanded to include `type=GAIN` — amplification is not the actionable event | ~10 expr | **clinical judgement**, prompt |
| **A7** | `p.K28M` for histone H3 — verify against what the patient-side pipeline emits for H3 K27M | 4 expr | verification only |

Three more are **extraction-stage**, not mapping — the interpreted cell already contains them, so the mapper was
faithful. Log them for the extraction backlog rather than fixing here:

- `NCT06333951` — `homozygous MTAP deletion AND homozygous MTAP deletion AND KRAS p.G12C` (duplicated clause).
- `NCT05544552` — `eligible FGFR3 gene rearrangement AND FGFR3 other kinase domain mutation`: an AND that is
  clinically much more likely to be an OR (two alternative entry routes).
- `NCT05734105` — `KIT exon 11 mutation AND KIT exon 17 mutation`: same suspicion; a compound *primary* KIT
  genotype is rare, alternative-entry is the common design.

### A4 withdrawn — the "three renderings of wild-type" finding was an artifact of the wrong grouping

The original analysis grouped values **by gene** and concluded that 16 genes carried 2–3 competing spellings of
"no alteration in X", calling it the largest real defect. Measuring properly overturns it:

- Grouping by gene puts unrelated criteria in the same bucket. `"EGFR wild type"` was grouped with
  `"ALK fusion positivity AND NOT(EGFR resistance mutation)"` — they share a gene and nothing else.
- The spellings are **not synonyms**. `"EGFR wild-type"` → `Wildtype[gene=EGFR]` (no alteration of any kind);
  `"NOT(EGFR mutation)"` → `NOT(SmallVariant[gene=EGFR])` (an amplification would still be allowed);
  `"NOT(EGFR alteration)"` → the negated full expansion. Three different source claims, three different patient
  sets, three correct renderings.
- Grouping by **concept** instead — the source wording with inexpressible qualifiers stripped — gives the real
  measurement: of 909 values there are 857 distinct concepts, **36 concepts carry more than one source wording,
  and all 36 already agree**. Stage-1 consistency at the concept level is already total.

Two consequences, both recorded in code so this is not relitigated: the gene-based grouping rule is documented as
REJECTED in `reconcile.find_groups`, and there is deliberately no `wildtype_style_mixed` check in
`checks.CATALOGUE`. The concept-based rule stays as a forward guard — it fires on zero values today, which is the
correct result, not a broken detector.

**A2/A3's shared root cause is worth stating.** In `NCT02609776` the source clause is *"EGFR alteration **mediating
resistance to a third-generation TKI**"*. "Mediating resistance" is not expressible in finding-model, so our own
expressiveness-limits rule says **drop the clause**. The mapper instead rendered it as a generic
`SmallVariant | GainDeletion | Fusion` expansion, which then absorbs into the neighbouring conjunct and contributes
nothing. The prompt fix is a rule about *inexpressible qualifiers on a whole clause*, not a new example.

## 3. The grammar reference is wrong and must be re-grounded

`tools/finding_model.py` was written from the curated spreadsheet. The authoritative source is
`/Users/junrancao/HMF_repository/hmftools/finding-datamodel/…/SmallVariant.java` et al. Divergences found:

| our grammar | authoritative | action |
|---|---|---|
| `effects ∈ {INFRAME_DELETION, INFRAME_INSERTION, MISSENSE, SPLICE}` | `VariantEffect` has 20 members; **no `SPLICE`**; `MISSENSE` and both `INFRAME_*` are valid | drop `SPLICE`; add the members we can justify |
| `codingEffect ∈ {NONSENSE_OR_FRAMESHIFT}` | `CodingEffect` = `{NONSENSE_OR_FRAMESHIFT, SPLICE, MISSENSE, SYNONYMOUS, NONE, UNDEFINED}` | complete the enum; **`SPLICE` lives here** |
| `GainDeletion.type ∈ {GAIN, HOM_DEL, HET_DEL}` | `+ CN_NEUTRAL_LOH, NONE` | add `CN_NEUTRAL_LOH` — it is how "LOH" should be expressed |
| — | `transcriptImpact.affectedCodon` (Integer) | add; the honest encoding for "any mutation at codon 12" (currently pseudo-HGVS `p.G12X`) |
| `Arm[... & region= & band=]` only | compound form `Arm[(chromosome=1 & arm=p & type=ARM_LOSS) & (chromosome=16 & arm=q & type=ARM_LOSS)]` is supported and is the *only* way to express multi-arm co-occurrence | document it; use it for `1p/16q`, `monosomy 17` |
| `PharmocoGenotype` | `PharmacoGenotype` in Java, but the engine's own constant is `"PharmocoGenotype"` | leave the spelling; switch HLA uses to `HlaAllele` (A5) |
| `Wildtype[gene=X]` | no Java record; engine skips it | keep emitting (A4 decides the semantics); escalated as G8 |

**Confirmed correct, do not change:** wildcard `p.V600X` (47 expressions — `AminoAcidParser.get("X")` returns null
→ `Substitution(alternate=null)` → matches any substitution at that codon, and `determinant()` returns `"X"`,
so `X` is the intended convention); `Fusion[geneStart=X & geneEnd=Y]`; `Fusion[geneStart=X | geneEnd=X]`
(escalated as an engine bug, G4); `ARM_GAIN`/`ARM_LOSS` (the engine strips the prefix); `NOT(a | b | c)`.

## 3b. THE DNF INVARIANT, STATED PRECISELY — read this before changing row grain or cell contents

**Promote to `Locked decisions` in the handover at approval.** The loose phrasing "one row = one satisfiable
conjunction" is only true of the free-text layer, and the ambiguity is load-bearing: read the wrong way, it argues
for exploding a gene-family OR into one row per gene, which would be a serious mistake.

### The invariant

> **One row = one conjunction of SOURCE CRITERIA. Within a cell, a disjunction is permitted ONLY where it
> enumerates the vocabulary tokens of a SINGLE criterion.**

The DNF-ness lives in the **source language**. Extraction decides how many conjunctions an arm has; mapping
translates each criterion into however many vocabulary tokens that criterion requires, and **preserves row
structure exactly**.

### Why mapping cannot change row count (structural, not a convention)

`interpreted_eligibility` is keyed `(trial_arm_id, conjunction_index)` — 17,830 rows over 5,284 arms (mean 3.4
conjunctions/arm, max 68). The vocabulary maps are keyed by the **distinct free-text value** (900 distinct
`gene_alteration` values). Mapping is a value→value lookup joined onto the row; it has no mechanism to add or
remove rows. Confirmed empirically: export rows are 17,912 before and after this correction.

### Why an in-cell OR is NOT a DNF violation

An OR inside a mapped cell is almost always an artifact of the TARGET VOCABULARY being less expressive than the
source, not a disjunction in the trial's logic. Measured over the corrected output:

| | |
|---|---:|
| interpreted (free-text) cells containing a genuine positive ` OR ` | **3** of 900 |
| ⤷ plus 4 more where the OR sits inside `NOT(...)` — a conjunction after De Morgan, so not a violation | 4 |
| mapped expressions carrying a top-level OR | **132** |
| ⤷ spanning exactly ONE gene — pure vocabulary artifact | 75 |
| ⤷ spanning several genes — a family or panel expansion | 57 |

Extraction is therefore already near-perfectly DNF; **the ORs in the mapped output are introduced by the mapping.**
"TP53 alteration" is one atom in English and needs `SmallVariant | GainDeletion | Disruption`. "RAS mutation" is one
atom and needs three genes. "An alteration in the SWI/SNF complex" is one atom and needs 93 terms.

Because a cell holds the mapping of **exactly one** free-text value, every disjunct in it came from one source
concept **by construction** — so the invariant's second clause is automatically satisfied and needs no policing.

### What splitting them into rows would cost

1. **It would misrepresent the trial.** One criterion would appear as N alternative cohorts. The SWI/SNF value alone
   becomes 96 rows.
2. **It is lossy in the other direction.** You lose the fact that those N rows are one criterion, so any per-arm
   conjunction count becomes meaningless.
3. **It does not help the consumer.** The matching engine's model is *already* `OR of positives` — a flat OR is the
   one shape it handles natively. Splitting would make its job harder.
4. **Volume.** The 132 OR-bearing values expand to 639 disjuncts.

### The one thing that IS a violation

A genuine positive disjunction left in an INTERPRETED cell — 3 values, e.g. `NIH criteria for NF1 OR documented
germline genetic lesion in NF1 gene predicted to be deleterious`, and two PIK3CA "sponsor-approved alternative"
values. Those are alternatives in the trial's logic and extraction should have split them into rows. **Upstream of
mapping; logged against backlog item C4** ("an OR left inside a cell"), not fixed here.

## 4. Stage 1 — faithful literal translation

Unchanged in purpose. The only changes are that it is grounded in the corrected grammar (§3) and carries the
clause-level rule from §2 (an inexpressible qualifier on a whole clause means **drop the clause**, never
substitute a broader term). Stage 1 keeps full fidelity, **including positive conjunctions** — we do not
pre-emptively weaken co-occurrence because the engine cannot yet consume it.

## 5. Stage 2 — reconciliation to a canonical form

Deterministic first; LLM only for judgements that need the source text.

**Deterministic (`gene_expr.py`, mirroring `oncotree_expr.py`):**
1. parse to an AST over `{term, AND, OR, NOT}` respecting `[]` and `()`
2. De Morgan: `NOT(a | b)` → `NOT(a) & NOT(b)`; no nested `NOT`
3. absorption + subsumption: `A & (A|B)` → `A`; `A' & A` → `A'` where `A' ⊆ A` (A2); `B & NOT(A & B)` → `B & NOT(A)` (A3)
4. dedupe terms and conjuncts
5. canonical ordering — class order `SmallVariant, GainDeletion, Disruption, Fusion, Arm, Wildtype, …`, then gene, then fields; OR-terms sorted
6. **DNF normal form**: exactly one top-level conjunction per row, OR-groups parenthesised, no `|` mixed with `&` unparenthesised
7. **idempotence** required — `canonicalise(canonicalise(x)) == canonicalise(x)`, tested over the whole corpus

**LLM (mapper → reviewer, on `core/review.py`):** only A4's semantic choice, A6's expansion judgement, and A1's
re-encoding where the source is ambiguous about splice mechanism.

**Explicitly NOT done here** (this is the guardrail against §0.4): no dropping of conjuncts to fit
`GeneticCriteria`; no splitting `Fusion[geneStart=X | geneEnd=X]`; no rewriting `Wildtype[gene=X]` to
`NOT(SmallVariant[gene=X])`; no re-ordering into the `NOT(...) & A | B` shape that the engine's `firstPass`
happens to accept. Each of those is a capability request, filed in `matching_engine_capability_gaps.md`.

## 6. The conformance checker — new, and the most valuable artifact

`engine_conformance.py`: the validated Python port of `TrialGeneticsParser`, `GeneticAlterationParser`,
`ProteinAnnotationParser`, the `ChromosomeArm*` parsers and `GeneticCriteria`. It answers, per expression,
*what the engine will actually do with this* — clean / silently mis-parsed / dropped — and aggregates by cause.

Two uses:
- a **report** (not a hard gate) each run, so downstream breakage is visible instead of inferred;
- a **regression check on the engine**: re-run after the engine ships G1–G8 and the numbers must move.

It must stay pinned to a recorded engine commit, with its 12-case validation against `TrialGeneticsParserTest.kt`
as a unit test — otherwise it silently drifts from the thing it models.

## 7. Where the code lives during testing

Mirrors the OncoTree sandbox (`v2_oncotree_correction_spec.md` §7); approval is a file copy.

```
aus_trial_universe/gene_alteration_review/
  expr.py                  # AST + canonical form (-> tasks/eligibility/tools/gene_expr.py)
  checks.py                # semantic defect catalogue (-> tools/finding_model_checks.py)
  engine_conformance.py    # the validated engine port  (-> qa/engine_conformance.py)
  updated/
    finding_model.py       # -> tasks/eligibility/tools/finding_model.py   (§3 grammar + enums)
    agents.py              # -> tasks/eligibility/mapping/agents.py        (A1,A4,A5,A6 + reviewer)
    reconcile.py           # -> tasks/eligibility/mapping/reconcile.py     (wire the gene path, §5)
  bind.py                  # test-only injection into the real entry points; deleted at approval
  compare.py               # the single before/after comparison file
```

Data root for the sandbox: `data/agentic/gene_alteration_review/` — never the production store.

## 7b. MIGRATION — EXECUTED 2026-08-04/05. All six stages passed.

| stage | what | gate | result |
|---|---|---|---|
| A | snapshot `masters/ derived/ analysis/` + `MD5SUMS` | — | `data/backups/pre_gene_alteration_20260804_2327/` · 116 MB · 123 files |
| B | restructure `mapping/` per column; `tools/` deleted; `is_oncotree` → `column` | 350 tests **and** `agentic-demo` byte-identical | ✅ 20/20 files identical — pure refactor proven |
| C | swap in the reviewed modules; wire gene stage 2; register 6 agents; new gate; move tests | tests green | ✅ 350 |
| D-i | `prompt_sha` equality, sandbox → production | all 6 byte-identical | ✅ rename is cache-neutral (`agent_name` is not in the key) |
| D-ii | production path over 909 values; **pass = ZERO cache misses** | 0 misses | ✅ 1,974 hits · 0 misses · 909/909 identical at stage 1 AND final |
| E | archive + write 2 gene tables + rebuild joined views + export + gates | gates no FAIL | ✅ gates **WARN · 0 FAIL**; new gate 0 errors / 862 expressions |
| F | known-good snapshot · cache prune · retire sandbox · docs | Stage D re-verified after prune | ✅ still 0 misses |

**Three traps this migration hit, all worth remembering:**

1. **`cp -n <glob>` silently did nothing.** Merging 9,850 sandbox cache entries reported `+0`; the glob exceeded
   `ARG_MAX` and `|| true` swallowed the failure. Use `find … | xargs -0 -n 200 cp`. *Never let `|| true` guard a
   step whose success you then assert from its own output.*
2. **The cache prune would have deleted the entire approved run.** The merged entries were tagged with the sandbox
   agent names (`ga_review_*`), which are not in `prompt_registry`, so `cache_prune` classified all 9,850 as STALE.
   Applying that would have silently re-billed the reviewed run. Fixed by **re-tagging** the entries to their
   production names (legitimate — Stage D-i proved the prompts byte-identical), after which the prune correctly
   removed only the 7,882 entries from superseded iterations 1–5 and kept the 1,968 live ones.
3. **`--max-attempts` defaults to 6; the approved gene run used 3** — the exact trap the OncoTree migration hit. And
   the gene column needed its **own** escalation suffix (`_GENE_ESCALATION`), because the shared `_ESCALATION` text
   differs from what the approved run used and 11 values had escalated.

**Additive safety verified:** of the 9 store tables, the 5 that must not change (`arm_eligibility_raw`, `arm_scope`,
`interpreted_eligibility`, `cancer_type_map`, `molecular_signature_map`) are **byte-identical** to the archive, and
`finalised_cancer_type_map` / `finalised_molecular_signature_map` were reproduced byte-identically by the production
writers — confirming that reusing their existing FINAL values was correct.

**The remap record lives beside the data it superseded** (decision D3):
`masters/eligibility/archive/20260804/gene_alteration_remap_20260804_2352{.md,_comparison.tsv,_engine_dry_run.md}`.

## 8. Phases

- **Phase 0 — deterministic only, NO API, NO data change.** Build `expr.py`, `checks.py`,
  `engine_conformance.py` + tests. Run over the frozen `finalised_gene_alteration_map.tsv`; produce the
  before/after comparison file and the conformance report. **This alone measures A2, A3 and every §3 divergence,
  and needs no LLM.**
- **Phase 1 — prompt changes, live API, sandboxed.** A1/A4/A5/A6 via `bind.py` into
  `data/agentic/gene_alteration_review/`. Cache misses automatically (prompt text is hashed into the key).
  Review the comparison file; require `stable_across_runs`.
- **Phase 2 — production swap**, only on sign-off: copy `updated/*`, add the conformance report to
  `qa/gates.py`, delete `bind.py` and the sandbox, re-snapshot to `data/backups/known_good_*`.

## 9. Open questions — needed before Phase 1

1. **A4:** what does "EGFR wild-type" mean for matching — no small variant, or no alteration of any kind?
   This decides the canonical rendering and depends on the answer to engine gap G8.
2. **A1:** which splice encoding will the engine support (`codingEffect=SPLICE`, or two OR'd
   `effects=SPLICE_ACCEPTOR|SPLICE_DONOR` terms)? Blocked on G7.
3. **A6:** confirm that "ALK/ROS1/RET/NTRK actionable alteration" should exclude amplification.
4. **G1 policy:** if the engine will not add AND-of-ORs positives, what do we do with the 33 co-occurrence
   expressions? Recommendation: keep them faithful and let the conformance report carry the known gap — never
   silently weaken them.

## 9b. Measured outcomes, iteration by iteration

**Iteration 0 — deterministic layers only, over the frozen production map (no LLM).**
909 values · 141 canonicalised · **14 error-severity defects → 0**, all by mechanical substitution. 3 tautological
conjuncts collapsed by absorption. 0 reconciliation groups.

**Iteration 1 — corrected stage-1 prompts + stage 2 (1,961 LLM calls, 5 min).**
Stage 1 emitted **0 error-severity defects** — the mechanical pass had nothing left to fix, i.e. the grammar
correction removed the defect at source rather than downstream. 720 identical · 104 normalised · 85 changed
meaning · 0 groups. **But the regression detector found 18 flags, and reviewing them by hand mattered more than
the count did**: 11 were the intended rewrites (DNF distribution, negation factoring, compound `Arm`, absorption)
which the detector mis-read as losses, and **7 were real regressions**.

The 7 real regressions, grouped by the PATTERN behind them rather than patched case by case:

| pattern | instances | systematic fix |
|---|---|---|
| a gene named with a **functional-state word** returned `""` | `FH deficient`, `RET activation` (and `SDH deficient`, already empty in production) | the expansion trigger is "gene + a word describing its state without naming the event" — so `-deficient`, `deficiency`, `loss of function`, `activation`, `activated`, `dysregulation` join `alteration`/`aberration` |
| a **family ROOT token emitted as a gene symbol** | `PRKC`, `YAP` | prompt rule (roots are not loci; HGNC-numbered symbols) **plus a deterministic gate**: a token that is a proper prefix of known genes and not itself known is an `error` (`family_root_as_gene`) |
| a **double negative** read as an exclusion or dropped | `NOT(known negative for APC LoF mutation)` | resolve polarity through to the end before choosing the shape |
| a **hyphenated gene pair** dropped as inexpressible | `NOT(BCR-ABL mutation)` | a gene pair denotes the FUSION whatever noun follows it |
| a **pathway naming its families** treated as vague | `MAPK pathway mutations (eg, RAS, RAF, MAPKK)` | a pathway is vague only when it names NO members; an enumeration IS the gene set |

**Iteration 2 — the five pattern fixes applied.** All 6 targeted regressions resolved (`FH deficient`,
`RET activation`, `PRKC`, `YAP`, `NOT(known negative for APC…)`, `NOT(BCR-ABL mutation)` all back to a correct
mapping or better). Flags rose 18 → 25, and reading all 25 was again where the value was:

- **~15 were my detector's own false positives**, including a genuine bug: `width()` counted literals by splitting
  the `literal_set` key on `|`, but a `Fusion[geneStart=X | geneEnd=X]` term contains `|` in its own body, so every
  expression using that idiom was over-counted. Fixed with a `LITERAL_SEP` sentinel that cannot occur in an
  expression.
- **~5 were correct improvements the heuristic mistook for losses.** The clearest: `NOT(EGFR genomic alterations)
  AND NOT(ALK …) AND NOT(ROS1 …)` dropped `GainDeletion[ALK]`, `GainDeletion[ROS1]` and `Fusion[EGFR]` — the
  fusion-driver rule applied *per gene*, correctly (ALK/ROS1 amplification is not the actionable event; EGFR fusion
  is not either). Also `NTRK1 fusion-positive` → `Fusion[geneEnd=NTRK1]` (NTRK is the 3' partner, matching the
  curated resource), `TAZ` → `WWTR1` (HGNC), and `KMT2A rearrangement AND NOT(KMT2A-wildtype)` → the redundant
  exclusion dropped.
- **1 real regression**: `an alteration in at least one of the genes of the SWI/SNF complex` emptied. The pathway
  rule over-fired — a named protein COMPLEX has established membership and must expand, unlike a signalling
  pathway with no agreed gene list. Rule split accordingly (SWI/SNF, HRR, Fanconi, MMR expand; "PI3K signalling"
  does not).
- **2 left for human judgement**, flagged rather than decided: whether `homozygous CDKN2A/B deletion` means both
  loci (AND, as production had it) or either (OR, as the new run has it); and whether `unmutated IGHV` should map
  at all — `Wildtype[gene=IGHV]` cannot match in a somatic-variant pipeline, so `""` may be the more honest answer.

**Iteration 3 — polarity precedence + named-complex expansion.** SWI/SNF restored; actionability-qualified
exclusions correctly omitted; **R3 fired for the first time (4 groups over 8 values)**, so the concept guard is
live rather than vacuous. Flags rose to 39, and reviewing them exposed that *my precedence rule itself was wrong*:

`NOT(sensitizing EGFR mutation)` was being omitted across ~10 values, because I had listed "sensitivity" as an
inexpressible qualifier alongside "sensitive to available targeted therapy". They are not the same thing.
**"Sensitizing / activating EGFR mutation" is a biological class with a stable, expressible membership** (exon 19
deletion, L858R, G719X, L861Q, S768I); "an alteration for which therapy is available" has no fixed referent. The
same over-reach dropped `NOT(BCL2 … including G101V)`, where a *specific variant* is named and excluding exactly
that variant broadens nothing.

The rule was rewritten around the right question — **does the excluded thing have an expressible referent?** —
with a three-way order of precedence: a named SPECIFIC alteration is kept and excluded exactly; a named CLASS WITH
ESTABLISHED MEMBERSHIP is kept and expanded; only an availability judgement over an UNSPECIFIED set is omitted.
A mixed clause keeps its named parts and drops only the open-ended part. Also added: `t(a;b)` cytogenetic notation
denotes a fusion (`t(11;14)` had been emptied), and the "including" rule was sharpened after `MET fusion, including
PTPRZ1-MET` was still being narrowed to the example.

This is the third time in three iterations that **the flag list was more wrong than the mappings**, and each time
the fix was one general rule rather than a patch per case. Both of the rules I got wrong (the wild-type grouping,
the sensitivity qualifier) failed the same way: they keyed on a *surface feature* (a shared gene, a word) instead
of the *semantics* (the source's claim, the referent's expressibility).

**Iteration 4 — referent-based exclusion rule.** Flags 39 → **16**, R3 groups 4 → 2. All four targeted fixes landed:
`t(11;14)` now maps to both fusion orientations (better than production, which had one), `NOT(BCL2 … G101V)` keeps
the named variant, the general `MET fusion` head survives its "including" example, and `NOT(sensitizing EGFR
mutation)` expands to the classical set instead of collapsing to the bare gene.

**Iteration 5 — the defect only a human read could find.** Reading the R3/R4 section of the log showed the
reconciliation stage *undoing* the stage-1 improvement: group A193 unified `EGFR mutation`,
`sensitizing EGFR mutation` and `activating EGFR mutation` to bare `SmallVariant[gene=EGFR]`, and A564 collapsed
`NOT(EGFR activating mutation)` to `NOT(SmallVariant[gene=EGFR])` — the over-exclusion the mapping rules forbid.

**Neither automated check could see it.** The engine dry run cannot: both spellings parse fine. The regression
detector cannot: production *also* had the collapsed form, so the row reads "identical" and no flag fires. It was
visible only in the adjudicator's own rationale, which said in as many words that it was dropping "activating" and
"sensitizing" as qualifiers without a finding-model field.

Root cause was mine, in `concept_key`: `_DROPPABLE` listed "activating"/"sensitising"/"sensitizing" alongside
"documented" and "deleterious". Removing them (with the reasoning recorded in the code) took R3 groups 2 → **0**,
which is the correct result. The reconciler prompt also gained an explicit prohibition on collapsing a named class
to its bare gene.

**Final state:** 909 values · **0 error-severity defects** · 675 identical · 105 normalised · 129 changed ·
16 regression flags, of which **0 are unexplained regressions** (9 are the intended rewrites re-flagged, 4 are
improvements, 3 are the clinical-content questions below). Engine dry run: **OURS 14 → 0**.

### Clinical commitments, endorsed by the user 2026-08-04 — the medical basis

**The EGFR "sensitising" class** = exon 19 deletion · L858R · G719X (G719A/C/S) · L861Q · S768I. The first two are
the classical sensitising mutations (~45% and ~40% of EGFR-mutant NSCLC); the last three are the label-recognised
uncommon sensitising mutations. **Excluded deliberately:** T790M (the acquired-resistance gatekeeper to 1st/2nd-gen
TKIs — actionable, but the opposite of sensitising), exon 20 *insertions* (intrinsically TKI-insensitive; note the
contrast with S768I, a point mutation in the same exon), and C797S (osimertinib resistance). "Activating" and
"sensitising" are interchangeable in NSCLC protocol language and both denote this class in every context.
*Residual risk:* a protocol occasionally defines its own list (some add E709X); the "including" rule means an
explicitly named list in the source wins.

**Genes where an unspecified "alteration" must NOT include `type=GAIN`** — gene-specific, not derivable by family:

| gene | actionable alteration | exclude GAIN |
|---|---|---|
| ROS1 · NTRK1/2/3 · NRG1 | fusion only | yes |
| RET | fusion (NSCLC/thyroid) + activating point mutations (M918T, MTC) | yes |
| ALK | fusion (NSCLC) — **but** amplification and point mutations are real drivers in NEUROBLASTOMA | yes (NSCLC-facing) |
| **FGFR3** | mutations (R248C, S249C, G370C, Y373C) + FGFR3–TACC3 fusion | **yes — added** |
| **FGFR2** | fusions (cholangiocarcinoma) **and amplification** (gastric) | **no — must not be added** |
| FGFR1 | amplification (squamous NSCLC) | no |
| MET · EGFR · ERBB2 | amplification (+ MET exon-14 skipping) | no |

### The five open calls — RESOLVED 2026-08-04 (user: "resolve them")

Each resolution is expressed as a GENERAL rule in the prompt, never as a per-value patch.

1. **`homozygous CDKN2A/B deletion` → AND (both).** `CDKN2A/B` names the 9p21 *locus*: the genes sit ~25 kb apart
   and are co-deleted by a single lesion, which is why the WHO grading criterion for IDH-mutant astrocytoma is
   written as one event. Production's AND was right. **General rule:** a slash joining ADJACENT genes/loci names one
   event affecting both → AND them (consistent with the existing `1p/19q` compound-`Arm` rule); a slash joining
   UNRELATED genes (`EGFR/ALK/ROS1 wild-type`) is a list of separate criteria.
2. **`unmutated IGHV` → `""`.** IGHV status is somatic *hypermutation* of the immunoglobulin heavy-chain locus,
   scored by comparing IGHV sequence to germline. It is not a somatic variant call, IGHV is not a single gene
   (IGHV1-69 …), and `Wildtype[gene=IGHV]` could never match in a somatic pipeline — production's mapping was
   unmatchable. A real CLL biomarker with no finding-model referent.
3. **`mean ERBB2 copy number <6 by ISH` → `NOT(GainDeletion[gene=ERBB2 & type=GAIN])`.** The ISH threshold IS the
   definition of HER2 amplification (positive at ratio ≥2.0 or average copy number ≥6.0), so "<6" means
   non-amplified. **General rule:** a copy-number THRESHOLD is inexpressible but its DIRECTION is not — drop the
   number, keep the polarity; never drop a whole clause because it contains a number.
4. **bare `activating EGFR mutation` → expand to the sensitising class**, aligning the one outlier with its ~14
   siblings. Fixed by strengthening the general statement (those words denote the class in EVERY context), not by
   adding an example for the outlier.
5. **`eligible without documented PIK3CA mutation only with sponsor approval AND no other genetic driver
   documented` → `""`.** Read carefully, the clause grants a PERMISSION for PIK3CA-wildtype patients ("eligible
   without … only with sponsor approval"); it does not impose a requirement. Production's
   `NOT(SmallVariant[gene=PIK3CA])` asserted a requirement the source never makes. The trailing "no other genetic
   driver" is open-ended → omitted.

### Previously flagged — superseded by the resolutions above

1. **`homozygous CDKN2A/B deletion`** — both loci (`AND`, as production had) or either (`OR`, as now)? The `/`
   notation is genuinely ambiguous; CDKN2A and CDKN2B are adjacent and usually co-deleted.
2. **`unmutated IGHV`** → now `""` (production: `Wildtype[gene=IGHV]`). IGHV mutational status is somatic
   hypermutation of an immunoglobulin locus; `Wildtype[gene=IGHV]` cannot match in a somatic-variant pipeline, so
   `""` may be the more honest answer — but it does drop a real CLL biomarker.
3. **`mean ERBB2 gene copy number <6 by ISH`** — the threshold is inexpressible, but the direction (not amplified)
   would be expressible as `NOT(GainDeletion[gene=ERBB2 & type=GAIN])`.
4. **`activating EGFR mutation` (standalone)** maps to the bare gene while ~14 sibling values containing
   "activating" expand to the classical set. One outlier in fifteen — **deliberately not fixed**: tightening the
   rule for it would risk the fourteen that are already right.
5. **`eligible without documented PIK3CA mutation only with sponsor approval AND no other genetic driver
   documented`** → now `""` (production: `NOT(SmallVariant[gene=PIK3CA])`). The source is convoluted enough that
   either reading is defensible.

Two process lessons, both already paid for once:

1. **The detector needed fixing before the mappings did.** 11 of 18 flags were false — comparing top-level conjunct
   counts made DNF distribution look like a loss. Comparing canonical *literal sets* first fixes it. A regression
   detector that cries wolf trains you to skim the one flag that matters.
2. **A mechanical defect must never reach the LLM.** Handing `effects=SPLICE` to the repairer worked — and the
   repairer also re-derived the rest of the expression, adding ~18 `NOT()` terms the original had deliberately
   omitted. Moving both substitutions (`splice_as_effect`, `hla_as_pharmacogenotype`) into `mechanical_fixes()`
   made the change set minimal and auditable. The LLM repairer keeps a **scope guard**: it may remove literals but
   may never introduce a gene the current expression does not already contain.
3. **Every rule of mine that turned out wrong keyed on a SURFACE FEATURE instead of the semantics.** The wild-type
   grouping keyed on a shared *gene* rather than the source's claim; the enumeration rule keyed on the presence of a
   *list* rather than polarity; the sensitivity rule keyed on a *word* rather than whether the referent is
   expressible; `_DROPPABLE` keyed on a word's *look* rather than its meaning. Each correlated with the real signal
   often enough to pass casual inspection, then broke exactly where it diverged. When writing a rule, state it in
   terms of what the source MEANS, and check it against the whole corpus.
4. **The automated checks are necessary and not sufficient.** The most damaging defect found in five iterations —
   the reconciler collapsing a named class to its bare gene, producing an over-exclusion — was invisible to the
   syntax gate, the semantic catalogue, the regression detector and the engine dry run. It was legible only in the
   agent's own rationale in the log. That is why the log records rationales at all, and why the log must be READ.

## 10. Follow-ups logged during this work (not in scope here)

- **Split `tasks/eligibility/mapping/` by mapping type** (user, 2026-08-04). `agents.py` is 830 lines carrying
  oncotree + gene_alteration + molecular_signature + all four reconcilers in one file; `workflow.py` and
  `reconcile.py` likewise branch on column. Separate into per-vocabulary modules (`oncotree/`, `gene_alteration/`,
  `molecular_signature/`) with the shared loop/harness above them. Do it AFTER this correction lands, so the
  sandbox copy targets stable filenames.
- **Audit `molecular_signature` against the engine** the same way — see the scope note in §1.

## 11. Lessons carried over from B1 (do not relearn)

Condensing a prompt deletes rules · opposing reviewer checks need an explicit tie-breaker · prefer a deterministic
fix to a prompt fix when the rule is mechanical · verify a rule change against the whole corpus, not its motivating
value · class docstrings and `Field` descriptions are hashed into the cache key · register every new agent in
`prompt_registry.py` · test the live connection, not just the imports.
