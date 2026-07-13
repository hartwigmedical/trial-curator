"""Drug-reference table schemas (spec §6.1).

The three persisted TSV tables are plain dataclasses (one row each); their column order is derived
from the field order so the schema is the single source of truth. LLM I/O schemas (pydantic) for the
canonicalize / annotate / approval agents live alongside the agents and populate these rows.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, fields

from pydantic import BaseModel, Field

# Approval status vocabulary (per agency, per indication).
APPROVED = "approved"
NOT_APPROVED = "not_approved"
UNKNOWN = "unknown"


# --- Table 1: raw name (as written) -> canonical identity -------------------- #
@dataclass
class DrugAlias:
    raw_name: str = ""       # exactly as written in the registry (the lookup key)
    canonical_id: str = ""   # FK -> DrugRef.canonical_id


# --- Table 2: canonical drug -> intrinsic, drug-level facts ------------------ #
@dataclass
class DrugRef:
    canonical_id: str = ""       # RxNorm ingredient RXCUI where it exists, else a normalized-name id
    canonical_name: str = ""     # human-readable canonical (RxNorm ingredient name / best canonical)
    rxcui: str = ""              # "" for investigational / not-in-RxNorm agents
    aliases: str = ""            # "; "-joined brand names / synonyms
    modality: str = ""           # small molecule / mAb / ADC / bispecific / cell therapy / vaccine / …
    mechanism: str = ""          # molecular target(s) / mechanism of action
    drug_class: str = ""         # general drug class (used even when not in POTTR)
    pottr_drug_class: str = ""   # POTTR class hierarchy
    atc_code: str = ""           # WHO ATC classification code
    fda_status: str = ""         # coarse FDA approval summary (additional context)
    ema_status: str = ""         # coarse EMA approval summary (additional context)
    sources: str = ""            # compact per-fact citations, "field=url; ..." (cite always)
    researched_on: str = ""      # ISO date (YYYY-MM-DD) this canonical was last researched


# --- Table 3: (canonical, indication) -> TGA/PBS approval (indication-specific) #
@dataclass
class DrugIndication:
    """One approved indication, captured AS THE REGULATOR STATES IT. An indication is often
    cancer + biomarker (HER2+ breast, NSCLC PD-L1>=50%) and/or + line/stage, so it's decomposed into
    as-stated free-text components and the PK is a surrogate `indication_id` (a drug has many, incl.
    several per cancer type).

    DEFERRED (spec §6.1; user 2026-07-13): mapping these components into the eligibility vocabulary
    (OncoTree code + finding-model) — that's what enables the symmetric approval<->eligibility match, and
    it comes with the trial-link step, which is parked. For now these stay free text, faithful to the source.
    """

    canonical_id: str = ""       # FK -> DrugRef.canonical_id
    indication_id: str = ""      # surrogate id, unique within a canonical
    indication_raw: str = ""     # the indication exactly as the regulator states it (full text, audit)
    # --- as-stated components (free text; NOT yet mapped to the eligibility vocabulary) ---
    cancer_type: str = ""        # as stated, e.g. "melanoma", "NSCLC"
    biomarker: str = ""          # as stated, e.g. "PD-L1 >=50%", "HER2-positive", "MSI-H/dMMR"
    line_of_therapy: str = ""    # e.g. "1L", ">=2 prior lines"
    stage: str = ""              # e.g. "unresectable Stage III/IV"
    # --- per-agency approval + evidence ---
    tga_status: str = ""         # approved | not_approved | unknown
    tga_date: str = ""           # approval/registration date (evidence)
    tga_evidence_url: str = ""   # ARTG / PI link
    pbs_status: str = ""
    pbs_date: str = ""
    pbs_evidence_url: str = ""
    researched_on: str = ""


def _columns(dc) -> list[str]:
    return [f.name for f in fields(dc)]


DRUG_ALIAS_COLUMNS = _columns(DrugAlias)
DRUG_REF_COLUMNS = _columns(DrugRef)
DRUG_INDICATION_COLUMNS = _columns(DrugIndication)

TABLE_FILES = {
    "drug_alias": "drug_alias.tsv",
    "drug_ref": "drug_ref.tsv",
    "drug_indication": "drug_indication.tsv",
}


# --------------------------------------------------------------------------- #
# Canonical identity — keyed on the normalized canonical NAME (robust dedup; a web-searched RXCUI is
# noisy, so it is kept as an attribute, not the key). All aliases of one drug share this id.
# --------------------------------------------------------------------------- #
_ID_WS_RE = re.compile(r"\s+")


def canonical_id_for(canonical_name: str) -> str:
    norm = _ID_WS_RE.sub(" ", (canonical_name or "").strip().lower())
    return f"name:{norm}" if norm else ""


# --------------------------------------------------------------------------- #
# LLM I/O schemas (pydantic) for the doer/reviewer agents
# --------------------------------------------------------------------------- #
class Canonicalization(BaseModel):
    """Stage 1: raw drug name -> canonical identity (RxNorm ingredient, grounded, LLM judgement)."""

    canonical_name: str = Field(description="The canonical ingredient name (RxNorm ingredient where it exists; "
                                            "for an investigational agent, its best canonical/INN name).")
    rxcui: str = Field(default="", description="RxNorm RXCUI of the ingredient if found, else \"\".")
    aliases: list[str] = Field(default_factory=list, description="Brand names / synonyms for this drug.")
    is_investigational: bool = Field(default=False, description="True if not an approved/RxNorm drug (novel agent).")
    notes: str = Field(default="", description="Brief reasoning / RxNorm evidence.")


class DrugAnnotation(BaseModel):
    """Stage 2: intrinsic, drug-level facts (trial-independent)."""

    modality: str = Field(default="", description="small molecule / mAb / ADC / bispecific / cell therapy / vaccine / …")
    mechanism: str = Field(default="", description="Molecular target(s) / mechanism of action.")
    drug_class: str = Field(default="", description="Concise general drug class / mechanism.")
    pottr_drug_class: str = Field(default="", description="POTTR class hierarchy, root->leaf joined by ' -> '; \"\" if not in POTTR.")
    atc_code: str = Field(default="", description="WHO ATC classification code.")
    fda_status: str = Field(default="", description="Coarse FDA approval summary (e.g. 'approved 2016 (melanoma, NSCLC, …)').")
    ema_status: str = Field(default="", description="Coarse EMA approval summary.")
    sources: str = Field(default="", description="Per-fact citations, compact 'field=url; ...' (cite always).")


class ApprovedIndication(BaseModel):
    """One indication as the regulator states it (free text; NOT yet eligibility-vocabulary mapped)."""

    indication_raw: str = Field(description="The indication exactly as stated by the regulator (full text).")
    cancer_type: str = Field(default="", description="Cancer type as stated, e.g. 'melanoma', 'NSCLC'.")
    biomarker: str = Field(default="", description="Biomarker as stated, e.g. 'PD-L1 >=50%', 'HER2-positive', 'MSI-H/dMMR'.")
    line_of_therapy: str = Field(default="", description="e.g. '1L', '>=2 prior lines'.")
    stage: str = Field(default="", description="e.g. 'unresectable Stage III/IV'.")
    tga_status: str = Field(default=UNKNOWN, description="approved | not_approved | unknown (ARTG/PI).")
    tga_date: str = Field(default="", description="TGA approval/registration date/year (evidence).")
    tga_evidence_url: str = Field(default="", description="Official ARTG / PI link.")
    pbs_status: str = Field(default=UNKNOWN, description="approved | not_approved | unknown (PBS).")
    pbs_date: str = Field(default="", description="PBS listing date/year (evidence).")
    pbs_evidence_url: str = Field(default="", description="Official pbs.gov.au link.")


class ApprovalByIndication(BaseModel):
    """Stage 3: the drug's TGA/PBS approvals, one entry per indication (indication-specific)."""

    indications: list[ApprovedIndication] = Field(
        default_factory=list,
        description="Every distinct TGA/PBS ONCOLOGY indication (approved or explicitly not). [] if none in Australia.",
    )


class ReviewVerdict(BaseModel):
    """A reviewer's verdict on one drug-ref stage (verdict + problems only — never a rewritten artifact)."""

    faithful: bool
    problems: list[str] = Field(default_factory=list, description="Concrete, actionable issues if not faithful.")
