from pathlib import Path


SQL_PATH = Path("sql/drug_ontology/004_atc_classification_schema.sql")


def test_atc_sql_defines_simplified_table_and_export_views():
    sql = SQL_PATH.read_text(encoding="utf-8")

    assert "CREATE TABLE drug_classification.rxnorm_ingredient_atc_mapping" in sql
    assert "CREATE OR REPLACE VIEW drug_classification.atc_ingredient_mapping_export" in sql
    assert "CREATE OR REPLACE VIEW drug_classification.ctgov_intervention_atc_link_export" in sql


def test_atc_sql_does_not_use_old_ctgov_drug_term_atc_mapping_table():
    sql = SQL_PATH.read_text(encoding="utf-8")

    assert "ctgov_drug_term_atc_mapping" not in sql


def test_atc_mapping_export_keeps_atc_annotation_columns():
    sql = SQL_PATH.read_text(encoding="utf-8")

    mapping_view = sql.split(
        "CREATE OR REPLACE VIEW drug_classification.atc_ingredient_mapping_export AS",
        1,
    )[1].split(
        "CREATE OR REPLACE VIEW drug_classification.ctgov_intervention_atc_link_export AS",
        1,
    )[0]

    for column in [
        "rxnorm_ingredient_rxcui",
        "rxnorm_ingredient_name",
        "atc_match_status",
        "atc_code",
        "atc_name",
        "atc_level",
        "atc_l1_code",
        "atc_l5_code",
    ]:
        assert column in mapping_view

    assert "resolution_payload" not in mapping_view
    assert "manual_review_needed" not in mapping_view


def test_atc_link_export_is_keys_only_and_does_not_duplicate_atc_annotation_columns():
    sql = SQL_PATH.read_text(encoding="utf-8")

    link_view = sql.split(
        "CREATE OR REPLACE VIEW drug_classification.ctgov_intervention_atc_link_export AS",
        1,
    )[1]

    for column in [
        "ctgov_intervention_key",
        "nct_id",
        "intervention_index",
        "input_drug_name",
        "rxnorm_ingredient_rxcui",
    ]:
        assert column in link_view

    for duplicated_mapping_column in [
        "atc_match_status",
        "atc_code",
        "atc_name",
        "atc_level",
        "atc_l1_code",
        "atc_l1_name",
        "atc_l2_code",
        "atc_l2_name",
        "atc_l3_code",
        "atc_l3_name",
        "atc_l4_code",
        "atc_l4_name",
        "atc_l5_code",
        "atc_l5_name",
    ]:
        assert duplicated_mapping_column not in link_view
