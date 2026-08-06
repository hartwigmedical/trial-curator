"""PER-COLUMN CONFIGURATION for the prompt harness — what actually differs between vocabulary columns.

Added 2026-08-06, when gene_alteration became the harness's second customer. Only a few things vary per
column; every entry point in this package is column-independent once these are injected:

    the table shape   which TSV, which key column, which FINAL column
    the baselines     which reviewed states the candidate must beat
    `errs`            the deterministic defect signal for that vocabulary
    `direction`       how a CLEAN-but-CHANGED value is classified — the regression class a defect count cannot see

DIRECTION IS NOT THE SAME QUESTION IN EVERY COLUMN, and forcing one implementation on both would silently
mis-grade one of them. OncoTree has an ONTOLOGY, so "broader" means an ancestor node and the dangerous case is
broadening to a sentinel. Finding-model has NO hierarchy; "broader" means a term with FEWER fields (term
subsumption), and — crucially — the danger flips with POLARITY:

    an INCLUSION that broadens   -> a superset. The mapping rules explicitly prefer this when unsure: "a superset
                                    is acceptable, a lost patient is not."
    an EXCLUSION that broadens   -> an OVER-EXCLUSION, which silently denies a trial to patients carrying a
                                    harmless variant, and is invisible to the clinician reviewing the match.

So `broadened_exclusion` is the gene column's regression predicate, and it is exactly the failure mode the
2026-08-06 audit found in ~13 live values (a `NOT(actionable EGFR mutation)` clause collapsing to
`NOT(SmallVariant[gene=EGFR])`).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from aus_trial_universe.core.paths import ANALYSIS_DIR, DATA_ROOT, ELIGIBILITY_OUTPUT, current_version_dir
from aus_trial_universe.tasks.eligibility.mapping import stage_tables as _ST

BACKUPS = DATA_ROOT.parent / "backups"


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    key: str                                  # the TSV's key column
    final_col: str                            # the CURRENT store's shipped-value column
    #: The same column in an OLD backup, which predates the stage-table rename and still says `*_FINAL`.
    #: Kept explicit rather than guessed: a baseline read with the wrong column name returns blanks, and a
    #: blank baseline makes every value look FIXED.
    final_col_baseline: str
    table: str                                # the finalised map filename
    baselines: dict[str, Path]                # label -> path; the FIRST is the HARD gate
    out_dir: Path
    errs: Callable[[str, str], list[str]]     # (source, expression) -> error-severity defect ids
    direction: Callable[[str, str], str]      # (before, after) -> "" | a regression/change label
    register: str                             # module name under qa.adjudications


# --------------------------------------------------------------------------- #
# cancer_type — unchanged behaviour, just named.
# --------------------------------------------------------------------------- #
def _ct_errs(src: str, expr: str) -> list[str]:
    from aus_trial_universe.tasks.eligibility.mapping.cancer_type.vocab import expression_problems, source_problems
    return [p.defect for p in expression_problems(expr) + source_problems(src, expr) if p.severity == "error"]


def _ct_direction(before: str, after: str) -> str:
    from aus_trial_universe.tasks.eligibility.mapping.cancer_type.expr import broadening, narrows
    b = broadening(before, after)
    if b and b[0] == "sentinel":
        return "REGRESSED_BROADENED"
    if b:
        return "CHANGED_ANCESTOR"
    if narrows(before, after):
        return "CHANGED_NARROWER"
    return "CHANGED_LATERAL"


# --------------------------------------------------------------------------- #
# gene_alteration — polarity-aware direction over finding-model term subsumption.
# --------------------------------------------------------------------------- #
def _ga_errs(src: str, expr: str) -> list[str]:
    from aus_trial_universe.tasks.eligibility.mapping.gene_alteration.checks import semantic_problems
    return [f.check for f in semantic_problems(src, expr) if f.severity == "error"]


def _literals(expr: str):
    """(positive terms, negated terms) of an expression's canonical form. ([], []) if it will not parse."""
    from aus_trial_universe.tasks.eligibility.mapping.gene_alteration import expr as E
    try:
        node = E.parse(expr)
    except E.ExprError:
        return [], []
    if node is None:
        return [], []
    pos, neg = [], []
    for conj in E._to_dnf(E._flatten(E._push_not(E._flatten(node)))):
        for lit in conj:
            (neg if isinstance(lit, E.Not) else pos).append(lit)
    return pos, neg


def _child_terms(lits):
    """The `Term`s carried by a list of literals, unwrapping one level of Not/And."""
    from aus_trial_universe.tasks.eligibility.mapping.gene_alteration import expr as E
    out = []
    for l in lits:
        node = l.child if isinstance(l, E.Not) else l
        if isinstance(node, E.Term):
            out.append(node)
        elif isinstance(node, E.And):
            out.extend(p for p in node.parts if isinstance(p, E.Term))
    return out


def broadened_exclusion(before: str, after: str) -> bool:
    """True if `after` excludes something STRICTLY BROADER than `before` did — an over-exclusion.

    Formally: some negated term of `after` is implied by a negated term of `before` without being equal to it, i.e.
    `before` excluded the specific and `after` excludes the general. `NOT(SmallVariant[gene=EGFR & p.L858R])`
    becoming `NOT(SmallVariant[gene=EGFR])` rejects every patient with any EGFR variant, harmless ones included.
    """
    from aus_trial_universe.tasks.eligibility.mapping.gene_alteration.expr import implies
    _pb, nb = _literals(before)
    _pa, na = _literals(after)
    tb, ta = _child_terms(nb), _child_terms(na)
    return any(implies(b, a) and b != a for a in ta for b in tb)


def narrowed_inclusion(before: str, after: str) -> bool:
    """True if `after` REQUIRES something strictly more specific than `before` did — patients are lost."""
    from aus_trial_universe.tasks.eligibility.mapping.gene_alteration.expr import implies
    pb, _nb = _literals(before)
    pa, _na = _literals(after)
    tb, ta = _child_terms(pb), _child_terms(pa)
    return any(implies(a, b) and a != b for a in ta for b in tb)


def _negated_genes(expr: str) -> frozenset[str]:
    from aus_trial_universe.tasks.eligibility.mapping.gene_alteration import expr as E
    _pos, neg = _literals(expr)
    out: set[str] = set()
    for t in _child_terms(neg):
        out |= E.genes_of(t.render())
    return frozenset(out)


def _ga_direction(before: str, after: str) -> str:
    """Classify a clean-but-changed value. TWO tiers, and the split is the whole point.

    AUTO-GATED (unambiguously worse whatever the source says):
        BROADENED_EXCLUSION   the same gene, excluded less specifically -> rejects patients the trial accepts
        NARROWED_INCLUSION    a required term got more specific         -> patients lost

    REVIEW-REQUIRED (correctness depends on the source, so a machine must not decide):
        LOST_EXCLUSION        a gene is no longer excluded. RIGHT for an open resistance set ("MET kinase
                              inhibitor resistance mutation"), WRONG for an enumerated list. Judged per value.
        GAINED_EXCLUSION      a gene is now excluded. The expected shape of the G1 fix, but still read.

    ⚠ `LOST_EXCLUSION` was auto-failed in the first version of this classifier, on the assumption that losing an
    exclusion is always a regression. That was the same mistake the first G1 candidate made. It is not always a
    regression, so it is surfaced for judgement rather than gated — a gate that fires on a correct change trains
    the reviewer to ignore it.
    """
    if broadened_exclusion(before, after):
        return "REGRESSED_BROADENED_EXCLUSION"
    if narrowed_inclusion(before, after):
        return "REGRESSED_NARROWED_INCLUSION"
    if not (after or "").strip() and (before or "").strip():
        return "CHANGED_TO_EMPTY"
    if (after or "").strip() and not (before or "").strip():
        return "CHANGED_FROM_EMPTY"
    nb, na = _negated_genes(before), _negated_genes(after)
    if nb - na and not na - nb:
        return "LOST_EXCLUSION"
    if na - nb and not nb - na:
        return "GAINED_EXCLUSION"
    return "CHANGED_LATERAL"


def _sig_errs(src: str, expr: str) -> list[str]:
    """molecular_signature has no semantic defect catalogue — the gene one misfires on it wholesale (51 of its
    171 values trip `empty_for_named_gene`, because naming a gene is NORMAL for a value that correctly maps to
    empty here). So the only honest deterministic signal is the finding-model syntax PARSER. Everything else
    about this column is a judgement, and judgements are the LLM judge's job."""
    from aus_trial_universe.tasks.eligibility.mapping.finding_model import finding_model_problems
    return [str(p) for p in finding_model_problems(expr)]


def _sig_direction(before: str, after: str) -> str:
    """The six-term vocabulary admits only three interesting moves: a signature gained, lost, or swapped."""
    b, a = (before or "").strip(), (after or "").strip()
    if b and not a:
        return "CHANGED_TO_EMPTY"
    if a and not b:
        return "CHANGED_FROM_EMPTY"
    return "CHANGED_LATERAL"


CANCER_TYPE = ColumnSpec(
    name="cancer_type", key="cancer_type", final_col=_ST.CANCER_TYPE.value_col("finalised"), final_col_baseline="oncotree_code_FINAL",
    table=_ST.CANCER_TYPE.file("finalised"),
    baselines={
        "28Jul": BACKUPS / "pre_stage1_20260729_013636/masters/eligibility/current_version/finalised_cancer_type_map.tsv",
        "04Aug": ELIGIBILITY_OUTPUT / "archive/20260804/finalised_cancer_type_map.tsv",
        "live": current_version_dir(ELIGIBILITY_OUTPUT) / "finalised_cancer_type_map.tsv",
    },
    out_dir=ANALYSIS_DIR / "cancer_type_stage1_review",
    errs=_ct_errs, direction=_ct_direction, register="cancer_type",
)

#: gene_alteration has only TWO distinct reviewed states, not three. Measured 2026-08-06: 28 July and the 4 August
#: pre-B1b archive are byte-identical on all 859 shared values (4 Aug merely added 50 new ones), and the 5 August
#: archive equals the live store on all 909 shared values. So the useful axes are PRE-B1b (the oldest reviewed
#: state, and the hard gate — the only baseline that can see a regression B1b itself introduced) and LIVE.
GENE_ALTERATION = ColumnSpec(
    name="gene_alteration", key="gene_alteration",
    final_col=_ST.GENE_ALTERATION.value_col("finalised"), final_col_baseline="finding_model_FINAL",
    table=_ST.GENE_ALTERATION.file("finalised"),
    baselines={
        "preB1b": BACKUPS / "pre_gene_alteration_20260804_2327/masters/eligibility/current_version/finalised_gene_alteration_map.tsv",
        "live": current_version_dir(ELIGIBILITY_OUTPUT) / _ST.GENE_ALTERATION.file("finalised"),
    },
    out_dir=ANALYSIS_DIR / "gene_alteration_stage1_review",
    errs=_ga_errs, direction=_ga_direction, register="gene_alteration",
)

#: molecular_signature — its FIRST refinement. Unlike the other two columns it has only ONE reviewed state:
#: measured 2026-08-06, the 28 July mapping and the live mapping agree on all 167 shared values, so nothing has
#: ever changed here. That makes 28 July both the hard gate and the current state.
MOLECULAR_SIGNATURE = ColumnSpec(
    name="molecular_signature", key="molecular_signature",
    final_col=_ST.MOLECULAR_SIGNATURE.value_col("finalised"), final_col_baseline="finding_model_FINAL",
    table=_ST.MOLECULAR_SIGNATURE.file("finalised"),
    baselines={
        "28Jul": BACKUPS / "pre_stage1_20260729_013636/masters/eligibility/current_version/finalised_molecular_signature_map.tsv",
        "live": current_version_dir(ELIGIBILITY_OUTPUT) / _ST.MOLECULAR_SIGNATURE.file("finalised"),
    },
    out_dir=ANALYSIS_DIR / "molecular_signature_stage1_review",
    errs=_sig_errs, direction=_sig_direction, register="molecular_signature",
)

SPECS = {s.name: s for s in (CANCER_TYPE, GENE_ALTERATION, MOLECULAR_SIGNATURE)}


def spec(name: str) -> ColumnSpec:
    if name not in SPECS:
        raise SystemExit(f"unknown column {name!r}; valid: {', '.join(SPECS)}")
    return SPECS[name]
