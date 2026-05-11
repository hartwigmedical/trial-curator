from pathlib import Path


SQL_PATH = Path("sql/drug_ontology/005_fda_classification_schema.sql")


def test_fda_sql_defines_simplified_tables_and_export_views():
    sql = SQL_PATH.read_text(encoding="utf-8")

    assert "CREATE TABLE drug_classification.fda_product_ingredient_component" in sql
    assert "CREATE TABLE drug_classification.input_drug_name_fda_anchor" in sql
    assert "CREATE TABLE drug_classification.rxnorm_fda_anchor_product_mapping" in sql
    assert "CREATE OR REPLACE VIEW drug_classification.fda_anchor_product_mapping_export" in sql
    assert "CREATE OR REPLACE VIEW drug_classification.ctgov_intervention_fda_link_export" in sql


def test_fda_sql_does_not_use_old_ctgov_term_expanded_tables_or_payloads():
    sql = SQL_PATH.read_text(encoding="utf-8")

    assert "ctgov_drug_term_fda_anchor" not in sql
    assert "ctgov_drug_term_fda_evidence_view" not in sql
    assert "resolution_payload" not in sql
    assert "manual_review_needed" not in sql


def test_fda_link_export_is_keys_only():
    sql = SQL_PATH.read_text(encoding="utf-8")
    link_view = sql.split(
        "CREATE OR REPLACE VIEW drug_classification.ctgov_intervention_fda_link_export AS",
        1,
    )[1]

    # Required key/link columns.
    for expression in [
        "l.ctgov_intervention_key",
        "l.nct_id",
        "l.intervention_index",
        "l.input_drug_name",
        "a.rxnorm_fda_anchor_rxcui",
    ]:
        assert expression in link_view

    # Product/application evidence must stay in fda_anchor_product_mapping_export,
    # not be duplicated into the intervention link export.
    #
    # Use qualified expressions here because "drug_name" is legitimately present
    # inside the key column name "input_drug_name".
    for duplicated_product_expression in [
        "c.appl_no",
        "c.product_no",
        "c.drug_name",
        "c.active_ingredient_component",
        "c.marketing_status_description",
        "c.latest_approved_submission_status_date",
    ]:
        assert duplicated_product_expression not in link_view
