"""Specialist agents for the extraction task (spec §6; audit doc §2-§3).

Extraction agents (source-dependent):
- extractor:        relevant trial text + COHORTS list -> scoped DNF rows (5 eligibility columns).
- drug (ANZCTR):    INTERVENTIONS text -> raw drug names.
- cohort (ANZCTR):  conservative detection of distinct-eligibility cohorts (default single).

Review panel (source-independent), run in parallel; each has full context, focused prompt:
- cancer_type (strengthened false-positive check), molecular (+ column correctness),
  prior_therapy, drug (advisory), structural (DNF integrity + cohort assignment).

Column taxonomy mirrors pydantic_curator/criterion_schema.py. No OncoTree / finding-model
conversion here — this stage extracts normalized human descriptions only.
"""
from __future__ import annotations

from dataclasses import dataclass

from aus_trial_universe.agentic.core.agent import Agent
from aus_trial_universe.agentic.core.client import LlmClient
from aus_trial_universe.agentic.tasks.extraction.schema import (
    CohortDetection,
    DrugExtraction,
    EligibilityExtraction,
    JudgeVerdict,
)

# --------------------------------------------------------------------------- #
# Extraction agents
# --------------------------------------------------------------------------- #
EXTRACTOR_INSTRUCTIONS = """\
You are given the relevant sections of a clinical trial (titles, summary, description, conditions, \
keywords, eligibility/inclusion/exclusion criteria, interventions) and a COHORTS list. From ALL the \
text, extract the trial's eligibility into a normalized table of DNF rows over these FIVE columns ONLY:

- cancer_type: the required cancer/tumour type under study (site + histology + stage/extent), in the \
trial's own words (e.g. "metastatic NSCLC"). Do NOT put other/prior malignancies here, and do NOT \
treat a tumour mentioned only in a prior-therapy or medical-history phrase as the cancer type.
- gene_alteration: a required SPECIFIC gene + alteration, DNA/mRNA-level (e.g. "EGFR exon 19 deletion", \
"KRAS G12C", "ALK fusion", "ERBB2 amplification").
- molecular_signature: a required COMPOSITE/genomic signature not tied to one gene's variant \
(e.g. "MSI-H", "TMB-high", "HRD", "genomic instability", "1p/19q codeletion").
- molecular_biomarker: a required EXPRESSION-based biomarker, mostly protein/IHC \
(e.g. "PD-L1 >=1% (IHC)", "HER2 IHC 3+", "ER positive", "dMMR (IHC)").
- prior_therapy: a prior-treatment condition that CONSTRAINS eligibility — either REQUIRED (e.g. \
">=1 prior platinum line", "treatment-naive") or EXCLUDED (wrap in NOT(), e.g. "NOT(prior anti-PD-1)"). \
A prior therapy that is merely PERMITTED/ALLOWED (neither required nor disqualifying) does NOT restrict \
eligibility — OMIT it.

Column edge rules:
- HER2/ERBB2: expression or IHC -> molecular_biomarker; gene amplification -> gene_alteration.
- MMR: dMMR/pMMR by IHC -> molecular_biomarker; MSI-H (genomic) -> molecular_signature.
- Histology (adenocarcinoma, squamous, etc.) -> fold into cancer_type.
- Ignore everything else (age, labs, performance status, comorbidities, other/prior malignancy, \
reproductive status, drug/intervention names) — only the five columns above.

Cohort assignment — set each row's `cohort`:
- If a requirement applies ONLY to a specific cohort in the COHORTS list, set cohort to that cohort's id (e.g. "C1").
- If it applies to the whole trial / all patients, set cohort to "trial-wide".
- When in doubt (or the trial has a single cohort), use "trial-wide".

DNF rules:
- One row = one satisfiable combination of requirements (a conjunction: all cells ANDed).
- If eligibility offers alternatives (OR), emit one row per alternative.
- Conditionals become co-occurrence: "if <cancer A> then <mutation X>; if <cancer B> then <mutation Y>" \
-> two rows: (cancer=A, gene=X) and (cancer=B, gene=Y).
- Use "" for any column not required by a row.

Negation (inclusion AND exclusion are BOTH in scope):
- Wrap an excluded criterion in NOT(...), e.g. prior_therapy = "NOT(prior EGFR TKI)".
- A single cell holds the FULL requirement for its criterion in that row and may hold several ANDed \
terms; wrap excluded ones in NOT(). Same-column carve-outs stay in ONE cell: \
"solid tumours except melanoma" -> cancer_type = "solid tumour AND NOT(melanoma)". Only genuine \
OR-alternatives split into rows.

Provenance — for EVERY non-empty value, list which input section(s) it came from:
- Sections are headed "## <LABEL>". Put the exact LABEL(s) into that column's *_sources list; if a value \
is supported by several sections, list ALL of them. Leave *_sources empty for empty columns.
"""

DRUG_EXTRACTOR_INSTRUCTIONS = """\
From the trial text (especially the INTERVENTIONS section), extract the investigational and comparator \
drug/treatment names administered in the trial. Return the names as stated (RAW — no normalization, no \
RxNorm). Exclude dosing/schedule prose. Exclude placebo unless it is the only comparator. Return [] if none.
"""

COHORT_DETECTOR_INSTRUCTIONS = """\
Identify whether this trial has DISTINCT patient cohorts with DIFFERENT eligibility.

Most trials have a SINGLE cohort. Only return multiple cohorts when the trial EXPLICITLY defines separate \
patient groups selected by different eligibility (e.g. a basket/umbrella trial with per-group tumour or \
molecular selection). Do NOT treat the arms of a randomised trial (same eligibility, different treatment) \
as separate cohorts. If there is any doubt, return an EMPTY list (meaning one trial-wide cohort).
"""


def build_extractor_agent(client: LlmClient, *, model: str | None = None) -> Agent[EligibilityExtraction]:
    return Agent(name="eligibility_extractor", instructions=EXTRACTOR_INSTRUCTIONS,
                 output_schema=EligibilityExtraction, client=client, model=model)


def build_drug_agent(client: LlmClient, *, model: str | None = None) -> Agent[DrugExtraction]:
    return Agent(name="drug_extractor", instructions=DRUG_EXTRACTOR_INSTRUCTIONS,
                 output_schema=DrugExtraction, client=client, model=model)


def build_cohort_detector_agent(client: LlmClient, *, model: str | None = None) -> Agent[CohortDetection]:
    return Agent(name="cohort_detector", instructions=COHORT_DETECTOR_INSTRUCTIONS,
                 output_schema=CohortDetection, client=client, model=model)


# --------------------------------------------------------------------------- #
# Reviewer panel
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ReviewerSpec:
    key: str
    gating: bool          # does a failure gate/loop the refine? (drug is advisory)
    instructions: str


_REVIEW_PREAMBLE = """\
You audit ONE dimension of an extracted eligibility DNF table against the provided trial text. \
Cells may hold inline logic (ANDed terms; exclusions wrapped in NOT()) and a trailing "[SECTION; ...]" \
provenance tag — the tag is an annotation, not a criterion. Judge only your dimension below; set \
faithful=false with concrete, actionable problems if anything is missing, invented, mis-paired, \
mis-columned, or mis-scoped for YOUR dimension; otherwise faithful=true.
"""

REVIEWERS: tuple[ReviewerSpec, ...] = (
    ReviewerSpec("cancer_type", True, _REVIEW_PREAMBLE + """
DIMENSION = cancer_type. Check every row's cancer_type is faithful AND is genuinely the trial's tumour \
UNDER STUDY. Flag FALSE POSITIVES: a tumour that appears only inside a prior-therapy phrase, medical \
history, an exclusion of other malignancies, or an example is NOT the trial's cancer type (e.g. \
"progressed after therapy for melanoma" in a lung trial -> melanoma must NOT be a cancer_type). Also flag \
missing tumour types and mis-placed histology/stage."""),
    ReviewerSpec("molecular", True, _REVIEW_PREAMBLE + """
DIMENSION = gene_alteration + molecular_signature + molecular_biomarker. Check faithfulness AND that each \
value is in the RIGHT column: specific gene+alteration -> gene_alteration; composite/genomic signature -> \
molecular_signature; expression/IHC/protein -> molecular_biomarker. Watch the edge rules: HER2/ERBB2 \
(expression->biomarker, amplification->gene_alteration); MMR (dMMR/pMMR IHC->biomarker, MSI-H->signature)."""),
    ReviewerSpec("prior_therapy", True, _REVIEW_PREAMBLE + """
DIMENSION = prior_therapy. Only REQUIRED prior therapies (positive) and EXCLUDED ones (wrapped in NOT())
are eligibility constraints. A merely PERMITTED/ALLOWED prior therapy (neither required nor disqualifying)
is NOT a constraint and should be OMITTED — do NOT flag its absence. Flag missing REQUIRED/EXCLUDED
conditions and wrong/missing negation."""),
    ReviewerSpec("drug", False, _REVIEW_PREAMBLE + """
DIMENSION = drug. Each cohort's drug(s) are listed in the COHORTS section — NOT in the eligibility rows
(eligibility is cohort-agnostic; drugs are assigned per cohort separately downstream). Check each cohort's
listed drug(s) match the intervention(s) administered to that cohort per the source; flag wrong, missing,
or extraneous drugs. Do NOT ask for a drug column in the eligibility table; ignore normalization/formatting."""),
    ReviewerSpec("structural", True, _REVIEW_PREAMBLE + """
DIMENSION = DNF structure & cohort scope. Check conjunctions/conditionals are correct rows, no OR-\
alternative is missing or wrongly merged, and each requirement's cohort scope is right (cohort-specific \
requirements assigned to their cohort; shared ones 'trial-wide')."""),
)


def build_reviewer_agents(client: LlmClient, *, model: str | None = None) -> list[tuple[ReviewerSpec, Agent[JudgeVerdict]]]:
    return [
        (spec, Agent(name=f"reviewer_{spec.key}", instructions=spec.instructions,
                     output_schema=JudgeVerdict, client=client, model=model))
        for spec in REVIEWERS
    ]
