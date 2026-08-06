"""Gene-alteration mapping agents — corrected stage-1 pair + the NEW stage-2 reconciliation agents.

⚠ Every prompt string here, and every pydantic docstring / Field description in `schema.py`, is hashed into the
response-cache key. Editing this prose orphans every cached decision behind it — including the values signed off
on 2026-08-04. Change it only with a full re-run.

WHAT CHANGED vs the production prompt, and why (see docs/planning/archive/v2_gene_alteration_correction_spec.md §2):
  A1  splice: `transcriptImpact.effects=SPLICE` was impossible — SPLICE is a `CodingEffect`, not a
      `VariantEffect`. MET exon-14 skipping now maps to `transcriptImpact.codingEffect=SPLICE`.
  A2  a WHOLE clause whose entire content is inexpressible (a resistance MECHANISM, an actionability judgement)
      must be DROPPED, not replaced by a broad stand-in expansion. The old prompt only covered dropping a
      *qualifier*, so the mapper substituted "EGFR alteration" for "alteration mediating resistance to a
      third-generation TKI" — which then collapsed to nothing under absorption while looking like content.
  A5  HLA is `HlaAllele`, never `PharmocoGenotype`.
  A6  a canonical fusion driver's "actionable alteration" is the FUSION, not amplification.
  --  the compound `Arm[(...) & (...)]` form for a multi-arm event is now stated (it did not exist in the old
      grammar, so co-deletions were rendered as two ANDed Arm terms).

DELIBERATELY UNCHANGED, having been verified against the Java datamodel and the matching engine's parser:
  the `p.<ref><n>X` wildcard (it pins the reference residue AND the substitution class — more specific than
  `affectedCodon`), the single-term `Fusion[geneStart=X | geneEnd=X]` alternation, `Wildtype[gene=X]`, and
  `ARM_GAIN`/`ARM_LOSS`.
"""
from __future__ import annotations

from aus_trial_universe.core.agent import Agent
from aus_trial_universe.core.client import LlmClient
from aus_trial_universe.tasks.eligibility.mapping.finding_model import GRAMMAR_REFERENCE
# Reused unchanged from production so approval does not have to touch schema.py.
from aus_trial_universe.tasks.eligibility.mapping.schema import FindingModelMapping, ReviewVerdict


# --------------------------------------------------------------------------- #
# STAGE 1 — faithful, literal translation of the free text into the vocabulary.
# --------------------------------------------------------------------------- #
_GENE_RULES = """\
You convert a clinical trial's GENE-ALTERATION expression into Hartwig finding-model syntax. Return `finding_model`
— the same expression re-expressed in the grammar below, preserving its logical structure (AND `&`, OR `|`,
exclusions wrapped in `NOT(...)`). Follow the grammar EXACTLY: only the listed classes and fields exist.

Your job is a FAITHFUL, LITERAL translation. Do not simplify, generalise, or "improve" the criterion; do not drop a
co-occurrence requirement because it looks unusual. A later reconciliation stage normalises spelling — you supply
meaning.

WHAT YOU RECEIVE
The interpreted gene-alteration criterion of one trial arm. It usually carries wording finding-model CANNOT express;
strip it and map the underlying MOLECULAR ALTERATION. Qualifiers to DROP (dropping them is CORRECT, never a fault):
- FUNCTIONAL / SIGNIFICANCE: "activating", "actionable", "oncogenic", "driver", "pathogenic", "deleterious or
  suspected deleterious", "sensitising", "known/documented", "qualifying".
- ORIGIN: "germline", "somatic", "germline or somatic".
- DETECTION / ASSAY / SAMPLE / TIMING: "detected by ctDNA / central NGS / an FDA-approved assay", "per local
  testing", "in tumour and/or blood", "in a specimen collected after progression on <drug>", "ISH+/NGS-confirmed".
- QUANTITATIVE: copy-number COUNT / level ("≥5 copies", "high-level"), VAF threshold, allelic ratio.
- PROTEIN DOMAIN / REGION: "tyrosine kinase domain (TKD)", "bZIP", "kinase domain", "juxtamembrane" — a protein
  sub-region finding-model has no field for. Drop it (map to the gene-level alteration).
Keep only what the grammar has a field for: gene, protein change, codon, exon, effect, copy-number TYPE, fusion
orientation, chromosome arm.

⚠ A DROPPED QUALIFIER IS NOT A DROPPED CLAUSE. Distinguish:
- "activating EGFR mutation"  -> the qualifier "activating" is inexpressible; the CLAUSE still names an alteration
  (an EGFR mutation). Map it: SmallVariant[gene=EGFR].
- "an EGFR alteration MEDIATING RESISTANCE to a third-generation TKI" -> the clause's entire content is a
  RESISTANCE MECHANISM. Finding-model cannot express "mediates resistance", and no specific alteration is named.
  DROP THE WHOLE CLAUSE. Do NOT substitute a broad stand-in ("SmallVariant[gene=EGFR] | GainDeletion[...] |
  Fusion[...]"): that asserts something the source never said, and when ANDed with a neighbouring clause it
  collapses to nothing (A & (A | B | C) = A) while looking like real content.
- Same for "a co-occurring driver for which standard-of-care therapy exists", "an alteration rendering the patient
  ineligible for approved therapy" — whole-clause judgements, all DROPPED.
If dropping the clause leaves the expression empty, return "".

NOTATION — normalise to the grammar's canonical forms:
- Protein change -> HGVS `p.` form ALWAYS: "G12C" -> p.G12C ; "V600E" -> p.V600E ; "Exon21 L858R" -> p.L858R.
- Codon named but the substituted residue UNSTATED (or an explicit "X" / "any") -> the `X` wildcard:
  "IDH1 R132" -> p.R132X ; "KRAS Q61" / "Q61X" -> p.Q61X ; "BRAF V600X" -> p.V600X.
- Exon indels: "exon 19 deletion" -> affectedExon=19 & effects=INFRAME_DELETION ; "exon 20 insertion" ->
  affectedExon=20 & effects=INFRAME_INSERTION.
- SPLICE is a codingEffect, NOT an effect: "MET exon 14 skipping" -> affectedExon=14 & codingEffect=SPLICE.
  Never write `transcriptImpact.effects=SPLICE` — it is not a member of that enum.

GRANULARITY — as specific as the wording supports, no more, no less:
- Named protein change / exon / effect -> include it. NEVER invent detail the source does not state.
- Copy number: name the TYPE only — GAIN (amplification / copy-number gain), HOM_DEL (homozygous / biallelic / deep
  deletion or an unspecified "deletion"/"loss" of a gene), HET_DEL (heterozygous / single-copy loss),
  CN_NEUTRAL_LOH (copy-neutral LOH). Drop counts.
- A stated copy-number THRESHOLD is inexpressible, but its DIRECTION is expressible and must be kept. A
  copy number BELOW the threshold means NOT amplified; AT OR ABOVE means amplified:
  "mean ERBB2 copy number <6 by in situ hybridisation" -> NOT(GainDeletion[gene=ERBB2 & type=GAIN]) ;
  "HER2 copy number >=6" / "ISH ratio >=2.0" -> GainDeletion[gene=ERBB2 & type=GAIN].
  Drop the number, keep the polarity — never drop the whole clause just because a number appears in it.
- A SLASH joining two ADJACENT genes or loci ("CDKN2A/B", "CDKN2A/CDKN2B", "1p/19q") names ONE event affecting
  BOTH, because such neighbours are co-deleted by a single lesion. Render both, ANDed — never as alternatives:
  "homozygous CDKN2A/B deletion" -> GainDeletion[gene=CDKN2A & type=HOM_DEL] & GainDeletion[gene=CDKN2B & type=HOM_DEL].
  (For chromosome arms the equivalent is the compound Arm term — see below.) Contrast a slash joining
  UNRELATED genes ("EGFR/ALK/ROS1 wild-type"), which is a list of separate criteria.
- A bare "X mutation" / "X mutated" / "X-mutant" with NO specific variant -> SmallVariant[gene=X] ONLY. "Mutation"
  denotes a SEQUENCE variant (SNV/indel); do NOT expand it to amplification/deletion/fusion (those are different
  events named by their own words). Do NOT expand when a specific variant IS named.
- Only a genuinely UNSPECIFIED event -> the EXPANSION RULE (tumour-suppressor vs oncogene) in the grammar. The
  trigger is a GENE plus a word describing its state WITHOUT naming the molecular event. That covers
  "X alteration" / "aberration" / "abnormality" / "genomic alteration" / "aberrant X" / "X-altered" AND equally
  "X-deficient" / "X deficiency" / "loss of X" / "loss of function of X" / "X activation" / "activated X" /
  "X dysregulation" / "X-driven". A functional-state word is NOT a reason to return "" — the gene is named, so the
  alteration is expressible: "FH deficient" -> the TSG expansion of FH; "RET activation" -> the oncogene expansion
  of RET. Only a phrase with NO gene at all (a pathway with no members, a protein-expression or methylation
  readout) returns "".
- NEVER mix granularities inside one OR-group: listing both `SmallVariant[gene=EGFR]` and
  `SmallVariant[gene=EGFR & ...p.L861Q]` is redundant (the first already covers the second). Pick the level the
  source states. If the source names SPECIFIC variants ("other EGFR mutations, including L861Q, G719X, S768I"),
  list the SPECIFIC ones — the "including" list IS the definition, not an example of a broader set.

ALTERATION VOCABULARY (word -> class):
- amplification / copy-number gain             -> GainDeletion[gene=X & type=GAIN]
- homozygous|biallelic|deep deletion / loss    -> GainDeletion[gene=X & type=HOM_DEL]
- heterozygous / single-copy loss              -> GainDeletion[gene=X & type=HET_DEL]
- copy-neutral loss of heterozygosity          -> GainDeletion[gene=X & type=CN_NEUTRAL_LOH]
- CYTOGENETIC TRANSLOCATION NOTATION `t(a;b)` denotes a FUSION of the genes at those loci — map it, never "":
  t(11;14) -> Fusion[geneStart=CCND1 & geneEnd=IGH] | Fusion[geneStart=IGH & geneEnd=CCND1] ;
  t(9;22) -> Fusion[geneStart=BCR & geneEnd=ABL1] ; t(15;17) -> Fusion[geneStart=PML & geneEnd=RARA] ;
  t(14;18) -> Fusion[geneStart=IGH & geneEnd=BCL2] | Fusion[geneStart=BCL2 & geneEnd=IGH] ;
  t(8;14) -> Fusion[geneStart=IGH & geneEnd=MYC] | Fusion[geneStart=MYC & geneEnd=IGH].
  When the partner orientation is not conventionally fixed, OR both orderings.
- rearrangement / translocation / gene fusion / fusion -> Fusion (NEVER Disruption; this holds inside NOT() too).
  One gene, orientation unknown: Fusion[geneStart=X | geneEnd=X] — ONE term, never split into two OR'd terms.
  Named partners "A::B" / "A-B fusion": Fusion[geneStart=A & geneEnd=B]. A family of 3' partners (e.g. NTRK) ->
  OR one Fusion per member.
- MET exon 14 skipping -> SmallVariant[gene=MET & transcriptImpact.affectedExon=14 & transcriptImpact.codingEffect=SPLICE]
- FLT3-ITD (internal tandem duplication) -> SmallVariant[gene=FLT3 & transcriptImpact.effects=INFRAME_INSERTION]
  (do NOT add an exon — the source does not state one; never infer it).
- FLT3-TKD (tyrosine-kinase-domain mutation) -> SmallVariant[gene=FLT3] (TKD is an inexpressible protein DOMAIN;
  drop it — do NOT invent an exon).
- chromosome-arm change: "1p loss / deletion / LOH" -> Arm[chromosome=1 & arm=p & type=ARM_LOSS] ; "1q gain" ->
  type=ARM_GAIN. SEVERAL arms lost/gained in ONE event go in ONE compound term, NOT two ANDed terms:
  "1p/19q codeletion" -> Arm[(chromosome=1 & arm=p & type=ARM_LOSS) & (chromosome=19 & arm=q & type=ARM_LOSS)] ;
  "monosomy 17" -> Arm[(chromosome=17 & arm=p & type=ARM_LOSS) & (chromosome=17 & arm=q & type=ARM_LOSS)].
  A cytoband / SEGMENTAL loss or gain maps to the ARM level (finding-model has no field for a band RANGE, so drop
  it): "9p21.1-24.3 loss" -> Arm[chromosome=9 & arm=p & type=ARM_LOSS]. KEEP it (never omit); holds inside NOT() too.

WILD-TYPE — one spelling per shape, so equivalent criteria look equivalent:
- A WHOLE gene stated wild-type / non-mutated / "negative" -> Wildtype[gene=X] (one per gene; AND several
  together: Wildtype[gene=EGFR] & Wildtype[gene=ALK]). Use this whenever the source says the GENE is wild-type.
- A SPECIFIC codon/variant stated wild-type ("KRAS G12/G13 wild-type", "BRAF V600 wild-type") -> the negation of
  that SPECIFIC change: NOT(SmallVariant[gene=KRAS & transcriptImpact.hgvsProteinImpact=p.G12X]) &
  NOT(SmallVariant[gene=KRAS & transcriptImpact.hgvsProteinImpact=p.G13X]).
- "NOT(gene A and gene B wild-type)" means at least one IS altered. State that positively — Wildtype must NEVER
  appear inside a NOT(): -> SmallVariant[gene=A] | SmallVariant[gene=B] (or the alteration type the source
  implies). This holds however the negation is spelled: "NOT(KIT and PDGFRA wild-type)" is
  SmallVariant[gene=KIT] | SmallVariant[gene=PDGFRA], never NOT(Wildtype[gene=KIT] & Wildtype[gene=PDGFRA]).
- Wildtype[gene=X] already asserts that X carries NO alteration, so never AND it with a negated alteration in the
  SAME gene: write Wildtype[gene=EGFR], not Wildtype[gene=EGFR] & NOT(SmallVariant[gene=EGFR]). The second
  conjunct adds nothing and makes two equivalent criteria look different.

GENE FAMILIES / PANELS / PATHWAY TOKENS:
- A recognised gene FAMILY (not a single gene) -> expand to its member genes OR'd, applying the SAME variant to
  each. RAS = KRAS, NRAS, HRAS.
- The HRR / HRR-gene / "homologous recombination repair gene" PANEL -> expand to these 15 genes OR'd (the PROfound
  panel): BRCA1, BRCA2, ATM, BARD1, BRIP1, CDK12, CHEK1, CHEK2, FANCL, PALB2, PPP2R2A, RAD51B, RAD51C, RAD51D,
  RAD54L. Apply the word logic per gene: "HRR gene MUTATION" -> SmallVariant per gene; "HRR gene ALTERATION" ->
  the full TSG expansion per gene. NB "HRR deficiency" / "HRD" is the functional SIGNATURE
  (homologousRecombination[ChordStatus=HR_DEFICIENT]), a molecular_signature — NOT this panel.
- EVERY gene= value must be a REAL gene symbol. A FAMILY ROOT is not a gene: "PRKC", "YAP", "RAS", "AKT",
  "MAP2K", "NTRK", "SDH", "FANC" name families, not loci. Never emit the root — expand it to the member genes the
  source means (PRKC -> PRKCA/PRKCB/PRKCG/PRKCD/PRKCE/PRKCH/PRKCI/PRKCQ/PRKCZ; SDH -> SDHA/SDHB/SDHC/SDHD).
  Where the symbol carries a numeric suffix in HGNC, use it: YAP is YAP1, TAZ is WWTR1, MLL is KMT2A.
  A root token silently matches nothing downstream, so this is a hard requirement, not a preference.
- A NAMED PROTEIN COMPLEX or DNA-REPAIR PATHWAY has an established membership and MUST be expanded, exactly like a
  gene family: the SWI/SNF (BAF) complex, the HRR panel, the Fanconi-anaemia genes, the mismatch-repair genes
  (MLH1, MSH2, MSH6, PMS2). "an alteration in at least one of the genes of the SWI/SNF complex" is a complete,
  expressible criterion — expand it (ARID1A, ARID1B, ARID2, SMARCA2, SMARCA4, SMARCB1, SMARCC1, SMARCC2, SMARCD1,
  SMARCD2, SMARCD3, SMARCE1, PBRM1, BRD7, BRD9, DPF1, DPF2, DPF3, BCL7A, BCL7B, BCL7C, BCL11A, BCL11B, BICRA,
  BICRAL, PHF10, ACTL6A, ACTL6B, ACTB, ACTG1, SS18, SS18L1), never "".
- A SIGNALLING PATHWAY is different: it is vague only when it names NO members. If the source enumerates families
  or examples ("MAPK pathway mutations (eg, RAS, RAF, and MAPKK mutations)"), that enumeration IS the gene set —
  expand the named families and ignore the pathway word. Return "" only for a signalling pathway with no members
  named anywhere and no established gene list ("RAS/MAPK pathway alteration", "PI3K signalling alteration").
  ⚠ PRECEDENCE — an enumerated family is expanded in BOTH polarities. In an INCLUSION, expand it freely. Inside
  NOT(), an enumerated gene list is still a NAMED referent and is still KEPT (see "POLARITY PRECEDENCE" under
  NEGATION); the only thing omitted inside NOT() is a part that names NOTHING — an open "any/other actionable
  alteration" remainder.

"INCLUDING" — does the list DEFINE the criterion, or merely illustrate it? Decide from the HEAD:
- head is already a COMPLETE expressible event -> KEEP THE HEAD, and do NOT replace it with the example. The list
  illustrates; restricting to it NARROWS the criterion and loses patients. "MET fusion, including PTPRZ1-MET
  fusion" -> Fusion[geneStart=MET | geneEnd=MET] — the answer is the general MET fusion term, NOT
  Fusion[geneStart=PTPRZ1 & geneEnd=MET]. Optionally OR the named specific onto the head; never substitute it.
- head is a vague class that the list is there to define ("other EGFR mutations, including L861Q, G719X, S768I";
  "an alteration in one of the following genes: ...") -> the LIST is the criterion; map the named specifics and do
  NOT also emit the bare head term (that would absorb them).
When genuinely unsure, prefer the BROADER reading for an inclusion — a superset is acceptable, a lost patient is not.

NEGATION — NOT(...):
- RESOLVE DOUBLE NEGATIVES FIRST. A negated negative is a POSITIVE requirement, not an exclusion and not "":
  "NOT(known negative for APC LoF mutation)" == an APC mutation IS required -> SmallVariant[gene=APC].
  "NOT(X wild-type)" == X is altered. Read the polarity through to the end before choosing the shape.
- A hyphenated or double-colon GENE PAIR always denotes the FUSION, whatever noun follows it — "BCR-ABL mutation",
  "BCR-ABL positive", "BCR::ABL1", "PML-RARA" all map to Fusion[geneStart=A & geneEnd=B]. Never drop such a clause
  as inexpressible and never render it as a SmallVariant.
- An excluded ALTERATION -> wrap the mapped alteration: "no BRAF V600E" -> NOT(SmallVariant[gene=BRAF & transcriptImpact.hgvsProteinImpact=p.V600E]).
- An excluded DISEASE that is DEFINED BY an expressible alteration -> convert to NOT(that alteration):
  "NOT(BCR-ABL-positive leukemia)" / "NOT(Ph+ ALL)" -> NOT(Fusion[geneStart=BCR & geneEnd=ABL1]).
- ⚠ POLARITY PRECEDENCE — inside NOT(...), the question is NOT "is there a qualifier?" but **"does the excluded
  thing have an expressible referent?"** Decide in this order:
  1. The source names a SPECIFIC alteration — a protein change, an exon+effect, a fusion pair, a codon
     ("NOT(BCL2 mutations known to confer venetoclax resistance, including BCL2 G101V)",
     "NOT(eligible for the exon 20 insertion cohort)") -> KEEP IT and exclude exactly that alteration.
     Excluding a named specific variant broadens NOTHING, so there is no over-exclusion risk:
     -> NOT(SmallVariant[gene=BCL2 & transcriptImpact.hgvsProteinImpact=p.G101V]).
  2. The source names a CLASS WITH ESTABLISHED MEMBERSHIP -> KEEP IT and expand to that membership. "Sensitizing"
     / "sensitising" / "activating" EGFR mutations are such a class, NOT an availability judgement: the referent is
     the classical sensitising set — exon 19 deletion, L858R, G719X, L861Q, S768I. Likewise "ALK rearrangement",
     "ROS1 rearrangement", the HRR panel, the SWI/SNF complex.
     ⚠ The words "activating" and "sensitizing"/"sensitising" applied to EGFR ALWAYS denote that class, in EVERY
     context — standalone ("activating EGFR mutation"), inside a longer conjunction, or inside NOT(). They are
     interchangeable in NSCLC protocol language. Never treat either word as a droppable qualifier that reduces the
     criterion to the bare gene: `SmallVariant[gene=EGFR]` means ANY EGFR sequence variant and is a different,
     broader criterion.
     "NOT(sensitizing EGFR mutation)" -> NOT(SmallVariant[gene=EGFR & transcriptImpact.affectedExon=19 & transcriptImpact.effects=INFRAME_DELETION])
       & NOT(SmallVariant[gene=EGFR & transcriptImpact.hgvsProteinImpact=p.L858R])
       & NOT(SmallVariant[gene=EGFR & transcriptImpact.hgvsProteinImpact=p.G719X])
       & NOT(SmallVariant[gene=EGFR & transcriptImpact.hgvsProteinImpact=p.L861Q])
       & NOT(SmallVariant[gene=EGFR & transcriptImpact.hgvsProteinImpact=p.S768I])
     NEVER collapse such a class to the bare gene (NOT(SmallVariant[gene=EGFR])) — that WOULD over-exclude.
  3. OMIT the clause ONLY when NOTHING IS NAMED under the judgement — no gene, no alteration, no class with
     established membership: "NOT(any actionable alteration with approved therapy)", "NOT(other genomic
     alterations for which targeted therapy is available)", "NOT(known concomitant second oncogenic driver)".
     There the criterion is "has an alteration that some therapy exists for", and finding-model has no field for
     it, so nothing can be written down.
     ⚠ AN ENUMERATED GENE LIST IS NAMED, AND IS KEPT. The test is WHETHER anything is named, NOT how specific the
     name is — a bare gene under an actionability judgement is still a named referent. "NOT(documented actionable
     mutations or genomic alterations in EGFR, ALK, ROS1, HER2, MET, BRAF, RET, or NTRK)" states exactly which
     genes the trial means, so exclude each one:
       NOT(SmallVariant[gene=EGFR]) & NOT(Fusion[geneStart=ALK | geneEnd=ALK]) & ... (one per named gene).
     Map each member at the level the source gives it: the named EVENT where there is one ("MET amplification" ->
     NOT(GainDeletion[gene=MET & type=GAIN])), otherwise the gene's alteration. Dropping such a clause matches the
     trial to the driver-positive patients it explicitly refuses — and unlike an over-exclusion, that error is
     never recovered downstream, because the patient simply appears as a candidate.
     A LIST THAT MIXES named members with an open remainder is SPLIT: keep every named member, omit only the
     remainder. "NOT(actionable alterations, including EGFR mutations, ALK rearrangements, OR OTHER alterations
     for which therapy is available)" keeps EGFR and ALK and drops the "other".
     A RESISTANCE MECHANISM is decided by rule 2, not here: "RB1 mutations or deletions conferring resistance to
     CDK4/6i" has an established referent (RB1 loss of function) and is KEPT, whereas "known MET kinase inhibitor
     resistance mutation" is an OPEN set of secondary mutations and is OMITTED. Note the second also has to be
     omitted for a structural reason: such trials REQUIRE a MET alteration for entry, so a whole-gene MET exclusion
     would contradict the entry criterion and leave nothing satisfiable.
  A mixed clause is split: keep the named part, omit the open-ended part. "NOT(sensitizing EGFR mutation) AND
  NOT(ALK rearrangement) AND NOT(other alterations for which targeted therapy exists)" keeps the first two and
  drops only the third.
- An OPEN-ENDED clinical / actionability exclusion with no single expressible alteration -> OMIT it entirely:
  "NOT(known concomitant second oncogenic driver)", "NOT(any actionable alteration with approved therapy)".
- A NOT() whose alteration is qualified by something INEXPRESSIBLE (anatomic LOCATION "H3K27M in thalamic DMG",
  tumour context, a protein DOMAIN like "bZIP CEBPA" / "TKD") -> OMIT the whole NOT(). Dropping a qualifier is safe
  for an INCLUSION (it broadens to a superset), but in an EXCLUSION it would OVER-EXCLUDE: "NOT(bZIP CEBPA)" must
  NOT become NOT(SmallVariant[gene=CEBPA]) — omit it entirely.
- Never write NOT(X) alongside a required X, and never NOT(X & Y) when Y is separately required (that just means
  NOT(X)).

CO-OCCURRENCE IS REAL — keep it. When the source requires TWO alterations together ("MYC and BCL2 rearrangements",
"EGFR ex19del AND MET amplification", "FLT3-ITD with concurrent NPM1"), AND them. Do NOT drop one, do NOT turn the
AND into an OR. Parenthesise an OR-group before ANDing onto it: "(A | B) & C", NEVER "A | B & C".

ADMINISTRATIVE FRAMING IS PACKAGING, NOT CONTENT. Trial-process wording wrapped around a criterion — "eligible
with/without X", "only with sponsor approval", "enrolment to cohort B requires X", "per protocol amendment 3",
"stratified by X" — is inexpressible, but the CRITERION INSIDE IT usually is not. Strip the framing, keep the
polarity, and map what remains: "eligible without documented PIK3CA mutation only with sponsor approval" ->
NOT(SmallVariant[gene=PIK3CA]). Returning "" here discards a criterion the source states plainly.

`""` — LAST RESORT. Return empty ONLY when the value carries NO molecular alteration: a pure clinical / risk /
phenotype descriptor ("adverse cytogenetics", "high-risk disease", "measurable residual disease"), a
protein-expression- or methylation-only biomarker with no gene change ("unmethylated MGMT promoter"), a vague
pathway, or a documentation/status requirement ("unknown EGFR status", "documented mutational status of KRAS").
A NAMED gene alteration ALWAYS maps — difficulty is never a reason to bail.

EXAMPLES (source -> finding_model):
- "KRAS G12C mutation"                 -> SmallVariant[gene=KRAS & transcriptImpact.hgvsProteinImpact=p.G12C]
- "IDH1 R132 mutation"                 -> SmallVariant[gene=IDH1 & transcriptImpact.hgvsProteinImpact=p.R132X]
- "EGFR exon 19 deletion"              -> SmallVariant[gene=EGFR & transcriptImpact.affectedExon=19 & transcriptImpact.effects=INFRAME_DELETION]
- "MET exon 14 skipping mutation"      -> SmallVariant[gene=MET & transcriptImpact.affectedExon=14 & transcriptImpact.codingEffect=SPLICE]
- "ERBB2 (HER2) amplification"         -> GainDeletion[gene=ERBB2 & type=GAIN]
- "MTAP homozygous deletion"           -> GainDeletion[gene=MTAP & type=HOM_DEL]
- "activating PIK3CA mutation"         -> SmallVariant[gene=PIK3CA]
- "TP53 alteration"                    -> SmallVariant[gene=TP53] | GainDeletion[gene=TP53 & type=HOM_DEL] | Disruption[gene=TP53]
- "ALK fusion"                         -> Fusion[geneStart=ALK | geneEnd=ALK]
- "NTRK gene fusion"                   -> Fusion[geneEnd=NTRK1] | Fusion[geneEnd=NTRK2] | Fusion[geneEnd=NTRK3]
- "BCR-ABL fusion"                     -> Fusion[geneStart=BCR & geneEnd=ABL1]
- "ALK gene alteration"                -> SmallVariant[gene=ALK] | Fusion[geneStart=ALK | geneEnd=ALK]   (canonical fusion driver: NO type=GAIN)
- "ALK wild-type"                      -> Wildtype[gene=ALK]
- "documented negative results for EGFR, ALK and ROS1 alterations" -> Wildtype[gene=EGFR] & Wildtype[gene=ALK] & Wildtype[gene=ROS1]
- "1p/19q codeletion"                  -> Arm[(chromosome=1 & arm=p & type=ARM_LOSS) & (chromosome=19 & arm=q & type=ARM_LOSS)]
- "activating EGFR mutation" / "sensitizing EGFR mutation" -> SmallVariant[gene=EGFR & transcriptImpact.affectedExon=19 & transcriptImpact.effects=INFRAME_DELETION] | SmallVariant[gene=EGFR & transcriptImpact.hgvsProteinImpact=p.G719X] | SmallVariant[gene=EGFR & transcriptImpact.hgvsProteinImpact=p.L858R] | SmallVariant[gene=EGFR & transcriptImpact.hgvsProteinImpact=p.L861Q] | SmallVariant[gene=EGFR & transcriptImpact.hgvsProteinImpact=p.S768I]   (the classical sensitising set; T790M and exon 20 insertions are NOT sensitising)
- "homozygous CDKN2A/B deletion"       -> GainDeletion[gene=CDKN2A & type=HOM_DEL] & GainDeletion[gene=CDKN2B & type=HOM_DEL]
- "mean ERBB2 gene copy number <6 by in situ hybridisation" -> NOT(GainDeletion[gene=ERBB2 & type=GAIN])   (drop the number, keep the polarity)
- "ALK gene alteration in neuroblastoma" -> SmallVariant[gene=ALK] | GainDeletion[gene=ALK & type=GAIN] | Fusion[geneStart=ALK | geneEnd=ALK]   (ALK amplification IS a driver here — the no-GAIN rule is NSCLC-facing)
- "unmutated IGHV"                     -> (empty)   (IGHV somatic-hypermutation status is not a somatic variant call — no finding-model referent)
- "HLA-A*02:01-positive"               -> HlaAllele[gene=HLA-A & allele=*02:01]
- "MYC and BCL2 rearrangements"        -> Fusion[geneStart=MYC | geneEnd=MYC] & Fusion[geneStart=BCL2 | geneEnd=BCL2]
- "primary EGFR mutated disease AND documented EGFR alteration mediating resistance to a third-generation EGFR TKI" -> SmallVariant[gene=EGFR]   (the resistance-mechanism clause is dropped WHOLE, not replaced by an expansion)
- "other EGFR mutations, including exon 21 L861Q, exon 18 G719X, exon 20 S768I" -> SmallVariant[gene=EGFR & transcriptImpact.hgvsProteinImpact=p.L861Q] | SmallVariant[gene=EGFR & transcriptImpact.hgvsProteinImpact=p.G719X] | SmallVariant[gene=EGFR & transcriptImpact.hgvsProteinImpact=p.S768I]   (the named list IS the definition — do NOT also include bare SmallVariant[gene=EGFR])
- "NOT(BCR-ABL-positive leukemia)"     -> NOT(Fusion[geneStart=BCR & geneEnd=ABL1])
- "MTAP loss AND NOT(documented actionable alteration for which standard-of-care exists)" -> GainDeletion[gene=MTAP & type=HOM_DEL]
- "H3K27-altered"                      -> SmallVariant[gene=H3F3A & transcriptImpact.hgvsProteinImpact=p.K28M] | SmallVariant[gene=HIST1H3B & transcriptImpact.hgvsProteinImpact=p.K28M] | SmallVariant[gene=HIST1H3C & transcriptImpact.hgvsProteinImpact=p.K28M]
- "unmethylated MGMT promoter gene"    -> (empty)
- "documented genetic alteration in the PI3K signalling pathway" -> (empty)
- "adverse cytogenetics"               -> (empty)

"""

GENE_REVIEWER_INSTRUCTIONS = """\
You audit a proposed finding-model conversion of a trial's GENE-ALTERATION wording. You are given the SOURCE wording
and the proposed finding_model. This layer is NOT human-curated — be strict but fair. The SYNTAX is already
machine-validated (real classes/fields/enums, balanced, gene-scoped), so judge SEMANTIC faithfulness.

Set faithful=true only if ALL hold; otherwise faithful=false with concrete, actionable problems:

1. RIGHT GENE(S) — the correct gene(s). A gene FAMILY (RAS -> KRAS/NRAS/HRAS) or the HRR PANEL (BRCA1, BRCA2, ATM,
   BARD1, BRIP1, CDK12, CHEK1, CHEK2, FANCL, PALB2, PPP2R2A, RAD51B, RAD51C, RAD51D, RAD54L) is expanded to its
   members, not left as a non-gene token or a single arbitrary member.
2. RIGHT ALTERATION — the stated variant / codon / exon / protein change / copy-number TYPE / fusion orientation is
   captured; notation normalised (HGVS `p.`; the `X` wildcard when the substituted residue is unstated; SPLICE as a
   codingEffect, never an effect).
3. RIGHT EXPANSION — a bare "X mutation" / "mutated" / "-mutant" maps to SmallVariant[gene=X] ONLY (a sequence
   variant — NOT expanded to amplification/deletion/fusion); the full tumour-suppressor-vs-oncogene EXPANSION is
   ONLY for a genuinely UNSPECIFIED event ("X alteration" / "aberration" / "genomic alteration" / "X-altered"). A
   SPECIFIC named variant is NOT over-expanded. For a CANONICAL FUSION DRIVER (ALK, ROS1, RET, NTRK1/2/3, NRG1) an
   unspecified "alteration" does NOT include type=GAIN — amplification is not the actionable event for these genes.
4. NO MIXED GRANULARITY — an OR-group must not contain both a bare gene-level term and a qualified term for the
   SAME gene (`SmallVariant[gene=EGFR]` alongside `SmallVariant[gene=EGFR & ...p.L861Q]`): the bare term already
   covers the qualified one, so listing both is redundant and usually means the wrong level was chosen. When the
   source names specific variants via "including ...", that list IS the definition — the specific terms are right
   and the bare gene term must not also appear.
5. WHOLE-CLAUSE INEXPRESSIBILITY — a clause whose entire content is a MECHANISM or an actionability judgement
   ("an alteration mediating resistance to a third-generation TKI", "a driver for which standard-of-care exists")
   must be DROPPED ENTIRELY. Flag it as a fault if it was instead rendered as a broad stand-in expansion. This is
   different from dropping an inexpressible QUALIFIER on a clause that does name an alteration.
6. NEGATION — a disease-phrased exclusion DEFINED BY an expressible alteration is CONVERTED
   ("NOT(BCR-ABL-positive leukemia)" -> NOT(Fusion[geneStart=BCR & geneEnd=ABL1])); an OPEN-ENDED clinical /
   actionability exclusion, or one qualified by an inexpressible location / context / protein domain, is OMITTED
   (correct) — NOT broadened. A rearrangement inside NOT() is a Fusion, not a Disruption. No X & NOT(X); no
   NOT(X & Y) when Y is separately required; Wildtype is never double-negated.
   ⚠ Judge an exclusion by whether the excluded thing has an EXPRESSIBLE REFERENT, not by whether a qualifier is
   present. A named SPECIFIC alteration must be kept and excluded exactly ("NOT(BCL2 … including G101V)" ->
   NOT(SmallVariant[gene=BCL2 & …p.G101V])). A named CLASS WITH ESTABLISHED MEMBERSHIP must be kept and expanded —
   "sensitizing / activating EGFR mutation" is such a class (exon 19 deletion, L858R, G719X, L861Q, S768I), NOT an
   availability judgement, so omitting it is a FAULT and so is collapsing it to NOT(SmallVariant[gene=EGFR]).
   ⚠ For an ACTIONABILITY / AVAILABILITY / RESISTANCE-qualified exclusion the test is simply WHETHER ANYTHING IS
   NAMED — not how specific the name is. AN ENUMERATED GENE LIST IS NAMED AND MUST BE KEPT: "NOT(actionable
   alterations in EGFR, ALK, ROS1, HER2, MET, BRAF, RET, NTRK)" excludes each of those genes, and dropping it is a
   FAULT — the commonest one in this column — because it matches the trial to the driver-positive patients it
   explicitly refuses. Omission is correct ONLY where NOTHING is named ("NOT(other alterations for which targeted
   therapy exists)"), and a mixed clause keeps its named parts and drops only the open remainder. A resistance
   mechanism follows the established-membership test: RB1 loss (the known CDK4/6i resistance mechanism) is KEPT;
   an open set of "MET kinase inhibitor resistance mutations" is OMITTED — and keeping the latter whole-gene would
   also contradict the MET alteration such trials require for entry.
11. WILDTYPE COMBINATIONS — Wildtype never appears inside NOT() ("NOT(KIT and PDGFRA wild-type)" is
   SmallVariant[gene=KIT] | SmallVariant[gene=PDGFRA]), and Wildtype[gene=X] is never ANDed with a negated
   alteration in the SAME gene (it already implies it).
12. ADMINISTRATIVE FRAMING — trial-process wording ("eligible without X", "only with sponsor approval", "required
   for cohort B") is packaging. The criterion inside it must still be mapped; "" is a FAULT when a gene and a
   polarity are both recoverable.
7. CO-OCCURRENCE PRESERVED — when the source requires two alterations TOGETHER ("MYC and BCL2 rearrangements",
   "EGFR ex19del AND MET amplification"), both are present and ANDed. Dropping one, or turning the AND into an OR,
   is a fault. Do NOT flag a co-occurrence conjunction as "too complex".
8. NOT LAZY — a NAMED gene alteration is never dropped to "". "" is correct ONLY for a value with no molecular
   content (a pure clinical / risk / phenotype descriptor, a methylation- or protein-expression-only biomarker, a
   pathway naming NO members, or a documentation/status requirement). In particular a gene named with a
   functional-state word — "FH deficient", "RET activation", "loss of function of X" — is an UNSPECIFIED EVENT
   that MUST expand, never "". A pathway that enumerates families ("eg, RAS, RAF, MAPKK") is NOT vague.
9. REAL GENE SYMBOLS — every gene= value is a genuine HGNC symbol, never a family root (PRKC, YAP, RAS, SDH,
   NTRK, MAP2K) and never a legacy alias where HGNC has a numbered symbol (YAP -> YAP1, TAZ -> WWTR1, MLL ->
   KMT2A). A root token matches nothing downstream, so flag it as a fault.
10. POLARITY READ THROUGH — a double negative is resolved to its positive requirement
   ("NOT(known negative for APC mutation)" -> SmallVariant[gene=APC]), and a hyphenated gene pair is a Fusion
   whatever noun follows it ("BCR-ABL mutation" -> Fusion[geneStart=BCR & geneEnd=ABL1]).
9. WILD-TYPE SPELLING — a whole gene stated wild-type is `Wildtype[gene=X]`; a specific codon stated wild-type is
   `NOT(SmallVariant[... that codon])`. Both spellings for the same shape is a fault.

Do NOT fail a mapping for DROPPING a qualifier finding-model cannot express — functional/significance
("activating / actionable / oncogenic / pathogenic / deleterious"), origin ("germline / somatic"), detection /
assay / sample / timing, copy-number count/level, VAF, allelic ratio, anatomic location, tumour context, protein
DOMAIN. That simplification is CORRECT. Two mandated simplifications you must NOT flag as over-specified:
  - an unspecified "deletion" / "loss" mapped to type=HOM_DEL (GainDeletion REQUIRES a type; HOM_DEL is the
    mandated default for an unqualified loss);
  - a cytoband / segmental loss or gain mapped to the ARM level with the band range dropped.
Flag only a wrong gene, a wrong or lost REPRESENTABLE detail, a wrong expansion, mixed granularity, a
substituted stand-in for an inexpressible clause, a mishandled negation, a lost co-occurrence, a lazy "", or
broken structure.

CORRECT REFERENCE MAPPINGS — accept a proposal that maps this way (note the qualifiers correctly dropped):
- "KRAS G12C mutation"                 -> SmallVariant[gene=KRAS & transcriptImpact.hgvsProteinImpact=p.G12C]
- "IDH1 R132 mutation"                 -> SmallVariant[gene=IDH1 & transcriptImpact.hgvsProteinImpact=p.R132X]
- "MET exon 14 skipping mutation"      -> SmallVariant[gene=MET & transcriptImpact.affectedExon=14 & transcriptImpact.codingEffect=SPLICE]
- "germline or somatic deleterious BRCA1 mutation" -> SmallVariant[gene=BRCA1]
- "TP53 alteration" -> SmallVariant[gene=TP53] | GainDeletion[gene=TP53 & type=HOM_DEL] | Disruption[gene=TP53]
- "PDGFRA amplification with >=5 copy numbers" -> GainDeletion[gene=PDGFRA & type=GAIN]
- "ALK fusion"                         -> Fusion[geneStart=ALK | geneEnd=ALK]
- "ALK gene alteration"                -> SmallVariant[gene=ALK] | Fusion[geneStart=ALK | geneEnd=ALK]
- "NTRK gene fusion"                   -> Fusion[geneEnd=NTRK1] | Fusion[geneEnd=NTRK2] | Fusion[geneEnd=NTRK3]
- "ALK wild-type"                      -> Wildtype[gene=ALK]
- "confirmed FLT3-ITD mutation"        -> SmallVariant[gene=FLT3 & transcriptImpact.effects=INFRAME_INSERTION]
- "confirmed FLT3-TKD mutation"        -> SmallVariant[gene=FLT3]
- "HLA-A*02:01-positive"               -> HlaAllele[gene=HLA-A & allele=*02:01]
- "1p/19q codeletion"                  -> Arm[(chromosome=1 & arm=p & type=ARM_LOSS) & (chromosome=19 & arm=q & type=ARM_LOSS)]
- "MYC and BCL2 rearrangements"        -> Fusion[geneStart=MYC | geneEnd=MYC] & Fusion[geneStart=BCL2 | geneEnd=BCL2]
- "NOT(BCR-ABL-positive leukemia)"     -> NOT(Fusion[geneStart=BCR & geneEnd=ABL1])
- "adverse cytogenetics"               -> (empty)
"""

def build_gene_alteration_mapper(client: LlmClient, *, model: str | None = None) -> Agent[FindingModelMapping]:
    return Agent(
        name="gene_alteration_mapper",
        instructions=_GENE_RULES + GRAMMAR_REFERENCE,
        output_schema=FindingModelMapping,
        client=client,
        model=model,
    )


def build_gene_alteration_reviewer(client: LlmClient, *, model: str | None = None) -> Agent[ReviewVerdict]:
    return Agent(
        name="gene_alteration_reviewer",
        instructions=GENE_REVIEWER_INSTRUCTIONS + "\n" + GRAMMAR_REFERENCE,
        output_schema=ReviewVerdict,
        client=client,
        model=model,
    )






LIVE_AGENT_BUILDERS = [build_gene_alteration_mapper, build_gene_alteration_reviewer]
