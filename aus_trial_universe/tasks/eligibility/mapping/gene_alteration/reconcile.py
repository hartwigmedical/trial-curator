"""STAGE 2 of gene-alteration mapping — CANONICALISATION. Deterministic, pure, single-value.

    R0a mechanical fixes   pure substitutions whose correction is not a judgement call
    R0b canonicalise       De Morgan, absorption, dedup, term/field/OR ordering, DNF
    R1  detect             the semantic defect catalogue (checks.py) — REPORTED, never auto-repaired
    R3  group              divergent concept groups — REPORTED as residue for the register
    R5  finalise           canonicalise to a fixed point; postcondition = the syntax gate

WHY THIS STAGE IS DETERMINISTIC-ONLY (2026-08-06, following the OncoTree stage-2 respec)
----------------------------------------------------------------------------------------
It used to hold two LLM layers, both now DELETED:

  R2  per-value repair       had EXACTLY stage 1's information set (the source wording plus the current
                             expression), so it was stage 1's job all along. Measured over the 913 live values it
                             fired ONCE, on `AGA negative`, whose output the register overrode anyway.
  R4  group adjudication     re-decided a whole group whenever group membership shifted, so a value with no defect
                             of its own could be rewritten because an unrelated value entered the corpus. That is
                             the churn mechanism that silently re-rolled 7 cancer_type values on 2026-08-05. It
                             found 0 groups over the 913 live values.

Net effect of removing both: ZERO change to the shipped corpus, and a stage that is now a pure function of a SINGLE
value — so churn is structurally impossible rather than merely mitigated.

DETECTION SURVIVES, ADJUDICATION DOES NOT. `find_groups` and the defect catalogue still run, and what they find is
LOGGED as residue. Anything they surface that stage 1's prompt cannot fix belongs in `qa/adjudications/
gene_alteration.py` — a reviewed human ruling, not an LLM re-decision. `concept_key` additionally backs
`qa/invariants.py` classes C1/C2/C6, so it is load-bearing beyond this module.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from aus_trial_universe.tasks.eligibility.mapping.adjudications import gene_alteration as gene_rulings
from aus_trial_universe.tasks.eligibility.mapping.gene_alteration import expr as E
from aus_trial_universe.tasks.eligibility.mapping.gene_alteration.checks import semantic_problems

logger = logging.getLogger(__name__)


@dataclass
class ValueOutcome:
    """One value's journey through stage 2 — every intermediate kept, for the comparison file and the log."""

    source: str
    stage1: str
    after_canon: str = ""
    final: str = ""
    defects_in: list[str] = field(default_factory=list)
    defects_out: list[str] = field(default_factory=list)
    group_id: str = ""
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# R0 / R5 — deterministic canonicalisation.
# --------------------------------------------------------------------------- #
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

# WORD-BOUNDED, and longest-alternative-first so "likely pathogenic" wins over "pathogenic".
# The previous implementation used a bare `s.replace(w, " ")` per droppable, i.e. an UNBOUNDED substring match, and
# the 2026-08-05 invariant sweep found it mangling 23 live values — including two meaning inversions, which is the
# dangerous kind: "ineligible" -> "in" (dropping "eligible") and "unknown" -> "un" (dropping "known"). A value
# saying a marker is UNKNOWN could therefore collide with one saying it is KNOWN.
_DROPPABLE_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(w) for w in sorted(_DROPPABLE, key=len, reverse=True)) + r")\b")

#: Same fixed-point reasoning as `consistency._MAX_NORMALISE_PASSES`: removing one droppable can make two others
#: adjacent ("by central NGS" -> "by NGS" -> ""), so a single pass is not stable.
_MAX_NORMALISE_PASSES = 4


def concept_key(source: str) -> str:
    """Normalise a source wording to its inexpressible-qualifier-free 'concept'.

    Two values with the SAME concept key must map identically — that is the whole basis of grouping rule A.
    Idempotent: applied to its own output it is a no-op.
    """
    s = (source or "").lower()
    for _ in range(_MAX_NORMALISE_PASSES):
        nxt = " ".join(_PUNCT.sub(" ", _DROPPABLE_RE.sub(" ", s)).split())
        if nxt == s:
            break
        s = nxt
    return s


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
# The stage.
# --------------------------------------------------------------------------- #
def reconcile_column(client, mapping: dict[str, str], *, workers: int, max_attempts: int,
                     use_reviewer: bool) -> tuple[dict[str, str], list[tuple[str, str, list[str]]], int]:
    """Adapter onto the shared `mapping.reconcile.reconcile_column` contract.

    The shared driver dispatches here when `column == GENE_ALTERATION`, because gene-alteration stage 2 is not
    OncoTree's: its deterministic layer is the finding-model canonical form, and its residue signal is grouping by
    SOURCE CONCEPT (`find_groups`) rather than `mapping.consistency.find_inconsistencies`.

    `client`, `workers`, `max_attempts` and `use_reviewer` are accepted and IGNORED — the stage makes no API calls.
    They stay in the signature because the shared driver calls every column the same way; dropping them here would
    make the dispatch asymmetric for no gain.

    Returns (final {value -> FINAL}, unresolved, n_groups). `unresolved` = values still carrying an error-severity
    defect, and `n_groups` = divergent concept groups; both are RESIDUE FOR A HUMAN, since nothing here repairs.
    """
    outcomes = run_stage2(mapping)
    refined = {s: o.final for s, o in outcomes.items()}
    unresolved = [
        (s, mapping.get(s, ""), [d for d in o.defects_out if d.startswith("[error]")])
        for s, o in outcomes.items() if any(d.startswith("[error]") for d in o.defects_out)
    ]
    return refined, unresolved, len({o.group_id for o in outcomes.values() if o.group_id})


def run_stage2(stage1: dict[str, str]) -> dict[str, ValueOutcome]:
    """Apply stage 2 to every value. Pure: no LLM, no network, no store access — safe to call from a test.

    Idempotent by construction; `qa/invariants.py` C2 asserts that over the whole live corpus rather than trusting
    the fixed-point loop inside `canonicalise`.
    """
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
    logger.info("R0a mechanical · %d value(s) fixed by substitution", n_mech)
    logger.info("R0b canonicalise · %d value(s); %d changed overall", len(out),
                sum(1 for o in out.values() if o.after_canon != o.stage1))
    n_err = sum(1 for o in out.values() if any(d.startswith("[error]") for d in o.defects_in))
    logger.info("R1 detect · %d value(s) carry an error-severity defect (reported, NOT repaired here)", n_err)

    # R3 — divergent concept groups are DETECTED and reported. They are not adjudicated: an LLM re-deciding a
    # group is the churn mechanism this stage was rewritten to remove. A group that is genuinely wrong is fixed
    # in stage 1's prompt, or ruled on in stage 3.
    current = {s: o.after_canon for s, o in out.items()}
    groups = find_groups(current)
    logger.info("R3 group · %d divergent concept group(s) over %d value(s) — residue for stage 1 / the register",
                len(groups), sum(len(m) for _, m in groups))
    for gid, members in groups:
        for m in members:
            out[m].group_id = gid
            out[m].notes.append(f"divergent concept group {gid} (detected only)")

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
