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
Convert a gene-alteration expression (the trial's normalized wording) into Hartwig finding-model syntax.
Return `finding_model`. Preserve the expression's logical structure: keep AND (&), OR (|) and NOT(...);
exclusions are wrapped in NOT(...). Use "" only if there is genuinely no molecular alteration.

The result must be LOGICALLY CONSISTENT:
- Never emit a duplicate term ("X & X" or "NOT(X) & NOT(X)" = just X / NOT(X)) — list each term once.
- Never emit "X & NOT(X)" or "X | NOT(X)" (a term both required and excluded).
- If a NOT(...) exclusion is qualified by something finding-model CANNOT express — an anatomic LOCATION
  ("H3K27M in thalamic DMG"), a tumour context, or any qualifier with no field for it — OMIT that NOT()
  entirely. Do NOT drop the qualifier and emit NOT(same-variant): that duplicates or contradicts the
  included term. (e.g. "H3K27-altered AND NOT(H3K27M in thalamic DMG)" -> just the H3K27M inclusion.)
- If the source's inclusion and exclusion of an alteration actually apply to DIFFERENT cancer types/cohorts,
  that must have been split into separate rows upstream — here map only what genuinely applies to this row.

Follow the grammar below exactly. Prefer the most specific term the wording supports (name the exon /
protein change / copy-number type when stated). For a bare "mutation"/"alteration" with no specifics, apply
the expansion rule (tumour-suppressor vs oncogene). Only emit the listed classes and fields.

Examples (input -> finding_model):
- "BRAF V600E"                 -> SmallVariant[gene=BRAF & transcriptImpact.hgvsProteinImpact=p.V600E]
- "KRAS G12C"                  -> SmallVariant[gene=KRAS & transcriptImpact.hgvsProteinImpact=p.G12C]
- "EGFR exon 19 deletion"      -> SmallVariant[gene=EGFR & transcriptImpact.affectedExon=19 & transcriptImpact.effects=INFRAME_DELETION]
- "EGFR exon 20 insertion"     -> SmallVariant[gene=EGFR & transcriptImpact.affectedExon=20 & transcriptImpact.effects=INFRAME_INSERTION]
- "ALK fusion"                 -> Fusion[geneStart=ALK | geneEnd=ALK]
- "NTRK fusion"                -> Fusion[geneEnd=NTRK1] | Fusion[geneEnd=NTRK2] | Fusion[geneEnd=NTRK3]
- "ERBB2 amplification"        -> GainDeletion[gene=ERBB2 & type=GAIN]
- "PDGFRA amplification with >=5 copy numbers" -> GainDeletion[gene=PDGFRA & type=GAIN]   (copy-number COUNT has no field — drop it, keep type=GAIN)
- "MTAP homozygous deletion"   -> GainDeletion[gene=MTAP & type=HOM_DEL] | Disruption[gene=MTAP]
- "BRCA1 mutation"             -> SmallVariant[gene=BRCA1] | GainDeletion[gene=BRCA1 & type=HOM_DEL] | Disruption[gene=BRCA1]
- "KRAS mutation"              -> SmallVariant[gene=KRAS] | GainDeletion[gene=KRAS & type=GAIN]
- "ALK wild-type"              -> Wildtype[gene=ALK]
- "no EGFR exon 20 insertion"  -> NOT(SmallVariant[gene=EGFR & transcriptImpact.affectedExon=20 & transcriptImpact.effects=INFRAME_INSERTION])
- "H3K27M"                     -> SmallVariant[gene=H3F3A & transcriptImpact.hgvsProteinImpact=p.K28M] | SmallVariant[gene=HIST1H3B & transcriptImpact.hgvsProteinImpact=p.K28M] | SmallVariant[gene=HIST1H3C & transcriptImpact.hgvsProteinImpact=p.K28M]
- "H3K27-altered"              -> (IDENTICAL to "H3K27M" above — see the "Histone H3 K27" note in the grammar; same 3 genes, p.K28M)
- "H3K27-altered AND BRAF V600E" -> (SmallVariant[gene=H3F3A & transcriptImpact.hgvsProteinImpact=p.K28M] | SmallVariant[gene=HIST1H3B & transcriptImpact.hgvsProteinImpact=p.K28M] | SmallVariant[gene=HIST1H3C & transcriptImpact.hgvsProteinImpact=p.K28M]) & SmallVariant[gene=BRAF & transcriptImpact.hgvsProteinImpact=p.V600E]

"""

GENE_REVIEWER_INSTRUCTIONS = """\
You audit a proposed finding-model conversion of a trial's GENE-ALTERATION wording. You are given the
SOURCE wording and the proposed finding_model. This layer is NOT human-curated, so be strict.

Set faithful=true only if: the syntax is valid finding-model (correct classes/fields, balanced brackets,
SmallVariant is gene-scoped); it captures exactly what the source states (right gene(s), right variant /
exon / protein change / copy-number type / fusion orientation); a bare mutation is expanded correctly
(tumour-suppressor vs oncogene); AND/OR/NOT structure matches the source; and it is LOGICALLY CONSISTENT (reject any "X & NOT(X)" /
"X | NOT(X)" self-contradiction, any duplicated term "NOT(X) & NOT(X)", and any NOT(...) that merely
negates an unrepresentable qualifier (e.g. a location) — that should have been omitted). Otherwise
faithful=false with concrete, actionable problems.

Do NOT fail a mapping merely because it dropped a qualifier finding-model has no field to express — a
copy-number count/threshold ("amplification with >=5 copies" -> type=GAIN is CORRECT), a quantitative level,
a VAF threshold, an anatomic location, a tumour context. That simplification is acceptable and correct (see
"Expressiveness limits" in the grammar); flag it only if a genuinely REPRESENTABLE detail is wrong or missing.
"""

_SIGNATURE_RULES = """\
Convert a molecular-signature expression into Hartwig finding-model syntax. Return `finding_model`,
preserving AND/OR/NOT. Use "" if there is no signature. Only these signature terms exist:

- "MSI-high" / "MSI-H"                       -> MicrosatelliteStability[PurpleMicrosatelliteStatus=MSI]
- "microsatellite stable" / "MSS"           -> MicrosatelliteStability[PurpleMicrosatelliteStatus=MSS]
- "HRD" / "homologous recombination deficient" -> homologousRecombination[ChordStatus=HR_DEFICIENT]
- "HR proficient"                           -> homologousRecombination[ChordStatus=HR_PROFICIENT]
- "TMB-high"                                -> tumorMutationBurden[Status=HIGH]
- "high tumour mutational load"             -> tumorMutationLoad[Status=HIGH]

If the term is really a gene alteration or biomarker (not one of the above signatures), return "".
"""

SIGNATURE_REVIEWER_INSTRUCTIONS = """\
You audit a proposed finding-model conversion of a MOLECULAR-SIGNATURE term. Given the SOURCE term and the
proposed finding_model, set faithful=true only if it uses the correct signature class/status and matches the
source (incl. NOT() for negations); otherwise faithful=false with concrete problems.
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
