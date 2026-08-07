"""COMPONENT 1b — the comparison engine's deterministic half, plus the crosswalk's rendering (no API).

The LLM does exactly two things in this comparison (align the two free-text columns, adjudicate a difference).
Everything else is deterministic and is pinned here — including the two properties that make the comparison
honest: backing our side out to trial grain, and folding the disease-derived alteration into our gene set.
"""
from __future__ import annotations

from aus_trial_universe.analysis.pottr.compare import (
    Side, build_our_side, compare_deterministic, dnf_difference, equivalent, literals, summarise,
)
from aus_trial_universe.analysis.pottr.crosswalk import render
from aus_trial_universe.analysis.pottr.pottr_source import CANCER_TYPE, GENE_ALTERATION
from aus_trial_universe.analysis.pottr.schema import DiseaseDerivedRow


def _export_row(**kw):
    base = {
        "trialId": "NCT1", "trial_arm_id": "NCT1::A", "cancer_type_interpreted": "", "oncotree_code": "",
        "gene_alteration_interpreted": "", "gene_alteration_findingmodel": "",
        "molecular_signature_interpreted": "", "molecular_signature_findingmodel": "",
        "molecular_biomarker_interpreted": "", "prior_therapy_interpreted": "",
    }
    return base | kw


# --- canonical literals ------------------------------------------------------ #
def test_literals_use_the_production_canonicalisers():
    assert literals(CANCER_TYPE, "COADREAD OR LUAD") == {"COADREAD", "LUAD"}
    assert len(literals(GENE_ALTERATION, "SmallVariant[gene=KRAS] | SmallVariant[gene=NRAS]")) == 2


def test_de_morgan_rewrites_count_as_identical():
    """The ex-B2 finding: `A AND NOT(B) AND NOT(C)` and `A AND NOT(B OR C)` are the same criterion in two
    renderings. Comparing strings would report a difference that does not exist."""
    assert equivalent(GENE_ALTERATION,
                      "NOT(SmallVariant[gene=A]) & NOT(SmallVariant[gene=B])",
                      "NOT(SmallVariant[gene=A] | SmallVariant[gene=B])")


def test_cancer_type_exclusions_are_signed_not_discarded():
    """`positive_codes` drops negated codes by definition, so using it alone would make every OncoTree exclusion
    disagreement invisible — `NSCLC AND NOT(LUSC)` would compare equal to plain `NSCLC`."""
    assert literals(CANCER_TYPE, "NSCLC AND NOT(LUSC)") == {"NSCLC", "NOT LUSC"}
    assert literals(CANCER_TYPE, "NSCLC") != literals(CANCER_TYPE, "NSCLC AND NOT(LUSC)")


def test_an_unparseable_expression_is_never_silently_dropped():
    """Dropping it would report the criterion as ABSENT from that side — a false difference, and the one
    failure mode a comparison must not have."""
    assert literals(GENE_ALTERATION, "!! not a finding model !!") == {"<unparsed> !! not a finding model !!"}


# --- backing our side out to trial grain -------------------------------------- #
def test_arms_are_backed_out_and_deduplicated_on_the_mapped_values():
    """Two arms whose free text differs but whose MAPPED values agree are ONE conjunction. POTTR has no arm
    concept, so without this every multi-arm trial would report a spurious shape difference."""
    rows = [
        _export_row(trial_arm_id="NCT1::A", cancer_type_interpreted="advanced NSCLC", oncotree_code="NSCLC"),
        _export_row(trial_arm_id="NCT1::B",
                    cancer_type_interpreted="locally advanced or metastatic non-small cell lung cancer",
                    oncotree_code="NSCLC"),
    ]
    side = build_our_side(rows, {})["NCT1"]
    assert side.rows == 2 and len(side.arms) == 2
    assert len(side.conjunctions) == 1                       # collapsed on the mapped value
    assert side.lits[CANCER_TYPE] == {"NSCLC"}


def test_disease_derived_alteration_joins_into_our_gene_set():
    """Component 2's whole purpose. POTTR reads `VHL disease associated tumors` as a VHL alteration; we record
    the genetics in the cancer-type column. Without this fold-in, POTTR's term scores as a criterion we lack."""
    derived = {"von Hippel-Lindau (VHL) disease associated tumors":
               DiseaseDerivedRow("von Hippel-Lindau (VHL) disease associated tumors", "VHL alteration",
                                 "VHL disease is defined by a germline VHL alteration", "SmallVariant[gene=VHL]")}
    rows = [_export_row(cancer_type_interpreted="von Hippel-Lindau (VHL) disease associated tumors",
                        oncotree_code="Solid tumour")]
    side = build_our_side(rows, derived)["NCT1"]
    assert side.lits[GENE_ALTERATION] == literals(GENE_ALTERATION, "SmallVariant[gene=VHL]")
    assert any(v.startswith("[disease-derived]") for v in side.values[GENE_ALTERATION])


def test_without_the_derived_map_the_gene_set_stays_empty():
    """The counterfactual of the test above — this is the difference component 2 makes."""
    rows = [_export_row(cancer_type_interpreted="von Hippel-Lindau (VHL) disease associated tumors",
                        oncotree_code="Solid tumour")]
    assert build_our_side(rows, {})["NCT1"].lits[GENE_ALTERATION] == set()


# --- Q1, the shape verdict ----------------------------------------------------- #
def test_dnf_difference_names_the_arm_asymmetry():
    ours = Side(arms={"a", "b", "c"}, conjunctions={(1,), (2,), (3,)})
    pottr = Side(rows=1)
    assert "no arm concept" in dnf_difference(pottr, ours)
    assert dnf_difference(Side(rows=2), Side(conjunctions={(1,), (2,)})) == ""


def test_dnf_difference_reports_pottr_being_finer():
    assert "POTTR splits" in dnf_difference(Side(rows=3), Side(conjunctions={(1,)}))


# --- Q2, deterministic set comparison ------------------------------------------- #
def test_matching_columns_produce_a_single_shared_row():
    p, o = Side(), Side()
    p.add(CANCER_TYPE, "Colorectal cancer", "COADREAD")
    o.add(CANCER_TYPE, "metastatic colorectal cancer", "COADREAD")
    rows = compare_deterministic("NCT1", CANCER_TYPE, p, o)
    assert [r.side for r in rows] == ["both"]


def test_divergent_columns_split_into_shared_and_side_only_rows():
    p, o = Side(), Side()
    p.add(CANCER_TYPE, "Colorectal cancer", "COADREAD OR LUAD")
    o.add(CANCER_TYPE, "colorectal cancer", "COADREAD")
    sides = {r.side for r in compare_deterministic("NCT1", CANCER_TYPE, p, o)}
    assert sides == {"both", "pottr_only"}


def test_same_disease_at_different_oncotree_depths_is_not_a_disagreement():
    """`BLADDER` vs `BLCA` and `PAAD` vs `PANCREAS` are the commonest shape of apparent difference between the
    two curations — one side simply chose a finer node. Pairing them off deterministically keeps the
    pottr_only / ours_only buckets meaning what they say."""
    p, o = Side(), Side()
    p.add(CANCER_TYPE, "Bladder cancer", "BLADDER")
    o.add(CANCER_TYPE, "transitional cell carcinoma of the bladder", "BLCA")
    rows = compare_deterministic("NCT1", CANCER_TYPE, p, o)
    assert [r.side for r in rows] == ["both"]
    assert "different granularity" in rows[0].detail


def test_unrelated_codes_remain_a_real_difference():
    p, o = Side(), Side()
    p.add(CANCER_TYPE, "Breast cancer", "BREAST")
    o.add(CANCER_TYPE, "lung adenocarcinoma", "LUAD")
    assert {r.side for r in compare_deterministic("NCT1", CANCER_TYPE, p, o)} == {"pottr_only", "ours_only"}


def test_an_or_group_spanning_two_columns_is_filed_per_atom():
    """`(ERBB2:amplification OR ERBB2:overexpression)` is the standard HER2-positive definition: one arm is a
    copy-number event, the other an IHC readout. 27 groups in the live file span two of our columns, so filing
    the whole group under the first atom's column would misfile half of every one of them."""
    from aus_trial_universe.analysis.pottr.compare import build_pottr_side
    from aus_trial_universe.analysis.pottr.crosswalk import CrosswalkRow
    from aus_trial_universe.analysis.pottr.pottr_source import MOLECULAR_BIOMARKER, parse_row

    cw = {
        "ERBB2:amplification": CrosswalkRow("ERBB2:amplification", "ERBB2", "amplification", GENE_ALTERATION,
                                            "ERBB2 amplification", "GainDeletion[gene=ERBB2 & type=GAIN]"),
        "ERBB2:overexpression": CrosswalkRow("ERBB2:overexpression", "ERBB2", "overexpression",
                                             MOLECULAR_BIOMARKER, "ERBB2 overexpression"),
    }
    row = parse_row("NCT1", 1, "(ERBB2:amplification OR ERBB2:overexpression)")
    side = build_pottr_side([row], cw)["NCT1"]
    assert side.lits[GENE_ALTERATION] == literals(GENE_ALTERATION, "GainDeletion[gene=ERBB2 & type=GAIN]")
    assert side.values[MOLECULAR_BIOMARKER] == ["overexpression"]


def test_a_criterion_the_vocabulary_cannot_express_stays_visible():
    """If a POTTR term fails to map, contributing nothing would report it as ABSENT from POTTR's side — a false
    agreement, which is worse than a reported difference."""
    p = Side()
    p.add(GENE_ALTERATION, "EGFR alpha-c-helix mutation", "")
    assert p.lits[GENE_ALTERATION] == {"<unmapped> EGFR alpha-c-helix mutation"}
    assert compare_deterministic("NCT1", GENE_ALTERATION, p, Side())[0].side == "pottr_only"


def test_soft_only_differences_are_flagged_on_the_headline():
    """A POTTR soft term is a modelling choice, not a factual claim, so a soft-only difference must be
    separable from a hard one."""
    p, o = Side(rows=1), Side(conjunctions={(1,)})
    p.add(GENE_ALTERATION, "ERBB2 amplification", "GainDeletion[gene=ERBB2 & type=GAIN]", soft=True)
    details = compare_deterministic("NCT1", GENE_ALTERATION, p, o)
    assert summarise("NCT1", p, o, details).soft_only_difference == "true"


# --- crosswalk rendering (deterministic; no LLM) --------------------------------- #
def test_pottr_dsl_renders_into_the_clinical_english_our_mappers_expect():
    assert render("BRAF", "V600E", GENE_ALTERATION) == "BRAF V600E"
    assert render("KRAS", "oncogenic_mutation", GENE_ALTERATION) == "KRAS mutation"
    assert render("EGFR", "exon_19_deletion", GENE_ALTERATION) == "EGFR exon 19 deletion"
    assert render("KRAS", "G12_missense_variant", GENE_ALTERATION) == "KRAS G12 missense variant"
    assert render("EGFR", "codon_719_mutation", GENE_ALTERATION) == "EGFR codon 719 mutation"
    assert render("catype", "Colorectal cancer", CANCER_TYPE) == "Colorectal cancer"
    assert render("microsatellite_instability", "high", "molecular_signature").startswith("microsatellite")
