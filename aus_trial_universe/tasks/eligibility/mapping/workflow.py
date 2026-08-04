"""Mapping-task workflow (spec §6.2).

OncoTree procedure: for each distinct cancer-type cell, run mapper -> (validate codes +
reviewer) -> bounded refine, then attach oncotree_name / oncotree_code back onto every row.
Plain-Python orchestration; the LLM only fills the mapper/reviewer steps.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from aus_trial_universe.core.client import LlmClient
from aus_trial_universe.core.logfmt import FAIL, PASS, bullet, line
from aus_trial_universe.core.review import review_refine
from aus_trial_universe.core.workflow import CheckResult, fan_out

# Appended to the reviewer input on the last-resort ESCALATION step (see core.review.review_refine): asks the
# reviewer to ALSO return a concrete `suggested_fix`, which the mapper gets for one final repair. Mechanism only —
# the reviewer PROMPTS that act on it are finalised separately.
_ESCALATION = ("\n\n[ESCALATION-MODE] Earlier attempts did not resolve the problems. In ADDITION to `problems`, "
               "fill `suggested_fix` with the concrete corrected mapping you would expect (the exact value).")
from aus_trial_universe.tasks.eligibility.mapping.cancer_type.agents import (
    build_oncotree_mapper,
    build_oncotree_reviewer,
)
from aus_trial_universe.tasks.eligibility.mapping.gene_alteration.agents import (
    build_gene_alteration_mapper,
    build_gene_alteration_reviewer,
)
from aus_trial_universe.tasks.eligibility.mapping.molecular_signature.agents import (
    build_molecular_signature_mapper,
    build_molecular_signature_reviewer,
)
from aus_trial_universe.tasks.eligibility.mapping.schema import (
    FindingModelMapping,
    OncotreeMapping,
    ReviewVerdict,
)
from aus_trial_universe.tasks.eligibility.mapping.finding_model import finding_model_problems
from aus_trial_universe.tasks.eligibility.mapping.cancer_type.vocab import (
    expression_problems,
    is_subcode,
    render_name_expression,
)

logger = logging.getLogger(__name__)

_PROVENANCE_RE = re.compile(r"\s*\[[^\]]*\]\s*$")


def strip_provenance(cell: str) -> str:
    """'metastatic NSCLC [TITLE; ELIGIBILITY CRITERIA]' -> 'metastatic NSCLC'."""
    return _PROVENANCE_RE.sub("", cell or "").strip()


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
        errors = [str(p) for p in expression_problems(m.oncotree_code)
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


def _log_doer_reviewer(distinct, results, *, use_reviewer, render) -> None:
    """Log a mapping procedure as doer (value → result) then reviewer (verdict per value)."""
    logger.info("")
    logger.info(line("doer"))
    for v, r in zip(distinct, results):
        logger.info(line(f"{v}  →  {render(r)}", indent=8))
    logger.info("")
    logger.info(line("reviewer" if use_reviewer else "reviewer · skipped (--no-review); validator only"))
    for v, r in zip(distinct, results):
        logger.info(line(f"{PASS if r.faithful else FAIL}  {v}", indent=8))
        if not r.faithful and r.problems:
            logger.info(bullet(r.problems[0], indent=12))


_CODE_TOKEN_RE = re.compile(r"[A-Z][A-Z0-9_]+")
_NOT_BODY_RE = re.compile(r"NOT\(([^)]*)\)")
_KW = {"AND", "OR", "NOT"}
_SENTINEL_NAMES = ("Pan-cancer", "Solid tumour", "Haematological malignancy")


def _top_level_or(expr: str) -> list[str]:   # still used by qa/validate_output.py
    """Split on ' OR ' at paren-depth 0 only, so an OR inside a NOT(...) carve-out (e.g. NOT(A OR B)) stays intact
    — otherwise a valid exclusion like 'Solid tumour AND NOT(NSCLC OR THYROID)' is split mid-NOT() and misread as a
    positive broad-ANDed-subtype."""
    parts: list[str] = []
    depth = start = i = 0
    while i < len(expr):
        c = expr[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif depth == 0 and expr[i:i + 4] == " OR ":
            parts.append(expr[start:i])
            i += 4
            start = i
            continue
        i += 1
    parts.append(expr[start:])
    return [p for p in parts if p.strip()]


def _top_level_has(expr: str, op: str) -> bool:
    """True if ``op`` (``OR`` / ``AND``) appears at paren-depth 0 — i.e. outside any NOT(...) or ( ) group."""
    token = f" {op} "
    depth = i = 0
    while i < len(expr):
        c = expr[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif depth == 0 and expr[i:i + len(token)] == token:
            return True
        i += 1
    return False


# --------------------------------------------------------------------------- #
# gene_alteration / molecular_signature -> finding-model syntax
# --------------------------------------------------------------------------- #
@dataclass
class FindingModelResult:
    source: str
    finding_model: str
    faithful: bool
    attempts: int
    problems: list[str]


#: The gene_alteration column keeps its OWN escalation suffix. It is not a style choice: the 909 approved
#: gene mappings (signed off 2026-08-04) were produced with this exact wording, and the reviewer input is hashed
#: into the response-cache key — so changing it to `_ESCALATION` would miss the cache on every escalated value and
#: silently re-roll answers that have already been reviewed. Same class of trap as pinning `--max-attempts`.
_GENE_ESCALATION = ("\n\n[ESCALATION-MODE] The writer is stuck. Also return `suggested_fix`: the exact expression you "
                    "would expect.")


def _map_finding_model(
    client: LlmClient, source_expr: str, build_mapper, build_reviewer, *, max_attempts: int, use_reviewer: bool,
    escalation: str = _ESCALATION,
) -> FindingModelResult:
    """Generic mapper -> validate(syntax) + reviewer -> refine for one gene/signature expression."""
    mapper = build_mapper(client)
    reviewer = build_reviewer(client) if use_reviewer else None

    def produce(feedback: str = "", prior: FindingModelMapping | None = None) -> FindingModelMapping:
        if not feedback:
            return mapper(source_expr)
        prior_txt = f"\n\n[Your previous mapping]:\nfinding_model: {prior.finding_model}" if prior is not None else ""
        return mapper(f"{source_expr}{prior_txt}\n\n[Reviewer feedback — fix ONLY these]:\n{feedback}")

    def check(m: FindingModelMapping, escalate: bool = False) -> CheckResult:
        probs = finding_model_problems(m.finding_model)
        if probs:
            return CheckResult(ok=False, problems=[f"invalid syntax: {p}" for p in probs])
        if reviewer is not None:
            v: ReviewVerdict = reviewer(f"SOURCE: {source_expr}\n\nPROPOSED finding_model: {m.finding_model}"
                                        + (escalation if escalate else ""))
            if not v.faithful:
                gate = v.problems or ["reviewer flagged the conversion"]
                if escalate and (v.suggested_fix or "").strip():
                    gate = gate + [f"SUGGESTED FIX: {v.suggested_fix.strip()}"]
                return CheckResult(ok=False, problems=gate)
        return CheckResult(ok=True)

    result = review_refine(produce, check, max_attempts=max_attempts, escalate=use_reviewer)
    return FindingModelResult(
        source=source_expr,
        finding_model=result.value.finding_model.strip(),
        faithful=result.ok,
        attempts=result.attempts,
        problems=result.problems,
    )


def _map_column(client, cells, build_mapper, build_reviewer, label, *, max_attempts, use_reviewer, workers=8,
                escalation=_ESCALATION):
    distinct = list(dict.fromkeys(strip_provenance(c) for c in cells if strip_provenance(c)))
    if not distinct:
        return {}
    logger.info("")
    logger.info("%s · %d value(s)", label, len(distinct))
    results = fan_out([
        (lambda v=v: _map_finding_model(client, v, build_mapper, build_reviewer,
                                        max_attempts=max_attempts, use_reviewer=use_reviewer,
                                        escalation=escalation))
        for v in distinct
    ], max_workers=workers)
    out = {v: r for v, r in zip(distinct, results)}
    _log_doer_reviewer(distinct, results, use_reviewer=use_reviewer, render=lambda r: r.finding_model or "?")
    return out


def map_gene_alterations(client: LlmClient, cells: list[str], *, max_attempts: int = 3, use_reviewer: bool = True,
                         workers: int = 8) -> dict[str, FindingModelResult]:
    return _map_column(client, cells, build_gene_alteration_mapper, build_gene_alteration_reviewer,
                       "gene→fm", max_attempts=max_attempts, use_reviewer=use_reviewer, workers=workers,
                       escalation=_GENE_ESCALATION)


def map_molecular_signatures(client: LlmClient, cells: list[str], *, max_attempts: int = 3, use_reviewer: bool = True,
                             workers: int = 8) -> dict[str, FindingModelResult]:
    return _map_column(client, cells, build_molecular_signature_mapper, build_molecular_signature_reviewer,
                       "sig→fm", max_attempts=max_attempts, use_reviewer=use_reviewer, workers=workers)


def map_all_columns(
    client: LlmClient, cancer_cells: list[str], gene_cells: list[str], signature_cells: list[str], *,
    max_attempts: int = 6, use_reviewer: bool = True, workers: int = 8,
) -> tuple[dict[str, OncotreeResult], dict[str, FindingModelResult], dict[str, FindingModelResult]]:
    """Map the DISTINCT values of ALL THREE columns in ONE concurrent pool (fastest for a full-store build: the
    client's global --max-concurrency is the only throttle, with no idle gap between columns). Dedups each column
    to its distinct provenance-stripped values, runs every value's mapper->reviewer->refine through a single
    ``fan_out(max_workers=workers)``, and returns the three ``{stripped_value -> Result}`` dicts (same shape as the
    per-column ``map_*`` functions). Empty values are skipped."""
    d_ct = list(dict.fromkeys(strip_provenance(c) for c in cancer_cells if strip_provenance(c)))
    d_ga = list(dict.fromkeys(strip_provenance(c) for c in gene_cells if strip_provenance(c)))
    d_sig = list(dict.fromkeys(strip_provenance(c) for c in signature_cells if strip_provenance(c)))
    logger.info("")
    logger.info("map-all · single pool · oncotree %d · gene %d · signature %d value(s) · %d workers",
                len(d_ct), len(d_ga), len(d_sig), workers)

    def _ct(v):
        return lambda: map_oncotree(client, v, max_attempts=max_attempts, use_reviewer=use_reviewer)

    def _fm(v, build_mapper, build_reviewer, escalation=_ESCALATION):
        return lambda: _map_finding_model(client, v, build_mapper, build_reviewer,
                                          max_attempts=max_attempts, use_reviewer=use_reviewer,
                                          escalation=escalation)

    tagged: list[tuple[str, str]] = [("ct", v) for v in d_ct] + [("ga", v) for v in d_ga] + [("sig", v) for v in d_sig]
    thunks = (
        [_ct(v) for v in d_ct]
        + [_fm(v, build_gene_alteration_mapper, build_gene_alteration_reviewer, _GENE_ESCALATION)
           for v in d_ga]
        + [_fm(v, build_molecular_signature_mapper, build_molecular_signature_reviewer) for v in d_sig]
    )
    results = fan_out(thunks, max_workers=workers) if thunks else []
    ct: dict[str, OncotreeResult] = {}
    ga: dict[str, FindingModelResult] = {}
    sig: dict[str, FindingModelResult] = {}
    for (tag, v), r in zip(tagged, results):
        (ct if tag == "ct" else ga if tag == "ga" else sig)[v] = r
    logger.info("map-all · done · oncotree %d · gene %d · signature %d mapped",
                len(ct), len(ga), len(sig))
    return ct, ga, sig
