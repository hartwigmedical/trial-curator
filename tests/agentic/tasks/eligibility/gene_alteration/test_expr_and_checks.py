"""Canonical form, grammar validator, semantic catalogue and the reconciliation stage's deterministic layers."""
from __future__ import annotations

import pytest

from aus_trial_universe.tasks.eligibility.mapping.gene_alteration import expr as E
from aus_trial_universe.tasks.eligibility.mapping.gene_alteration.checks import CATALOGUE, semantic_problems
from aus_trial_universe.tasks.eligibility.mapping.finding_model import finding_model_problems
from aus_trial_universe.tasks.eligibility.mapping.gene_alteration.reconcile import concept_key, find_groups, mechanical_fixes

SV = "SmallVariant[gene={}]".format


def checks_of(source: str, expression: str) -> set[str]:
    return {f.check for f in semantic_problems(source, expression)}


# --------------------------------------------------------------------------- #
# Grammar — the corrected enums, grounded in the Java datamodel.
# --------------------------------------------------------------------------- #
def test_splice_is_rejected_as_an_effect_with_a_pointer_to_the_right_field():
    probs = finding_model_problems("SmallVariant[gene=MET & transcriptImpact.effects=SPLICE]")
    assert probs
    assert "codingEffect" in probs[0]


def test_splice_is_accepted_as_a_coding_effect():
    assert finding_model_problems(
        "SmallVariant[gene=MET & transcriptImpact.affectedExon=14 & transcriptImpact.codingEffect=SPLICE]") == []


@pytest.mark.parametrize("effect", ["SPLICE_ACCEPTOR", "SPLICE_DONOR", "MISSENSE", "STOP_GAINED", "FRAMESHIFT"])
def test_real_variant_effects_are_accepted(effect):
    assert finding_model_problems(f"SmallVariant[gene=MET & transcriptImpact.effects={effect}]") == []


@pytest.mark.parametrize("cn", ["GAIN", "HOM_DEL", "HET_DEL", "CN_NEUTRAL_LOH"])
def test_all_copy_number_types_are_accepted(cn):
    assert finding_model_problems(f"GainDeletion[gene=RB1 & type={cn}]") == []


def test_affected_codon_is_a_known_field():
    assert finding_model_problems("SmallVariant[gene=KRAS & transcriptImpact.affectedCodon=12]") == []


def test_hla_is_rejected_as_a_pharmacogenotype():
    probs = finding_model_problems("PharmocoGenotype[gene=HLA-A & allele=*02:01]")
    assert probs and "HlaAllele" in probs[0]


def test_hla_allele_class_is_accepted():
    assert finding_model_problems("HlaAllele[gene=HLA-A & allele=*02:01]") == []


def test_real_pharmacogenomic_locus_is_accepted():
    assert finding_model_problems("PharmocoGenotype[gene=DPYD & allele=*2A]") == []


def test_compound_arm_term_is_accepted():
    assert finding_model_problems(
        "Arm[(chromosome=1 & arm=p & type=ARM_LOSS) & (chromosome=19 & arm=q & type=ARM_LOSS)]") == []


def test_in_bracket_fusion_alternation_is_accepted():
    """Deliberate: a fusion is ONE event with two slots. Do not split it."""
    assert finding_model_problems("Fusion[geneStart=ALK | geneEnd=ALK]") == []


def test_unparenthesised_or_and_mix_is_rejected():
    assert any("precedence" in p for p in finding_model_problems(f"{SV('A')} | {SV('B')} & NOT({SV('C')})"))


# --------------------------------------------------------------------------- #
# Canonical form.
# --------------------------------------------------------------------------- #
def test_de_morgan_is_normalised_to_one_spelling():
    """The B1 defect class: two renderings of one meaning must converge."""
    a = E.canonicalise(f"{SV('X')} & NOT({SV('A')} | {SV('B')})")
    b = E.canonicalise(f"{SV('X')} & NOT({SV('A')}) & NOT({SV('B')})")
    assert a == b


def test_not_of_a_conjunction_is_kept_whole():
    """NOT(a & b) means 'not both' and must NOT become NOT(a) & NOT(b)."""
    got = E.canonicalise(f"NOT({SV('A')} & {SV('B')})")
    assert got == f"NOT({SV('A')} & {SV('B')})"


def test_not_distributes_over_an_inner_or():
    got = E.canonicalise(f"NOT({SV('A')} & ({SV('B')} | {SV('C')}))")
    assert got == f"NOT({SV('A')} & {SV('B')}) & NOT({SV('A')} & {SV('C')})"
    assert E.canonicalise(got) == got


def test_or_order_is_canonical():
    assert E.canonicalise(f"{SV('B')} | {SV('A')}") == E.canonicalise(f"{SV('A')} | {SV('B')}")


def test_absorption_collapses_a_tautological_conjunct():
    """The NCT02609776 defect: A & (A | B | C) == A."""
    got = E.canonicalise(
        f"{SV('EGFR')} & ({SV('EGFR')} | GainDeletion[gene=EGFR & type=GAIN] | Fusion[geneStart=EGFR | geneEnd=EGFR])")
    assert got == SV("EGFR")


def test_absorption_prefers_the_specific_term_in_a_conjunction():
    specific = "SmallVariant[gene=EGFR & transcriptImpact.affectedExon=20]"
    assert E.canonicalise(f"{SV('EGFR')} & {specific}") == specific


def test_absorption_prefers_the_broad_term_in_a_disjunction():
    specific = "SmallVariant[gene=EGFR & transcriptImpact.affectedExon=20]"
    assert E.canonicalise(f"{SV('EGFR')} | {specific}") == SV("EGFR")


def test_not_and_with_a_required_twin_reduces():
    """B & NOT(A & B) == B & NOT(A)."""
    got = E.canonicalise(f"{SV('IDH2')} & NOT({SV('IDH1')} & {SV('IDH2')})")
    assert got == f"{SV('IDH2')} & NOT({SV('IDH1')})"


def test_unsatisfiable_expression_canonicalises_to_empty():
    assert E.canonicalise(f"{SV('X')} & NOT({SV('X')})") == ""


def test_duplicate_terms_are_deduped():
    assert E.canonicalise(f"{SV('A')} & {SV('A')}") == SV("A")


def test_shared_negatives_are_factored_to_the_tail():
    got = E.canonicalise(f"({SV('A')} | {SV('B')}) & NOT({SV('C')})")
    assert got.endswith(f"NOT({SV('C')})")
    assert got.startswith("(")


def test_field_order_inside_a_term_is_canonical():
    a = E.canonicalise("SmallVariant[gene=EGFR & transcriptImpact.affectedExon=21 & "
                       "transcriptImpact.hgvsProteinImpact=p.L858R]")
    b = E.canonicalise("SmallVariant[gene=EGFR & transcriptImpact.hgvsProteinImpact=p.L858R & "
                       "transcriptImpact.affectedExon=21]")
    assert a == b


def test_fusion_alternation_survives_canonicalisation_as_one_term():
    got = E.canonicalise("Fusion[geneStart=ALK | geneEnd=ALK]")
    assert got == "Fusion[geneStart=ALK | geneEnd=ALK]"


@pytest.mark.parametrize("src", [
    f"{SV('A')} | {SV('B')}",
    f"({SV('A')} | {SV('B')}) & NOT({SV('C')})",
    f"NOT({SV('A')} & ({SV('B')} | {SV('C')}))",
    "Arm[(chromosome=1 & arm=p & type=ARM_LOSS) & (chromosome=19 & arm=q & type=ARM_LOSS)]",
    "Fusion[geneStart=MYC | geneEnd=MYC] & Fusion[geneStart=BCL2 | geneEnd=BCL2]",
])
def test_canonicalise_is_idempotent(src):
    once = E.canonicalise(src)
    assert E.canonicalise(once) == once


def test_canonical_rendering_round_trips_through_the_parser():
    """A canonical form that re-parses differently is a rendering bug (precedence parens)."""
    src = f"NOT({SV('A')} & ({SV('B')} | {SV('C')}))"
    once = E.canonicalise(src)
    assert E.literal_set(once) == E.literal_set(src)


def test_genes_of_covers_all_slots():
    assert E.genes_of("Fusion[geneStart=BCR & geneEnd=ABL1] & GainDeletion[gene=MET & type=GAIN]") == {
        "BCR", "ABL1", "MET"}


# --------------------------------------------------------------------------- #
# Semantic catalogue.
# --------------------------------------------------------------------------- #
def test_splice_as_effect_is_an_error():
    assert "splice_as_effect" in checks_of("MET exon 14 skipping",
                                          "SmallVariant[gene=MET & transcriptImpact.effects=SPLICE]")


def test_hla_as_pharmacogenotype_is_an_error():
    assert "hla_as_pharmacogenotype" in checks_of("HLA-A*02:01-positive",
                                                  "PharmocoGenotype[gene=HLA-A & allele=*02:01]")


def test_tautological_conjunct_is_an_error():
    e = f"{SV('EGFR')} & ({SV('EGFR')} | GainDeletion[gene=EGFR & type=GAIN])"
    assert "tautological_conjunct" in checks_of("EGFR mutation AND EGFR alteration mediating resistance", e)


def test_not_and_with_required_twin_is_an_error():
    e = f"{SV('IDH2')} & NOT({SV('IDH1')} & {SV('IDH2')})"
    assert "not_and_with_required_twin" in checks_of("IDH2 mutation AND NOT(dual IDH1 and IDH2 mutations)", e)


def test_field_separator_ampersand_is_not_mistaken_for_a_conjunction():
    """The `&` inside `[...]` separates FIELDS. Counting it as a boolean AND produced 161 false positives."""
    e = "NOT(SmallVariant[gene=BRAF & transcriptImpact.hgvsProteinImpact=p.V600E])"
    assert "not_and_with_required_twin" not in checks_of("NOT(BRAF V600E mutation)", e)


def test_fusion_driver_with_gain_is_warned():
    e = f"{SV('ALK')} | GainDeletion[gene=ALK & type=GAIN] | Fusion[geneStart=ALK | geneEnd=ALK]"
    assert "fusion_driver_with_gain" in checks_of("ALK gene alteration", e)


def test_fusion_driver_without_gain_is_clean():
    e = f"{SV('ALK')} | Fusion[geneStart=ALK | geneEnd=ALK]"
    assert "fusion_driver_with_gain" not in checks_of("ALK gene alteration", e)


def test_arm_terms_not_compound_is_warned():
    e = ("Arm[chromosome=17 & arm=p & type=ARM_LOSS] & Arm[chromosome=17 & arm=q & type=ARM_LOSS]")
    assert "arm_terms_not_compound" in checks_of("monosomy 17", e)


def test_double_negated_wildtype_is_warned():
    e = "NOT(Wildtype[gene=KIT] & Wildtype[gene=PDGFRA])"
    assert "double_negated_wildtype" in checks_of("NOT(KIT and PDGFRA wild-type)", e)


def test_empty_for_a_named_inclusion_gene_is_warned():
    assert "empty_for_named_gene" in checks_of("Succinate Dehydrogenase (SDHB) deficient", "")


def test_empty_for_an_exclusion_only_source_is_not_warned():
    """A source that is nothing but an inexpressible NOT() correctly maps to empty."""
    src = "NOT(actionable genomic alterations, including EGFR mutations, ALK rearrangements)"
    assert "empty_for_named_gene" not in checks_of(src, "")


def test_mixed_wildtype_and_negation_styling_is_not_a_defect():
    """WITHDRAWN check: the source makes two different claims, so both spellings are correct."""
    e = f"Wildtype[gene=FLT3] & NOT({SV('KMT2A')})"
    assert not any("wildtype_style" in c for c in checks_of("FLT3 wild-type AND NOT(KMT2A mutation)", e))
    assert not any("wildtype_style" in k for k in CATALOGUE)


def test_a_clean_expression_has_no_findings():
    assert semantic_problems("KRAS G12C mutation",
                             "SmallVariant[gene=KRAS & transcriptImpact.hgvsProteinImpact=p.G12C]") == []


# --------------------------------------------------------------------------- #
# Reconciliation — mechanical fixes and grouping.
# --------------------------------------------------------------------------- #
def test_mechanical_fix_moves_splice_to_coding_effect():
    got, applied = mechanical_fixes(
        "SmallVariant[gene=MET & transcriptImpact.affectedExon=14 & transcriptImpact.effects=SPLICE]")
    assert got == ("SmallVariant[gene=MET & transcriptImpact.affectedExon=14 & "
                   "transcriptImpact.codingEffect=SPLICE]")
    assert applied and "splice_as_effect" in applied[0]


def test_mechanical_fix_rehomes_hla():
    got, applied = mechanical_fixes("PharmocoGenotype[gene=HLA-A & allele=*02:01]")
    assert got == "HlaAllele[gene=HLA-A & allele=*02:01]"
    assert applied


def test_mechanical_fix_leaves_a_real_pharmacogenotype_alone():
    got, applied = mechanical_fixes("PharmocoGenotype[gene=DPYD & allele=*2A]")
    assert got == "PharmocoGenotype[gene=DPYD & allele=*2A]"
    assert applied == []


def test_mechanical_fix_preserves_every_other_term():
    """The regression that motivated moving this out of the LLM: nothing else may change."""
    src = ("SmallVariant[gene=MET & transcriptImpact.affectedExon=14 & transcriptImpact.effects=SPLICE] "
           f"& NOT({SV('EGFR')})")
    got, _ = mechanical_fixes(src)
    assert got.endswith(f"& NOT({SV('EGFR')})")
    assert E.genes_of(got) == E.genes_of(src)


def test_concept_key_ignores_inexpressible_qualifiers():
    assert concept_key("germline or somatic deleterious BRCA1 mutation") == concept_key("BRCA1 mutation")


def test_concept_key_preserves_polarity():
    assert concept_key("EGFR wild-type") != concept_key("EGFR mutation")


def test_groups_fire_when_one_concept_has_two_mappings():
    mapped = {"BRCA1 mutation": SV("BRCA1"), "documented BRCA1 mutation": SV("BRCA2")}
    assert len(find_groups(mapped)) == 1


def test_groups_stay_silent_when_one_concept_agrees():
    mapped = {"BRCA1 mutation": SV("BRCA1"), "documented BRCA1 mutation": SV("BRCA1")}
    assert find_groups(mapped) == []


def test_groups_do_not_form_on_an_incidental_gene_overlap():
    """The REJECTED rule: 'EGFR wild type' and an unrelated criterion mentioning EGFR are not one group."""
    mapped = {
        "EGFR wild-type": "Wildtype[gene=EGFR]",
        "ALK fusion positivity AND NOT(EGFR resistance mutation)":
            f"Fusion[geneStart=ALK | geneEnd=ALK] & NOT({SV('EGFR')})",
    }
    assert find_groups(mapped) == []


# --------------------------------------------------------------------------- #
# Gene-symbol validity — the deterministic gate for the `PRKC` / `YAP` failure mode.
# --------------------------------------------------------------------------- #
def test_family_root_used_as_a_gene_is_an_error():
    assert "family_root_as_gene" in checks_of("PRKC fusion", "Fusion[geneStart=PRKC | geneEnd=PRKC]")


def test_legacy_alias_without_its_hgnc_number_is_an_error():
    assert "family_root_as_gene" in checks_of("functional YAP fusion", "Fusion[geneStart=YAP | geneEnd=YAP]")


def test_expanded_family_members_are_clean():
    e = "Fusion[geneStart=PRKCA | geneEnd=PRKCA] | Fusion[geneStart=PRKCB | geneEnd=PRKCB]"
    found = checks_of("PRKC fusion", e)
    assert "family_root_as_gene" not in found and "unknown_gene_symbol" not in found


def test_hla_gene_is_not_treated_as_an_unknown_symbol():
    assert checks_of("HLA-A*02:01-positive", "HlaAllele[gene=HLA-A & allele=*02:01]") == set()


def test_literal_set_separator_cannot_collide_with_an_in_bracket_fusion_or():
    """Regression: splitting a literal_set key on '|' over-counted `Fusion[geneStart=X | geneEnd=X]` as 2 literals,
    which made the review's regression detector cry wolf on every expression using that idiom."""
    ls = E.literal_set("Fusion[geneStart=NTRK1 | geneEnd=NTRK1]")
    assert len(ls) == 1
    only = next(iter(ls))
    assert len(only.split(E.LITERAL_SEP)) == 1
    assert E.LITERAL_SEP not in "Fusion[geneStart=NTRK1 | geneEnd=NTRK1]"


def test_literal_set_counts_a_real_conjunction_correctly():
    ls = E.literal_set(f"{SV('A')} & Fusion[geneStart=B | geneEnd=B]")
    assert len(ls) == 1
    assert len(next(iter(ls)).split(E.LITERAL_SEP)) == 2


def test_concept_key_does_not_drop_a_class_naming_qualifier():
    """'sensitizing'/'activating' name a class with established membership, so they change the concept. Dropping
    them merged `EGFR mutation` with `sensitizing EGFR mutation` and the adjudicator collapsed the sensitising
    expansion to the bare gene — an over-exclusion inside NOT()."""
    assert concept_key("sensitizing EGFR mutation") != concept_key("EGFR mutation")
    assert concept_key("activating EGFR mutation") != concept_key("EGFR mutation")


def test_concept_key_still_drops_a_genuine_significance_qualifier():
    assert concept_key("deleterious BRCA1 mutation") == concept_key("BRCA1 mutation")
    assert concept_key("documented BRCA1 mutation") == concept_key("BRCA1 mutation")


def test_sensitizing_and_plain_gene_do_not_form_a_group():
    mapped = {
        "EGFR mutation": SV("EGFR"),
        "sensitizing EGFR mutation": "SmallVariant[gene=EGFR & transcriptImpact.hgvsProteinImpact=p.L858R]",
    }
    assert find_groups(mapped) == []


# --------------------------------------------------------------------------- #
# The stage-2 DISPATCH. Until 2026-08-04 the gene column had no stage 2 at all — it short-circuited to
# `refined[value] = code`. These guard the wiring, which is exactly the kind of thing that regresses silently.
# --------------------------------------------------------------------------- #
def test_shared_reconcile_column_dispatches_gene_alteration_to_its_own_stage_2(monkeypatch):
    from aus_trial_universe.tasks.eligibility.mapping import reconcile as shared
    from aus_trial_universe.tasks.eligibility.mapping.gene_alteration import reconcile as ga

    seen = {}

    def fake(client, mapping, *, workers, max_attempts, use_reviewer):
        seen.update(mapping=mapping, workers=workers, max_attempts=max_attempts)
        return {"x": "OUT"}, [], 0

    monkeypatch.setattr(ga, "reconcile_column", fake)
    out, unresolved, n = shared.reconcile_column(
        None, {"x": SV("A")}, column=shared.GENE_ALTERATION,
        workers=4, max_attempts=3, use_reviewer=True)
    assert out == {"x": "OUT"} and seen["mapping"] == {"x": SV("A")}
    assert seen["max_attempts"] == 3, "production flags must reach the gene stage, not be silently defaulted"


def test_gene_stage_2_is_deterministic_without_a_client():
    """`client=None` must run R0/R1/R3 and skip the LLM stages — the offline path the gate and tests rely on."""
    from aus_trial_universe.tasks.eligibility.mapping.gene_alteration import reconcile as ga
    mapping = {
        "EGFR mutation": SV("EGFR"),
        # a tautological conjunct: canonicalisation must collapse it with no LLM in the loop
        "EGFR mutated disease AND EGFR alteration mediating resistance":
            f"{SV('EGFR')} & ({SV('EGFR')} | GainDeletion[gene=EGFR & type=GAIN])",
    }
    final, unresolved, n_groups = ga.reconcile_column(None, mapping, workers=2, max_attempts=3, use_reviewer=False)
    assert final["EGFR mutation"] == SV("EGFR")
    assert final["EGFR mutated disease AND EGFR alteration mediating resistance"] == SV("EGFR")
    assert unresolved == []


def test_gene_stage_2_output_is_canonical_and_idempotent():
    from aus_trial_universe.tasks.eligibility.mapping.gene_alteration import reconcile as ga
    mapping = {"src": f"{SV('B')} | {SV('A')}"}
    final, _u, _g = ga.reconcile_column(None, mapping, workers=1, max_attempts=3, use_reviewer=False)
    assert final["src"] == E.canonicalise(final["src"]), "stage 2 must emit canonical form"


def test_mechanical_fixes_run_without_an_llm():
    """A pure substitution must never need a client — R0a is the reason the repairer stopped over-reaching."""
    from aus_trial_universe.tasks.eligibility.mapping.gene_alteration import reconcile as ga
    mapping = {"MET exon 14 skipping":
               "SmallVariant[gene=MET & transcriptImpact.affectedExon=14 & transcriptImpact.effects=SPLICE]"}
    final, unresolved, _ = ga.reconcile_column(None, mapping, workers=1, max_attempts=3, use_reviewer=False)
    assert "codingEffect=SPLICE" in final["MET exon 14 skipping"]
    assert "effects=SPLICE" not in final["MET exon 14 skipping"]
    assert unresolved == [], "the mechanical fix should leave no error-severity residue"


# --------------------------------------------------------------------------- #
# Check narrowings from the 2026-08-06 corpus audit. Each is pinned to the REAL live value that exposed it, so a
# future widening of the check re-breaks a case we know is correct rather than passing an abstract example.
# --------------------------------------------------------------------------- #
def test_fusion_driver_gain_ignores_a_negated_amplification():
    """`NOT(NTRK gene amplification)` is the source's own exclusion. Excluding a gain cannot over-expand."""
    from aus_trial_universe.tasks.eligibility.mapping.gene_alteration.checks import semantic_problems
    src = "NTRK gene fusion AND NOT(NTRK gene amplification) AND NOT(NTRK point mutation)"
    expr = ("(Fusion[geneEnd=NTRK1] | Fusion[geneEnd=NTRK2] | Fusion[geneEnd=NTRK3]) "
            "& NOT(GainDeletion[gene=NTRK1 & type=GAIN]) & NOT(GainDeletion[gene=NTRK2 & type=GAIN])")
    assert not [f for f in semantic_problems(src, expr) if f.check == "fusion_driver_with_gain"]


def test_fusion_driver_gain_ignores_an_explicitly_stated_amplification():
    """`ROS1 amplification` MUST map to type=GAIN. The rule is about expanding an UNSPECIFIED alteration."""
    from aus_trial_universe.tasks.eligibility.mapping.gene_alteration.checks import semantic_problems
    found = semantic_problems("ROS1 amplification", "GainDeletion[gene=ROS1 & type=GAIN]")
    assert not [f for f in found if f.check == "fusion_driver_with_gain"]


def test_fusion_driver_gain_still_fires_on_an_unspecified_alteration():
    """The narrowing must not disarm the check: an unspecified ALK alteration must not gain type=GAIN."""
    from aus_trial_universe.tasks.eligibility.mapping.gene_alteration.checks import semantic_problems
    found = semantic_problems("ALK gene alteration",
                              "SmallVariant[gene=ALK] | GainDeletion[gene=ALK & type=GAIN]")
    assert [f for f in found if f.check == "fusion_driver_with_gain"]


def test_arm_terms_not_compound_ignores_an_exclusion_of_distinct_events():
    """del17p and monosomy 17 are DIFFERENT events, so three Arm terms in a NOT() is correct, not a defect."""
    from aus_trial_universe.tasks.eligibility.mapping.gene_alteration.checks import semantic_problems
    src = "NOT(TP53 mutation, del17p or monosomy 17 at diagnosis)"
    expr = ("NOT(SmallVariant[gene=TP53]) "
            "& NOT(Arm[(chromosome=17 & arm=p & type=ARM_LOSS) & (chromosome=17 & arm=q & type=ARM_LOSS)]) "
            "& NOT(Arm[chromosome=17 & arm=p & type=ARM_LOSS])")
    assert not [f for f in semantic_problems(src, expr) if f.check == "arm_terms_not_compound"]


def test_mapk3_is_an_attested_gene_symbol():
    """MAPK3 (ERK1) is a genuine HGNC symbol; its absence warned on a correct RAS-MEK-ERK pathway expansion."""
    from aus_trial_universe.tasks.eligibility.mapping.gene_alteration.checks import semantic_problems
    found = semantic_problems("documented genetic alteration in the RAS-MEK-ERK signalling pathway",
                              "SmallVariant[gene=MAPK3]")
    assert not [f for f in found if f.check in ("unknown_gene_symbol", "family_root_as_gene")]


def test_pi3k_catalytic_subunits_are_attested():
    """PIK3CB/CD/CG (p110 beta/delta/gamma) are genuine HGNC symbols. The 2026-08-06 prompt correctly expands
    'phosphoinositide 3-kinase' to the family, which warned only because the resource did not list them."""
    from aus_trial_universe.tasks.eligibility.mapping.gene_alteration.checks import semantic_problems
    expr = " | ".join(f"SmallVariant[gene={g}]" for g in ("PIK3CA", "PIK3CB", "PIK3CD", "PIK3CG"))
    found = semantic_problems("mutated phosphoinositide 3-kinase", expr)
    assert not [f for f in found if f.check in ("unknown_gene_symbol", "family_root_as_gene")]
