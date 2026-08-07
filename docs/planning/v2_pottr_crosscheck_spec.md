# POTTR cross-check + the disease-derived alteration inference

**Status:** first draft delivered 2026-08-07. Backlog item **C2** (cross-check against POTTR ground truth), plus a
new capability POTTR inspired. Code: `aus_trial_universe/analysis/pottr/`. Data:
`data/agentic/analysis/pottr/`. Tests: `tests/agentic/analysis/` (38, no API).

> **POTTR IS A PEER, NOT GROUND TRUTH.** Its file is hand-curated and contains defects — the `NCT04924075`
> wild-type-GIST row names **PDGFRB** where the entity is defined by KIT and **PDGFRA**; `catype:cytotoxic_chemotherapy`
> files a therapy as a cancer type. Every disagreement is adjudicated against the **registry source text**, never
> against POTTR's answer. This mirrors the standing principle already held for the matching engine.

---

## 1. What POTTR's file actually is

`https://github.com/fpylin/POTTR/blob/master/data/trial_eligibility.AU.tsv` — two columns
(`trial_id`, `eligibility_criteria`), **354 rows over 269 trials**. Every one of those 269 trials is already in our
universe (POTTR trials are force-included), so the intersection is complete and there is no join loss.

### The DSL, read off POTTR's own Perl

| construct | meaning | source |
|---|---|---|
| `;` | AND | `ClinicalTrials.pm:446` |
| `(A OR B)` | OR-group | same |
| `NOT ` | negation | `Rules.pm:759` |
| `*` | **SOFT** — assumed true when unknown | `Rules.pm:617` |

`*` is the one that matters and it is not decoration. `Rules.pm:617` performs *"contingency reasoning by falsely
asserting a fact to satisfy a constraint"*: when the patient's facts do not establish a `*` criterion, POTTR
**assumes it** and records the assumption, so the term never blocks a match. **207 of 808 terms carry it.**
Treating those as hard criteria would manufacture differences that do not exist, so they are parsed, compared,
and flagged separately (`soft_only_difference` on the headline).

Multiple rows for one trial are ORed — that is POTTR's DNF.

### POTTR's namespaces do NOT line up with our columns

`route()` in `pottr_source.py` is a router, not a rename. The **RHS decides** for a gene namespace, which is why
the same gene correctly lands in two different columns:

```
ERBB2:amplification      -> gene_alteration       (a copy-number event)
ERBB2:protein_expression -> molecular_biomarker   (an IHC readout)
```

`catype:` → cancer_type · `prior_therapy:` → prior_therapy · MSI/MMR/TMB → molecular_signature ·
`sensitive_to:`/`info:`/`trial_type:` and bare `COHORT_2`-style tokens → **annotation**, excluded from the
comparison (they are POTTR bookkeeping; counting them as criteria we lack would be a pure artifact).

### The file's defects, and how the parser handles them

Tolerant, but **never silently** — every repair is recorded on the row and is countable. Seven fire on the
current file:

| trial | defect | handling |
|---|---|---|
| `NCT05872295` | `ERBB2:overexpression ERBB2:protein_expression OR OR …` — a missing OR and a doubled OR | split; recorded |
| `NCT05872295` | `NOT Breast cancer` — the `catype:` prefix omitted | recovered **only** when the token is a catype the file uses elsewhere |
| `NCT06380751` | `ER:positive: HER2:negative` — a `;` typed as `:` | repaired at ROW level so the halves are ANDed conjuncts, not OR alternatives |
| `NCT06112314` | `HLA-A*02:01:Positive` — colons inside the value | HLA special-cased |
| `NCT06417814` | `(… OR NOT prior_therapy:systemic_therapy)` | atom-level `NOT` carried — see below |

Two of these were parser bugs found by testing against the live file, and both are pinned by tests:

- **The in-group `NOT` was being split off and dropped**, silently INVERTING a criterion. The fix is a
  `(?<!\bNOT)` **lookbehind** in the glue-split regex (a lookahead does not work — the text after the space is
  the atom, not the `NOT`).
- **The colon-as-semicolon repair produced an OR**, because it ran inside term parsing. Moved to row level.

---

## 2. Component 2 — the disease-derived alteration (built FIRST)

POTTR records genetics that the trial's **disease** implies rather than states. On `NCT04924075`:

| our row | POTTR |
|---|---|
| `"von Hippel-Lindau (VHL) disease associated localized tumors"` → `Solid tumour`, **no gene** | `VHL:oncogenic_mutation` |
| `"advanced wild-type gastrointestinal stromal tumor (wt GIST)"` → `GIST`, **no gene** | `*NOT KIT:oncogenic_mutation; *NOT PDGFRB:oncogenic_mutation` |

**This had to be built before the comparison.** Without it, POTTR's disease-implied genetics score as criteria we
lack, when in fact we record them in the cancer-type column.

### Why it keys on the interpreted free text

Because **OncoTree destroys exactly the content this stage reads**: `"VHL disease associated tumors"` → `Solid tumour`,
`"wild-type GIST"` → `GIST`. Keying on the mapped code would make the feature impossible. (User's call; the data
confirms it.)

### The bar: definitional entailment, and the polarity asymmetry

The withdrawal test — *if the alteration were absent, would the stated diagnosis still stand?* If yes, return `""`.
**Prevalence is not definition, at any frequency**: ccRCC→VHL (~90%), HGSOC→TP53 (~96%), PDAC→KRAS (~90%) all
return `""`.

The risk here is **inverted from every other mapper we have**. Elsewhere `""` is the lazy answer and the prompts
fight for output; here `""` is correct for ~92% of values and the failure mode is over-firing.

**The two polarities take different tests, and the exclusion test is strictly stronger.** This was got WRONG in
the first run and cost a full re-roll:

- **INCLUDED entity — NECESSITY.** Does the diagnosis require the alteration? `mantle cell lymphoma` → CCND1
  rearrangement.
- **EXCLUDED entity — SUFFICIENCY (pathognomonicity).** `NOT(disease)` entails `NOT(alteration)` only if
  essentially nothing else carries the alteration.
  - `NOT(Acute Promyelocytic Leukaemia)` → `NOT(PML::RARA fusion)` ✅ — pathognomonic.
  - `NOT(Burkitt lymphoma)` → `NOT(MYC rearrangement)` ❌ — MYC rearrangement is *necessary* for Burkitt but also
    occurs in DLBCL, so negating it discards MYC-rearranged DLBCL patients the trial is recruiting.

  The first run emitted the Burkitt form **33 times** — the second-largest group. The asymmetry is the same one
  already locked in the gene-alteration decisions: *dropping a qualifier broadens an INCLUSION (safe) but narrows
  an EXCLUSION*, and an over-exclusion is never recovered downstream.

Out of scope by rule: IHC receptor status (ER/PR/HER2-by-IHC → the biomarker column), functional signatures
(MSI/TMB/HRD/dMMR → the signature column), stage/grade/treatment qualifiers, and constitutional karyotype
abnormalities (trisomy 21 is not a somatic gene alteration).

### Shape

A pure function of ONE `cancer_type_interpreted` value, on the shared `core.review.review_refine` harness:

```
cancer_type  ->  derived_alteration (free text)  ->  finding_model
```

The second arrow reuses the **production gene mapper unchanged**, so the column lands in the same grammar as
`gene_alteration_findingmodel`, is directly comparable to it, and inherits the finding-model grammar validator.
It writes to its own table and never touches `gene_alteration_map_*` — the "must not affect existing
functionality" requirement met structurally, not by care.

---

## 3. Component 1 — the comparison

### Q1 and Q2 are separated deliberately

Conflating them is what made the v1 comparison hard to read.

**Q1 — how each side disaggregates.** POTTR has no arm concept: **193 of 269 trials are ONE POTTR row against
several arms of ours**. So our side is backed out to trial grain and deduplicated **on the mapped values**, not the
free text — arm-specific phrasing ("advanced NSCLC" vs "locally advanced or metastatic non-small cell lung
cancer") is not a semantic difference, and POTTR's terms are a controlled vocabulary anyway.

| our grain | rows over the 269 trials | trials matching POTTR's count |
|---|---:|---:|
| arm-grain (as exported) | 3,955 | 9 |
| trial-grain, dedup on free text | 1,617 | 75 |
| trial-grain, dedup on mapped + biomarker + prior tx | 1,437 | 90 |
| **trial-grain, dedup on the cohort-defining axes** (what Q1 uses) | **879** | **131** |

The verdict uses the **cohort-defining axes only** (cancer type, gene, signature, biomarker). POTTR does not split
rows on prior therapy, so including it would make the shape verdict hostage to therapy phrasing.

**Q2 — whether the criteria agree.** Compared as **per-trial unions per column**, which sidesteps grain entirely —
how criteria are bundled into conjunctions is Q1's business.

### Two tiers, because only three columns have a target vocabulary

| column | POTTR terms | comparison |
|---|---:|---|
| cancer_type | 86 | **deterministic** — OncoTree codes via the production mapper |
| gene_alteration | 246 | **deterministic** — finding-model via the production mapper |
| molecular_signature | 3 | **deterministic** |
| molecular_biomarker | 27 | **LLM aligner** (IHC; no target vocabulary) |
| prior_therapy | 101 | **LLM aligner** (free text both sides) |

Mapping POTTR's terms with **our own production mappers** is the point: both sides go through the identical
translation, so a residual difference is a difference in CURATION rather than in vocabulary handling. Hand-mapping
POTTR's side would confound every disagreement with two different mapping procedures — precisely the bug the
drug-approval side had before the pipelines were unified.

### Four things that stop the comparison lying

1. **Canonical, SIGNED literals.** The production canonicalisers do the work, so a De Morgan rewrite or a
   different OR ordering counts as identical. Cancer-type literals are signed (`NOT LUSC`), because
   `positive_codes` drops negated codes by definition and every exclusion disagreement would otherwise be
   invisible.
2. **Granularity is not disagreement.** `BLADDER` vs `BLCA`, `PAAD` vs `PANCREAS` — the commonest apparent
   difference is one side choosing a finer OncoTree node. These are paired off deterministically via
   `is_subcode`, so the `pottr_only` / `ours_only` buckets mean what they say.
3. **An OR-group is filed PER ATOM.** 27 groups span two of our columns, and they are not exotic:
   `(ERBB2:amplification OR ERBB2:overexpression)` is the standard HER2-positive definition, one arm a
   copy-number event and the other IHC. Filing the group under one column would misfile half of every one.
4. **A criterion the vocabulary cannot express stays visible** as `<unmapped> …` rather than contributing
   nothing — contributing nothing would report it as absent from that side, a *false agreement*, which is worse
   than a reported difference.

### The mistake verdict

Grounded in the **verbatim registry text** (`arm_eligibility_raw.tsv`), because adjudicating the two curations
against each other is just two opinions. Verdicts: `pottr_wrong` · `ours_wrong` · `both_wrong` · `neither_wrong` ·
`undecidable`. The prompt states explicitly that granularity differences, cross-column filing differences, soft-vs-hard
modelling differences and minor omissions are **`neither_wrong`** — only a factual error about the trial counts
(wrong gene, inverted polarity, a cancer type the trial does not enrol, a criterion the source never states).

---

## 4. Outputs

All under `data/agentic/analysis/pottr/`:

| file | grain | what |
|---|---|---|
| `disease_derived_alteration.tsv` | one cancer-type value | component 2's full lookup (+ `_hits.tsv`, fired only) |
| `pottr_term_crosswalk.tsv` | one POTTR term | **the intermediate file** — term → our column → our vocabulary value |
| `ours_trial_grain.tsv` | one trial | our side as the comparison sees it |
| `pottr_comparison_by_trial.tsv` | one trial | **the headline** — the verdict columns |
| `pottr_comparison_detail.tsv` | one criterion difference | **the long file** the headline aggregates from |

Headline columns: `overall_verdict` · `dnf_difference` · `pottr_only_criteria` · `ours_only_criteria` ·
`shared_criteria` · `soft_only_difference` · `mistake_verdict` · `mistake_detail` · the four counts.

Run: `make pottr-infer` → `make pottr-crosswalk` → `make pottr-compare` (in that order; the comparison consumes
both earlier outputs).

---

## 5. Results (run of 2026-08-07)

### Component 2

**292 of 4,880 distinct cancer types fired (6.0%)** — 104 distinct alterations. The largest groups are
`NOT(PML::RARA fusion)` ×76, `CCND1 rearrangement` ×35 (MCL), `BCR::ABL1 fusion` ×18, `EWSR1 fusion` ×17,
`JAK2 mutation` ×15 (post-PV myelofibrosis), `PAX3::FOXO1 or PAX7::FOXO1` ×15. 3 of 292 ended unfaithful.

**The sufficiency fix, measured.** The first run fired **411 (8.4%)**. Correcting the exclusion test retracted
**128** and added 9:

| over-fire | v1 | v2 |
|---|---:|---:|
| `NOT(MYC rearrangement)` from excluding Burkitt lymphoma | 33 | **0** |
| `NOT(constitutional trisomy 21)` from excluding ML-DS | 8 | **0** |

It also gets the case POTTR gets wrong: `"advanced wild-type gastrointestinal stromal tumor (wt GIST)"` →
`NOT(KIT mutation) AND NOT(PDGFRA mutation)` — **PDGFRA, where POTTR wrote PDGFRB**.

### Component 1

**269 trials · 7 identical · 262 differ · DNF shape matches on 131 · soft-only differences on 15.**
1,296 detail rows: 496 shared criteria, 136 POTTR-only, 664 ours-only.

Verdicts over the 800 substantive differences:

| verdict | n | reading |
|---|---:|---|
| `pottr_omission` | 532 | POTTR does not curate it — a coverage gap, not an error |
| `neither_wrong` | 127 | both defensible, or a granularity / filing difference |
| **`pottr_wrong`** | **70** | POTTR records something the source contradicts |
| `ours_omission` | 33 | we do not record it |
| **`ours_wrong`** | **30** | **we** record something the source contradicts |
| `undecidable` / `both_wrong` | 4 / 4 | |

At trial grain: **211 trials carry no factual error**, 36 `pottr_wrong`, 10 `ours_wrong`, 10 `mixed`, 2 `both_wrong`.

**Separating omission from error was necessary, not cosmetic.** The first pass returned `pottr_wrong` 418 —
**382 of them on `ours_only` rows**, i.e. the judge reading a POTTR omission as a POTTR error. A whole-column
coverage gap is now settled deterministically (no LLM opinion), and the judge has explicit `*_omission` verdicts.
Without that split the headline number is meaningless.

`pottr_wrong` concentrates in cancer_type (30) and gene_alteration (33); `ours_wrong` in prior_therapy (16) and
molecular_biomarker (10).

### Confirmed defects on OUR side, for follow-up

| trial | column | what the source says |
|---|---|---|
| `ACTRN12622001003763` | prior_therapy | source lists docetaxel **or** cabazitaxel **or** abiraterone **or** enzalutamide **or** olaparib **or** lutetium; we recorded only enzalutamide — a lost six-way OR |
| `NCT05872295` | molecular_biomarker | HER2-positive is "IHC 3+ **or** IHC 2+ with ISH+"; we recorded "IHC 2+" alone |
| `NCT04996875` | cancer_type | source enrols AdvSM/ASM/SM-AHN/MCL; we mapped `SMAHN \| SMMCL`, dropping the aggressive-SM arm |
| `NCT05503797` | prior_therapy | the standard-therapy requirement is disjunctive ("received all available **or** intolerant **or** investigator-determined"); we narrowed it |

## 6. Open

- **Component 2 needs a human review pass** before its column could ever be promoted out of `analysis/`. The
  definitional bar is a prompt, and prompts are graded by review, not by tests.
- `analysis/` is a **working directory that may be emptied**. Component 2's output is expensive to regenerate;
  if it is kept, it should migrate to a real store with its own stage tables.
- The comparison currently adjudicates every substantive difference. If the volume proves noisy, the obvious
  filter is to skip columns where one side records nothing at all — structural, not a mistake.
