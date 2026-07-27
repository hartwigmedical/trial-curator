"""Tests for the shared doer→reviewer harness (core.review.review_refine).

These pin the GLUE the driver owns on top of core.workflow.refine: the uniform produce(feedback, prior) /
check(candidate, escalate) contract, the '- '-bulleted feedback, and the last-resort ESCALATION step wired only
when escalate=True. (The refine loop's own convergence properties are covered by test_workflow.py.)
"""
from __future__ import annotations

from aus_trial_universe.agentic.core.review import review_refine
from aus_trial_universe.agentic.core.workflow import CheckResult


def test_review_refine_ok_first_try():
    result = review_refine(lambda feedback="", prior=None: "v0",
                           lambda c, escalate=False: CheckResult(ok=True))
    assert result.ok and result.value == "v0" and result.attempts == 1


def test_review_refine_passes_bulleted_feedback_and_prior_on_repair():
    """On a plain repair the driver hands the doer the '- '-bulleted problems AND its own prior answer."""
    seen: list[tuple] = []

    def produce(feedback="", prior=None):
        seen.append((feedback, prior))
        return f"cand{len(seen)}"

    def check(c, escalate=False):
        return CheckResult(ok=(c == "cand2"), problems=[] if c == "cand2" else ["p1", "p2"])

    result = review_refine(produce, check, max_attempts=3)
    assert result.ok and result.value == "cand2"
    # first call: fresh (no feedback, no prior); second call: bulleted feedback + the prior candidate.
    assert seen[0] == ("", None)
    assert seen[1] == ("- p1\n- p2", "cand1")


def test_review_refine_no_escalation_when_escalate_false():
    """escalate=False (drug / arm-ID loops) is a plain critique-only loop: check is NEVER called with escalate=True,
    and a cycling loop stops early on the best attempt (no stuck_repair)."""
    escalated: list[bool] = []

    def check(c, escalate=False):
        escalated.append(escalate)
        return CheckResult(ok=False, problems=["same"])   # identical every attempt -> cycling

    result = review_refine(lambda feedback="", prior=None: "x", check, max_attempts=5, escalate=False)
    assert not result.ok
    assert True not in escalated          # no ESCALATION-MODE re-review ever happened
    assert result.attempts == 2           # cycling detected on attempt 2, stopped early


def test_review_refine_escalation_recheck_supplies_enriched_fix():
    """escalate=True wires the last resort: on cycling, the driver re-checks with escalate=True (so the reviewer can
    add a concrete fix), then feeds that enriched problem set back to the doer once."""
    escalated: list[bool] = []

    def check(c, escalate=False):
        escalated.append(escalate)
        if c == "FIXED":
            return CheckResult(ok=True)
        return CheckResult(ok=False, problems=["FIX: make it FIXED"] if escalate else ["same"])

    def produce(feedback="", prior=None):
        return "FIXED" if "FIX:" in feedback else "loop"

    result = review_refine(produce, check, max_attempts=6, escalate=True)
    assert result.ok and result.value == "FIXED"
    assert True in escalated               # the escalated re-review fired
    assert result.attempts == 3            # attempt1 start, attempt2 cycles, attempt3 = escalated fix
