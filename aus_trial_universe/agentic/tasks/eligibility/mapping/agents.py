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
You map a clinical trial's cancer/tumour-type expression to OncoTree. Return TWO renderings of the
SAME expression: oncotree_name (using OncoTree names) and oncotree_code (using OncoTree codes).

Rules:
- Map each tumour/cancer term to its single CLOSEST OncoTree node from the VOCABULARY below. Pick the
  most specific node that still fully covers the stated type; do NOT over-narrow (e.g. "NSCLC" -> NSCLC,
  not a subtype) and do NOT over-broaden.
- Preserve the expression's logical structure, mapping only the tumour terms — BUT it is your job to fix
  logically invalid input (see "Logical consistency" below); do not blindly copy a broken AND/NOT.
- Use ONLY codes/names that appear in the VOCABULARY, or these THREE permitted non-OncoTree terms:
  - "Solid tumour"              — any solid tumour (e.g. "advanced solid tumours").
  - "Pan-cancer"               — any cancer incl. haematological (e.g. "solid and haematological malignancies").
  - "Haematological malignancy"— any blood / lymphoid cancer (e.g. "relapsed haematologic malignancies").
  If a term is genuinely not a cancer/tumour type, return "" (empty) — do not invent a code.
- oncotree_name and oncotree_code must be structurally identical (same terms, same AND/OR/NOT), differing
  only in name vs code.

Logical consistency (MANDATORY — a mapping must never be self-contradictory or redundant):
- NEVER repeat a code: "X AND X" = "X"; list each code once.
- NEVER include and exclude the same code: no "X AND NOT(X)".
- NEVER AND a broad term with a specific type under it (e.g. "Solid tumour AND Melanoma", or a code ANDed
  with its own OncoTree subtype/parent). A broad type and its subtype are OR-alternatives, not a
  conjunction — a patient has ONE tumour. If the trial is clearly about the specific type, keep only that
  and DROP the broad term; if it genuinely spans the broad group with the subtype named, use OR (e.g.
  "Solid tumour OR Melanoma").
- Keep NOT(cancer type) to a MINIMUM: only negate a genuinely excluded tumour type that has its OWN
  OncoTree node. If a NOT(...) names a histologic subtype/refinement with no OncoTree code of its own
  (e.g. "complex SCLC" under SCLC), OMIT that NOT() entirely — never map it to the PARENT code.

Examples (source -> oncotree_name  //  oncotree_code):
- "metastatic NSCLC"                     -> Non-Small Cell Lung Cancer  //  NSCLC
- "HR+/HER2- breast cancer"              -> Breast Cancer  //  BREAST
- "acute myeloid leukemia"               -> Acute Myeloid Leukemia  //  AML
- "advanced solid tumours"               -> Solid tumour  //  Solid tumour
- "solid and haematological malignancies"-> Pan-cancer  //  Pan-cancer
- "relapsed haematologic malignancies"   -> Haematological malignancy  //  Haematological malignancy
- "solid tumours except melanoma"        -> Solid tumour AND NOT(Melanoma)  //  Solid tumour AND NOT(MEL)
- "solid tumours, e.g. melanoma"         -> Solid tumour OR Melanoma  //  Solid tumour OR MEL

VOCABULARY (CODE<TAB>Name):
"""

ONCOTREE_REVIEWER_INSTRUCTIONS = """\
You audit a proposed OncoTree mapping of a trial's cancer-type expression. You are given the SOURCE
expression and the proposed oncotree_name / oncotree_code.

Set faithful=true only if ALL of the following hold; otherwise faithful=false with concrete, actionable problems:
- Every tumour term maps to the CORRECT OncoTree node at appropriate granularity (not too broad, not too narrow).
- Codes are valid OncoTree codes or one of the THREE permitted terms only: "Solid tumour", "Pan-cancer",
  "Haematological malignancy"; a non-cancer value must be empty, not a code.
- The mapping is LOGICALLY CONSISTENT: no "X AND X", no "X AND NOT(X)", and no broad term ANDed with a
  specific type under it (a broad type + its subtype are OR-alternatives, not AND). Flag any of these.
- NOT(cancer type) terms are minimal and each names a genuinely excluded tumour type with its own node.
- oncotree_name mirrors oncotree_code (same terms/structure).
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
        instructions=ONCOTREE_REVIEWER_INSTRUCTIONS,
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
