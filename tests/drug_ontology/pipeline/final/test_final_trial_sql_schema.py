from pathlib import Path


SQL_PATH = Path("sql/drug_ontology/009_final_trial_drug_classification_summary.sql")


def test_final_trial_sql_defines_one_export_view():
    sql = SQL_PATH.read_text(encoding="utf-8")

    assert "CREATE OR REPLACE VIEW drug_classification.final_trial_drug_classification_summary_export" in sql
    assert "final_intervention_drug_classification_export" in sql


def test_final_trial_sql_does_not_read_or_write_tsv_files():
    sql = SQL_PATH.read_text(encoding="utf-8").lower()

    assert ".tsv" not in sql
    assert "\\copy" not in sql
    assert "copy " not in sql


def test_final_trial_sql_aggregates_sources_to_nct_id_before_joining():
    sql = SQL_PATH.read_text(encoding="utf-8")

    for cte in [
        "trial_base AS",
        "rxnorm_by_trial AS",
        "atc_by_trial AS",
        "fda_by_trial AS",
        "pottr_by_trial AS",
        "chembl_by_trial AS",
    ]:
        assert cte in sql

    assert "GROUP BY f.nct_id" in sql
    assert "GROUP BY l.nct_id" in sql
    assert "GROUP BY p.nct_id" in sql
    assert "GROUP BY c.nct_id" in sql


def test_final_trial_sql_uses_sql_source_tables_and_views():
    sql = SQL_PATH.read_text(encoding="utf-8")

    for source in [
        "drug_classification.final_intervention_drug_classification_export",
        "drug_identity.ctgov_intervention_drug_term_link",
        "drug_identity.ctgov_drug_term_rxnorm_mapping",
        "drug_classification.ctgov_intervention_atc_link_export",
        "drug_classification.atc_ingredient_mapping_export",
        "drug_classification.ctgov_intervention_fda_link_export",
        "drug_classification.fda_anchor_product_mapping_export",
        "drug_classification.pottr_intervention_classification_review_export",
        "drug_classification.chembl_intervention_evidence_review_export",
    ]:
        assert source in sql


def test_final_trial_output_has_expected_key_and_evidence_columns():
    sql = SQL_PATH.read_text(encoding="utf-8")

    for column in [
        "nct_id",
        "drug_intervention_count",
        "intervention_names",
        "input_drug_names",
        "rxnorm_ingredient_names",
        "atc_codes",
        "fda_drug_names",
        "pottr_class_names",
        "matched_chembl_ids",
        "chembl_indication_phase_terms_summary",
        "chembl_oncology_indication_phase_terms_summary",
    ]:
        assert column in sql


def test_final_trial_sql_cleans_tabs_and_newlines_in_text_output():
    sql = SQL_PATH.read_text(encoding="utf-8")

    assert "E'[\\\\r\\\\n\\\\t]+'" in sql
    assert "regexp_replace(COALESCE(final_raw.intervention_names" in sql
    assert "regexp_replace(COALESCE(final_raw.chembl_mechanism_comment_summaries" in sql


def test_final_trial_sql_caps_text_cells_below_excel_limit():
    sql = SQL_PATH.read_text(encoding="utf-8")

    assert "length(regexp_replace(COALESCE(final_raw.chembl_indication_phase_terms_summary" in sql
    assert "left(regexp_replace(COALESCE(final_raw.chembl_indication_phase_terms_summary" in sql
    assert "29982" in sql
    assert "... [TRUNCATED]" in sql
