"""Shared doer→reviewer refine harness (spec §5; docs/v2_mapping_and_shared_loop_plan.md Part 1).

Every v2 agentic stage that runs a doer → reviewer → bounded-refine loop uses this ONE driver, so the loop
MECHANISM is shared and cannot drift per stage (it had already drifted — the mapping loop lacked the escalation
step the extraction loop has). Only the genuinely stage-specific pieces stay in the stage: the doer prompt (and how
it renders its own prior answer for a revision), the deterministic validators, and the reviewer(s)/panel.

The driver is deliberately **verdict-agnostic**: it deals only in `produce` / `check` / `CheckResult`. Each stage's
own reviewer OUTPUT SCHEMA (extraction `JudgeVerdict`, drug `ReviewVerdict`, …) stays stage-local — the loop is what
we unify, not the reviewer's data model. This is not just taste: `core/client._fingerprint` hashes
`output_schema.model_json_schema()` into the response-cache key, so renaming/merging a reviewer schema would change
that stage's cache keys and force a live recompute — unacceptable for the signed-off extraction/drug paths whose
frozen output must stay byte-identical across this refactor.

Stage contract — two callables with UNIFORM signatures:

    produce(feedback: str = "", prior=None) -> candidate
        Run the doer. `feedback` is the reviewer's problems, already '- '-bulleted by the driver. On a repair the
        stage MAY show the doer its own `prior` answer (REVISION MODE); a stage with no use for it ignores the arg.

    check(candidate, escalate: bool = False) -> CheckResult
        Run the deterministic validators + reviewer(s); return the gating problems. When `escalate=True` (only ever
        on the LAST-RESORT step, and only when the stage opted in via `escalate=True` below), the stage re-reviews
        asking the reviewer to ALSO supply a concrete `suggested_fix`, folded into the returned problems.

The driver owns the repair / stuck_repair glue, the `refine()` call, and the last-resort banner. `escalate=False`
(the default) reproduces a plain critique-only loop EXACTLY — no stuck_repair — for stages whose reviewers have no
`suggested_fix` channel (drug annotation, shared ANZCTR arm identification). `escalate=True` wires the last-resort
escalation used by extraction (and, from M2, mapping).
"""
from __future__ import annotations

import logging
from typing import Callable, TypeVar

from aus_trial_universe.core.logfmt import line
from aus_trial_universe.core.workflow import CheckResult, RefineResult, refine

logger = logging.getLogger(__name__)

T = TypeVar("T")

# The shared refine cap across every stage (the simple drug/arm-ID loops historically used 3; extraction used 6).
DEFAULT_MAX_ATTEMPTS = 6

# Emitted by the driver right before the last-resort escalation, so the loop event is logged uniformly (previously
# each stage logged its own near-identical banner from inside a bespoke stuck_repair closure).
_LAST_RESORT_BANNER = "last-resort · re-review in ESCALATION-MODE (writer stuck; requesting concrete fixes)"


def _fmt(problems: list[str]) -> str:
    """Render the reviewer's problems as the doer's revision feedback (uniform bulleting across every stage)."""
    return "\n".join(f"- {p}" for p in problems)


def review_refine(
    produce: Callable[..., T],
    check: Callable[..., CheckResult],
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    escalate: bool = False,
) -> RefineResult[T]:
    """Drive a doer→reviewer bounded-refine loop (the shared mechanism; see the module docstring for the contract).

    Returns the `RefineResult` from `core.workflow.refine` — `value` is the BEST attempt (fewest problems), plus
    `ok` / `attempts` / `problems`. `escalate=True` wires the last-resort ESCALATION-MODE step; `escalate=False`
    is a plain critique-only loop.
    """
    def repair(prev: T, problems: list[str]) -> T:
        return produce(feedback=_fmt(problems), prior=prev)

    def stuck_repair(prev: T, problems: list[str]) -> T:
        # LAST RESORT — refine() invokes this only when the doer is cycling or the budget is nearly spent. Re-review
        # in ESCALATION-MODE so the reviewer ALSO returns concrete suggested fixes, then hand those to the doer once.
        logger.info("")
        logger.info(line(_LAST_RESORT_BANNER))
        enriched = check(prev, escalate=True).problems
        return produce(feedback=_fmt(enriched or problems), prior=prev)

    return refine(
        produce=lambda: produce(),
        check=lambda c: check(c),
        repair=repair,
        max_attempts=max_attempts,
        stuck_repair=(stuck_repair if escalate else None),
    )
