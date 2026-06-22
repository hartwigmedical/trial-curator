from __future__ import annotations

import pandas as pd

from aus_trial_universe.eligibility_path.shared.cancer_types.conditions.conditions_mapping import (
    build_conditions_mapping_table,
)


def test_build_conditions_mapping_table_supports_pipe_delimited_registry_terms():
    input_df = pd.DataFrame(
        {
            "trial_id": ["ACTRN1"],
            "HEALTH CONDITION": ["Breast cancer | Lung cancer | Breast cancer"],
        }
    )

    out = build_conditions_mapping_table(
        input_df,
        trial_id_column="trial_id",
        conditions_column="HEALTH CONDITION",
        mapping={
            "Breast cancer": "Breast",
            "Lung cancer": "Non-Small Cell Lung Cancer",
        },
        mapping_ci={},
        parser="pipe",
    )

    assert out.to_dict("records") == [
        {
            "trial_id": "ACTRN1",
            "conditions_original": "Breast cancer | Lung cancer | Breast cancer",
            "conditions_oncotree_curation": "Breast | Non-Small Cell Lung Cancer",
        }
    ]


def test_build_conditions_mapping_table_supports_ctgov_python_list_terms():
    input_df = pd.DataFrame(
        {
            "nctId": ["nct00000001"],
            "conditions": ["['Breast cancer', 'Unknown term']"],
        }
    )

    out = build_conditions_mapping_table(
        input_df,
        trial_id_column="nctId",
        conditions_column="conditions",
        mapping={},
        mapping_ci={"breast cancer": "Breast"},
        parser="python_list",
    )

    assert out.to_dict("records") == [
        {
            "trial_id": "NCT00000001",
            "conditions_original": "['Breast cancer', 'Unknown term']",
            "conditions_oncotree_curation": "Breast",
        }
    ]
