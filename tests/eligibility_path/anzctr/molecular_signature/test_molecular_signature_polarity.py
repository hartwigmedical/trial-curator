from __future__ import annotations

import pandas as pd

from aus_trial_universe.eligibility_path.anzctr.ii_process_eligibility_criteria.molecular_signature.molecular_signature_pipeline import (
    _polarity_from_rule_and_not,
    collapse_to_trial_level,
)


def test_include_positive_molecular_signature_is_inclusive() -> None:
    assert _polarity_from_rule_and_not(
        rule_exclude=False,
        under_not_criterion=False,
    ) == "inclusive"


def test_include_notcriterion_molecular_signature_is_exclusive() -> None:
    assert _polarity_from_rule_and_not(
        rule_exclude=False,
        under_not_criterion=True,
    ) == "exclusive"


def test_exclude_notcriterion_molecular_signature_is_exclusive() -> None:
    assert _polarity_from_rule_and_not(
        rule_exclude=True,
        under_not_criterion=True,
    ) == "exclusive"


def test_trial_level_collapse_places_exclusion_rule_under_exclusive() -> None:
    mapped_df = pd.DataFrame(
        [
            {
                "trial_id": "ACTRN12605000003673",
                "polarity": "inclusive",
                "molecular_signature_curation": "homologousRecombination[ChordStatus=HR_DEFICIENT]",
            },
            {
                "trial_id": "ACTRN12605000003673",
                "polarity": "exclusive",
                "molecular_signature_curation": "MicrosatelliteStability[PurpleMicrosatelliteStatus=MSI]",
            },
        ]
    )

    out = collapse_to_trial_level(mapped_df)

    assert len(out) == 1
    assert out.loc[0, "trial_id"] == "ACTRN12605000003673"
    assert out.loc[0, "molecular_signature_inclusive"] == (
        "homologousRecombination[ChordStatus=HR_DEFICIENT]"
    )
    assert out.loc[0, "molecular_signature_exclusive"] == (
        "NOT(MicrosatelliteStability[PurpleMicrosatelliteStatus=MSI])"
    )
