"""Tests for the slice-1 eligibility-extraction workflow (fake client, no LLM).

Exercise the orchestration wiring: extractor -> fan-out OncoTree -> consolidate ->
check (rules + judge) -> bounded refine.
"""
from __future__ import annotations

from aus_trial_universe.agentic.core.client import LlmResult
from aus_trial_universe.agentic.tasks.eligibility_extraction.schema import (
    EligibilityExtraction,
    ExtractedRow,
    JudgeVerdict,
    OncotreeMapping,
)
from aus_trial_universe.agentic.tasks.eligibility_extraction.workflow import extract_eligibility


class _ScriptedClient:
    """Fake LlmClient.parse dispatching canned outputs by schema.

    extractions: consumed one per extractor call (attempt); oncotree: cancer_type
    string -> OncotreeMapping; verdicts: consumed one per judge call.
    """

    def __init__(self, extractions, oncotree, verdicts):
        self._extractions = extractions
        self._oncotree = oncotree
        self._verdicts = verdicts
        self.extractor_calls = 0
        self.judge_calls = 0

    def parse(self, output_schema, *, instructions, user_input, model=None,
              temperature=None, seed=None, max_completion_tokens=None):
        if output_schema is EligibilityExtraction:
            idx = min(self.extractor_calls, len(self._extractions) - 1)
            self.extractor_calls += 1
            return LlmResult(self._extractions[idx], "fake", "{}", False, 1)
        if output_schema is OncotreeMapping:
            m = self._oncotree.get(user_input, OncotreeMapping(oncotree_name="", oncotree_code=""))
            return LlmResult(m, "fake", "{}", False, 1)
        if output_schema is JudgeVerdict:
            idx = min(self.judge_calls, len(self._verdicts) - 1)
            self.judge_calls += 1
            return LlmResult(self._verdicts[idx], "fake", "{}", False, 1)
        raise AssertionError(f"unexpected schema {output_schema}")


def _extraction(*pairs):
    return EligibilityExtraction(
        rows=[ExtractedRow(cancer_type=c, gene_alteration=g) for c, g in pairs]
    )


def test_happy_path_consolidates_and_passes():
    client = _ScriptedClient(
        extractions=[_extraction(
            ("lung adenocarcinoma", "EGFR exon 19 deletion"),
            ("melanoma", "BRAF V600E"),
        )],
        oncotree={
            "lung adenocarcinoma": OncotreeMapping(oncotree_name="Lung Adenocarcinoma", oncotree_code="LUAD"),
            "melanoma": OncotreeMapping(oncotree_name="Melanoma", oncotree_code="MEL"),
        },
        verdicts=[JudgeVerdict(faithful=True)],
    )
    result = extract_eligibility(client, trial_id="NCT1", cohort="all", source_text="...")
    assert result.faithful and result.attempts == 1
    assert [r.oncotree_code for r in result.rows] == ["LUAD", "MEL"]
    assert result.rows[0].trialId == "NCT1" and result.rows[0].cohort == "all"
    assert result.rows[0].gene_alteration == "EGFR exon 19 deletion"


def test_refines_when_judge_unfaithful_then_passes():
    client = _ScriptedClient(
        extractions=[
            _extraction(("lung cancer", "EGFR")),                       # attempt 1 (vague)
            _extraction(("lung adenocarcinoma", "EGFR exon 19 deletion")),  # attempt 2 (after feedback)
        ],
        oncotree={
            "lung cancer": OncotreeMapping(oncotree_name="Lung", oncotree_code="LUNG"),
            "lung adenocarcinoma": OncotreeMapping(oncotree_name="Lung Adenocarcinoma", oncotree_code="LUAD"),
        },
        verdicts=[JudgeVerdict(faithful=False, problems=["cancer_type too vague"]), JudgeVerdict(faithful=True)],
    )
    result = extract_eligibility(client, trial_id="NCT2", cohort="all", source_text="...", max_attempts=3)
    assert result.faithful and result.attempts == 2
    assert result.rows[0].oncotree_code == "LUAD"
    assert client.extractor_calls == 2


def test_rule_failure_missing_code_exhausts_attempts():
    client = _ScriptedClient(
        extractions=[_extraction(("weird tumour", "X"))],
        oncotree={},  # no mapping -> empty code -> rule failure every attempt
        verdicts=[JudgeVerdict(faithful=True)],
    )
    result = extract_eligibility(client, trial_id="NCT3", cohort="all", source_text="...", max_attempts=2)
    assert not result.faithful and result.attempts == 2
    assert any("OncoTree code" in p for p in result.problems)
    assert client.judge_calls == 0  # rules fail before the judge is consulted
