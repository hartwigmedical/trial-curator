"""Specialist agents for the extraction task (see docs/v2_agentic_pipeline_spec.md §7).

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
treat a tumour mentioned only in a prior-therapy or medical-history phrase as the cancer type. \
NEVER AND two DIFFERENT cancer types in one cell — a patient has ONE tumour type. The CONDITIONS section \
is AUTHORITATIVE for the tumour type(s): if the eligibility text uses a broad umbrella ("advanced solid \
tumours", "any cancer") but the CONDITIONS + description + drugs make clear the trial is about ONE specific \
type, use only that specific type and DROP the umbrella. Only when the trial genuinely enrols a broad group \
with a subtype named do you keep both — as separate OR rows, never ANDed. Genuinely different eligible \
tumour types are separate OR rows. \
CAPTURE EXCLUDED tumour types: when the eligibility text carves OUT a specific tumour subtype / histology / \
anatomic location ("except ...", "excluding ...", "other than ...", "not ... tumours"), that exclusion is a \
REAL eligibility criterion — encode it as a same-cell NOT() carve-out and NEVER drop it (e.g. a DMG trial that \
excludes thalamic/cerebellar DMG → cancer_type = "DMG AND NOT(thalamic and cerebellar DMG)"; \
"grade III/IV glioma, not histone-H3-wildtype grade II astrocytoma" → keep the NOT() term). Losing a stated \
tumour-type exclusion is a serious error.
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

Cohort assignment — set each row's `cohort` (this is a PRIMARY task, not an afterthought):
The COHORTS list is FIXED and already identified for you (from the trial's own structure). Your job is to
ASSIGN each eligibility criterion to the cohort(s) it actually governs. Each `trial-wide` row is
AND-combined onto EVERY cohort's rows downstream to build that cohort's complete, self-contained
eligibility — so assign each criterion to EXACTLY ONE scope; NEVER state the same criterion in two scopes.
- a cohort id (e.g. "C1"): a criterion that DEFINES, is SPECIFIC to, or DIFFERS for that cohort (e.g. a
  per-cohort tumour/staging selection, a per-cohort prior-therapy/treatment-phase condition). If a criterion
  applies to several — but not all — cohorts, emit it once per applicable cohort (not trial-wide).
- "trial-wide": ONLY a criterion shared IDENTICALLY by ALL cohorts (a common disease definition, a trial-wide
  exclusion). State it ONCE — do NOT also repeat it inside cohort rows.
- Single-cohort trial: everything is "trial-wide".
CRITICAL — do NOT restate a shared criterion in both scopes, and do NOT put a cohort-defining criterion in
trial-wide. In particular, a single-valued axis like cancer_type / tumour-stage must appear in ONE scope only:
if it varies between cohorts, put each cohort's value in that cohort's rows (NOT trial-wide); if it is the same
for all, state it once trial-wide. Restating it in both scopes creates impossible combinations
("Stage A AND Stage B") when the scopes are AND-combined downstream.

DNF rules:
- One row = one satisfiable combination of requirements (a conjunction: all cells ANDed).
- If eligibility offers alternatives (OR), emit one row per alternative.
- Conditionals become co-occurrence: "if <cancer A> then <mutation X>; if <cancer B> then <mutation Y>" \
-> two rows: (cancer=A, gene=X) and (cancer=B, gene=Y).
- Use "" for any column not required by a row.
- Split into separate OR rows ONLY for GENUINELY distinct eligibility paths a patient chooses between. Do NOT \
emit near-duplicate rows that differ only by a TRIVIAL or SUBSUMING variation of the SAME criterion — e.g. two \
rows identical except one adds "AND refractory to standard therapy" to prior_therapy, or one prior_therapy \
that is a strict superset of another's terms. These are NOT real alternatives (the stricter row is subsumed by \
the looser one, so it adds nothing). Apply JUDGEMENT and read the text: decide whether that extra clause is \
actually REQUIRED for the cohort — if it applies to the MAJORITY of eligible patients keep only the version \
WITH it; if not, keep only the version WITHOUT it — but keep exactly ONE. Never emit both.

Negation (inclusion AND exclusion are BOTH in scope):
- Wrap an excluded criterion in NOT(...), e.g. prior_therapy = "NOT(prior EGFR TKI)".
- A single cell holds the FULL requirement for its criterion in that row and may hold several ANDed \
terms; wrap excluded ones in NOT(). Same-column carve-outs stay in ONE cell: \
"solid tumours except melanoma" -> cancer_type = "solid tumour AND NOT(melanoma)". Only genuine \
OR-alternatives split into rows.
- NEVER write a self-contradiction in one cell — no "X AND NOT(X)". If the SAME thing (e.g. an H3K27M \
mutation) is REQUIRED for one tumour/cohort but EXCLUDED for another, those belong on DIFFERENT DNF rows: \
split them (X on one row's cell, NOT(X) on the other), never combine them in a single cell.

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
    label: str            # human-readable "what this reviewer checks" (for the run log)
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
    ReviewerSpec("cancer_type", "cancer type", True, _REVIEW_PREAMBLE + """
DIMENSION = cancer_type. Check every row's cancer_type is faithful AND is genuinely the trial's tumour \
UNDER STUDY. Flag FALSE POSITIVES: a tumour that appears only inside a prior-therapy phrase, medical \
history, an exclusion of other malignancies, or an example is NOT the trial's cancer type (e.g. \
"progressed after therapy for melanoma" in a lung trial -> melanoma must NOT be a cancer_type). Also flag \
missing tumour types and mis-placed histology/stage. Flag any cell that ANDs two DIFFERENT cancer types \
(a patient has one tumour — different types are OR-alternatives on separate rows), and flag a broad umbrella \
("solid tumours", "any cancer") left in when the CONDITIONS/description show the trial is about ONE specific \
type (the umbrella should be dropped). Flag a MISSING tumour-type EXCLUSION: if the eligibility text carves out \
a specific tumour subtype / histology / anatomic location ("except ...", "excluding ...", "other than ..."), it \
MUST appear as a NOT() carve-out in cancer_type — a dropped tumour-type exclusion is a serious faithfulness error."""),
    ReviewerSpec("molecular", "molecular columns (gene / signature / biomarker)", True, _REVIEW_PREAMBLE + """
DIMENSION = gene_alteration + molecular_signature + molecular_biomarker. Check faithfulness AND that each \
value is in the RIGHT column: specific gene+alteration -> gene_alteration; composite/genomic signature -> \
molecular_signature; expression/IHC/protein -> molecular_biomarker. Watch the edge rules: HER2/ERBB2 \
(expression->biomarker, amplification->gene_alteration); MMR (dMMR/pMMR IHC->biomarker, MSI-H->signature). \
Flag any single cell containing a self-contradiction like "X AND NOT(X)" (same alteration required and \
excluded) — requirements for DIFFERENT cancer types/cohorts were conflated and must be split into separate \
DNF rows."""),
    ReviewerSpec("prior_therapy", "prior therapy", True, _REVIEW_PREAMBLE + """
DIMENSION = prior_therapy. Only REQUIRED prior therapies (positive) and EXCLUDED ones (wrapped in NOT())
are eligibility constraints. A merely PERMITTED/ALLOWED prior therapy (neither required nor disqualifying)
is NOT a constraint and should be OMITTED — do NOT flag its absence. Flag missing REQUIRED/EXCLUDED
conditions and wrong/missing negation. Flag OVER-ENUMERATION: two near-duplicate rows that differ ONLY by a
trivial/subsuming prior_therapy variation (one row's prior_therapy a strict superset of another's — e.g. an
extra "AND refractory to standard therapy") are not genuine alternatives; they must be collapsed by judgement
to the SINGLE version applicable to the majority of patients, not emitted as separate rows."""),
    ReviewerSpec("drug", "drug", False, _REVIEW_PREAMBLE + """
DIMENSION = drug. Each cohort's drug(s) are listed in the COHORTS section — NOT in the eligibility rows
(eligibility is cohort-agnostic; drugs are assigned per cohort separately downstream). Check each cohort's
listed drug(s) match the intervention(s) administered to that cohort per the source; flag wrong, missing,
or extraneous drugs. Do NOT ask for a drug column in the eligibility table; ignore normalization/formatting."""),
    ReviewerSpec("structural", "DNF structure & cohort scope", True, _REVIEW_PREAMBLE + """
DIMENSION = DNF structure & cohort scope. Check conjunctions/conditionals are correct rows, no OR-\
alternative is missing or wrongly merged, and each requirement's cohort scope is right (cohort-specific \
requirements assigned to their cohort; shared ones 'trial-wide'). Each trial-wide row is AND-combined onto \
EVERY cohort downstream, so flag a criterion DUPLICATED across scopes (stated both trial-wide AND in a \
cohort) — it must live in exactly ONE scope. In particular flag a single-valued axis (cancer_type / \
tumour-stage) populated in BOTH trial-wide and cohort rows: that produces impossible AND-combinations \
("Stage A AND Stage B") when scopes combine — a per-cohort tumour/stage belongs in that cohort's rows only, \
a shared one trial-wide only. Also flag any cell holding a self-contradiction "X AND NOT(X)" — that conflates \
two cohorts and must be split so X is on one row and NOT(X) on another. Flag REDUNDANT near-duplicate OR rows \
that differ only by a subsuming variation of one criterion (the stricter row just adds an extra AND-clause to \
an otherwise identical row): they are not distinct alternatives — keep the single majority-applicable version."""),
)


def build_reviewer_agents(client: LlmClient, *, model: str | None = None) -> list[tuple[ReviewerSpec, Agent[JudgeVerdict]]]:
    return [
        (spec, Agent(name=f"reviewer_{spec.key}", instructions=spec.instructions,
                     output_schema=JudgeVerdict, client=client, model=model))
        for spec in REVIEWERS
    ]
