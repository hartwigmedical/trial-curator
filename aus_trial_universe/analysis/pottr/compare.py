"""COMPONENT 1b — POTTR's curation against ours, over the 269 shared trials.

TWO QUESTIONS, DELIBERATELY SEPARATED. Conflating them is what made the v1 comparison hard to read.

**Q1 — how each side DISAGGREGATES.** POTTR has no arm concept: 193 of the 269 shared trials are ONE POTTR row
against several arms of ours. So we back our side out to trial grain and deduplicate — on the MAPPED values, not
the free text, because arm-specific phrasing ("advanced NSCLC" vs "locally advanced or metastatic non-small cell
lung cancer") is not a semantic difference and POTTR's terms are a controlled vocabulary anyway. Measured, that
takes our 3,955 export rows to ~1,437 and lifts trials whose conjunction count matches POTTR from 9 to ~90.
The verdict is computed on the COHORT-DEFINING axes only (cancer type, gene, signature, biomarker): POTTR does
not split rows on prior therapy, so including it would make the shape verdict hostage to therapy phrasing.

**Q2 — whether the CRITERIA agree.** Compared as per-trial UNIONS per column, which sidesteps grain entirely —
how criteria are bundled into conjunctions is Q1's business. Three columns have a controlled vocabulary on both
sides after the crosswalk, so their comparison is DETERMINISTIC, via the production canonicalisers
(`cancer_type.expr.canonical_string` / `positive_codes`, `gene_alteration.expr.same_meaning` / `literal_set`).
The other two — molecular_biomarker (IHC) and prior_therapy — have no target vocabulary, so an LLM aligns them.

**The mistake verdict is grounded in the REGISTRY TEXT**, read out of `arm_eligibility_raw.tsv`. Adjudicating the
two curations against each other would just be two opinions; against the source it is a finding. POTTR is a peer,
not ground truth — its own file contains a wild-type-GIST row naming PDGFRB where the entity is defined by PDGFRA.

Component 2 feeds in here: our gene-alteration set for a trial is the union of `gene_alteration_findingmodel` AND
the disease-derived finding models. Without that, POTTR's disease-implied genetics would score as criteria we
lack, when in fact we record them in the cancer-type column.
"""
from __future__ import annotations

import csv
import logging
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

from aus_trial_universe.analysis.pottr.agents import build_freetext_aligner, build_mistake_judge
from aus_trial_universe.analysis.pottr.crosswalk import read_crosswalk
from aus_trial_universe.analysis.pottr.disease_inference import read_map as read_derived_map
from aus_trial_universe.analysis.pottr.paths import (
    COMPARISON_DETAIL, COMPARISON_HEADLINE, DISEASE_DERIVED_MAP, OURS_TRIAL_GRAIN,
)
from aus_trial_universe.analysis.pottr.pottr_source import (
    CANCER_TYPE, CRITERION_COLUMNS, GENE_ALTERATION, MOLECULAR_BIOMARKER, MOLECULAR_SIGNATURE, PRIOR_THERAPY,
    PottrRow, Term, load_rows,
)
from aus_trial_universe.core.client import LlmClient
from aus_trial_universe.core.paths import ELIGIBILITY_OUTPUT, current_version_dir
from aus_trial_universe.core.workflow import fan_out
from aus_trial_universe.tasks.eligibility.mapping.cancer_type import expr as ct_expr
from aus_trial_universe.tasks.eligibility.mapping.cancer_type import vocab as ct_vocab
from aus_trial_universe.tasks.eligibility.mapping.gene_alteration import expr as fm_expr
from aus_trial_universe.tasks.eligibility.mapping.workflow import strip_provenance

logger = logging.getLogger(__name__)

#: export column -> (free-text column, mapped column). The two unmapped columns carry "" as their mapped column.
EXPORT_COLUMNS = {
    CANCER_TYPE: ("cancer_type_interpreted", "oncotree_code"),
    GENE_ALTERATION: ("gene_alteration_interpreted", "gene_alteration_findingmodel"),
    MOLECULAR_SIGNATURE: ("molecular_signature_interpreted", "molecular_signature_findingmodel"),
    MOLECULAR_BIOMARKER: ("molecular_biomarker_interpreted", ""),
    PRIOR_THERAPY: ("prior_therapy_interpreted", ""),
}
#: Columns whose comparison is deterministic (a controlled vocabulary exists on both sides after the crosswalk).
DETERMINISTIC_COLUMNS = (CANCER_TYPE, GENE_ALTERATION, MOLECULAR_SIGNATURE)
#: Columns needing the LLM aligner.
JUDGED_COLUMNS = (MOLECULAR_BIOMARKER, PRIOR_THERAPY)
#: The axes both sides actually split cohorts on — prior therapy is an attribute of a row, not a splitting axis.
COHORT_AXES = (CANCER_TYPE, GENE_ALTERATION, MOLECULAR_SIGNATURE, MOLECULAR_BIOMARKER)


# --------------------------------------------------------------------------- #
# Canonical literals — the deterministic half of Q2
# --------------------------------------------------------------------------- #
def literals(column: str, expression: str) -> set[str]:
    """A mapped expression -> its set of SIGNED canonical atoms, for set-difference comparison.

    Signed, because an exclusion is a criterion. The finding-model `literal_set` already carries negation in its
    literals (`Not(child=Term(...))`), but the OncoTree side's `positive_codes` by definition does not — so the
    negated codes are collected here from the same parse tree and prefixed. Without that, `NOT catype:Melanoma`
    would compare as no criterion at all and every exclusion disagreement would be invisible.

    A parse failure falls back to the raw string rather than an empty set: dropping an unparseable expression
    would silently report the criterion as absent from that side, which is the one outcome we cannot allow.
    """
    e = (expression or "").strip()
    if not e:
        return set()
    try:
        if column == CANCER_TYPE:
            node, table = ct_expr.parse(e)
            return {("NOT " if neg else "") + (ct_expr.classify(a.text, table)[1] or a.text)
                    for a, neg in ct_expr.atoms(node)}
        return set(fm_expr.literal_set(e))
    except Exception:                                     # noqa: BLE001 — any parse defect, same safe fallback
        return {f"<unparsed> {e}"}


def equivalent(column: str, a: str, b: str) -> bool:
    """Do two mapped expressions mean the same thing? Uses the production canonicalisers, so a De Morgan
    rewrite or a different OR ordering counts as identical rather than as a difference."""
    a, b = (a or "").strip(), (b or "").strip()
    if a == b:
        return True
    if not a or not b:
        return False
    try:
        if column == CANCER_TYPE:
            return ct_expr.canonical_string(a) == ct_expr.canonical_string(b)
        return fm_expr.same_meaning(a, b)
    except Exception:                                     # noqa: BLE001
        return False


# --------------------------------------------------------------------------- #
# The two sides
# --------------------------------------------------------------------------- #
@dataclass
class Side:
    """One trial's criteria from one curation, as per-column unions plus the conjunction shapes."""

    #: column -> the source-level values (our interpreted text / POTTR's rendered term, with polarity)
    values: dict[str, list[str]] = field(default_factory=lambda: {c: [] for c in CRITERION_COLUMNS})
    #: column -> the mapped expressions
    mapped: dict[str, list[str]] = field(default_factory=lambda: {c: [] for c in CRITERION_COLUMNS})
    #: column -> canonical literal set (deterministic columns only)
    lits: dict[str, set[str]] = field(default_factory=lambda: {c: set() for c in CRITERION_COLUMNS})
    #: the distinct conjunctions, each a tuple of per-axis mapped signatures — Q1's material
    conjunctions: set[tuple] = field(default_factory=set)
    #: POTTR only: values carried by a SOFT (`*`) term, so a soft-only difference is separable
    soft_values: set[str] = field(default_factory=set)
    arms: set[str] = field(default_factory=set)
    rows: int = 0

    def add(self, column: str, value: str, mapped: str, *, soft: bool = False) -> None:
        v = (value or "").strip()
        if not v:
            return
        if v not in self.values[column]:
            self.values[column].append(v)
        m = (mapped or "").strip()
        if m and m not in self.mapped[column]:
            self.mapped[column].append(m)
        if m:
            self.lits[column] |= literals(column, m)
        elif column in DETERMINISTIC_COLUMNS:
            # A criterion the vocabulary could not express. Contributing NOTHING would report it as absent from
            # this side — a false agreement. Carry it as a labelled literal so it shows up in the diff instead.
            self.lits[column].add(f"<unmapped> {v}")
        if soft:
            self.soft_values.add(v)


def build_our_side(export_rows: list[dict], derived: dict) -> dict[str, Side]:
    """Back our export out to trial grain and deduplicate on the MAPPED values.

    The disease-derived alteration (component 2) is folded into the gene column here — that is the whole reason
    it had to be built first.
    """
    sides: dict[str, Side] = defaultdict(Side)
    for r in export_rows:
        tid = r["trialId"]
        s = sides[tid]
        s.rows += 1
        s.arms.add(r["trial_arm_id"])
        for column, (free_col, mapped_col) in EXPORT_COLUMNS.items():
            s.add(column, strip_provenance(r.get(free_col, "")), r.get(mapped_col, "") if mapped_col else "")
        # component 2: the genetics carried by the cancer-type wording
        d = derived.get(strip_provenance(r.get("cancer_type_interpreted", "")))
        if d and d.derived_alteration:
            s.add(GENE_ALTERATION, f"[disease-derived] {d.derived_alteration}", d.finding_model)
        s.conjunctions.add(tuple(
            (r.get(EXPORT_COLUMNS[c][1], "") or r.get(EXPORT_COLUMNS[c][0], "")).strip() for c in COHORT_AXES
        ))
    return dict(sides)


def _render_term(t: Term) -> str:
    """A POTTR term as a readable criterion, polarity and softness included — this string is what the LLM
    aligner and the human reader both see, so it must not hide either flag."""
    body = " OR ".join(a.value for a in t.atoms)
    if t.negated or any(a.negated for a in t.atoms):
        body = f"NOT({body})"
    return ("*" if t.soft else "") + body


def build_pottr_side(rows: list[PottrRow], crosswalk: dict) -> dict[str, Side]:
    """POTTR's rows as per-column unions plus conjunction signatures.

    ⚠ AN OR-GROUP IS FILED PER ATOM, NOT PER TERM. 27 groups in the current file span two of our columns, and
    they are not exotic — `(ERBB2:amplification OR ERBB2:overexpression)` is the standard HER2-positive
    definition, one arm of which is a copy-number event (gene_alteration) and the other an IHC readout
    (molecular_biomarker). Filing the whole group under the first atom's column would misfile the other half and
    report a difference wherever we happened to record it in the other column. The OR itself is not lost: the
    conjunction signature below keeps the term intact, which is where logical shape belongs (Q1).
    """
    sides: dict[str, Side] = defaultdict(Side)
    for row in rows:
        s = sides[row.trial_id]
        s.rows += 1
        conj: dict[str, list[str]] = defaultdict(list)
        for t in row.criteria():
            by_column: dict[str, list[str]] = defaultdict(list)
            for a in t.atoms:
                # A term can mix criterion atoms with an annotation one — the malformed NCT05872295 group holds
                # a stray token beside two real criteria. Only criterion atoms are filed.
                if a.column not in CRITERION_COLUMNS:
                    continue
                m = crosswalk[a.key()].mapped_value if a.key() in crosswalk else ""
                by_column[a.column].append(f"NOT({m})" if (a.negated and m) else m)
            for column, parts in by_column.items():
                mapped = " OR ".join(p for p in parts if p)
                if t.negated and mapped:
                    mapped = f"NOT({mapped})"
                # The value string names only the atoms that landed in THIS column, so a reader can see which
                # half of a split group they are looking at; the full term stays in `raw` on the parsed row.
                atoms_here = [a for a in t.atoms if a.column == column]
                rendered = _render_term(Term(atoms=tuple(atoms_here), negated=t.negated, soft=t.soft, raw=t.raw))
                s.add(column, rendered, mapped, soft=t.soft)
                conj[column].append(mapped or rendered)
        s.conjunctions.add(tuple(" & ".join(sorted(conj.get(c, []))) for c in COHORT_AXES))
    return dict(sides)


# --------------------------------------------------------------------------- #
# Output rows
# --------------------------------------------------------------------------- #
@dataclass
class DetailRow:
    trialId: str
    column: str
    side: str                  # both | pottr_only | ours_only
    pottr_value: str = ""
    pottr_mapped: str = ""
    our_value: str = ""
    our_mapped: str = ""
    soft: str = ""             # 'soft' when the POTTR term is `*`-marked
    basis: str = ""            # deterministic | judged
    detail: str = ""
    mistake_verdict: str = ""
    mistake_reason: str = ""
    correct_value: str = ""

    COLUMNS = ("trialId", "column", "side", "pottr_value", "pottr_mapped", "our_value", "our_mapped", "soft",
               "basis", "detail", "mistake_verdict", "mistake_reason", "correct_value")


@dataclass
class HeadlineRow:
    trialId: str
    registry: str
    overall_verdict: str = ""          # identical | differs
    dnf_difference: str = ""           # how the two disaggregations differ ("" when the shape matches)
    pottr_only_criteria: str = ""      # POTTR has, we do not
    ours_only_criteria: str = ""       # we have, POTTR does not
    shared_criteria: int = 0
    soft_only_difference: str = ""     # 'true' when every POTTR-only difference is a soft term
    mistake_verdict: str = ""          # the aggregated who-is-wrong
    mistake_detail: str = ""
    pottr_rows: int = 0
    our_arms: int = 0
    our_export_rows: int = 0
    our_conjunctions: int = 0

    COLUMNS = ("trialId", "registry", "overall_verdict", "dnf_difference", "pottr_only_criteria",
               "ours_only_criteria", "shared_criteria", "soft_only_difference", "mistake_verdict",
               "mistake_detail", "pottr_rows", "our_arms", "our_export_rows", "our_conjunctions")


# --------------------------------------------------------------------------- #
# Q1 — disaggregation
# --------------------------------------------------------------------------- #
def dnf_difference(pottr: Side, ours: Side) -> str:
    p, o = pottr.rows, len(ours.conjunctions)
    if p == o:
        return ""
    if o > p:
        extra = f"we split into {o} conjunction(s) against POTTR's {p}"
        if len(ours.arms) > 1 and p == 1:
            return f"{extra}; POTTR has no arm concept and we carry {len(ours.arms)} arms"
        return extra
    return f"POTTR splits into {p} row(s) against our {o} conjunction(s)"


# --------------------------------------------------------------------------- #
# Q2 — criteria
# --------------------------------------------------------------------------- #
def compare_deterministic(trial_id: str, column: str, pottr: Side, ours: Side) -> list[DetailRow]:
    """Set-compare one controlled-vocabulary column via canonical literals."""
    p_lits, o_lits = pottr.lits[column], ours.lits[column]
    p_expr = " ; ".join(pottr.mapped[column])
    o_expr = " ; ".join(ours.mapped[column])
    out: list[DetailRow] = []
    if not p_lits and not o_lits:
        return out
    # `equivalent` parses, so it only applies when each side is ONE expression; a ' ; '-joined list is not
    # grammar. Multi-value columns are settled by the literal sets, which is where the canonicalisation already
    # did its work.
    single = len(pottr.mapped[column]) == 1 and len(ours.mapped[column]) == 1
    if p_lits == o_lits or (single and equivalent(column, p_expr, o_expr)):
        return [DetailRow(trialId=trial_id, column=column, side="both", basis="deterministic",
                          pottr_value=" ; ".join(pottr.values[column]), pottr_mapped=p_expr,
                          our_value=" ; ".join(ours.values[column]), our_mapped=o_expr,
                          detail=f"{len(p_lits)} shared canonical atom(s)")]
    p_extra, o_extra = p_lits - o_lits, o_lits - p_lits
    granularity = _granularity_pairs(column, p_extra, o_extra) if column == CANCER_TYPE else []
    for p_code, o_code, direction in granularity:
        p_extra.discard(p_code)
        o_extra.discard(o_code)
        out.append(DetailRow(trialId=trial_id, column=column, side="both", basis="deterministic",
                             pottr_mapped=p_code, our_mapped=o_code,
                             pottr_value=" ; ".join(pottr.values[column]),
                             our_value=" ; ".join(ours.values[column]),
                             detail=f"same disease at different granularity — POTTR's {p_code} is the "
                                    f"{direction} of our {o_code}"))
    shared = p_lits & o_lits
    if shared:
        out.append(DetailRow(trialId=trial_id, column=column, side="both", basis="deterministic",
                             pottr_mapped=" | ".join(sorted(shared)), our_mapped=" | ".join(sorted(shared)),
                             detail=f"{len(shared)} shared canonical atom(s)"))
    if p_extra:
        out.append(DetailRow(trialId=trial_id, column=column, side="pottr_only", basis="deterministic",
                             pottr_value=" ; ".join(pottr.values[column]), pottr_mapped=" | ".join(sorted(p_extra)),
                             our_value=" ; ".join(ours.values[column]), our_mapped=o_expr,
                             soft=_soft_flag(pottr, column),
                             detail=f"{len(p_extra)} atom(s) POTTR asserts and we do not"))
    if o_extra:
        out.append(DetailRow(trialId=trial_id, column=column, side="ours_only", basis="deterministic",
                             pottr_value=" ; ".join(pottr.values[column]), pottr_mapped=p_expr,
                             our_value=" ; ".join(ours.values[column]), our_mapped=" | ".join(sorted(o_extra)),
                             detail=f"{len(o_extra)} atom(s) we assert and POTTR does not"))
    return out


def _granularity_pairs(column: str, p_extra: set[str], o_extra: set[str]) -> list[tuple[str, str, str]]:
    """Codes on the two sides that name the SAME disease at different depths of the OncoTree.

    `BLADDER` vs `BLCA`, `PAAD` vs `PANCREAS` — the commonest shape of "difference" between the two curations,
    and not a disagreement at all: one side simply chose a finer node. Pairing them here, deterministically via
    the OncoTree hierarchy, keeps them out of the pottr_only / ours_only buckets, so those buckets mean what
    they say. Only codes with NO ancestral relation survive as real differences.
    """
    pairs: list[tuple[str, str, str]] = []
    taken: set[str] = set()
    for p in sorted(p_extra):
        if p.startswith(("NOT ", "<")):
            continue
        for o in sorted(o_extra):
            if o in taken or o.startswith(("NOT ", "<")):
                continue
            if ct_vocab.is_subcode(o, p):
                pairs.append((p, o, "ancestor"))
            elif ct_vocab.is_subcode(p, o):
                pairs.append((p, o, "descendant"))
            else:
                continue
            taken.add(o)
            break
    return pairs


def _soft_flag(pottr: Side, column: str) -> str:
    vals = pottr.values[column]
    return "soft" if vals and all(v in pottr.soft_values for v in vals) else ""


def compare_judged(client: LlmClient, trial_id: str, column: str, pottr: Side, ours: Side,
                   context: str) -> list[DetailRow]:
    """Align one free-text column with the LLM (no controlled vocabulary on either side)."""
    p_vals, o_vals = pottr.values[column], ours.values[column]
    if not p_vals and not o_vals:
        return []
    if not p_vals:
        return [DetailRow(trialId=trial_id, column=column, side="ours_only", basis="judged",
                          our_value=" ; ".join(o_vals), detail="POTTR records nothing in this column")]
    if not o_vals:
        return [DetailRow(trialId=trial_id, column=column, side="pottr_only", basis="judged",
                          pottr_value=" ; ".join(p_vals), soft=_soft_flag(pottr, column),
                          detail="we record nothing in this column")]

    aligner = build_freetext_aligner(client)
    prompt = (f"TRIAL: {trial_id}\nCOLUMN: {column}\nCANCER TYPE (context): {context}\n\n"
              f"POTTR's list:\n" + "\n".join(f"- {v}" for v in p_vals) +
              "\n\nOur list:\n" + "\n".join(f"- {v}" for v in o_vals))
    a = aligner(prompt)
    out = [DetailRow(trialId=trial_id, column=column, side="both", basis="judged",
                     pottr_value=p.pottr_term, our_value=p.our_value, detail=p.reason,
                     soft="soft" if p.pottr_term in pottr.soft_values else "")
           for p in a.aligned]
    out += [DetailRow(trialId=trial_id, column=column, side="pottr_only", basis="judged", pottr_value=v,
                      soft="soft" if v in pottr.soft_values else "",
                      our_value=" ; ".join(o_vals), detail="no counterpart in our curation")
            for v in a.pottr_only]
    out += [DetailRow(trialId=trial_id, column=column, side="ours_only", basis="judged", our_value=v,
                      pottr_value=" ; ".join(p_vals), detail="no counterpart in POTTR's curation")
            for v in a.ours_only]
    return out


# --------------------------------------------------------------------------- #
# Adjudication — grounded in the registry text
# --------------------------------------------------------------------------- #
def load_raw_text() -> dict[str, dict[str, str]]:
    """`trialId -> {column -> verbatim source text}`, pooled across the trial's arms."""
    path = current_version_dir(ELIGIBILITY_OUTPUT) / "arm_eligibility_raw.tsv"
    csv.field_size_limit(10 ** 9)
    out: dict[str, dict[str, str]] = defaultdict(lambda: defaultdict(str))
    if not path.exists():
        logger.warning("no arm_eligibility_raw.tsv — the mistake verdict will be unavailable")
        return {}
    with path.open(encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh, delimiter="\t"):
            tid = r["trial_arm_id"].split("::", 1)[0]
            for column in CRITERION_COLUMNS:
                cell = (r.get(f"{column}_raw") or "").strip()
                if cell and cell not in out[tid][column]:
                    out[tid][column] += (" | " if out[tid][column] else "") + cell
    return {k: dict(v) for k, v in out.items()}


def adjudicate(client: LlmClient, row: DetailRow, raw: dict[str, str]) -> DetailRow:
    # A WHOLE-COLUMN coverage gap needs no opinion: if the other side records nothing at all in this column for
    # this trial, it has made no claim and cannot be wrong about one. Settling these deterministically is what
    # stops the verdict counts being swamped — in the first run 382 of 418 `pottr_wrong` verdicts were really
    # "POTTR does not curate this", which is a completeness finding, not a factual one.
    if row.side == "ours_only" and not row.pottr_value.strip():
        row.mistake_verdict = "pottr_omission"
        row.mistake_reason = "POTTR records nothing in this column for this trial — a coverage gap, not an error"
        return row
    if row.side == "pottr_only" and not row.our_value.strip():
        row.mistake_verdict = "ours_omission"
        row.mistake_reason = "we record nothing in this column for this trial — a coverage gap, not an error"
        return row

    source = raw.get(row.column, "")
    if not source:
        row.mistake_verdict, row.mistake_reason = "undecidable", "no verbatim source text for this column"
        return row
    judge = build_mistake_judge(client)
    v = judge(
        f"TRIAL: {row.trialId}\nCOLUMN: {row.column}\n\n"
        f"VERBATIM REGISTRY TEXT for this column:\n{source[:12000]}\n\n"
        f"THE DIFFERENCE ({row.side}):\n"
        f"- POTTR records: {row.pottr_value or '(nothing)'}"
        f"{f'  [mapped: {row.pottr_mapped}]' if row.pottr_mapped else ''}\n"
        f"- We record:     {row.our_value or '(nothing)'}"
        f"{f'  [mapped: {row.our_mapped}]' if row.our_mapped else ''}\n"
        f"{'- NOTE: the POTTR term is SOFT (its matcher assumes it when unknown).' if row.soft else ''}"
    )
    row.mistake_verdict, row.mistake_reason, row.correct_value = v.verdict, v.reason, v.correct_value
    return row


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #
def run_comparison(client: LlmClient, export_rows: list[dict], *, workers: int = 8,
                   adjudicate_diffs: bool = True) -> tuple[list[HeadlineRow], list[DetailRow]]:
    """Both questions, then the two output files. The aligner and the judge are single-shot — there is no
    doer→reviewer loop here, because neither produces a value the pipeline goes on to consume."""
    crosswalk = read_crosswalk()
    if not crosswalk:
        raise SystemExit("no crosswalk on disk — run the `crosswalk` command first.")
    derived = read_derived_map(DISEASE_DERIVED_MAP)
    logger.info("crosswalk %d term(s) · disease-derived %d value(s) (%d fired)",
                len(crosswalk), len(derived), sum(1 for d in derived.values() if d.derived_alteration))

    pottr_rows = load_rows()
    pottr_sides = build_pottr_side(pottr_rows, crosswalk)
    our_sides = build_our_side(export_rows, derived)
    shared = sorted(set(pottr_sides) & set(our_sides))
    logger.info("POTTR trials %d · ours %d · SHARED %d", len(pottr_sides), len(our_sides), len(shared))
    write_our_trial_grain(our_sides, shared)

    # --- Q2 deterministic columns + Q1, all local ---------------------------------------------------------- #
    details: list[DetailRow] = []
    for tid in shared:
        p, o = pottr_sides[tid], our_sides[tid]
        for column in DETERMINISTIC_COLUMNS:
            details += compare_deterministic(tid, column, p, o)

    # --- Q2 judged columns, one LLM call per (trial, column) ------------------------------------------------ #
    jobs = [(tid, column) for tid in shared for column in JUDGED_COLUMNS
            if pottr_sides[tid].values[column] or our_sides[tid].values[column]]
    logger.info("free-text alignment · %d (trial, column) job(s) · %d workers", len(jobs), workers)
    judged = fan_out([
        (lambda tid=tid, c=column: compare_judged(
            client, tid, c, pottr_sides[tid], our_sides[tid],
            " ; ".join(our_sides[tid].values[CANCER_TYPE][:3])))
        for tid, column in jobs
    ], max_workers=workers)
    for batch in judged:
        details += batch

    # --- adjudication, only on SUBSTANTIVE differences ------------------------------------------------------ #
    if adjudicate_diffs:
        raw = load_raw_text()
        targets = [d for d in details if d.side != "both"]
        logger.info("adjudication · %d substantive difference(s) · grounded in the registry text", len(targets))
        fan_out([(lambda d=d: adjudicate(client, d, raw.get(d.trialId, {}))) for d in targets],
                max_workers=workers)

    headlines = [summarise(tid, pottr_sides[tid], our_sides[tid], [d for d in details if d.trialId == tid])
                 for tid in shared]
    write_detail(details)
    write_headline(headlines)
    _log_summary(headlines, details)
    return headlines, details


def summarise(trial_id: str, pottr: Side, ours: Side, details: list[DetailRow]) -> HeadlineRow:
    p_only = [d for d in details if d.side == "pottr_only"]
    o_only = [d for d in details if d.side == "ours_only"]
    shared_n = sum(1 for d in details if d.side == "both")
    # Only FACTUAL errors reach the headline verdict. Omissions and granularity are reported through the
    # pottr_only / ours_only columns, which is where a completeness question belongs.
    _not_errors = ("neither_wrong", "undecidable", "pottr_omission", "ours_omission")
    verdicts = [d.mistake_verdict for d in details if d.mistake_verdict
                and d.mistake_verdict not in _not_errors]
    reasons = [f"[{d.column}] {d.mistake_verdict}: {d.mistake_reason}" for d in details
               if d.mistake_verdict and d.mistake_verdict not in _not_errors]
    return HeadlineRow(
        trialId=trial_id,
        registry="anzctr" if trial_id.startswith("ACTRN") else "ctgov",
        overall_verdict="identical" if not p_only and not o_only else "differs",
        dnf_difference=dnf_difference(pottr, ours),
        pottr_only_criteria=" ; ".join(f"[{d.column}] {d.pottr_mapped or d.pottr_value}" for d in p_only),
        ours_only_criteria=" ; ".join(f"[{d.column}] {d.our_mapped or d.our_value}" for d in o_only),
        shared_criteria=shared_n,
        soft_only_difference="true" if p_only and all(d.soft == "soft" for d in p_only) else "false",
        mistake_verdict=("none" if not verdicts else
                         verdicts[0] if len(set(verdicts)) == 1 else "mixed"),
        mistake_detail=" ; ".join(reasons),
        pottr_rows=pottr.rows,
        our_arms=len(ours.arms),
        our_export_rows=ours.rows,
        our_conjunctions=len(ours.conjunctions),
    )


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def _write(path: Path, columns, rows) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(columns), delimiter="\t", lineterminator="\n",
                           extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: str(v) for k, v in asdict(r).items()})
    return path


def write_detail(rows: list[DetailRow], path: Path = COMPARISON_DETAIL) -> Path:
    return _write(path, DetailRow.COLUMNS, sorted(rows, key=lambda r: (r.trialId, r.column, r.side)))


def write_headline(rows: list[HeadlineRow], path: Path = COMPARISON_HEADLINE) -> Path:
    return _write(path, HeadlineRow.COLUMNS, sorted(rows, key=lambda r: r.trialId))


def write_our_trial_grain(sides: dict[str, Side], shared: list[str], path: Path = OURS_TRIAL_GRAIN) -> Path:
    """Our side as the comparison actually sees it — trial grain, deduplicated on the mapped values."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = ["trialId", "arms", "export_rows", "conjunctions"] + [f"{c}_values" for c in CRITERION_COLUMNS]
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, delimiter="\t", lineterminator="\n")
        w.writeheader()
        for tid in shared:
            s = sides[tid]
            row = {"trialId": tid, "arms": len(s.arms), "export_rows": s.rows,
                   "conjunctions": len(s.conjunctions)}
            row |= {f"{c}_values": " ; ".join(s.values[c]) for c in CRITERION_COLUMNS}
            w.writerow(row)
    return path


def _log_summary(headlines: list[HeadlineRow], details: list[DetailRow]) -> None:
    logger.info("")
    logger.info("TRIALS %d · identical %d · differs %d", len(headlines),
                sum(1 for h in headlines if h.overall_verdict == "identical"),
                sum(1 for h in headlines if h.overall_verdict == "differs"))
    logger.info("DNF shape matches on %d trial(s)", sum(1 for h in headlines if not h.dnf_difference))
    logger.info("soft-only differences on %d trial(s)",
                sum(1 for h in headlines if h.soft_only_difference == "true"))
    by_side = Counter(d.side for d in details)
    logger.info("criterion rows · both %d · pottr_only %d · ours_only %d",
                by_side["both"], by_side["pottr_only"], by_side["ours_only"])
    mv = Counter(d.mistake_verdict for d in details if d.mistake_verdict)
    if mv:
        logger.info("mistake verdicts · %s", " · ".join(f"{k} {v}" for k, v in mv.most_common()))
