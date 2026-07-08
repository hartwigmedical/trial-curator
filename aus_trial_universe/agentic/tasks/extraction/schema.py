"""Output schemas for the extraction task (spec §6; audit doc §2-§3).

The product is a DNF table: one row = one satisfiable conjunction; rows sharing a
(trialId, cohort) are ORed. Cells hold pre-curation *normalized human descriptions*
(NO OncoTree / finding-model conversion yet — that is a later stage).

Each extracted cell carries:
- inline ``NOT(...)`` for excluded criteria; a cell may hold several ANDed terms
  (e.g. ``solid tumour AND NOT(melanoma)``) — only OR-alternatives split into rows.
- a provenance tag ``[SRC1; SRC2]`` naming every input section it was found in.

Each extracted row also carries a ``cohort`` scope: a cohort id from the supplied
list (e.g. ``C1``) or ``trial-wide``. Cohort-specific criteria are AND-combined with
the trial-wide criteria per cohort downstream (representation A).
"""
from __future__ import annotations

from pydantic import BaseModel, Field

TRIAL_WIDE = "trial-wide"


# --- Eligibility extractor I/O ---------------------------------------------- #
class ExtractedRow(BaseModel):
    """One DNF conjunction. Every cell is the *complete* requirement for its
    criterion type within this conjunction: multiple ANDed terms allowed, excluded
    terms wrapped in NOT(), "" when not required. Each value has a parallel
    ``*_sources`` list of input section header(s) it was drawn from."""

    cohort: str = Field(
        default=TRIAL_WIDE,
        description=(
            "Scope of this requirement: a cohort id from the supplied COHORTS list "
            "(e.g. 'C1') if it applies ONLY to that cohort, or 'trial-wide' if it "
            "applies to the whole trial / all cohorts."
        ),
    )
    cancer_type: str = Field(
        default="",
        description=(
            "Required cancer/tumour type under study (site + histology + stage/extent), "
            "in the trial's words, e.g. 'metastatic NSCLC'. Exclude other/prior malignancies. "
            "Carve-outs use NOT(), e.g. 'solid tumour AND NOT(melanoma)'."
        ),
    )
    cancer_type_sources: list[str] = Field(default_factory=list)
    gene_alteration: str = Field(
        default="",
        description=(
            "Required specific gene + alteration (DNA/mRNA-level), e.g. 'EGFR exon 19 deletion', "
            "'KRAS G12C', 'ALK fusion', 'ERBB2 amplification'. \"\" if none."
        ),
    )
    gene_alteration_sources: list[str] = Field(default_factory=list)
    molecular_signature: str = Field(
        default="",
        description=(
            "Required composite/genomic signature not tied to one gene's variant, "
            "e.g. 'MSI-H', 'TMB-high', 'HRD', 'genomic instability', '1p/19q codeletion'. \"\" if none."
        ),
    )
    molecular_signature_sources: list[str] = Field(default_factory=list)
    molecular_biomarker: str = Field(
        default="",
        description=(
            "Required expression-based biomarker (mostly protein/IHC), e.g. 'PD-L1 >=1% (IHC)', "
            "'HER2 IHC 3+', 'ER positive', 'dMMR (IHC)'. \"\" if none."
        ),
    )
    molecular_biomarker_sources: list[str] = Field(default_factory=list)
    prior_therapy: str = Field(
        default="",
        description=(
            "Required prior-treatment condition, e.g. '>=1 prior platinum line', 'prior anti-PD-1', "
            "'treatment-naive'. \"\" if none."
        ),
    )
    prior_therapy_sources: list[str] = Field(default_factory=list)


class EligibilityExtraction(BaseModel):
    """The extractor's output: the trial's eligibility as scoped DNF rows.

    Each row is a conjunction (all cells ANDed); the set of rows is the OR of the
    alternatives. Conditionals become co-occurrence: 'if cancer A then mutation X;
    if cancer B then mutation Y' -> two rows (A,X) and (B,Y).
    """

    rows: list[ExtractedRow]


# --- ANZCTR drug extractor I/O ---------------------------------------------- #
class DrugExtraction(BaseModel):
    """Investigational + comparator drug/treatment names, raw (no normalization)."""

    drugs: list[str] = Field(
        default_factory=list,
        description="Drug/treatment names administered in the trial, as stated. [] if none.",
    )


# --- ANZCTR cohort-detection I/O -------------------------------------------- #
class DetectedCohort(BaseModel):
    label: str = Field(description="Short human-readable cohort name.")
    description: str = Field(default="", description="What distinguishes this cohort's eligibility.")


class CohortDetection(BaseModel):
    """Conservative cohort detection: empty list => a single trial-wide cohort."""

    cohorts: list[DetectedCohort] = Field(
        default_factory=list,
        description="Distinct patient groups with DIFFERENT eligibility. Empty if the trial is a single cohort.",
    )


# --- Reviewer I/O ----------------------------------------------------------- #
class JudgeVerdict(BaseModel):
    """One reviewer's verdict on its dimension of the extracted table."""

    faithful: bool
    problems: list[str] = Field(
        default_factory=list,
        description="Concrete, actionable issues (missing/invented/mis-paired/mis-columned/mis-scoped) if not faithful.",
    )


# --- Final table row -------------------------------------------------------- #
class DnfRow(BaseModel):
    """A row of the final eligibility DNF table (one satisfiable conjunction).

    Eligibility cells are rendered ``value [sources]``; ``drug`` is the cohort's
    intervention (raw, not normalized).
    """

    trialId: str
    cohort: str
    arm_type: str
    cancer_type: str
    gene_alteration: str
    molecular_signature: str
    molecular_biomarker: str
    prior_therapy: str
    drug: str
