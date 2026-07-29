"""Specialist agents for the extraction task (see docs/v2_agentic_pipeline_spec.md §7).

Two-sub-stage extraction (raw text vs. its interpretation are different jobs → different agents):

Stage I-a — RAW (source-dependent):
- raw_extractor:   relevant trial text + COHORTS -> VERBATIM criterion spans (criterion + scope + one source).
- raw_reviewer:    completeness gate — every relevant span captured, nothing extraneous, verbatim, right bucket.

Stage I-b — INTERPRETATION (grounded in the raw spans):
- interpreter:     raw spans (+ source + COHORTS) -> scoped DNF rows (5 eligibility columns; NO source tags).
- review panel:    cancer_type / molecular / prior_therapy / structural (gating) + drug (advisory) + enumeration.

ANZCTR regime axis:
- drug:            INTERVENTIONS/COMPARATOR text -> raw intervention + comparator drug names (single cohort).

Column taxonomy mirrors pydantic_curator/criterion_schema.py.
"""
from __future__ import annotations

from dataclasses import dataclass

from aus_trial_universe.core.agent import Agent
from aus_trial_universe.core.client import LlmClient
from aus_trial_universe.tasks.eligibility.extraction.schema import (
    EligibilityExtraction,
    JudgeVerdict,
    RawExtraction,
)

# --------------------------------------------------------------------------- #
# Shared column taxonomy (both sub-stages reference the same 5 criteria)
# --------------------------------------------------------------------------- #
_COLUMN_TAXONOMY = """\
- cancer_type: the required cancer/tumour type under study (site + histology + stage/extent), in the trial's \
own words (e.g. "metastatic NSCLC"). NOT other/prior malignancies, and NOT a tumour named only in a \
prior-therapy or medical-history phrase. The CONDITIONS section is AUTHORITATIVE for the tumour type(s). \
"except / excluding / other than ..." tumour carve-outs ARE eligibility criteria and must be kept.
- gene_alteration: a required SPECIFIC gene + alteration, DNA/mRNA-level (e.g. "EGFR exon 19 deletion", \
"KRAS G12C", "ALK fusion", "ERBB2 amplification").
- molecular_signature: a required COMPOSITE/genomic signature not tied to one gene's variant \
(e.g. "MSI-H", "TMB-high", "HRD", "genomic instability", "1p/19q codeletion").
- molecular_biomarker: a required protein-EXPRESSION / receptor / IHC status biomarker that defines a molecular \
subgroup (e.g. "PD-L1 >=1% (IHC)", "HER2 IHC 3+", "ER positive", "dMMR (IHC)"). NOT a quantitative disease-BURDEN / \
measurability / activity level (serum M-protein level, tumour size, blast %, LDH, blood counts) — those are \
disease-measurability criteria, OUT OF SCOPE (like labs).
- prior_therapy: a prior/current ANTI-CANCER treatment-history condition that constrains eligibility — REQUIRED \
(e.g. ">=1 prior platinum line", "treatment-naive", "prior BRAF inhibitor") or EXCLUDED (e.g. "NOT(prior \
anti-PD-1)"). "Anti-cancer" = systemic anti-cancer therapy (chemo / targeted / immuno / hormonal / \
investigational anti-cancer agent), cancer radiotherapy, cancer surgery, or transplant — INCLUDING their washout \
windows ("no systemic anti-cancer therapy within 28 days"). A merely PERMITTED/ALLOWED prior therapy does NOT \
constrain eligibility — omit it.

Column edge rules:
- HER2/ERBB2: expression or IHC -> molecular_biomarker; gene amplification -> gene_alteration.
- MMR: dMMR/pMMR by IHC -> molecular_biomarker; MSI-H (genomic) -> molecular_signature.
- Histology (adenocarcinoma, squamous, etc.) -> part of cancer_type.
- Capture a molecular RESULT, never the procedural requirement to TEST for it ("BRAF V600E mutation", NOT \
"BRAF testing prior to entry").
- Ignore everything else — it is OUT OF SCOPE, do NOT capture it: age, labs, organ function, performance status, \
disease-burden / measurability / activity levels (serum M-protein, tumour size, blast %, LDH, blood counts), \
comorbidities, other/prior non-study malignancy, reproductive / contraception / consent status, drug/intervention \
names, AND general safety / supportive-care rules that are NOT anti-cancer therapy — corticosteroid / \
immunosuppressant / vaccine / antibiotic / anticoagulant washouts, herbal or vitamin supplements, \
hormone-replacement therapy, and generic "any other medication / treatment / supplement" clauses — plus \
biospecimen-submission requirements and non-cancer surgery / surgical-recovery status. Only the five criteria above."""

_COHORT_SCOPE_RULES = """\
The COHORTS list is the trial's FIXED, KNOWN set of DRUG REGIMES (each = an arm/regime with its own drug(s)). \
You do NOT invent cohorts — you ASSIGN each criterion to the scope it governs:
- "trial-wide" (the DEFAULT): a criterion shared by ALL regimes (the common disease definition, a shared \
exclusion, a shared prior-therapy rule). State it ONCE.
- a cohort id (e.g. "C1"): ONLY a criterion the text CLEARLY ties to that specific regime (a per-regime \
tumour/staging selection or prior-therapy condition). If it applies to several — but not all — regimes, \
assign it once per applicable regime.
- DROP criteria for groups NOT in the COHORTS list (closed / withdrawn / not-yet-open cohorts) — never invent \
an id, never fold them into trial-wide.
- Single-regime trial: everything is "trial-wide".
Assign each criterion to EXACTLY ONE scope. A single-valued axis (cancer_type / tumour-stage) must appear in \
ONE scope only — restating it in two scopes creates impossible AND-combinations downstream."""


# --------------------------------------------------------------------------- #
# Stage I-a — RAW extractor
# --------------------------------------------------------------------------- #
RAW_EXTRACTOR_INSTRUCTIONS = f"""\
You are given the relevant sections of a clinical trial (each headed "## <LABEL>") and a COHORTS list. Your job \
is to COPY OUT, VERBATIM, every source span that states an eligibility criterion of these FIVE kinds — nothing \
more. This is faithful copying, NOT interpretation: do not paraphrase, normalize, summarize, translate, or add \
any words; you may only quote contiguous source text.

The five criteria:
{_COLUMN_TAXONOMY}

For EACH relevant span, emit one fragment with:
- criterion: which of the five it informs (exactly one).
- text: the source span copied VERBATIM. Copy the COMPLETE clause so its logic survives — keep connectives \
("or", "and", "and/or", commas) and any "except / excluding / other than" carve-out. Prefer one coherent clause \
per fragment; if one sentence states several DIFFERENT criteria, split it so each fragment is single-criterion, \
but never drop words WITHIN the span you keep. Do not truncate mid-clause.
- source: the ONE section LABEL (the "## <LABEL>") you copied it from.
- scope: which regime it governs (see below).

{_COHORT_SCOPE_RULES}

Completeness + cleanliness (both matter — the reviewer checks both):
- Capture EVERY relevant span for the five criteria, including exclusions ("except ...", "not ... tumours", \
"NOT prior anti-PD-1"). A dropped tumour-type exclusion or a dropped required prior therapy is a serious miss.
- Do NOT copy OUT-OF-SCOPE text (see the taxonomy's ignore list): age, labs, organ function, performance status, \
comorbidities, consent / reproductive, other/prior malignancies (unless the tumour under study), drug/dosing prose, \
and general safety / supportive-care rules that are NOT anti-cancer therapy — corticosteroid / vaccine / antibiotic / \
supplement / hormone-replacement washouts, "any other medication" clauses, biospecimen-submission or "must be tested \
for X" procedural requirements, and non-cancer surgery / recovery status. If a criterion type is not stated, emit no \
fragment for it.
- If the same criterion is stated in several sections, copy it once from the MOST authoritative/complete section \
(CONDITIONS is authoritative for the tumour type).
"""

RAW_REVIEWER_INSTRUCTIONS = """\
You audit a RAW extraction: verbatim source spans copied out for five eligibility criteria (cancer_type, \
gene_alteration, molecular_signature, molecular_biomarker, prior_therapy), each tagged with its source section \
and scope. You are given the full trial text and the proposed fragments.

BE LENIENT — gate (faithful=false) ONLY on a MATERIAL problem that changes WHICH PATIENTS the criteria match: a \
missing IN-SCOPE criterion, a truncation that drops a connective/carve-out, a fabricated/paraphrased span, or a \
fragment mis-assigned to the WRONG cohort. A faithful span with slightly different whitespace, a defensible \
either-way bucket/scope call, or an OUT-OF-SCOPE criterion is NOT a reason to fail. Sending a good raw extraction \
back for a nitpick wastes a whole cycle — when in doubt, pass.

Set faithful=true unless one of these MATERIAL problems holds:
1. NOT VERBATIM — a fragment's text is paraphrased/translated/invented, or truncated so it drops a \
   connective or an "except/excluding" carve-out (changing meaning). Minor whitespace/section-choice differences \
   are fine.
2. MISSING an IN-SCOPE span — a source span stating one of the five criteria is not captured, ESPECIALLY a \
   tumour-type exclusion ("except / excluding / other than ...") or a required/excluded ANTI-CANCER prior therapy.
   OUT OF SCOPE — do NOT flag these as missing: age / labs / organ function / performance status / comorbidity; \
   consent / contraception / reproductive; a general safety or supportive-care washout that is NOT anti-cancer \
   therapy (corticosteroids, vaccines, antibiotics, anticoagulants, supplements, hormone replacement, "any other \
   medication"); a biospecimen-submission or "must be tested for X" procedural requirement; non-cancer surgery / \
   surgical-recovery status.
3. EXTRANEOUS — a captured fragment is one of the OUT-OF-SCOPE items above, or plainly irrelevant.
4. WRONG COHORT — a fragment assigned to a cohort NOT in the COHORTS list.

Anti-oscillation: a borderline span that could sit in either of two criteria — accept EITHER placement; never \
flag the same span as both "missing" and "mis-bucketed". Do NOT nitpick which authoritative section was chosen.

`suggested_fix` — normally leave EMPTY (report problems only). ONLY when the input is marked "[ESCALATION-MODE]" \
(the writer got stuck repeating the same mistake) do you ALSO fill it with the concrete fragment(s) to add / \
remove / correct (verbatim text + criterion + scope) — last-resort advice for the writer (it regenerates and is \
re-checked, never applied directly).
"""


def build_raw_extractor_agent(client: LlmClient, *, model: str | None = None) -> Agent[RawExtraction]:
    return Agent(name="raw_extractor", instructions=RAW_EXTRACTOR_INSTRUCTIONS,
                 output_schema=RawExtraction, client=client, model=model)


def build_raw_reviewer_agent(client: LlmClient, *, model: str | None = None) -> Agent[JudgeVerdict]:
    return Agent(name="raw_reviewer", instructions=RAW_REVIEWER_INSTRUCTIONS,
                 output_schema=JudgeVerdict, client=client, model=model)


# --------------------------------------------------------------------------- #
# Stage I-b — interpreter (raw spans -> DNF logic)
# --------------------------------------------------------------------------- #
INTERPRETER_INSTRUCTIONS = f"""\
You are given (1) the VERBATIM raw source spans already extracted for a trial's eligibility, grouped by criterion \
and scope, and (2) the full trial text as context, and (3) a COHORTS list. INTERPRET the raw spans into a \
normalized DNF table over these FIVE columns ONLY. Work FROM the raw spans (they are the authoritative set of \
relevant text); use the full text only to resolve logic (which alternatives are OR vs AND, which scope applies).

{_COLUMN_TAXONOMY}

cancer_type specifics:
- NEVER AND two DIFFERENT cancer types in one cell — a patient has ONE tumour type. If the raw shows a broad \
umbrella ("advanced solid tumours") but CONDITIONS + description + drugs make clear the trial is ONE specific \
type, use only that type and DROP the umbrella. Genuinely different eligible types are separate OR rows.
- CAPTURE EXCLUDED tumour types as a same-cell NOT() carve-out and NEVER drop them (e.g. "DMG AND \
NOT(thalamic and cerebellar DMG)"). Losing a stated tumour-type exclusion is a serious error.

{_COHORT_SCOPE_RULES}

DNF rules:
- One row = one satisfiable combination of requirements (a conjunction: all cells ANDed).
- If eligibility offers alternatives (OR), emit one row per alternative. A raw span phrased with "or" / \
"and/or" / commas ("A, B, or C") is mutually-substitutable ALTERNATIVES — split them into OR rows; do NOT AND \
them together in one cell.
- Conditionals become co-occurrence: "if <cancer A> then <mutation X>; if <cancer B> then <mutation Y>" \
-> two rows: (cancer=A, gene=X) and (cancer=B, gene=Y).
- Use "" for any column not required by a row.
- Do NOT emit near-duplicate OR rows that differ only by a TRIVIAL or SUBSUMING variation of the SAME criterion \
(e.g. one adds "AND refractory to standard therapy"). Apply JUDGEMENT: keep exactly ONE — the version applying \
to the majority of eligible patients.

Negation (inclusion AND exclusion are BOTH in scope):
- USE THE SOURCE TAG: a raw span tagged "[EXCLUSION CRITERIA]" (or ANZCTR "[EXCLUSIVE CRITERIA]") states an \
EXCLUSION — represent its criterion as NOT(...). A span from an inclusion/eligibility section is a REQUIREMENT \
(no NOT, unless its own wording is negative — "no prior", "without", "except"). The one exception: an EXCLUSION \
span that positively DEFINES the tumour under study is still the cancer_type (not everything in EXCLUSION is a NOT).
- Wrap an excluded criterion in NOT(...), e.g. prior_therapy = "NOT(prior EGFR TKI)".
- A single cell holds the FULL requirement for its criterion in that row and may hold several ANDed terms; \
same-column carve-outs stay in ONE cell: "solid tumours except melanoma" -> "solid tumour AND NOT(melanoma)".
- NEVER write a self-contradiction "X AND NOT(X)" in one cell. If the SAME thing is REQUIRED for one \
tumour/cohort but EXCLUDED for another, split them onto DIFFERENT rows.

Output ONLY the five columns + the cohort scope. Do NOT add source tags — provenance is tracked separately.
"""


def build_interpreter_agent(client: LlmClient, *, model: str | None = None) -> Agent[EligibilityExtraction]:
    return Agent(name="eligibility_interpreter", instructions=INTERPRETER_INSTRUCTIONS,
                 output_schema=EligibilityExtraction, client=client, model=model)


# NB: the ANZCTR drug extractor + reviewer (the regime axis) moved to `tasks/shared/agents.py` — ANZCTR arm
# identification is now a path-neutral shared module (`tasks/shared/cohorts.anzctr_regimes`) used by BOTH paths.


# --------------------------------------------------------------------------- #
# Stage I-b — interpretation reviewer panel
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ReviewerSpec:
    key: str
    label: str            # human-readable "what this reviewer checks" (for the run log)
    gating: bool          # does a failure gate/loop the refine? (drug is advisory)
    instructions: str


_REVIEW_PREAMBLE = """\
You audit ONE dimension of an INTERPRETED eligibility DNF table against the VERBATIM raw source spans it was \
built from (and the trial text as context). Cells may hold inline logic (ANDed terms; exclusions wrapped in \
NOT()). Judge only your dimension below: set faithful=false with concrete, actionable problems if the \
interpretation is unfaithful to the raw spans — anything missing, invented, mis-paired, mis-columned, or \
mis-scoped for YOUR dimension; otherwise faithful=true.

BE LENIENT — gate ONLY on MATERIAL errors, i.e. ones that change WHICH PATIENTS the criteria match (a \
missing/invented/mis-columned/mis-scoped criterion, a dropped exclusion, a fabricated conjunction). Do NOT set \
faithful=false for a faithful paraphrase of the raw span (wording, phrasing, formatting, ordering, granularity) \
or anything you would merely "prefer" differently. Sending an already-correct trial back for a nitpick wastes a \
whole refine cycle.

SCOPE — the five criteria are ONCOLOGY-specific: the STUDY tumour type, its molecular features, and ANTI-CANCER \
prior therapy. These are OUT OF SCOPE and are correctly OMITTED — do NOT flag one as missing, do NOT fail a row \
for omitting one: age / labs / organ function / performance status / comorbidity; consent / contraception / \
reproductive; adverse-event-resolution criteria; general safety or supportive-care / concomitant-medication rules \
that are NOT anti-cancer therapy (corticosteroids, vaccines, antibiotics, supplements, hormone replacement); AND a \
generic "no history of other / second / prior malignancy" exclusion (that is NOT the study tumour type).

`suggested_fix` — normally leave it EMPTY and report problems only; pointing out the error is enough (the writer \
fixes it). ONLY when the input is marked "[ESCALATION-MODE]" (the writer got stuck repeating the same mistake) do \
you ALSO fill `suggested_fix` with the concrete corrected cell(s)/row(s) you would expect — the exact text, as \
last-resort advice for the writer (it still regenerates and is re-checked, never applied directly).
"""

REVIEWERS: tuple[ReviewerSpec, ...] = (
    ReviewerSpec("cancer_type", "cancer type", True, _REVIEW_PREAMBLE + """
DIMENSION = cancer_type. Check every row's cancer_type is faithful to the raw spans AND is genuinely the trial's \
tumour UNDER STUDY. Flag FALSE POSITIVES: a tumour that appears only inside a prior-therapy phrase, medical \
history, an exclusion of other malignancies, or an example is NOT the trial's cancer type. Also flag missing \
tumour types and mis-placed histology/stage. Flag any cell that ANDs two DIFFERENT cancer types (a patient has \
one tumour — different types are OR-alternatives on separate rows), and flag a broad umbrella ("solid tumours", \
"any cancer") left in when the CONDITIONS/description show the trial is about ONE specific type. Flag a MISSING \
tumour-type EXCLUSION only when a raw span carves out a specific subtype / histology / anatomic location OF THE \
STUDY TUMOUR ("DMG except thalamic DMG", "melanoma other than uveal") — that MUST appear as a NOT() carve-out. A \
generic "no history of other / second / prior malignancy" exclusion is NOT the study tumour type: it is OUT OF \
SCOPE — do NOT require it and do NOT fail whether it is present or omitted."""),
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
COHORTS list — a closed / withdrawn / not-yet-open cohort — must be DROPPED: flag it if it was invented as a \
cohort id, mis-assigned to a listed regime, or folded into trial-wide; (c) the default scope is trial-wide. \
Each trial-wide row is AND-combined onto EVERY regime downstream, so flag a criterion DUPLICATED across scopes \
(stated both trial-wide AND in a regime) — it must live in exactly ONE scope. In particular flag a single-valued \
axis (cancer_type / tumour-stage) populated in BOTH trial-wide and cohort rows: that produces impossible \
AND-combinations ("Stage A AND Stage B") when scopes combine. Also flag any cell holding a self-contradiction \
"X AND NOT(X)" — that conflates two cohorts and must be split. Flag REDUNDANT near-duplicate OR rows that differ \
only by a subsuming variation of one criterion — keep the single majority-applicable version."""),
)


def build_reviewer_agents(client: LlmClient, *, model: str | None = None) -> list[tuple[ReviewerSpec, Agent[JudgeVerdict]]]:
    return [
        (spec, Agent(name=f"reviewer_{spec.key}", instructions=spec.instructions,
                     output_schema=JudgeVerdict, client=client, model=model))
        for spec in REVIEWERS
    ]


# --------------------------------------------------------------------------- #
# Enumeration-plausibility reviewer — the ONE lens that sees the ASSEMBLED DNF.
# The per-dimension panel above audits the scoped rows cell-by-cell; none sees the final cross-multiplied,
# de-duplicated row SET or asks "is this row count plausible vs. the raw's alternative structure?". That blind
# spot is how OR-alternatives of ONE criterion get fabricated into AND-combinations and the table blows up.
# --------------------------------------------------------------------------- #
ENUMERATION_REVIEWER_INSTRUCTIONS = """\
You audit the ASSEMBLED eligibility DNF table (one row = one satisfiable AND-conjunction; the rows are
OR-alternatives) against the trial text, with ONE lens: ENUMERATION PLAUSIBILITY. Checking is far cheaper than
generating — your job is to catch OR-alternatives that were fabricated into AND-combinations, and any implausible
row-count blow-up.

For EACH criterion column (cancer_type, gene_alteration, molecular_signature, molecular_biomarker, prior_therapy):
1. Read the source and count the distinct ALTERNATIVES it actually states for that criterion. A list phrased with
   "or" / "and/or" / commas ("A, B, or C") = mutually-substitutable alternatives — a patient needs just ONE.
2. A single cell must NEVER AND-together the mutually-substitutable alternatives of ONE criterion. If a cell reads
   like "MYCN amplification AND MYCL amplification" but the source says "MYCN, MYC OR MYCL amplification", that is a
   FABRICATED conjunction — flag it and say the alternatives belong on SEPARATE OR-rows, not ANDed in one cell.
3. If the table enumerates MORE distinct combinations for a criterion than the source's alternatives support
   (tell-tale signs: AND-pairs of same-criterion alternatives, or both orderings of a pair), an OR was fabricated
   into an AND — flag it, naming the criterion and the offending cells.
4. If the source's true structure is a handful of OR-paths but the table has many times more rows, flag the
   implausible total and name the criterion driving the blow-up.

Set faithful=false with concrete, actionable problems (name the offending column/cells and the correct alternative
structure) so the interpreter can split them onto separate rows. Otherwise faithful=true. Do NOT flag genuine
independent AND-requirements ACROSS DIFFERENT criteria (e.g. a cancer_type AND a required biomarker) — those are
correct conjunctions.

`suggested_fix` — normally leave EMPTY (report problems only). ONLY when the input is marked "[ESCALATION-MODE]"
(the interpreter got stuck) do you ALSO fill it with the corrected row structure you would expect (e.g. "split
into rows: gene=MYCN amp; gene=MYCL amp") — last-resort advice, not applied directly.
"""


def build_enumeration_reviewer(client: LlmClient, *, model: str | None = None) -> Agent[JudgeVerdict]:
    return Agent(name="reviewer_enumeration", instructions=ENUMERATION_REVIEWER_INSTRUCTIONS,
                 output_schema=JudgeVerdict, client=client, model=model)
