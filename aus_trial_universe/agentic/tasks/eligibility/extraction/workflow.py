"""Extraction workflow (see docs/v2_agentic_pipeline_spec.md §7).

Deterministic orchestrator; the LLM only fills the agent steps. Per trial, extraction runs in TWO sub-stages:

  resolve regimes + drug  (CTGov: given/deterministic from armGroups · ANZCTR: single eligibility cohort,
                           regimes from the INTERVENTIONS/COMPARATOR drug agent — spec §6.1)

  I-a RAW      -> raw_extractor copies VERBATIM criterion spans (criterion + scope + source)
               -> raw_reviewer gates on completeness/cleanliness (bounded refine)
               -> assemble the per-arm `arm_eligibility_raw` table (trial-wide spans replicated onto each arm)

  I-b INTERPRET-> interpreter reads the raw spans (+ source) -> scoped DNF rows (paraphrase logic, NO sources)
               -> parallel reviewer panel (gating: cancer_type/molecular/prior_therapy/structural + enumeration;
                  advisory: drug) (bounded refine)
               -> distribute: per cohort, rows = trial-wide x cohort-specific, cells ANDed, de-duplicated

No OncoTree / finding-model conversion — that is a later mapping stage.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from aus_trial_universe.agentic.core.client import LlmClient
from aus_trial_universe.agentic.core.logfmt import ADVISORY, FAIL, PASS, bullet, kv, line
from aus_trial_universe.agentic.core.review import review_refine
from aus_trial_universe.agentic.core.workflow import CheckResult, fan_out
from aus_trial_universe.agentic.tasks.eligibility.extraction.agents import (
    build_enumeration_reviewer,
    build_interpreter_agent,
    build_raw_extractor_agent,
    build_raw_reviewer_agent,
    build_reviewer_agents,
)
from aus_trial_universe.agentic.tasks.eligibility.extraction.schema import (
    CRITERION_STEMS,
    DnfRow,
    EligibilityExtraction,
    ExtractedRow,
    RawFragment,
    TRIAL_WIDE,
)
# ANZCTR arm identification is a path-neutral SHARED module (used by the drug path too). `Cohort` lives there.
from aus_trial_universe.agentic.tasks.shared.cohorts import Cohort, resolve_cohorts

logger = logging.getLogger(__name__)

ELIGIBILITY_COLUMNS = list(CRITERION_STEMS)


@dataclass
class ArmRaw:
    """The verbatim raw text for one arm (grain of the `arm_eligibility_raw` table). One string per criterion:
    `text [source] | text [source] | ...` (trial-wide spans are replicated onto every arm)."""

    arm: str
    cancer_type_raw: str = ""
    gene_alteration_raw: str = ""
    molecular_signature_raw: str = ""
    molecular_biomarker_raw: str = ""
    prior_therapy_raw: str = ""


@dataclass
class _Cell:
    value: str = ""
    sources: list[str] = field(default_factory=list)


@dataclass
class _EligRaw:
    """A scoped eligibility conjunction from the interpreter (cells are interpreted logic, no sources)."""

    cohort: str = TRIAL_WIDE  # a cohort id (e.g. "C1") or TRIAL_WIDE
    cancer_type: _Cell = field(default_factory=_Cell)
    gene_alteration: _Cell = field(default_factory=_Cell)
    molecular_signature: _Cell = field(default_factory=_Cell)
    molecular_biomarker: _Cell = field(default_factory=_Cell)
    prior_therapy: _Cell = field(default_factory=_Cell)


@dataclass
class ExtractionResult:
    arm_raw: list[ArmRaw]        # the per-arm verbatim raw text (arm_eligibility_raw table)
    rows: list[DnfRow]           # the interpreted DNF conjunctions (interpreted_eligibility table)
    faithful: bool
    attempts: int
    cohorts: list[Cohort] = field(default_factory=list)  # the resolved arms (label + arm_type) — the trial_arms spine
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
    """Extract a trial's eligibility (two sub-stages: raw copy -> interpret; see module docstring).

    cohorts=None triggers the ANZCTR path (single eligibility cohort; drug doer->reviewer);
    a provided list is the CTGov path (deterministic regimes/drug).
    """
    cohorts = _resolve_cohorts(client, source_text, cohorts)
    cohort_index = {f"C{i + 1}": c for i, c in enumerate(cohorts)}
    logger.info("")
    logger.info("cohorts (%d)", len(cohort_index))
    for cid, c in cohort_index.items():
        arm = f"  [{c.arm_type}]" if c.arm_type else ""
        logger.info(line(f"{cid}  {c.label}{arm}"))

    base_input = f"{source_text}\n\n{_cohorts_section(cohort_index)}"

    # --- STAGE I-a: RAW extraction -> per-arm verbatim table ---------------- #
    fragments, raw_faithful, raw_attempts = _extract_raw(
        client, source_text, cohort_index, base_input, max_attempts=max_attempts, use_judge=use_judge)
    arm_raw = _assemble_arm_raw(cohort_index, fragments)

    # --- STAGE I-b: interpretation -> DNF conjunctions ---------------------- #
    rows, interp_faithful, interp_attempts, problems = _interpret(
        client, trial_id, source_text, cohort_index, fragments, base_input,
        max_attempts=max_attempts, use_judge=use_judge)

    logger.info("")
    logger.info("result · raw(faithful=%s, attempts=%d) · interpret(faithful=%s, attempts=%d) · %d arm(s) → %d DNF row(s)",
                raw_faithful, raw_attempts, interp_faithful, interp_attempts, len(cohort_index), len(rows))
    # `attempts` = the INTERPRETATION refine count (the meaningful loop); raw attempts are logged above.
    return ExtractionResult(arm_raw=arm_raw, rows=rows, faithful=raw_faithful and interp_faithful,
                            attempts=interp_attempts, cohorts=cohorts, problems=problems)


# --------------------------------------------------------------------------- #
# Stage I-a — raw extraction
# --------------------------------------------------------------------------- #
def _extract_raw(client: LlmClient, source_text: str, cohort_index: dict[str, "Cohort"], base_input: str,
                 *, max_attempts: int, use_judge: bool) -> tuple[list[RawFragment], bool, int]:
    """Copy verbatim criterion spans, gated by the completeness reviewer (bounded refine)."""
    extractor = build_raw_extractor_agent(client)
    reviewer = build_raw_reviewer_agent(client) if use_judge else None
    _ESCALATION = ("\n\n[ESCALATION-MODE] The writer has repeated the same errors across attempts. In ADDITION to "
                   "problems, fill `suggested_fix` with the concrete fragment(s) to add / remove / correct.")
    logger.info("")
    logger.info(line("Stage I-a · RAW extraction (verbatim source spans)"))
    attempt = {"n": 0}

    def produce(feedback: str = "", prior=None) -> list[RawFragment]:
        attempt["n"] += 1
        prompt = base_input if not feedback else (
            f"{base_input}\n\n[REVISION — a reviewer flagged these; fix ONLY these, keep the rest verbatim]:\n{feedback}")
        frags = extractor(prompt).fragments
        logger.info("")
        logger.info("raw doer · attempt %d · %d fragment(s)%s", attempt["n"], len(frags),
                    " · refined" if feedback else "")
        return frags

    def check(frags: list[RawFragment], escalate: bool = False) -> CheckResult:
        if not frags:
            logger.info("raw rules · %s · no fragments", FAIL)
            return CheckResult(ok=False, problems=["no raw fragments were extracted"])
        if reviewer is None:
            return CheckResult(ok=True)
        v = reviewer(_raw_review_input(source_text, cohort_index, frags) + (_ESCALATION if escalate else ""))
        probs = [] if v.faithful else (v.problems or ["raw extraction flagged (no detail)"])
        if escalate and not v.faithful and (v.suggested_fix or "").strip():
            probs = probs + [f"SUGGESTED FIX: {v.suggested_fix.strip()}"]
        logger.info("raw reviewer · %s%s", PASS if v.faithful else FAIL,
                    f" · {len(probs)} issue(s)" if probs else "")
        for p in probs:
            logger.info(bullet(p))
        return CheckResult(ok=v.faithful, problems=probs)

    # Shared doer→reviewer loop (core.review): escalate=True (last-resort ESCALATION-MODE) only when a reviewer is
    # present — with --no-judge the check passes on attempt 1 and no escalation is possible.
    result = review_refine(produce, check, max_attempts=max_attempts, escalate=reviewer is not None)
    return result.value, result.ok, result.attempts


def _assemble_arm_raw(cohort_index: dict[str, "Cohort"], fragments: list[RawFragment]) -> list[ArmRaw]:
    """Per arm, per criterion: `text [source] | ...` from the trial-wide + that-arm fragments (spec: trial-wide
    spans are replicated onto every arm). Grain: (arm)."""
    arms: list[ArmRaw] = []
    for cid, cohort in cohort_index.items():
        row = ArmRaw(arm=cohort.label)
        for stem in CRITERION_STEMS:
            frags = [f for f in fragments
                     if _norm_criterion(f.criterion) == stem
                     and _resolve_scope(f.scope, cohort_index) in (TRIAL_WIDE, cid)]
            rendered = " | ".join(
                f"{_norm_ws(f.text)} [{(f.source or '').strip()}]" if (f.source or "").strip() else _norm_ws(f.text)
                for f in frags if (f.text or "").strip()
            )
            setattr(row, f"{stem}_raw", rendered)
        arms.append(row)
    return arms


def _norm_ws(text: str) -> str:
    """Collapse all whitespace (incl. embedded newlines/tabs) in a verbatim span to single spaces, so the raw
    table stays line-oriented TSV. Content-preserving; only whitespace is normalized."""
    return " ".join((text or "").split())


def _norm_criterion(name: str) -> str:
    """Map a raw fragment's `criterion` onto one of the 5 stems (lenient: case/space/substring)."""
    low = (name or "").strip().lower().replace(" ", "_")
    for stem in CRITERION_STEMS:
        if low == stem or stem in low or low in stem:
            return stem
    return ""


def _raw_review_input(source_text: str, cohort_index: dict[str, "Cohort"], fragments: list[RawFragment]) -> str:
    cohorts_txt = "\n".join(_regime_line(cid, c) for cid, c in cohort_index.items()) or "(single trial-wide cohort)"
    frags_txt = "\n".join(
        f"- criterion={f.criterion}, scope={f.scope}, source={f.source!r}: {f.text!r}" for f in fragments
    ) or "(no fragments)"
    return (
        f"SOURCE TRIAL TEXT (all relevant sections):\n{source_text}\n\n"
        f"COHORTS:\n{cohorts_txt}\n\n"
        f"PROPOSED RAW FRAGMENTS (verbatim spans copied per criterion):\n{frags_txt}"
    )


def _render_raw_grouped(cohort_index: dict[str, "Cohort"], fragments: list[RawFragment]) -> str:
    """Render the raw spans grouped by scope then criterion, for the interpreter + review input."""
    scopes = [TRIAL_WIDE] + list(cohort_index.keys())
    lines: list[str] = []
    for scope in scopes:
        scoped = [f for f in fragments if _resolve_scope(f.scope, cohort_index) == scope]
        if not scoped:
            continue
        header = "trial-wide" if scope == TRIAL_WIDE else f"{scope} = {cohort_index[scope].label}"
        lines.append(f"[{header}]")
        for stem in CRITERION_STEMS:
            texts = [f"{f.text.strip()} [{(f.source or '').strip()}]" for f in scoped
                     if _norm_criterion(f.criterion) == stem and (f.text or "").strip()]
            if texts:
                lines.append(f"  {stem}: " + " ; ".join(texts))
    return "\n".join(lines) or "(no raw spans)"


# --------------------------------------------------------------------------- #
# Stage I-b — interpretation
# --------------------------------------------------------------------------- #
def _interpret(client: LlmClient, trial_id: str, source_text: str, cohort_index: dict[str, "Cohort"],
               fragments: list[RawFragment], base_input: str, *, max_attempts: int, use_judge: bool,
               ) -> tuple[list[DnfRow], bool, int, list[str]]:
    interpreter = build_interpreter_agent(client)
    reviewers = build_reviewer_agents(client) if use_judge else []
    enum_reviewer = build_enumeration_reviewer(client) if use_judge else None
    raw_grouped = _render_raw_grouped(cohort_index, fragments)
    interpreter_input = (
        f"RAW ELIGIBILITY SPANS (verbatim, grouped by scope then criterion — interpret THESE):\n{raw_grouped}\n\n"
        f"FULL TRIAL TEXT (context for resolving OR/AND and scope):\n{base_input}"
    )
    advisory: list[str] = []
    attempt = {"n": 0}
    _ESCALATION = ("\n\n[ESCALATION-MODE] The writer has repeated the same errors across attempts. In ADDITION to "
                   "problems, fill `suggested_fix` with the concrete corrected cell(s)/row(s).")
    logger.info("")
    logger.info(line("Stage I-b · INTERPRETATION (raw spans -> DNF)"))

    def produce(feedback: str = "", prior: list[_EligRaw] | None = None) -> list[_EligRaw]:
        attempt["n"] += 1
        prompt = interpreter_input
        if feedback:
            prior_table = _render_prior_table(prior) if prior else "(previous table unavailable)"
            prompt = (
                f"{interpreter_input}\n\n"
                f"[REVISION MODE] Your previous interpretation produced this table:\n{prior_table}\n\n"
                f"A reviewer flagged ONLY the following issues:\n{feedback}\n\n"
                f"Return the FULL corrected table. KEEP every row and cell that was NOT flagged EXACTLY as-is; "
                f"apply ONLY the fixes above (correct, add, split, re-scope, or move to the right column as each "
                f"issue requires). Do not re-derive or re-word the rows that were already correct."
            )
        eligs = [_to_raw(r, cohort_index) for r in interpreter(prompt).rows]
        logger.info("")
        logger.info("interp doer · attempt %d · %d row(s)%s", attempt["n"], len(eligs),
                    " · refined on reviewer feedback" if feedback else "")
        for idx, e in enumerate(eligs, 1):
            logger.info("")
            logger.info(line(f"row {idx} · cohort {e.cohort}"))
            for col in ELIGIBILITY_COLUMNS:
                val = getattr(e, col).value.strip()
                if val:
                    logger.info(kv(col, val, pad=22))
        return eligs

    def check(eligs: list[_EligRaw], escalate: bool = False) -> CheckResult:
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
        review_input = _review_input(source_text, cohort_index, eligs, raw_grouped) + (_ESCALATION if escalate else "")
        thunks = [(lambda a=agent: a(review_input)) for _, agent in reviewers]
        assembled = _distribute(eligs, cohort_index, trial_id) if enum_reviewer is not None else []
        if enum_reviewer is not None:
            agg_input = _aggregate_input(source_text, assembled) + (_ESCALATION if escalate else "")
            thunks.append(lambda: enum_reviewer(agg_input))
        verdicts = fan_out(thunks)
        gating: list[str] = []
        advisory.clear()
        logger.info("")
        logger.info("reviewer · panel of %d%s", len(reviewers), " + enumeration" if enum_reviewer is not None else "")
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
            if escalate and not verdict.faithful and (verdict.suggested_fix or "").strip():
                fix = f"[{spec.key}] SUGGESTED FIX: {verdict.suggested_fix.strip()}"
                logger.info(bullet(fix))
                (gating if spec.gating else advisory).append(fix)
        if enum_reviewer is not None:
            v = verdicts[-1]
            probs = [] if v.faithful else (v.problems or ["flagged (no detail)"])
            logger.info("")
            logger.info(line(f"enumeration: {PASS if v.faithful else FAIL}"
                             + (f" · {len(probs)} issue(s)" if probs else "") + f" · {len(assembled)} assembled row(s)"))
            for p in probs:
                logger.info(bullet(p))
                gating.append(f"[enumeration] {p}")
            if escalate and not v.faithful and (v.suggested_fix or "").strip():
                fix = f"[enumeration] SUGGESTED FIX: {v.suggested_fix.strip()}"
                logger.info(bullet(fix))
                gating.append(fix)
        return CheckResult(ok=not gating, problems=gating)

    # Shared doer→reviewer loop (core.review): escalate=True wires the last-resort ESCALATION-MODE re-review that
    # asks the panel for concrete suggested fixes (a no-op when --no-judge, where check passes on attempt 1).
    result = review_refine(produce, check, max_attempts=max_attempts, escalate=True)
    rows = _distribute(result.value, cohort_index, trial_id)
    return rows, result.ok, result.attempts, list(result.problems) + advisory


# --------------------------------------------------------------------------- #
# Cohort / drug resolution
# --------------------------------------------------------------------------- #
# ANZCTR arm identification (`extract_anzctr_drugs` / `anzctr_regimes`) + the `Cohort` type + the non-drug-modality
# filter now live in the path-neutral SHARED module `tasks/shared/cohorts.py` (used by the drug path too). Here we
# only wrap the shared `resolve_cohorts` to add the ANZCTR log line.
_PROVENANCE_RE = re.compile(r"\s*\[[^\]]*\]\s*$")   # trailing "[SECTION; ...]" tag (still used for the drug cell)


def _resolve_cohorts(client: LlmClient, source_text: str, cohorts: list[Cohort] | None) -> list[Cohort]:
    regimes = resolve_cohorts(client, source_text, cohorts)   # shared, flag-independent derivation
    if cohorts is None:  # ANZCTR — log the derived regime axis
        logger.info("")
        logger.info("cohorts · ANZCTR single eligibility cohort · %d regime(s) · %s", len(regimes),
                    " · ".join(f"{c.label}: {c.drug or '(none)'}" for c in regimes))
    return regimes


def _regime_line(cid: str, c: Cohort) -> str:
    """One regime rendered for the extractor/reviewer: id, label, arm_type, drug, description."""
    arm = f" [{c.arm_type}]" if c.arm_type else ""
    drug = f" · drug: {c.drug}" if c.drug else ""
    desc = f" — {c.description}" if c.description else ""
    return f"- {cid} = {c.label}{arm}{drug}{desc}"


def _cohorts_section(cohort_index: dict[str, Cohort]) -> str:
    lines = [_regime_line(cid, c) for cid, c in cohort_index.items()]
    return (
        "## COHORTS — the FIXED, KNOWN set of drug regimes for this trial. Assign each row's `cohort`/`scope` to "
        "one of these ids, or 'trial-wide' (the default). A criterion the text ties to a group NOT listed here (a "
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
# Rendering / consolidation (interpreted DNF; cells carry no source)
# --------------------------------------------------------------------------- #
def _to_raw(r: ExtractedRow, cohort_index: dict[str, Cohort]) -> _EligRaw:
    return _EligRaw(
        cohort=_resolve_scope(r.cohort, cohort_index),
        cancer_type=_Cell(r.cancer_type),
        gene_alteration=_Cell(r.gene_alteration),
        molecular_signature=_Cell(r.molecular_signature),
        molecular_biomarker=_Cell(r.molecular_biomarker),
        prior_therapy=_Cell(r.prior_therapy),
    )


def _render_prior_table(eligs: list[_EligRaw]) -> str:
    """Render the previous interpretation as an editable numbered table for REVISION MODE."""
    lines: list[str] = []
    for i, e in enumerate(eligs, 1):
        cells = "; ".join(
            f"{col}={getattr(e, col).value}" for col in ELIGIBILITY_COLUMNS if getattr(e, col).value.strip()
        ) or "(all columns empty)"
        lines.append(f"{i}. [cohort={e.cohort}] {cells}")
    return "\n".join(lines)


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

    exclusive=True (a single-valued axis like cancer_type): the cohort-specific value `b` WINS (ANDing two
    positive types would be unsatisfiable), but trial-wide NOT(...) exclusions are carve-outs and are PRESERVED.
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


# A trial-wide x cohort-specific product beyond this is almost always the interpreter mis-scoping OR-alternatives
# into both scopes (they should live in one) — surfaced as a WARN, never silently emitted.
_CROSS_PRODUCT_WARN = 50


def _distribute(eligs: list[_EligRaw], cohort_index: dict[str, Cohort], trial_id: str) -> list[DnfRow]:
    """Per cohort: rows = (trial-wide OR-rows) x (cohort-specific OR-rows), cells ANDed, then de-duplicated.

    Each output row is a self-contained cohort row. A single-valued axis (cancer_type) is never ANDed across
    scopes — the cohort value wins (see _merge_cell). Eligibility cells carry NO source tag (provenance is in the
    raw table); only the drug cell keeps its source (for the drug path)."""
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
                    cohort=cohort.label,   # raw arm label — the (trialId, arm) join key to the drug utility path
                    arm_type=cohort.arm_type,
                    cancer_type=e.cancer_type.value.strip(),
                    gene_alteration=e.gene_alteration.value.strip(),
                    molecular_signature=e.molecular_signature.value.strip(),
                    molecular_biomarker=e.molecular_biomarker.value.strip(),
                    prior_therapy=e.prior_therapy.value.strip(),
                    drug=_with_drug_source(cohort.drug, cohort.drug_source),
                )
            )
    return _dedup_rows(rows)


def _with_drug_source(value: str, source: str) -> str:
    value = (value or "").strip()
    src = (source or "").strip()
    return f"{value} [{src}]" if value and src else value


def _canon_cell(value: str) -> str:
    """Order-independent form of a cell for de-duplication: top-level AND-terms sorted (AND is commutative), any
    trailing provenance tag stripped. So `A AND B` and `B AND A` share a key."""
    body = _PROVENANCE_RE.sub("", value or "").strip()
    return " AND ".join(sorted(_top_level_and(body)))


def _dedup_rows(rows: list[DnfRow]) -> list[DnfRow]:
    """Drop duplicate DNF rows, preserving first-seen order + text (commutative duplicates collapse)."""
    seen: set[tuple] = set()
    out: list[DnfRow] = []
    for r in rows:
        key = (r.trialId, r.cohort, r.arm_type, _canon_cell(r.cancer_type), _canon_cell(r.gene_alteration),
               _canon_cell(r.molecular_signature), _canon_cell(r.molecular_biomarker),
               _canon_cell(r.prior_therapy), _canon_cell(r.drug))
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _review_input(source_text: str, cohort_index: dict[str, Cohort], eligs: list[_EligRaw], raw_grouped: str) -> str:
    cohorts_txt = "\n".join(
        _regime_line(cid, c) for cid, c in cohort_index.items()
    ) or "(single trial-wide cohort)"
    table = "\n".join(
        f"- cohort={e.cohort}, " + ", ".join(
            f"{col}={getattr(e, col).value!r}" for col in ELIGIBILITY_COLUMNS
        )
        for e in eligs
    ) or "(no rows)"
    return (
        f"VERBATIM RAW SPANS (the interpretation must be faithful to THESE):\n{raw_grouped}\n\n"
        f"SOURCE TRIAL TEXT (context):\n{source_text}\n\n"
        f"COHORTS:\n{cohorts_txt}\n\n"
        f"INTERPRETED DNF TABLE (rows ORed; cells within a row ANDed; NOT(...) = exclusion):\n{table}"
    )


def _aggregate_input(source_text: str, rows: list[DnfRow]) -> str:
    """The ASSEMBLED DNF table (post-distribute, de-duplicated) rendered for the enumeration reviewer."""
    lines = []
    for i, r in enumerate(rows, 1):
        cells = ", ".join(f"{col}={getattr(r, col)}" for col in ELIGIBILITY_COLUMNS if getattr(r, col).strip())
        lines.append(f"{i}. [{r.cohort}] {cells or '(empty)'}")
    table = "\n".join(lines) or "(no rows)"
    return (
        f"TRIAL TEXT:\n{source_text}\n\n"
        f"ASSEMBLED DNF TABLE — {len(rows)} row(s) (each row = one AND-conjunction; the rows are OR-alternatives; "
        f"NOT(...) = exclusion):\n{table}"
    )
