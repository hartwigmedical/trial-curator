"""Finding-model grammar + syntax validator, re-grounded on the AUTHORITATIVE Java datamodel.

WHY THIS EXISTS. The production grammar was written from the curated spreadsheet
(`GeneAlterationCurationResource_13052026.xlsx`) and diverges from the real model in ways the validator then
*blessed*, because its enums were transcribed from the prompt rather than from the source of truth:

  - `transcriptImpact.effects=SPLICE` — `SPLICE` is NOT a member of `VariantEffect`. It is a member of the OTHER
    enum, `CodingEffect`. 8 expressions / 46 export rows were emitting an impossible value.
  - `transcriptImpact.codingEffect` was restricted to one member of six.
  - `GainDeletion.type` was missing `CN_NEUTRAL_LOH`, which is how copy-neutral LOH must be expressed.
  - `transcriptImpact.affectedCodon` was absent entirely — so "any mutation at codon 12" had no honest encoding
    and was rendered as the pseudo-HGVS wildcard `p.G12X`.
  - HLA eligibility was expressed as `PharmocoGenotype`, though the datamodel has a dedicated `HlaAllele` record.

Authoritative sources (read 2026-08-04):
  hmftools/finding-datamodel/src/main/java/com/hartwig/hmftools/finding/datamodel/
    SmallVariant.java          TranscriptImpact fields; VariantEffect (20); CodingEffect (6); VariantType; HotspotType
    GainDeletion.java          Type = GAIN | HOM_DEL | HET_DEL | CN_NEUTRAL_LOH | NONE
    Disruption.java            Type = DISRUPTION | HOM_DUP_DISRUPTION | HOM_DEL_DISRUPTION
    ChromosomeArmCopyNumber.java   ChromosomeArm = P | Q ; Type = GAIN | LOSS | DIPLOID
    Virus.java                 OncogenicVirus = MCV | EBV | HPV | HBV | HHV8
    HlaAllele.java             gene, allele
    PharmacoGenotype.java      gene, allele   (the DSL token keeps the legacy spelling `PharmocoGenotype`)

`Wildtype[gene=X]` has no Java record — it is a curation-level concept. It is retained deliberately: the
matching engine carves it out by name, and rewriting it to `NOT(SmallVariant[gene=X])` would change the meaning
(wild-type = no alteration of ANY kind, not merely no small variant).
"""
from __future__ import annotations

import re

# --------------------------------------------------------------------------- #
# Vocabulary — transcribed from the Java records, not from the curated spreadsheet.
# --------------------------------------------------------------------------- #
KNOWN_CLASSES = frozenset({
    "SmallVariant", "GainDeletion", "Disruption", "Fusion", "Arm", "Wildtype",
    "Virus", "PharmocoGenotype", "HlaAllele",
    "MicrosatelliteStability", "homologousRecombination", "tumorMutationBurden", "tumorMutationLoad",
})

# SmallVariant.VariantEffect — all 20 members. The validator accepts every real member; the PROMPT advertises only
# the subset a trial criterion can plausibly name (see GRAMMAR_REFERENCE).
VARIANT_EFFECT = frozenset({
    "STOP_GAINED", "STOP_LOST", "START_LOST", "FRAMESHIFT",
    "SPLICE_ACCEPTOR", "SPLICE_DONOR",
    "INFRAME_INSERTION", "INFRAME_DELETION", "MISSENSE",
    "PHASED_MISSENSE", "PHASED_INFRAME_INSERTION", "PHASED_INFRAME_DELETION",
    "SYNONYMOUS", "PHASED_SYNONYMOUS",
    "INTRONIC", "FIVE_PRIME_UTR", "THREE_PRIME_UTR", "UPSTREAM_GENE",
    "NON_CODING_TRANSCRIPT", "OTHER",
})
# SmallVariant.CodingEffect — the enum that DOES contain SPLICE.
CODING_EFFECT = frozenset({"NONSENSE_OR_FRAMESHIFT", "SPLICE", "MISSENSE", "SYNONYMOUS", "NONE", "UNDEFINED"})
# GainDeletion.Type
CN_TYPE = frozenset({"GAIN", "HOM_DEL", "HET_DEL", "CN_NEUTRAL_LOH", "NONE"})
# ChromosomeArmCopyNumber.Type, plus the ARM_-prefixed spellings the engine strips and the resource uses.
ARM_TYPE = frozenset({"GAIN", "LOSS", "DIPLOID", "ARM_GAIN", "ARM_LOSS"})
# Virus.OncogenicVirus
VIRUS_NAME = frozenset({"MCV", "EBV", "HPV", "HBV", "HHV8"})

GRAMMAR_REFERENCE = """\
FINDING-MODEL GRAMMAR (term = Class[field=value & ...]; combine terms with | (OR), & (AND), NOT(...)):

Gene alterations:
- SmallVariant[gene=X]  -- a sequence variant (SNV/indel) in gene X. Optional refinements ANDed inside:
    & transcriptImpact.hgvsProteinImpact=p.V600E     (specific protein change, HGVS; `X` as the substituted
                                                      residue = "any change at that codon", e.g. p.R132X)
    & transcriptImpact.affectedCodon=12              (codon number. Use ONLY when the source names a codon with
                                                      NO reference residue, or explicitly admits non-substitution
                                                      variants at it. When the residue is known, the wildcard
                                                      p.<ref><n>X is MORE specific and is preferred — it pins the
                                                      reference residue AND the substitution class.)
    & transcriptImpact.affectedExon=19               (exon number)
    & transcriptImpact.effects=INFRAME_DELETION|INFRAME_INSERTION|MISSENSE|STOP_GAINED|FRAMESHIFT|SPLICE_ACCEPTOR|SPLICE_DONOR
    & transcriptImpact.codingEffect=SPLICE|NONSENSE_OR_FRAMESHIFT|MISSENSE
    & inSpliceRegion                                 (bare flag, no value)
  (SmallVariant MUST be gene-scoped: always include gene=.)
  NOTE on splice: `SPLICE` is a codingEffect, NEVER an effect. A splice-altering variant whose donor/acceptor
  side is unknown -> transcriptImpact.codingEffect=SPLICE. Only name SPLICE_ACCEPTOR / SPLICE_DONOR when the
  source actually distinguishes them.
- GainDeletion[gene=X & type=GAIN|HOM_DEL|HET_DEL|CN_NEUTRAL_LOH]   -- copy number: GAIN=amplification,
  HOM_DEL=homozygous/deep deletion, HET_DEL=single-copy loss, CN_NEUTRAL_LOH=copy-neutral loss of heterozygosity.
- Disruption[gene=X]                                  -- structural disruption of gene X.
- Fusion[geneStart=A & geneEnd=B]                     -- fusion A::B. Single gene, orientation UNKNOWN:
  Fusion[geneStart=X | geneEnd=X] (ONE term — a fusion is one event; do NOT split it into two OR'd terms).
  5' only: Fusion[geneStart=X]; 3' only: Fusion[geneEnd=X].
- Arm[chromosome=N & arm=p|q & type=ARM_GAIN|ARM_LOSS]  -- chromosome-arm gain/loss.
  SEVERAL arms in ONE event (co-deletion, monosomy) go in ONE term as parenthesised groups:
  Arm[(chromosome=1 & arm=p & type=ARM_LOSS) & (chromosome=19 & arm=q & type=ARM_LOSS)]
- Wildtype[gene=X]                                    -- gene X wild-type (no alteration of any kind).
- Virus[name=HPV|EBV|HHV8|HBV|MCV]                    -- viral status.
- HlaAllele[gene=HLA-A & allele=*02:01]               -- HLA type.
- PharmocoGenotype[gene=X & allele=*1]                -- pharmacogenomic allele (drug-metabolism genes only:
  DPYD, UGT1A1, TPMT, ... ). NEVER use this for HLA — HLA is HlaAllele.

Molecular signatures:
- MicrosatelliteStability[PurpleMicrosatelliteStatus=MSI|MSS]
- homologousRecombination[ChordStatus=HR_DEFICIENT|HR_PROFICIENT]
- tumorMutationBurden[Status=HIGH]     (TMB-high)
- tumorMutationLoad[Status=HIGH]       (TML-high)

"X mutation" / "mutated" / "-mutant" (no specific variant) denotes a SEQUENCE variant -> SmallVariant[gene=X] ONLY
(do NOT add amplification/deletion/fusion). Reserve the full EXPANSION for a genuinely UNSPECIFIED event —
"X alteration"/"aberration"/"abnormality"/"genomic alteration"/"X-altered":
- tumour suppressor gene  -> SmallVariant[gene=X] | GainDeletion[gene=X & type=HOM_DEL] | Disruption[gene=X]
- oncogene                -> SmallVariant[gene=X] | GainDeletion[gene=X & type=GAIN]   (add Fusion[...] if the gene is a known fusion partner)
For a CANONICAL FUSION DRIVER — **ALK, ROS1, RET, NTRK1, NTRK2, NTRK3, NRG1, FGFR3** — an "actionable /
targetable alteration" means the FUSION (plus any specifically named mutation) — do NOT add type=GAIN;
amplification of these genes is not the actionable event and adding it over-matches.
The list is genuinely gene-specific, so do NOT generalise it by family. Counter-examples where amplification IS
the actionable event and type=GAIN MUST be kept: **FGFR1** (squamous NSCLC), **FGFR2** (gastric — contrast with
FGFR3), **MET** (amplification and exon-14 skipping), **EGFR**, **ERBB2**. Note also that ALK amplification and
ALK point mutations are real drivers in NEUROBLASTOMA even though the NSCLC-facing rule above excludes GAIN.

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
unrepresentable qualifier as a fault. Preserve only representable detail (gene, protein change, codon, exon,
effect, copy-number TYPE, fusion orientation, chromosome arm).

WHOLE-CLAUSE inexpressibility is DIFFERENT from a dropped qualifier. If an ENTIRE clause names no expressible
alteration — its whole content is a mechanism, a timing, or an actionability judgement ("an alteration MEDIATING
RESISTANCE to a third-generation TKI", "a co-occurring driver for which standard-of-care exists") — DROP THE
WHOLE CLAUSE. Never substitute a broad stand-in expansion for it: doing so adds a term the source never asserted,
and when ANDed with its neighbour it collapses to nothing (A & (A|B|C) = A) while looking like real content.

Exclusions are wrapped in NOT(...). Order terms SmallVariant, GainDeletion, Disruption, Fusion.
"""

# --------------------------------------------------------------------------- #
# Grammar spec — the COMPLETE field vocabulary per class (mirrors GRAMMAR_REFERENCE).
# Field kinds: None=freeform non-empty · "int" · "chrom" · "hgvs" · "flag" (no '=value') · frozenset=enum.
# --------------------------------------------------------------------------- #
CLASS_SPEC: dict[str, dict] = {
    "SmallVariant": {
        "fields": {
            "gene": None,
            "transcriptImpact.hgvsProteinImpact": "hgvs",
            "transcriptImpact.affectedCodon": "int",
            "transcriptImpact.affectedExon": "int",
            "transcriptImpact.effects": VARIANT_EFFECT,
            "transcriptImpact.codingEffect": CODING_EFFECT,
            "inSpliceRegion": "flag",
        },
        "require_all": ["gene"],
    },
    "GainDeletion": {"fields": {"gene": None, "type": CN_TYPE}, "require_all": ["gene", "type"]},
    "Disruption": {"fields": {"gene": None}, "require_all": ["gene"]},
    "Fusion": {"fields": {"geneStart": None, "geneEnd": None}, "require_any": ["geneStart", "geneEnd"]},
    "Arm": {
        "fields": {"chromosome": "chrom", "arm": frozenset({"p", "q", "P", "Q"}), "type": ARM_TYPE,
                   "region": "int", "band": "int"},
        "require_all": ["chromosome", "arm", "type"],
    },
    "Wildtype": {"fields": {"gene": None}, "require_all": ["gene"]},
    "Virus": {"fields": {"name": VIRUS_NAME}, "require_all": ["name"]},
    "HlaAllele": {"fields": {"gene": None, "allele": None}, "require_all": ["gene", "allele"]},
    "PharmocoGenotype": {"fields": {"gene": None, "allele": None}, "require_all": ["gene"]},
    "MicrosatelliteStability": {
        "fields": {"PurpleMicrosatelliteStatus": frozenset({"MSI", "MSS"})},
        "require_all": ["PurpleMicrosatelliteStatus"],
    },
    "homologousRecombination": {
        "fields": {"ChordStatus": frozenset({"HR_DEFICIENT", "HR_PROFICIENT"})},
        "require_all": ["ChordStatus"],
    },
    "tumorMutationBurden": {"fields": {"Status": frozenset({"HIGH", "LOW"})}, "require_all": ["Status"]},
    "tumorMutationLoad": {"fields": {"Status": frozenset({"HIGH", "LOW"})}, "require_all": ["Status"]},
}

_CLASS_RE = re.compile(r"([A-Za-z][A-Za-z0-9]*)\s*\[")
_CLASS_BODY_RE = re.compile(r"([A-Za-z][A-Za-z0-9]*)\[([^\[\]]*)\]")
_NOT_BODY_RE = re.compile(r"NOT\(([^()]*)\)")
_HGVS_RE = re.compile(r"^p\.[A-Za-z*]{1,3}\d+[A-Za-z0-9*>?_.]*$")
_CHROM = frozenset([str(n) for n in range(1, 23)] + ["X", "Y"])

# Genes that are genuine pharmacogenomic (drug-metabolism) loci. Anything HLA-* belongs in HlaAllele.
PGX_GENES = frozenset({"DPYD", "UGT1A1", "TPMT", "NUDT15", "CYP2D6", "CYP2C19", "CYP2C9", "G6PD"})


def top_level_terms(expr: str) -> list[str]:
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


def top_level_has(expr: str, op: str) -> bool:
    """True if operator char ``op`` appears at bracket/paren depth 0."""
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
    """Parse a class body into (name, value) pairs; flags get value None. Parens (Arm sub-groups) are flattened."""
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
    """Validate one ``Class[body]`` term against CLASS_SPEC."""
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
                extra = ""
                if cls == "SmallVariant" and name == "transcriptImpact.effects" and val in CODING_EFFECT:
                    extra = (f" — '{val}' is a codingEffect, not an effect: write "
                             f"transcriptImpact.codingEffect={val}")
                problems.append(f"invalid value '{val}' for {cls}.{name} "
                                f"(allowed: {', '.join(sorted(kind))}){extra}")
        elif kind == "int":
            if not val.isdigit():
                problems.append(f"{cls}.{name} must be an integer, got '{val}'")
        elif kind == "chrom":
            if val not in _CHROM:
                problems.append(f"{cls}.{name} must be a chromosome 1-22/X/Y, got '{val}'")
        elif kind == "hgvs":
            if not _HGVS_RE.match(val):
                problems.append(f"invalid HGVS protein change '{val}' for {cls}.{name} — expected p.<change> "
                                f"(e.g. p.V600E)")
    for req in spec.get("require_all", []):
        if req not in present:
            problems.append(f"{cls} term missing required {req}= scope")
    any_req = spec.get("require_any")
    if any_req and not (present & set(any_req)):
        problems.append(f"{cls} term needs at least one of: {'/'.join(f'{r}=' for r in any_req)}")
    # HLA must not be smuggled in as a pharmacogenotype (the datamodel has a dedicated record).
    if cls == "PharmocoGenotype":
        gene = dict((n, v) for n, v in _fields_of(body)).get("gene", "") or ""
        if gene.upper().startswith("HLA"):
            problems.append(f"'{gene}' is an HLA locus — use HlaAllele[gene={gene} & allele=...], "
                            f"not PharmocoGenotype")
        elif gene and gene.upper() not in PGX_GENES:
            problems.append(f"'{gene}' is not a recognised pharmacogenomic locus for PharmocoGenotype "
                            f"(expected one of: {', '.join(sorted(PGX_GENES))})")
    return problems


def finding_model_problems(expr: str) -> list[str]:
    """Deterministic SYNTAX problems in a finding-model expression (empty list = well-formed).

    Guarantees a passing expression uses real classes, real fields, valid enum members, correct scoping,
    well-formed HGVS, balanced delimiters and unambiguous precedence — leaving the reviewer to judge only
    SEMANTIC faithfulness. Semantic defects live in `checks.py`.
    """
    expr = (expr or "").strip()
    problems: list[str] = []
    if not expr:
        return problems
    if expr.count("[") != expr.count("]"):
        return ["unbalanced square brackets"]
    if expr.count("(") != expr.count(")"):
        return ["unbalanced parentheses"]

    unknown = sorted({c for c in _CLASS_RE.findall(expr) if c not in KNOWN_CLASSES})
    if unknown:
        problems.append(f"unknown finding-model class(es): {', '.join(unknown)}")
    for cls, body in _CLASS_BODY_RE.findall(expr):
        if cls in CLASS_SPEC:
            problems.extend(_term_problems(cls, body))

    if top_level_has(expr, "&") and top_level_has(expr, "|"):
        problems.append("ambiguous OR/AND precedence at top level — parenthesise the OR-group: write "
                        "'(A | B) & NOT(C)', not 'A | B & NOT(C)'")

    terms = top_level_terms(expr)
    dups = sorted({t for t in terms if terms.count(t) > 1})
    if dups:
        problems.append(f"duplicate term(s): {'; '.join(dups)} — X AND X = X (and NOT(X) AND NOT(X) = NOT(X)); "
                        f"list each once")

    positive = _NOT_BODY_RE.sub("", expr)
    for body in _NOT_BODY_RE.findall(expr):
        body = body.strip()
        if body and body in positive:
            problems.append(f"self-contradiction: term both required and excluded: {body}")
            break

    return list(dict.fromkeys(problems))
