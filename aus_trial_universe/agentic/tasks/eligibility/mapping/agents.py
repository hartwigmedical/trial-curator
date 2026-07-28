"""Mapping-task agents: LLM mapper -> reviewer (spec §6.2).

OncoTree procedure: a mapper converts a cancer-type expression into OncoTree names +
codes (grounded in the real vocabulary from tools/oncotree.py), and a reviewer audits
it. Same doer->reviewer + bounded-refine pattern as extraction. Few-shot examples are
drawn from the curated legacy resource (ConditionsCurationResource / PrimaryTumour).
"""
from __future__ import annotations

from aus_trial_universe.agentic.core.agent import Agent
from aus_trial_universe.agentic.core.client import LlmClient
from aus_trial_universe.agentic.tasks.eligibility.mapping.schema import (
    FindingModelMapping,
    GroupReconciliation,
    OncotreeMapping,
    ReviewVerdict,
)
from aus_trial_universe.agentic.tasks.eligibility.tools.finding_model import GRAMMAR_REFERENCE
from aus_trial_universe.agentic.tasks.eligibility.tools.oncotree import vocab_reference

_ONCOTREE_RULES = """\
You map a clinical trial's cancer/tumour-type expression to OncoTree. Return TWO renderings of the SAME
expression: oncotree_name (using OncoTree NAMES) and oncotree_code (using OncoTree CODES). They must be
structurally identical — same terms, same AND/OR/NOT — differing only in name vs code.

WHAT YOU RECEIVE
The input is the interpreted cancer-type criterion of one trial arm. It is often a full clinical phrase carrying
qualifiers OncoTree cannot express — disease stage/grade; "advanced / metastatic / locally advanced / unresectable
/ recurrent / relapsed / refractory"; line of therapy; treatment-resistance; biomarker status; "histologically
confirmed", etc. Extract the underlying TUMOUR TYPE(s) and map those, DROPPING every qualifier OncoTree has no
field for. Keep only stated tumour-type EXCLUSIONS, as NOT(...).

THE OUTPUT VOCABULARY
Use ONLY codes/names from the VOCABULARY below, OR one of these THREE non-OncoTree sentinels. The sentinels form a
hierarchy — always use the MOST SPECIFIC one that covers the trial's scope:
- "Solid tumour"               — any solid tumour. Use this (NOT Pan-cancer) whenever the scope is solid tumours.
- "Haematological malignancy"  — any blood / lymphoid cancer.
- "Pan-cancer"                 — ONLY when the scope genuinely spans BOTH solid and haematological (e.g. "solid and
                                 haematological malignancies"), or is a truly unspecified "any cancer" (e.g. bare
                                 "Cancer", "Malignant Neoplasm"). NEVER use Pan-cancer for a solid-tumour trial.
If a value is genuinely NOT an oncological condition (a non-cancer disease, a procedure, a therapy, a lab value),
return "" (empty). Difficulty is NOT a reason to bail: a real cancer ALWAYS has at least a sentinel, so never return
"" for a hard-to-place cancer — use the closest node, or the appropriate sentinel.

GRANULARITY — map only as specific as the SOURCE WORDING supports (no broader, no narrower):
- Go as granular as the stated subtype/histology allows: "lung adenocarcinoma" -> LUAD; "clear cell RCC" -> CCRCC;
  "high-grade serous ovarian" -> HGSOC; "PDAC" (ductal adenocarcinoma) -> PAAD.
- When the source names an organ cancer WITHOUT a histology, do NOT infer one — map to the ORGAN node:
  "prostate cancer" -> PROSTATE; "pancreatic cancer" -> PANCREAS; "breast cancer" -> BREAST.
  (Named exception: "colorectal cancer" -> COADREAD, Colorectal Adenocarcinoma.)
- When OncoTree has NO finer node for a stated subtype, use the closest ancestor rather than inventing one:
  "eyelid squamous cell carcinoma" -> SKIN.
- An ANATOMIC / REGIONAL scope OncoTree has no node for ("abdominal", "pelvic", "thoracic", "gastrointestinal
  region", "CNS-located", etc.) is an INEXPRESSIBLE qualifier: map to the appropriate broad SENTINEL (usually
  Solid tumour) ALONE — do NOT approximate the region by enumerating its organs.
- BREAST is a special case (its "Invasive Breast Carcinoma / BRCA" node is a near-synonym of the organ and only
  fragments matching): map generic breast cancer / breast carcinoma / invasive breast cancer / breast
  adenocarcinoma / TNBC / HR- or HER2-status breast cancer ALL to BREAST. Go deeper only when a specific HISTOTYPE
  is named — ductal -> IDC, lobular -> ILC.

LOGICAL STRUCTURE
- Preserve the expression's AND / OR / NOT, mapping only the tumour terms — BUT fix logically invalid input; never
  blindly copy a broken AND/NOT.
- Genuinely different tumour types stated together as alternatives are OR-branches: "AML/MDS" -> AML OR MDS.
- ALWAYS parenthesise an OR-group when you AND anything onto it: write "(A OR B OR C) AND NOT(D)", NEVER
  "A OR B OR C AND NOT(D)" — the unparenthesised form is ambiguous and misreads as "C AND NOT(D)".
- NEVER repeat a code (X AND X = X). NEVER include and exclude the same code (no X AND NOT(X)). NEVER AND a broad
  term with a specific type under it — a broad type and its subtype are OR-alternatives, not AND (a patient has ONE
  tumour): if the trial is clearly the specific type keep only that; if it genuinely spans the group, use OR.
- Keep NOT(...) to a MINIMUM: only negate a genuinely excluded tumour type that has its OWN node. If a NOT() names a
  refinement with no code of its own, OMIT it — never map it to the parent code. A NOT() on a broad SENTINEL is
  valid ONLY when the source's positive scope genuinely IS that whole group ("solid tumours EXCEPT melanoma" ->
  Solid tumour AND NOT(MEL)); if you reached a sentinel by DROPPING a narrower inexpressible scope (an anatomic
  region), DROP the scope-relative exclusion too ("abdominal/pelvic malignancy except vulvar" -> Solid tumour).
- When a NOT() excludes a broad tumour CATEGORY ("sarcomas", "carcinomas", "lymphomas", "neuroendocrine tumours"),
  exclude the BROAD node(s) covering that category, NEVER a narrow "..., NOS" subtype: "NOT(sarcomas)" ->
  NOT(SOFT_TISSUE) AND NOT(BONE), not NOT(SARCNOS).

EXAMPLES (source -> oncotree_name  //  oncotree_code):
- "metastatic NSCLC"                                -> Non-Small Cell Lung Cancer  //  NSCLC
- "advanced solid tumours"                          -> Solid tumour  //  Solid tumour
- "HER2-positive advanced solid tumors"             -> Solid tumour  //  Solid tumour
- "clear cell renal cell cancer (ccRCC)"            -> Renal Clear Cell Carcinoma  //  CCRCC
- "metastatic colorectal cancer"                    -> Colorectal Adenocarcinoma  //  COADREAD
- "metastatic PDAC"                                 -> Pancreatic Adenocarcinoma  //  PAAD
- "metastatic castration-resistant prostate cancer" -> Prostate  //  PROSTATE
- "metastatic breast cancer"                        -> Breast  //  BREAST
- "invasive breast cancer"                          -> Breast  //  BREAST
- "triple-negative breast cancer (TNBC)"            -> Breast  //  BREAST
- "invasive ductal carcinoma of the breast"         -> Breast Invasive Ductal Carcinoma  //  IDC
- "SCC of the oral cavity, oropharynx, or larynx, except nasopharynx" -> (Oral Cavity SCC OR Oropharynx SCC OR Larynx SCC) AND NOT(Nasopharyngeal Carcinoma)  //  (OCSC OR OPHSC OR LXSC) AND NOT(NPC)
- "recurrent high-grade serous ovarian cancer"      -> High-Grade Serous Ovarian Cancer  //  HGSOC
- "B-acute lymphoblastic leukemia (B-ALL)"          -> B-Lymphoblastic Leukemia/Lymphoma  //  BLL
- "multiple myeloma"                                -> Plasma Cell Myeloma  //  PCM
- "AML/MDS"                                          -> Acute Myeloid Leukemia OR Myelodysplastic Syndromes  //  AML OR MDS
- "Haematologic Malignancies"                       -> Haematological malignancy  //  Haematological malignancy
- "advanced non-haematologic malignancy"            -> Solid tumour  //  Solid tumour
- "solid and haematological malignancies"           -> Pan-cancer  //  Pan-cancer
- "eyelid squamous cell carcinoma"                  -> Skin  //  SKIN
- "solid tumours except melanoma"                   -> Solid tumour AND NOT(Melanoma)  //  Solid tumour AND NOT(MEL)
- "abdominal or pelvic malignancy, excluding vulvar cancer" -> Solid tumour  //  Solid tumour
- "Rett syndrome"                                   -> (empty)  //  (empty)

ONCOTREE VOCABULARY (an indented tree — indentation shows the subtype hierarchy: a child is a subtype of its
parent; each node is `Name (CODE)`):
"""

ONCOTREE_REVIEWER_INSTRUCTIONS = """\
You audit a proposed OncoTree mapping of a trial's cancer-type expression. You are given the SOURCE expression and
the proposed oncotree_name / oncotree_code. The full OncoTree VOCABULARY (an indented Name (CODE) tree — a child is
a subtype of its parent) is provided below; USE IT to check both code validity and granularity.

Set faithful=true only if ALL of the following hold; otherwise faithful=false with concrete, actionable problems:

1. VALID CODES — every code is a real OncoTree code from the vocabulary, or one of the three sentinels
   ("Solid tumour", "Haematological malignancy", "Pan-cancer"); a non-cancer value must be "" (empty), not a code.

2. CORRECT NODE — each tumour term maps to the right node (right organ / lineage / histology), not a wrong branch
   (e.g. small-cell vs non-small-cell lung; cholangiocarcinoma vs gallbladder).

3. GRANULARITY — as specific as the SOURCE WORDING supports, no more, no less. Using the vocabulary tree:
   - If a MORE SPECIFIC valid node exists that the source clearly supports, the mapping is UNDER-granular
     (e.g. "non-small cell lung cancer" mapped to LUNG when NSCLC exists).
   - If the mapping is MORE specific than the source states — an INFERRED histology — it is OVER-granular
     (e.g. "prostate cancer", histology unstated, mapped to PRAD instead of the organ node PROSTATE).
   - Organ-node when histology is unstated (prostate→PROSTATE, pancreatic→PANCREAS, breast→BREAST); go granular
     only when the subtype/histology is stated (PDAC→PAAD, serous ovarian→HGSOC, ccRCC→CCRCC). Named exception:
     colorectal cancer→COADREAD. When OncoTree has NO finer node for a stated subtype, the closest ancestor is
     CORRECT (eyelid SCC→SKIN) — do not flag that as under-granular.
   - BREAST special case: generic breast cancer / carcinoma / invasive / adenocarcinoma / TNBC / HR- or HER2-status
     all map to BREAST; flag BRCA / IDC / ILC UNLESS a specific histotype (ductal/lobular) is named.

4. SENTINEL SPECIFICITY — the most specific sentinel is used: "Solid tumour" (NOT Pan-cancer) for a solid-tumour
   scope; "Haematological malignancy" for blood/lymphoid; "Pan-cancer" ONLY when the scope genuinely spans BOTH or
   is a truly unspecified "any cancer". Flag Pan-cancer used for a solid-tumour trial.

5. NOT LAZY — if the SOURCE is a real cancer but the mapping is "" (empty), that is WRONG: a real cancer always has
   at least a sentinel. "" is correct ONLY when the value is genuinely not an oncological condition.

6. COMPLETE + LOGICALLY CONSISTENT — every distinct tumour type the source states is present (do not drop an
   OR-alternative); no "X AND X", no "X AND NOT(X)", no broad term ANDed with a specific type under it (those are
   OR-alternatives, not AND). An OR-group ANDed with anything MUST be parenthesised: "(A OR B) AND NOT(C)", NEVER
   "A OR B AND NOT(C)" (that misreads as "C AND NOT(C)"'s scope). NOT(...) terms are minimal and each names a
   genuinely excluded type with its own node. oncotree_name mirrors oncotree_code (same terms/structure).

DO NOT flag a mapping merely because it DROPPED a qualifier OncoTree cannot express — disease stage/grade,
"advanced/metastatic/recurrent/relapsed/refractory", line of therapy, treatment-resistance, or biomarker status.
Dropping those is CORRECT; flag only a genuinely wrong node, wrong granularity, misused sentinel, lazy "", a
dropped OR-alternative, or broken logic.

CORRECT REFERENCE MAPPINGS — a proposal that maps this way is faithful; accept it (note the qualifiers correctly
dropped and the granularity conventions applied):
- "metastatic NSCLC"                                -> Non-Small Cell Lung Cancer  //  NSCLC
- "advanced solid tumours"                          -> Solid tumour  //  Solid tumour
- "HER2-positive advanced solid tumors"             -> Solid tumour  //  Solid tumour
- "clear cell renal cell cancer (ccRCC)"            -> Renal Clear Cell Carcinoma  //  CCRCC
- "metastatic colorectal cancer"                    -> Colorectal Adenocarcinoma  //  COADREAD
- "metastatic PDAC"                                 -> Pancreatic Adenocarcinoma  //  PAAD
- "metastatic castration-resistant prostate cancer" -> Prostate  //  PROSTATE
- "metastatic breast cancer"                        -> Breast  //  BREAST
- "invasive breast cancer"                          -> Breast  //  BREAST
- "triple-negative breast cancer (TNBC)"            -> Breast  //  BREAST
- "invasive ductal carcinoma of the breast"         -> Breast Invasive Ductal Carcinoma  //  IDC
- "SCC of the oral cavity, oropharynx, or larynx, except nasopharynx" -> (Oral Cavity SCC OR Oropharynx SCC OR Larynx SCC) AND NOT(Nasopharyngeal Carcinoma)  //  (OCSC OR OPHSC OR LXSC) AND NOT(NPC)
- "recurrent high-grade serous ovarian cancer"      -> High-Grade Serous Ovarian Cancer  //  HGSOC
- "B-acute lymphoblastic leukemia (B-ALL)"          -> B-Lymphoblastic Leukemia/Lymphoma  //  BLL
- "multiple myeloma"                                -> Plasma Cell Myeloma  //  PCM
- "AML/MDS"                                          -> Acute Myeloid Leukemia OR Myelodysplastic Syndromes  //  AML OR MDS
- "Haematologic Malignancies"                       -> Haematological malignancy  //  Haematological malignancy
- "advanced non-haematologic malignancy"            -> Solid tumour  //  Solid tumour
- "solid and haematological malignancies"           -> Pan-cancer  //  Pan-cancer
- "eyelid squamous cell carcinoma"                  -> Skin  //  SKIN
- "solid tumours except melanoma"                   -> Solid tumour AND NOT(Melanoma)  //  Solid tumour AND NOT(MEL)
- "abdominal or pelvic malignancy, excluding vulvar cancer" -> Solid tumour  //  Solid tumour
- "Rett syndrome"                                   -> (empty)  //  (empty)

MAPPINGS YOU MUST FAIL (proposed -> problem -> fix):
- SOURCE "non-small cell lung cancer", proposed "Lung // LUNG"
    -> under-granular; NSCLC is supported. fix "Non-Small Cell Lung Cancer // NSCLC"
- SOURCE "prostate cancer", proposed "Prostate Adenocarcinoma // PRAD"
    -> over-granular; histology unstated. fix "Prostate // PROSTATE"
- SOURCE "small cell lung cancer", proposed "Non-Small Cell Lung Cancer // NSCLC"
    -> WRONG node; SCLC and NSCLC are different entities. fix "Small Cell Lung Cancer // SCLC"
- SOURCE "advanced solid tumours", proposed "Pan-cancer // Pan-cancer"
    -> solid-only scope; Pan-cancer misused. fix "Solid tumour // Solid tumour"
- SOURCE "cholangiocarcinoma", proposed "// (empty)"
    -> lazy empty; CHOL exists. fix "Cholangiocarcinoma // CHOL"
- SOURCE "solid tumours including melanoma", proposed "Solid tumour AND Melanoma // Solid tumour AND MEL"
    -> broad term ANDed with its own subtype. fix "Solid tumour OR Melanoma // Solid tumour OR MEL"
- SOURCE "AML/MDS", proposed "Acute Myeloid Leukemia // AML"
    -> dropped the MDS alternative (distinct OR-branches). fix "Acute Myeloid Leukemia OR Myelodysplastic Syndromes // AML OR MDS"
- SOURCE "invasive breast cancer", proposed "Invasive Breast Carcinoma // BRCA"
    -> generic breast (no histotype named) must map to BREAST, not the near-synonym BRCA. fix "Breast // BREAST"
- SOURCE "SCC of oral cavity, oropharynx, or larynx except nasopharynx", proposed "OCSC OR OPHSC OR LXSC AND NOT(NPC)"
    -> unparenthesised OR-group ANDed with an exclusion (ambiguous). fix "(OCSC OR OPHSC OR LXSC) AND NOT(NPC)"
- SOURCE "abdominal or pelvic malignancy, excluding vulvar cancer", proposed "Solid tumour AND NOT(VULVA)"
    -> the anatomic scope is inexpressible so it falls back to the sentinel; the exclusion was relative to that
       dropped scope, not a real carve-out from "all solid tumours". fix "Solid tumour // Solid tumour"
- SOURCE "solid tumours excluding sarcomas", proposed "Solid tumour AND NOT(SARCNOS)"
    -> under-scoped; SARCNOS is only "Sarcoma, NOS". A broad-category exclusion must cover the whole category.
       fix "Solid tumour AND NOT(SOFT_TISSUE) AND NOT(BONE)"

`suggested_fix` — normally leave EMPTY (reporting the problems is enough). ONLY when the input is marked
"[ESCALATION-MODE]", fill it with the concrete corrected oncotree_name // oncotree_code you would expect.

ONCOTREE VOCABULARY (an indented tree — indentation shows the subtype hierarchy: a child is a subtype of its
parent; each node is `Name (CODE)`):
"""


def build_oncotree_mapper(client: LlmClient, *, model: str | None = None) -> Agent[OncotreeMapping]:
    return Agent(
        name="oncotree_mapper",
        instructions=_ONCOTREE_RULES + vocab_reference(),
        output_schema=OncotreeMapping,
        client=client,
        model=model,
    )


def build_oncotree_reviewer(client: LlmClient, *, model: str | None = None) -> Agent[ReviewVerdict]:
    return Agent(
        name="oncotree_reviewer",
        instructions=ONCOTREE_REVIEWER_INSTRUCTIONS + vocab_reference(),   # vocab-grounded: validate codes + granularity
        output_schema=ReviewVerdict,
        client=client,
        model=model,
    )


# --------------------------------------------------------------------------- #
# gene_alteration -> finding-model
# --------------------------------------------------------------------------- #
_GENE_RULES = """\
You convert a clinical trial's GENE-ALTERATION expression into Hartwig finding-model syntax. Return `finding_model`
— the same expression re-expressed in the grammar below, preserving its logical structure (AND `&`, OR `|`,
exclusions wrapped in `NOT(...)`). Follow the grammar EXACTLY: only the listed classes and fields exist.

WHAT YOU RECEIVE
The interpreted gene-alteration criterion of one trial arm. It usually carries wording finding-model CANNOT express;
strip it and map the underlying MOLECULAR ALTERATION. Qualifiers to DROP (dropping them is CORRECT, never a fault):
- FUNCTIONAL / SIGNIFICANCE: "activating", "actionable", "oncogenic", "driver", "pathogenic", "deleterious or
  suspected deleterious", "sensitising", "known/documented", "qualifying".
- ORIGIN: "germline", "somatic", "germline or somatic".
- DETECTION / ASSAY / SAMPLE / TIMING: "detected by ctDNA / central NGS / an FDA-approved assay", "per local
  testing", "in tumour and/or blood", "in a specimen collected after progression on <drug>", "ISH+/NGS-confirmed".
- QUANTITATIVE: copy-number COUNT / level ("≥5 copies", "high-level"), VAF threshold.
- PROTEIN DOMAIN / REGION: "tyrosine kinase domain (TKD)", "bZIP", "kinase domain", "juxtamembrane" — a protein
  sub-region finding-model has no field for. Drop it (map to the gene-level alteration).
Keep only what the grammar has a field for: gene, protein change, exon, effect, copy-number TYPE, fusion
orientation, chromosome arm.

NOTATION — normalise to the grammar's canonical forms:
- Protein change -> HGVS `p.` form ALWAYS: "G12C" -> p.G12C ; "V600E" -> p.V600E ; "Exon21 L858R" -> p.L858R.
- Codon named but the substituted residue UNSTATED (or an explicit "X" / "any") -> the `X` wildcard:
  "IDH1 R132" -> p.R132X ; "KRAS Q61" / "Q61X" -> p.Q61X ; "BRAF V600X" -> p.V600X.
- Exon indels: "exon 19 deletion" -> affectedExon=19 & effects=INFRAME_DELETION ; "exon 20 insertion" ->
  affectedExon=20 & effects=INFRAME_INSERTION.

GRANULARITY — as specific as the wording supports, no more, no less:
- Named protein change / exon / effect -> include it. NEVER invent detail the source does not state.
- Copy number: name the TYPE only — GAIN (amplification / copy-number gain), HOM_DEL (homozygous / biallelic / deep
  deletion or an unspecified "deletion"/"loss" of a gene), HET_DEL (heterozygous / single-copy loss). Drop counts.
- A bare "X mutation" / "X mutated" / "X-mutant" with NO specific variant -> SmallVariant[gene=X] ONLY. "Mutation"
  denotes a SEQUENCE variant (SNV/indel); do NOT expand it to amplification/deletion/fusion (those are different
  events named by their own words). Functional/origin qualifiers still drop ("activating/deleterious/germline X
  mutation" -> SmallVariant[gene=X]). Do NOT expand when a specific variant IS named.
- Only a genuinely UNSPECIFIED event — "X alteration" / "aberration" / "abnormality" / "genomic alteration" /
  "aberrant X" / "X-altered" -> the EXPANSION RULE (tumour-suppressor vs oncogene) in the grammar.

ALTERATION VOCABULARY (word -> class):
- amplification / copy-number gain             -> GainDeletion[gene=X & type=GAIN]
- homozygous|biallelic|deep deletion / loss    -> GainDeletion[gene=X & type=HOM_DEL]
- heterozygous / single-copy loss              -> GainDeletion[gene=X & type=HET_DEL]
- rearrangement / translocation / gene fusion / fusion -> Fusion (NEVER Disruption; this holds inside NOT() too).
  One gene, orientation unknown: Fusion[geneStart=X | geneEnd=X]. Named partners "A::B" / "A-B fusion":
  Fusion[geneStart=A & geneEnd=B]. A family of 3' partners (e.g. NTRK) -> OR one Fusion per member.
- MET exon 14 skipping (the exon IS stated) -> SmallVariant[gene=MET & transcriptImpact.affectedExon=14 & transcriptImpact.effects=SPLICE]
- FLT3-ITD (internal tandem duplication) -> SmallVariant[gene=FLT3 & transcriptImpact.effects=INFRAME_INSERTION]
  (do NOT add an exon — the source does not state one; never infer it).
- FLT3-TKD (tyrosine-kinase-domain mutation) -> SmallVariant[gene=FLT3] (TKD is an inexpressible protein DOMAIN;
  drop it — do NOT invent an exon).
- chromosome-arm change: "1p loss / deletion / LOH" -> Arm[chromosome=1 & arm=p & type=ARM_LOSS] ; "1q gain" ->
  type=ARM_GAIN ; codeletion "1p/19q" -> Arm[chromosome=1 & arm=p & type=ARM_LOSS] & Arm[chromosome=19 & arm=q & type=ARM_LOSS].
  A cytoband / SEGMENTAL loss or gain maps to the ARM level (finding-model has no field for a band RANGE, so drop
  it): "9p21.1-24.3 loss" -> Arm[chromosome=9 & arm=p & type=ARM_LOSS]. KEEP it (never omit); this holds inside NOT() too.

WILD-TYPE:
- A WHOLE gene stated wild-type / non-mutated -> Wildtype[gene=X] (one per gene; AND several together:
  Wildtype[gene=EGFR] & Wildtype[gene=ALK]).
- A SPECIFIC codon/variant stated wild-type ("KRAS G12/G13 wild-type", "BRAF V600 wild-type") is expressible and
  more faithful as the negation of that specific change: NOT(SmallVariant[gene=KRAS & transcriptImpact.hgvsProteinImpact=p.G12X])
  & NOT(SmallVariant[gene=KRAS & transcriptImpact.hgvsProteinImpact=p.G13X]).

GENE FAMILIES / PANELS / PATHWAY TOKENS:
- A recognised gene FAMILY (not a single gene) -> expand to its member genes OR'd, applying the SAME variant to
  each. RAS = KRAS, NRAS, HRAS.
- The HRR / HRR-gene / "homologous recombination repair gene" PANEL -> expand to these 15 genes OR'd (the PROfound
  panel): BRCA1, BRCA2, ATM, BARD1, BRIP1, CDK12, CHEK1, CHEK2, FANCL, PALB2, PPP2R2A, RAD51B, RAD51C, RAD51D,
  RAD54L. Apply the word logic per gene: "HRR gene MUTATION" -> SmallVariant[gene=BRCA1] | SmallVariant[gene=BRCA2]
  | ... (one SmallVariant per gene); "HRR gene ALTERATION" -> the full TSG expansion per gene
  (SmallVariant | GainDeletion[type=HOM_DEL] | Disruption, OR'd across all 15). NB: "HRR deficiency" / "HRD" is the
  functional SIGNATURE (homologousRecombination[ChordStatus=HR_DEFICIENT]), a molecular_signature — NOT this panel.
- A vague PATHWAY with no defined gene set ("RAS/MAPK pathway alteration") -> "".

NEGATION — NOT(...):
- An excluded ALTERATION -> wrap the mapped alteration: "no BRAF V600E" -> NOT(SmallVariant[gene=BRAF & transcriptImpact.hgvsProteinImpact=p.V600E]).
- An excluded DISEASE that is DEFINED BY an expressible alteration -> convert to NOT(that alteration):
  "NOT(BCR-ABL-positive leukemia)" / "NOT(Ph+ ALL)" -> NOT(Fusion[geneStart=BCR & geneEnd=ABL1]).
- An OPEN-ENDED clinical / actionability exclusion with no single expressible alteration -> OMIT it entirely:
  "NOT(known concomitant second oncogenic driver)", "NOT(any actionable alteration with approved therapy)",
  "NOT(tumours with a targetable alteration)". Omitting an inexpressible NOT() is CORRECT.
- A NOT() whose alteration is qualified by something INEXPRESSIBLE (anatomic LOCATION "H3K27M in thalamic DMG",
  tumour context, a protein DOMAIN like "bZIP CEBPA" / "TKD") -> OMIT the whole NOT(). Dropping a qualifier is safe
  for an INCLUSION (it broadens to a superset, which is acceptable), but in an EXCLUSION it would OVER-EXCLUDE:
  e.g. "NOT(bZIP CEBPA)" must NOT become NOT(SmallVariant[gene=CEBPA]) (that wrongly excludes ALL CEBPA variants) —
  omit it entirely. Likewise never encode NOT(same-variant) as an included term, which would self-contradict.
- If the source's inclusion and exclusion of an alteration apply to DIFFERENT cancer types/cohorts, that was split
  into separate rows upstream — here map only what genuinely applies to this row.

LOGICAL CONSISTENCY:
- Preserve AND/OR/NOT but FIX broken logic — never blindly copy an invalid structure.
- Parenthesise an OR-group before ANDing onto it: "(A | B) & C", NEVER "A | B & C".
- Never duplicate a term (X & X = X); never require and exclude the same term (no X & NOT(X)).
- Order terms SmallVariant, GainDeletion, Disruption, Fusion.

`""` — LAST RESORT. Return empty ONLY when the value carries NO molecular alteration: a pure clinical / risk /
phenotype descriptor ("adverse cytogenetics", "high-risk disease", "measurable residual disease", a protein-
expression-only biomarker with no underlying gene change). A NAMED gene alteration ALWAYS maps — difficulty is
never a reason to bail.

EXAMPLES (source -> finding_model):
- "KRAS G12C mutation"                 -> SmallVariant[gene=KRAS & transcriptImpact.hgvsProteinImpact=p.G12C]
- "IDH1 R132 mutation"                 -> SmallVariant[gene=IDH1 & transcriptImpact.hgvsProteinImpact=p.R132X]
- "EGFR exon 19 deletion"              -> SmallVariant[gene=EGFR & transcriptImpact.affectedExon=19 & transcriptImpact.effects=INFRAME_DELETION]
- "EGFR exon 20 insertion"             -> SmallVariant[gene=EGFR & transcriptImpact.affectedExon=20 & transcriptImpact.effects=INFRAME_INSERTION]
- "MET exon 14 skipping mutation"      -> SmallVariant[gene=MET & transcriptImpact.affectedExon=14 & transcriptImpact.effects=SPLICE]
- "ERBB2 (HER2) amplification"         -> GainDeletion[gene=ERBB2 & type=GAIN]
- "PDGFRA amplification with >=5 copy numbers" -> GainDeletion[gene=PDGFRA & type=GAIN]
- "MTAP homozygous deletion"           -> GainDeletion[gene=MTAP & type=HOM_DEL]
- "activating PIK3CA mutation"         -> SmallVariant[gene=PIK3CA]   ("mutation" = sequence variant; do NOT add amplification)
- "germline or somatic deleterious or suspected deleterious BRCA1 mutation" -> SmallVariant[gene=BRCA1]
- "KRAS mutation"                      -> SmallVariant[gene=KRAS]
- "TP53 alteration"                    -> SmallVariant[gene=TP53] | GainDeletion[gene=TP53 & type=HOM_DEL] | Disruption[gene=TP53]   ("alteration" is UNSPECIFIED -> full TSG expansion)
- "deleterious mutation in an HRR gene" -> SmallVariant[gene=BRCA1] | SmallVariant[gene=BRCA2] | SmallVariant[gene=ATM] | SmallVariant[gene=BARD1] | SmallVariant[gene=BRIP1] | SmallVariant[gene=CDK12] | SmallVariant[gene=CHEK1] | SmallVariant[gene=CHEK2] | SmallVariant[gene=FANCL] | SmallVariant[gene=PALB2] | SmallVariant[gene=PPP2R2A] | SmallVariant[gene=RAD51B] | SmallVariant[gene=RAD51C] | SmallVariant[gene=RAD51D] | SmallVariant[gene=RAD54L]
- "ALK fusion"                         -> Fusion[geneStart=ALK | geneEnd=ALK]
- "NTRK gene fusion"                   -> Fusion[geneEnd=NTRK1] | Fusion[geneEnd=NTRK2] | Fusion[geneEnd=NTRK3]
- "BCR-ABL fusion"                     -> Fusion[geneStart=BCR & geneEnd=ABL1]
- "ALK wild-type"                      -> Wildtype[gene=ALK]
- "KRAS G12/G13 wild-type"             -> NOT(SmallVariant[gene=KRAS & transcriptImpact.hgvsProteinImpact=p.G12X]) & NOT(SmallVariant[gene=KRAS & transcriptImpact.hgvsProteinImpact=p.G13X])
- "RAS Q61X mutation"                  -> SmallVariant[gene=KRAS & transcriptImpact.hgvsProteinImpact=p.Q61X] | SmallVariant[gene=NRAS & transcriptImpact.hgvsProteinImpact=p.Q61X] | SmallVariant[gene=HRAS & transcriptImpact.hgvsProteinImpact=p.Q61X]
- "NOT(BCR-ABL-positive leukemia)"     -> NOT(Fusion[geneStart=BCR & geneEnd=ABL1])
- "MTAP loss AND NOT(documented actionable alteration for which standard-of-care exists)" -> GainDeletion[gene=MTAP & type=HOM_DEL]
- "H3K27-altered"                      -> SmallVariant[gene=H3F3A & transcriptImpact.hgvsProteinImpact=p.K28M] | SmallVariant[gene=HIST1H3B & transcriptImpact.hgvsProteinImpact=p.K28M] | SmallVariant[gene=HIST1H3C & transcriptImpact.hgvsProteinImpact=p.K28M]
- "H3K27-altered AND BRAF V600E"       -> (SmallVariant[gene=H3F3A & transcriptImpact.hgvsProteinImpact=p.K28M] | SmallVariant[gene=HIST1H3B & transcriptImpact.hgvsProteinImpact=p.K28M] | SmallVariant[gene=HIST1H3C & transcriptImpact.hgvsProteinImpact=p.K28M]) & SmallVariant[gene=BRAF & transcriptImpact.hgvsProteinImpact=p.V600E]
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
2. RIGHT ALTERATION — the stated variant / exon / protein change / copy-number TYPE / fusion orientation is
   captured; notation normalised (HGVS `p.`; the `X` wildcard when the substituted residue is unstated).
3. RIGHT EXPANSION — a bare "X mutation" / "mutated" / "-mutant" maps to SmallVariant[gene=X] ONLY (a sequence
   variant — NOT expanded to amplification/deletion/fusion); the full tumour-suppressor-vs-oncogene EXPANSION is
   ONLY for a genuinely UNSPECIFIED event ("X alteration" / "aberration" / "genomic alteration" / "X-altered"). A
   SPECIFIC named variant is NOT over-expanded.
4. NEGATION — a disease-phrased exclusion DEFINED BY an expressible alteration is CONVERTED
   ("NOT(BCR-ABL-positive leukemia)" -> NOT(Fusion[geneStart=BCR & geneEnd=ABL1])); an OPEN-ENDED clinical /
   actionability exclusion, or one qualified by an inexpressible location / context / protein domain, is OMITTED
   (correct) — NOT broadened (dropping a qualifier inside an exclusion would OVER-exclude, e.g. NOT(bZIP CEBPA) must
   not become NOT(SmallVariant[gene=CEBPA])). A rearrangement inside NOT() is a Fusion, not a Disruption. No X & NOT(X).
5. NOT LAZY — a NAMED gene alteration is never dropped to "". "" is correct ONLY for a value with no molecular
   content (a pure clinical / risk / phenotype descriptor).
6. COMPLETE + CONSISTENT — every stated alteration is present (no dropped OR-alternative); the AND/OR/NOT structure
   matches the source; an OR-group ANDed with anything is parenthesised.

Do NOT fail a mapping for DROPPING a qualifier finding-model cannot express — functional/significance
("activating / actionable / oncogenic / pathogenic / deleterious"), origin ("germline / somatic"), detection /
assay / sample / timing, copy-number count/level, VAF, anatomic location, tumour context, protein DOMAIN. That
simplification is CORRECT. Two mandated simplifications you must NOT flag as over-specified / over-broadened:
  - an unspecified "deletion" / "loss" mapped to type=HOM_DEL (GainDeletion REQUIRES a type; HOM_DEL is the
    mandated default for an unqualified loss — "MTAP loss" -> GainDeletion[gene=MTAP & type=HOM_DEL] is correct);
  - a cytoband / segmental loss or gain mapped to the ARM level with the band range dropped
    ("9p21.1-24.3 loss" -> Arm[chromosome=9 & arm=p & type=ARM_LOSS] is correct; do not demand the cytoband).
Flag only a wrong gene, a wrong or lost REPRESENTABLE detail, a wrong expansion, a mishandled negation, a lazy "",
or broken structure.

CORRECT REFERENCE MAPPINGS — accept a proposal that maps this way (note the qualifiers correctly dropped):
- "KRAS G12C mutation"                 -> SmallVariant[gene=KRAS & transcriptImpact.hgvsProteinImpact=p.G12C]
- "IDH1 R132 mutation"                 -> SmallVariant[gene=IDH1 & transcriptImpact.hgvsProteinImpact=p.R132X]
- "EGFR exon 20 insertion"             -> SmallVariant[gene=EGFR & transcriptImpact.affectedExon=20 & transcriptImpact.effects=INFRAME_INSERTION]
- "MET exon 14 skipping mutation"      -> SmallVariant[gene=MET & transcriptImpact.affectedExon=14 & transcriptImpact.effects=SPLICE]
- "germline or somatic deleterious BRCA1 mutation" -> SmallVariant[gene=BRCA1]   ("mutation" -> SmallVariant only)
- "TP53 alteration" -> SmallVariant[gene=TP53] | GainDeletion[gene=TP53 & type=HOM_DEL] | Disruption[gene=TP53]   ("alteration" -> full expansion)
- "PDGFRA amplification with >=5 copy numbers" -> GainDeletion[gene=PDGFRA & type=GAIN]
- "ALK fusion"                         -> Fusion[geneStart=ALK | geneEnd=ALK]
- "NTRK gene fusion"                   -> Fusion[geneEnd=NTRK1] | Fusion[geneEnd=NTRK2] | Fusion[geneEnd=NTRK3]
- "ALK wild-type"                      -> Wildtype[gene=ALK]
- "confirmed FLT3-ITD mutation"        -> SmallVariant[gene=FLT3 & transcriptImpact.effects=INFRAME_INSERTION]
- "confirmed FLT3-TKD mutation"        -> SmallVariant[gene=FLT3]   (TKD is a protein domain; drop it, no exon)
- "RAS Q61X mutation"                  -> SmallVariant[gene=KRAS & transcriptImpact.hgvsProteinImpact=p.Q61X] | SmallVariant[gene=NRAS & transcriptImpact.hgvsProteinImpact=p.Q61X] | SmallVariant[gene=HRAS & transcriptImpact.hgvsProteinImpact=p.Q61X]
- "NOT(BCR-ABL-positive leukemia)"     -> NOT(Fusion[geneStart=BCR & geneEnd=ABL1])
- "adverse cytogenetics"               -> (empty)

MAPPINGS YOU MUST FAIL (proposed -> problem -> fix):
- SOURCE "KRAS G12C mutation", proposed "SmallVariant[gene=KRAS]"
    -> lost the protein change. fix "...& transcriptImpact.hgvsProteinImpact=p.G12C"
- SOURCE "RAS Q61X mutation", proposed "SmallVariant[gene=KRAS & transcriptImpact.hgvsProteinImpact=p.Q61X]"
    -> gene family not expanded; NRAS and HRAS are missing. fix the 3-gene OR.
- SOURCE "IDH1 R132 mutation", proposed "SmallVariant[gene=IDH1]"
    -> lost the codon; an unstated residue is the X wildcard. fix "...hgvsProteinImpact=p.R132X"
- SOURCE "NOT(BCR-ABL-positive leukemia)", proposed "" (or the disease left as text)
    -> the disease is defined by the BCR::ABL1 fusion; convert it. fix "NOT(Fusion[geneStart=BCR & geneEnd=ABL1])"
- SOURCE "cholangiocarcinoma with FGFR2 fusion", proposed ""
    -> lazy empty; the FGFR2 fusion is expressible. fix "Fusion[geneStart=FGFR2 | geneEnd=FGFR2]"
- SOURCE "EGFR exon 20 insertion", proposed "SmallVariant[gene=EGFR]"
    -> lost the exon + effect. fix "...affectedExon=20 & transcriptImpact.effects=INFRAME_INSERTION"
- SOURCE "ALK wild-type", proposed "NOT(SmallVariant[gene=ALK])"
    -> a whole-gene wild-type uses the Wildtype class. fix "Wildtype[gene=ALK]"
- SOURCE "KRAS amplification with >=5 copies", proposed problems=["dropped the copy count"]
    -> dropping the copy count is CORRECT; do NOT fail for it.
- SOURCE "EGFR mutation", proposed "SmallVariant[gene=EGFR] | GainDeletion[gene=EGFR & type=GAIN]"
    -> "mutation" is a sequence variant; amplification must NOT be added. fix "SmallVariant[gene=EGFR]"
- SOURCE "HRR gene mutation", proposed "SmallVariant[gene=BRCA1] | SmallVariant[gene=BRCA2]"
    -> the HRR panel is 15 genes; only 2 listed. fix the full 15-gene SmallVariant OR (BRCA1/2, ATM, BARD1, BRIP1,
       CDK12, CHEK1/2, FANCL, PALB2, PPP2R2A, RAD51B/C/D, RAD54L).
- SOURCE "FLT3-ITD", proposed "SmallVariant[gene=FLT3 & transcriptImpact.affectedExon=14 & transcriptImpact.effects=INFRAME_INSERTION]"
    -> the source does not state an exon; do not infer it. fix "SmallVariant[gene=FLT3 & transcriptImpact.effects=INFRAME_INSERTION]"
- SOURCE "NOT(bZIP CEBPA mutation)", proposed "NOT(SmallVariant[gene=CEBPA])"
    -> a domain-qualified exclusion was broadened; this over-excludes ALL CEBPA variants. fix: OMIT the NOT() entirely.
- SOURCE "NOT(MYC and BCL2 rearrangement)", proposed "NOT(Disruption[gene=MYC] & Disruption[gene=BCL2])"
    -> a rearrangement is a Fusion, not a Disruption. fix "NOT(Fusion[geneStart=MYC | geneEnd=MYC] & Fusion[geneStart=BCL2 | geneEnd=BCL2])"

`suggested_fix` — normally leave EMPTY (reporting the problems is enough). ONLY when the input is marked
"[ESCALATION-MODE]", fill it with the concrete corrected finding_model you would expect.
"""

_SIGNATURE_RULES = """\
You convert a clinical trial's MOLECULAR-SIGNATURE expression into Hartwig finding-model syntax. Return
`finding_model`, preserving the logical structure (AND `&`, OR `|`, exclusions wrapped in `NOT(...)`).

There are EXACTLY SIX signature terms — the ONLY output vocabulary. Map a genuine signature to its term,
recognising the common synonyms:
- MicrosatelliteStability[PurpleMicrosatelliteStatus=MSI]  <- MSI-high / MSI-H / MSI / dMMR / MMRd / mismatch-repair
    deficient / MMR-deficient / MSI-L / microsatellite instability-low / HNPCC / Lynch syndrome / constitutional MMR deficiency.
- MicrosatelliteStability[PurpleMicrosatelliteStatus=MSS]  <- MSS / microsatellite stable / pMMR / MMR-proficient / normal MMR.
- homologousRecombination[ChordStatus=HR_DEFICIENT]        <- HRD / HRD-positive / homologous-recombination deficient /
    HRR deficiency / HRRm / BRCAness / FH-deficient / SDH-deficient.
- homologousRecombination[ChordStatus=HR_PROFICIENT]       <- HR proficient / HRR proficient / HR-repair non-mutated.
- tumorMutationBurden[Status=HIGH]                         <- TMB-high / TMB-H / high tumour mutational BURDEN.
- tumorMutationLoad[Status=HIGH]                           <- high mutational LOAD / TML-high / high tumour mutational load.
(TMB "burden" -> tumorMutationBurden; "load"/TML -> tumorMutationLoad. Only Status=HIGH exists — a stated
"low/normal TMB/TML" requirement is expressible only as NOT(...[Status=HIGH]).)

DROP inexpressible qualifiers, then map the underlying signature: thresholds/levels ("≥100 somatic SNVs/exome",
"moderate to high"), assay/detection ("by NGS", "centrally confirmed"), treatment/timing context, "high TILs/TLS".

`""` (EMPTY) is CORRECT and COMMON here — return it whenever the value is NOT one of the six signatures. The
molecular-signature column carries MANY non-signature values; map ALL of these to "":
- risk / prognostic scores & categories: "IPI 3-5", "IPSS intermediate-2/high", "FLIPI 2-5", "Oncotype DX RS 11-25",
  "cytogenetic high-risk", "adverse/standard/favourable biology", "high-risk", "complex karyotype".
- expression / molecular SUBTYPES: "Luminal A", "PAM50", "CMS4", "SHH", "triple-negative/TNBC", "non-secretory".
- a GENE ALTERATION or chromosomal event (belongs to gene_alteration): "1p/19q-codeletion", "H3/IDH-wildtype",
  "Ph-like", "HPV", "LOH", "MYCN amplification".
- a protein-expression / receptor BIOMARKER: "HER2-", "HR+", "hormone receptor", "PD-L1".
Do NOT force any of these into a signature term. But do NOT drop a GENUINE signature — "dMMR" IS MSI, "FH-deficient"
IS HR_DEFICIENT; difficulty recognising a synonym is not a reason to bail on a real signature.

NEGATION — an excluded signature wraps its term: "MSS required, exclude MSI-H" side / "NOT(MSI-H)" ->
NOT(MicrosatelliteStability[PurpleMicrosatelliteStatus=MSI]). A signature qualified in an EXCLUSION by an
inexpressible condition ("NOT(MSI-H without prior immune checkpoint inhibitor)") — omit the inexpressible qualifier
only if that does not over-exclude; otherwise keep the bare signature negation.

EXAMPLES (source -> finding_model):
- "MSI-high"                                 -> MicrosatelliteStability[PurpleMicrosatelliteStatus=MSI]
- "dMMR/MSI-H"                                -> MicrosatelliteStability[PurpleMicrosatelliteStatus=MSI]
- "mismatch repair proficient (pMMR)"        -> MicrosatelliteStability[PurpleMicrosatelliteStatus=MSS]
- "HRD-positive"                             -> homologousRecombination[ChordStatus=HR_DEFICIENT]
- "FH deficient"                             -> homologousRecombination[ChordStatus=HR_DEFICIENT]
- "TMB-high"                                 -> tumorMutationBurden[Status=HIGH]
- "high mutational load (>100 somatic SNVs/exome)" -> tumorMutationLoad[Status=HIGH]
- "NOT(MSI-H)"                               -> NOT(MicrosatelliteStability[PurpleMicrosatelliteStatus=MSI])
- "adverse biology"                          -> (empty)
- "IPSS intermediate-2 or high-risk"         -> (empty)
- "Luminal A"                                -> (empty)
- "1p/19q-codeletion"                        -> (empty)   (a chromosomal alteration, not a signature)
- "HER2-negative"                            -> (empty)   (an expression biomarker, not a signature)

"""

SIGNATURE_REVIEWER_INSTRUCTIONS = """\
You audit a proposed finding-model conversion of a MOLECULAR-SIGNATURE value. You are given the SOURCE value and the
proposed finding_model. There are EXACTLY SIX valid signature terms (MSI/MSS microsatellite status, HR_DEFICIENT/
HR_PROFICIENT, tumorMutationBurden HIGH, tumorMutationLoad HIGH).

Set faithful=true only if ALL hold; otherwise faithful=false with concrete, actionable problems:
1. RIGHT TERM — a genuine signature uses the correct class + status, recognising synonyms (dMMR/MMRd/Lynch -> MSI;
   pMMR -> MSS; HRD/HRR-deficient/FH-/SDH-deficient -> HR_DEFICIENT; TMB "burden" vs "load" kept distinct).
2. EMPTY IS CORRECT FOR NON-SIGNATURES — a risk/prognostic score, an expression subtype, a gene/chromosomal
   alteration, or a protein-expression biomarker MUST be "" — NOT forced into a signature term. Flag a HALLUCINATED
   signature (a non-signature value mapped to one of the six terms).
3. NOT LAZY — a GENUINE signature must NOT be dropped to "" (dMMR -> MSI, not empty).
4. NEGATION — an excluded signature is wrapped in NOT(); structure matches the source.

Do NOT fail a mapping for dropping an inexpressible qualifier (threshold/level, assay, timing, "high TILs"). "" is
the expected answer for the many non-signature values — do NOT demand a mapping for them.

CORRECT REFERENCE MAPPINGS — accept these:
- "dMMR/MSI-H" -> MicrosatelliteStability[PurpleMicrosatelliteStatus=MSI]
- "FH deficient" -> homologousRecombination[ChordStatus=HR_DEFICIENT]
- "high mutational load (>100 somatic SNVs/exome)" -> tumorMutationLoad[Status=HIGH]
- "NOT(MSI-H)" -> NOT(MicrosatelliteStability[PurpleMicrosatelliteStatus=MSI])
- "adverse biology" -> (empty)      - "Luminal A" -> (empty)      - "1p/19q-codeletion" -> (empty)

MAPPINGS YOU MUST FAIL (proposed -> problem -> fix):
- SOURCE "dMMR", proposed "" -> lazy empty; dMMR IS MSI. fix "MicrosatelliteStability[PurpleMicrosatelliteStatus=MSI]".
- SOURCE "cytogenetic high-risk", proposed "tumorMutationBurden[Status=HIGH]" -> hallucinated; a risk category is not
  a signature. fix "" (empty).
- SOURCE "HER2-negative", proposed "MicrosatelliteStability[...]" -> a biomarker, not a signature. fix "" (empty).
- SOURCE "high mutational BURDEN", proposed "tumorMutationLoad[Status=HIGH]" -> burden is tumorMutationBurden.
  fix "tumorMutationBurden[Status=HIGH]".

`suggested_fix` — normally leave EMPTY. ONLY when the input is marked "[ESCALATION-MODE]", fill it with the concrete
corrected finding_model you would expect.
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


def build_molecular_signature_mapper(client: LlmClient, *, model: str | None = None) -> Agent[FindingModelMapping]:
    return Agent(
        name="molecular_signature_mapper",
        instructions=_SIGNATURE_RULES,
        output_schema=FindingModelMapping,
        client=client,
        model=model,
    )


def build_molecular_signature_reviewer(client: LlmClient, *, model: str | None = None) -> Agent[ReviewVerdict]:
    return Agent(
        name="molecular_signature_reviewer",
        instructions=SIGNATURE_REVIEWER_INSTRUCTIONS,
        output_schema=ReviewVerdict,
        client=client,
        model=model,
    )


# --------------------------------------------------------------------------- #
# STEP 2 — cross-value reconciliation adjudicators (doer -> reviewer per flagged group).
# Prompts finalised with the user 2026-07-28. cancer_type uses OncoTree; gene/signature reuse the same rules with
# the finding-model grammar. Grounding (vocab / grammar) is appended by the builders.
# --------------------------------------------------------------------------- #
_ONCOTREE_RECONCILE_RULES = """\
You reconcile the OncoTree mapping of a GROUP of cancer-type phrasings that a consistency check flagged as ONE
underlying concept but which received DIFFERENT OncoTree codes when each was mapped independently (Step 1 mapped each
value alone, with no sight of its siblings). Return the FINAL value (an OncoTree code expression) for EACH member.

- EQUIVALENT members — same tumour scope, differing only in phrasing / synonym / dropped qualifier-noise — MUST share
  ONE final code: the code that best represents the shared concept (normally the most-specific OncoTree node the
  group's wording jointly supports). e.g. "lung cancer" / "NSCLC" / "non-small cell lung carcinoma" -> all NSCLC;
  "anaplastic astrocytoma" mapped once to HGGNOS and once to DIFG -> pick the ONE correct node for both.
- GENUINELY-DISTINCT members — the group over-merged a difference OncoTree DOES encode (grade, histology subtype,
  distinct organ / lineage) — MUST keep each member's own correct code. e.g. "astrocytoma grade 3" -> ASTR3 and
  "astrocytoma grade 4" -> ASTR4 stay DIFFERENT; do NOT collapse a real distinction to force agreement.
- A difference OncoTree canNOT encode (laterality, stage, "advanced/metastatic/recurrent") is NOT a real distinction
  -> unify those members.
- Fix any Step-1 code that is simply the WRONG node while you are here; obey the mapper's conventions (granularity =
  the most-specific node covering the wording; the 3 sentinels; organ-node when histology unstated). Preserve each
  member's AND / OR / NOT() structure and OR-branch content — you are reconciling the CODES, not re-deriving the logic.

Return every input from the group exactly once, with its FINAL OncoTree code expression.

ONCOTREE VOCABULARY (an indented tree — indentation shows the subtype hierarchy; each node is `Name (CODE)`):
"""

ONCOTREE_RECONCILE_REVIEWER_INSTRUCTIONS = """\
You audit a reconciliation decision for a flagged group of cancer-type phrasings (each with its FINAL OncoTree code).
Set faithful=true only if ALL hold; otherwise faithful=false with concrete problems (which members, which code, why):
1. EQUIVALENT members now share ONE code (phrasing / synonym / qualifier-noise / laterality / stage differences were
   unified — not left divergent).
2. GENUINELY-DISTINCT members are kept apart, each with its own correct code (a real OncoTree-encodable difference —
   grade / histology subtype / distinct organ — was NOT collapsed to force agreement).
3. Each unifying code is the MOST-SPECIFIC node correctly covering all its members (not an over-broad umbrella, not
   an inferred subtype the wording doesn't support).
4. Every final code is a VALID OncoTree code (or a sentinel), following the granularity conventions.
5. Each member's AND / OR / NOT() structure is preserved.

`suggested_fix` — normally leave EMPTY. ONLY when the input is marked "[ESCALATION-MODE]", fill it with the concrete
corrected per-member codes you would expect.

ONCOTREE VOCABULARY (an indented tree — indentation shows the subtype hierarchy; each node is `Name (CODE)`):
"""

_FINDINGMODEL_RECONCILE_RULES = """\
You reconcile the finding-model mapping of a GROUP of gene-alteration / molecular-signature phrasings that a
consistency check flagged as ONE underlying concept but which received DIFFERENT finding-model expressions when each
was mapped independently. Return the FINAL finding-model expression for EACH member.

- EQUIVALENT members (same alteration, differing only in phrasing / synonym / dropped inexpressible qualifier) MUST
  share ONE final expression (the correct one, per the grammar conventions). e.g. two phrasings of the same fusion
  that got different orientations -> the one correct rendering.
- GENUINELY-DISTINCT members (a real difference the grammar DOES encode — different gene, variant, exon,
  copy-number type, fusion orientation) MUST keep their own correct expression; do NOT collapse a real distinction.
- Fix any expression that is simply wrong while you are here; obey the mapper's conventions (mutation -> SmallVariant
  only; families/panels expanded; notation normalised). Preserve each member's AND / OR / NOT() structure.

Return every input from the group exactly once, with its FINAL finding-model expression.
"""

FINDINGMODEL_RECONCILE_REVIEWER_INSTRUCTIONS = """\
You audit a reconciliation decision for a flagged group of gene/signature phrasings (each with its FINAL
finding-model expression). Set faithful=true only if: EQUIVALENT members now share ONE expression (phrasing noise
unified); GENUINELY-DISTINCT members kept apart with their own correct expression (a real grammar-encodable
difference NOT collapsed); every expression is valid finding-model following the mapper conventions; AND / OR / NOT()
structure preserved. Otherwise faithful=false with concrete problems. `suggested_fix` only under "[ESCALATION-MODE]".
"""


def build_oncotree_reconciler(client: LlmClient, *, model: str | None = None) -> Agent[GroupReconciliation]:
    return Agent(name="oncotree_reconciler", instructions=_ONCOTREE_RECONCILE_RULES + vocab_reference(),
                 output_schema=GroupReconciliation, client=client, model=model)


def build_oncotree_reconcile_reviewer(client: LlmClient, *, model: str | None = None) -> Agent[ReviewVerdict]:
    return Agent(name="oncotree_reconcile_reviewer",
                 instructions=ONCOTREE_RECONCILE_REVIEWER_INSTRUCTIONS + vocab_reference(),
                 output_schema=ReviewVerdict, client=client, model=model)


def build_findingmodel_reconciler(client: LlmClient, *, model: str | None = None) -> Agent[GroupReconciliation]:
    return Agent(name="findingmodel_reconciler", instructions=_FINDINGMODEL_RECONCILE_RULES + GRAMMAR_REFERENCE,
                 output_schema=GroupReconciliation, client=client, model=model)


def build_findingmodel_reconcile_reviewer(client: LlmClient, *, model: str | None = None) -> Agent[ReviewVerdict]:
    return Agent(name="findingmodel_reconcile_reviewer",
                 instructions=FINDINGMODEL_RECONCILE_REVIEWER_INSTRUCTIONS + "\n" + GRAMMAR_REFERENCE,
                 output_schema=ReviewVerdict, client=client, model=model)
