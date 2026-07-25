"""Tests for workflow engine primitives (fan_out, refine)."""
from __future__ import annotations

import pytest

from aus_trial_universe.agentic.core.workflow import CheckResult, fan_out, refine, run_parallel


def test_fan_out_preserves_order():
    thunks = [(lambda i=i: i * 10) for i in range(5)]
    assert fan_out(thunks) == [0, 10, 20, 30, 40]


def test_run_parallel_sink_per_item_isolates_failures():
    """The durability primitive: every item reaches the sink exactly once; a failing item is delivered as an
    exception and does NOT stop the others (each completed item is the caller's chance to persist it)."""
    def work(x):
        if x == 3:
            raise ValueError("boom")
        return x * 10

    seen: dict[int, tuple] = {}
    run_parallel([1, 2, 3, 4], work, lambda item, res, exc: seen.__setitem__(item, (res, exc)), max_workers=2)
    assert set(seen) == {1, 2, 3, 4}
    assert seen[1] == (10, None) and seen[2] == (20, None) and seen[4] == (40, None)
    assert seen[3][0] is None and isinstance(seen[3][1], ValueError)   # failure surfaced, batch continued


def test_run_parallel_empty_is_noop():
    calls = []
    run_parallel([], lambda x: x, lambda i, r, e: calls.append(i))
    assert calls == []


def test_fan_out_empty_and_single():
    assert fan_out([]) == []
    assert fan_out([lambda: 42]) == [42]


def test_fan_out_propagates_exceptions():
    def boom():
        raise ValueError("nope")

    with pytest.raises(ValueError):
        fan_out([lambda: 1, boom])


def test_refine_ok_first_try_skips_repair():
    calls = {"produce": 0, "check": 0, "repair": 0}

    def produce():
        calls["produce"] += 1
        return "v0"

    def check(v):
        calls["check"] += 1
        return CheckResult(ok=True)

    def repair(v, problems):
        calls["repair"] += 1
        return v

    result = refine(produce, check, repair)
    assert result.ok and result.attempts == 1 and result.value == "v0"
    assert calls == {"produce": 1, "check": 1, "repair": 0}


def test_refine_repairs_until_ok():
    def produce():
        return 0

    def check(v):
        return CheckResult(ok=(v >= 2), problems=[] if v >= 2 else [f"too small: {v}"])

    def repair(v, problems):
        return v + 1

    result = refine(produce, check, repair, max_attempts=5)
    assert result.ok and result.value == 2 and result.attempts == 3


def test_refine_stops_early_when_cycling_and_returns_best():
    def produce():
        return 0

    def check(v):
        return CheckResult(ok=False, problems=["always bad"])   # identical problem set every attempt = cycling

    def repair(v, problems):
        return v + 1

    result = refine(produce, check, repair, max_attempts=3)
    # attempt 2 reproduces attempt 1's exact problem set -> not converging -> stop early (no 3rd attempt);
    # return the BEST (fewest-problems, ties=earliest) attempt, not the last.
    assert not result.ok and result.attempts == 2 and result.value == 0


def test_refine_stuck_repair_fires_on_cycle_before_budget_end():
    """Cycling (repeated problem-set) escalates IMMEDIATELY — it does not waste the middle attempts."""
    calls = {"stuck": 0}

    def check(v):
        return CheckResult(ok=(v == "FIXED"), problems=["same"])   # identical every attempt -> cycling

    def stuck_repair(v, problems):
        calls["stuck"] += 1
        return "FIXED"

    result = refine(lambda: "start", check, lambda v, p: "loop", max_attempts=6, stuck_repair=stuck_repair)
    assert calls["stuck"] == 1 and result.ok and result.attempts == 3   # escalated at attempt 3, not attempt 5


def test_refine_stuck_repair_fires_near_cap_even_without_cycling():
    """A slow-but-not-converging loop (distinct problems each attempt, never cycles) still gets the last-resort
    escalation on its final attempt when the budget is nearly spent."""
    calls = {"repair": 0, "stuck": 0}

    def check(v):
        return CheckResult(ok=(v == "FIXED"), problems=[f"problem-{v}"])   # distinct each attempt -> no cycle

    def repair(v, problems):
        calls["repair"] += 1
        return f"v{calls['repair']}"                                       # always new -> never ok, never cycles

    def stuck_repair(v, problems):
        calls["stuck"] += 1
        return "FIXED"

    result = refine(lambda: "v0", check, repair, max_attempts=3, stuck_repair=stuck_repair)
    assert calls["stuck"] == 1 and result.ok and result.value == "FIXED"


def test_refine_returns_best_attempt_not_last():
    """A repair that makes things worse never wins — refine returns the fewest-problems attempt seen."""
    scores = {0: ["a", "b", "c"], 1: ["a"], 2: ["a", "b"]}   # value -> problems; v=1 is the best (1 problem)

    def check(v):
        return CheckResult(ok=False, problems=scores.get(v, ["x"]))

    result = refine(lambda: 0, check, lambda v, p: v + 1, max_attempts=3)
    assert result.value == 1 and result.problems == ["a"]     # best (attempt 2), not the last (attempt 3, v=2)


def test_refine_rejects_bad_max_attempts():
    with pytest.raises(ValueError):
        refine(lambda: 0, lambda v: CheckResult(ok=True), lambda v, p: v, max_attempts=0)
