"""Approved rulings for the `gene_alteration` column -> `finding_model_FINAL`.

Applied at `mapping/gene_alteration/reconcile.py` R5, after the final canonicalisation. See the package docstring
for the doctrine and the two rules for callers — in particular that `""` is a legitimate ruling, which this module
relies on.
"""
from __future__ import annotations

from aus_trial_universe.tasks.eligibility.mapping.adjudications import Adjudication

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

# --------------------------------------------------------------------------- #
# 2026-08-06 — the stage-1 prompt refinement (session 12). The refined prompts fixed the actionability-exclusion
# class store-wide (see docs/v2_agentic_handover.md), and the user reviewed the full three-way comparison and
# approved it. These SEVEN values are the residue: the ones where the new prompts came out WORSE than what shipped,
# so per the user's instruction they "go to stage 3 for a final correction".
#
# Six restore the mapping that shipped before the refinement. One (`KRAS WT amplification`) does NOT: neither run
# is right there, and the entry records the correct answer instead — see its rationale.
#
# Three further values the review judge called regressions are DELIBERATELY ABSENT, because the judge was wrong
# and the new prompt is right. Recorded so they are not "fixed" later:
#   · FGFR3 pathway alteration ... — the judge claimed the actionable FGFR3 event is the fusion alone. Our own
#     `_FUSION_DRIVERS` note says otherwise: erdafitinib targets FGFR3 MUTATIONS and the FGFR3-TACC3 fusion, and
#     it is amplification that is not actionable. The candidate restoring SmallVariant[gene=FGFR3] recovers a
#     cohort the shipped mapping loses.
#   · NTRK3 fusion-positive — `Fusion[geneEnd=NTRK3]` is the convention the mapper's own worked examples use, and
#     NTRK3 is in practice always the 3' partner. A spelling choice, not a loss.
#   · NOT(KIT and PDGFRA wild-type) ... — in GIST, "KIT/PDGFRA wild-type" is a term of art meaning no KIT/PDGFRA
#     MUTATION (SDH-deficient and NF1-driven tumours are called wild-type). So the candidate's
#     SmallVariant[gene=KIT] | SmallVariant[gene=PDGFRA] is the standard reading, not an over-narrowing.
#
# `rationale` on each entry is the review judge's own wording, kept verbatim so the register records the argument
# that was actually reviewed rather than a later paraphrase of it.
# --------------------------------------------------------------------------- #

# candidate produced: 'SmallVariant[gene=BRAF & transcriptImpact.hgvsProteinImpact=p.V600E] & NOT(SmallVariant[gene=HRAS]) '
_add(Adjudication(
    value=(
        'BRAF V600E mutation AND NOT(known or suspected neurofibromatosis-1 (NF-1) and/or RAS related '
        'gene alterations) AND NOT(known activating AR-V7 or ESR1 alterations leading to constitutive '
        'hormone receptor activation in prostate, breast, or gynecologic cancers)'
    ),
    final=(
        'SmallVariant[gene=BRAF & transcriptImpact.hgvsProteinImpact=p.V600E] & '
        'NOT(SmallVariant[gene=AR & transcriptImpact.codingEffect=SPLICE]) & '
        'NOT(SmallVariant[gene=ESR1]) & NOT(SmallVariant[gene=HRAS]) & NOT(SmallVariant[gene=KRAS]) & '
        'NOT(SmallVariant[gene=NF1]) & NOT(SmallVariant[gene=NRAS]) & NOT(GainDeletion[gene=ESR1 & '
        'type=GAIN]) & NOT(GainDeletion[gene=HRAS & type=GAIN]) & NOT(GainDeletion[gene=KRAS & '
        'type=GAIN]) & NOT(GainDeletion[gene=NF1 & type=HOM_DEL]) & NOT(GainDeletion[gene=NRAS & '
        'type=GAIN]) & NOT(Disruption[gene=NF1])'
    ),
    rationale=(
        'NEW drops the entire hormone-receptor exclusion: AR-V7/AR splice and ESR1 alterations. That '
        'would allow BRAF V600E patients with ESR1 mutation/amplification or AR-V7-like splice '
        'alterations to match, even though the source explicitly excludes them.'
    ),
    approved='2026-08-06, user',
))

# candidate produced: 'GainDeletion[gene=KRAS & type=GAIN]'
_add(Adjudication(
    value=(
        'KRAS WT amplification'
    ),
    final=(
        'GainDeletion[gene=KRAS & type=GAIN] & NOT(SmallVariant[gene=KRAS])'
    ),
    rationale=(
        'NEW drops the expressible Wildtype[gene=KRAS] requirement. It would match KRAS-amplified '
        'tumors that also have a KRAS alteration, whereas the source specifies KRAS WT amplification.'
    ),
    approved='2026-08-06, user',
))

# candidate produced: 'NOT(SmallVariant[gene=BRCA1]) & NOT(SmallVariant[gene=BRCA2])'
_add(Adjudication(
    value=(
        'NOT(BRCA1 and/or BRCA2 positive)'
    ),
    final=(
        'NOT(SmallVariant[gene=BRCA1]) & NOT(SmallVariant[gene=BRCA2]) & NOT(GainDeletion[gene=BRCA1 & '
        'type=HOM_DEL]) & NOT(GainDeletion[gene=BRCA2 & type=HOM_DEL]) & NOT(Disruption[gene=BRCA1]) & '
        'NOT(Disruption[gene=BRCA2])'
    ),
    rationale=(
        'NEW drops the BRCA1/BRCA2 homozygous-deletion and disruption exclusions that CURRENT retains. '
        'It would allow patients with BRCA1 or BRCA2 loss/disruption but no small variant, even though '
        '“BRCA1 and/or BRCA2 positive” is broader than sequence variants alone.'
    ),
    approved='2026-08-06, user',
))

# candidate produced: 'NOT(SmallVariant[gene=ALK]) & NOT(SmallVariant[gene=BRAF]) & NOT(SmallVariant[gene=EGFR]) & NOT(Smal'
_add(Adjudication(
    value=(
        'NOT(EGFR alteration) AND NOT(ALK alteration) AND NOT(ROS1 alteration) AND NOT(NTRK alteration) '
        'AND NOT(BRAF alteration) AND NOT(MET exon 14 skipping) AND NOT(RET alteration)'
    ),
    final=(
        'NOT(SmallVariant[gene=ALK]) & NOT(SmallVariant[gene=BRAF]) & NOT(SmallVariant[gene=EGFR]) & '
        'NOT(SmallVariant[gene=MET & transcriptImpact.affectedExon=14 & '
        'transcriptImpact.codingEffect=SPLICE]) & NOT(SmallVariant[gene=NTRK1]) & '
        'NOT(SmallVariant[gene=NTRK2]) & NOT(SmallVariant[gene=NTRK3]) & NOT(SmallVariant[gene=RET]) & '
        'NOT(SmallVariant[gene=ROS1]) & NOT(GainDeletion[gene=BRAF & type=GAIN]) & '
        'NOT(GainDeletion[gene=EGFR & type=GAIN]) & NOT(Fusion[geneEnd=NTRK1]) & '
        'NOT(Fusion[geneEnd=NTRK2]) & NOT(Fusion[geneEnd=NTRK3]) & NOT(Fusion[geneStart=ALK | '
        'geneEnd=ALK]) & NOT(Fusion[geneStart=BRAF | geneEnd=BRAF]) & NOT(Fusion[geneStart=EGFR | '
        'geneEnd=EGFR]) & NOT(Fusion[geneStart=RET | geneEnd=RET]) & NOT(Fusion[geneStart=ROS1 | '
        'geneEnd=ROS1])'
    ),
    rationale=(
        'NEW drops the exclusion for EGFR fusions (`Fusion[geneStart=EGFR | geneEnd=EGFR]`) while the '
        'source says `NOT(EGFR alteration)`. That would allow patients with EGFR fusion alterations to '
        'match despite the stated EGFR-alteration exclusion.'
    ),
    approved='2026-08-06, user',
))

# candidate produced: 'NOT(SmallVariant[gene=ALK]) & NOT(SmallVariant[gene=BRAF]) & NOT(SmallVariant[gene=EGFR]) & NOT(Smal'
_add(Adjudication(
    value=(
        'NOT(EGFR genomic alteration) AND NOT(ALK genomic alteration) AND NOT(ROS1 genomic alteration) '
        'AND NOT(NTRK genomic alteration) AND NOT(BRAF genomic alteration) AND NOT(RET genomic '
        'alteration) AND NOT(MET exon 14 skipping) AND NOT(KRAS G12C) AND NOT(HER2 genomic alteration) '
        'AND NOT(any other actionable driver oncogene for which there are locally approved and '
        'available targeted first-line therapies)'
    ),
    final=(
        'NOT(SmallVariant[gene=ALK]) & NOT(SmallVariant[gene=BRAF]) & NOT(SmallVariant[gene=EGFR]) & '
        'NOT(SmallVariant[gene=ERBB2]) & NOT(SmallVariant[gene=KRAS & '
        'transcriptImpact.hgvsProteinImpact=p.G12C]) & NOT(SmallVariant[gene=MET & '
        'transcriptImpact.affectedExon=14 & transcriptImpact.codingEffect=SPLICE]) & '
        'NOT(SmallVariant[gene=NTRK1]) & NOT(SmallVariant[gene=NTRK2]) & NOT(SmallVariant[gene=NTRK3]) '
        '& NOT(SmallVariant[gene=RET]) & NOT(SmallVariant[gene=ROS1]) & NOT(GainDeletion[gene=BRAF & '
        'type=GAIN]) & NOT(GainDeletion[gene=EGFR & type=GAIN]) & NOT(GainDeletion[gene=ERBB2 & '
        'type=GAIN]) & NOT(Fusion[geneEnd=NTRK1]) & NOT(Fusion[geneEnd=NTRK2]) & '
        'NOT(Fusion[geneEnd=NTRK3]) & NOT(Fusion[geneStart=ALK | geneEnd=ALK]) & '
        'NOT(Fusion[geneStart=BRAF | geneEnd=BRAF]) & NOT(Fusion[geneStart=RET | geneEnd=RET]) & '
        'NOT(Fusion[geneStart=ROS1 | geneEnd=ROS1])'
    ),
    rationale=(
        'NEW drops the BRAF fusion exclusion. Because the source excludes “BRAF genomic alteration,” '
        'CURRENT’s NOT(Fusion[geneStart=BRAF | geneEnd=BRAF]) more faithfully excludes patients with '
        'BRAF fusions, while NEW would let them match.'
    ),
    approved='2026-08-06, user',
))

# candidate produced: 'SmallVariant[gene=BRAF] | (SmallVariant[gene=EGFR] & NOT(SmallVariant[gene=EGFR & transcriptImpact.a'
_add(Adjudication(
    value=(
        'one or more actionable genomic alteration with available targeted therapy: EGFR other than '
        'activating mutations, ALK, ROS1, MET, BRAF, RET, NTRK, HER2, KRAS, or other genomic alteration'
    ),
    final=(
        'SmallVariant[gene=BRAF] | (SmallVariant[gene=EGFR] & NOT(SmallVariant[gene=EGFR & '
        'transcriptImpact.affectedExon=19 & transcriptImpact.effects=INFRAME_DELETION]) & '
        'NOT(SmallVariant[gene=EGFR & transcriptImpact.hgvsProteinImpact=p.G719X]) & '
        'NOT(SmallVariant[gene=EGFR & transcriptImpact.hgvsProteinImpact=p.L858R]) & '
        'NOT(SmallVariant[gene=EGFR & transcriptImpact.hgvsProteinImpact=p.L861Q]) & '
        'NOT(SmallVariant[gene=EGFR & transcriptImpact.hgvsProteinImpact=p.S768I])) | '
        'SmallVariant[gene=ERBB2] | SmallVariant[gene=KRAS] | SmallVariant[gene=MET & '
        'transcriptImpact.affectedExon=14 & transcriptImpact.codingEffect=SPLICE] | '
        'GainDeletion[gene=BRAF & type=GAIN] | GainDeletion[gene=EGFR & type=GAIN] | '
        'GainDeletion[gene=ERBB2 & type=GAIN] | GainDeletion[gene=KRAS & type=GAIN] | '
        'GainDeletion[gene=MET & type=GAIN] | Fusion[geneEnd=NTRK1] | Fusion[geneEnd=NTRK2] | '
        'Fusion[geneEnd=NTRK3] | Fusion[geneStart=ALK | geneEnd=ALK] | Fusion[geneStart=RET | '
        'geneEnd=RET] | Fusion[geneStart=ROS1 | geneEnd=ROS1]'
    ),
    rationale=(
        'NEW broadens the MET sequence term from MET exon 14 splice/skipping to any MET small variant. '
        'The source’s “actionable ... MET” is supported by the expressible MET actionable events of '
        'amplification and exon-14 skipping, so NEW would match patients with non-exon14 MET point '
        'mutations that the source does not assert.'
    ),
    approved='2026-08-06, user',
))

# candidate produced: 'SmallVariant[gene=PTCH1] & NOT(SmallVariant[gene=SMO])'
_add(Adjudication(
    value=(
        'tumour PTCH1 mutation AND NOT(SMO mutation with known resistance to sonidegib)'
    ),
    final=(
        'SmallVariant[gene=PTCH1]'
    ),
    rationale=(
        'NEW turns the unexpressible subset “SMO mutation with known resistance to sonidegib” into '
        'NOT(any SMO small variant). That over-excludes PTCH1-mutant patients with SMO mutations that '
        'are not known sonidegib-resistance mutations, whereas CURRENT omits the inexpressible '
        'resistance-specific exclusion.'
    ),
    approved='2026-08-06, user',
))

