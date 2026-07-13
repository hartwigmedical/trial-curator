"""Drug-reference agents: a doer -> reviewer pair per build stage (spec §6.1).

Three per-drug, trial-independent stages, each grounded by web search on an authoritative source:
  1. canonicalize  raw name -> canonical identity   (RxNorm ingredient; judgement, not substring)
  2. annotate      canonical -> intrinsic facts      (class / POTTR / modality / mechanism / ATC / FDA / EMA)
  3. approvals     canonical -> TGA/PBS approvals     (INDICATION-specific: cancer + biomarker + line/stage)

Same doer->reviewer + bounded-refine pattern as the mapping task; reviewers verify (never rewrite).
"""
from __future__ import annotations

from aus_trial_universe.agentic.core.agent import Agent
from aus_trial_universe.agentic.core.client import LlmClient
from aus_trial_universe.agentic.tasks.drug_ref.schema import (
    ApprovalByIndication,
    Canonicalization,
    DrugAnnotation,
    ReviewVerdict,
)

# --------------------------------------------------------------------------- #
# Stage 1 — canonicalize (raw name -> canonical identity)
# --------------------------------------------------------------------------- #
CANONICALIZER_INSTRUCTIONS = """\
You are given ONE drug/treatment name exactly as written in a clinical-trial registry. Identify the CANONICAL
drug it refers to, grounded in RxNorm. Use WEB SEARCH (RxNav / RxNorm) to confirm — this is a judgement task,
NOT string matching.

Return:
- canonical_name: the RxNorm INGREDIENT name (the active substance / INN), e.g. brand "Keytruda" and code
  "MK-3475" both -> "pembrolizumab". For an INVESTIGATIONAL agent not in RxNorm, give its best canonical / INN
  name (e.g. "Ris-Rez" -> "risvutatug rezetecan").
- rxcui: the RxNorm RXCUI of that ingredient if it exists, else "" (leave empty rather than guess a number).
- aliases: brand names / synonyms / code names you are confident about.
- is_investigational: true if the drug has no RxNorm ingredient / is a novel experimental agent.
- notes: one line of reasoning / what RxNorm shows.

Rules:
- Resolve brand -> ingredient, code name -> ingredient, salt/formulation -> base ingredient (e.g.
  "temozolomide 100 MG" -> "temozolomide").
- If the name is a multi-drug REGIMEN abbreviation (e.g. "R-CHOP", "FOLFOX"), set canonical_name to the regimen
  as written, is_investigational=false, and say so in notes (do not invent an ingredient).
- If the value is not a drug at all (a procedure, "radiotherapy", placebo, "best supportive care"), set
  canonical_name to "" and note why.
"""

CANONICALIZER_REVIEWER_INSTRUCTIONS = """\
You audit a proposed canonicalization of a raw trial drug name (you are NOT re-doing the search — check
plausibility). Given the RAW name and the proposed canonical_name / rxcui / aliases / is_investigational, set
faithful=true only if: canonical_name is the correct RxNorm ingredient (or best canonical for an investigational
agent), brand/code names are resolved to the ingredient, a salt/formulation is reduced to the base ingredient,
is_investigational is set correctly (true when there is genuinely no RxNorm ingredient), and rxcui is either a
plausible RXCUI or empty (never a guessed number). A non-drug (procedure/placebo) must have canonical_name "".
Otherwise faithful=false with concrete, actionable problems.
"""


def build_canonicalizer(client: LlmClient, *, model: str | None = None) -> Agent[Canonicalization]:
    return Agent(name="drug_canonicalizer", instructions=CANONICALIZER_INSTRUCTIONS,
                 output_schema=Canonicalization, client=client, model=model, web_search=True)


def build_canonicalizer_reviewer(client: LlmClient, *, model: str | None = None) -> Agent[ReviewVerdict]:
    return Agent(name="drug_canonicalizer_reviewer", instructions=CANONICALIZER_REVIEWER_INSTRUCTIONS,
                 output_schema=ReviewVerdict, client=client, model=model)


# --------------------------------------------------------------------------- #
# Stage 2 — intrinsic annotation (drug-level facts, trial-independent)
# --------------------------------------------------------------------------- #
ANNOTATOR_INSTRUCTIONS = """\
You are given a CANONICAL drug name (RxNorm ingredient, or an investigational agent's canonical name). Return
its intrinsic, drug-level facts — properties of the DRUG ITSELF, independent of any trial. Use WEB SEARCH for
POTTR / ATC / regulatory class when unsure. CITE a source for every fact you can.

- modality: molecular modality — small molecule / monoclonal antibody / ADC / bispecific / cell therapy /
  vaccine / radioligand / peptide / oligonucleotide, etc.
- mechanism: the molecular target(s) / mechanism of action (e.g. "anti-PD-1", "EGFR TKI", "PARP inhibitor",
  "DRD2 antagonist / ClpP agonist").
- drug_class: a concise GENERAL drug class (usable even when the drug is not in POTTR).
- pottr_drug_class: the POTTR drug-class hierarchy, root -> leaf joined by " -> "
  (e.g. "cancer_therapy -> cancer_therapy,immunotherapy -> anti-PD-1"). "" if not in POTTR.
- atc_code: the WHO ATC classification code (e.g. "L01FF02" for pembrolizumab). "" if none / investigational.
- fda_status: a COARSE summary of US FDA approval (e.g. "approved 2014; melanoma, NSCLC, + others" or "not
  FDA-approved (investigational)"). Additional context only — do NOT enumerate every indication.
- ema_status: a COARSE summary of EMA approval, same style.
- sources: compact per-fact citations, "field=url; ..." (e.g. "atc_code=who.int/...; fda_status=fda.gov/...").
"""

ANNOTATOR_REVIEWER_INSTRUCTIONS = """\
You audit proposed intrinsic drug facts (plausibility + completeness — NOT re-doing the search). Given the
canonical drug name and the proposed fields, set faithful=true only if: modality, mechanism and drug_class are
correct and sensible for this drug; pottr_drug_class is a plausible root->leaf hierarchy or "" (not in POTTR);
atc_code is a plausible ATC code or "" (investigational); fda_status/ema_status are coarse and correct; and
sources are present for the facts that need them. Otherwise faithful=false with concrete, actionable problems.
"""


def build_annotator(client: LlmClient, *, model: str | None = None) -> Agent[DrugAnnotation]:
    return Agent(name="drug_annotator", instructions=ANNOTATOR_INSTRUCTIONS,
                 output_schema=DrugAnnotation, client=client, model=model, web_search=True)


def build_annotator_reviewer(client: LlmClient, *, model: str | None = None) -> Agent[ReviewVerdict]:
    return Agent(name="drug_annotator_reviewer", instructions=ANNOTATOR_REVIEWER_INSTRUCTIONS,
                 output_schema=ReviewVerdict, client=client, model=model)


# --------------------------------------------------------------------------- #
# Stage 3 — TGA/PBS approvals (INDICATION-specific)
# --------------------------------------------------------------------------- #
APPROVAL_INSTRUCTIONS = """\
You are given a CANONICAL drug name. Find its AUSTRALIAN ONCOLOGY regulatory approvals and return ONE entry per
distinct indication. TGA and PBS approval are INDICATION-SPECIFIC (a drug can be approved for melanoma but not
lung; an indication is often cancer + biomarker, e.g. "NSCLC with PD-L1 >=50%", "HER2-positive breast"). Use
WEB SEARCH on the OFFICIAL sources — TGA (tga.gov.au ARTG / Product Information) and PBS (pbs.gov.au).

For EACH indication return:
- indication_raw: the indication exactly as the regulator states it (full wording).
- cancer_type: the cancer type as stated (e.g. "melanoma", "NSCLC", "breast cancer").
- biomarker: the biomarker qualifier as stated, if any (e.g. "PD-L1 >=50%", "HER2-positive", "MSI-H/dMMR"); "" if none.
- line_of_therapy: e.g. "1L", ">=2 prior lines"; "" if unspecified.
- stage: e.g. "unresectable Stage III/IV"; "" if unspecified.
- tga_status: "approved" if TGA-registered (ARTG/PI) for this indication, "not_approved" if you can confirm it
  is not, else "unknown". tga_date: year of the ARTG approval. tga_evidence_url: the official ARTG/PI link.
- pbs_status / pbs_date / pbs_evidence_url: the same for the PBS listing (a drug may be TGA-approved but not
  PBS-listed for the same indication — set them independently).

Rules:
- Cover every distinct ONCOLOGY indication you can find for either agency; combine the TGA and PBS view of the
  SAME indication into one entry. Do NOT include non-oncology indications.
- Registration = an actual ARTG entry / PBS listing; do NOT count SAS / Authorised Prescriber / clinical-trial supply.
- If the drug has NO Australian oncology approval (e.g. investigational), return an EMPTY indications list.
- Always give an official source link where you assert a status.
"""

APPROVAL_REVIEWER_INSTRUCTIONS = """\
You audit a proposed set of TGA/PBS approvals for a drug (plausibility + structure — NOT re-doing the search).
Set faithful=true only if: each indication is captured at the right specificity (cancer type, and the biomarker
qualifier when the approval is biomarker-restricted — e.g. PD-L1 level, HER2 status, MSI-H — is NOT dropped);
tga_status/pbs_status are each approved/not_approved/unknown with an official evidence link where a status is
asserted; TGA and PBS are set independently; and an investigational / non-approved drug yields an empty list
rather than invented approvals. Flag a dropped biomarker qualifier, a missing evidence link on an asserted
approval, or a non-oncology indication. Otherwise faithful=false with concrete, actionable problems.
"""


def build_approval_agent(client: LlmClient, *, model: str | None = None) -> Agent[ApprovalByIndication]:
    return Agent(name="drug_approval", instructions=APPROVAL_INSTRUCTIONS,
                 output_schema=ApprovalByIndication, client=client, model=model, web_search=True)


def build_approval_reviewer(client: LlmClient, *, model: str | None = None) -> Agent[ReviewVerdict]:
    return Agent(name="drug_approval_reviewer", instructions=APPROVAL_REVIEWER_INSTRUCTIONS,
                 output_schema=ReviewVerdict, client=client, model=model)
