from __future__ import annotations

from aus_trial_universe.drug_utility_path.oncotree_agentic_workflow.models import (
    MappingCandidate,
    NavDecision,
    NavigationStep,
    NavigationTrace,
)
from aus_trial_universe.drug_utility_path.oncotree_agentic_workflow.oncotree import OncoTree
from aus_trial_universe.drug_utility_path.oncotree_agentic_workflow.reasoning import (
    ReasoningSemanticReviewer,
)
from tests.drug_utility_path.oncotree_agentic_workflow.fakes import (
    ScriptedNavClient,
    review_ok,
    review_reject,
)


def _trace() -> NavigationTrace:
    return NavigationTrace(
        scope="specific",
        steps=(
            NavigationStep(
                parent_code=None,
                parent_name="OncoTree roots",
                stop_here=False,
                decisions=(NavDecision("LUNG", "Lung", "descend"),),
            ),
            NavigationStep(
                parent_code="LUNG",
                parent_name="Lung",
                stop_here=False,
                decisions=(NavDecision("NSCLC", "Non-Small Cell Lung Cancer", "emit"),),
            ),
        ),
    )


def test_reviewer_accepts_clean_mapping(tree: OncoTree):
    client = ScriptedNavClient(reviews=[review_ok("NSCLC is entailed.")])
    reviewer = ReasoningSemanticReviewer(client, tree)

    result = reviewer.review(
        input_text="non-small cell lung cancer",
        mappings=(MappingCandidate("NSCLC"),),
        trace=_trace(),
    )

    assert result.accepted is True
    assert result.findings == ()


def test_reviewer_rejects_and_returns_finding(tree: OncoTree):
    client = ScriptedNavClient(
        reviews=[review_reject("LUAD", "Over-specific; input only supports NSCLC.")]
    )
    reviewer = ReasoningSemanticReviewer(client, tree)

    result = reviewer.review(
        input_text="non-small cell lung cancer",
        mappings=(MappingCandidate("LUAD"),),
        trace=_trace(),
    )

    assert result.semantically_correct is False
    assert [f.code for f in result.findings] == ["LUAD"]
    assert result.findings[0].reason


def test_reviewer_prompt_includes_paths_and_trace(tree: OncoTree):
    client = ScriptedNavClient(reviews=[review_ok()])
    reviewer = ReasoningSemanticReviewer(client, tree)

    reviewer.review(
        input_text="non-small cell lung cancer",
        mappings=(MappingCandidate("NSCLC"),),
        trace=_trace(),
    )

    _, prompt = client.calls[-1]
    # The reviewer must see the resolved path and the navigation trace so it can
    # reason about over/under-specificity.
    assert "Lung > Non-Small Cell Lung Cancer" in prompt
    assert "Navigation trace" in prompt
    assert "DESCEND LUNG" in prompt


def test_reviewer_short_circuits_when_no_mapping(tree: OncoTree):
    client = ScriptedNavClient(reviews=[])
    reviewer = ReasoningSemanticReviewer(client, tree)

    result = reviewer.review(
        input_text="something",
        mappings=(),
        trace=NavigationTrace(scope="non_cancer"),
    )

    assert result.semantically_correct is False
    assert client.calls == []  # no LLM call when there is nothing to review
