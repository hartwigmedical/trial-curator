from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from aus_trial_universe.drug_utility_path.oncotree_agentic_workflow.models import (
    ALLOWED_FALLBACK_CODES,
    MappingCandidate,
    MappingFeedback,
    MappingIteration,
    MappingOutcome,
    MappingStatus,
    NavigationTrace,
    ReviewFinding,
    ReviewResult,
    WorkflowResult,
)
from aus_trial_universe.drug_utility_path.oncotree_agentic_workflow.navigation import (
    DEFAULT_MAX_NODES,
    NavigatorMapper,
)
from aus_trial_universe.drug_utility_path.oncotree_agentic_workflow.oncotree import (
    DEFAULT_ONCOTREE_YAML,
    OncoTree,
)
from aus_trial_universe.drug_utility_path.oncotree_agentic_workflow.reasoning import (
    OpenAIReasoningClient,
    ReasoningSemanticReviewer,
)


class MapperAgent(Protocol):
    def map(
        self,
        *,
        input_text: str,
        feedback: Sequence[MappingFeedback] = (),
    ) -> MappingOutcome:
        ...


class ReviewerAgent(Protocol):
    def review(
        self,
        *,
        input_text: str,
        mappings: Sequence[MappingCandidate],
        trace: NavigationTrace,
    ) -> ReviewResult:
        ...


class OncoTreeAgenticWorkflow:
    """Orchestrator: mapper -> two-step checker -> feedback -> mapper, bounded.

    The checker validates in two stages. First a cheap programmatic check that
    every proposed code exists in the YAML (or is an allowed fallback) — with
    the navigator this should always pass, but it stays as a guard. Then the LLM
    semantic reviewer. A semantic rejection is fed back to the mapper and the
    loop repeats until the checker accepts or ``max_iterations`` is reached.
    """

    def __init__(
        self,
        *,
        tree: OncoTree,
        mapper: MapperAgent,
        reviewer: ReviewerAgent,
        max_iterations: int = 3,
    ) -> None:
        self.tree = tree
        self.mapper = mapper
        self.reviewer = reviewer
        self.max_iterations = max_iterations

    @classmethod
    def from_yaml(
        cls,
        path: str = str(DEFAULT_ONCOTREE_YAML),
        *,
        client: OpenAIReasoningClient | None = None,
        max_iterations: int = 3,
        max_nodes: int = DEFAULT_MAX_NODES,
    ) -> "OncoTreeAgenticWorkflow":
        tree = OncoTree.from_yaml(path)
        client = client or OpenAIReasoningClient()
        return cls(
            tree=tree,
            mapper=NavigatorMapper(client, tree, max_nodes=max_nodes),
            reviewer=ReasoningSemanticReviewer(client, tree),
            max_iterations=max_iterations,
        )

    def run(self, input_text: str) -> WorkflowResult:
        feedback: list[MappingFeedback] = []
        iterations: list[MappingIteration] = []
        terminal_status: MappingStatus | None = None

        for iteration_number in range(1, self.max_iterations + 1):
            outcome = self.mapper.map(input_text=input_text, feedback=tuple(feedback))
            review = self._review(input_text, outcome)
            iterations.append(
                MappingIteration(
                    iteration=iteration_number,
                    mapper_feedback=tuple(feedback),
                    trace=outcome.trace,
                    proposed_mappings=outcome.proposed_mappings,
                    review=review,
                )
            )

            proposed = outcome.proposed_mappings
            if review.accepted and proposed:
                return WorkflowResult(
                    input_text=input_text,
                    status="accepted",
                    codes=tuple(mapping.code for mapping in proposed),
                    iterations=tuple(iterations),
                )

            if not proposed:
                # Navigator abstained (non-cancer / nothing entailed) -> blank.
                terminal_status = "needs_human_review"
                break

            new_feedback = _feedback_from_review(review)
            if not new_feedback:
                terminal_status = "needs_human_review"
                break

            seen = {(item.code, item.note) for item in feedback}
            if all((item.code, item.note) in seen for item in new_feedback):
                # Reviewer repeated itself; the loop is not converging.
                terminal_status = "failed"
                break
            feedback.extend(new_feedback)

        return WorkflowResult(
            input_text=input_text,
            status=terminal_status or _unresolved_status(iterations),
            codes=(
                tuple(mapping.code for mapping in iterations[-1].proposed_mappings)
                if iterations
                else ()
            ),
            iterations=tuple(iterations),
        )

    def _review(self, input_text: str, outcome: MappingOutcome) -> ReviewResult:
        programmatic = self._programmatic_code_check(outcome.proposed_mappings)
        if not programmatic.valid_codes:
            return programmatic

        semantic = self.reviewer.review(
            input_text=input_text,
            mappings=outcome.proposed_mappings,
            trace=outcome.trace,
        )
        return ReviewResult(
            valid_codes=True,
            semantically_correct=semantic.semantically_correct,
            findings=semantic.findings,
            reasoning=(*programmatic.reasoning, *semantic.reasoning),
        )

    def _programmatic_code_check(
        self,
        proposed: Sequence[MappingCandidate],
    ) -> ReviewResult:
        reasoning: list[str] = []
        findings: list[ReviewFinding] = []
        for mapping in proposed:
            if mapping.code in ALLOWED_FALLBACK_CODES:
                reasoning.append(
                    f"{mapping.code}: programmatic check passed as an allowed fallback value."
                )
            elif self.tree.has_code(mapping.code):
                node = self.tree.get(mapping.code)
                name = node.name if node else mapping.code
                reasoning.append(
                    f"{mapping.code}: programmatic check passed; exists in YAML as '{name}'."
                )
            else:
                findings.append(
                    ReviewFinding(
                        code=mapping.code,
                        reason=(
                            "Programmatic code check failed: code is neither present "
                            "in the OncoTree YAML nor an allowed fallback."
                        ),
                        suggested_action="Return only real OncoTree codes or allowed fallback values.",
                    )
                )
                reasoning.append(
                    f"{mapping.code}: programmatic check failed; code does not exist in YAML."
                )

        return ReviewResult(
            valid_codes=not findings,
            semantically_correct=not findings,
            findings=tuple(findings),
            reasoning=tuple(reasoning),
        )


def map_oncotree_codes(
    input_text: str,
    *,
    oncotree_yaml: str = str(DEFAULT_ONCOTREE_YAML),
    max_iterations: int = 3,
) -> WorkflowResult:
    workflow = OncoTreeAgenticWorkflow.from_yaml(
        oncotree_yaml,
        max_iterations=max_iterations,
    )
    return workflow.run(input_text)


def _feedback_from_review(review: ReviewResult) -> list[MappingFeedback]:
    return [
        MappingFeedback(code=finding.code, note=finding.reason)
        for finding in review.findings
    ]


def _unresolved_status(iterations: Sequence[MappingIteration]) -> MappingStatus:
    if not iterations or not iterations[-1].proposed_mappings:
        return "needs_human_review"
    return "failed"
