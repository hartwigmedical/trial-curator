"""SEMANTIC defect catalogue for gene-alteration finding-model expressions.

`grammar.finding_model_problems` is the hard SYNTAX gate — real classes, real fields, valid enum members, correct
scoping, balanced delimiters. It says nothing about whether a well-formed expression is a *sensible* one. This
module is the missing half: a named, severity-tagged catalogue of the ways a syntactically valid expression can
still be wrong, so defects are counted and reported rather than discovered anecdotally.

Severity contract (same as the OncoTree catalogue):
  error    the expression is wrong or self-defeating; it must not ship. Drives the gate.
  warn     probably wrong, or right but worth a human's eyes; reported, does not gate.
  info     an observation that is expected in a healthy corpus; reported for shape only.

`layer` says WHO can fix it: `det` = a deterministic rewrite can (canonicalise already does most), `llm` = needs
the source text and judgement, `up` = the defect is upstream in extraction and mapping cannot repair it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from aus_trial_universe.tasks.eligibility.mapping.gene_alteration import expr as E
from aus_trial_universe.tasks.eligibility.mapping.finding_model import finding_model_problems, top_level_has

# --------------------------------------------------------------------------- #
# The catalogue.
# --------------------------------------------------------------------------- #
CATALOGUE: dict[str, tuple[str, str, str]] = {
    # id                          layer  severity  description
    "invalid_syntax":            ("det", "error", "fails the finding-model syntax validator"),
    "unparseable":               ("det", "error", "cannot be parsed as a boolean finding-model expression"),
    "unsatisfiable":             ("llm", "error", "no patient can satisfy it (a term is both required and excluded)"),
    "splice_as_effect":          ("det", "error", "transcriptImpact.effects=SPLICE — SPLICE is a codingEffect, "
                                                 "not a VariantEffect member"),
    "hla_as_pharmacogenotype":   ("det", "error", "an HLA locus expressed as PharmocoGenotype; use HlaAllele"),
    "tautological_conjunct":     ("det", "error", "a conjunct adds nothing — A & (A | B) collapses to A; usually a "
                                                 "broad stand-in substituted for an inexpressible clause"),
    "not_and_with_required_twin": ("det", "error", "NOT(X & Y) where Y is also required — reduces to NOT(X)"),
    "absorbed_specific_terms":   ("llm", "warn", "canonicalisation removed MORE SPECIFIC terms because a broader "
                                                "term was present — the broad term is probably the mapping error"),
    # NB there is deliberately NO `wildtype_style_mixed` check. An expression that uses BOTH `Wildtype[gene=X]`
    # and `NOT(SmallVariant[gene=Y])` is normally CORRECT: the source makes two different claims (a whole gene
    # wild-type vs a specific alteration excluded), and they select different patient sets. Measured over 909
    # values, every instance of the "mixed styling" pattern was faithful — see reconcile.find_groups for the
    # related grouping rule that was rejected for the same reason.
    "fusion_driver_with_gain":   ("llm", "warn", "a canonical fusion driver expanded to include type=GAIN; "
                                                "amplification is not the actionable event for these genes"),
    "arm_terms_not_compound":    ("det", "warn", "several Arm terms ANDed; a multi-arm event belongs in ONE "
                                                "compound Arm[(...) & (...)] term"),
    "empty_for_named_gene":      ("llm", "warn", "mapped to '' although the source names a gene symbol"),
    "double_negated_wildtype":   ("llm", "warn", "NOT(Wildtype[...] & Wildtype[...]) — a convoluted spelling of a "
                                                "positive OR; state the alteration directly"),
    "family_root_as_gene":       ("llm", "error", "a gene-family ROOT token used as if it were a gene symbol "
                                                 "(e.g. PRKC, YAP) — it will never match; expand to its members"),
    "acronym_as_gene":           ("det", "error", "the 'gene' is a clinical abbreviation that merely LOOKS like a "
                                                 "symbol (AGA = actionable genomic alteration), so the mapping "
                                                 "asserts a criterion the source never states"),
    "unknown_gene_symbol":       ("llm", "warn", "gene symbol not seen in the curated resource or prior output — "
                                                "verify it is a real HGNC symbol"),
    "negation_only":             ("up", "info", "no positive term — an exclusion-only gene criterion"),
    "large_expansion":           ("up", "info", "10 or more OR'd terms (a gene family or panel expansion)"),
}

# Gene symbols attested by the hand-curated GeneAlterationCurationResource or by the signed-off production output.
# Used for two DIFFERENT checks, deliberately at different severities:
#   error — the token is a proper PREFIX of one or more known genes and is not itself known. That is the precise
#           signature of a family root smuggled in as a gene ("PRKC" for PRKCA/B/G/..., "YAP" for YAP1), which
#           silently matches nothing downstream.
#   warn  — merely unattested. A genuinely new gene must be able to appear, so this can never gate.
_KNOWN_GENES: frozenset[str] = frozenset(
    line.strip() for line in
    (__import__("pathlib").Path(__file__).with_name("known_genes.txt").read_text().splitlines())
    if line.strip()
)

# Clinical abbreviations that are shaped exactly like a gene symbol, so an unattested-symbol WARN is not enough:
# the mapper reads them as genes and invents a criterion. Added 2026-08-05 after the corpus audit found
# "AGA negative" (an NSCLC trial's ACTIONABLE GENOMIC ALTERATION status) mapped to `Wildtype[gene=AGA]` — AGA is
# a real HGNC symbol (aspartylglucosaminidase, a lysosomal enzyme with no role in cancer), so no vocabulary check
# could catch it. The tell was that the sibling cohort's "AGA positive" mapped to "": the same token read as a
# gene in one value and not the other. ERROR severity, because the result is a false criterion, not a vague one.
# Keep this list SHORT and evidence-driven — only add a symbol once it has actually been mis-mapped.
_NON_GENE_ACRONYMS: dict[str, str] = {
    "AGA": "actionable genomic alteration",
}

# Genes whose ACTIONABLE alteration is the fusion (plus any named mutation), so an unspecified "alteration" must
# NOT be expanded to include type=GAIN. Gene-specific by necessity, not derivable by family: FGFR3 belongs here
# (erdafitinib targets FGFR3 mutations and the FGFR3-TACC3 fusion; amplification is not actionable) while FGFR2
# does NOT (amplification is actionable in gastric cancer), and FGFR1 amplification is the classic squamous-NSCLC
# driver. MET, EGFR and ERBB2 are likewise amplification-actionable and must never be added.
_FUSION_DRIVERS = frozenset({"ALK", "ROS1", "RET", "NTRK1", "NTRK2", "NTRK3", "NRG1", "FGFR3"})
_GENE_TOKEN = re.compile(r"\b([A-Z][A-Z0-9]{1,7}(?:-[A-Z0-9]+)?)\b")
# Words that look like gene symbols but are not, so `empty_for_named_gene` does not misfire on prose.
_NOT_GENES = frozenset({
    "AND", "NOT", "OR", "WHO", "ECOG", "MRD", "CNS", "HLA", "PET", "CT", "MRI", "IHC", "ISH", "FISH", "NGS",
    "DNA", "RNA", "PCR", "VAF", "LOH", "TMB", "MSI", "MSS", "HRD", "TKI", "SOC", "ITD", "TKD", "US", "FDA",
    "EU", "AU", "II", "III", "IV", "IA", "IB", "IIA", "IIB", "IIIA", "IIIB", "IIIC", "IVA", "IVB",
})


@dataclass(frozen=True)
class Finding:
    check: str
    severity: str
    layer: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.check}: {self.detail}"


def _terms(expr: str) -> list[tuple[str, str]]:
    return E._TERM_RE.findall(expr or "")


def semantic_problems(source: str, expression: str) -> list[Finding]:
    """Every catalogued defect in one (source free text -> finding-model expression) pair."""
    out: list[Finding] = []

    def add(check: str, detail: str) -> None:
        layer, sev, _ = CATALOGUE[check]
        out.append(Finding(check, sev, layer, detail))

    e = (expression or "").strip()
    src = (source or "").strip()

    # ---- empty output --------------------------------------------------- #
    if not e:
        # Only an INCLUSION clause naming a gene must map. A source that is nothing but inexpressible
        # NOT(...) exclusions ("NOT(actionable alteration incl. EGFR...)") correctly maps to empty, so the
        # negated clauses are stripped before looking for gene symbols.
        positive_src = _strip_not_clauses(src)
        cands = {g for g in _GENE_TOKEN.findall(positive_src) if g not in _NOT_GENES and not g.isdigit()}
        if cands:
            add("empty_for_named_gene", f"source names {', '.join(sorted(cands)[:6])} but the mapping is empty")
        return out

    # ---- syntax (the hard gate) ---------------------------------------- #
    syn = finding_model_problems(e)
    for p in syn:
        add("invalid_syntax", p)
    if "transcriptImpact.effects=SPLICE" in e:
        add("splice_as_effect", "write transcriptImpact.codingEffect=SPLICE")
    if re.search(r"PharmocoGenotype\[gene=HLA", e):
        add("hla_as_pharmacogenotype", "use HlaAllele[gene=HLA-... & allele=...]")

    # ---- structure (needs a parse) ------------------------------------- #
    try:
        node = E.parse(e)
    except E.ExprError as ex:
        add("unparseable", str(ex))
        return out
    if node is None:
        return out

    try:
        canon = E.canonicalise(e)
    except E.ExprError as ex:                                   # pragma: no cover - parse already succeeded
        add("unparseable", str(ex))
        return out

    if not canon:
        add("unsatisfiable", "canonical form is empty — a required term is also excluded")
        return out

    # A2 / A3 — what the deterministic rewrite had to remove tells us what was wrong.
    before_lits = E.literal_set(e)
    after_lits = E.literal_set(canon)
    if canon != e and before_lits != after_lits:
        n_before = sum(len(c.split("|")) for c in before_lits)
        n_after = sum(len(c.split("|")) for c in after_lits)
        if n_after < n_before:
            add("tautological_conjunct", f"canonicalisation removed {n_before - n_after} redundant literal(s): "
                                         f"{canon}")
            # Did we lose SPECIFIC terms to a broader one? That is a mapping error, not just noise.
            spec_before = [t for t in _terms(e) if len(t[1].split("&")) > 1]
            spec_after = [t for t in _terms(canon) if len(t[1].split("&")) > 1]
            if len(spec_after) < len(spec_before):
                add("absorbed_specific_terms",
                    f"{len(spec_before) - len(spec_after)} qualified term(s) absorbed by a broader term")

    # NOT(X & Y) where Y is ALSO required positively. The `&` must be at boolean depth — an `&` inside
    # `[...]` is a field separator, not a conjunction.
    positives = [t for t in _top_conjuncts(e) if not t.startswith("NOT(")]
    for body in _not_bodies(e):
        conj = _split_depth0(body, "&")
        if len(conj) < 2:
            continue
        twins = [c for c in conj if any(_same_term(c, p) for p in positives)]
        if twins:
            add("not_and_with_required_twin",
                f"NOT({body.strip()}) contains {twins[0]}, which is also required — reduces to NOT(rest)")
        break

    # ---- polarity / style --------------------------------------------- #
    if not any(not t.startswith("NOT(") for t in _top_conjuncts(e)):
        add("negation_only", "the criterion only excludes")

    if re.search(r"NOT\(\s*Wildtype\[[^\]]*\]\s*&\s*Wildtype\[", e):
        add("double_negated_wildtype", "NOT(Wildtype[A] & Wildtype[B]) == 'A or B altered'")

    for cls, body in _terms(e):
        if cls == "GainDeletion" and "type=GAIN" in body:
            g = dict(p.strip().partition("=")[::2] for p in body.split("&") if "=" in p).get("gene", "")
            if g in _FUSION_DRIVERS:
                add("fusion_driver_with_gain", f"{g} amplification — expected the fusion (and named mutations) only")

    for gene in sorted(E.genes_of(e)):
        if gene in _NON_GENE_ACRONYMS:
            add("acronym_as_gene",
                f"'{gene}' is the clinical abbreviation {_NON_GENE_ACRONYMS[gene]!r}, not a gene — the mapping "
                f"invents a criterion the source never states")
            continue
        if gene in _KNOWN_GENES or gene.upper().startswith("HLA"):
            continue
        members = sorted(g for g in _KNOWN_GENES if g.startswith(gene) and g != gene)
        if members:
            add("family_root_as_gene",
                f"'{gene}' is not a gene symbol but the root of {', '.join(members[:6])}"
                f"{' …' if len(members) > 6 else ''} — expand to the members the source means")
        else:
            add("unknown_gene_symbol", f"'{gene}' is unattested; confirm it is a real HGNC symbol")

    n_arm = sum(1 for cls, _ in _terms(e) if cls == "Arm")
    if n_arm > 1 and top_level_has(e, "&"):
        add("arm_terms_not_compound", f"{n_arm} Arm terms — use one compound Arm[(...) & (...)]")

    if e.count("|") >= 9:
        add("large_expansion", f"{e.count('|') + 1} OR'd terms")

    return out


def _split_depth0(e: str, op: str) -> list[str]:
    """Split on `op` at bracket/paren depth 0 (so an `&` inside `[...]` — a field separator — never splits)."""
    parts: list[str] = []
    d = 0
    cur = ""
    for c in e:
        if c in "[(":
            d += 1
            cur += c
        elif c in "])":
            d -= 1
            cur += c
        elif d == 0 and c == op:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += c
    if cur.strip():
        parts.append(cur.strip())
    return [p for p in parts if p]


def _top_conjuncts(e: str) -> list[str]:
    """Top-level `&`-separated conjuncts, respecting brackets and parens."""
    return _split_depth0(e, "&")


def _not_bodies(e: str) -> list[str]:
    """The body of every top-level NOT(...), found by paren matching (bodies may themselves contain parens)."""
    out: list[str] = []
    i = 0
    while True:
        i = e.find("NOT(", i)
        if i < 0:
            return out
        d = 0
        j = i + 3
        while j < len(e):
            if e[j] == "(":
                d += 1
            elif e[j] == ")":
                d -= 1
                if d == 0:
                    break
            j += 1
        out.append(e[i + 4:j])
        i = j + 1


def _same_term(a: str, b: str) -> bool:
    """Loose term equality — whitespace-insensitive, parens stripped."""
    n = lambda s: re.sub(r"\s+", "", s).strip("()")   # noqa: E731
    return n(a) == n(b)


def _strip_not_clauses(src: str) -> str:
    """Remove every NOT(...) clause from a source string, leaving only its inclusion content."""
    out = src
    for body in _not_bodies(src):
        out = out.replace(f"NOT({body})", " ")
    return out


def gating_problems(source: str, expression: str) -> list[str]:
    """The error-severity findings, rendered for the refine loop's feedback channel."""
    return [str(f) for f in semantic_problems(source, expression) if f.severity == "error"]
