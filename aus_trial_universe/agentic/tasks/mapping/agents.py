"""Mapping-task agents: LLM mapper -> reviewer (spec §6.2).

OncoTree procedure: a mapper converts a cancer-type expression into OncoTree names +
codes (grounded in the real vocabulary from tools/oncotree.py), and a reviewer audits
it. Same doer->reviewer + bounded-refine pattern as extraction. Few-shot examples are
drawn from the curated legacy resource (ConditionsCurationResource / PrimaryTumour).
"""
from __future__ import annotations

from aus_trial_universe.agentic.core.agent import Agent
from aus_trial_universe.agentic.core.client import LlmClient
from aus_trial_universe.agentic.tasks.mapping.schema import (
    DrugCuration,
    FindingModelMapping,
    OncotreeMapping,
    ReviewVerdict,
)
from aus_trial_universe.agentic.tools.finding_model import GRAMMAR_REFERENCE
from aus_trial_universe.agentic.tools.oncotree import vocab_reference

_ONCOTREE_RULES = """\
You map a clinical trial's cancer/tumour-type expression to OncoTree. Return TWO renderings of the
SAME expression: oncotree_name (using OncoTree names) and oncotree_code (using OncoTree codes).

Rules:
- Map each tumour/cancer term to its single CLOSEST OncoTree node from the VOCABULARY below. Pick the
  most specific node that still fully covers the stated type; do NOT over-narrow (e.g. "NSCLC" -> NSCLC,
  not a subtype) and do NOT over-broaden.
- Preserve the expression's logical structure EXACTLY: keep "AND" and "NOT(...)" as-is, mapping only the
  tumour terms inside them.
- Use ONLY codes/names that appear in the VOCABULARY, or these sentinels:
  - "Solid tumour"  — any solid tumour (e.g. "advanced solid tumours").
  - "Pan-cancer"    — any cancer incl. haematological (e.g. "solid and haematological malignancies").
  - "[None]"        — the whole term is not a cancer/tumour type at all.
- oncotree_name and oncotree_code must be structurally identical (same terms, same AND/NOT), differing
  only in name vs code.
- Excluded subtypes with NO distinct OncoTree node: if a NOT(...) names a histologic subtype/refinement
  that has no OncoTree code of its own (e.g. "complex SCLC" or "transformed SCLC" under SCLC), OMIT that
  NOT() entirely. Never map it to the PARENT code (that would wrongly exclude the included type, e.g.
  "SCLC AND NOT(SCLC)"), and never put "[None]" inside a NOT(). A code that is included must not also appear negated.

Examples (source -> oncotree_name  //  oncotree_code):
- "metastatic NSCLC"                     -> Non-Small Cell Lung Cancer  //  NSCLC
- "HR+/HER2- breast cancer"              -> Breast Cancer  //  BREAST
- "acute myeloid leukemia"               -> Acute Myeloid Leukemia  //  AML
- "advanced solid tumours"               -> Solid tumour  //  Solid tumour
- "solid and haematological malignancies"-> Pan-cancer  //  Pan-cancer
- "solid tumour AND NOT(melanoma)"       -> Solid tumour AND NOT(Melanoma)  //  Solid tumour AND NOT(MEL)
- "Rett syndrome"                        -> [None]  //  [None]

VOCABULARY (CODE<TAB>Name):
"""

ONCOTREE_REVIEWER_INSTRUCTIONS = """\
You audit a proposed OncoTree mapping of a trial's cancer-type expression. You are given the SOURCE
expression and the proposed oncotree_name / oncotree_code.

Set faithful=true only if: every tumour term is mapped to the CORRECT OncoTree node with appropriate
granularity (not too broad, not too narrow); the codes are valid OncoTree codes or the sentinels
(Solid tumour / Pan-cancer / [None]); and the AND / NOT(...) structure of the source is preserved and
oncotree_name mirrors oncotree_code. Otherwise faithful=false with concrete, actionable problems.
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
- "MTAP homozygous deletion"   -> GainDeletion[gene=MTAP & type=HOM_DEL] | Disruption[gene=MTAP]
- "BRCA1 mutation"             -> SmallVariant[gene=BRCA1] | GainDeletion[gene=BRCA1 & type=HOM_DEL] | Disruption[gene=BRCA1]
- "KRAS mutation"              -> SmallVariant[gene=KRAS] | GainDeletion[gene=KRAS & type=GAIN]
- "ALK wild-type"              -> Wildtype[gene=ALK]
- "no EGFR exon 20 insertion"  -> NOT(SmallVariant[gene=EGFR & transcriptImpact.affectedExon=20 & transcriptImpact.effects=INFRAME_INSERTION])

"""

GENE_REVIEWER_INSTRUCTIONS = """\
You audit a proposed finding-model conversion of a trial's GENE-ALTERATION wording. You are given the
SOURCE wording and the proposed finding_model. This layer is NOT human-curated, so be strict.

Set faithful=true only if: the syntax is valid finding-model (correct classes/fields, balanced brackets,
SmallVariant is gene-scoped); it captures exactly what the source states (right gene(s), right variant /
exon / protein change / copy-number type / fusion orientation); a bare mutation is expanded correctly
(tumour-suppressor vs oncogene); and AND/OR/NOT structure matches the source. Otherwise faithful=false
with concrete, actionable problems.
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


# --------------------------------------------------------------------------- #
# drug enrichment (main/auxiliary + POTTR/general class + TGA/PBS via web search)
# --------------------------------------------------------------------------- #
DRUG_CURATOR_INSTRUCTIONS = """\
You curate the DRUG information for one clinical trial. You are given the trial's title/summary, its
interventions/arms, and the drugs administered. Use WEB SEARCH for the Australian regulatory lookups
(TGA/ARTG and PBS) and for the general drug class when unsure. Return:

- main_drugs: the trial's MAIN investigational drug(s) or REGIMEN under study — the agent(s) the trial is
  actually testing (usually the experimental-arm drug named in the title). List the drug, or the whole
  regimen as one element (e.g. "FOLFOX", "pembrolizumab + lenvatinib"), FIRST. Exclude comparators/backbone.
- auxiliary_drugs: the remaining drugs (comparators, chemo backbone, standard-of-care, placebo, premedication).
- pottr_drug_class: the POTTR drug-class hierarchy of the MAIN drug(s)/regimen, root -> leaf joined by " -> "
  (e.g. "cancer_therapy -> cancer_therapy,EGFR-targeting -> EGFR_inhibitor"). "" if not in POTTR.
- drug_class: a concise GENERAL (non-POTTR) class/mechanism of the MAIN drug(s) (e.g. "PARP inhibitor",
  "anti-PD-1 monoclonal antibody"). ALWAYS fill this (web search if unsure).
- tga_status: TGA approval of the MAIN drug(s). "Approved (YYYY)" with the ARTG registration year if it has any
  current ARTG entry (any indication); else "Not approved"; "Unclear" if ambiguous. Do NOT count SAS /
  Authorised Prescriber / clinical-trial / section 19A supply.
- pbs_status: PBS reimbursement of the MAIN drug(s): whether PBS-listed and for which indication(s), with brief
  details; "Unclear" if not determinable.

Base main/auxiliary + POTTR on the trial text and your knowledge; use web search for tga_status, pbs_status,
and drug_class.
"""

DRUG_REVIEWER_INSTRUCTIONS = """\
You audit a proposed drug curation for a trial (plausibility + format — you are NOT re-doing the web search).
Given the trial text + drug list and the proposed fields, set faithful=true only if: main_drugs names the
investigational agent(s)/regimen actually under study (listed first) and auxiliary_drugs are genuinely
non-investigational; drug_class is a sensible class for the main drug(s); tga_status is well-formed
("Approved (YYYY)" / "Not approved" / "Unclear"); pbs_status is present. Otherwise faithful=false with
concrete, actionable problems.
"""


def build_drug_curator(client: LlmClient, *, model: str | None = None) -> Agent[DrugCuration]:
    return Agent(name="drug_curator", instructions=DRUG_CURATOR_INSTRUCTIONS,
                 output_schema=DrugCuration, client=client, model=model, web_search=True)


def build_drug_reviewer(client: LlmClient, *, model: str | None = None) -> Agent[ReviewVerdict]:
    return Agent(name="drug_reviewer", instructions=DRUG_REVIEWER_INSTRUCTIONS,
                 output_schema=ReviewVerdict, client=client, model=model)
