"""FIDELITY TEST for the matching-engine port — every case from the engine's own TrialGeneticsParserTest.kt.

If this fails, every number the dry run produces is suspect. Re-transcribe the parser before trusting any of it.

Pinned to: /Users/junrancao/HMF_repository/oncoact · branch trial_matching · commit a97142938 (2026-05-22),
file trial-matching/src/test/kotlin/com/hartwig/oncoact/trialmatching/trial/TrialGeneticsParserTest.kt
"""
from __future__ import annotations

import pytest

from aus_trial_universe.qa.engine_port import (
    PHARMACOGENOTYPE,
    VIRUS,
    WILDTYPE,
    GeneticAlterationParser,
    TrialGeneticsParser,
)


def P(s: str):
    return GeneticAlterationParser(s).parse()


def test_parse_empty():
    assert TrialGeneticsParser("").parse() == []


def test_parse_single_small_variant():
    i = "SmallVariant[gene=BRCA1]"
    assert TrialGeneticsParser(i).parse() == [(P(i), True, i)]


def test_handle_and_within_variant():
    i1, i2, i3 = "SmallVariant[gene=KRAS]", "GainDeletion[gene=KRAS & type=GAIN]", "Fusion[geneEnd=KRAS]"
    assert TrialGeneticsParser(f"{i1} | {i2} | {i3}").parse() == [
        (P(i1), True, i1), (P(i2), True, i2), (P(i3), True, i3)]


def test_parse_two_small_variants():
    i1, i2 = "SmallVariant[gene=BRCA1]", "SmallVariant[gene=BRCA2]"
    assert TrialGeneticsParser(f"{i1} | {i2}").parse() == [(P(i1), True, i1), (P(i2), True, i2)]


def test_handle_delimiter_within_brackets():
    parts = ["GainDeletion[gene=MAP2K1]", "Disruption[gene=MAP2K1]",
             "Fusion[geneStart=MAP2K1 | geneEnd=MAP2K1]", "SmallVariant[gene=MAP2K2]"]
    assert TrialGeneticsParser(" | ".join(parts)).parse() == [(P(p), True, p) for p in parts]


def test_handle_negated_criterion():
    inner = "Disruption[gene=HRAS]"
    assert TrialGeneticsParser(f"NOT({inner})").parse() == [(P(inner), False, inner)]


def test_handle_multiple_negated_criteria():
    a = "SmallVariant[gene=BRAF & transcriptImpact.hgvsProteinImpact=p.V600E]"
    b = "GainDeletion[gene=BRCA2]"
    assert TrialGeneticsParser(f"NOT({a}) & NOT({b})").parse() == [(P(a), False, a), (P(b), False, b)]


def test_handle_negated_multiple_criteria():
    a, b, c = "SmallVariant[gene=BRAF]", "GainDeletion[gene=BRAF & type=GAIN]", "Fusion[geneEnd=BRAF]"
    assert TrialGeneticsParser(f"NOT({a} | {b} | {c})").parse() == [
        (P(a), False, a), (P(b), False, b), (P(c), False, c)]


def test_handle_deeply_nested_criteria():
    a, b = "SmallVariant[gene=FLT3]", "Disruption[gene=FLT3]"
    c, d = "Fusion[geneStart=FLT3]", "Fusion[geneEnd=FLT3]"
    got = TrialGeneticsParser(f"NOT({a}) & NOT({b}) & NOT(({c} | {d}))").parse()
    assert got == [(P(a), False, a), (P(b), False, b), (P(c), False, c), (P(d), False, d)]


@pytest.mark.parametrize("ignored", [f"{PHARMACOGENOTYPE}[gene=KRAS]", f"{WILDTYPE}[KRAS]", f"{VIRUS}[KRAS]"])
def test_ignored_classes_are_skipped(ignored):
    i1, i2 = "SmallVariant[gene=KRAS]", "GainDeletion[gene=KRAS & type=GAIN]"
    assert TrialGeneticsParser(f"{i1} | {i2} | {ignored}").parse() == [(P(i1), True, i1), (P(i2), True, i2)]


# --------------------------------------------------------------------------- #
# The defects the transcription exposed. These are assertions about the ENGINE's current behaviour, not about
# what is desirable — they are what `matching_engine_capability_gaps.md` asks to have fixed. When the engine is
# upgraded these tests should FLIP, which is exactly the signal we want.
# --------------------------------------------------------------------------- #
def test_engine_gap_G4_in_bracket_fusion_or_loses_both_genes():
    """`Fusion[geneStart=X | geneEnd=X]` -> Fusion(None, None): matches ANY fusion (engine gap G4)."""
    assert P("Fusion[geneStart=ALK | geneEnd=ALK]") == ("Fusion", None, None)


def test_engine_gap_G2_positive_and_is_not_split():
    """A positive term ANDed with a NOT() is handed to the term parser whole, corrupting the gene (gap G2)."""
    got = TrialGeneticsParser(
        "SmallVariant[gene=EGFR] & NOT(SmallVariant[gene=EGFR & "
        "transcriptImpact.hgvsProteinImpact=p.T790M])").parse()
    assert len(got) == 1
    alteration = got[0][0]
    assert alteration[0] == "SmallVariantAlteration"
    assert alteration[1] == "EGFR]"          # the trailing bracket ends up inside the gene symbol


def test_engine_gap_G2_paren_or_group_drops_the_whole_expression():
    """`(A | B) & NOT(C)` raises inside the parser, so the engine logs a failure and keeps nothing (gap G2)."""
    p = TrialGeneticsParser(
        "(SmallVariant[gene=BRCA1] | SmallVariant[gene=BRCA2]) & NOT(GainDeletion[gene=ERBB2 & type=GAIN])")
    assert p.parse() == []
    assert p.failures


def test_engine_gap_G5_het_del_is_widened_to_any_copy_number_event():
    assert P("GainDeletion[gene=RB1 & type=HET_DEL]") == ("Fluctuation", "RB1", None)


def test_wildcard_protein_annotation_matches_any_substitution_at_the_codon():
    """p.V600X -> Substitution(alternate=None). CONFIRMED WORKING — do not 'fix' our wildcards."""
    alteration = P("SmallVariant[gene=BRAF & transcriptImpact.hgvsProteinImpact=p.V600X]")
    assert alteration[0] == "SmallVariantAlteration"
    assert alteration[4] == ("Substitution", "BRAF", 600, "V", None)


def test_codon_wildcard_keeps_the_reference_residue():
    """p.R132X pins ref=R at 132 — which is why it is preferred over affectedCodon=132."""
    alteration = P("SmallVariant[gene=IDH1 & transcriptImpact.hgvsProteinImpact=p.R132X]")
    assert alteration[4] == ("Substitution", "IDH1", 132, "R", None)
