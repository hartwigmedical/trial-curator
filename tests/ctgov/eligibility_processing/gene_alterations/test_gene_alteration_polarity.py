from __future__ import annotations

import pandas as pd

from aus_trial_universe.ctgov.eligibility.ii_process_eligibility_criteria.gene_alterations.gene_alteration_pipeline import (
    _polarity_from_rule_and_not,
    collapse_to_trial_level,
)


def _collapse_one_trial(rows: list[dict[str, str]]) -> pd.DataFrame:
    return collapse_to_trial_level(pd.DataFrame(rows))


def test_include_notcriterion_negative_aga_maps_to_exclusive_nct05215340() -> None:
    """
    NCT05215340:
    INCLUDE No known AGAs in NTRK/BRAF/RET/MET...
    rule_exclude=False, under_not_criterion=True

    This should remain exclusive.
    """
    assert _polarity_from_rule_and_not(
        rule_exclude=False,
        under_not_criterion=True,
    ) == "exclusive"

    out = _collapse_one_trial(
        [
            {
                "nct_id": "NCT05215340",
                "polarity": "exclusive",
                "gene_alteration_curation": "SmallVariant[gene=NTRK1]",
            },
            {
                "nct_id": "NCT05215340",
                "polarity": "inclusive",
                "gene_alteration_curation": "SmallVariant[gene=KRAS]",
            },
        ]
    )

    assert len(out) == 1
    row = out.iloc[0]
    assert row["gene_alteration_inclusive"] == "SmallVariant[gene=KRAS]"
    assert row["gene_alteration_exclusive"] == "NOT(SmallVariant[gene=NTRK1])"


def test_exclude_notcriterion_tp53_mutation_maps_to_exclusive_nct03964233() -> None:
    """
    NCT03964233:
    EXCLUDE a documented amino-acid altering mutation in TP53...
    rule_exclude=True, under_not_criterion=True

    Under the CTGov curation convention this is exclusionary, not inclusive.
    """
    assert _polarity_from_rule_and_not(
        rule_exclude=True,
        under_not_criterion=True,
    ) == "exclusive"

    out = _collapse_one_trial(
        [
            {
                "nct_id": "NCT03964233",
                "polarity": "inclusive",
                "gene_alteration_curation": (
                    "SmallVariant[gene=MDM2] | "
                    "GainDeletion[gene=MDM2 & type=GAIN] | "
                    "Wildtype[TP53]"
                ),
            },
            {
                "nct_id": "NCT03964233",
                "polarity": "exclusive",
                "gene_alteration_curation": (
                    "SmallVariant[gene=TP53] | "
                    "GainDeletion[gene=TP53 & type=HOM_DEL] | "
                    "Disruption[gene=TP53]"
                ),
            },
        ]
    )

    assert len(out) == 1
    row = out.iloc[0]

    assert row["gene_alteration_inclusive"] == (
        "SmallVariant[gene=MDM2] | "
        "GainDeletion[gene=MDM2 & type=GAIN] | "
        "Wildtype[TP53]"
    )
    assert row["gene_alteration_exclusive"] == (
        "NOT(SmallVariant[gene=TP53]) & "
        "NOT(GainDeletion[gene=TP53 & type=HOM_DEL]) & "
        "NOT(Disruption[gene=TP53])"
    )


def test_exclude_notcriterion_non_detrimental_brca_maps_to_exclusive_nct01844986() -> None:
    """
    NCT01844986:
    INCLUDE deleterious/suspected deleterious BRCA mutation.
    EXCLUDE non-detrimental BRCA1/BRCA2 mutations.
    rule_exclude=True, under_not_criterion=True should be exclusive.
    """
    assert _polarity_from_rule_and_not(
        rule_exclude=True,
        under_not_criterion=True,
    ) == "exclusive"

    out = _collapse_one_trial(
        [
            {
                "nct_id": "NCT01844986",
                "polarity": "inclusive",
                "gene_alteration_curation": (
                    "SmallVariant[gene=BRCA1] | "
                    "GainDeletion[gene=BRCA1 & type=HOM_DEL] | "
                    "Disruption[gene=BRCA1] | "
                    "SmallVariant[gene=BRCA2] | "
                    "GainDeletion[gene=BRCA2 & type=HOM_DEL] | "
                    "Disruption[gene=BRCA2]"
                ),
            },
            {
                "nct_id": "NCT01844986",
                "polarity": "exclusive",
                "gene_alteration_curation": (
                    "SmallVariant[gene=BRCA1] | "
                    "GainDeletion[gene=BRCA1 & type=HOM_DEL] | "
                    "Disruption[gene=BRCA1]"
                ),
            },
            {
                "nct_id": "NCT01844986",
                "polarity": "exclusive",
                "gene_alteration_curation": (
                    "SmallVariant[gene=BRCA2] | "
                    "GainDeletion[gene=BRCA2 & type=HOM_DEL] | "
                    "Disruption[gene=BRCA2]"
                ),
            },
        ]
    )

    assert len(out) == 1
    row = out.iloc[0]

    assert "SmallVariant[gene=BRCA1]" in row["gene_alteration_inclusive"]
    assert "SmallVariant[gene=BRCA2]" in row["gene_alteration_inclusive"]

    assert "NOT(SmallVariant[gene=BRCA1])" in row["gene_alteration_exclusive"]
    assert "NOT(GainDeletion[gene=BRCA1 & type=HOM_DEL])" in row["gene_alteration_exclusive"]
    assert "NOT(Disruption[gene=BRCA1])" in row["gene_alteration_exclusive"]
    assert "NOT(SmallVariant[gene=BRCA2])" in row["gene_alteration_exclusive"]
    assert "NOT(GainDeletion[gene=BRCA2 & type=HOM_DEL])" in row["gene_alteration_exclusive"]
    assert "NOT(Disruption[gene=BRCA2])" in row["gene_alteration_exclusive"]