"""STAGE 1 of OncoTree mapping — TRANSLATE one free-text value into an OncoTree code expression.

The LLM mapper + reviewer, on the shared `core.review.review_refine` doer->reviewer harness. Moved here from
`mapping/workflow.py` on 2026-08-06 so all three OncoTree stages live in this package (user: *"all 3 stages which
are unique to oncotree mapping should stay in that subfolder"*), while genuinely SHARED machinery — the provenance
stripper, the three-column concurrent pool, the doer/reviewer logger — stays in `workflow.py`, where
gene_alteration and molecular_signature use it too.

STAGE 1'S JOB, and its boundaries (spec agreed 2026-08-05/06):
  · unit of work : ONE distinct interpreted value, considered alone
  · sees         : the value + the OncoTree vocabulary. NOT the trial — a consequence of the map being keyed on the
                   value alone, and deliberate: the interpreted cell is already extraction's distillation of the
                   trial, so context has been applied upstream.
  · job          : the most faithful expression of that value, KEEPING the source's own logical shape, including an
                   exclusion written as a nested carve-out. Boolean algebra belongs to stage 2.
  · gate         : every operand a real node, well-formed, not self-contradictory — plus the SOURCE-AWARE checks
                   (`source_problems`), which until 2026-08-05 ran only in `qa/gates.py`, i.e. after the value had
                   already shipped, so the sentinel-broadening class could be reported but never repaired.
  · may NOT      : consult another value, normalise for comparability, or simplify anything.

`syn_nested_not` is deliberately NON-blocking here: "X other than Y" is faithfully written as a nested NOT and
stage 2 flattens it. Same split as vacuous exclusions — the mapper translates, stage 2 reduces.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from aus_trial_universe.core.client import LlmClient
from aus_trial_universe.core.review import review_refine
from aus_trial_universe.core.workflow import CheckResult, fan_out
from aus_trial_universe.tasks.eligibility.mapping.cancer_type.agents import (
    build_oncotree_mapper,
    build_oncotree_reviewer,
)
from aus_trial_universe.tasks.eligibility.mapping.cancer_type.vocab import (
    expression_problems,
    render_name_expression,
    source_problems,
)
from aus_trial_universe.tasks.eligibility.mapping.schema import OncotreeMapping, ReviewVerdict

logger = logging.getLogger(__name__)

#: The escalation suffix for the oncotree REPAIR reviewer. Kept beside the stage it belongs to; `workflow.py` has
#: its own copy for the gene/signature columns, and the two are pinned separately because each is hashed into its
#: agent's cache key — changing one must not re-roll the other's approved answers.
_ESCALATION = ("\n\n[ESCALATION-MODE] Earlier attempts did not resolve the problems. In ADDITION to `problems`, "
               "fill `suggested_fix` with the concrete corrected mapping you would expect (the exact value).")

@dataclass
class OncotreeResult:
    source: str
    oncotree_name: str
    oncotree_code: str
    faithful: bool
    attempts: int
    problems: list[str]


def map_oncotree(
    client: LlmClient, source_expr: str, *, max_attempts: int = 3, use_reviewer: bool = True
) -> OncotreeResult:
    """Map ONE cancer-type expression to OncoTree name+code (mapper -> validate+review -> refine)."""
    mapper = build_oncotree_mapper(client)
    reviewer = build_oncotree_reviewer(client) if use_reviewer else None

    def produce(feedback: str = "", prior: OncotreeMapping | None = None) -> OncotreeMapping:
        if not feedback:
            return mapper(source_expr)
        # Byte-identical to the form the approved 2026-08-04 run used — this string is hashed into the
        # response-cache key, so any drift orphans every cached refine step behind that output.
        prior_txt = f"\n\n[Your previous mapping]:\noncotree_code: {prior.oncotree_code}" if prior else ""
        return mapper(f"{source_expr}{prior_txt}\n\n[Reviewer feedback — fix ONLY these]:\n{feedback}")

    def check(m: OncotreeMapping, escalate: bool = False) -> CheckResult:
        # `syn_nested_not` is NOT blocking here: "X other than Y" is faithfully written as a nested NOT and the
        # refinement flattens it deterministically (mapper translates, refinement reduces — the same split as
        # vacuous exclusions). It stays an error on the FINAL value, which is what ships.
        #
        # `source_problems` joined this gate on 2026-08-05. It had only ever run in `qa/gates.py`, i.e. AFTER the
        # value shipped — so the one check aimed at the sentinel-broadening class (`log_unjustified_sentinel`, the
        # majority of the hand-rulings in `qa/adjudications/cancer_type.py`) could report the defect but never
        # provoke a repair. The mapper holds the source, so this is the earliest point it is decidable. Note this
        # does NOT re-fingerprint the agent: a check is not part of the cache key, so first attempts stay cache
        # hits and only a value that now FAILS costs another call. Measured over the 4,978 live values at the time
        # of wiring: 0 fail, so it is inert on the corpus and preventive from here on.
        errors = [str(p) for p in expression_problems(m.oncotree_code) + source_problems(source_expr, m.oncotree_code)
                  if p.severity == "error" and p.defect != "syn_nested_not"]
        if errors:
            return CheckResult(ok=False, problems=errors)
        if reviewer is not None:
            v: ReviewVerdict = reviewer(_review_input(source_expr, m) + (_ESCALATION if escalate else ""))
            if not v.faithful:
                probs = v.problems or ["reviewer flagged the mapping"]
                if escalate and (v.suggested_fix or "").strip():
                    probs = probs + [f"SUGGESTED FIX: {v.suggested_fix.strip()}"]
                return CheckResult(ok=False, problems=probs)
        return CheckResult(ok=True)

    result = review_refine(produce, check, max_attempts=max_attempts, escalate=use_reviewer)
    m = result.value
    return OncotreeResult(
        source=source_expr,
        oncotree_name=render_name_expression(m.oncotree_code.strip()),   # DERIVED — the name/code invariant
        oncotree_code=m.oncotree_code.strip(),
        faithful=result.ok,
        attempts=result.attempts,
        problems=result.problems,
    )


def _review_input(source_expr: str, m: OncotreeMapping) -> str:
    return (
        f"SOURCE cancer-type expression:\n{source_expr}\n\n"
        f"PROPOSED oncotree_code: {m.oncotree_code}"
    )


def map_cancer_types(
    client: LlmClient, cancer_cells: list[str], *, max_attempts: int = 3, use_reviewer: bool = True,
    workers: int = 8,
) -> dict[str, OncotreeResult]:
    """Map the DISTINCT (provenance-stripped) cancer-type values, concurrently.

    Returns {stripped_value -> OncotreeResult}. Empty values are skipped. `workers` sets the fan-out width (the
    client's global --max-concurrency semaphore is the true API ceiling); raise it for a large map-only build.
    """
    # deferred import: `workflow` re-exports this module, so a module-level import would be circular. Both
    # helpers are genuinely SHARED with gene_alteration/molecular_signature and belong there, not here.
    from aus_trial_universe.tasks.eligibility.mapping.workflow import _log_doer_reviewer, strip_provenance
    distinct = list(dict.fromkeys(strip_provenance(c) for c in cancer_cells if strip_provenance(c)))
    if not distinct:
        return {}
    logger.info("")
    logger.info("oncotree · %d value(s)", len(distinct))
    results = fan_out(
        [(lambda v=v: map_oncotree(client, v, max_attempts=max_attempts, use_reviewer=use_reviewer)) for v in distinct],
        max_workers=workers,
    )
    out = {v: r for v, r in zip(distinct, results)}
    _log_doer_reviewer(distinct, results, use_reviewer=use_reviewer, render=lambda r: r.oncotree_code or "?")
    return out
