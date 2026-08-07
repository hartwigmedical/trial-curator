"""POTTR DSL parser — the grammar, the routing, and every DEFECT the live file actually contains.

The defect cases are not hypothetical: each is a real line in `trial_eligibility.AU.tsv` that broke an earlier
draft of the parser. They are pinned here because a silent mis-parse would corrupt the comparison invisibly —
an inverted negation reads as a criterion we simply disagree about, not as a bug.
"""
from __future__ import annotations

from aus_trial_universe.analysis.pottr.pottr_source import (
    ANNOTATION, CANCER_TYPE, GENE_ALTERATION, MOLECULAR_BIOMARKER, MOLECULAR_SIGNATURE, PRIOR_THERAPY,
    parse_row, route,
)


def _terms(text, known=None):
    return parse_row("T1", 1, text, known or set()).terms


# --- the grammar ------------------------------------------------------------ #
def test_semicolon_is_and_and_paren_group_is_or():
    terms = _terms("catype:Colorectal cancer; (KRAS:oncogenic_mutation OR NRAS:oncogenic_mutation)")
    assert len(terms) == 2
    assert terms[0].atoms[0].value == "Colorectal cancer"
    assert [a.namespace for a in terms[1].atoms] == ["KRAS", "NRAS"]


def test_not_and_soft_are_parsed_separately():
    """`*` and `NOT` are orthogonal flags and both can sit on one term."""
    hard, soft = _terms("NOT BRAF:V600E; *NOT prior_therapy:Irinotecan")
    assert (hard.negated, hard.soft) == (True, False)
    assert (soft.negated, soft.soft) == (True, True)


def test_soft_flag_is_carried_not_discarded():
    """POTTR's `*` means its matcher ASSUMES the criterion when unknown (Rules.pm:617), so a soft term never
    blocks a match. 207 of 808 terms carry it — scoring them as hard would manufacture differences."""
    assert _terms("*(ERBB2:amplification OR ERBB2:overexpression)")[0].soft is True


# --- routing: POTTR's namespaces do not line up with our columns ------------- #
def test_rhs_decides_gene_versus_biomarker_for_the_same_gene():
    """The RHS is what routes a gene namespace, which is why ERBB2 correctly lands in BOTH columns."""
    assert route("ERBB2", "amplification") == GENE_ALTERATION
    assert route("ERBB2", "protein_expression") == MOLECULAR_BIOMARKER
    assert route("ESR1", "oncogenic_mutation") == GENE_ALTERATION
    assert route("ESR1", "protein_expression") == MOLECULAR_BIOMARKER


def test_routing_normalises_pottr_s_two_spellings_of_the_same_value():
    """POTTR's file writes both `ESR1:protein_expression` and `ESR1:protein expression`. Until the underscore
    normalisation went in, the space variants fell through to gene_alteration and mapped to an empty finding
    model — indistinguishable from a genuinely inexpressible criterion."""
    for spelling in ("protein_expression", "protein expression", "low_protein_expression",
                     "loss of protein expression"):
        assert route("ERBB2", spelling) == MOLECULAR_BIOMARKER, spelling


def test_namespace_routing():
    assert route("catype", "Breast cancer") == CANCER_TYPE
    assert route("prior_therapy", "Irinotecan") == PRIOR_THERAPY
    assert route("microsatellite_instability", "high") == MOLECULAR_SIGNATURE
    assert route("mismatch_repair", "deficient") == MOLECULAR_SIGNATURE
    assert route("sensitive_to", "Atezolizumab") == ANNOTATION      # POTTR's recommender, not eligibility
    assert route("info", "panel:FM1 CDx") == ANNOTATION


def test_curation_annotations_are_not_criteria():
    """`COHORT_2` / `CLOSED_TO_RECRUITMENT` are POTTR bookkeeping. Counting them as criteria we lack would be
    a pure artifact of the file format."""
    terms = _terms("catype:Breast cancer; CLOSED_TO_RECRUITMENT; COHORT_4")
    assert [t.is_criterion for t in terms] == [True, False, False]


# --- the live file's defects ------------------------------------------------- #
def test_in_group_not_keeps_its_negation():
    """NCT06417814: `(prior_therapy:Osimertinib OR NOT prior_therapy:systemic_therapy)`. An earlier draft split
    the `NOT` off as its own token and dropped it — silently INVERTING the criterion."""
    term = _terms("*(prior_therapy:Osimertinib OR NOT prior_therapy:systemic_therapy)")[0]
    assert [(a.value, a.negated) for a in term.atoms] == [("Osimertinib", False), ("systemic_therapy", True)]
    assert "NOT prior_therapy:systemic_therapy" in term.render()


def test_colon_typed_as_semicolon_yields_two_ANDed_conjuncts():
    """NCT06380751: `ER:positive: HER2:negative`. These are ANDed conjuncts — recovering them inside one term
    would wrongly make them OR alternatives, which is a different criterion entirely."""
    row = parse_row("T1", 1, "catype:Breast cancer; ER:positive: HER2:negative")
    assert [t.render() for t in row.terms] == ["catype:Breast cancer", "ER:positive", "HER2:negative"]
    assert any("';'" in r for r in row.repairs)


def test_missing_or_between_glued_atoms_is_repaired_and_recorded():
    """NCT05872295: `ERBB2:overexpression ERBB2:protein_expression` — a dropped OR."""
    row = parse_row("T1", 1, "(ERBB2:amplification OR ERBB2:overexpression ERBB2:protein_expression)")
    assert [a.value for a in row.terms[0].atoms] == ["amplification", "overexpression", "protein_expression"]
    assert any("missing OR" in r for r in row.repairs)


def test_omitted_catype_prefix_is_recovered_only_for_known_values():
    """NCT05872295: `NOT Breast cancer` with the `catype:` prefix dropped. Recovered when the token is a value
    the file uses elsewhere as a catype; otherwise it stays an annotation rather than being invented into one."""
    row = parse_row("T1", 1, "NOT Breast cancer; NOT Something unheard of", {"breast cancer"})
    assert (row.terms[0].column, row.terms[0].negated) == (CANCER_TYPE, True)
    assert row.terms[1].column == ANNOTATION
    assert any("missing 'catype:' prefix" in r for r in row.repairs)


def test_hla_allele_colons_survive():
    """NCT06112314: `HLA-A*02:01:Positive` — colons are part of the allele, not separators."""
    atom = _terms("*HLA-A*02:01:Positive")[0].atoms[0]
    assert atom.namespace == "HLA-A*02" and atom.value == "01:Positive"
    assert atom.column == GENE_ALTERATION


def test_repairs_are_recorded_never_silent():
    """Every repair must be countable — a tolerant parser that hides its repairs is worse than a strict one."""
    assert parse_row("T1", 1, "catype:Breast cancer").repairs == []
