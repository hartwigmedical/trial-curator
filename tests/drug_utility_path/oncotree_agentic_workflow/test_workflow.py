from __future__ import annotations

from collections.abc import Sequence

from aus_trial_universe.drug_utility_path.oncotree_agentic_workflow.cli import format_reasoning_log
from aus_trial_universe.drug_utility_path.oncotree_agentic_workflow.models import (
    MappingCandidate,
    MappingFeedback,
    MappingOutcome,
    NavigationTrace,
)
from aus_trial_universe.drug_utility_path.oncotree_agentic_workflow.navigation import NavigatorMapper
from aus_trial_universe.drug_utility_path.oncotree_agentic_workflow.oncotree import OncoTree
from aus_trial_universe.drug_utility_path.oncotree_agentic_workflow.reasoning import (
    OpenAIReasoningClient,
    ReasoningSemanticReviewer,
)
from aus_trial_universe.drug_utility_path.oncotree_agentic_workflow.workflow import OncoTreeAgenticWorkflow
from tests.drug_utility_path.oncotree_agentic_workflow.fakes import (
    FakeOpenAIClient,
    ScriptedNavClient,
    node_decision,
    review_ok,
    review_reject,
    root_decision,
)


def _workflow(tree: OncoTree, client: ScriptedNavClient, **kwargs) -> OncoTreeAgenticWorkflow:
    return OncoTreeAgenticWorkflow(
        tree=tree,
        mapper=NavigatorMapper(client, tree),
        reviewer=ReasoningSemanticReviewer(client, tree),
        **kwargs,
    )


def test_workflow_accepts_specific_term_in_one_round(tree: OncoTree):
    client = ScriptedNavClient(
        root=root_decision("specific", LUNG="descend"),
        nodes={"LUNG": [node_decision(NSCLC="descend")], "NSCLC": [node_decision(stop_here=True)]},
        reviews=[review_ok("NSCLC is entailed.")],
    )

    result = _workflow(tree, client).run("non-small cell lung cancer")

    assert result.status == "accepted"
    assert result.codes == ("NSCLC",)
    assert len(result.iterations) == 1


def test_workflow_multi_label(tree: OncoTree):
    client = ScriptedNavClient(
        root=root_decision("specific", BREAST="descend", LUNG="descend"),
        nodes={"BREAST": [node_decision(stop_here=True)], "LUNG": [node_decision(stop_here=True)]},
        reviews=[review_ok("Two distinct primaries are both supported.")],
    )

    result = _workflow(tree, client).run("breast and lung cancer")

    assert result.status == "accepted"
    assert set(result.codes) == {"BREAST", "LUNG"}


def test_workflow_repairs_over_specific_mapping_across_rounds(tree: OncoTree):
    """The checker's semantic reasoning drives a real mapper<->checker exchange."""
    client = ScriptedNavClient(
        root=root_decision("specific", LUNG="descend"),
        nodes={
            "LUNG": [node_decision(NSCLC="descend")],
            # Round 1 over-specifies (LUAD); round 2 stops at NSCLC after feedback.
            "NSCLC": [node_decision(LUAD="emit"), node_decision(stop_here=True)],
        },
        reviews=[
            review_reject(
                "LUAD",
                "Over-specific: input supports NSCLC, not the adenocarcinoma subtype.",
                "Stop at NSCLC.",
            ),
            review_ok("NSCLC is the supported level of specificity."),
        ],
    )

    result = _workflow(tree, client).run("non-small cell lung cancer")

    assert result.status == "accepted"
    assert result.codes == ("NSCLC",)
    assert len(result.iterations) == 2
    assert result.iterations[0].review.accepted is False
    assert result.iterations[1].mapper_feedback  # feedback carried into round 2

    log = format_reasoning_log(result)
    assert "Feedback sent back to mapper:" in log
    assert "Over-specific" in log
    assert "including at least one repair round" in log


def test_workflow_abstains_for_non_cancer(tree: OncoTree):
    client = ScriptedNavClient(root=root_decision("non_cancer"))

    result = _workflow(tree, client).run("benign lung condition")

    assert result.status == "needs_human_review"
    assert result.codes == ()

    log = format_reasoning_log(result)
    assert "Final OncoTree code(s): <none>" in log
    assert "abstained" in log


def test_workflow_accepts_pan_cancer_fallback(tree: OncoTree):
    client = ScriptedNavClient(
        root=root_decision("pan_cancer"),
        reviews=[review_ok("Tumour-agnostic scope.")],
    )

    result = _workflow(tree, client).run("any NTRK-fusion cancer")

    assert result.status == "accepted"
    assert result.codes == ("Pan-cancer",)


def test_workflow_accepts_solid_tumour_fallback(tree: OncoTree):
    client = ScriptedNavClient(
        root=root_decision("solid_tumour"),
        reviews=[review_ok("Broad solid tumour scope.")],
    )

    result = _workflow(tree, client).run("advanced solid tumours")

    assert result.status == "accepted"
    assert result.codes == ("Solid-Tumor",)


def test_workflow_fails_when_checker_repeats_same_rejection(tree: OncoTree):
    client = ScriptedNavClient(
        root=root_decision("specific", LUNG="emit"),
        reviews=[review_reject("LUNG", "Input does not support any lung cancer.")],
    )

    result = _workflow(tree, client).run("ambiguous input")

    assert result.status == "failed"
    assert result.codes == ("LUNG",)
    assert len(result.iterations) == 2


def test_programmatic_check_catches_hallucinated_code_then_repairs(tree: OncoTree):
    """A mapper that bypasses navigation still cannot smuggle a fake code through."""
    review_client = ScriptedNavClient(reviews=[review_ok("NSCLC is entailed.")])

    class BadThenGoodMapper:
        def map(
            self, *, input_text: str, feedback: Sequence[MappingFeedback] = ()
        ) -> MappingOutcome:
            trace = NavigationTrace(scope="specific")
            if not feedback:
                return MappingOutcome((MappingCandidate("NOT_A_REAL_CODE"),), trace)
            return MappingOutcome((MappingCandidate("NSCLC"),), trace)

    result = OncoTreeAgenticWorkflow(
        tree=tree,
        mapper=BadThenGoodMapper(),
        reviewer=ReasoningSemanticReviewer(review_client, tree),
    ).run("non-small cell lung cancer")

    assert result.status == "accepted"
    assert result.codes == ("NSCLC",)
    assert len(result.iterations) == 2
    assert result.iterations[0].review.valid_codes is False
    assert result.iterations[1].review.accepted is True


def test_reasoning_log_shows_navigation_checker_and_final_code(tree: OncoTree):
    client = ScriptedNavClient(
        root=root_decision("specific", LUNG="descend"),
        nodes={"LUNG": [node_decision(NSCLC="descend")], "NSCLC": [node_decision(stop_here=True)]},
        reviews=[review_ok("NSCLC is entailed.")],
    )

    log = format_reasoning_log(_workflow(tree, client).run("non-small cell lung cancer"))

    assert "Mapper navigation (scope=specific)" in log
    assert "Mapper proposed:" in log
    assert "code existence: PASS" in log
    assert "semantic review: PASS" in log
    assert "Final OncoTree code(s): NSCLC" in log


# --- OpenAIReasoningClient wiring -------------------------------------------

def test_openai_reasoning_client_applies_model_settings():
    wrapped = FakeOpenAIClient()
    client = OpenAIReasoningClient(
        model="gpt-5.5",
        reasoning_effort="medium",
        max_output_tokens=1200,
        store=False,
        openai_client=wrapped,
    )

    payload = client.complete_json(system_prompt="Return JSON.", user_prompt="{}")

    assert payload == {"ok": True}
    assert wrapped.responses.kwargs["model"] == "gpt-5.5"
    assert wrapped.responses.kwargs["reasoning"] == {"effort": "medium"}
    assert wrapped.responses.kwargs["max_output_tokens"] == 1200
    assert wrapped.responses.kwargs["store"] is False
    # Default format when no schema is requested.
    assert wrapped.responses.kwargs["text"]["format"] == {"type": "json_object"}


def test_openai_reasoning_client_passes_strict_schema():
    wrapped = FakeOpenAIClient()
    client = OpenAIReasoningClient(openai_client=wrapped)
    strict_format = {"type": "json_schema", "name": "x", "strict": True, "schema": {}}

    client.complete_json(system_prompt="s", user_prompt="u", response_format=strict_format)

    assert wrapped.responses.kwargs["text"]["format"] == strict_format
