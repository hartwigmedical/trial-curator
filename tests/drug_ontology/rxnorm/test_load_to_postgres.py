import argparse
import json

import pytest

from aus_trial_universe.ctgov.drug_ontology.common.classification_schema import (
    COL_INTERVENTION_ALL_ALIASES_NORMALISED,
)
from aus_trial_universe.ctgov.drug_ontology.rxnorm import load_to_postgres


def staging_row(
    intervention_type="DRUG",
    intervention_name="Imatinib",
    normalised="Imatinib | Gleevec | STI571",
):
    return {
        "nct_id": "NCT00000001",
        "intervention_index": 0,
        "intervention_type": intervention_type,
        "intervention_name": intervention_name,
        "row_payload": json.dumps(
            {
                COL_INTERVENTION_ALL_ALIASES_NORMALISED: normalised,
                "intervention_otherNames": "raw other name should not be used",
                "intervention_all_aliases_normalised_full": "full alias should not be used",
            }
        ),
    }


def test_input_drug_names_use_only_intervention_all_aliases_normalised():
    terms = load_to_postgres.input_drug_names_for_staging_row(staging_row())

    assert terms == ["Imatinib", "Gleevec", "STI571"]
    assert "raw other name should not be used" not in terms
    assert "full alias should not be used" not in terms


def test_input_drug_names_exclude_other_interventions_even_if_aliases_present():
    terms = load_to_postgres.input_drug_names_for_staging_row(
        staging_row(
            intervention_type="OTHER",
            intervention_name="Observation",
            normalised="Observation | Usual care",
        )
    )

    assert terms == []


def test_input_drug_names_do_not_fallback_to_raw_intervention_name_when_aliases_blank():
    terms = load_to_postgres.input_drug_names_for_staging_row(
        staging_row(
            intervention_type="DRUG",
            intervention_name="Placebo",
            normalised="",
        )
    )

    assert terms == []


def test_ctgov_intervention_key_is_stable():
    assert load_to_postgres.ctgov_intervention_key("NCT00000001", 3) == "NCT00000001:3"


class FakeConnection:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((str(sql), params))

        class Result:
            def scalar_one(self):
                return 1

        return Result()


def test_link_insert_uses_only_essential_link_columns():
    conn = FakeConnection()

    load_to_postgres.insert_intervention_drug_term_link(
        conn=conn,
        staging_row={
            "nct_id": "NCT00000001",
            "intervention_index": 0,
        },
        input_drug_name="Imatinib",
    )

    sql_text, params = conn.calls[0]

    assert "ctgov_intervention_key" in sql_text
    assert params == {
        "ctgov_intervention_key": "NCT00000001:0",
        "nct_id": "NCT00000001",
        "intervention_index": 0,
        "input_drug_name": "Imatinib",
    }


def test_parser_does_not_accept_python_side_check_tsv_arg():
    parser = load_to_postgres.build_arg_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "--rxnorm_rrf_dir",
                "data/rxnorm",
                "--rxnorm_source_version",
                "RxNorm_test",
                "--output_check_tsv",
                "bad.tsv",
            ]
        )
