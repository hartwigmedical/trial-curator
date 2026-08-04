"""STAGE 2 — gene-alteration reconciliation. NEW: the production pipeline has no gene-alteration stage 2.

Production `reconcile.py` branches on `is_oncotree`; the gene path short-circuits (`refined[value] = code`), so
stage-1 output shipped unreconciled. That is why the corpus carries three spellings of "gene X wild-type", two
renderings of a De Morgan pair, and tautological conjuncts nothing removed.

The stage is deliberately deterministic-first — a mechanical rule is testable and cannot regress elsewhere, so the
LLM is reserved for judgements that genuinely need the source text:

  R0  canonicalise            deterministic  De Morgan, absorption, dedup, term/field/OR ordering, DNF
  R1  detect                  deterministic  the semantic defect catalogue (checks.py)
  R2  repair                  LLM            per-value, given ONLY that value's own defects  (doer -> reviewer)
  R3  group                   deterministic  values whose SOURCES mean the same thing but whose expressions differ
  R4  adjudicate              LLM            one FINAL expression per group member          (doer -> reviewer)
  R5  finalise                deterministic  canonicalise again, then the syntax gate; idempotence is asserted

R2 and R4 both run on the shared `core.review.review_refine` harness, so the loop mechanism is the one used
everywhere else in the pipeline (memory `v2-shared-loop-harness`).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from aus_trial_universe.core.client import LlmClient
from aus_trial_universe.core.review import review_refine
from aus_trial_universe.core.workflow import CheckResult, fan_out
from aus_trial_universe.qa.adjudications import gene_alteration as gene_rulings
from aus_trial_universe.tasks.eligibility.mapping.gene_alteration import expr as E
from aus_trial_universe.tasks.eligibility.mapping.gene_alteration.agents import (
    build_gene_reconcile_reviewer,
    build_gene_reconciler,
    build_gene_repair_reviewer,
    build_gene_repairer,
)
from aus_trial_universe.tasks.eligibility.mapping.gene_alteration.checks import gating_problems, semantic_problems
from aus_trial_universe.tasks.eligibility.mapping.finding_model import finding_model_problems
from aus_trial_universe.tasks.eligibility.mapping.schema import GroupReconciliation, ReviewVerdict

logger = logging.getLogger(__name__)

_ESCALATION = ("\n\n[ESCALATION-MODE] The writer is stuck. Also return `suggested_fix`: the exact corrected "
               "expression you would expect.")


@dataclass
class ValueOutcome:
    """One value's journey through stage 2 — every intermediate kept, for the comparison file and the log."""

    source: str
    stage1: str
    after_canon: str = ""
    after_repair: str = ""
    final: str = ""
    defects_in: list[str] = field(default_factory=list)
    defects_out: list[str] = field(default_factory=list)
    repaired: bool = False
    repair_rationale: str = ""
    group_id: str = ""
    group_rationale: str = ""
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# R0 / R5 — deterministic canonicalisation.
# --------------------------------------------------------------------------- #
def _safe(fn):
    """`fan_out` re-raises, which would abandon a whole batch for one bad value. Soft-fail per item instead."""
    def wrapped():
        try:
            return fn(), None
        except Exception as ex:                                  # noqa: BLE001 - deliberate per-item isolation
            logger.warning("item failed: %s", ex)
            return None, ex
    return wrapped


def canonicalise_or_keep(expression: str, notes: list[str] | None = None) -> str:
    """Canonicalise; on a parse failure keep the input untouched and note it (never silently drop meaning)."""
    try:
        return E.canonicalise(expression)
    except E.ExprError as ex:
        if notes is not None:
            notes.append(f"canonicalise failed, kept as-is: {ex}")
        return (expression or "").strip()


# --------------------------------------------------------------------------- #
# R0a — MECHANICAL fixes. A defect whose correction is a pure substitution must never reach an LLM.
#
# Measured why: handing `transcriptImpact.effects=SPLICE` to the repairer worked, but the repairer also re-derived
# the REST of the expression and added ~18 NOT() terms that the original had deliberately omitted (actionability-
# qualified exclusions, which our own rules say to drop because dropping the qualifier would over-exclude). A
# one-token substitution is not a judgement call; doing it in code is testable and cannot regress elsewhere.
# --------------------------------------------------------------------------- #
_MECHANICAL: list[tuple[str, re.Pattern, str, str]] = [
    ("splice_as_effect",
     re.compile(r"transcriptImpact\.effects\s*=\s*SPLICE\b"),
     "transcriptImpact.codingEffect=SPLICE",
     "SPLICE is a CodingEffect, not a VariantEffect"),
    ("hla_as_pharmacogenotype",
     re.compile(r"PharmocoGenotype\[(\s*gene\s*=\s*HLA[^\]]*)\]"),
     r"HlaAllele[\1]",
     "HLA belongs to the HlaAllele record, not PharmocoGenotype"),
]


def mechanical_fixes(expression: str) -> tuple[str, list[str]]:
    """Apply every pure-substitution correction. Returns (expression, [applied fix description, ...])."""
    out = expression or ""
    applied: list[str] = []
    for name, pattern, repl, why in _MECHANICAL:
        new = pattern.sub(repl, out)
        if new != out:
            applied.append(f"{name}: {why}")
            out = new
    return out, applied


# --------------------------------------------------------------------------- #
# R3 — grouping. Two rules, both deterministic and both narrow enough to be defensible.
# --------------------------------------------------------------------------- #
# Qualifiers finding-model cannot express. Two sources that differ ONLY by these mean the same thing, so their
# mappings MUST be identical. Deliberately excludes polarity words ("positive", "negative", "wild-type", "no",
# "without"), which DO change the meaning.
#
# ⚠ "activating" / "sensitising" / "sensitizing" are DELIBERATELY ABSENT. They look like the same kind of word as
# "documented" or "deleterious", and they are not: they name a CLASS WITH ESTABLISHED MEMBERSHIP (for EGFR, the
# classical sensitising set — exon 19 deletion, L858R, G719X, L861Q, S768I), so they have an expressible referent
# and change the meaning. Including them here cost a real defect: `EGFR mutation`, `sensitizing EGFR mutation` and
# `activating EGFR mutation` normalised to one concept, the adjudicator dutifully unified them, and the sensitising
# expansion collapsed back to the bare gene — with `NOT(EGFR activating mutation)` becoming
# `NOT(SmallVariant[gene=EGFR])`, exactly the over-exclusion the mapping rules forbid. Neither the engine dry run
# nor the regression detector could see it (both spellings parse, and production had the collapsed form too).
_DROPPABLE = [
    "actionable", "oncogenic", "driver", "pathogenic", "likely pathogenic",
    "deleterious or suspected deleterious", "deleterious", "suspected",
    "known", "documented", "confirmed", "qualifying", "eligible", "clinically relevant", "detected",
    "germline or somatic", "germline", "somatic", "tumour", "tumor", "central", "local",
    "by ngs", "by ctdna", "by ish", "by fish", "by pcr", "by sequencing", "by immunohistochemistry",
    "per local testing", "measurable", "molecular", "genomic", "genetic", "any", "at least one",
]
_PUNCT = re.compile(r"[^a-z0-9+*:]+")


def concept_key(source: str) -> str:
    """Normalise a source wording to its inexpressible-qualifier-free 'concept'.

    Two values with the SAME concept key must map identically — that is the whole basis of grouping rule A.
    """
    s = (source or "").lower()
    for w in _DROPPABLE:
        s = s.replace(w, " ")
    s = _PUNCT.sub(" ", s)
    return " ".join(s.split())


def find_groups(mapped: dict[str, str]) -> list[tuple[str, list[str]]]:
    """Groups of values that SHOULD agree but do not. Returns [(group_id, [source, ...]), ...].

    ONE rule: same concept key, different expression. Once inexpressible qualifiers are stripped, the sources say
    the same thing, so any difference in output is an inconsistency — and nothing weaker than that is safe to
    hand an adjudicator.

    A REJECTED rule, recorded so it is not reinvented: grouping by "gene X appears with both wild-type spellings
    somewhere in the corpus" (`Wildtype[gene=X]` in one value, `NOT(SmallVariant[gene=X])` in another). It looks
    like it targets a real inconsistency and does not: measured over 909 values it produced 14 groups spanning 173
    values, almost all of which were unrelated criteria that merely mention the same gene — e.g. "EGFR wild type"
    grouped with "ALK fusion positivity AND NOT(EGFR resistance mutation)". Worse, the two spellings are usually
    both CORRECT, because they render different source claims: "EGFR wild-type" (no alteration of any kind) is
    `Wildtype[gene=EGFR]`, whereas "NOT(EGFR mutation)" (an amplification would still be allowed) is
    `NOT(SmallVariant[gene=EGFR])`. Handing such a group to an LLM to "reconcile" invites it to erase a real
    distinction. Grouping must be driven by the SOURCE meaning, never by an incidental gene overlap.
    """
    groups: list[tuple[str, list[str]]] = []
    by_concept: dict[str, list[str]] = {}
    for src in mapped:
        by_concept.setdefault(concept_key(src), []).append(src)
    for i, (_key, members) in enumerate(sorted(by_concept.items())):
        if len(members) > 1 and len({mapped[m] for m in members}) > 1:
            groups.append((f"A{i}", sorted(members)))
    return groups


# --------------------------------------------------------------------------- #
# R2 — per-value LLM repair (doer -> reviewer -> bounded refine).
# --------------------------------------------------------------------------- #
def repair_value(client: LlmClient, source: str, expression: str, defects: list[str], *,
                 max_attempts: int = 3) -> tuple[str, str, bool]:
    """Repair one value against its own defect list. Returns (expression, rationale, ok)."""
    repairer = build_gene_repairer(client)
    reviewer = build_gene_repair_reviewer(client)
    defect_txt = "\n".join(f"- {d}" for d in defects)
    base = (f"SOURCE: {source}\n\nCURRENT finding_model: {expression}\n\nDEFECTS to fix:\n{defect_txt}")

    def produce(feedback: str = "", prior=None):
        if not feedback:
            return repairer(base)
        prior_txt = f"\n\n[Your previous repair]:\n{prior.finding_model}" if prior is not None else ""
        return repairer(f"{base}{prior_txt}\n\n[Reviewer feedback — fix ONLY these]:\n{feedback}")

    def check(cand, escalate: bool = False) -> CheckResult:
        syn = finding_model_problems(cand.finding_model)
        if syn:
            return CheckResult(ok=False, problems=[f"invalid syntax: {p}" for p in syn])
        still = gating_problems(source, cand.finding_model)
        if still:
            return CheckResult(ok=False, problems=[f"defect not resolved: {p}" for p in still])
        # SCOPE GUARD. A repairer left to itself re-derives the whole mapping: asked only to move SPLICE to the
        # right field, it also added eighteen NOT() terms the original had deliberately omitted. A repair may
        # REMOVE literals (that is what fixing a redundancy means) but may never introduce a gene the current
        # expression did not already mention.
        new_genes = E.genes_of(cand.finding_model) - E.genes_of(expression)
        if new_genes:
            return CheckResult(ok=False, problems=[
                f"out of scope: the repair introduced gene(s) {', '.join(sorted(new_genes))} that the current "
                f"expression does not contain. Fix ONLY the listed defects and leave every other term verbatim."])
        v: ReviewVerdict = reviewer(
            f"SOURCE: {source}\n\nDEFECTS that prompted the repair:\n{defect_txt}\n\n"
            f"PROPOSED corrected finding_model: {cand.finding_model}" + (_ESCALATION if escalate else "")
        )
        if not v.faithful:
            gate = v.problems or ["reviewer rejected the repair"]
            if escalate and (v.suggested_fix or "").strip():
                gate = gate + [f"SUGGESTED FIX: {v.suggested_fix.strip()}"]
            return CheckResult(ok=False, problems=gate)
        return CheckResult(ok=True)

    res = review_refine(produce, check, max_attempts=max_attempts, escalate=True)
    return res.value.finding_model.strip(), (res.value.rationale or "").strip(), res.ok


# --------------------------------------------------------------------------- #
# R4 — group adjudication (doer -> reviewer -> bounded refine).
# --------------------------------------------------------------------------- #
def reconcile_group(client: LlmClient, members: list[tuple[str, str]], *,
                    max_attempts: int = 3) -> tuple[dict[str, str], str, bool]:
    """Adjudicate one group. `members` = [(source, expression), ...]. Returns ({source: final}, rationale, ok)."""
    reconciler = build_gene_reconciler(client)
    reviewer = build_gene_reconcile_reviewer(client)
    rendered = "\n".join(f"- input: {s}\n  current: {e or '(empty)'}" for s, e in members)
    base = f"GROUP ({len(members)} members whose sources describe the same requirement):\n{rendered}"
    known = {s for s, _ in members}

    def produce(feedback: str = "", prior=None):
        if not feedback:
            return reconciler(base)
        prior_txt = ""
        if prior is not None:
            prior_txt = "\n\n[Your previous answer]:\n" + "\n".join(
                f"- {m.input} -> {m.final_value}" for m in prior.members)
        return reconciler(f"{base}{prior_txt}\n\n[Reviewer feedback — fix ONLY these]:\n{feedback}")

    def check(cand: GroupReconciliation, escalate: bool = False) -> CheckResult:
        problems: list[str] = []
        got = {m.input for m in cand.members}
        missing = known - got
        extra = got - known
        if missing:
            problems.append(f"missing member(s): {'; '.join(sorted(missing))[:300]} — return every input verbatim")
        if extra:
            problems.append(f"unknown member(s) invented: {'; '.join(sorted(extra))[:300]}")
        for m in cand.members:
            syn = finding_model_problems(m.final_value)
            if syn:
                problems.append(f"invalid syntax for {m.input!r}: {syn[0]}")
        if problems:
            return CheckResult(ok=False, problems=problems)
        v: ReviewVerdict = reviewer(
            base + "\n\nPROPOSED final values:\n"
            + "\n".join(f"- {m.input} -> {m.final_value}" for m in cand.members)
            + (_ESCALATION if escalate else "")
        )
        if not v.faithful:
            gate = v.problems or ["reviewer rejected the reconciliation"]
            if escalate and (v.suggested_fix or "").strip():
                gate = gate + [f"SUGGESTED FIX: {v.suggested_fix.strip()}"]
            return CheckResult(ok=False, problems=gate)
        return CheckResult(ok=True)

    res = review_refine(produce, check, max_attempts=max_attempts, escalate=True)
    out = {m.input: (m.final_value or "").strip() for m in res.value.members if m.input in known}
    return out, (res.value.rationale or "").strip(), res.ok


# --------------------------------------------------------------------------- #
# The stage.
# --------------------------------------------------------------------------- #
def reconcile_column(client, mapping: dict[str, str], *, workers: int, max_attempts: int,
                     use_reviewer: bool) -> tuple[dict[str, str], list[tuple[str, str, list[str]]], int]:
    """Adapter onto the shared `mapping.reconcile.reconcile_column` contract.

    The shared driver dispatches here when `column == GENE_ALTERATION`, because gene-alteration stage 2 is a
    different pipeline from OncoTree's: its grouping is by SOURCE CONCEPT (see `find_groups`) rather than by
    `mapping.consistency.find_inconsistencies`, and its deterministic layer is the finding-model canonical form
    rather than the OncoTree one.

    Returns (final {value -> FINAL}, unresolved, n_groups) — `unresolved` being the values that still carry an
    error-severity defect after repair, which is the residue a human must look at.
    """
    outcomes = run_stage2(client, mapping, workers=workers, use_llm=client is not None,
                          max_attempts=max_attempts, use_reviewer=use_reviewer)
    refined = {s: o.final for s, o in outcomes.items()}
    unresolved = [
        (s, mapping.get(s, ""), [d for d in o.defects_out if d.startswith("[error]")])
        for s, o in outcomes.items() if any(d.startswith("[error]") for d in o.defects_out)
    ]
    return refined, unresolved, len({o.group_id for o in outcomes.values() if o.group_id})


def run_stage2(client: LlmClient | None, stage1: dict[str, str], *, workers: int = 8,
               use_llm: bool = True, max_attempts: int = 3,
               use_reviewer: bool = True) -> dict[str, ValueOutcome]:
    """Run R0-R5 over {source: stage-1 expression}. `use_llm=False` runs the deterministic layers only."""
    out = {s: ValueOutcome(source=s, stage1=e) for s, e in stage1.items()}

    # R0a mechanical substitutions, R0b canonicalise, R1 detect
    n_mech = 0
    for s, o in out.items():
        fixed, applied = mechanical_fixes(o.stage1)
        if applied:
            n_mech += 1
            o.notes.extend(f"mechanical fix — {a}" for a in applied)
        o.after_canon = canonicalise_or_keep(fixed, o.notes)
        o.defects_in = [str(f) for f in semantic_problems(s, o.after_canon)]
        o.after_repair = o.after_canon
    logger.info("R0a mechanical · %d value(s) fixed by substitution (no LLM)", n_mech)
    logger.info("R0b canonicalise · %d value(s); %d changed overall", len(out),
                sum(1 for o in out.values() if o.after_canon != o.stage1))
    n_err = sum(1 for o in out.values() if any(d.startswith("[error]") for d in o.defects_in))
    logger.info("R1 detect · %d value(s) carry an error-severity defect", n_err)

    # R2 — repair only what the catalogue gates on.
    todo = [(s, o) for s, o in out.items() if any(d.startswith("[error]") for d in o.defects_in)]
    if todo and use_llm and client is not None:
        logger.info("R2 repair · %d value(s)", len(todo))
        results = fan_out(
            [_safe(lambda s=s, o=o: repair_value(client, s, o.after_canon,
                                                 [d for d in o.defects_in if d.startswith("[error]")],
                                                 max_attempts=max_attempts))
             for s, o in todo],
            max_workers=workers,
        )
        for (s, o), (r, exc) in zip(todo, results):
            if r is None:
                o.notes.append(f"repair failed: {exc}")
                continue
            expression, rationale, ok = r
            o.after_repair = canonicalise_or_keep(expression, o.notes)
            o.repair_rationale = rationale
            o.repaired = True
            if not ok:
                o.notes.append("repair loop did not converge; best attempt kept")

    # R3 + R4
    current = {s: o.after_repair for s, o in out.items()}
    groups = find_groups(current)
    logger.info("R3 group · %d inconsistency group(s) over %d value(s)",
                len(groups), sum(len(m) for _, m in groups))
    if groups and use_llm and client is not None:
        logger.info("R4 adjudicate · %d group(s)", len(groups))
        results = fan_out(
            [_safe(lambda g=g: reconcile_group(client, [(m, current[m]) for m in g[1]],
                                              max_attempts=max_attempts)) for g in groups],
            max_workers=max(1, workers // 2),
        )
        for (gid, members), (r, exc) in zip(groups, results):
            if r is None:
                for m in members:
                    out[m].notes.append(f"group {gid} failed: {exc}")
                continue
            finals, rationale, ok = r
            for m in members:
                out[m].group_id = gid
                out[m].group_rationale = rationale
                if m in finals:
                    current[m] = finals[m]
                if not ok:
                    out[m].notes.append(f"group {gid} did not converge; best attempt kept")
    else:
        for gid, members in groups:
            for m in members:
                out[m].group_id = gid
                out[m].notes.append("group detected but not adjudicated (deterministic-only run)")

    # R5 — final canonical form, then any approved hand-ruling has the last word.
    for s, o in out.items():
        o.final = canonicalise_or_keep(current[s], o.notes)
        again = canonicalise_or_keep(o.final, o.notes)
        if again != o.final:
            o.notes.append(f"NOT IDEMPOTENT: {o.final!r} -> {again!r}")
        ruling = gene_rulings.APPROVED.get(s)
        if ruling is not None:                       # NB `""` is a legitimate ruling — test identity, not truth
            verdict = "match" if o.final == ruling.final else "override"
            o.notes.append(f"adjudication {verdict} ({ruling.approved})")
            o.final = ruling.final
        o.defects_out = [str(f) for f in semantic_problems(s, o.final)]
    logger.info("R5 finalise · %d value(s) still carry an error-severity defect",
                sum(1 for o in out.values() if any(d.startswith("[error]") for d in o.defects_out)))
    return out
