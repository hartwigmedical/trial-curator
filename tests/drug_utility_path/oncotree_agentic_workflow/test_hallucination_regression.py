"""Regression tests built from a curated truth set of real trial `cancerTypes`.

Each entry is (input, incorrect_codes, correct_codes):
- ``incorrect_codes`` is what the previous (flat-mapper) version emitted. Some of
  those codes are pure hallucinations (not in the project OncoTree YAML, e.g.
  ``GBM``/``OV``/``MESO``); others exist but were the wrong choice (e.g. ``BRCA``
  for "Breast Cancer", which should map to the organ root ``BREAST``).
- ``correct_codes`` is the human-curated ground truth. Every code in it must be a
  real OncoTree code or the allowed fallback ``Solid-Tumor``.

These tests verify the ground truth is internally consistent and that the system
can never *accept* a hallucinated code. They do NOT assert the navigator reaches
the correct codes — that requires live model runs (see the live correctness
harness) — but they pin the data the navigator is judged against.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from aus_trial_universe.drug_utility_path.oncotree_agentic_workflow.models import (
    ALLOWED_FALLBACK_CODES,
    MappingCandidate,
    MappingFeedback,
    MappingOutcome,
    NavigationTrace,
)
from aus_trial_universe.drug_utility_path.oncotree_agentic_workflow.oncotree import OncoTree
from aus_trial_universe.drug_utility_path.oncotree_agentic_workflow.reasoning import (
    ReasoningSemanticReviewer,
)
from aus_trial_universe.drug_utility_path.oncotree_agentic_workflow.truth_set import TRUTH_SET
from aus_trial_universe.drug_utility_path.oncotree_agentic_workflow.workflow import OncoTreeAgenticWorkflow
from tests.drug_utility_path.oncotree_agentic_workflow.fakes import ScriptedNavClient, review_ok

# Codes the previous version emitted that are pure hallucinations: present in an
# incorrect column but neither a real OncoTree code nor an allowed fallback.
HALLUCINATED_CODES = ["GBM", "MESO", "OV", "STS", "BTC", "FTC", "LYM", "LGSC", "FTT", "PPT", "CNS"]


def _is_valid(code: str, tree: OncoTree) -> bool:
    return tree.has_code(code) or code in ALLOWED_FALLBACK_CODES


def test_hallucinated_list_matches_dataset(tree: OncoTree):
    """The hallucination list must be exactly the invalid incorrect codes."""
    derived = {
        code
        for _, incorrect, _ in TRUTH_SET
        for code in incorrect
        if not _is_valid(code, tree)
    }
    assert derived == set(HALLUCINATED_CODES)


@pytest.mark.parametrize("code", HALLUCINATED_CODES)
def test_hallucinated_code_is_not_valid(code, tree: OncoTree):
    assert _is_valid(code, tree) is False, f"{code} is unexpectedly valid"


@pytest.mark.parametrize(
    "input_text, correct",
    [(text, correct) for text, _, correct in TRUTH_SET],
    ids=[text[:40] for text, _, _ in TRUTH_SET],
)
def test_every_correct_code_is_a_real_code_or_fallback(input_text, correct, tree: OncoTree):
    invalid = [code for code in correct if not _is_valid(code, tree)]
    assert invalid == [], f"truth set references unknown code(s): {invalid}"


@pytest.mark.parametrize("code", HALLUCINATED_CODES)
def test_workflow_never_accepts_a_hallucinated_code(code, tree: OncoTree):
    """If any mapper proposes a hallucinated code, the checker must reject it."""

    class FixedMapper:
        def map(
            self, *, input_text: str, feedback: Sequence[MappingFeedback] = ()
        ) -> MappingOutcome:
            return MappingOutcome(
                (MappingCandidate(code),),
                NavigationTrace(scope="specific"),
            )

    review_client = ScriptedNavClient(reviews=[review_ok()])
    result = OncoTreeAgenticWorkflow(
        tree=tree,
        mapper=FixedMapper(),
        reviewer=ReasoningSemanticReviewer(review_client, tree),
        max_iterations=2,
    ).run("anything")

    assert result.status != "accepted"
    assert all(it.review.valid_codes is False for it in result.iterations)
    assert any(
        finding.code == code
        for it in result.iterations
        for finding in it.review.findings
    )
    assert review_client.calls == []  # never reached semantic review
