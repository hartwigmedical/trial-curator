from actin_curator import actin_curator, actin_curator_utils


def test_collapse_singleton_and_wrapper_toxicity_example():
    raw_actin_rule = {
        "AND": [
            {
                "NOT": {
                    "HAS_TOXICITY_CTCAE_OF_AT_LEAST_GRADE_X_IGNORING_ICD_TITLES_Y": [
                        2,
                        "Alopecia",
                    ]
                }
            }
        ]
    }

    expected_rule = {
        "NOT": {
            "HAS_TOXICITY_CTCAE_OF_AT_LEAST_GRADE_X_IGNORING_ICD_TITLES_Y": [
                2,
                "Alopecia",
            ]
        }
    }

    simplified_rule = actin_curator.collapse_singleton_logical_wrappers(raw_actin_rule)
    expr = actin_curator_utils.actin_rule_reformat(simplified_rule)

    assert simplified_rule == expected_rule
    assert expr == (
        "NOT(HAS_TOXICITY_CTCAE_OF_AT_LEAST_GRADE_X_IGNORING_ICD_TITLES_Y[2, 'Alopecia'])"
    )
