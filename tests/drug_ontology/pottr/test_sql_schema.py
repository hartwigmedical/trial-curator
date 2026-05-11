from pathlib import Path
import re


SQL_PATH = Path("sql/drug_ontology/006_pottr_classification_schema.sql")


def test_pottr_sql_defines_simplified_tables_and_export_views():
    sql = SQL_PATH.read_text(encoding="utf-8")

    assert "CREATE TABLE drug_classification.pottr_drug_concept" in sql
    assert "CREATE TABLE drug_classification.pottr_drug_class_assignment" in sql
    assert "CREATE TABLE drug_classification.input_drug_name_pottr_anchor" in sql
    assert "CREATE TABLE drug_classification.pottr_anchor_class_mapping" in sql
    assert "CREATE OR REPLACE VIEW drug_classification.pottr_anchor_class_mapping_export" in sql
    assert "CREATE OR REPLACE VIEW drug_classification.ctgov_intervention_pottr_link_export" in sql
    assert "CREATE OR REPLACE VIEW drug_classification.pottr_intervention_classification_review_export" in sql


def test_pottr_sql_does_not_use_old_ctgov_expanded_tables_or_payloads():
    sql = SQL_PATH.read_text(encoding="utf-8")

    assert "ctgov_pottr_anchor_drug_term" not in sql
    assert "ctgov_drug_term_pottr_evidence_view" not in sql
    assert "resolution_payload" not in sql
    assert "manual_review_needed" not in sql


def test_pottr_link_export_is_keys_only():
    sql = SQL_PATH.read_text(encoding="utf-8")
    link_view = sql.split(
        "CREATE OR REPLACE VIEW drug_classification.ctgov_intervention_pottr_link_export AS",
        1,
    )[1].split(
        "CREATE OR REPLACE VIEW drug_classification.pottr_intervention_classification_review_export AS",
        1,
    )[0]

    for expression in [
        "l.ctgov_intervention_key",
        "l.nct_id",
        "l.intervention_index",
        "l.input_drug_name",
        "a.pottr_anchor_key",
    ]:
        assert expression in link_view

    for duplicated_mapping_expression in [
        "pottr_class_name",
        "direct_class_name",
        "pottr_canonical_drug_name",
        "class_path",
        "class_relation",
    ]:
        assert duplicated_mapping_expression not in link_view


def test_pottr_final_review_export_is_human_facing_and_hides_internal_keys():
    sql = SQL_PATH.read_text(encoding="utf-8")
    review_view = sql.split(
        "CREATE OR REPLACE VIEW drug_classification.pottr_intervention_classification_review_export AS",
        1,
    )[1]

    # The view has a CTE with its own SELECT/FROM, so do not split on the first
    # SELECT or first FROM. Capture the final SELECT DISTINCT projection only.
    match = re.search(
        r"\)\s*SELECT\s+DISTINCT(?P<projection>.*?)"
        r"FROM\s+drug_classification\.ctgov_intervention_pottr_link_export\s+AS\s+l",
        review_view,
        flags=re.DOTALL,
    )

    assert match is not None
    final_select_block = match.group("projection")

    for column in [
        "l.nct_id",
        "l.intervention_index",
        "s.intervention_type",
        "s.intervention_name",
        "l.input_drug_name",
        "pottr_match_status",
        "matched_pottr_drug_name",
        "direct_class_name",
        "pottr_class_name",
        "class_relation",
        "class_depth_from_direct",
        "class_path",
        "class_in_hierarchy",
    ]:
        assert column in final_select_block

    for hidden_key in [
        "pottr_anchor_key",
        "pottr_concept_key",
        "pottr_class_assignment_key",
        "ctgov_intervention_key",
    ]:
        assert hidden_key not in final_select_block