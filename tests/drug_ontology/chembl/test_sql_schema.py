from pathlib import Path
import re


SQL_PATH = Path("sql/drug_ontology/007_chembl_classification_schema.sql")


def test_chembl_sql_defines_simplified_tables_and_export_views():
    sql = SQL_PATH.read_text(encoding="utf-8")

    assert "CREATE TABLE drug_classification.input_drug_name_chembl_anchor" in sql
    assert "CREATE TABLE drug_classification.chembl_anchor_molecule_mapping" in sql
    assert "CREATE OR REPLACE VIEW drug_classification.ctgov_intervention_chembl_link_export" in sql
    assert "CREATE OR REPLACE VIEW drug_classification.chembl_intervention_evidence_review_export" in sql


def test_chembl_sql_does_not_use_old_ctgov_expanded_tables_or_payloads():
    sql = SQL_PATH.read_text(encoding="utf-8")

    assert "ctgov_chembl_anchor_drug_term" not in sql
    assert "ctgov_drug_term_chembl_evidence_view" not in sql
    assert "resolution_payload" not in sql
    assert "manual_review_needed" not in sql


def test_chembl_link_export_is_keys_only():
    sql = SQL_PATH.read_text(encoding="utf-8")
    link_view = sql.split(
        "CREATE OR REPLACE VIEW drug_classification.ctgov_intervention_chembl_link_export AS",
        1,
    )[1].split(
        "CREATE OR REPLACE VIEW drug_classification.chembl_intervention_evidence_review_export AS",
        1,
    )[0]

    for expression in [
        "l.ctgov_intervention_key",
        "l.nct_id",
        "l.intervention_index",
        "l.input_drug_name",
        "a.chembl_anchor_key",
    ]:
        assert expression in link_view

    for duplicated_evidence_expression in [
        "chembl_id",
        "pref_name",
        "mechanism_summary",
        "oncology_indication",
        "standard_inchi_key",
    ]:
        assert duplicated_evidence_expression not in link_view


def test_chembl_review_export_is_human_facing_and_hides_internal_keys():
    sql = SQL_PATH.read_text(encoding="utf-8")
    review_view = sql.split(
        "CREATE OR REPLACE VIEW drug_classification.chembl_intervention_evidence_review_export AS",
        1,
    )[1]

    match = re.search(
        r"\)\s*SELECT\s+DISTINCT(?P<projection>.*?)"
        r"FROM\s+drug_classification\.ctgov_intervention_chembl_link_export\s+AS\s+l",
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
        "chembl_match_status",
        "matched_chembl_id",
        "matched_chembl_pref_name",
        "molecule_relation_type",
        "chembl_match_strategy",
        "chembl_matched_name",
        "chembl_matched_name_type",
        "structure_type",
        "first_in_class",
        "prodrug",
        "chemical_probe",
        "mechanism_summary",
        "target_summary",
        "target_chembl_ids_summary",
        "target_type_summary",
        "target_organism_summary",
        "target_accessions_summary",
        "direct_interaction_summary",
        "molecular_mechanism_summary",
        "disease_efficacy_summary",
        "mechanism_comment_summary",
        "selectivity_comment_summary",
        "binding_site_comment_summary",
        "indication_phase_terms_summary",
        "oncology_indication_phase_terms_summary",
        "highest_indication_terms_with_phase_summary",
        "highest_oncology_indication_terms_with_phase_summary",
    ]:
        assert column in final_select_block

    for removed_or_hidden in [
        "chembl_anchor_key",
        "chembl_molecule_id",
        "ctgov_intervention_key",
        "standard_inchi_key",
        "chembl_atc_count",
        "chembl_atc_codes_summary",
        "warning_count",
        "warning_summary",
    ]:
        assert removed_or_hidden not in final_select_block
