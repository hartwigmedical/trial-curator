"""Output schemas for the eligibility-extraction task (spec §6).

The product is a DNF table: one row = one satisfiable conjunction; rows sharing a
(trialId, cohort) are ORed. Cells hold pre-curation *normalized* terms (cancer
type as OncoTree name + code; gene alteration as a normalized human description).
"""
from __future__ import annotations

from pydantic import BaseModel, Field


# --- Extractor agent I/O ---------------------------------------------------- #
class ExtractedRow(BaseModel):
    """One DNF conjunction as first extracted (cancer type still free text)."""

    cancer_type: str = Field(
        description="Cancer type this row requires, in the trial's own words (e.g. 'metastatic NSCLC')."
    )
    gene_alteration: str = Field(
        default="",
        description=(
            "Gene alteration this row requires, as a normalized human description "
            "(e.g. 'EGFR exon 19 deletion', 'BRAF V600E'). Empty string if the row "
            "has no molecular requirement."
        ),
    )


class EligibilityExtraction(BaseModel):
    """The extractor's output: the cohort's eligibility expressed as DNF rows.

    Each row is a conjunction (all cells ANDed); the set of rows is the OR of the
    cohort's alternatives. Conditionals become co-occurrence: 'if cancer A then
    mutation X; if cancer B then mutation Y' -> two rows (A,X) and (B,Y).
    """

    rows: list[ExtractedRow]


# --- cancer_type -> OncoTree specialist I/O -------------------------------- #
class OncotreeMapping(BaseModel):
    """OncoTree normalization for one free-text cancer-type mention."""

    oncotree_name: str = Field(description="Closest OncoTree cancer-type name.")
    oncotree_code: str = Field(description="Its OncoTree code (e.g. 'LUAD').")


# --- Faithfulness judge I/O ------------------------------------------------- #
class JudgeVerdict(BaseModel):
    """Adversarial check: does the DNF table faithfully represent the source text?"""

    faithful: bool
    problems: list[str] = Field(
        default_factory=list,
        description="Concrete, actionable issues (missing/invented/mis-paired criteria) if not faithful.",
    )


# --- Final table row -------------------------------------------------------- #
class DnfRow(BaseModel):
    """A row of the final eligibility DNF table (one satisfiable conjunction)."""

    trialId: str
    cohort: str
    cancer_type: str  # as stated in the trial
    oncotree_name: str  # normalized (pre-curation)
    oncotree_code: str
    gene_alteration: str
