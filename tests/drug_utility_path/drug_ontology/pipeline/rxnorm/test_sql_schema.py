from pathlib import Path


SQL_PATH = Path("sql/drug_ontology/003_ctgov_drug_terms_rxnorm.sql")


def test_part2_sql_defines_only_essential_export_views():
    sql = SQL_PATH.read_text(encoding="utf-8")

    assert "CREATE OR REPLACE VIEW drug_identity.rxnorm_drug_term_mapping_export" in sql
    assert "CREATE OR REPLACE VIEW drug_identity.ctgov_intervention_drug_term_link_export" in sql


def test_part2_mapping_export_keeps_only_essential_rxnorm_columns():
    sql = SQL_PATH.read_text(encoding="utf-8")

    expected_columns = [
        "input_drug_name",
        "match_status",
        "matched_term",
        "rxnorm_rxcui",
        "rxnorm_canonical_name",
        "rxnorm_term_type",
        "rxnorm_ingredient_rxcui",
        "rxnorm_ingredient_name",
        "rxnorm_ingredient_term_type",
    ]

    for column in expected_columns:
        assert column in sql

    assert "manual_review_needed" not in sql
    assert "rxnorm_source_version" not in sql
    assert "candidate_summary" not in sql
    assert "candidate_count" not in sql
    assert "input_drug_name_normalized" not in sql


def test_part2_link_export_keeps_only_essential_link_columns():
    sql = SQL_PATH.read_text(encoding="utf-8")

    assert "ctgov_intervention_key" in sql
    assert "nct_id" in sql
    assert "intervention_index" in sql
    assert "input_drug_name" in sql
