# Finding-model capability gaps in `oncoact/trial-matching`

**Audience:** the trial-matching engine maintainers · **Author:** trial-curator (AUS trial universe) · **Date:** 2026-08-04

We curate Australian trial eligibility into finding-model expressions (the `gene_alteration_findingmodel` column of
`trial_eligibility.tsv`). This note reports what happens to those expressions when they reach
`oncoact/trial-matching`, measured by running **every distinct expression** in our corrected mapping through a
faithful Python port of `TrialGeneticsParser` + `GeneticAlterationParser` + `GeneticCriteria`.

**The port is validated against your own test suite** — it reproduces all 12 cases in
`TrialGeneticsParserTest.kt` exactly (12/12). Everything below is derived from it, not from inspection.

## Headline

Measured over the **corrected** gene-alteration mapping (871 non-empty expressions), after our own defects were
fixed and the mapping was human-reviewed:

| | expressions | export rows | trials |
|---|---:|---:|---:|
| parse cleanly through the engine | 558 | — | — |
| **need an ENGINE upgrade** | **313** | **~1,300** | — |
| have a defect of OURS | **0** | 0 | 0 |

Our side is clean: the 14 expressions that carried a defect of ours in the previous export (11 × invalid
`transcriptImpact.effects=SPLICE`, 3 × HLA typed as `PharmocoGenotype`) are fixed. Everything remaining is
well-formed finding-model, grounded in your own `finding-datamodel` Java records, that the engine cannot currently
consume.

**Two gaps got BIGGER because we corrected our mapping, and that is the expected direction.** `G6` went 1 → 17
because `effects=SPLICE` (not a `VariantEffect` member) is now the valid `codingEffect=SPLICE` — which
`parseSmallVariant` never reads. `G4` went 167 → 185 because better mappings emit more correct
`Fusion[geneStart=X | geneEnd=X]` terms. We have deliberately **not** compensated for these downstream: where the
mapping is right and the engine cannot read it, that is a capability request, not a reason to write something less
accurate.

| gap | expressions | export rows | trials |
|---|---:|---:|---:|
| G4 in-bracket `Fusion` alternation loses both genes | 185 | 1105 | 132 |
| G2 top-level `&` not split unless preceded by `NOT(` | 86 | 347 | 45 |
| G3 unparsed input silently disables the biomarker gate | 51 | 178 | 27 |
| G8 `Wildtype`/`Virus`/`PharmocoGenotype`/`HlaAllele` skipped | 38 | 126 | 37 |
| G1 positive conjunction unrepresentable | 37 | 102 | 27 |
| G5 `HET_DEL`/`CN_NEUTRAL_LOH` dropped | 18 | 53 | 5 |
| G6 `codingEffect`/`affectedCodon` never read | 17 | 73 | 13 |

The single most important structural fact, from `GeneticCriteria.kt:7-8`:

```kotlin
if (negatives.any { n -> patient.genetics.any { g -> n.includes(g) } }) return false
return positives.isEmpty() || positives.any { p -> patient.genetics.any { g -> p.includes(g) } }
```

Criteria are **one flat OR of positives** and **one AND-NOT list of negatives**. Two consequences drive most of
this note: a positive *conjunction* has nowhere to live, and **`positives.isEmpty()` returns `true`** — so a
failed parse does not fail closed, it silently removes the biomarker gate.

---

## G1 — Positive conjunctions are unrepresentable (**highest clinical impact**)

**37 expressions · 102 export rows · 27 trials.** Full list in the appendix.

`GeneticCriteria` can express "A or B" and "not C", but not "A **and** B". Many of the most important precision-
oncology cohorts are defined by exactly that:

| requirement | trials | expression |
|---|---|---|
| double-hit lymphoma — `MYC` + `BCL2` rearrangement | 3+ | `Fusion[…MYC…] & Fusion[…BCL2…]` |
| triple-hit — `MYC` + `BCL2` + `BCL6` | 3 | `Fusion[…MYC…] & Fusion[…BCL2…] & Fusion[…BCL6…]` |
| osimertinib resistance — `EGFR` ex19del + `MET` amplification | NCT05261399 | `SmallVariant[gene=EGFR & …exon=19…] & GainDeletion[gene=MET & type=GAIN]` |
| `EGFR` compound — L858R + T790M (and + C797X) | NCT05388669, NCT07699328 | `SmallVariant[…L858R] & SmallVariant[…T790M]` |
| `BCR::ABL1` + T315I | NCT05304377, NCT07354074 | `SmallVariant[gene=ABL1 & …T315I] & Fusion[geneStart=BCR & geneEnd=ABL1]` |
| `FLT3`-ITD + `NPM1` / + `CEBPA` | NCT05457556 | `SmallVariant[gene=FLT3 & …INFRAME_INSERTION] & SmallVariant[gene=NPM1]` |
| `MTAP` HOM_DEL + `KRAS` G12C / + RAS mutation | NCT06333951, NCT06360354 | `GainDeletion[gene=MTAP & type=HOM_DEL] & …` |
| `IDH1` R132 + `ATRX` LOF | NCT05303519 | `(SmallVariant[…R132H] \| …) & SmallVariant[gene=ATRX]` |

**Why this can't be worked around on our side.** Dropping a conjunct turns a co-occurrence cohort into a
single-marker cohort, which **over-matches** — a `MYC`-rearranged patient without `BCL2` would be offered a
double-hit trial. Splitting into separate rows makes it worse, because our rows are OR'd by construction. There is
no faithful rewrite; the capability has to exist.

**Ask:** let `GeneticCriteria.positives` hold a conjunction of OR-groups rather than a single flat set — i.e.
`positives: List<Set<GeneticAlteration>>` evaluated as AND-of-ORs, with today's behaviour being the one-element
case. `satisfies` becomes `positives.all { grp -> grp.any { … } }`.

## G2 — `firstPass` only splits `&` after a `NOT(` token

`TrialGeneticsParser.kt:52`:

```kotlin
} else if (c == '&' && bracketLevel == 0 && currentToken.trim().startsWith("NOT(")) {
```

A top-level `&` is a separator **only when the accumulating token already begins with `NOT(`**. So any expression
with a positive term ANDed to anything is never split; the whole string is passed to
`GeneticAlterationParser` as if it were a single term. Two outcomes, both wrong:

- `SmallVariant[gene=EGFR] & NOT(SmallVariant[gene=EGFR & …hgvsProteinImpact=p.T790M])`
  → `data["gene"] == "EGFR]"` → a positive criterion on a gene that does not exist → **matches nobody**, silently.
- the same shape with an enum-valued field → `VariantEffect.valueOf("INFRAME_INSERTION]")` throws → the whole
  expression is dropped → positives empty → **biomarker gate removed** (see G3).

Note `(A | B) & NOT(C)` is *already representable* in your data model — positives `{A,B}`, negatives `{C}`. It is
only the parser that cannot read it. The parenthesised-OR shape is the 16 expressions previously reported to us as
"too complicated"; the flat `A & NOT(B)` form accounts for the rest and fails with no message at all.
**86 expressions · 347 export rows · 45 trials.**

**Ask:** split on top-level `&` unconditionally (tracking `[` `]` as well as `(` `)`), then classify each conjunct
as negated or not. Your existing tests all continue to pass under that rule.

## G3 — A parse failure silently disables the biomarker gate

`positives.isEmpty()` short-circuits to `true` (`GeneticCriteria.kt:8`). Combined with the `try/catch` that only
`println`s (`TrialGeneticsParser.kt:20-22`), an expression the engine cannot read becomes *no genetic constraint
at all*, and matching falls through to oncology criteria alone. **51 expressions · 178 export rows · 27 trials** are
in this state. This is also why the volume went unnoticed: most failures print nothing.

**Ask:** distinguish "no criteria supplied" from "criteria supplied but unparseable". The latter should surface as
an error (or an explicit `UNPARSEABLE` state that fails closed), not as a permissive match.

## G4 — `Fusion[geneStart=X | geneEnd=X]` silently becomes "any fusion"

**185 expressions · 1105 export rows · 132 trials — the largest single defect.**

`GeneticAlterationParser.kt:29-32` keeps a field only when `it.split('=')` yields exactly 2 parts. For
`geneStart=ALK | geneEnd=ALK` the split yields 3, so the pair is discarded, `data` is empty, and
`parseFusion()` returns `Fusion(null, null)`. `Fusion.includes` then skips both null checks
(`Fusion.kt:9,11`) and **matches any fusion in any gene**.

This is the canonical idiom for "gene X is a fusion partner, orientation unspecified": it appears **47 times** in
the hand-curated `GeneAlterationCurationResource` and in your own test
`handle delimiter within brackets` (`TrialGeneticsParserTest.kt:53-65`) — that test passes only because it
asserts parser output against parser output, so it cannot catch this.

We are **not** rewriting these to `Fusion[geneStart=X] | Fusion[geneEnd=X]`. A fusion is one event with two
slots; the `|` expresses which slot is unknown, not two candidate events. Splitting it breaks the
one-term-per-alteration invariant and makes `Fusion[geneStart=ALK] | Fusion[geneEnd=ROS1]` ambiguous between
"ALK or ROS1 fusion" and a mangled expression.

**Ask:** parse `|` inside a `Fusion[...]` body as alternation over the named slots. If the `|`-inside-brackets
wart is unwelcome, we would equally accept a cleaner single-clause form — **`Fusion[gene=X]`**, "X is a fusion
partner, orientation unspecified" — which states what is actually known and needs only a small `includes` change.
Either works for us; the in-bracket fix is lower friction since it preserves the existing curation resource.

## G5 — `HET_DEL` is dropped, widening the criterion

`GeneticAlterationParser.kt:56-62` maps only `GAIN` and `HOM_DEL`; anything else returns `null`, and
`Fluctuation.includes` then skips the type check — so `GainDeletion[gene=X & type=HET_DEL]` matches **any**
copy-number event in X. **18 expressions · 53 export rows.** `HET_DEL` is a member of `GainDeletion.Type` in the
datamodel, as is `CN_NEUTRAL_LOH`, which we now emit for copy-neutral LOH criteria.

**Ask:** map all `GainDeletion.Type` members; treat an unrecognised value as an error rather than as "any".

## G6 — Fields present in the datamodel but never read

`parseSmallVariant` reads `gene`, `transcriptImpact.hgvsProteinImpact`, `transcriptImpact.affectedExon` and
`transcriptImpact.effects` only. Unread, and therefore silently ignored:

| field | why we need it |
|---|---|
| `transcriptImpact.affectedCodon` | "any mutation at codon 12/61/132" — currently only expressible as pseudo-HGVS `p.G12X` |
| `transcriptImpact.codingEffect` | this is the enum that actually contains `SPLICE` (see G7) |
| `transcriptImpact.inSpliceRegion` | recognised only when the bracket body is *exactly* `inSpliceRegion`, i.e. with no `gene=`; in any other position the token is discarded. As it stands the field is unusable — we need `SmallVariant[gene=MET & inSpliceRegion]` to work |
| multiple `effects` values | `valueOf` on the raw string means only one effect per term is expressible |

## G7 — `effects=SPLICE`: was our bug, now FIXED — and it exposes G6

`transcriptImpact.effects` is `Set<VariantEffect>`, which has `SPLICE_ACCEPTOR`/`SPLICE_DONOR` but no `SPLICE`;
`SPLICE` belongs to the *other* enum, `CodingEffect`. So our 11 expressions using `effects=SPLICE` were
**our defect**, and they are now FIXED — they emit `transcriptImpact.codingEffect=SPLICE`.

The open question is what the correct encoding for "MET exon 14 skipping" is. `codingEffect=SPLICE` is the natural
single-value answer but is not read (G6). `effects=SPLICE_ACCEPTOR | effects=SPLICE_DONOR` needs two OR'd terms
because only one effect value is parseable. **Please tell us which you want to support**, and we will emit that.

## G8 — `Wildtype`, `Virus` and `PharmocoGenotype` are skipped, not matched

`TrialGeneticsParser.kt:69-71` returns early on any section containing these class names. Because of G2 the check
runs against the whole unsplit token, so `Wildtype[gene=EGFR] & Wildtype[gene=ALK]` drops everything and leaves
the arm unconstrained (G3).

We use `Wildtype[gene=X]` where a trial requires whole-gene wild-type status (**38 expressions · 126 export rows ·
37 trials**, together with `Virus` / `PharmocoGenotype` / `HlaAllele`). We will not rewrite it to
`NOT(SmallVariant[gene=X])` — those are different patient sets (wild-type means no alteration of any kind, not
merely no small variant). Lower priority: `Virus[name=HPV|EBV|HHV8]`, and HLA eligibility, for which the
datamodel's `HlaAllele` record looks like the right target (we currently emit `PharmocoGenotype[gene=HLA-A &
allele=*02:01]`, which we will correct on our side).

**Ask:** confirm whether `Wildtype` is intended to be matched. If yes, it needs implementing; if no, we need to
know what to emit instead.

---

## Suggested priority

| | gap | rows affected | why first |
|---|---|---:|---|
| 1 | **G3** fail-loud on unparseable input | 191 today | cheapest change; makes every other gap visible instead of silent |
| 2 | **G2** split `&` unconditionally | ~1,100 | unblocks the largest class, and your data model already supports the result |
| 3 | **G4** `Fusion` slot alternation | 852 | largest single defect; currently produces false positives |
| 4 | **G1** AND-of-ORs positives | 114 | highest clinical stakes; needs a datamodel change |
| 5 | **G5**, **G6**, **G8** | ~120 + | correctness and expressiveness follow-ups |

## Reproducing this

The port lives with the curation project and can be shared. It is a direct transcription of
`TrialGeneticsParser`, `GeneticAlterationParser`, `ProteinAnnotationParser`, `ChromosomeArm*Parser` and
`GeneticCriteria`, checked against `TrialGeneticsParserTest.kt`. Point it at
`trial_eligibility.tsv` → per-expression outcome plus the aggregate table above.

**Confirmed working, no action needed:** wildcard protein annotations (`p.V600X` → `Substitution(alternate=null)`
→ matches any substitution at that codon — 47 expressions rely on this); `NOT(a | b | c)` flattening; the compound
`Arm[(chromosome=1 & arm=p & type=ARM_LOSS) & (chromosome=16 & arm=q & type=ARM_LOSS)]` form; `ARM_`-prefix
stripping.

## Appendix — the 37 expressions requiring a positive conjunction

Generated from the corrected mapping (`data/agentic/analysis/gene_alteration_comparison.tsv`, column
`after_stage2_FINAL`); row counts are export rows. `source` is the interpreted eligibility text.

- **28 rows** · ACTRN12626000586314, NCT03960840, NCT04332822 …
  - source: `MYC and BCL2 rearrangements`
  - expression: `Fusion[geneStart=BCL2 | geneEnd=BCL2] & Fusion[geneStart=MYC | geneEnd=MYC]`
- **13 rows** · NCT03960840, NCT04628494, NCT04824092 …
  - source: `MYC rearrangement AND BCL6 rearrangement`
  - expression: `Fusion[geneStart=BCL6 | geneEnd=BCL6] & Fusion[geneStart=MYC | geneEnd=MYC]`
- **7 rows** · NCT03960840, NCT04628494, NCT04824092
  - source: `MYC rearrangement AND BCL2 rearrangement AND BCL6 rearrangement`
  - expression: `Fusion[geneStart=BCL2 | geneEnd=BCL2] & Fusion[geneStart=BCL6 | geneEnd=BCL6] & Fusion[geneStart=MYC | geneEnd=MYC]`
- **4 rows** · NCT05388669
  - source: `EGFR Exon 19 deletion (Exon 19del) AND EGFR T790M mutation`
  - expression: `SmallVariant[gene=EGFR & transcriptImpact.affectedExon=19 & transcriptImpact.effects=INFRAME_DELETION] & SmallVariant[gene=EGFR & transcriptImpact.hgvsProteinImpact=p.T790M]`
- **4 rows** · NCT05388669
  - source: `EGFR Exon 21 L858R mutation AND EGFR T790M mutation`
  - expression: `SmallVariant[gene=EGFR & transcriptImpact.hgvsProteinImpact=p.L858R & transcriptImpact.affectedExon=21] & SmallVariant[gene=EGFR & transcriptImpact.hgvsProteinImpact=p.T790M]`
- **3 rows** · NCT07007312
  - source: `NOT(BCR-ABL mutation) AND NPM1 mutation AND FLT3 ITD ratio <0.05`
  - expression: `SmallVariant[gene=FLT3 & transcriptImpact.effects=INFRAME_INSERTION] & SmallVariant[gene=NPM1] & NOT(Fusion[geneStart=BCR & geneEnd=ABL1])`
- **3 rows** · NCT07007312
  - source: `NOT(BCR-ABL mutation) AND NPM1 mutation AND FLT3 mutation for which FLT3 inhibition is not standard of care`
  - expression: `SmallVariant[gene=FLT3] & SmallVariant[gene=NPM1] & NOT(Fusion[geneStart=BCR & geneEnd=ABL1])`
- **3 rows** · NCT07007312
  - source: `NOT(BCR-ABL mutation) AND KMT2A rearrangement AND NOT(KMT2A partial tandem duplication) AND FLT3 ITD ratio <0.05`
  - expression: `SmallVariant[gene=FLT3 & transcriptImpact.effects=INFRAME_INSERTION] & Fusion[geneStart=KMT2A | geneEnd=KMT2A] & NOT(SmallVariant[gene=KMT2A & transcriptImpact.effects=INFRAME_INSERTION]) & NOT(Fusion[geneStart=BCR & geneEnd=ABL1])`
- **3 rows** · NCT07007312
  - source: `NOT(BCR-ABL mutation) AND KMT2A rearrangement AND NOT(KMT2A partial tandem duplication) AND FLT3 mutation for which FLT3 inhibition is not standard of care`
  - expression: `SmallVariant[gene=FLT3] & Fusion[geneStart=KMT2A | geneEnd=KMT2A] & NOT(SmallVariant[gene=KMT2A & transcriptImpact.effects=INFRAME_INSERTION]) & NOT(Fusion[geneStart=BCR & geneEnd=ABL1])`
- **3 rows** · NCT05457556
  - source: `FLT3/ITD+ with allelic ratio > 0.1 with concurrent bZIP CEBPA`
  - expression: `SmallVariant[gene=CEBPA] & SmallVariant[gene=FLT3 & transcriptImpact.effects=INFRAME_INSERTION]`
- **3 rows** · NCT05457556
  - source: `FLT3/ITD+ with allelic ratio > 0.1 with concurrent NPM1`
  - expression: `SmallVariant[gene=FLT3 & transcriptImpact.effects=INFRAME_INSERTION] & SmallVariant[gene=NPM1]`
- **3 rows** · NCT05304377, NCT07354074
  - source: `BCR-ABL1 positive AND T315I mutation`
  - expression: `SmallVariant[gene=ABL1 & transcriptImpact.hgvsProteinImpact=p.T315I] & Fusion[geneStart=BCR & geneEnd=ABL1]`
- **2 rows** · NCT05261399
  - source: `EGFR exon19 deletion AND MET amplification in tumour specimen collected following progression on prior osimertinib treatment`
  - expression: `SmallVariant[gene=EGFR & transcriptImpact.affectedExon=19 & transcriptImpact.effects=INFRAME_DELETION] & GainDeletion[gene=MET & type=GAIN]`
- **2 rows** · NCT05261399
  - source: `EGFR L858R mutation AND MET amplification in tumour specimen collected following progression on prior osimertinib treatment`
  - expression: `SmallVariant[gene=EGFR & transcriptImpact.hgvsProteinImpact=p.L858R] & GainDeletion[gene=MET & type=GAIN]`
- **2 rows** · NCT05261399
  - source: `EGFR T790M AND MET amplification in tumour specimen collected following progression on prior osimertinib treatment`
  - expression: `SmallVariant[gene=EGFR & transcriptImpact.hgvsProteinImpact=p.T790M] & GainDeletion[gene=MET & type=GAIN]`
- **2 rows** · NCT05544552
  - source: `eligible FGFR3 gene rearrangement AND FGFR3 other kinase domain mutation`
  - expression: `SmallVariant[gene=FGFR3] & Fusion[geneStart=FGFR3 | geneEnd=FGFR3]`
- **2 rows** · NCT05734105
  - source: `KIT exon 11 mutation AND KIT exon 17 mutation AND NOT(KIT exon 9, 13, or 14 mutation)`
  - expression: `SmallVariant[gene=KIT & transcriptImpact.affectedExon=11] & SmallVariant[gene=KIT & transcriptImpact.affectedExon=17] & NOT(SmallVariant[gene=KIT & transcriptImpact.affectedExon=13]) & NOT(SmallVariant[gene=KIT & transcriptImpact.affectedExon=14]) & NOT(SmallVariant[gene=KIT & transcriptImpact.affectedExon=9])`
- **2 rows** · NCT05734105
  - source: `KIT exon 11 mutation AND KIT exon 18 mutation AND NOT(KIT exon 9, 13, or 14 mutation)`
  - expression: `SmallVariant[gene=KIT & transcriptImpact.affectedExon=11] & SmallVariant[gene=KIT & transcriptImpact.affectedExon=18] & NOT(SmallVariant[gene=KIT & transcriptImpact.affectedExon=13]) & NOT(SmallVariant[gene=KIT & transcriptImpact.affectedExon=14]) & NOT(SmallVariant[gene=KIT & transcriptImpact.affectedExon=9])`
- **2 rows** · NCT03839771
  - source: `IDH1 R132 mutation AND FLT3 mutation for which treatment with a FLT3 inhibitor is not considered for medical or other reasons AND NOT(dual IDH1 and IDH2 mutations)`
  - expression: `SmallVariant[gene=FLT3] & SmallVariant[gene=IDH1 & transcriptImpact.hgvsProteinImpact=p.R132X] & NOT(SmallVariant[gene=IDH2])`
- **2 rows** · NCT03839771
  - source: `IDH2 R140 mutation AND FLT3 mutation for which treatment with a FLT3 inhibitor is not considered for medical or other reasons AND NOT(dual IDH1 and IDH2 mutations)`
  - expression: `SmallVariant[gene=FLT3] & SmallVariant[gene=IDH2 & transcriptImpact.hgvsProteinImpact=p.R140X] & NOT(SmallVariant[gene=IDH1])`
- **2 rows** · NCT03839771
  - source: `IDH2 R172 mutation AND FLT3 mutation for which treatment with a FLT3 inhibitor is not considered for medical or other reasons AND NOT(dual IDH1 and IDH2 mutations)`
  - expression: `SmallVariant[gene=FLT3] & SmallVariant[gene=IDH2 & transcriptImpact.hgvsProteinImpact=p.R172X] & NOT(SmallVariant[gene=IDH1])`
- **2 rows** · NCT02609776, NCT03175224
  - source: `primary EGFR mutated disease AND MET amplification`
  - expression: `SmallVariant[gene=EGFR] & GainDeletion[gene=MET & type=GAIN]`
- **1 rows** · NCT06333951
  - source: `homozygous MTAP deletion AND homozygous MTAP deletion AND KRAS p.G12C mutation`
  - expression: `SmallVariant[gene=KRAS & transcriptImpact.hgvsProteinImpact=p.G12C] & GainDeletion[gene=MTAP & type=HOM_DEL]`
- **1 rows** · NCT02609776
  - source: `primary EGFR mutated disease AND MET mutation`
  - expression: `SmallVariant[gene=EGFR] & SmallVariant[gene=MET]`
- **1 rows** · NCT06163430
  - source: `BCR-ABL1 positive AND certain BCR-ABL1 resistance mutations`
  - expression: `SmallVariant[gene=ABL1] & Fusion[geneStart=BCR & geneEnd=ABL1]`
- **1 rows** · NCT07699328
  - source: `documented EGFR mutation AND NOT(EGFR exon 20 insertion mutation) AND NOT(HER2 exon 20 insertion mutation) AND EGFR exon 19 deletion AND EGFR C797X resistance mutation AND NOT(another target`
  - expression: `SmallVariant[gene=EGFR & transcriptImpact.affectedExon=19 & transcriptImpact.effects=INFRAME_DELETION] & SmallVariant[gene=EGFR & transcriptImpact.hgvsProteinImpact=p.C797X] & NOT(SmallVariant[gene=BRAF & transcriptImpact.hgvsProteinImpact=p.V600E]) & NOT(SmallVariant[gene=EGFR & transcriptImpact.affectedExon=20 & transcriptImpact.effects=INFRAME_INSERTION]) & NOT(SmallVariant[gene=ERBB2 & transcriptImpact.affectedExon=20 & transcriptImpact.effects=INFRAME_INSERTION]) & NOT(SmallVariant[gene=KRAS & transcriptImpact.hgvsProteinImpact=p.G12X]) & NOT(GainDeletion[gene=ERBB2 & type=GAIN]) & NOT(GainDeletion[gene=MET & type=GAIN]) & NOT(Fusion[geneEnd=NTRK1]) & NOT(Fusion[geneEnd=NTRK2]) & NOT(Fusion[geneEnd=NTRK3]) & NOT(Fusion[geneStart=ALK | geneEnd=ALK]) & NOT(Fusion[geneStart=RET | geneEnd=RET]) & NOT(Fusion[geneStart=ROS1 | geneEnd=ROS1])`
- **1 rows** · NCT07699328
  - source: `documented EGFR mutation AND NOT(EGFR exon 20 insertion mutation) AND NOT(HER2 exon 20 insertion mutation) AND EGFR L858R mutation AND EGFR C797X resistance mutation AND NOT(another targetab`
  - expression: `SmallVariant[gene=EGFR & transcriptImpact.hgvsProteinImpact=p.C797X] & SmallVariant[gene=EGFR & transcriptImpact.hgvsProteinImpact=p.L858R] & NOT(SmallVariant[gene=BRAF & transcriptImpact.hgvsProteinImpact=p.V600E]) & NOT(SmallVariant[gene=EGFR & transcriptImpact.affectedExon=20 & transcriptImpact.effects=INFRAME_INSERTION]) & NOT(SmallVariant[gene=ERBB2 & transcriptImpact.affectedExon=20 & transcriptImpact.effects=INFRAME_INSERTION]) & NOT(SmallVariant[gene=KRAS & transcriptImpact.hgvsProteinImpact=p.G12X]) & NOT(GainDeletion[gene=ERBB2 & type=GAIN]) & NOT(GainDeletion[gene=MET & type=GAIN]) & NOT(Fusion[geneEnd=NTRK1]) & NOT(Fusion[geneEnd=NTRK2]) & NOT(Fusion[geneEnd=NTRK3]) & NOT(Fusion[geneStart=ALK | geneEnd=ALK]) & NOT(Fusion[geneStart=RET | geneEnd=RET]) & NOT(Fusion[geneStart=ROS1 | geneEnd=ROS1])`
- **0 rows** · 
  - source: `MYC rearrangement AND BCL-2 rearrangement`
  - expression: `Fusion[geneStart=BCL2 | geneEnd=BCL2] & Fusion[geneStart=MYC | geneEnd=MYC]`
- **0 rows** · 
  - source: `MYC rearrangement AND BCL-6 rearrangement`
  - expression: `Fusion[geneStart=BCL6 | geneEnd=BCL6] & Fusion[geneStart=MYC | geneEnd=MYC]`
- **0 rows** · 
  - source: `eligible FGFR3 gene rearrangement AND FGFR3 resistance mutation`
  - expression: `SmallVariant[gene=FGFR3] & Fusion[geneStart=FGFR3 | geneEnd=FGFR3]`
- **0 rows** · 
  - source: `MYC translocation AND BCL2 translocation`
  - expression: `Fusion[geneStart=BCL2 | geneEnd=BCL2] & Fusion[geneStart=MYC | geneEnd=MYC]`
- **0 rows** · 
  - source: `MYC translocation AND BCL6 translocation`
  - expression: `Fusion[geneStart=BCL6 | geneEnd=BCL6] & Fusion[geneStart=MYC | geneEnd=MYC]`
- **0 rows** · 
  - source: `MYC translocation AND BCL2 translocation AND BCL6 translocation`
  - expression: `Fusion[geneStart=BCL2 | geneEnd=BCL2] & Fusion[geneStart=BCL6 | geneEnd=BCL6] & Fusion[geneStart=MYC | geneEnd=MYC]`
- **0 rows** · 
  - source: `BCR::ABL1 e14a2 transcript AND NOT(BCR::ABL1 mutation with known resistance to study treatment) AND BCR::ABL1 T315I mutation`
  - expression: `SmallVariant[gene=ABL1 & transcriptImpact.hgvsProteinImpact=p.T315I] & Fusion[geneStart=BCR & geneEnd=ABL1]`
- **0 rows** · 
  - source: `BCR::ABL1 e13a2 transcript AND NOT(BCR::ABL1 mutation with known resistance to study treatment) AND BCR::ABL1 T315I mutation`
  - expression: `SmallVariant[gene=ABL1 & transcriptImpact.hgvsProteinImpact=p.T315I] & Fusion[geneStart=BCR & geneEnd=ABL1]`
- **0 rows** · 
  - source: `MYC/BCL2 rearrangement`
  - expression: `Fusion[geneStart=BCL2 | geneEnd=BCL2] & Fusion[geneStart=MYC | geneEnd=MYC]`
- **0 rows** · 
  - source: `MYC rearrangement AND BCL2 rearrangement`
  - expression: `Fusion[geneStart=BCL2 | geneEnd=BCL2] & Fusion[geneStart=MYC | geneEnd=MYC]`
