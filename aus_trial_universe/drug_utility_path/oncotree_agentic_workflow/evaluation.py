"""Live correctness harness for the OncoTree agentic workflow.

Runs every row of the curated :data:`truth_set.TRUTH_SET` through the real
navigator + reviewer and scores the emitted codes against the human-curated
correct codes (set-based precision / recall / F1, plus the missing and extra
codes per row). This is how prompt changes are judged against a real match rate,
rather than by spot-checking single inputs.

Usage (from the repo root, in an env with `openai` and OPENAI_API_KEY set):

    python -m aus_trial_universe.drug_utility_path.oncotree_agentic_workflow.evaluation
    python -m aus_trial_universe.drug_utility_path.oncotree_agentic_workflow.evaluation --limit 3
    python -m aus_trial_universe.drug_utility_path.oncotree_agentic_workflow.evaluation --reasoning-effort high
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass

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
)
from aus_trial_universe.drug_utility_path.oncotree_agentic_workflow.truth_set import TRUTH_SET, TruthRow
from aus_trial_universe.drug_utility_path.oncotree_agentic_workflow.workflow import OncoTreeAgenticWorkflow


@dataclass(frozen=True)
class RowScore:
    input_text: str
    gold: tuple[str, ...]
    predicted: tuple[str, ...]
    status: str
    precision: float
    recall: float
    f1: float
    missing: tuple[str, ...]
    extra: tuple[str, ...]

    @property
    def exact(self) -> bool:
        return not self.missing and not self.extra


def score_row(predicted: Sequence[str], gold: Sequence[str]) -> tuple[float, float, float, tuple[str, ...], tuple[str, ...]]:
    pset, gset = set(predicted), set(gold)
    true_positives = len(pset & gset)
    precision = true_positives / len(pset) if pset else (1.0 if not gset else 0.0)
    recall = true_positives / len(gset) if gset else 1.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall)
        else 0.0
    )
    missing = tuple(sorted(gset - pset))
    extra = tuple(sorted(pset - gset))
    return precision, recall, f1, missing, extra


def evaluate(
    rows: Sequence[TruthRow],
    workflow: OncoTreeAgenticWorkflow,
) -> list[RowScore]:
    scores: list[RowScore] = []
    for row in rows:
        result = workflow.run(row.input_text)
        precision, recall, f1, missing, extra = score_row(result.codes, row.correct_codes)
        scores.append(
            RowScore(
                input_text=row.input_text,
                gold=row.correct_codes,
                predicted=tuple(result.codes),
                status=result.status,
                precision=precision,
                recall=recall,
                f1=f1,
                missing=missing,
                extra=extra,
            )
        )
    return scores


def format_report(scores: Sequence[RowScore]) -> str:
    lines: list[str] = []
    for index, score in enumerate(scores, start=1):
        tag = "EXACT" if score.exact else "DIFF"
        lines.append(f"[{index:>2}] {score.input_text[:70]}")
        lines.append(f"     gold: {' | '.join(score.gold) or '<none>'}")
        lines.append(f"     pred: {' | '.join(score.predicted) or '<none>'}")
        if score.missing:
            lines.append(f"     missing: {' | '.join(score.missing)}")
        if score.extra:
            lines.append(f"     extra:   {' | '.join(score.extra)}")
        lines.append(
            f"     P={score.precision:.2f} R={score.recall:.2f} "
            f"F1={score.f1:.2f}  status={score.status}  {tag}"
        )
        lines.append("")

    lines.append(_aggregate(scores))
    return "\n".join(lines)


def _aggregate(scores: Sequence[RowScore]) -> str:
    if not scores:
        return "No rows evaluated."

    exact = sum(1 for s in scores if s.exact)
    n = len(scores)
    macro_f1 = sum(s.f1 for s in scores) / n

    total_tp = sum(len(set(s.predicted) & set(s.gold)) for s in scores)
    total_pred = sum(len(set(s.predicted)) for s in scores)
    total_gold = sum(len(set(s.gold)) for s in scores)
    micro_p = total_tp / total_pred if total_pred else 0.0
    micro_r = total_tp / total_gold if total_gold else 0.0
    micro_f1 = (
        2 * micro_p * micro_r / (micro_p + micro_r) if (micro_p + micro_r) else 0.0
    )

    return "\n".join(
        [
            "=== Aggregate ===",
            f"Rows: {n} | exact matches: {exact} ({exact / n:.0%})",
            f"Micro  P={micro_p:.2f} R={micro_r:.2f} F1={micro_f1:.2f}",
            f"Macro  F1={macro_f1:.2f}",
        ]
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oncotree-yaml", default=str(DEFAULT_ONCOTREE_YAML))
    parser.add_argument("--api-model", default=DEFAULT_REASONING_MODEL)
    parser.add_argument(
        "--reasoning-effort",
        default=DEFAULT_REASONING_EFFORT,
        choices=("minimal", "low", "medium", "high", "none"),
    )
    parser.add_argument("--max-iterations", type=int, default=3)
    parser.add_argument("--max-nodes", type=int, default=DEFAULT_MAX_NODES)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Evaluate only the first N truth-set rows (saves API calls).",
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

    rows = TRUTH_SET[: args.limit] if args.limit else TRUTH_SET
    scores = evaluate(rows, workflow)
    print(format_report(scores))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
