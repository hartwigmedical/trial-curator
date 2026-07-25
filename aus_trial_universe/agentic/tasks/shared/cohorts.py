"""Shared trial-cohort (arm) identification — the SINGLE module both the eligibility and the drug-utility paths
use to derive a trial's arms, so the `(trialId, arm)` split (and the `trial_arm_id` derived from it) is IDENTICAL
across them. Neither path re-implements this or imports it from the other.

- CTGov arms are deterministic (from `armGroups`), assembled in the eligibility loaders and passed in.
- ANZCTR arms are LLM-derived here: `anzctr_regimes` runs the intervention/comparator drug doer->reviewer
  (`shared.agents`) and forms an experimental regime (+ a control regime when a real comparator drug is named).

`trial_arm_id` is the deterministic slug of `(trialId, arm)` that the drug and eligibility tables link to.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from aus_trial_universe.agentic.core.client import LlmClient
from aus_trial_universe.agentic.core.workflow import CheckResult, refine
from aus_trial_universe.agentic.tasks.shared.agents import (
    DrugExtraction,
    build_drug_agent,
    build_drug_reviewer_agent,
)

logger = logging.getLogger(__name__)


@dataclass
class Cohort:
    """One cohort/arm of a trial. drug is raw ("; "-joined), not normalized."""

    label: str
    drug: str = ""
    drug_source: str = ""
    description: str = ""
    arm_type: str = ""  # CTGov armGroups[].type: EXPERIMENTAL / ACTIVE_COMPARATOR / PLACEBO_COMPARATOR / ...


# --------------------------------------------------------------------------- #
# trial_arm_id — the deterministic FK derived from the natural key (trialId, arm)
# --------------------------------------------------------------------------- #
_ARM_WS_RE = re.compile(r"\s+")


def trial_arm_id(trial_id: str, arm: str) -> str:
    """Deterministic `trial_arm_id` slug: ``{trialId}::{arm}`` with whitespace in the arm collapsed and any ``::``
    in the arm neutralised (so the separator is unambiguous). Reproducible with no sequence authority — parallel
    workers and re-runs produce the same id. trialId (NCT.../ACTRN...) never contains ``::``, so ``split('::', 1)``
    recovers it. Returns "" if trialId is empty."""
    tid = (trial_id or "").strip()
    if not tid:
        return ""
    a = _ARM_WS_RE.sub(" ", (arm or "").strip()).replace("::", ":")
    return f"{tid}::{a}"


def trial_id_of(trial_arm_id_value: str) -> str:
    """The trialId encoded in a `trial_arm_id` slug (the part before the first ``::``)."""
    return (trial_arm_id_value or "").split("::", 1)[0]


# --------------------------------------------------------------------------- #
# ANZCTR arm derivation (LLM) — intervention/comparator drugs -> regimes
# --------------------------------------------------------------------------- #
# Deterministic backstop to the drug reviewer: an enumerable set of NON-DRUG modalities that must never surface as
# a drug regime, however the LLM phrases it. Matched on the parenthetical-stripped, lowercased, whitespace-collapsed
# FULL name (exact phrase — never a substring, so a real drug name is never clipped).
_NON_DRUG_MODALITIES = frozenset({
    "surgery", "surgical resection", "resection",
    "radiotherapy", "radiation therapy", "radiation", "radiation treatment", "total body irradiation", "tbi",
    "external beam radiotherapy", "stereotactic radiotherapy", "stereotactic body radiotherapy", "brachytherapy",
    "observation", "active surveillance", "watchful waiting", "no treatment", "no active treatment", "no intervention",
    "best supportive care", "supportive care", "placebo",
})
_PAREN_RE = re.compile(r"\([^)]*\)")
_WS_RE = re.compile(r"\s+")


def _is_non_drug_modality(name: str) -> bool:
    """True if `name` is (exactly) one of the enumerable non-drug modalities — paren-stripped, case/space-normalized."""
    norm = _WS_RE.sub(" ", _PAREN_RE.sub(" ", name or "").strip().lower()).strip()
    return norm in _NON_DRUG_MODALITIES


def _drop_non_drug_modalities(names: list[str]) -> list[str]:
    """Filter out clear non-drug modalities from an extracted drug list (order preserved)."""
    return [n for n in names if n and n.strip() and not _is_non_drug_modality(n)]


def extract_anzctr_drugs(client: LlmClient, source_text: str, *, max_attempts: int = 3,
                         use_reviewer: bool = True) -> DrugExtraction:
    """ANZCTR drug identification (doer -> reviewer): intervention + comparator drug names from the text."""
    doer = build_drug_agent(client)
    reviewer = build_drug_reviewer_agent(client) if use_reviewer else None

    def produce(feedback: str = "") -> DrugExtraction:
        return doer(source_text if not feedback
                    else f"{source_text}\n\n[Reviewer feedback — fix these]:\n{feedback}")

    def check(d: DrugExtraction) -> CheckResult:
        if reviewer is not None:
            v = reviewer(f"{source_text}\n\nPROPOSED intervention_drugs={d.intervention_drugs}; "
                         f"comparator_drugs={d.comparator_drugs}")
            if not v.faithful:
                return CheckResult(ok=False, problems=v.problems or ["reviewer flagged the drug extraction"])
        return CheckResult(ok=True)

    result = refine(produce=lambda: produce(""), check=check,
                    repair=lambda d, probs: produce("\n".join(f"- {p}" for p in probs)),
                    max_attempts=max_attempts).value
    return DrugExtraction(
        intervention_drugs=_drop_non_drug_modalities(result.intervention_drugs),
        comparator_drugs=_drop_non_drug_modalities(result.comparator_drugs),
    )


def anzctr_regimes(client: LlmClient, source_text: str) -> list[Cohort]:
    """CANONICAL ANZCTR arm/regime derivation — the SINGLE source of truth shared by BOTH the eligibility and the
    drug-utility paths, so the `(trialId, arm)` split is IDENTICAL across them (the join key must match).

    Deliberately FLAG-INDEPENDENT (`use_reviewer=True` always): a run's `--no-judge` / `--no-review` must NEVER
    change arm identity. Given the same trial text + this fixed invocation, the shared DiskCache then makes the
    two paths' results byte-identical. ANZCTR has one eligibility cohort; the regime axis is an experimental
    regime (INTERVENTIONS) + a control regime (COMPARATOR) when it names a real drug; else a single `all` arm
    (spec §6.1). arm_type flags control arms; a lone arm is EXPERIMENTAL."""
    dr = extract_anzctr_drugs(client, source_text, use_reviewer=True)   # PINNED — arm identity is flag-independent
    main = "; ".join(dict.fromkeys(d.strip() for d in dr.intervention_drugs if d and d.strip()))
    comp = "; ".join(dict.fromkeys(d.strip() for d in dr.comparator_drugs if d and d.strip()))
    regimes: list[Cohort] = []
    if main:
        regimes.append(Cohort(label="intervention", drug=main, drug_source="INTERVENTIONS", arm_type="EXPERIMENTAL"))
    if comp:
        regimes.append(Cohort(label="comparator", drug=comp, drug_source="COMPARATOR", arm_type="ACTIVE_COMPARATOR"))
    return regimes or [Cohort(label="all", drug_source="INTERVENTIONS", arm_type="EXPERIMENTAL")]


def resolve_cohorts(client: LlmClient, source_text: str, cohorts: list[Cohort] | None) -> list[Cohort]:
    """The trial's resolved arm list: CTGov passes its deterministic `cohorts` (from armGroups); ANZCTR passes
    None and we derive via `anzctr_regimes`. A lone treatment arm with no explicit cohorts is EXPERIMENTAL `all`."""
    if cohorts is not None:  # CTGov: deterministic regimes from armGroups (shared loaders._ctgov_cohorts)
        return cohorts or [Cohort("all", arm_type="EXPERIMENTAL")]
    return anzctr_regimes(client, source_text)   # ANZCTR: the shared, flag-independent derivation
