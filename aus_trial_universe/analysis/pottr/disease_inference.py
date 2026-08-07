"""COMPONENT 2 — recover the genetics that a trial's DISEASE wording entails.

A pure function of ONE `cancer_type_interpreted` value, run doer -> reviewer -> bounded refine on the shared
`core.review.review_refine` harness, exactly like every other stage. Two outputs per value:

    cancer_type  ->  derived_alteration (free text)  ->  finding_model

The second arrow reuses the PRODUCTION gene mapper unchanged, so the derived column lands in the same grammar as
`gene_alteration_findingmodel` and is directly comparable to it — and inherits the finding-model grammar
validator for free. It writes to its OWN table in the analysis workspace and never touches
`gene_alteration_map_*`, so no production table, gate or invariant changes. That is the "must not impact existing
functionality" requirement, met structurally rather than by care.

Keying on the INTERPRETED text is load-bearing, not incidental: OncoTree destroys the very content this stage
reads ("VHL disease associated tumors" -> `Solid tumour`, "wild-type GIST" -> `GIST`).
"""
from __future__ import annotations

import csv
import logging
from pathlib import Path

from aus_trial_universe.analysis.pottr.agents import (
    build_disease_inference_agent,
    build_disease_inference_reviewer,
)
from aus_trial_universe.analysis.pottr.schema import DiseaseDerivedAlteration, DiseaseDerivedRow
from aus_trial_universe.core.client import LlmClient
from aus_trial_universe.core.logfmt import FAIL, PASS, bullet, line
from aus_trial_universe.core.review import review_refine
from aus_trial_universe.core.workflow import CheckResult, fan_out
from aus_trial_universe.tasks.eligibility.mapping.gene_alteration.agents import (
    build_gene_alteration_mapper,
    build_gene_alteration_reviewer,
)
from aus_trial_universe.tasks.eligibility.mapping.schema import ReviewVerdict
from aus_trial_universe.tasks.eligibility.mapping.workflow import (
    _GENE_ESCALATION,
    _map_finding_model,
    strip_provenance,
)

logger = logging.getLogger(__name__)

_ESCALATION = ("\n\n[ESCALATION-MODE] Earlier attempts did not resolve the problems. In ADDITION to `problems`, "
               "fill `suggested_fix` with the concrete answer you would expect — and remember that \"\" is a "
               "legitimate answer, so say so plainly if the correct outcome is no inference at all.")

#: Finding-model tokens must never appear in the free-text answer — that conversion is the NEXT stage's job, and a
#: doer that jumps ahead produces a value the gene mapper then has to un-parse.
_SYNTAX_LEAKS = ("SmallVariant[", "GainDeletion[", "Fusion[", "Wildtype[", "Disruption[", "Arm[", "HlaAllele[")


def _local_problems(m: DiseaseDerivedAlteration) -> list[str]:
    """Deterministic checks — cheap, certain, and they never need the reviewer's opinion."""
    alt = (m.derived_alteration or "").strip()
    problems: list[str] = []
    if not alt:
        # An empty answer is the expected outcome; a stray basis beside it is just noise, not a fault worth a retry.
        return problems
    if not (m.basis or "").strip():
        problems.append("a non-empty derived_alteration must state its `basis` — name the diagnostic criterion or "
                        "the qualifier that makes the alteration definitional")
    if leak := [t for t in _SYNTAX_LEAKS if t in alt]:
        problems.append(f"derived_alteration must be plain clinical English, not finding-model syntax (found "
                        f"{', '.join(leak)}); a later stage does that conversion")
    return problems


def infer_one(client: LlmClient, cancer_type: str, *, max_attempts: int = 3,
              use_reviewer: bool = True) -> DiseaseDerivedRow:
    """Doer -> (deterministic checks + reviewer) -> refine, for ONE cancer-type value."""
    doer = build_disease_inference_agent(client)
    reviewer = build_disease_inference_reviewer(client) if use_reviewer else None

    def produce(feedback: str = "", prior: DiseaseDerivedAlteration | None = None) -> DiseaseDerivedAlteration:
        if not feedback:
            return doer(cancer_type)
        prior_txt = ""
        if prior is not None:
            prior_txt = (f"\n\n[Your previous answer]:\nderived_alteration: {prior.derived_alteration!r}"
                         f"\nbasis: {prior.basis!r}")
        return doer(f"{cancer_type}{prior_txt}\n\n[Reviewer feedback — fix ONLY these]:\n{feedback}")

    def check(m: DiseaseDerivedAlteration, escalate: bool = False) -> CheckResult:
        if probs := _local_problems(m):
            return CheckResult(ok=False, problems=probs)
        if reviewer is not None:
            v: ReviewVerdict = reviewer(
                f"CANCER TYPE: {cancer_type}\n\nPROPOSED derived_alteration: {m.derived_alteration!r}"
                f"\nPROPOSED basis: {m.basis!r}" + (_ESCALATION if escalate else "")
            )
            if not v.faithful:
                gate = v.problems or ["reviewer flagged the inference"]
                if escalate and (v.suggested_fix or "").strip():
                    gate = gate + [f"SUGGESTED FIX: {v.suggested_fix.strip()}"]
                return CheckResult(ok=False, problems=gate)
        return CheckResult(ok=True)

    result = review_refine(produce, check, max_attempts=max_attempts, escalate=use_reviewer)
    return DiseaseDerivedRow(
        cancer_type=cancer_type,
        derived_alteration=(result.value.derived_alteration or "").strip(),
        basis=(result.value.basis or "").strip(),
        faithful=result.ok,
        attempts=result.attempts,
        problems=" | ".join(result.problems),
    )


def infer_all(client: LlmClient, cancer_types: list[str], *, max_attempts: int = 3, use_reviewer: bool = True,
              workers: int = 8) -> list[DiseaseDerivedRow]:
    """Stage A — infer over the DISTINCT cancer-type values, one concurrent pool."""
    distinct = list(dict.fromkeys(strip_provenance(c) for c in cancer_types if strip_provenance(c)))
    if not distinct:
        return []
    logger.info("")
    logger.info("disease→alteration · %d distinct value(s) · %d workers", len(distinct), workers)
    rows: list[DiseaseDerivedRow] = fan_out(
        [(lambda v=v: infer_one(client, v, max_attempts=max_attempts, use_reviewer=use_reviewer)) for v in distinct],
        max_workers=workers,
    )
    hits = [r for r in rows if r.derived_alteration]
    logger.info("disease→alteration · done · %d/%d fired (%.1f%%)", len(hits), len(rows),
                100.0 * len(hits) / len(rows))
    logger.info("")
    logger.info(line("inferences that FIRED"))
    for r in sorted(hits, key=lambda r: r.cancer_type):
        logger.info(line(f"{PASS if r.faithful else FAIL}  {r.cancer_type}  →  {r.derived_alteration}", indent=8))
        logger.info(bullet(r.basis, indent=12))
    return rows


def map_derived_to_finding_model(client: LlmClient, rows: list[DiseaseDerivedRow], *, max_attempts: int = 3,
                                 use_reviewer: bool = True, workers: int = 8) -> list[DiseaseDerivedRow]:
    """Stage B — put every non-empty derived alteration through the PRODUCTION gene mapper (mutates in place)."""
    hits = [r for r in rows if r.derived_alteration]
    if not hits:
        return rows
    logger.info("")
    logger.info("derived→fm · %d value(s) · production gene mapper", len(hits))
    results = fan_out(
        [(lambda r=r: _map_finding_model(client, r.derived_alteration, build_gene_alteration_mapper,
                                         build_gene_alteration_reviewer, max_attempts=max_attempts,
                                         use_reviewer=use_reviewer, escalation=_GENE_ESCALATION))
         for r in hits],
        max_workers=workers,
    )
    for row, res in zip(hits, results):
        row.finding_model = res.finding_model
        if not res.faithful:
            row.faithful = False
            row.problems = " | ".join(filter(None, [row.problems, "fm: " + " | ".join(res.problems)]))
        logger.info(line(f"{PASS if res.faithful else FAIL}  {row.derived_alteration}  →  {res.finding_model}",
                         indent=8))
    return rows


# --------------------------------------------------------------------------- #
# Persistence — a plain 3NF single-key lookup, plus the hits-only view a human actually reads.
# --------------------------------------------------------------------------- #
def write_map(rows: list[DiseaseDerivedRow], path: Path, *, hits_only: bool = False) -> Path:
    out = [r for r in rows if r.derived_alteration] if hits_only else list(rows)
    out.sort(key=lambda r: r.cancer_type)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(DiseaseDerivedRow.COLUMNS), delimiter="\t",
                           lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        for r in out:
            w.writerow(r.as_row())
    return path


def read_map(path: Path) -> dict[str, DiseaseDerivedRow]:
    """`cancer_type -> row`, for the comparison stage to join on. Missing file -> empty (first run)."""
    if not Path(path).exists():
        return {}
    with Path(path).open(encoding="utf-8", newline="") as fh:
        return {
            r["cancer_type"]: DiseaseDerivedRow(
                cancer_type=r["cancer_type"],
                derived_alteration=r.get("derived_alteration", ""),
                basis=r.get("basis", ""),
                finding_model=r.get("finding_model", ""),
                faithful=r.get("faithful", "true") == "true",
                attempts=int(r.get("attempts") or 0),
                problems=r.get("problems", ""),
            )
            for r in csv.DictReader(fh, delimiter="\t")
        }
