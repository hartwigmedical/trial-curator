"""Approved rulings for the `gene_alteration` column -> `finding_model_FINAL`.

Applied at `mapping/gene_alteration/reconcile.py` R5, after the final canonicalisation. See the package docstring
for the doctrine and the two rules for callers — in particular that `""` is a legitimate ruling, which this module
relies on.
"""
from __future__ import annotations

from aus_trial_universe.qa.adjudications import Adjudication

APPROVED: dict[str, Adjudication] = {}


def _add(a: Adjudication) -> None:
    APPROVED[a.value] = a


# --------------------------------------------------------------------------- #
# 2026-08-05 — the full-corpus mapping audit (see cancer_type.py for the context).
# --------------------------------------------------------------------------- #

# was: ''
_add(Adjudication(
    value=(
        'AGA negative'
    ),
    final=(
        ''
    ),
    rationale=(
        "'AGA' here is the NSCLC term ACTIONABLE GENOMIC ALTERATION, not the lysosomal gene AGA. The "
        'mapping asserted Wildtype[gene=AGA], a criterion the trial never states. Our own data exposed '
        "it: the sibling cohort's 'AGA positive' mapped to '' — the same token read as a gene in one "
        "value and not the other. 'No actionable genomic alteration' is a complement over an open-ended "
        'panel, and the locked rule is that an open-ended or inexpressible criterion is OMITTED, never '
        "approximated. '' is the faithful mapping and makes the positive/negative pair symmetric."
    ),
    approved='2026-08-05, user',
))

# was: 'Fusion[geneStart=MET | geneEnd=MET] & NOT(SmallVariant[gene=ALK]) & NOT(SmallVariant[gene=BRAF]) & NOT(SmallVariant[gene=EGFR]) & NOT(SmallVariant[gen'
_add(Adjudication(
    value=(
        'MET fusion, including PTPRZ1-MET fusion AND NOT(known MET kinase inhibitor resistance mutation) '
        'AND NOT(known actionable EGFR mutation/gene rearrangement) AND NOT(known actionable ALK '
        'mutation/gene rearrangement) AND NOT(known actionable ROS1 mutation/gene rearrangement) AND '
        'NOT(known actionable RET mutation/gene rearrangement) AND NOT(known actionable NTRK '
        'mutation/gene rearrangement) AND NOT(known actionable KRAS mutation/gene rearrangement) AND '
        'NOT(known actionable BRAF mutation/gene rearrangement)'
    ),
    final=(
        'Fusion[geneStart=MET | geneEnd=MET] & NOT(SmallVariant[gene=ALK]) & NOT(SmallVariant[gene=BRAF]) '
        '& NOT(SmallVariant[gene=EGFR]) & NOT(SmallVariant[gene=KRAS]) & NOT(SmallVariant[gene=NTRK1]) & '
        'NOT(SmallVariant[gene=NTRK2]) & NOT(SmallVariant[gene=NTRK3]) & NOT(SmallVariant[gene=RET]) & '
        'NOT(SmallVariant[gene=ROS1]) & NOT(Fusion[geneEnd=NTRK1]) & NOT(Fusion[geneEnd=NTRK2]) & '
        'NOT(Fusion[geneEnd=NTRK3]) & NOT(Fusion[geneStart=ALK | geneEnd=ALK]) & '
        'NOT(Fusion[geneStart=BRAF | geneEnd=BRAF]) & NOT(Fusion[geneStart=EGFR | geneEnd=EGFR]) & '
        'NOT(Fusion[geneStart=KRAS | geneEnd=KRAS]) & NOT(Fusion[geneStart=RET | geneEnd=RET]) & '
        'NOT(Fusion[geneStart=ROS1 | geneEnd=ROS1])'
    ),
    rationale=(
        "Over-narrowing: the source is ANY MET fusion, with PTPRZ1-MET given as an example ('including'). "
        'The mapping restricted the criterion to the example alone, losing every other MET fusion '
        'partner. The exclusion clauses are correct and are preserved verbatim.'
    ),
    approved='2026-08-05, user',
))
