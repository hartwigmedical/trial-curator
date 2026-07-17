"""Drug-reference table schemas (spec §6.1).

The persisted TSV tables are plain dataclasses (one row each); their column order derives from the field
order so the schema is the single source of truth. LLM I/O schemas (pydantic) drive the *judgement* stages
(canonicalize / annotate / approvals); the *deterministic* facts (rxcui / pottr_drug_class / atc_code) are
NOT produced by the LLM — they come from the RxNorm / POTTR / ATC lookups (see pottr.py / rxnorm.py / atc.py).

Conventions:
- `canonical_id` is a NAMESPACED, self-describing string: ``rxcui:<n>`` when RxNorm-resolved, else
  ``name:<normalized canonical name>`` (investigational agents have no RXCUI). Uniform type, no int/string mixing.
- `|` is the list separator inside free-text fields (aliases, fda/ema detail) — a plain "and-also" list, NOT logic.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, fields

from pydantic import BaseModel, Field

# Approval status vocabulary (per agency, per indication).
APPROVED = "approved"
NOT_APPROVED = "not_approved"
UNKNOWN = "unknown"

# Controlled vocabulary for drug modality (pick exactly one).
MODALITIES = (
    "small molecule", "monoclonal antibody", "antibody-drug conjugate", "bispecific antibody",
    "cell therapy", "oncolytic virus", "therapeutic vaccine", "radioligand", "peptide",
    "oligonucleotide", "gene therapy", "other",
)


# --- Table 1a: trial intervention name (as written) -> canonical drug(s) ----- #
@dataclass
class InterventionToCanonical:
    """One (input intervention name -> canonical drug) mapping. A combination/regimen input yields SEVERAL rows
    (one per component drug, `1 input -> N`); a non-drug input yields a single row with an empty canonical_id.
    Deduped by the input string — the mapping is a pure function of the string, independent of any trial (the
    trial provenance lives in TrialToIntervention)."""

    input_intervention_name: str = ""   # exactly as written in the registry (the lookup key)
    raw_name_to_map: str = ""           # the drug fragment of the input mapped to THIS canonical (= the whole
    #                                     input for a single-drug name; the split component for a combination;
    #                                     "" when the input is an undecomposable regimen acronym, e.g. "CAPEOX")
    canonical_id: str = ""              # FK -> DrugRef.canonical_id (namespaced: rxcui:<n> | name:<x>); "" if non-drug


# --- Table 1b: which trial (and registry) each intervention name came from ---- #
@dataclass
class TrialToIntervention:
    """Provenance (the traceability record): a trial used an intervention name. Many-to-many — one input name can
    appear in several trials, and a trial has several intervention names. FK input_intervention_name ->
    InterventionToCanonical (the mapping is looked up once per string and reused across every trial that uses it)."""

    trialId: str = ""                   # NCT... (ctgov) or ACTRN... (anzctr)
    registry: str = ""                  # "ctgov" | "anzctr"
    input_intervention_name: str = ""   # FK -> InterventionToCanonical.input_intervention_name


# --- Table 2: canonical drug -> intrinsic, drug-level facts ------------------ #
@dataclass
class DrugRef:
    canonical_id: str = ""       # rxcui:<n> when RxNorm-resolved, else name:<normalized canonical name>
    canonical_name: str = ""     # RxNorm ingredient name / best canonical name
    rxcui: str = ""              # RxNorm identity ONLY — presence means "in RxNorm", NOT "approved". "" if none.
    aliases: str = ""            # "|"-joined brand names / synonyms
    modality: str = ""           # one of MODALITIES (deterministic vocab)
    drug_class: str = ""         # general drug class (LLM; used even when not in POTTR)
    pottr_drug_class: str = ""   # POTTR hierarchy, root->leaf joined by " -> " (DETERMINISTIC lookup). "" if not in POTTR.
    atc_code: str = ""           # WHO ATC classification code (DETERMINISTIC lookup). "" if none.
    fda_status: str = ""         # coarse FDA summary, "<status> (<year>): <detail> | <detail>" (LLM)
    ema_status: str = ""         # coarse EMA summary, same format (LLM)
    sources: str = ""            # per-fact citations, "field=url | ..." (cite always)
    researched_on: str = ""      # ISO date (YYYY-MM-DD) this canonical was last researched


# --- Table 3: canonical drug -> (molecular target, action) pairs ------------- #
@dataclass
class DrugTarget:
    """One (target, action) pair — a drug acts on each target via a specific action, and a multi-target
    drug has different actions per target (dordaviprone: ClpP=activator, DRD2=antagonist), so they are kept
    PAIRED, one row per target. `target` is the queryable matching dimension."""

    canonical_id: str = ""   # FK -> DrugRef.canonical_id
    target: str = ""         # molecular target / pathway, e.g. "PD-1", "TOP1", "ClpP", "B7-H3"
    action: str = ""         # inhibitor / antagonist / agonist / activator / degrader / ADC-binding / …
    note: str = ""           # nuance, e.g. "payload" / "antigen" / "dual"


# --- Table 4: (canonical, indication) -> TGA/PBS approval (indication-specific) #
@dataclass
class DrugIndication:
    """One approved indication, captured AS THE REGULATOR STATES IT, decomposed into a static, comprehensive
    set of free-text components (an indication is often cancer + biomarker + line/stage + combination, etc.).
    TGA and PBS are independent columns on the same row (both depend fully on the key; only two fixed AU
    agencies). DEFERRED (user): mapping the components into the eligibility vocabulary (OncoTree + finding-model)
    for symmetric matching — comes with the trial-link."""

    canonical_id: str = ""       # FK -> DrugRef.canonical_id
    indication_id: str = ""      # surrogate id, unique within a canonical
    indication_raw: str = ""     # the indication exactly as the regulator states it (full text, audit)
    # --- static, comprehensive as-stated components (free text) ---
    cancer_type: str = ""        # e.g. "melanoma", "NSCLC"
    biomarker: str = ""          # e.g. "PD-L1 >=50%", "HER2-positive", "MSI-H/dMMR"
    stage: str = ""              # e.g. "unresectable Stage III/IV"
    line_of_therapy: str = ""    # e.g. "1L", ">=2 prior lines"
    prior_therapy: str = ""      # required prior treatment, e.g. "after platinum failure"
    combination: str = ""        # "monotherapy" | "in combination with <X>"
    setting: str = ""            # adjuvant / neoadjuvant / metastatic / curative-intent
    patient_population: str = "" # e.g. "adult", "pediatric >=1 year"
    # --- per-agency approval + evidence (independent) ---
    tga_status: str = ""         # approved | not_approved | unknown
    tga_date: str = ""
    tga_evidence_url: str = ""
    pbs_status: str = ""
    pbs_date: str = ""
    pbs_evidence_url: str = ""
    researched_on: str = ""


def _columns(dc) -> list[str]:
    return [f.name for f in fields(dc)]


INTERVENTION_TO_CANONICAL_COLUMNS = _columns(InterventionToCanonical)
TRIAL_TO_INTERVENTION_COLUMNS = _columns(TrialToIntervention)
DRUG_REF_COLUMNS = _columns(DrugRef)
DRUG_TARGET_COLUMNS = _columns(DrugTarget)
DRUG_INDICATION_COLUMNS = _columns(DrugIndication)

TABLE_FILES = {
    "intervention_to_canonical": "intervention_to_canonical.tsv",
    "trial_to_intervention": "trial_to_intervention.tsv",
    "drug_ref": "drug_ref.tsv",
    "drug_target": "drug_target.tsv",
    "drug_indication": "drug_indication.tsv",
}


# --------------------------------------------------------------------------- #
# Canonical identity — namespaced, self-describing string (no int/string mixing)
# --------------------------------------------------------------------------- #
_ID_WS_RE = re.compile(r"\s+")


def canonical_id_for(canonical_name: str, rxcui: str = "") -> str:
    """``rxcui:<n>`` when RxNorm-resolved (a real id, distinct from the name), else
    ``name:<normalized canonical name>`` for investigational agents with no RXCUI."""
    rx = (rxcui or "").strip()
    if rx:
        return f"rxcui:{rx}"
    norm = _ID_WS_RE.sub(" ", (canonical_name or "").strip().lower())
    return f"name:{norm}" if norm else ""


# --------------------------------------------------------------------------- #
# LLM I/O schemas (pydantic) — JUDGEMENT stages only (deterministic facts excluded)
# --------------------------------------------------------------------------- #
class CanonicalComponent(BaseModel):
    """One standalone drug that a raw token resolves to. A single molecular entity (small molecule, mAb, ADC,
    bispecific / trispecific antibody, fusion protein) is ONE component even if it engages several targets; a
    multi-drug regimen resolves to SEVERAL (one per distinct active drug). rxcui is a later deterministic lookup."""

    canonical_name: str = Field(description="The canonical ingredient / INN name of THIS component (RxNorm "
                                            "ingredient where it exists; for an investigational agent, its best "
                                            "canonical / INN or development code). Salt / formulation reduced to base.")
    raw_name_to_map: str = Field(default="", description="The exact substring of the INPUT name that refers to THIS "
                                                         "component — the split fragment for a combination (e.g. "
                                                         "'Palbociclib' from 'Arm A: Gedatolisib + Palbociclib + "
                                                         "Fulvestrant'), or the whole cleaned input for a single-drug "
                                                         "name. Leave \"\" only for an undecomposable regimen acronym "
                                                         "(e.g. a component of 'CAPEOX' with no substring of its own).")
    aliases: list[str] = Field(default_factory=list, description="Brand / synonym / code names for THIS component.")
    is_investigational: bool = Field(default=False, description="True if no approved/RxNorm drug (novel agent).")


class Canonicalization(BaseModel):
    """Stage 1 (judgement): a raw drug/treatment name -> the standalone drug(s) it refers to. A combination or
    regimen yields MULTIPLE components (one per distinct active drug); a single molecular entity yields ONE; a
    non-drug (procedure / placebo / radiotherapy / best supportive care) yields NONE. rxcui is NOT here — it is a
    deterministic RxNorm lookup per component `canonical_name`."""

    components: list[CanonicalComponent] = Field(
        default_factory=list,
        description="One entry per distinct standalone drug the raw name refers to. [] if it is not a drug.")
    notes: str = Field(default="", description="Brief reasoning, incl. why this is one drug vs. a combination.")


class TargetAction(BaseModel):
    """One (target, action) pair for a drug (Stage 2 judgement)."""

    target: str = Field(description="Molecular target / pathway, e.g. 'PD-1', 'TOP1', 'ClpP', 'B7-H3', 'PI3K/mTOR'.")
    action: str = Field(description="Action on that target: inhibitor / antagonist / agonist / activator / "
                                    "degrader / ADC-binding / etc.")
    note: str = Field(default="", description="Nuance, e.g. 'payload', 'antigen', 'dual'. \"\" if none.")


class DrugAnnotation(BaseModel):
    """Stage 2 (judgement): intrinsic facts the LLM interprets. POTTR / ATC are NOT here (deterministic)."""

    modality: str = Field(description=f"EXACTLY ONE of: {', '.join(MODALITIES)}.")
    targets: list[TargetAction] = Field(default_factory=list, description="The (target, action) pairs.")
    drug_class: str = Field(default="", description="Concise GENERAL drug class (e.g. 'PARP inhibitor').")
    fda_status: str = Field(default="", description="Coarse US FDA summary: '<status> (<year>): <detail> | <detail>' "
                                                    "(e.g. 'approved (2014): melanoma | NSCLC'); '|' separates items.")
    ema_status: str = Field(default="", description="Coarse EMA summary, same format.")
    sources: str = Field(default="", description="Per-fact citations, 'field=url | ...'.")


class ApprovedIndication(BaseModel):
    """One indication as the regulator states it (free text; NOT eligibility-vocabulary mapped)."""

    indication_raw: str = Field(description="The indication exactly as stated by the regulator (full text).")
    cancer_type: str = Field(default="", description="Cancer type as stated, e.g. 'melanoma', 'NSCLC'.")
    biomarker: str = Field(default="", description="Biomarker as stated, e.g. 'PD-L1 >=50%', 'HER2-positive'.")
    stage: str = Field(default="", description="e.g. 'unresectable Stage III/IV'.")
    line_of_therapy: str = Field(default="", description="e.g. '1L', '>=2 prior lines'.")
    prior_therapy: str = Field(default="", description="Required prior treatment, e.g. 'after platinum failure'.")
    combination: str = Field(default="", description="'monotherapy' or 'in combination with <X>'.")
    setting: str = Field(default="", description="adjuvant / neoadjuvant / metastatic / curative-intent.")
    patient_population: str = Field(default="", description="e.g. 'adult', 'pediatric >=1 year'.")
    tga_status: str = Field(default=UNKNOWN, description="approved | not_approved | unknown (ARTG/PI).")
    tga_date: str = Field(default="", description="TGA approval date/year.")
    tga_evidence_url: str = Field(default="", description="Official ARTG / PI link.")
    pbs_status: str = Field(default=UNKNOWN, description="approved | not_approved | unknown (PBS).")
    pbs_date: str = Field(default="", description="PBS listing date/year.")
    pbs_evidence_url: str = Field(default="", description="Official pbs.gov.au link.")


class ApprovalByIndication(BaseModel):
    """Stage 3 (judgement): the drug's TGA/PBS approvals, one entry per indication."""

    indications: list[ApprovedIndication] = Field(
        default_factory=list,
        description="Every distinct TGA/PBS ONCOLOGY indication. [] if none in Australia.",
    )


class ReviewVerdict(BaseModel):
    """A reviewer's verdict on one stage (verdict + problems only — never a rewritten artifact)."""

    faithful: bool
    problems: list[str] = Field(default_factory=list, description="Concrete, actionable issues if not faithful.")
