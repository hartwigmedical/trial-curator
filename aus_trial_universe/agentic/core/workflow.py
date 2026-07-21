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
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Generic, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")
I = TypeVar("I")
R = TypeVar("R")

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


def run_parallel(
    items: list[I],
    work: Callable[[I], R],
    on_result: Callable[[I, R | None, Exception | None], None],
    *,
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> None:
    """Parallel map with a PER-ITEM sink — the durability primitive shared by every batch process.

    `work(item)` runs in a worker thread (parallel; the expensive LLM/IO). As each item finishes,
    `on_result(item, result, exc)` is invoked **in the calling thread, one at a time, in completion order** —
    so it is the safe place to mutate shared state and **persist that item to disk before the next is handled**.
    A finished item is therefore written straightaway; a crash / lost connection loses only the still-running
    items, never a completed one. `work` exceptions are captured and delivered to `on_result` as
    (item, None, exc) — the batch continues past a failed item.
    """
    if not items:
        return
    with ThreadPoolExecutor(max_workers=min(max_workers, len(items))) as executor:
        futures = {executor.submit(work, it): it for it in items}
        for future in as_completed(futures):
            item = futures[future]
            try:
                on_result(item, future.result(), None)
            except Exception as exc:  # noqa: BLE001 — surfaced to the sink; batch continues
                on_result(item, None, exc)


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

    Produce once and check; while not ok and attempts remain, `repair` from the reported problems and re-check.
    Two convergence properties so the attempt cap is meaningful rather than arbitrary:
    - **Return the BEST attempt** — the one with the fewest problems seen (ties: earliest), NOT whatever the last
      attempt happened to land on. A repair that made things worse never wins.
    - **Stop when it stops converging** — if an attempt fails to beat the best-so-far problem count, the loop is
      oscillating rather than improving (e.g. row count flip-flopping); further attempts just burn latency, so
      cut early and keep the best. A strictly-improving loop still runs the full budget.
    Plain-Python, deterministic control flow.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    value = produce()
    result = check(value)
    best_value, best_result, best_score = value, result, len(result.problems)
    seen = {tuple(sorted(result.problems))}     # problem sets already produced — for cycle detection
    attempts = 1
    while not result.ok and attempts < max_attempts:
        logger.debug("refine attempt %d/%d: %d problem(s); repairing", attempts, max_attempts, len(result.problems))
        value = repair(value, result.problems)
        result = check(value)
        attempts += 1
        if result.ok or len(result.problems) < best_score:   # keep the fewest-problems attempt (ties: earliest)
            best_value, best_result, best_score = value, result, len(result.problems)
        if result.ok:
            break
        sig = tuple(sorted(result.problems))
        if sig in seen:   # this exact problem set already occurred -> repair is cycling, not converging; cut early
            logger.debug("refine attempt %d reproduced a prior problem set; stopping (not converging)", attempts)
            break
        seen.add(sig)
    return RefineResult(value=best_value, ok=best_result.ok, attempts=attempts, problems=best_result.problems)
