from aus_trial_universe.drug_utility_path.trial_drug_curation.utils.trial_ids import select_trial_ids


def test_select_trial_ids_deduplicates_and_groups_mixed_registries():
    selection = select_trial_ids(
        [
            " nct00000001 ",
            "NCT00000001",
            "ACTRN12616000689471",
            "bad-id",
        ]
    )

    assert selection.unique_ids == (
        "NCT00000001",
        "ACTRN12616000689471",
        "BAD-ID",
    )
    assert selection.grouped_trial_ids == {
        "ctgov": ["NCT00000001"],
        "anzctr": ["ACTRN12616000689471"],
    }
    assert len(selection.skipped_trials) == 1
    assert selection.skipped_trials[0].trial_id == "BAD-ID"
    assert selection.skipped_trials[0].reason == "unrecognised_trial_id"
