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
from aus_trial_universe.agentic.tasks.drug_utility.schema import (
    MODALITIES,
    ROLE_AUXILIARY,
    ROLE_MAIN,
    ApprovalByIndication,
    ArmRoleClassification,
    Canonicalization,
    DrugAnnotation,
    ReviewVerdict,
)

_MODALITY_LIST = ", ".join(MODALITIES)

# --------------------------------------------------------------------------- #
# Stage 1 — canonicalize (raw name -> canonical identity). Judgement only; RXCUI is a later deterministic lookup.
# --------------------------------------------------------------------------- #
CANONICALIZER_INSTRUCTIONS = """\
You are given ONE drug/treatment name exactly as written in a clinical-trial registry. Identify the standalone
CANONICAL drug(s) it refers to (each drug's active substance / INN). This is a judgement task, NOT string
matching — use web search to confirm identity when unsure. Do NOT return an RXCUI or any id (looked up separately).

Return `components` — ONE entry per distinct standalone drug:
- SINGLE drug -> exactly one component. A single molecular entity is ONE drug even if it acts on several targets:
  an antibody-drug conjugate ("trastuzumab deruxtecan"), a bispecific / trispecific antibody ("a bispecific
  antibody against PD-L1 and VEGF" -> the one agent, e.g. "pumitamig"), or a fusion protein — DO NOT split these.
- COMBINATION / REGIMEN -> one component PER distinct active drug. Split a name giving several drugs administered
  together, however written: "A + B", "A / B", "A and B", "A plus B", "A, B and C", or a fixed-dose combination
  product (split into its active ingredients). A plain COMMA-SEPARATED LIST of drug names ("A, B, C" or
  "A, B, or C") is several drugs -> split into each. A named regimen abbreviation ("FOLFOX", "R-CHOP", "FOLFIRINOX")
  -> expand into its component drugs. If the name lists ALTERNATIVE regimens joined by "OR", return each DISTINCT
  component drug once (the union), not the alternatives.
  BUT a comma is NOT a separator when it introduces a form / strain / source / valency descriptor of ONE drug —
  keep those as a SINGLE component: e.g. "immune globulin, human"; "influenza virus vaccine, trivalent";
  "BCG, Danish strain 1331, live attenuated"; "HPV 9-valent vaccine, recombinant". Split a comma only when each
  side is itself a distinct drug name.
- NON-DRUG -> return an EMPTY list (a procedure, "radiotherapy", placebo, "best supportive care", "observation").
- GENERIC / UNSPECIFIED treatment word ("chemotherapy", "chemo", "standard of care", "standard therapy",
  "immunotherapy", "chemotherapy 1") names NO specific drug — NEVER invent or expand it into specific agents (do
  NOT guess "vincristine, cyclophosphamide, ..." from the word "chemotherapy"). If the ONLY treatment is such a
  generic word, return an EMPTY list; if it accompanies a named drug ("Lorlatinib with chemotherapy"), return ONLY
  the named drug(s).

For EACH component:
- canonical_name: the ingredient / INN — brand "Keytruda" and code "MK-3475" both -> "pembrolizumab"; a
  development code -> its INN ("ONC201" -> "dordaviprone", "Ris-Rez" -> "risvutatug rezetecan"). Reduce a
  salt / formulation to the base ingredient ("temozolomide 100 MG" -> "temozolomide"). NEVER leave a "+", "/",
  "and" or other join word inside a canonical_name — that means it was not split.
- raw_name_to_map: the EXACT substring of the INPUT name that refers to THIS component — for a combination, the
  split fragment ("Palbociclib" from "Arm A: Gedatolisib + Palbociclib + Fulvestrant"); for a single-drug input,
  the whole input as given. Set it to "" ONLY for an undecomposable regimen acronym (a component of "CAPEOX" /
  "R-CHOP" has no substring of its own).
- aliases: brand / synonym / code names you are confident about.
- is_investigational: true if a novel/experimental agent with no approved/marketed form.
Set notes to one line of reasoning (incl. why one drug vs. a combination).
"""

CANONICALIZER_REVIEWER_INSTRUCTIONS = """\
You audit a proposed canonicalization of a raw trial drug name (check plausibility — not re-doing the search).
Given the RAW name and the proposed components, set faithful=true only if:
- the SPLIT is correct: a multi-drug regimen/combination is split into ALL its distinct component drugs (one per
  active drug), while a single molecular entity (ADC, bispecific / trispecific antibody, fusion) is kept as ONE
  component and NOT split; a named regimen (FOLFOX / R-CHOP) is expanded into its component drugs; a comma-separated
  LIST of distinct drugs ("anastrozole, exemestane, letrozole") is split into each (flag it if left as one
  comma-joined component) — but a comma that is only a form / strain / source descriptor of ONE drug
  ("immune globulin, human"; "influenza virus vaccine, trivalent") must stay ONE component, NOT be split;
- each component's canonical_name is the correct ingredient / INN (brand & code names resolved to it; salt /
  formulation reduced to base) with NO leftover "+" / "/" / "and" / drug-joining "," join word inside it;
- is_investigational is set correctly per component;
- a non-drug (procedure / placebo / radiotherapy) yields an EMPTY component list;
- NO component is a specific drug INVENTED from a generic word — flag it if the raw names only "chemotherapy" /
  "standard of care" / "immunotherapy" yet specific agents (e.g. vincristine, cyclophosphamide) were returned.
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


# --------------------------------------------------------------------------- #
# Phase 2 — per-arm drug role (main vs auxiliary). Judgement from the arm context; NO web search.
# --------------------------------------------------------------------------- #
ROLE_CLASSIFIER_INSTRUCTIONS = f"""\
You are given ONE trial ARM and the drugs administered in it. Classify EACH drug as its role IN THIS ARM:

- {ROLE_MAIN}: the investigational / defining agent(s) the trial is actually TESTING in this arm — the novel or
  key agent under study (usually the drug named in the trial/arm title, or a novel agent with no marketed form).
- {ROLE_AUXILIARY}: everything else administered as support — a comparator, a chemotherapy backbone, a
  standard-of-care agent added to the experimental drug, placebo, or premedication.

You are given, for the arm: its label, its type (arm_type), and a description; and for each drug: its canonical_id,
canonical_name, general drug_class, and modality. Base the judgement on THIS context and your own drug knowledge —
do NOT use web search.

Guidance:
- arm_type is a strong signal: an EXPERIMENTAL arm contains the investigational agent(s); an ACTIVE_COMPARATOR or
  PLACEBO_COMPARATOR / SHAM arm is a control — its drugs are typically ALL {ROLE_AUXILIARY} (standard-of-care /
  comparator / placebo), even when it is the only drug in the arm.
- A novel agent whose canonical_id begins with `name:` has no RxNorm/marketed entry — that is a strong (not
  absolute) signal it is the investigational {ROLE_MAIN} agent.
- An experimental arm often adds the novel agent ON TOP OF an approved backbone (e.g. novel drug + carboplatin +
  pemetrexed): the novel agent is {ROLE_MAIN}; the established backbone drugs are {ROLE_AUXILIARY}.
- An arm may have SEVERAL {ROLE_MAIN} drugs (a novel combination both under study), or ZERO (a pure control arm).
- Classify by what the trial is investigating, not by how new a drug feels in isolation: an approved drug being
  repurposed / tested as the key agent of the arm is {ROLE_MAIN}; the same drug used as backbone is {ROLE_AUXILIARY}.

Return `assignments` — ONE entry per drug given, echoing its canonical_id EXACTLY (never add, drop, or alter an
id), each with role = {ROLE_MAIN} or {ROLE_AUXILIARY}. Put one line of reasoning in `notes`.
"""

ROLE_REVIEWER_INSTRUCTIONS = f"""\
You audit a proposed main/auxiliary classification of the drugs in ONE trial arm (plausibility check — you are NOT
re-researching). Given the arm context (label, arm_type, description) + the drugs (canonical_id, canonical_name,
drug_class, modality) and the proposed roles, set faithful=true only if:
- EVERY given drug has exactly one assignment and its canonical_id matches one given (none invented, dropped, or altered);
- every role is exactly {ROLE_MAIN} or {ROLE_AUXILIARY};
- the investigational / defining agent(s) under study are {ROLE_MAIN} and genuine backbone / standard-of-care /
  comparator / placebo / premedication are {ROLE_AUXILIARY};
- a control arm (ACTIVE_COMPARATOR / PLACEBO_COMPARATOR / SHAM) is NOT given a spurious {ROLE_MAIN} — its drugs
  are {ROLE_AUXILIARY} unless the arm genuinely administers an investigational agent.
Otherwise faithful=false with concrete, actionable problems.
"""


def build_role_classifier(client: LlmClient, *, model: str | None = None) -> Agent[ArmRoleClassification]:
    return Agent(name="drug_role_classifier", instructions=ROLE_CLASSIFIER_INSTRUCTIONS,
                 output_schema=ArmRoleClassification, client=client, model=model)


def build_role_reviewer(client: LlmClient, *, model: str | None = None) -> Agent[ReviewVerdict]:
    return Agent(name="drug_role_reviewer", instructions=ROLE_REVIEWER_INSTRUCTIONS,
                 output_schema=ReviewVerdict, client=client, model=model)
