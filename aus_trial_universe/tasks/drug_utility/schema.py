"""Drug-reference table schemas (spec §6.1).

The persisted TSV tables are plain dataclasses (one row each); their column order derives from the field
order so the schema is the single source of truth. LLM I/O schemas (pydantic) drive the *judgement* stages
(canonicalize / annotate / approvals); the *deterministic* facts (rxcui / pottr_drug_class / atc_code) are
NOT produced by the LLM — they come from the RxNorm / POTTR / ATC lookups (rxnorm.py resolves both rxcui and
the ATC code from RXNCONSO.RRF; pottr.py resolves the drug class).

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

# Drug role within a trial ARM (Phase 2): the investigational/defining agent(s) vs. everything else.
ROLE_MAIN = "main"            # the agent(s) the trial is actually testing (usually the experimental-arm drug)
ROLE_AUXILIARY = "auxiliary"  # comparator / chemo backbone / standard-of-care / placebo / premedication
ROLES = (ROLE_MAIN, ROLE_AUXILIARY)

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
    canonical_id: str = ""              # FK -> DrugAnnotationsCore.canonical_id (namespaced: rxcui:<n> | name:<x>); "" if non-drug


# --- Table 1b: which trial ARM each intervention name came from --------------- #
@dataclass
class TrialToIntervention:
    """Provenance (the traceability record): a trial ARM used an intervention name. Many-to-many — one input name
    can appear in several arms, and an arm has several intervention names. The arm identity (trialId / registry /
    arm label / arm_type) lives ONCE in the shared `trial_arms` registry; this table links to it by `trial_arm_id`.
    FK input_intervention_name -> InterventionToCanonical (mapping looked up once per string, reused across uses)."""

    trial_arm_id: str = ""              # FK -> shared trial_arms.trial_arm_id (the (trialId, arm) slug)
    input_intervention_name: str = ""   # FK -> InterventionToCanonical.input_intervention_name


# --- Table 2: canonical drug -> intrinsic, drug-level facts ------------------ #
@dataclass
class DrugAnnotationsCore:
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
class DrugTargetAction:
    """One (target, action) pair — a drug acts on each target via a specific action, and a multi-target
    drug has different actions per target (dordaviprone: ClpP=activator, DRD2=antagonist), so they are kept
    PAIRED, one row per target. `target` is the queryable matching dimension."""

    canonical_id: str = ""   # FK -> DrugAnnotationsCore.canonical_id
    target: str = ""         # molecular target / pathway, e.g. "PD-1", "TOP1", "ClpP", "B7-H3"
    action: str = ""         # inhibitor / antagonist / agonist / activator / degrader / ADC-binding / …
    note: str = ""           # nuance, e.g. "payload" / "antigen" / "dual"


# --- Table 4: (canonical, indication) -> TGA/PBS approval (indication-specific) #
@dataclass
class DrugRegulatoryApproval:
    """One approved indication, captured AS THE REGULATOR STATES IT, decomposed into a static, comprehensive
    set of free-text components (an indication is often cancer + biomarker + line/stage + combination, etc.).
    TGA and PBS are independent columns on the same row (both depend fully on the key; only two fixed AU
    agencies). DEFERRED (user): mapping the components into the eligibility vocabulary (OncoTree + finding-model)
    for symmetric matching — comes with the trial-link."""

    canonical_id: str = ""       # FK -> DrugAnnotationsCore.canonical_id
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


# --- Table 5: (trial ARM, canonical drug) -> role (main / auxiliary) ---------- #
@dataclass
class TrialArmDrugRole:
    """Phase 2: the role a drug plays IN a given trial ARM — `main` (the investigational/defining agent(s) the
    trial is actually testing) vs. `auxiliary` (comparator / chemo backbone / standard-of-care / placebo /
    premedication). Kept at the (arm, canonical drug) grain — NOT on `trial_to_intervention`, whose input strings
    can bundle several drugs of differing roles — so it joins cleanly to `drug_regulatory_approvals` on
    canonical_id (per-main TGA/PBS). One row per distinct (trial_arm_id, canonical_id); non-drug inputs (empty
    canonical_id) get no row. The (arm, canonical) pairs are exactly those derivable from
    `trial_to_intervention` ⋈ `intervention_to_canonical` (no orphans)."""

    trial_arm_id: str = ""   # FK -> shared trial_arms.trial_arm_id (the (trialId, arm) slug)
    canonical_id: str = ""   # FK -> DrugAnnotationsCore.canonical_id
    role: str = ""           # one of ROLES (main | auxiliary)


# --- Table 6 + 7: symmetric-match vocab for the approval free-text (indication-specific matching) --- #
# The regulatory-approval indication is matched against a trial's eligibility on the SAME axes the trial side uses:
# OncoTree code (cancer_type) and finding-model (gene/signature). These two per-value lookup tables map the
# `drug_regulatory_approvals` free-text `cancer_type` / `biomarker` into that vocab — the mapping is a pure function
# of the free-text value (independent of which indication/drug uses it), so it is 3NF single-key, exactly like the
# eligibility side's value->vocab maps. The 5 core drug tables + `trial_arm_drug_role` are unchanged.
@dataclass
class ApprovalCancerTypeMap:
    """One distinct approval `cancer_type` free-text value -> OncoTree. 3NF single-key (keyed by cancer_type).

    ⚠ EVERY MAPPED COLUMN HERE IS THE **FINALISED** VALUE — the output of the full three-stage pipeline
    (1 translate -> 2 canonicalise -> 3 apply the approved register), the same code path and the same register the
    eligibility side uses. The `_finalised` suffix is deliberately NOT repeated in the column names: on this side
    there is only ever one answer per value, so a suffix would be noise. It is implicit, and it is what the
    matching engine compares against `oncotree_code_finalised` on the trial side.

    The intermediate stages are deliberately NOT persisted here. Eligibility keeps all three tables because its
    values are reviewed and the provenance chain is what a reviewer reads; the drug side is not separately
    reviewed, so only the shipped answer is stored."""

    cancer_type: str = ""            # the lookup key: the free-text value as stated in drug_regulatory_approvals
    oncotree_name: str = ""          # OncoTree name expression, rendered FROM oncotree_code (never authored)
    oncotree_code: str = ""          # the FINALISED code expression — the matchable key


@dataclass
class ApprovalBiomarkerMap:
    """One distinct approval `biomarker` free-text value, split into the SAME three buckets the trial side uses and
    the gene/signature parts rendered in finding-model. `molecular_biomarker` (protein-expression / IHC: PD-L1, CD20,
    hormone-receptor, HER2-IHC) has no finding-model representation and stays free text — symmetric with the trial
    side, whose `molecular_biomarker` column is likewise never vocab-mapped. All columns are a function of the single
    `biomarker` key -> 3NF single-key lookup.

    ⚠ Like the cancer_type map, the finding-model columns are the **FINALISED** values (the three-stage output);
    the `_finalised` suffix is implicit rather than repeated in every column name."""

    biomarker: str = ""                         # the lookup key: the free-text value as stated
    gene_alteration: str = ""                   # the gene-alteration part of the split ("" if none)
    molecular_signature: str = ""               # the composite/genomic-signature part of the split ("" if none)
    molecular_biomarker: str = ""               # the expression/IHC part — carried as free text (no vocab)
    gene_alteration_findingmodel: str = ""      # finding-model for gene_alteration (== trial-side rendering)
    molecular_signature_findingmodel: str = ""  # finding-model for molecular_signature


def _columns(dc) -> list[str]:
    return [f.name for f in fields(dc)]


INTERVENTION_TO_CANONICAL_COLUMNS = _columns(InterventionToCanonical)
TRIAL_TO_INTERVENTION_COLUMNS = _columns(TrialToIntervention)
DRUG_ANNOTATIONS_CORE_COLUMNS = _columns(DrugAnnotationsCore)
DRUG_TARGET_ACTIONS_COLUMNS = _columns(DrugTargetAction)
DRUG_REGULATORY_APPROVALS_COLUMNS = _columns(DrugRegulatoryApproval)
TRIAL_ARM_DRUG_ROLE_COLUMNS = _columns(TrialArmDrugRole)
APPROVAL_CANCER_TYPE_MAP_COLUMNS = _columns(ApprovalCancerTypeMap)
APPROVAL_BIOMARKER_MAP_COLUMNS = _columns(ApprovalBiomarkerMap)

TABLE_FILES = {
    "intervention_to_canonical": "intervention_to_canonical.tsv",
    "trial_to_intervention": "trial_to_intervention.tsv",
    "drug_annotations_core": "drug_annotations_core.tsv",
    "drug_target_actions": "drug_target_actions.tsv",
    "drug_regulatory_approvals": "drug_regulatory_approvals.tsv",
    "trial_arm_drug_role": "trial_arm_drug_role.tsv",
    "approval_cancer_type_map": "mapped_approval_cancer_type.tsv",
    "approval_biomarker_map": "mapped_approval_biomarker.tsv",
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


class ArmDrugRole(BaseModel):
    """One drug's role within a trial arm (Phase 2 judgement)."""

    canonical_id: str = Field(description="The canonical_id of THIS drug, echoed back EXACTLY as given in the input "
                                          "(the join key). Do NOT invent, alter, or omit any given id.")
    role: str = Field(description=f"EXACTLY ONE of: {ROLE_MAIN} | {ROLE_AUXILIARY}. "
                                  f"{ROLE_MAIN} = the investigational/defining agent(s) the trial is testing; "
                                  f"{ROLE_AUXILIARY} = comparator / backbone / standard-of-care / placebo / premedication.")


class ArmRoleClassification(BaseModel):
    """Stage (judgement): assign every drug in ONE trial arm a main/auxiliary role. One assignment per input drug —
    same set of canonical_ids as given, none added or dropped."""

    assignments: list[ArmDrugRole] = Field(
        default_factory=list,
        description="One entry per drug given for this arm (echo each canonical_id exactly). [] only if no drugs.")
    notes: str = Field(default="", description="One line of reasoning (which agent is under study vs. backbone).")


class BiomarkerSplit(BaseModel):
    """Split ONE approval `biomarker` free-text phrase into the SAME three buckets the trial eligibility side uses,
    so the two can be matched. Each part is copied out verbatim (in the source wording), never invented; a part with
    nothing to carry stays "". A composite phrase may populate several buckets (e.g. 'HR-positive, HER2-negative,
    PIK3CA mutation' -> molecular_biomarker='HR-positive, HER2-negative', gene_alteration='PIK3CA mutation')."""

    gene_alteration: str = Field(default="", description=(
        "The SPECIFIC gene + alteration part (DNA/mRNA-level), e.g. 'BRAF V600E mutation', 'EGFR exon 19 deletion', "
        "'KRAS G12C', 'ALK rearrangement', 'HER2 amplification', 'RET fusion'. \"\" if none."))
    molecular_signature: str = Field(default="", description=(
        "The COMPOSITE/genomic-signature part not tied to one gene's variant, e.g. 'MSI-H', 'dMMR (genomic)', "
        "'TMB-high', 'HRD'. \"\" if none."))
    molecular_biomarker: str = Field(default="", description=(
        "The protein-EXPRESSION / receptor / IHC-status part, e.g. 'PD-L1 CPS >=1', 'HER2-positive (IHC)', "
        "'CD20-positive', 'hormone receptor-positive', 'PSMA-positive'. Also the home for any remaining "
        "non-gene/non-signature qualifier that is a molecular subgroup. \"\" if none."))


class ReviewVerdict(BaseModel):
    """A reviewer's verdict on one stage (verdict + problems only — never a rewritten artifact)."""

    faithful: bool
    problems: list[str] = Field(default_factory=list, description="Concrete, actionable issues if not faithful.")
