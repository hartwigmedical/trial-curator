"""Workflow engine primitives (spec §5): fan-out and a bounded check/repair loop.

The orchestrator is plain Python. A task composes these primitives with its own
consolidation logic (e.g. building DNF rows) — no LLM decides control flow:

    parts   = fan_out([lambda: agent_a(text), lambda: agent_b(text)])   # dispatch
    result  = refine(                                                    # check + loop
        produce = lambda: consolidate(parts),
        check   = validate_rows,
        repair  = lambda rows, problems: consolidate(rerun(problems)),
    )
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Generic, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

DEFAULT_MAX_WORKERS = 8
DEFAULT_MAX_ATTEMPTS = 3


def fan_out(thunks: list[Callable[[], T]], *, max_workers: int = DEFAULT_MAX_WORKERS) -> list[T]:
    """Run zero-arg callables concurrently (threads; LLM calls are IO-bound).

    Results preserve input order. An exception in any thunk propagates (re-raised
    on `.result()`); wrap a thunk in try/except yourself if you want soft failures.
    """
    if not thunks:
        return []
    if len(thunks) == 1:  # avoid spinning up a pool for the common single-agent case
        return [thunks[0]()]
    with ThreadPoolExecutor(max_workers=min(max_workers, len(thunks))) as executor:
        futures = [executor.submit(thunk) for thunk in thunks]
        return [future.result() for future in futures]


@dataclass
class CheckResult:
    """Outcome of a check step: ok, plus human-readable problems to feed back."""

    ok: bool
    problems: list[str] = field(default_factory=list)


@dataclass
class RefineResult(Generic[T]):
    """Final result of a bounded refine loop."""

    value: T
    ok: bool
    attempts: int
    problems: list[str]


def refine(
    produce: Callable[[], T],
    check: Callable[[T], CheckResult],
    repair: Callable[[T, list[str]], T],
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> RefineResult[T]:
    """Bounded evaluator-optimizer loop (spec §5, 'check -> loop').

    Produce once and check it; while not ok and attempts remain, `repair` using
    the reported problems and re-check. Plain-Python, deterministic control flow.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    value = produce()
    result = check(value)
    attempts = 1
    while not result.ok and attempts < max_attempts:
        logger.info(
            "refine attempt %d/%d failed (%d problem(s)); repairing",
            attempts,
            max_attempts,
            len(result.problems),
        )
        value = repair(value, result.problems)
        result = check(value)
        attempts += 1
    return RefineResult(value=value, ok=result.ok, attempts=attempts, problems=result.problems)
