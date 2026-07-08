"""Hartwig finding-model grammar reference + validator for the mapping task.

The gene-alteration / molecular-signature mappers convert normalized human terms into
finding-model syntax. This module supplies (a) the grammar block that grounds the mapper
prompt and (b) a deterministic validator that catches malformed syntax before the reviewer.

Grammar/class vocabulary is derived from the legacy curation resources (the VITAL
GeneAlteration / MolecularSignature resources) so output stays concordant with them.
"""
from __future__ import annotations

import re

# Class tokens that may appear as `Class[...]` in finding-model syntax (legacy vocabulary).
KNOWN_CLASSES = frozenset({
    "SmallVariant", "GainDeletion", "Disruption", "Fusion", "Arm", "Wildtype",
    "Virus", "PharmocoGenotype",
    "MicrosatelliteStability", "homologousRecombination", "tumorMutationBurden", "tumorMutationLoad",
})

GRAMMAR_REFERENCE = """\
FINDING-MODEL GRAMMAR (term = Class[field=value & ...]; combine terms with | (OR), & (AND), NOT(...)):

Gene alterations:
- SmallVariant[gene=X]  -- a mutation (SNV/indel) in gene X. Optional refinements ANDed inside:
    & transcriptImpact.hgvsProteinImpact=p.V600E     (specific protein change, HGVS)
    & transcriptImpact.affectedExon=19               (exon number)
    & transcriptImpact.effects=INFRAME_DELETION|INFRAME_INSERTION|MISSENSE|SPLICE
    & transcriptImpact.codingEffect=NONSENSE_OR_FRAMESHIFT
    & inSpliceRegion
  (SmallVariant MUST be gene-scoped: always include gene=.)
- GainDeletion[gene=X & type=GAIN|HOM_DEL|HET_DEL]   -- copy number: GAIN=amplification, HOM_DEL=homozygous/deep deletion, HET_DEL=single-copy loss.
- Disruption[gene=X]                                  -- structural disruption of gene X.
- Fusion[geneStart=A & geneEnd=B]                     -- fusion A::B. Single gene, unknown orientation: Fusion[geneStart=X | geneEnd=X]; 5' only: Fusion[geneStart=X]; 3' only: Fusion[geneEnd=X].
- Arm[chromosome=N & arm=p|q & type=ARM_GAIN|ARM_LOSS]  -- chromosome-arm gain/loss (optional & region=R & band=B).
- Wildtype[gene=X]                                    -- gene X wild-type.
- Virus[name=HPV|EBV]                                 -- viral status.
- PharmocoGenotype[gene=X & allele=*1]               -- pharmacogenomic allele.

Molecular signatures:
- MicrosatelliteStability[PurpleMicrosatelliteStatus=MSI|MSS]
- homologousRecombination[ChordStatus=HR_DEFICIENT|HR_PROFICIENT]
- tumorMutationBurden[Status=HIGH]     (TMB-high)
- tumorMutationLoad[Status=HIGH]       (TML-high)

Expansion when NO specific variant is given (a bare "mutation"/"alteration"/"positive"):
- tumour suppressor gene  -> SmallVariant[gene=X] | GainDeletion[gene=X & type=HOM_DEL] | Disruption[gene=X]
- oncogene                -> SmallVariant[gene=X] | GainDeletion[gene=X & type=GAIN]   (add Fusion[...] if the gene is a known fusion partner)
Exclusions are wrapped in NOT(...). Order terms SmallVariant, GainDeletion, Disruption, Fusion.
"""

_CLASS_RE = re.compile(r"([A-Za-z][A-Za-z0-9]*)\s*\[")
_SMALLVARIANT_RE = re.compile(r"SmallVariant\[([^\[\]]*)\]")


def finding_model_problems(expr: str) -> list[str]:
    """Deterministic syntax problems in a finding-model expression (empty = well-formed)."""
    expr = (expr or "").strip()
    problems: list[str] = []
    if not expr:
        return problems
    if expr.count("[") != expr.count("]"):
        problems.append("unbalanced square brackets")
    if expr.count("(") != expr.count(")"):
        problems.append("unbalanced parentheses")
    unknown = sorted({c for c in _CLASS_RE.findall(expr) if c not in KNOWN_CLASSES})
    if unknown:
        problems.append(f"unknown finding-model class(es): {', '.join(unknown)}")
    for body in _SMALLVARIANT_RE.findall(expr):
        if "gene=" not in body:
            problems.append("SmallVariant term missing gene= scope")
            break
    return problems
