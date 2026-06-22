from __future__ import annotations

import pandas as pd

from aus_trial_universe.eligibility_path.anzctr.ii_process_eligibility_criteria.gene_alterations.gene_alteration_pipeline import (
    _polarity_from_rule_and_not,
    collapse_to_trial_level,
)


def _collapse_one_trial(rows: list[dict[str, str]]) -> pd.DataFrame:
    return collapse_to_trial_level(pd.DataFrame(rows))


def test_include_notcriterion_negative_aga_maps_to_exclusive() -> None:
    assert _polarity_from_rule_and_not(
        rule_exclude=False,
        under_not_criterion=True,
    ) == "exclusive"

    out = _collapse_one_trial(
        [
            {
                "trial_id": "ACTRN12605000003673",
                "polarity": "exclusive",
                "gene_alteration_curation": "SmallVariant[gene=NTRK1]",
            },
            {
                "trial_id": "ACTRN12605000003673",
                "polarity": "inclusive",
                "gene_alteration_curation": "SmallVariant[gene=KRAS]",
            },
        ]
    )

    assert len(out) == 1
    row = out.iloc[0]
    assert row["trial_id"] == "ACTRN12605000003673"
    assert row["gene_alteration_inclusive"] == "SmallVariant[gene=KRAS]"
    assert row["gene_alteration_exclusive"] == "NOT(SmallVariant[gene=NTRK1])"


def test_exclude_notcriterion_gene_alteration_maps_to_exclusive() -> None:
    assert _polarity_from_rule_and_not(
        rule_exclude=True,
        under_not_criterion=True,
    ) == "exclusive"

    out = _collapse_one_trial(
        [
            {
                "trial_id": "ACTRN12605000003673",
                "polarity": "inclusive",
                "gene_alteration_curation": (
                    "SmallVariant[gene=MDM2] | "
                    "GainDeletion[gene=MDM2 & type=GAIN] | "
                    "Wildtype[TP53]"
                ),
            },
            {
                "trial_id": "ACTRN12605000003673",
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


def test_trial_level_collapse_dedupes_terms_preserving_order() -> None:
    out = _collapse_one_trial(
        [
            {
                "trial_id": "ACTRN12605000003673",
                "polarity": "inclusive",
                "gene_alteration_curation": "SmallVariant[gene=BRCA1] | SmallVariant[gene=BRCA2]",
            },
            {
                "trial_id": "ACTRN12605000003673",
                "polarity": "inclusive",
                "gene_alteration_curation": "SmallVariant[gene=BRCA1]",
            },
        ]
    )

    assert len(out) == 1
    assert out.loc[0, "gene_alteration_inclusive"] == (
        "SmallVariant[gene=BRCA1] | SmallVariant[gene=BRCA2]"
    )

