"""Specialist agents for the extraction task (see docs/v2_agentic_pipeline_spec.md §7).

Extraction agents (source-dependent):
- extractor:        relevant trial text + COHORTS (regime) list -> scoped DNF rows (5 eligibility columns).
- drug (ANZCTR):    INTERVENTIONS/COMPARATOR text -> raw intervention + comparator drug names (the regime axis;
                    ANZCTR has a single eligibility cohort, so there is no cohort-detection agent — spec §6.1).

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
The COHORTS list is the trial's FIXED, KNOWN set of DRUG REGIMES (each cohort = an arm/regime with its own
drug(s), already identified from the trial's structure). You do NOT identify or invent cohorts — you ASSIGN
each eligibility criterion to the regime(s) it actually governs, using each regime's arm_type / drug /
description to decide. Each `trial-wide` row is AND-combined onto EVERY regime's rows downstream to build that
regime's complete, self-contained eligibility — so assign each criterion to EXACTLY ONE scope; NEVER state the
same criterion in two scopes.
- "trial-wide" (the DEFAULT — when in doubt, use this): a criterion shared by ALL regimes (the common disease
  definition, a trial-wide exclusion, a shared prior-therapy rule). State it ONCE — do NOT also repeat it
  inside a regime's rows.
- a cohort id (e.g. "C1"): ONLY a criterion the text CLEARLY ties to that specific regime — one that DEFINES,
  is SPECIFIC to, or DIFFERS for it (a per-regime tumour/staging selection, a per-regime prior-therapy /
  treatment-phase condition). If a criterion applies to several — but not all — regimes, emit it once per
  applicable regime.
- DROP criteria for groups NOT in the COHORTS list: the eligibility text often describes cohorts/arms that are
  NOT among the listed regimes — closed, withdrawn, or not-yet-open groups (e.g. the text details "Cohort
  1A/1B/2A/2B" but only "Cohort 4/5/6" are listed). Those regimes are not in this trial's output: DISCARD their
  criteria entirely. Never invent a cohort id for them, and never fold their regime-specific criteria into
  trial-wide (that would wrongly impose a closed cohort's selection on every real regime).
- Single-regime trial: everything is "trial-wide".
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


def build_extractor_agent(client: LlmClient, *, model: str | None = None) -> Agent[EligibilityExtraction]:
    return Agent(name="eligibility_extractor", instructions=EXTRACTOR_INSTRUCTIONS,
                 output_schema=EligibilityExtraction, client=client, model=model)


def build_drug_agent(client: LlmClient, *, model: str | None = None) -> Agent[DrugExtraction]:
    return Agent(name="drug_extractor", instructions=DRUG_EXTRACTOR_INSTRUCTIONS,
                 output_schema=DrugExtraction, client=client, model=model)


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


def build_drug_reviewer_agent(client: LlmClient, *, model: str | None = None) -> Agent[JudgeVerdict]:
    return Agent(name="drug_extractor_reviewer", instructions=DRUG_EXTRACTOR_REVIEWER_INSTRUCTIONS,
                 output_schema=JudgeVerdict, client=client, model=model)


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
    ReviewerSpec("structural", "DNF structure & regime-scope assignment", True, _REVIEW_PREAMBLE + """
DIMENSION = DNF structure & regime-scope assignment. Check conjunctions/conditionals are correct rows, no OR-\
alternative is missing or wrongly merged, and each requirement's scope is right. The COHORTS list is the \
trial's FIXED set of DRUG REGIMES — audit the ASSIGNMENT of eligibility to it (this is a primary check): \
(a) a regime-specific criterion must sit on the regime the text actually ties it to (use each regime's \
arm_type / drug / description to judge); (b) a criterion the text attaches to a group that is NOT in the \
COHORTS list — a closed / withdrawn / not-yet-open cohort (e.g. "Cohort 1A/1B" when only Cohorts 4/5/6 are \
listed) — must be DROPPED: flag it if it was invented as a cohort id, mis-assigned to a listed regime, or \
folded into trial-wide; (c) the default scope is trial-wide. \
Each trial-wide row is AND-combined onto EVERY regime downstream, so flag a criterion DUPLICATED across scopes \
(stated both trial-wide AND in a regime) — it must live in exactly ONE scope. In particular flag a single-valued axis (cancer_type / \
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
