from __future__ import annotations

import argparse
from collections.abc import Sequence

from aus_trial_universe.drug_utility_path.oncotree_agentic_workflow.models import (
    MappingFeedback,
    ReviewFinding,
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
    DEFAULT_REASONING_EFFORT,
    DEFAULT_REASONING_MODEL,
    OpenAIReasoningClient,
    ReasoningSemanticReviewer,
    describe_trace_lines,
)
from aus_trial_universe.drug_utility_path.oncotree_agentic_workflow.workflow import OncoTreeAgenticWorkflow


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Interactively map free-text cancer terms to OncoTree codes."
    )
    parser.add_argument(
        "--oncotree-yaml",
        default=str(DEFAULT_ONCOTREE_YAML),
        help=f"Path to OncoTree YAML. Default: {DEFAULT_ONCOTREE_YAML}",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=3,
        help="Maximum mapper/checker repair iterations. Default: 3.",
    )
    parser.add_argument(
        "--max-nodes",
        type=int,
        default=DEFAULT_MAX_NODES,
        help=f"Safety cap on nodes the navigator visits. Default: {DEFAULT_MAX_NODES}.",
    )
    parser.add_argument(
        "--once",
        default=None,
        help="Map one input string and exit instead of starting the prompt loop.",
    )
    parser.add_argument(
        "--api-model",
        default=DEFAULT_REASONING_MODEL,
        help=(
            "OpenAI model for the navigator mapper and semantic reviewer. "
            f"Default: {DEFAULT_REASONING_MODEL}."
        ),
    )
    parser.add_argument(
        "--reasoning-effort",
        default=DEFAULT_REASONING_EFFORT,
        choices=("minimal", "low", "medium", "high", "none"),
        help=(
            "Reasoning effort for the OpenAI mapper/reviewer. Use 'none' to omit "
            f"the setting. Default: {DEFAULT_REASONING_EFFORT}."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    tree = OncoTree.from_yaml(args.oncotree_yaml)
    client = OpenAIReasoningClient(
        model=args.api_model,
        reasoning_effort=args.reasoning_effort,
    )
    workflow = OncoTreeAgenticWorkflow(
        tree=tree,
        mapper=NavigatorMapper(client, tree, max_nodes=args.max_nodes),
        reviewer=ReasoningSemanticReviewer(client, tree),
        max_iterations=args.max_iterations,
    )

    if args.once is not None:
        _run_once(workflow, args.once)
        return 0

    print("OncoTree agentic mapper")
    print("Type a cancer term, or type 'exit' to stop.")
    while True:
        try:
            text = input("\ncancer> ").strip()
        except EOFError:
            print()
            return 0

        if not text:
            continue
        if text.casefold() in {"exit", "quit", "q"}:
            return 0

        _run_once(workflow, text)


def _run_once(workflow: OncoTreeAgenticWorkflow, input_text: str) -> None:
    result = workflow.run(input_text)
    print(format_reasoning_log(result))


def format_reasoning_log(result: WorkflowResult) -> str:
    lines = [f"Input: {result.input_text}", ""]

    for iteration in result.iterations:
        lines.append(f"Iteration {iteration.iteration}")
        lines.append("  Mapper feedback received:")
        if iteration.mapper_feedback:
            for feedback in iteration.mapper_feedback:
                lines.extend(_format_feedback(feedback))
        else:
            lines.append("    none")

        lines.append(f"  Mapper navigation (scope={iteration.trace.scope}):")
        lines.extend(describe_trace_lines(iteration.trace))
        if iteration.trace.truncated:
            lines.append("    NOTE: navigation hit the max-nodes cap; walk was truncated.")

        lines.append("  Mapper proposed:")
        if iteration.proposed_mappings:
            for mapping in iteration.proposed_mappings:
                lines.append(f"    {mapping.code} | confidence={mapping.confidence}")
                if mapping.rationale:
                    lines.append(f"       rationale: {mapping.rationale}")
        else:
            lines.append("    No mapping proposed.")

        lines.append("  Checker:")
        lines.append(f"    code existence: {_pass_fail(iteration.review.valid_codes)}")
        lines.append(f"    semantic review: {_pass_fail(iteration.review.semantically_correct)}")
        if iteration.review.reasoning:
            lines.append("    reasoning:")
            for note in iteration.review.reasoning:
                lines.append(f"      - {note}")
        if iteration.review.findings:
            lines.append("    findings:")
            for finding in iteration.review.findings:
                lines.extend(_format_finding(finding))
            lines.append("  Feedback sent back to mapper:")
            for finding in iteration.review.findings:
                lines.append(f"    rejected code: {finding.code}")
                lines.append(f"    note: {finding.reason}")
        else:
            lines.append("    findings: none")
        lines.append("")

    lines.append(f"Final status: {result.status}")
    if result.codes:
        lines.append(f"Final OncoTree code(s): {' | '.join(result.codes)}")
    else:
        lines.append("Final OncoTree code(s): <none>")
    lines.append(f"Loop stop reason: {_stop_reason(result)}")
    return "\n".join(lines)


def _format_finding(finding: ReviewFinding) -> list[str]:
    lines = [f"      - {finding.code}: {finding.reason}"]
    if finding.suggested_action:
        lines.append(f"        suggested action: {finding.suggested_action}")
    return lines


def _format_feedback(feedback: MappingFeedback) -> list[str]:
    return [
        f"    rejected code: {feedback.code}",
        f"    reviewer note: {feedback.note}",
    ]


def _pass_fail(value: bool) -> str:
    return "PASS" if value else "FAIL"


def _stop_reason(result: WorkflowResult) -> str:
    if result.status == "accepted":
        if len(result.iterations) > 1:
            return (
                f"checker accepted the mapper proposal after {len(result.iterations)} "
                "iteration(s), including at least one repair round"
            )
        return "checker accepted the mapper proposal; no repair iteration was needed"
    if result.status == "needs_human_review" and not result.codes:
        return "mapper abstained: no OncoTree code was entailed by the input"
    if result.status == "needs_human_review":
        return "checker could not confidently accept or repair the proposal"
    return "maximum iterations reached or repeated checker feedback did not change the mapping"


if __name__ == "__main__":
    raise SystemExit(main())
