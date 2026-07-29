"""Why an arm produced NO interpreted eligibility — the empty-output verdict (`arm_scope` table).

An arm with an empty DNF contributes no export row, so a trial can be fully curated and still be absent from the
deliverable. "Correctly out of scope" and "extraction MISSED it" look identical from the outside — a distinction
that has to be established by reading the source record. Unattended, nobody does that, so a genuine miss would
just nudge the empty count up by one and pass unnoticed.

This module records a REASON for every empty arm so a gate can assert `unexplained == 0` and fail the run the
moment an unexplainable empty appears. Two tiers, cheapest first:

  1. DETERMINISTIC rules — free, reproducible, no LLM. CTGov publishes `eligibilityModule.healthyVolunteers` as a
     structured boolean; an arm with no eligibility text at all is self-explaining; ANZCTR's inclusion criteria
     carry explicit healthy-volunteer language often enough to be worth matching.
  2. LLM fallback — only for arms no rule explains (a handful per run), judging from the source record: is this
     legitimately out of scope, or did extraction miss real cancer-patient eligibility? A single cheap call, no
     reviewer (the verdict is advisory metadata, not curated output; a wrong `unexplained` fails LOUD, which is
     the safe direction).

Idempotent and lookup-first: an arm that already has a verdict is skipped, so this both back-fills the existing
store and handles new arms on every refresh.
"""
from __future__ import annotations

import logging
import re
from typing import Iterable

from pydantic import BaseModel, Field

from aus_trial_universe.core.agent import Agent
from aus_trial_universe.core.client import LlmClient
from aus_trial_universe.core.workflow import run_parallel
from aus_trial_universe.tasks.eligibility.schema import (
    IN_SCOPE_VERDICTS,
    SCOPE_HEALTHY_VOLUNTEERS,
    SCOPE_NO_ELIGIBILITY_TEXT,
    SCOPE_NOT_CANCER_SELECTIVE,
    SCOPE_NOT_ONCOLOGY,
    SCOPE_UNEXPLAINED,
    SCOPE_VERDICTS,
    ArmScope,
)
from aus_trial_universe.tasks.shared.cohorts import trial_id_of

logger = logging.getLogger(__name__)


# --- tier 1: deterministic ---------------------------------------------------- #
# Healthy-volunteer language in a trial's own inclusion criteria. Deliberately tight: these phrases describe the
# ENROLLED population, so a false positive would mask a real miss. "healthy" alone is far too loose (it appears in
# "healthy diet", "otherwise healthy tissue"), hence the anchored forms.
_HEALTHY_PATTERNS = (
    r"\bhealthy (?:adult|male|female|volunteer|subject|participant|men|women)\w*",
    r"\b(?:adult|male|female|men|women)\s+healthy volunteers?\b",
    r"\bmedically healthy\b",
    r"\bhealthy,? as (?:judged|determined|assessed)\b",
    r"\bovertly healthy\b",
    r"\bin good general health\b",
)
_HEALTHY_RE = re.compile("|".join(_HEALTHY_PATTERNS), re.I)


def healthy_volunteer_text(text: str) -> str | None:
    """The matched healthy-volunteer phrase in `text`, or None. Kept public so the rule is testable on its own."""
    m = _HEALTHY_RE.search(text or "")
    return m.group(0).strip() if m else None


def deterministic_verdict(*, source_text: str, ctgov_healthy_volunteers: bool | None) -> ArmScope | None:
    """A verdict from rules alone, or None when the arm needs the LLM. `ctgov_healthy_volunteers` is CTGov's
    structured `eligibilityModule.healthyVolunteers` (None for ANZCTR, which has no such field).

    Only HEALTHY-VOLUNTEER signals qualify as deterministic, because only they are evidence about the enrolled
    population — i.e. actual legitimacy. Note what is deliberately NOT a rule here: "the arm's raw row is empty".
    That says the extractor found nothing, which restates the problem rather than explaining it, and excusing it
    would silently absolve exactly the failure this table exists to catch (a raw stage that missed real cancer
    eligibility). Those arms go to the LLM, which judges legitimacy from the SOURCE record instead.
    """
    if ctgov_healthy_volunteers is True:
        return ArmScope(scope_verdict=SCOPE_HEALTHY_VOLUNTEERS, source="deterministic",
                        scope_reason="CTGov eligibilityModule.healthyVolunteers = true")
    phrase = healthy_volunteer_text(source_text)
    if phrase:
        return ArmScope(scope_verdict=SCOPE_HEALTHY_VOLUNTEERS, source="deterministic",
                        scope_reason=f"inclusion criteria enrol healthy volunteers: “{phrase[:80]}”")
    return None


# --- tier 2: the LLM fallback ------------------------------------------------- #
class ScopeVerdict(BaseModel):
    """Why this arm has no cancer-patient eligibility — or that it SHOULD have had some (a miss)."""

    verdict: str = Field(description=(
        "Exactly one of: 'healthy_volunteers' (the trial enrols healthy people, e.g. a Phase-1 PK / "
        "bioavailability / first-in-human study of an oncology drug), 'not_oncology' (not a cancer trial at all), "
        "'population_not_cancer_selective' (supportive care, symptom control, risk reduction, or a procedure "
        "study whose population is NOT restricted to cancer patients — e.g. 'undergoing surgery, benign or "
        "malignant'), 'no_eligibility_text' (the source record genuinely carries no eligibility criteria to "
        "extract), or 'unexplained' (the source DOES state cancer-patient eligibility that should have been "
        "extracted — i.e. this is an extraction MISS)."))
    reason: str = Field(description="One sentence, citing the specific source wording that decides it.")


SCOPE_CLASSIFIER_INSTRUCTIONS = """\
You are auditing an oncology trial-curation pipeline. For one trial ARM, the pipeline extracted NO cancer-patient
eligibility criteria. Decide whether that is CORRECT (the trial legitimately has none) or a MISS.

The trial is in an oncology universe because its DRUG or a listed CONDITION matched — that alone does NOT mean it
enrols cancer patients. Common legitimate reasons for no eligibility:
  - healthy_volunteers: a Phase-1 PK, bioequivalence/bioavailability, or first-in-human study in healthy people.
    The listed condition is the drug's eventual target indication, not who is enrolled.
  - not_oncology: not a cancer trial at all (the cancer link is incidental, e.g. a secondary endpoint).
  - population_not_cancer_selective: supportive care, symptom control, screening, risk reduction, or a procedure
    study whose eligibility is NOT restricted to cancer patients. Wording like "malignant or non-malignant",
    "benign or malignant", or "undergoing <procedure>" is the tell.
  - no_eligibility_text: the record genuinely carries no eligibility criteria at all.

Answer 'unexplained' ONLY if the source really does state cancer-patient eligibility (a required diagnosis, stage,
histology, biomarker, or prior-therapy requirement that selects cancer patients) which should have been captured.
Do not guess 'unexplained' out of caution — but do not excuse a real miss either. Judge only the text given.

You are told nothing about what the pipeline produced, deliberately: the fact that it extracted nothing is NOT
evidence that there was nothing to extract. Decide from the source record alone.
"""


def build_scope_classifier(client: LlmClient, *, model: str | None = None) -> Agent[ScopeVerdict]:
    return Agent(name="eligibility_scope_classifier", instructions=SCOPE_CLASSIFIER_INSTRUCTIONS,
                 output_schema=ScopeVerdict, client=client, model=model)


def _llm_verdict(agent: Agent[ScopeVerdict], arm_id: str, source_text: str) -> ArmScope:
    payload = (f"TRIAL: {trial_id_of(arm_id)}\nARM: {arm_id.split('::', 1)[-1]}\n\n"
               f"SOURCE RECORD:\n{source_text[:12000]}")
    v = agent(payload)                        # __call__ -> the validated object (run() would return LlmResult)
    return ArmScope(trial_arm_id=arm_id, scope_verdict=(v.verdict or "").strip(),
                    scope_reason=(v.reason or "").strip(), source="llm")


# --- the pass ----------------------------------------------------------------- #
def classify_empty_arms(
    client: LlmClient | None,
    store,
    arm_ids: set[str],
    *,
    source_text_for: dict[str, str] | None = None,
    ctgov_healthy: dict[str, bool] | None = None,
    workers: int = 8,
    checkpoint=None,
) -> dict[str, int]:
    """Give every empty arm in `arm_ids` a scope verdict, writing into `store.scope` (mutated in place).

    Arms that already carry a verdict are skipped (idempotent / lookup-first). `client=None` runs tier 1 only —
    anything a rule cannot explain is left `unexplained`, which the gate then fails on. Returns per-verdict counts.
    """
    source_text_for = source_text_for or {}
    ctgov_healthy = ctgov_healthy or {}
    all_empty = store.empty_arms(arm_ids)
    # Housekeeping: the table is defined as "only empty arms carry a row", so a verdict for an arm that has since
    # been repaired (or expired) is dropped. Keeps the table meaningful instead of accumulating history.
    for stale in [a for a in store.scope if a not in set(all_empty)]:
        store.scope.pop(stale, None)
    empty = [a for a in all_empty if a not in store.scope]
    counts: dict[str, int] = {}

    def _record(scope: ArmScope) -> None:
        # The single enforcement point for the enum: ANY verdict that is not a recognised in-scope reason becomes
        # `unexplained`, whatever produced it. The gate's guarantee rests on this — an off-enum or blank verdict
        # must fail loud, never slip through as "explained". A deliberate `unexplained` passes through unannotated
        # (it is a legitimate verdict — the miss signal itself); only a value outside the enum gets flagged.
        if scope.scope_verdict not in IN_SCOPE_VERDICTS:
            if scope.scope_verdict not in SCOPE_VERDICTS:
                scope.scope_reason = (f"unrecognised verdict {scope.scope_verdict!r}: {scope.scope_reason}"
                                      if scope.scope_verdict else scope.scope_reason)
            scope.scope_verdict = SCOPE_UNEXPLAINED
        store.put_scope(scope)
        counts[scope.scope_verdict] = counts.get(scope.scope_verdict, 0) + 1

    pending: list[str] = []
    for arm_id in empty:
        tid = trial_id_of(arm_id)
        text = source_text_for.get(tid, "")
        verdict = deterministic_verdict(source_text=text, ctgov_healthy_volunteers=ctgov_healthy.get(tid))
        if verdict is not None:
            verdict.trial_arm_id = arm_id
            _record(verdict)
        else:
            pending.append(arm_id)

    if pending and client is not None:
        agent = build_scope_classifier(client)
        logger.info("    %d arm(s) need an LLM scope verdict · workers=%d", len(pending), workers)

        def _on_result(arm_id: str, res: ArmScope | None, exc: Exception | None) -> None:
            if exc is not None or res is None:
                logger.warning("    scope verdict FAILED for %s (%s) — left unexplained", arm_id, exc)
                _record(ArmScope(trial_arm_id=arm_id, scope_verdict=SCOPE_UNEXPLAINED, source="llm",
                                 scope_reason=f"classifier error: {exc}"))
            else:
                _record(res)
            if checkpoint:
                checkpoint()

        run_parallel(pending, lambda a: _llm_verdict(agent, a, source_text_for.get(trial_id_of(a), "")),
                     _on_result, max_workers=workers)
    elif pending:
        for arm_id in pending:
            _record(ArmScope(trial_arm_id=arm_id, scope_verdict=SCOPE_UNEXPLAINED, source="deterministic",
                             scope_reason="no deterministic rule applied and no LLM client was supplied"))

    if counts:
        logger.info("    scope verdicts · %s", " · ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return counts


def unexplained_arms(store, empty: Iterable[str]) -> list[str]:
    """Of the arms that are CURRENTLY empty, those whose verdict is missing or `unexplained` — the gate's failure set.

    Restricted to `empty` deliberately: once an arm is fixed and has conjunctions again, its old verdict is history,
    not a live failure. Without this the gate would keep failing on repaired arms forever, which is how a gate
    earns its way into being ignored."""
    still_empty = set(empty)
    return sorted(a for a in still_empty
                  if a not in store.scope or store.scope[a].scope_verdict not in IN_SCOPE_VERDICTS)
