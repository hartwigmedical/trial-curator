"""Tests for the eligibility-extraction workflow (fake client, no LLM).

Covers: cohort-aware extraction, cross-product distribution (trial-wide x cohort-specific),
multi-source provenance, inline NOT(), the reviewer panel (gating vs advisory), the refine
loop, and the ANZCTR path (LLM cohort-detection + drug extraction).
"""
from __future__ import annotations

from aus_trial_universe.agentic.core.client import LlmResult
from aus_trial_universe.agentic.tasks.extraction.agents import REVIEWERS
from aus_trial_universe.agentic.tasks.extraction.schema import (
    CohortDetection,
    DetectedCohort,
    DrugExtraction,
    EligibilityExtraction,
    ExtractedRow,
    JudgeVerdict,
)
from aus_trial_universe.agentic.tasks.extraction.workflow import (
    Cohort,
    extract_trial,
)


def _reviewer_key(instructions: str) -> str | None:
    for spec in REVIEWERS:
        if spec.instructions == instructions:
            return spec.key
    return None


class _ScriptedClient:
    """Fake LlmClient.parse dispatching canned outputs by schema.

    extractions: one per extractor call (attempt). verdicts: {reviewer_key -> [JudgeVerdict,...]},
    consumed in order per reviewer (default faithful=True). cohort_detection / drugs: ANZCTR path.
    """

    def __init__(self, extractions, verdicts=None, cohort_detection=None, drugs=None):
        self._extractions = extractions
        self._verdicts = {k: list(v) for k, v in (verdicts or {}).items()}
        self._vcount = {k: 0 for k in self._verdicts}
        self._cohort_detection = cohort_detection
        self._drugs = drugs or []
        self.extractor_calls = 0

    def parse(self, output_schema, *, instructions, user_input, model=None,
              temperature=None, seed=None, max_completion_tokens=None):
        if output_schema is EligibilityExtraction:
            idx = min(self.extractor_calls, len(self._extractions) - 1)
            self.extractor_calls += 1
            return LlmResult(self._extractions[idx], "fake", "{}", False, 1)
        if output_schema is JudgeVerdict:
            key = _reviewer_key(instructions)
            seq = self._verdicts.get(key)
            if not seq:
                return LlmResult(JudgeVerdict(faithful=True), "fake", "{}", False, 1)
            i = min(self._vcount[key], len(seq) - 1)
            self._vcount[key] += 1
            return LlmResult(seq[i], "fake", "{}", False, 1)
        if output_schema is CohortDetection:
            return LlmResult(self._cohort_detection or CohortDetection(cohorts=[]), "fake", "{}", False, 1)
        if output_schema is DrugExtraction:
            return LlmResult(DrugExtraction(drugs=self._drugs), "fake", "{}", False, 1)
        raise AssertionError(f"unexpected schema {output_schema}")


def _extraction(*rows):
    return EligibilityExtraction(rows=list(rows))


# --- CTGov path ------------------------------------------------------------- #
def test_trialwide_replicates_across_cohorts_with_per_cohort_drug():
    client = _ScriptedClient(extractions=[_extraction(
        ExtractedRow(cohort="trial-wide", cancer_type="NSCLC", cancer_type_sources=["TITLE"]),
    )])
    cohorts = [
        Cohort(label="Arm A", drug="drugA", drug_source="INTERVENTIONS MODULE", arm_type="EXPERIMENTAL"),
        Cohort(label="Arm B", drug="drugB", drug_source="INTERVENTIONS MODULE", arm_type="ACTIVE_COMPARATOR"),
    ]
    result = extract_trial(client, trial_id="NCT1", source_text="...", cohorts=cohorts)
    assert result.faithful and result.attempts == 1
    assert [(r.cohort, r.arm_type, r.cancer_type, r.drug) for r in result.rows] == [
        ("Arm A", "EXPERIMENTAL", "NSCLC [TITLE]", "drugA [INTERVENTIONS MODULE]"),
        ("Arm B", "ACTIVE_COMPARATOR", "NSCLC [TITLE]", "drugB [INTERVENTIONS MODULE]"),
    ]


def test_cohort_specific_criterion_merges_only_into_its_cohort():
    client = _ScriptedClient(extractions=[_extraction(
        ExtractedRow(cohort="trial-wide", cancer_type="solid tumour", cancer_type_sources=["ELIGIBILITY CRITERIA"]),
        ExtractedRow(cohort="C1", gene_alteration="EGFR mutation", gene_alteration_sources=["ELIGIBILITY CRITERIA"]),
    )])
    cohorts = [Cohort(label="Arm A", drug="osi"), Cohort(label="Arm B", drug="chemo")]
    result = extract_trial(client, trial_id="NCT2", source_text="...", cohorts=cohorts)
    by_cohort = {r.cohort: r for r in result.rows}
    assert len(result.rows) == 2
    # C1 = Arm A gets trial-wide AND its specific gene; Arm B gets only trial-wide
    assert by_cohort["Arm A"].cancer_type == "solid tumour [ELIGIBILITY CRITERIA]"
    assert by_cohort["Arm A"].gene_alteration == "EGFR mutation [ELIGIBILITY CRITERIA]"
    assert by_cohort["Arm B"].cancer_type == "solid tumour [ELIGIBILITY CRITERIA]"
    assert by_cohort["Arm B"].gene_alteration == ""


def test_not_negation_and_multisource_passthrough():
    client = _ScriptedClient(extractions=[_extraction(
        ExtractedRow(cohort="trial-wide",
                     cancer_type="solid tumour AND NOT(melanoma)", cancer_type_sources=["TITLE", "ELIGIBILITY CRITERIA"],
                     prior_therapy="NOT(prior EGFR TKI)", prior_therapy_sources=["ELIGIBILITY CRITERIA"]),
    )])
    result = extract_trial(client, trial_id="NCT3", source_text="...", cohorts=[Cohort("all")])
    row = result.rows[0]
    assert row.cancer_type == "solid tumour AND NOT(melanoma) [TITLE; ELIGIBILITY CRITERIA]"
    assert row.prior_therapy == "NOT(prior EGFR TKI) [ELIGIBILITY CRITERIA]"


def test_gating_reviewer_triggers_refine_then_passes():
    client = _ScriptedClient(
        extractions=[
            _extraction(ExtractedRow(cohort="trial-wide", cancer_type="lung cancer", cancer_type_sources=["TITLE"])),
            _extraction(ExtractedRow(cohort="trial-wide", cancer_type="NSCLC", cancer_type_sources=["ELIGIBILITY CRITERIA"])),
        ],
        verdicts={"cancer_type": [JudgeVerdict(faithful=False, problems=["too vague"]), JudgeVerdict(faithful=True)]},
    )
    result = extract_trial(client, trial_id="NCT4", source_text="...", cohorts=[Cohort("all")], max_attempts=3)
    assert result.faithful and result.attempts == 2 and client.extractor_calls == 2
    assert result.rows[0].cancer_type == "NSCLC [ELIGIBILITY CRITERIA]"


def test_drug_reviewer_is_advisory_not_gating():
    client = _ScriptedClient(
        extractions=[_extraction(ExtractedRow(cohort="trial-wide", cancer_type="NSCLC", cancer_type_sources=["TITLE"]))],
        verdicts={"drug": [JudgeVerdict(faithful=False, problems=["drug mismatch"])]},
    )
    result = extract_trial(client, trial_id="NCT5", source_text="...",
                                 cohorts=[Cohort("all", drug="x")], max_attempts=3)
    assert result.faithful and result.attempts == 1  # advisory does not loop
    assert any("[drug] drug mismatch" in p for p in result.problems)


def test_empty_extraction_exhausts_attempts_before_panel():
    client = _ScriptedClient(extractions=[_extraction()])
    result = extract_trial(client, trial_id="NCT6", source_text="...", cohorts=[Cohort("all")], max_attempts=2)
    assert not result.faithful and result.attempts == 2
    assert any("no eligibility rows" in p for p in result.problems)


# --- ANZCTR path ------------------------------------------------------------ #
def test_anzctr_single_cohort_llm_drug():
    client = _ScriptedClient(
        extractions=[_extraction(ExtractedRow(cohort="trial-wide", cancer_type="colorectal cancer", cancer_type_sources=["INCLUSION CRITERIA"]))],
        cohort_detection=CohortDetection(cohorts=[]),
        drugs=["capecitabine", "bevacizumab"],
    )
    result = extract_trial(client, trial_id="ACTRN1", source_text="...", cohorts=None)
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.cohort == "(all)"  # synthetic single cohort rendered in brackets
    assert row.cancer_type == "colorectal cancer [INCLUSION CRITERIA]"
    assert row.drug == "capecitabine; bevacizumab [INTERVENTIONS]"


def test_anzctr_detected_cohorts_mirror_ctgov_structure():
    client = _ScriptedClient(
        extractions=[_extraction(
            ExtractedRow(cohort="trial-wide", cancer_type="NSCLC", cancer_type_sources=["HEALTH CONDITION"]),
            ExtractedRow(cohort="C1", gene_alteration="EGFR mutation", gene_alteration_sources=["INCLUSION CRITERIA"]),
            ExtractedRow(cohort="C2", gene_alteration="ALK fusion", gene_alteration_sources=["INCLUSION CRITERIA"]),
        )],
        cohort_detection=CohortDetection(cohorts=[
            DetectedCohort(label="Cohort 1", description="EGFR+"),
            DetectedCohort(label="Cohort 2", description="ALK+"),
        ]),
        drugs=["osimertinib"],
    )
    result = extract_trial(client, trial_id="ACTRN2", source_text="...", cohorts=None)
    by_cohort = {r.cohort: r for r in result.rows}
    assert set(by_cohort) == {"Cohort 1", "Cohort 2"}
    assert by_cohort["Cohort 1"].gene_alteration == "EGFR mutation [INCLUSION CRITERIA]"
    assert by_cohort["Cohort 2"].gene_alteration == "ALK fusion [INCLUSION CRITERIA]"
    assert all(r.cancer_type == "NSCLC [HEALTH CONDITION]" for r in result.rows)  # trial-wide distributed
    assert all(r.drug == "osimertinib [INTERVENTIONS]" for r in result.rows)
