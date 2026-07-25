"""Output schemas for the extraction task (see docs/v2_agentic_pipeline_spec.md §7).

Extraction runs in TWO sub-stages (the verbatim text and its logical interpretation are different entities):

  I-a  RAW extraction  — copy the VERBATIM relevant source span(s) for each criterion, tagged with the ONE
                         section each came from and the scope (a cohort id or trial-wide). No logic, no
                         paraphrase. Product: ``RawExtraction`` -> the ``arm_eligibility_raw`` table.
  I-b  INTERPRETATION  — read the raw spans and produce the DNF table: one row = one satisfiable conjunction,
                         rows sharing a (trialId, cohort) are ORed, cells hold normalized human descriptions
                         with inline ``NOT(...)`` exclusions (NO source tags — those live in the raw table).
                         Product: ``EligibilityExtraction`` -> the ``interpreted_eligibility`` table.

The OR-branch structure is decided in I-b (interpretation), which is exactly why the raw text (per arm) and the
interpreted conjunctions (per conjunction) are separate tables.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

TRIAL_WIDE = "trial-wide"

# The five eligibility criterion stems (shared by both sub-stages).
CRITERION_STEMS = [
    "cancer_type", "gene_alteration", "molecular_signature", "molecular_biomarker", "prior_therapy",
]


# --- Stage I-a: RAW extractor I/O ------------------------------------------- #
class RawFragment(BaseModel):
    """One verbatim source span relevant to one criterion, with its single source section and scope."""

    criterion: str = Field(
        description=(
            "Which criterion column this span informs — EXACTLY one of: cancer_type, gene_alteration, "
            "molecular_signature, molecular_biomarker, prior_therapy."
        ),
    )
    scope: str = Field(
        default=TRIAL_WIDE,
        description=(
            "Scope of this span: a cohort id from the COHORTS list (e.g. 'C1') if it applies ONLY to that "
            "cohort/regime, or 'trial-wide' if it applies to the whole trial / all cohorts."
        ),
    )
    text: str = Field(
        description=(
            "The relevant source span copied VERBATIM (the complete clause, keeping connectives like "
            "'or' / 'and' / commas and any 'except/excluding' carve-out). Do NOT paraphrase, summarize, "
            "truncate, or add words."
        ),
    )
    source: str = Field(
        description="The ONE input section header (the '## <LABEL>') this span was copied from.",
    )


class RawExtraction(BaseModel):
    """The raw extractor's output: every relevant verbatim span, classified to a criterion + scope + source."""

    fragments: list[RawFragment]


# --- Stage I-b: interpreter I/O --------------------------------------------- #
class ExtractedRow(BaseModel):
    """One DNF conjunction (the INTERPRETATION of the raw spans). Every cell is the *complete* requirement for
    its criterion within this conjunction: several ANDed terms allowed, excluded terms wrapped in NOT(), "" when
    not required. No source tags — provenance lives in the raw table."""

    cohort: str = Field(
        default=TRIAL_WIDE,
        description=(
            "Scope of this requirement: a cohort id from the supplied COHORTS list (e.g. 'C1') if it applies "
            "ONLY to that cohort, or 'trial-wide' if it applies to the whole trial / all cohorts."
        ),
    )
    cancer_type: str = Field(
        default="",
        description=(
            "Required cancer/tumour type under study (site + histology + stage/extent), e.g. 'metastatic NSCLC'. "
            "Exclude other/prior malignancies. Carve-outs use NOT(), e.g. 'solid tumour AND NOT(melanoma)'."
        ),
    )
    gene_alteration: str = Field(
        default="",
        description=(
            "Required specific gene + alteration (DNA/mRNA-level), e.g. 'EGFR exon 19 deletion', 'KRAS G12C', "
            "'ALK fusion', 'ERBB2 amplification'. \"\" if none."
        ),
    )
    molecular_signature: str = Field(
        default="",
        description=(
            "Required composite/genomic signature not tied to one gene's variant, e.g. 'MSI-H', 'TMB-high', "
            "'HRD', 'genomic instability', '1p/19q codeletion'. \"\" if none."
        ),
    )
    molecular_biomarker: str = Field(
        default="",
        description=(
            "Required expression-based biomarker (mostly protein/IHC), e.g. 'PD-L1 >=1% (IHC)', 'HER2 IHC 3+', "
            "'ER positive', 'dMMR (IHC)'. \"\" if none."
        ),
    )
    prior_therapy: str = Field(
        default="",
        description=(
            "Required prior-treatment condition, e.g. '>=1 prior platinum line', 'prior anti-PD-1', "
            "'treatment-naive'. \"\" if none."
        ),
    )


class EligibilityExtraction(BaseModel):
    """The interpreter's output: the trial's eligibility as scoped DNF rows.

    Each row is a conjunction (all cells ANDed); the set of rows is the OR of the alternatives. Conditionals
    become co-occurrence: 'if cancer A then mutation X; if cancer B then mutation Y' -> two rows (A,X) and (B,Y).
    """

    rows: list[ExtractedRow]


# NB: the ANZCTR drug-extractor I/O (`DrugExtraction`) moved to `tasks/shared/agents.py` — ANZCTR arm
# identification is now a path-neutral shared module used by both the eligibility and drug-utility paths.


# --- Reviewer I/O ----------------------------------------------------------- #
class JudgeVerdict(BaseModel):
    """One reviewer's verdict on its dimension of the extracted table."""

    faithful: bool
    problems: list[str] = Field(
        default_factory=list,
        description="Concrete, actionable issues (missing/invented/mis-paired/mis-columned/mis-scoped) if not faithful.",
    )
    suggested_fix: str = Field(
        default="",
        description=(
            "OPTIONAL. When not faithful, a concrete corrected value / row / span that would resolve the problems "
            "(the exact text you would expect). This is ADVICE handed to the writer, which regenerates and is "
            "re-checked — it is NOT applied directly. Leave empty if you cannot propose a specific correction."
        ),
    )


# --- Final table row (interpreted DNF; internal) ---------------------------- #
class DnfRow(BaseModel):
    """A row of the interpreted eligibility DNF table (one satisfiable conjunction). Eligibility cells are the
    interpreted logic (no source tags); ``drug`` is the cohort's intervention (raw, tagged, for the drug path)."""

    trialId: str
    cohort: str
    arm_type: str
    cancer_type: str
    gene_alteration: str
    molecular_signature: str
    molecular_biomarker: str
    prior_therapy: str
    drug: str
