import uuid

import pytest

from aus_trial_universe.drug_utility_path.ctgov.drug_ontology.sources.atc import load as load_to_postgres
from aus_trial_universe.drug_utility_path.ctgov.drug_ontology.sources.atc.ingredient_to_atc import (
    AtcResolution,
)


def test_mapping_record_keeps_only_essential_atc_fields():
    anchor = load_to_postgres.RxNormIngredientAnchor(
        rxnorm_ingredient_rxcui="282388",
        rxnorm_ingredient_name="imatinib",
    )
    resolution = AtcResolution(
        atc_match_status="MATCHED_ATC",
        atc_code="L01EA01",
        atc_name="imatinib",
        atc_level=5,
        atc_l1_code="L",
        atc_l1_name="Antineoplastic and immunomodulating agents",
        atc_l2_code="L01",
        atc_l2_name="Antineoplastic agents",
        atc_l3_code="L01E",
        atc_l3_name="Protein kinase inhibitors",
        atc_l4_code="L01EA",
        atc_l4_name="BCR-ABL tyrosine kinase inhibitors",
        atc_l5_code="L01EA01",
        atc_l5_name="imatinib",
    )

    record = load_to_postgres.mapping_record(anchor, resolution, uuid.uuid4())

    assert record["rxnorm_ingredient_rxcui"] == "282388"
    assert record["rxnorm_ingredient_name"] == "imatinib"
    assert record["atc_match_status"] == "MATCHED_ATC"
    assert record["atc_code"] == "L01EA01"
    assert "input_drug_name" not in record
    assert "ctgov_intervention_key" not in record
    assert "resolution_payload" not in record


def test_parser_does_not_accept_python_side_check_tsv_or_rxnorm_source_version():
    parser = load_to_postgres.build_arg_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "--rxnorm_rrf_dir",
                "data/rxnorm",
                "--atc_tree_tsv",
                "data/atc_tree.tsv",
                "--atc_source_version",
                "ATC_test",
                "--output_check_tsv",
                "bad.tsv",
            ]
        )

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "--rxnorm_rrf_dir",
                "data/rxnorm",
                "--atc_tree_tsv",
                "data/atc_tree.tsv",
                "--atc_source_version",
                "ATC_test",
                "--rxnorm_source_version",
                "RxNorm_test",
            ]
        )


def test_fetch_query_uses_distinct_rxnorm_ingredient_anchors_only():
    sql = str(load_to_postgres.FETCH_RXNORM_INGREDIENT_ANCHORS_SQL)

    assert "SELECT DISTINCT" in sql
    assert "rxnorm_ingredient_rxcui" in sql
    assert "match_status = 'MATCHED'" in sql
    assert "input_drug_name" not in sql.split("FROM", 1)[0]
