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

Histone H3 K27 (diagnostic-category terms):
- "H3K27M" and "H3K27-altered" both denote the SAME expressible molecular core — the H3 K27M mutation.
  ("H3K27-altered" is the WHO diagnostic category, defined by loss of H3K27 trimethylation; the non-K27M
  mechanisms — EZHIP overexpression, EGFR-mutant thalamic tumours — cannot be expressed in finding-model, so
  capture the K27M core.) Render BOTH terms IDENTICALLY, OR'd across the canonical H3 genes:
    SmallVariant[gene=H3F3A & transcriptImpact.hgvsProteinImpact=p.K28M] | SmallVariant[gene=HIST1H3B & transcriptImpact.hgvsProteinImpact=p.K28M] | SmallVariant[gene=HIST1H3C & transcriptImpact.hgvsProteinImpact=p.K28M]
  The coordinate is p.K28M (strict HGVS: the initiator Met is residue 1, so histone "K27" = protein K28) —
  NEVER p.K27M. In a conjunction ("H3K27-altered AND <other>"), wrap this whole OR-block in parentheses and
  AND the other alteration onto it; NEVER drop the H3 block or reduce it to fewer genes.

Expressiveness limits — acceptable simplification:
The classes/fields above are the COMPLETE vocabulary. When the source free text carries a qualifier that has
NO corresponding field, map to the CLOSEST expressible term and DROP the unrepresentable qualifier:
- copy-number COUNT / threshold  ("amplification with >=5 copies", "high-level amplification")  -> just type=GAIN
- variant-allele-frequency threshold ("VAF > 10%")                                               -> drop the threshold
- anatomic location / tumour context ("H3K27M in thalamic DMG")                                   -> drop the qualifier
This loss of specificity is ACCEPTABLE and CORRECT — NEITHER mapper NOR reviewer may treat a dropped
unrepresentable qualifier as a fault. Preserve only representable detail (gene, protein change, exon,
copy-number TYPE GAIN|HOM_DEL|HET_DEL, fusion orientation).

Exclusions are wrapped in NOT(...). Order terms SmallVariant, GainDeletion, Disruption, Fusion.
"""

_CLASS_RE = re.compile(r"([A-Za-z][A-Za-z0-9]*)\s*\[")
_SMALLVARIANT_RE = re.compile(r"SmallVariant\[([^\[\]]*)\]")
_NOT_BODY_RE = re.compile(r"NOT\(([^()]*)\)")


def _top_level_terms(expr: str) -> list[str]:
    """Split on top-level & / | (bracket- and paren-depth 0), keeping NOT(...) as one term."""
    terms: list[str] = []
    depth = 0
    cur = ""
    for c in expr:
        if c in "[(":
            depth += 1
            cur += c
        elif c in "])":
            depth -= 1
            cur += c
        elif depth == 0 and c in "&|":
            if cur.strip():
                terms.append(cur.strip())
            cur = ""
        else:
            cur += c
    if cur.strip():
        terms.append(cur.strip())
    return terms


def finding_model_problems(expr: str) -> list[str]:
    """Deterministic syntax + logic problems in a finding-model expression (empty = well-formed)."""
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
    # Idempotency: a top-level term repeated (X & X, or NOT(X) & NOT(X)) is redundant noise.
    terms = _top_level_terms(expr)
    dups = sorted({t for t in terms if terms.count(t) > 1})
    if dups:
        problems.append(f"duplicate term(s): {'; '.join(dups)} — X AND X = X (and NOT(X) AND NOT(X) = NOT(X)); list each once")
    # Self-contradiction: the same term both required and excluded (X & NOT(X)). When a trial's
    # inclusion and exclusion of an alteration apply to DIFFERENT cancer types, that must be split
    # into separate DNF rows upstream, and a location/context-qualified exclusion that finding-model
    # cannot express must be OMITTED — never encoded as X & NOT(X) here.
    positive = _NOT_BODY_RE.sub("", expr)
    for body in _NOT_BODY_RE.findall(expr):
        body = body.strip()
        if body and body in positive:
            problems.append(f"self-contradiction: term both required and excluded: {body}")
            break
    return problems
