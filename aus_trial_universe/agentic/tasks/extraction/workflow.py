"""Eligibility-extraction workflow (spec §6; audit doc §2-§3).

Deterministic orchestrator; the LLM only fills the agent steps. Per trial:

  resolve cohorts + drug  (CTGov: given/deterministic · ANZCTR: cohort-detect + drug agents)
  -> cohort-aware extractor -> scoped DNF rows
  -> parallel reviewer panel (gating: cancer_type/molecular/prior_therapy/structural; advisory: drug)
  -> bounded refine (re-extract on gating problems)
  -> distribute: per cohort, rows = trial-wide  x  cohort-specific  (representation A), attach drug

No OncoTree / finding-model conversion — cells are normalized human descriptions with provenance.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from aus_trial_universe.agentic.core.client import LlmClient
from aus_trial_universe.agentic.core.workflow import CheckResult, fan_out, refine
from aus_trial_universe.agentic.tasks.extraction.agents import (
    build_cohort_detector_agent,
    build_drug_agent,
    build_extractor_agent,
    build_reviewer_agents,
)
from aus_trial_universe.agentic.tasks.extraction.schema import (
    DnfRow,
    EligibilityExtraction,
    ExtractedRow,
    TRIAL_WIDE,
)

logger = logging.getLogger(__name__)

ELIGIBILITY_COLUMNS = [
    "cancer_type", "gene_alteration", "molecular_signature", "molecular_biomarker", "prior_therapy",
]
TSV_COLUMNS = ["trialId", "cohort", "arm_type"] + ELIGIBILITY_COLUMNS + ["drug"]


@dataclass
class Cohort:
    """One cohort of a trial. drug is raw ("; "-joined), not normalized."""

    label: str
    drug: str = ""
    drug_source: str = ""
    description: str = ""
    arm_type: str = ""  # CTGov armGroups[].type: EXPERIMENTAL / ACTIVE_COMPARATOR / PLACEBO_COMPARATOR / ...


@dataclass
class _Cell:
    value: str = ""
    sources: list[str] = field(default_factory=list)


@dataclass
class _EligRaw:
    """A scoped eligibility conjunction, cells still raw (value + sources)."""

    cohort: str = TRIAL_WIDE  # a cohort id (e.g. "C1") or TRIAL_WIDE
    cancer_type: _Cell = field(default_factory=_Cell)
    gene_alteration: _Cell = field(default_factory=_Cell)
    molecular_signature: _Cell = field(default_factory=_Cell)
    molecular_biomarker: _Cell = field(default_factory=_Cell)
    prior_therapy: _Cell = field(default_factory=_Cell)


@dataclass
class ExtractionResult:
    rows: list[DnfRow]
    faithful: bool
    attempts: int
    problems: list[str] = field(default_factory=list)


def extract_trial(
    client: LlmClient,
    *,
    trial_id: str,
    source_text: str,
    cohorts: list[Cohort] | None = None,
    max_attempts: int = 3,
    use_judge: bool = True,
) -> ExtractionResult:
    """Extract a trial's eligibility into a DNF table (see module docstring).

    cohorts=None triggers the ANZCTR path (LLM cohort-detection + drug extraction);
    a provided list is the CTGov path (deterministic cohorts/drug).
    """
    cohorts = _resolve_cohorts(client, source_text, cohorts)
    cohort_index = {f"C{i + 1}": c for i, c in enumerate(cohorts)}
    logger.info("  COHORTS     %d: %s", len(cohorts),
                " | ".join(f"{cid}={c.label}" for cid, c in cohort_index.items()))
    extractor = build_extractor_agent(client)
    reviewers = build_reviewer_agents(client) if use_judge else []
    extractor_input = f"{source_text}\n\n{_cohorts_section(cohort_index)}"
    advisory: list[str] = []  # drug (non-gating) reviewer problems from the last check
    attempt = {"n": 0}

    def produce(feedback: str = "") -> list[_EligRaw]:
        attempt["n"] += 1
        prompt = extractor_input
        if feedback:
            prompt = f"{extractor_input}\n\n[Reviewer feedback — fix these issues]:\n{feedback}"
            logger.info("  REFINE      attempt %d - re-extracting with reviewer feedback", attempt["n"])
        extraction: EligibilityExtraction = extractor(prompt)
        eligs = [_to_raw(r, cohort_index) for r in extraction.rows]
        logger.info("  EXTRACT     attempt %d -> %d eligibility row(s):", attempt["n"], len(eligs))
        for e in eligs:
            logger.info("                %s", _fmt_elig(e))
        return eligs

    def check(eligs: list[_EligRaw]) -> CheckResult:
        problems = _rule_problems(eligs)
        if problems:
            logger.info("  RULE-CHECK  FAILED: %s", "; ".join(problems))
            return CheckResult(ok=False, problems=problems)
        if not reviewers:
            logger.info("  REVIEW      skipped (--no-judge)")
            return CheckResult(ok=True)
        review_input = _review_input(source_text, cohort_index, eligs)
        verdicts = fan_out([(lambda a=agent: a(review_input)) for _, agent in reviewers])
        symbols, gating = [], []
        advisory.clear()
        for (spec, _), verdict in zip(reviewers, verdicts):
            mark = "OK" if verdict.faithful else ("!" if not spec.gating else "X")
            symbols.append(f"{spec.key} {mark}")
            if not verdict.faithful:
                for p in (verdict.problems or ["flagged (no detail)"]):
                    (gating if spec.gating else advisory).append(f"[{spec.key}] {p}")
        logger.info("  REVIEW      %s", "  |  ".join(symbols))
        for g in gating:
            logger.info("                X  %s", g)
        for a in advisory:
            logger.info("                !  %s (advisory)", a)
        return CheckResult(ok=not gating, problems=gating)

    result = refine(
        produce=lambda: produce(""),
        check=check,
        repair=lambda eligs, problems: produce("\n".join(f"- {p}" for p in problems)),
        max_attempts=max_attempts,
    )

    rows = _distribute(result.value, cohort_index, trial_id)
    all_problems = list(result.problems) + advisory
    logger.info("  CONSOLIDATE %d cohort(s) -> %d DNF row(s)", len(cohort_index), len(rows))
    logger.info("  RESULT      faithful=%s | attempts=%d%s", result.ok, result.attempts,
                f" | {len(advisory)} advisory drug note(s)" if advisory else "")
    return ExtractionResult(rows=rows, faithful=result.ok, attempts=result.attempts, problems=all_problems)


# --------------------------------------------------------------------------- #
# Cohort / drug resolution
# --------------------------------------------------------------------------- #
def _resolve_cohorts(client: LlmClient, source_text: str, cohorts: list[Cohort] | None) -> list[Cohort]:
    if cohorts is not None:  # CTGov: deterministic cohorts + drug
        return cohorts or [Cohort("all")]
    # ANZCTR: detect cohorts + extract drug via LLM
    detection = build_cohort_detector_agent(client)(source_text)
    drug_res = build_drug_agent(client)(source_text)
    drug = "; ".join(dict.fromkeys(d.strip() for d in drug_res.drugs if d and d.strip()))
    logger.info("  COHORTS     ANZCTR detection -> %d group(s) | drug (LLM): %s",
                len(detection.cohorts), drug or "(none)")
    if detection.cohorts:
        return [Cohort(label=c.label, description=c.description, drug=drug, drug_source="INTERVENTIONS")
                for c in detection.cohorts]
    return [Cohort(label="all", drug=drug, drug_source="INTERVENTIONS")]


def _cohorts_section(cohort_index: dict[str, Cohort]) -> str:
    lines = [
        f"- {cid} = {c.label}" + (f" — {c.description}" if c.description else "")
        for cid, c in cohort_index.items()
    ]
    return "## COHORTS (assign each row's cohort to one of these ids, or 'trial-wide')\n" + "\n".join(lines)


def _resolve_scope(raw: str, cohort_index: dict[str, Cohort]) -> str:
    raw = (raw or "").strip()
    if raw in cohort_index:
        return raw
    low = raw.lower()
    if low in ("trial-wide", "trialwide", "all", ""):
        return TRIAL_WIDE
    for cid, c in cohort_index.items():
        if low == c.label.lower():
            return cid
    return TRIAL_WIDE  # unknown scope -> safe default


# --------------------------------------------------------------------------- #
# Rendering / consolidation
# --------------------------------------------------------------------------- #
def _to_raw(r: ExtractedRow, cohort_index: dict[str, Cohort]) -> _EligRaw:
    return _EligRaw(
        cohort=_resolve_scope(r.cohort, cohort_index),
        cancer_type=_Cell(r.cancer_type, r.cancer_type_sources),
        gene_alteration=_Cell(r.gene_alteration, r.gene_alteration_sources),
        molecular_signature=_Cell(r.molecular_signature, r.molecular_signature_sources),
        molecular_biomarker=_Cell(r.molecular_biomarker, r.molecular_biomarker_sources),
        prior_therapy=_Cell(r.prior_therapy, r.prior_therapy_sources),
    )


def _cohort_display(label: str) -> str:
    """Render the synthetic single-cohort label in brackets, e.g. 'all' -> '(all)'."""
    return "(all)" if label.strip().lower() == "all" else label


def _short(value: str, limit: int = 72) -> str:
    value = value.strip()
    return value if len(value) <= limit else value[: limit - 3] + "..."


def _fmt_elig(e: _EligRaw) -> str:
    """Compact one-line render of an extracted row for the log (values truncated)."""
    parts = [
        f"{col}={_short(getattr(e, col).value)}"
        for col in ELIGIBILITY_COLUMNS
        if getattr(e, col).value.strip()
    ]
    return f"[{e.cohort}] " + "  |  ".join(parts) if parts else f"[{e.cohort}] (empty)"


def _with_sources(value: str, sources: list[str]) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    seen: set[str] = set()
    labels: list[str] = []
    for s in sources or []:
        s = (s or "").strip()
        if s and s not in seen:
            seen.add(s)
            labels.append(s)
    return f"{value} [{'; '.join(labels)}]" if labels else value


def _merge_cell(a: _Cell, b: _Cell) -> _Cell:
    """AND-combine two same-column cells (trial-wide x cohort-specific)."""
    av, bv = a.value.strip(), b.value.strip()
    if not av:
        return _Cell(bv, list(b.sources))
    if not bv or av == bv:
        return _Cell(av, list(a.sources) + list(b.sources))
    return _Cell(f"{av} AND {bv}", list(a.sources) + list(b.sources))


def _merge(tw: _EligRaw, sp: _EligRaw) -> _EligRaw:
    merged = _EligRaw(cohort=sp.cohort)
    for col in ELIGIBILITY_COLUMNS:
        setattr(merged, col, _merge_cell(getattr(tw, col), getattr(sp, col)))
    return merged


def _rule_problems(eligs: list[_EligRaw]) -> list[str]:
    problems: list[str] = []
    if not eligs:
        problems.append("no eligibility rows were extracted")
    for i, e in enumerate(eligs):
        if not any(getattr(e, col).value.strip() for col in ELIGIBILITY_COLUMNS):
            problems.append(f"row {i}: all five eligibility columns are empty")
    return problems


def _distribute(eligs: list[_EligRaw], cohort_index: dict[str, Cohort], trial_id: str) -> list[DnfRow]:
    """Per cohort: rows = (trial-wide OR-rows) x (cohort-specific OR-rows), cells ANDed."""
    trial_wide = [e for e in eligs if e.cohort == TRIAL_WIDE]
    rows: list[DnfRow] = []
    for cid, cohort in cohort_index.items():
        specifics = [e for e in eligs if e.cohort == cid]
        if trial_wide and specifics:
            combos = [_merge(tw, sp) for tw in trial_wide for sp in specifics]
        elif specifics:
            combos = specifics
        else:
            combos = trial_wide
        for e in combos:
            rows.append(
                DnfRow(
                    trialId=trial_id,
                    cohort=_cohort_display(cohort.label),
                    arm_type=cohort.arm_type,
                    cancer_type=_with_sources(e.cancer_type.value, e.cancer_type.sources),
                    gene_alteration=_with_sources(e.gene_alteration.value, e.gene_alteration.sources),
                    molecular_signature=_with_sources(e.molecular_signature.value, e.molecular_signature.sources),
                    molecular_biomarker=_with_sources(e.molecular_biomarker.value, e.molecular_biomarker.sources),
                    prior_therapy=_with_sources(e.prior_therapy.value, e.prior_therapy.sources),
                    drug=_with_sources(cohort.drug, [cohort.drug_source]),
                )
            )
    return rows


def _review_input(source_text: str, cohort_index: dict[str, Cohort], eligs: list[_EligRaw]) -> str:
    cohorts_txt = "\n".join(
        f"- {cid} = {c.label}" + (f" — {c.description}" if c.description else "")
        + (f" (drug: {c.drug})" if c.drug else "")
        for cid, c in cohort_index.items()
    ) or "(single trial-wide cohort)"
    table = "\n".join(
        f"- cohort={e.cohort}, " + ", ".join(
            f"{col}={_with_sources(getattr(e, col).value, getattr(e, col).sources)!r}"
            for col in ELIGIBILITY_COLUMNS
        )
        for e in eligs
    ) or "(no rows)"
    return (
        f"SOURCE TRIAL TEXT (all relevant sections):\n{source_text}\n\n"
        f"COHORTS:\n{cohorts_txt}\n\n"
        f"EXTRACTED DNF TABLE (rows ORed; cells within a row ANDed; NOT(...) = exclusion):\n{table}"
    )
