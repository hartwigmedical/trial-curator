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
- Virus[name=HPV|EBV|HHV8]                            -- viral status.
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

# --------------------------------------------------------------------------- #
# Grammar spec — the COMPLETE field vocabulary per class (mirrors GRAMMAR_REFERENCE).
# Field kinds the validator enforces:
#   None                 freeform non-empty value (a gene symbol, allele, region, band)
#   "int"                integer value
#   "chrom"              a chromosome (1-22, X, Y)
#   "hgvs"               a protein-change HGVS string ("p.<...>")
#   "flag"               a bare flag, NO '=value' (e.g. inSpliceRegion)
#   frozenset({...})     an enum — the value must be one of these members
# `require_all` fields must ALL be present; `require_any` = at least ONE must be present.
# --------------------------------------------------------------------------- #
_EFFECTS = frozenset({"INFRAME_DELETION", "INFRAME_INSERTION", "MISSENSE", "SPLICE"})
_CODING = frozenset({"NONSENSE_OR_FRAMESHIFT"})

CLASS_SPEC: dict[str, dict] = {
    "SmallVariant": {
        "fields": {
            "gene": None,
            "transcriptImpact.hgvsProteinImpact": "hgvs",
            "transcriptImpact.affectedExon": "int",
            "transcriptImpact.effects": _EFFECTS,
            "transcriptImpact.codingEffect": _CODING,
            "inSpliceRegion": "flag",
        },
        "require_all": ["gene"],
    },
    "GainDeletion": {
        "fields": {"gene": None, "type": frozenset({"GAIN", "HOM_DEL", "HET_DEL"})},
        "require_all": ["gene", "type"],
    },
    "Disruption": {"fields": {"gene": None}, "require_all": ["gene"]},
    "Fusion": {
        "fields": {"geneStart": None, "geneEnd": None},
        "require_any": ["geneStart", "geneEnd"],
    },
    "Arm": {
        "fields": {
            "chromosome": "chrom", "arm": frozenset({"p", "q"}),
            "type": frozenset({"ARM_GAIN", "ARM_LOSS"}), "region": "int", "band": "int",
        },
        "require_all": ["chromosome", "arm", "type"],
    },
    "Wildtype": {"fields": {"gene": None}, "require_all": ["gene"]},
    "Virus": {"fields": {"name": frozenset({"HPV", "EBV", "HHV8"})}, "require_all": ["name"]},
    "PharmocoGenotype": {"fields": {"gene": None, "allele": None}, "require_all": ["gene"]},
    "MicrosatelliteStability": {
        "fields": {"PurpleMicrosatelliteStatus": frozenset({"MSI", "MSS"})},
        "require_all": ["PurpleMicrosatelliteStatus"],
    },
    "homologousRecombination": {
        "fields": {"ChordStatus": frozenset({"HR_DEFICIENT", "HR_PROFICIENT"})},
        "require_all": ["ChordStatus"],
    },
    "tumorMutationBurden": {"fields": {"Status": frozenset({"HIGH"})}, "require_all": ["Status"]},
    "tumorMutationLoad": {"fields": {"Status": frozenset({"HIGH"})}, "require_all": ["Status"]},
}

_CLASS_RE = re.compile(r"([A-Za-z][A-Za-z0-9]*)\s*\[")
# a whole `Class[body]` term — bodies never nest square brackets, so a non-bracket body is safe.
_CLASS_BODY_RE = re.compile(r"([A-Za-z][A-Za-z0-9]*)\[([^\[\]]*)\]")
_NOT_BODY_RE = re.compile(r"NOT\(([^()]*)\)")
# a protein-change HGVS string: p. + amino acid (1- or 3-letter, or *) + residue number + optional tail.
_HGVS_RE = re.compile(r"^p\.[A-Za-z*]{1,3}\d+[A-Za-z0-9*>?_.]*$")
_CHROM = frozenset([str(n) for n in range(1, 23)] + ["X", "Y"])


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


def _top_level_has(expr: str, op: str) -> bool:
    """True if operator char ``op`` (& or |) appears at bracket/paren depth 0 (outside every [ ] / ( ) group)."""
    depth = 0
    for c in expr:
        if c in "[(":
            depth += 1
        elif c in "])":
            depth -= 1
        elif depth == 0 and c == op:
            return True
    return False


def _fields_of(body: str) -> list[tuple[str, str | None]]:
    """Parse a class body into (name, value) pairs. Flags (no '=') get value None. Parens (Arm
    sub-groups) and the &/| that separate fields are flattened away — we validate the field SET."""
    out: list[tuple[str, str | None]] = []
    for piece in re.split(r"[&|]", body.replace("(", " ").replace(")", " ")):
        piece = piece.strip()
        if not piece:
            continue
        if "=" in piece:
            name, _, val = piece.partition("=")
            out.append((name.strip(), val.strip()))
        else:
            out.append((piece, None))
    return out


def _term_problems(cls: str, body: str) -> list[str]:
    """Validate one ``Class[body]`` term against CLASS_SPEC (unknown field / bad enum / bad value / missing scope)."""
    spec = CLASS_SPEC[cls]
    fields = spec["fields"]
    problems: list[str] = []
    present: set[str] = set()
    for name, val in _fields_of(body):
        if name not in fields:
            problems.append(f"unknown field '{name}' for {cls}")
            continue
        present.add(name)
        kind = fields[name]
        if kind == "flag":
            if val is not None:
                problems.append(f"{cls}.{name} is a flag and takes no value")
        elif val is None or val == "":
            problems.append(f"{cls}.{name} is missing a value")
        elif isinstance(kind, frozenset):
            if val not in kind:
                problems.append(f"invalid value '{val}' for {cls}.{name} (allowed: {', '.join(sorted(kind))})")
        elif kind == "int":
            if not val.isdigit():
                problems.append(f"{cls}.{name} must be an integer, got '{val}'")
        elif kind == "chrom":
            if val not in _CHROM:
                problems.append(f"{cls}.{name} must be a chromosome 1-22/X/Y, got '{val}'")
        elif kind == "hgvs":
            if not _HGVS_RE.match(val):
                problems.append(f"invalid HGVS protein change '{val}' for {cls}.{name} — expected p.<change> (e.g. p.V600E)")
    for req in spec.get("require_all", []):
        if req not in present:
            problems.append(f"{cls} term missing required {req}= scope")
    any_req = spec.get("require_any")
    if any_req and not (present & set(any_req)):
        problems.append(f"{cls} term needs at least one of: {'/'.join(f'{r}=' for r in any_req)}")
    return problems


def finding_model_problems(expr: str) -> list[str]:
    """Deterministic grammar + logic problems in a finding-model expression (empty list = well-formed).

    A field/enum/scope/HGVS-aware validator — the hard SYNTAX gate for the mapping loop. It guarantees a
    passing expression is structurally valid finding-model (real classes, real fields, valid enums, scoped
    correctly, well-formed HGVS, balanced + unambiguous), leaving the reviewer to judge only SEMANTIC
    faithfulness (right gene / variant / expansion).
    """
    expr = (expr or "").strip()
    problems: list[str] = []
    if not expr:
        return problems
    if expr.count("[") != expr.count("]"):
        return ["unbalanced square brackets"]
    if expr.count("(") != expr.count(")"):
        return ["unbalanced parentheses"]

    # 1. every Class[...] uses a real class, then valid fields/enums/values/scope for that class.
    unknown = sorted({c for c in _CLASS_RE.findall(expr) if c not in KNOWN_CLASSES})
    if unknown:
        problems.append(f"unknown finding-model class(es): {', '.join(unknown)}")
    for cls, body in _CLASS_BODY_RE.findall(expr):
        if cls in CLASS_SPEC:
            problems.extend(_term_problems(cls, body))

    # 2. ambiguous top-level precedence: an OR-group ANDed with something needs parentheses.
    if _top_level_has(expr, "&") and _top_level_has(expr, "|"):
        problems.append("ambiguous OR/AND precedence at top level — parenthesise the OR-group: write "
                        "'(A | B) & NOT(C)', not 'A | B & NOT(C)'")

    # 3. idempotency: a top-level term repeated (X & X, or NOT(X) & NOT(X)) is redundant noise.
    terms = _top_level_terms(expr)
    dups = sorted({t for t in terms if terms.count(t) > 1})
    if dups:
        problems.append(f"duplicate term(s): {'; '.join(dups)} — X AND X = X (and NOT(X) AND NOT(X) = NOT(X)); list each once")

    # 4. self-contradiction: the same term both required and excluded (X & NOT(X)). When a trial's
    # inclusion and exclusion of an alteration apply to DIFFERENT cancer types, that must be split into
    # separate DNF rows upstream, and a location/context-qualified exclusion that finding-model cannot
    # express must be OMITTED — never encoded as X & NOT(X) here.
    positive = _NOT_BODY_RE.sub("", expr)
    for body in _NOT_BODY_RE.findall(expr):
        body = body.strip()
        if body and body in positive:
            problems.append(f"self-contradiction: term both required and excluded: {body}")
            break

    return list(dict.fromkeys(problems))
