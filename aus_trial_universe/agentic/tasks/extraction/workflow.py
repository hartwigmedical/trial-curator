"""Extraction workflow (see docs/v2_agentic_pipeline_spec.md §7).

Deterministic orchestrator; the LLM only fills the agent steps. Per trial:

  resolve regimes + drug  (CTGov: given/deterministic from armGroups · ANZCTR: single eligibility cohort,
                           regimes from the INTERVENTIONS/COMPARATOR drug agent — spec §6.1)
  -> regime-aware extractor -> scoped DNF rows
  -> parallel reviewer panel (gating: cancer_type/molecular/prior_therapy/structural; advisory: drug)
  -> bounded refine (re-extract on gating problems)
  -> distribute: per cohort, rows = trial-wide  x  cohort-specific  (representation A), attach drug

No OncoTree / finding-model conversion — cells are normalized human descriptions with provenance.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from aus_trial_universe.agentic.core.client import LlmClient
from aus_trial_universe.agentic.core.logfmt import ADVISORY, FAIL, PASS, bullet, kv, line
from aus_trial_universe.agentic.core.workflow import CheckResult, fan_out, refine
from aus_trial_universe.agentic.tasks.extraction.agents import (
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
    logger.info("")
    logger.info("cohorts (%d)", len(cohort_index))
    for cid, c in cohort_index.items():
        arm = f"  [{c.arm_type}]" if c.arm_type else ""
        logger.info(line(f"{cid}  {c.label}{arm}"))
    extractor = build_extractor_agent(client)
    reviewers = build_reviewer_agents(client) if use_judge else []
    extractor_input = f"{source_text}\n\n{_cohorts_section(cohort_index)}"
    advisory: list[str] = []  # drug (non-gating) reviewer problems from the last check
    attempt = {"n": 0}

    def produce(feedback: str = "", prior: list[_EligRaw] | None = None) -> list[_EligRaw]:
        attempt["n"] += 1
        prompt = extractor_input
        if feedback:
            # Incremental repair: give the doer its OWN prior table + only the flagged
            # issues, and tell it to keep everything unflagged verbatim. This preserves
            # correct rows and lets the loop converge instead of re-deriving from scratch.
            prior_table = _render_prior_table(prior) if prior else "(previous table unavailable)"
            prompt = (
                f"{extractor_input}\n\n"
                f"[REVISION MODE] Your previous extraction produced this table:\n{prior_table}\n\n"
                f"A reviewer flagged ONLY the following issues:\n{feedback}\n\n"
                f"Return the FULL corrected table. KEEP every row and cell that was NOT flagged EXACTLY "
                f"as-is (same values, same [SECTION] provenance); apply ONLY the fixes above (correct, add, "
                f"split, re-scope, or move to the right column as each issue requires). Do not re-derive or "
                f"re-word the rows that were already correct."
            )
        extraction: EligibilityExtraction = extractor(prompt)
        eligs = [_to_raw(r, cohort_index) for r in extraction.rows]
        refined = " · refined on reviewer feedback" if feedback else ""
        logger.info("")
        logger.info("doer · attempt %d · %d row(s)%s", attempt["n"], len(eligs), refined)
        for idx, e in enumerate(eligs, 1):
            logger.info("")
            logger.info(line(f"row {idx} · cohort {e.cohort}"))
            for col in ELIGIBILITY_COLUMNS:
                val = getattr(e, col).value.strip()
                if val:
                    logger.info(kv(col, val, pad=22))
        return eligs

    def check(eligs: list[_EligRaw]) -> CheckResult:
        problems = _rule_problems(eligs)
        if problems:
            logger.info("")
            logger.info("rules · %s", FAIL)
            for p in problems:
                logger.info(bullet(p))
            return CheckResult(ok=False, problems=problems)
        if not reviewers:
            logger.info("")
            logger.info("reviewer · skipped (--no-judge)")
            return CheckResult(ok=True)
        review_input = _review_input(source_text, cohort_index, eligs)
        verdicts = fan_out([(lambda a=agent: a(review_input)) for _, agent in reviewers])
        gating: list[str] = []
        advisory.clear()
        logger.info("")
        logger.info("reviewer · panel of %d", len(reviewers))
        for (spec, _), verdict in zip(reviewers, verdicts):
            probs = [] if verdict.faithful else (verdict.problems or ["flagged (no detail)"])
            if verdict.faithful:
                verdict_word = PASS
            elif spec.gating:
                verdict_word = FAIL
            else:
                verdict_word = ADVISORY
            count = f" · {len(probs)} issue(s)" if probs else ""
            note = " (non-gating)" if (probs and not spec.gating) else ""
            logger.info("")
            logger.info(line(f"{spec.label}: {verdict_word}{count}{note}"))
            for p in probs:
                logger.info(bullet(p))
                (gating if spec.gating else advisory).append(f"[{spec.key}] {p}")
        return CheckResult(ok=not gating, problems=gating)

    result = refine(
        produce=lambda: produce(""),
        check=check,
        repair=lambda eligs, problems: produce("\n".join(f"- {p}" for p in problems), prior=eligs),
        max_attempts=max_attempts,
    )

    rows = _distribute(result.value, cohort_index, trial_id)
    all_problems = list(result.problems) + advisory
    adv = f" · {len(advisory)} advisory drug note(s)" if advisory else ""
    logger.info("")
    logger.info("result · faithful=%s · attempts=%d · %d cohort(s) → %d DNF row(s)%s",
                result.ok, result.attempts, len(cohort_index), len(rows), adv)
    return ExtractionResult(rows=rows, faithful=result.ok, attempts=result.attempts, problems=all_problems)


# --------------------------------------------------------------------------- #
# Cohort / drug resolution
# --------------------------------------------------------------------------- #
def _resolve_cohorts(client: LlmClient, source_text: str, cohorts: list[Cohort] | None) -> list[Cohort]:
    if cohorts is not None:  # CTGov: deterministic regimes from armGroups
        return cohorts or [Cohort("all")]
    # ANZCTR: a SINGLE eligibility cohort (all criteria are trial-wide — no cohort detection); the regime axis
    # comes from the drugs — an experimental regime (INTERVENTIONS) + a control regime (COMPARATOR) only when the
    # comparator names an actual drug. Same data structure as CTGov (spec §6.1).
    dr = build_drug_agent(client)(source_text)
    main = "; ".join(dict.fromkeys(d.strip() for d in dr.intervention_drugs if d and d.strip()))
    comp = "; ".join(dict.fromkeys(d.strip() for d in dr.comparator_drugs if d and d.strip()))
    regimes: list[Cohort] = []
    if main:
        regimes.append(Cohort(label="intervention", drug=main, drug_source="INTERVENTIONS", arm_type="EXPERIMENTAL"))
    if comp:
        regimes.append(Cohort(label="comparator", drug=comp, drug_source="COMPARATOR", arm_type="ACTIVE_COMPARATOR"))
    logger.info("")
    logger.info("cohorts · ANZCTR single eligibility cohort · %d regime(s) · intervention: %s · comparator: %s",
                len(regimes) or 1, main or "(none)", comp or "(none)")
    return regimes or [Cohort(label="all", drug_source="INTERVENTIONS")]


def _regime_line(cid: str, c: Cohort) -> str:
    """One regime rendered for the extractor/reviewer: id, label, arm_type, drug, description (the
    arm_type/drug/description are the signals used to ASSIGN eligibility to the right regime)."""
    arm = f" [{c.arm_type}]" if c.arm_type else ""
    drug = f" · drug: {c.drug}" if c.drug else ""
    desc = f" — {c.description}" if c.description else ""
    return f"- {cid} = {c.label}{arm}{drug}{desc}"


def _cohorts_section(cohort_index: dict[str, Cohort]) -> str:
    lines = [_regime_line(cid, c) for cid, c in cohort_index.items()]
    return (
        "## COHORTS — the FIXED, KNOWN set of drug regimes for this trial. Assign each row's `cohort` to one of "
        "these ids, or 'trial-wide' (the default). A criterion the text ties to a group NOT listed here (a "
        "closed/withdrawn cohort) must be DROPPED — never invented as a new id.\n" + "\n".join(lines)
    )


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


def _render_prior_table(eligs: list[_EligRaw]) -> str:
    """Render the previous extraction as an editable numbered table for REVISION MODE."""
    lines: list[str] = []
    for i, e in enumerate(eligs, 1):
        cells = "; ".join(
            f"{col}={_with_sources(getattr(e, col).value, getattr(e, col).sources)}"
            for col in ELIGIBILITY_COLUMNS
            if getattr(e, col).value.strip()
        ) or "(all columns empty)"
        lines.append(f"{i}. [cohort={e.cohort}] {cells}")
    return "\n".join(lines)


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


# Single-valued axes: a patient has exactly ONE of these, so a trial-wide value and a cohort-specific
# value cannot co-occur — ANDing them would make an unsatisfiable row. The cohort-specific value wins.
_EXCLUSIVE_COLUMNS = {"cancer_type"}


def _top_level_and(expr: str) -> list[str]:
    """Split `expr` on top-level ' AND ' (paren-depth 0), keeping any NOT(...) body intact."""
    parts: list[str] = []
    depth = start = i = 0
    while i < len(expr):
        c = expr[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif depth == 0 and expr[i:i + 5] == " AND ":
            parts.append(expr[start:i])
            i += 5
            start = i
            continue
        i += 1
    parts.append(expr[start:])
    return [p.strip() for p in parts if p.strip()]


def _merge_cell(a: _Cell, b: _Cell, *, exclusive: bool = False) -> _Cell:
    """AND-combine two same-column cells (trial-wide `a` x cohort-specific `b`).

    exclusive=True (a single-valued axis like cancer_type): the two values cannot both hold for one
    patient, so ANDing the positive types ("Stage A AND Stage B") would be unsatisfiable — the
    cohort-specific value `b` WINS. But any trial-wide NOT(...) EXCLUSIONS are carve-outs (compatible
    with any positive type), so they are PRESERVED, never silently dropped. This is the safety net for
    an extractor that restates the axis in both scopes; the extractor is separately instructed to keep
    each criterion in exactly one scope.
    """
    av, bv = a.value.strip(), b.value.strip()
    if not av:
        return _Cell(bv, list(b.sources))
    if not bv or av == bv:
        return _Cell(av, list(a.sources) + list(b.sources))
    if exclusive:
        tw_exclusions = [t for t in _top_level_and(av) if t.startswith("NOT(") and t not in bv]
        if tw_exclusions:  # cohort positive type wins, but keep trial-wide exclusions
            return _Cell(" AND ".join([bv, *tw_exclusions]), list(a.sources) + list(b.sources))
        return _Cell(bv, list(b.sources))  # cohort-specific wins; never "av AND bv"
    return _Cell(f"{av} AND {bv}", list(a.sources) + list(b.sources))


def _merge(tw: _EligRaw, sp: _EligRaw) -> _EligRaw:
    merged = _EligRaw(cohort=sp.cohort)
    for col in ELIGIBILITY_COLUMNS:
        setattr(merged, col, _merge_cell(getattr(tw, col), getattr(sp, col), exclusive=col in _EXCLUSIVE_COLUMNS))
    return merged


def _rule_problems(eligs: list[_EligRaw]) -> list[str]:
    problems: list[str] = []
    if not eligs:
        problems.append("no eligibility rows were extracted")
    for i, e in enumerate(eligs):
        if not any(getattr(e, col).value.strip() for col in ELIGIBILITY_COLUMNS):
            problems.append(f"row {i}: all five eligibility columns are empty")
    return problems


# A trial-wide x cohort-specific product beyond this is almost always the extractor mis-scoping
# OR-alternatives into both scopes (they should live in one) — surfaced as a WARN, never silently emitted.
_CROSS_PRODUCT_WARN = 50


def _distribute(eligs: list[_EligRaw], cohort_index: dict[str, Cohort], trial_id: str) -> list[DnfRow]:
    """Per cohort: rows = (trial-wide OR-rows) x (cohort-specific OR-rows), cells ANDed, then de-duplicated.

    Each output row is a self-contained cohort row (shared trial-wide criteria merged in). A single-valued
    axis (cancer_type) is never ANDed across scopes — the cohort value wins (see _merge_cell).
    """
    trial_wide = [e for e in eligs if e.cohort == TRIAL_WIDE]
    rows: list[DnfRow] = []
    for cid, cohort in cohort_index.items():
        specifics = [e for e in eligs if e.cohort == cid]
        if trial_wide and specifics:
            product = len(trial_wide) * len(specifics)
            if product > _CROSS_PRODUCT_WARN:
                logger.info(bullet(
                    f"WARN · cohort {cid} ({cohort.label}): {len(trial_wide)} trial-wide × "
                    f"{len(specifics)} cohort-specific = {product} rows before dedup — likely OR-alternatives "
                    f"duplicated across scopes; each criterion should be assigned to exactly one scope."))
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
    return _dedup_rows(rows)


def _dedup_rows(rows: list[DnfRow]) -> list[DnfRow]:
    """Drop exact-duplicate DNF rows (cohort label is part of the key), preserving first-seen order.

    After the cohort-wins merge, distinct trial-wide OR-rows can collapse to the same self-contained row;
    genuinely different sub-populations are kept."""
    seen: set[tuple] = set()
    out: list[DnfRow] = []
    for r in rows:
        key = (r.trialId, r.cohort, r.arm_type, r.cancer_type, r.gene_alteration,
               r.molecular_signature, r.molecular_biomarker, r.prior_therapy, r.drug)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _review_input(source_text: str, cohort_index: dict[str, Cohort], eligs: list[_EligRaw]) -> str:
    cohorts_txt = "\n".join(
        _regime_line(cid, c) for cid, c in cohort_index.items()
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
