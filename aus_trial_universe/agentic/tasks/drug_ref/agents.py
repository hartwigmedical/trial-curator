"""Drug-reference agents: a doer -> reviewer pair per JUDGEMENT stage (spec §6.1).

The LLM does only what needs judgement/interpretation; the deterministic facts (rxcui, pottr_drug_class,
atc_code) are NOT here — they come from rxnorm.py / pottr.py after the LLM stages.

  1. canonicalize  raw name -> canonical identity (name, aliases, investigational?) — judgement
  2. annotate      canonical -> modality (vocab) + (target,action) pairs + drug_class + FDA/EMA — interpretation
  3. approvals     canonical -> TGA/PBS approvals (indication-specific) — interpretation

Same doer->reviewer + bounded-refine pattern as the mapping task; reviewers verify (never rewrite).
"""
from __future__ import annotations

from aus_trial_universe.agentic.core.agent import Agent
from aus_trial_universe.agentic.core.client import LlmClient
from aus_trial_universe.agentic.tasks.drug_ref.schema import (
    MODALITIES,
    ApprovalByIndication,
    Canonicalization,
    DrugAnnotation,
    ReviewVerdict,
)

_MODALITY_LIST = ", ".join(MODALITIES)

# --------------------------------------------------------------------------- #
# Stage 1 — canonicalize (raw name -> canonical identity). Judgement only; RXCUI is a later deterministic lookup.
# --------------------------------------------------------------------------- #
CANONICALIZER_INSTRUCTIONS = """\
You are given ONE drug/treatment name exactly as written in a clinical-trial registry. Identify the CANONICAL
drug it refers to (its active substance / INN). This is a judgement task, NOT string matching — use web search
to confirm identity when unsure. Do NOT return an RXCUI or any id (that is looked up separately).

Return:
- canonical_name: the ingredient / INN name — brand "Keytruda" and code "MK-3475" both -> "pembrolizumab";
  a development code -> its INN (e.g. "ONC201" -> "dordaviprone", "Ris-Rez" -> "risvutatug rezetecan").
  Reduce a salt/formulation to the base ingredient ("temozolomide 100 MG" -> "temozolomide").
  Set "" if the value is NOT a drug (a procedure, "radiotherapy", placebo, "best supportive care").
- aliases: brand names / synonyms / code names you are confident about.
- is_investigational: true if it is a novel/experimental agent with no approved/marketed form.
- notes: one line of reasoning.

If the name is a multi-drug REGIMEN abbreviation (e.g. "R-CHOP", "FOLFOX"), set canonical_name to the regimen
as written and say so in notes (do not invent a single ingredient).
"""

CANONICALIZER_REVIEWER_INSTRUCTIONS = """\
You audit a proposed canonicalization of a raw trial drug name (check plausibility — not re-doing the search).
Given the RAW name and the proposed canonical_name / aliases / is_investigational, set faithful=true only if:
canonical_name is the correct ingredient/INN (brand & code names resolved to it; salt/formulation reduced to the
base), is_investigational is set correctly, and a non-drug (procedure/placebo/radiotherapy) has canonical_name "".
Otherwise faithful=false with concrete, actionable problems.
"""


def build_canonicalizer(client: LlmClient, *, model: str | None = None) -> Agent[Canonicalization]:
    return Agent(name="drug_canonicalizer", instructions=CANONICALIZER_INSTRUCTIONS,
                 output_schema=Canonicalization, client=client, model=model, web_search=True)


def build_canonicalizer_reviewer(client: LlmClient, *, model: str | None = None) -> Agent[ReviewVerdict]:
    return Agent(name="drug_canonicalizer_reviewer", instructions=CANONICALIZER_REVIEWER_INSTRUCTIONS,
                 output_schema=ReviewVerdict, client=client, model=model)


# --------------------------------------------------------------------------- #
# Stage 2 — intrinsic annotation (modality + target/action pairs + general class + FDA/EMA). No POTTR/ATC (deterministic).
# --------------------------------------------------------------------------- #
ANNOTATOR_INSTRUCTIONS = f"""\
You are given a CANONICAL drug name. Return its intrinsic, drug-level facts (properties of the drug itself,
independent of any trial). Use WEB SEARCH when unsure, and CITE a source for every fact you can. Do NOT return
POTTR class or ATC code — those are looked up deterministically elsewhere.

- modality: EXACTLY ONE of these terms — {_MODALITY_LIST}.
- targets: the drug's (target, action) pairs — one per molecular target. `target` = the molecule/pathway
  (e.g. "PD-1", "TOP1", "ClpP", "B7-H3", "PI3K/mTOR"); `action` = how it acts (inhibitor / antagonist / agonist
  / activator / degrader / ADC-binding / ...); `note` = nuance ("payload", "antigen", "dual") or "". A
  multi-target drug has SEVERAL pairs, each with its own action (e.g. dordaviprone: ClpP=activator, DRD2=antagonist);
  an ADC has the antigen pair (action=ADC-binding, note="antigen") AND the payload-target pair (note="payload").
- drug_class: a concise GENERAL drug class (e.g. "PARP inhibitor", "checkpoint inhibitor").
- fda_status: a COARSE US FDA summary in the form "<status> (<year>): <detail> | <detail>", '|' separating
  items (e.g. "approved (2014): melanoma | NSCLC"); "not FDA-approved (investigational)" if none. Do NOT use ';'.
- ema_status: a COARSE EMA summary, same format.
- sources: per-fact citations, "field=url | ..." (e.g. "modality=... | fda_status=...").
"""

ANNOTATOR_REVIEWER_INSTRUCTIONS = f"""\
You audit proposed intrinsic drug facts (plausibility — not re-doing the search). Set faithful=true only if:
modality is EXACTLY ONE of [{_MODALITY_LIST}] and correct; the (target, action) pairs are correct and complete
for this drug (each target has the right action; a multi-target drug lists all its targets; an ADC has both the
antigen and the payload target); drug_class is sensible; fda_status/ema_status are coarse, correctly formatted
("<status> (<year>): <detail> | ...", no ';'), and correct; and sources are present. Otherwise faithful=false
with concrete, actionable problems.
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
distinct indication. TGA and PBS approval are INDICATION-SPECIFIC (approved for melanoma but maybe not lung; an
indication is often cancer + biomarker, e.g. "NSCLC with PD-L1 >=50%", and often combination/line/stage/population
specific). Use WEB SEARCH on the OFFICIAL sources — TGA (tga.gov.au ARTG / Product Information) and PBS (pbs.gov.au).

For EACH indication capture the full, static profile AS THE REGULATOR STATES IT:
- indication_raw: the indication exactly as worded.
- cancer_type: e.g. "melanoma", "NSCLC".
- biomarker: e.g. "PD-L1 >=50%", "HER2-positive", "MSI-H/dMMR"; "" if none.
- stage: e.g. "unresectable Stage III/IV"; "" if unspecified.
- line_of_therapy: e.g. "1L", ">=2 prior lines"; "" if unspecified.
- prior_therapy: required prior treatment, e.g. "after platinum-based chemotherapy"; "" if none.
- combination: "monotherapy" or "in combination with <X>".
- setting: adjuvant / neoadjuvant / metastatic / curative-intent; "" if unspecified.
- patient_population: e.g. "adult", "pediatric >=1 year"; "" if unspecified.
- tga_status: "approved" if TGA-registered (ARTG/PI) for this indication, "not_approved" if confirmed not, else
  "unknown". tga_date: year of the ARTG approval. tga_evidence_url: the official ARTG/PI link.
- pbs_status / pbs_date / pbs_evidence_url: the same for the PBS listing (a drug may be TGA-approved but not
  PBS-listed for the SAME indication — set them independently).

Rules: cover every distinct ONCOLOGY indication for either agency; combine the TGA and PBS view of the SAME
indication into one entry; registration = an actual ARTG entry / PBS listing (NOT SAS / Authorised Prescriber /
trial supply); an investigational / non-approved drug returns an EMPTY list; always give an official source link
where you assert a status.
"""

APPROVAL_REVIEWER_INSTRUCTIONS = """\
You audit a proposed set of TGA/PBS approvals (plausibility — not re-doing the search). Set faithful=true only if:
each indication is at the right specificity (the biomarker qualifier — PD-L1 level, HER2, MSI-H — and the
combination/line where the approval is restricted are NOT dropped); tga_status/pbs_status are each
approved/not_approved/unknown with an official evidence link where a status is asserted; TGA and PBS are set
independently; and an investigational / non-approved drug yields an empty list rather than invented approvals.
Flag a dropped biomarker/combination qualifier, a missing evidence link on an asserted approval, or a
non-oncology indication. Otherwise faithful=false with concrete, actionable problems.
"""


def build_approval_agent(client: LlmClient, *, model: str | None = None) -> Agent[ApprovalByIndication]:
    return Agent(name="drug_approval", instructions=APPROVAL_INSTRUCTIONS,
                 output_schema=ApprovalByIndication, client=client, model=model, web_search=True)


def build_approval_reviewer(client: LlmClient, *, model: str | None = None) -> Agent[ReviewVerdict]:
    return Agent(name="drug_approval_reviewer", instructions=APPROVAL_REVIEWER_INSTRUCTIONS,
                 output_schema=ReviewVerdict, client=client, model=model)
