"""ANZCTR drug-identification agents — the LLM half of the shared cohort-identification module.

These are the doer + reviewer that turn an ANZCTR trial's free text into intervention/comparator drug names, from
which `cohorts.anzctr_regimes` derives the trial's arms. They live HERE (neutral ground) so that BOTH the
eligibility and the drug-utility paths identify ANZCTR cohorts through one module — neither path imports arm
identification from the other. The instructions are the same text they had when they lived in the eligibility
extraction package (moved verbatim; agent names unchanged), so behaviour is preserved.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from aus_trial_universe.core.agent import Agent
from aus_trial_universe.core.client import LlmClient


# --- LLM I/O schemas -------------------------------------------------------- #
class DrugExtraction(BaseModel):
    """ANZCTR drug/treatment names, raw (no normalization), split by arm role (spec §6.1).

    ANZCTR has a single eligibility cohort; the regime axis comes from the drugs. The intervention
    section gives the experimental regime; the comparator section gives a control regime IF it names an
    actual drug (not placebo / radiotherapy / observation)."""

    intervention_drugs: list[str] = Field(
        default_factory=list,
        description="Investigational drug/treatment names from the INTERVENTIONS section, as stated. [] if none.",
    )
    comparator_drugs: list[str] = Field(
        default_factory=list,
        description=(
            "Comparator DRUG names from the COMPARATOR section, as stated. [] if the comparator is placebo, "
            "radiotherapy, observation/no treatment, or otherwise not a drug (use the CONTROL field as a hint)."
        ),
    )


class RegimeVerdict(BaseModel):
    """A reviewer's verdict on the extracted ANZCTR drugs (faithful + actionable problems)."""

    faithful: bool
    problems: list[str] = Field(
        default_factory=list,
        description="Concrete, actionable issues (non-drug included / real drug missed / mis-classified) if not faithful.",
    )
    suggested_fix: str = Field(
        default="",
        description="OPTIONAL. A concrete corrected drug list that would resolve the problems; advice for the writer, "
                    "not applied directly. Leave empty if you cannot propose a specific correction.",
    )


# --- Doer ------------------------------------------------------------------- #
DRUG_EXTRACTOR_INSTRUCTIONS = """\
This is an ANZCTR trial (a single eligibility cohort); its drug regimes come from the drugs it ADMINISTERS AS THE \
STUDY INTERVENTION. Return drug/treatment names as stated (RAW — no normalization, no RxNorm; exclude \
dosing/schedule prose):
- intervention_drugs: the pharmacological agent(s) administered as the trial's intervention. The INTERVENTIONS \
section is your PRIMARY source; when it is sparse or empty, ALSO use the STUDY TITLE / SCIENTIFIC TITLE and the \
other fields to identify the intervention drug(s) (the drug is sometimes named only in the title).
- comparator_drugs: the comparator DRUG(s) named in COMPARATOR — but [] if the comparator is a placebo, \
radiotherapy, observation / no active treatment, or otherwise not a drug. Use the CONTROL field as a hint: \
"Placebo"/"Uncontrolled" usually mean no comparator drug; "Active"/"Dose comparison" usually mean there is one.

Return ONLY actual pharmacological agents (small molecules, biologics, chemo, targeted / immuno / hormonal therapy, \
vaccines, radioligands, cell / gene therapy, herbal or investigational compounds). NEVER emit:
- a NON-DRUG modality: surgery, a transplantation procedure, radiotherapy / radiation / TOTAL BODY IRRADIATION \
(TBI), observation, best supportive / standard care, watchful waiting, a device, ablation, diet / exercise / \
counselling, or an imaging-only diagnostic agent. (Do KEEP the drugs given WITHIN such a regime — e.g. the \
conditioning chemotherapy before a transplant.)
- the DISEASE / CONDITION or its abbreviation (e.g. "AL" for AL amyloidosis) — a condition is never a drug.
- a drug named only as PRIOR therapy, a REQUIRED or PROHIBITED concomitant medication, washout, rescue medication, \
premedication, or an eligibility criterion — that is not the intervention under study.
Prefer the actual named agent(s) over an opaque internal code or arm label: if the text says a code IS a named \
compound or combination (e.g. a herbal combination composed of two named herbs), return the named component(s), \
not the bare code; and do NOT emit a stray abbreviation, cohort / part label, or sentence fragment that is not \
clearly a drug name. Return [] for a list with none.
"""


def build_drug_agent(client: LlmClient, *, model: str | None = None) -> Agent[DrugExtraction]:
    return Agent(name="drug_extractor", instructions=DRUG_EXTRACTOR_INSTRUCTIONS,
                 output_schema=DrugExtraction, client=client, model=model)


# --- Reviewer --------------------------------------------------------------- #
DRUG_EXTRACTOR_REVIEWER_INSTRUCTIONS = """\
You audit the drugs extracted from an ANZCTR trial (plausibility — not re-reading everything). Given the trial
text and the proposed intervention_drugs + comparator_drugs, set faithful=true only if EVERY extracted name is an
actual pharmacological agent administered AS the trial's intervention or comparator, and NONE is:
- a NON-DRUG modality (surgery / transplantation / radiotherapy / total body irradiation / observation / best
  supportive or standard care / device / ablation / diet / exercise / imaging-only agent);
- the DISEASE / CONDITION or its abbreviation (e.g. "AL" for AL amyloidosis);
- a drug named only as PRIOR / concomitant / prohibited / rescue / premedication or in the eligibility criteria;
- a stray non-drug abbreviation, cohort / part label, or sentence fragment (e.g. "PA"), or an opaque code where the
  text actually names the underlying agent(s).
Also: intervention_drugs are the agents actually administered (INTERVENTIONS is primary, but a drug named only in
the study / scientific title counts; dosing/schedule prose excluded, not invented, none missed); comparator_drugs
are the COMPARATOR drug(s), or [] when the comparator is placebo / radiotherapy / observation / no active treatment
(judge with the CONTROL field). Flag any non-drug modality, disease/condition, concomitant/prior med, or stray
fragment wrongly included, and any real intervention drug missed. Otherwise faithful=false with concrete,
actionable problems.
"""


def build_drug_reviewer_agent(client: LlmClient, *, model: str | None = None) -> Agent[RegimeVerdict]:
    return Agent(name="drug_extractor_reviewer", instructions=DRUG_EXTRACTOR_REVIEWER_INSTRUCTIONS,
                 output_schema=RegimeVerdict, client=client, model=model)
