from pathlib import Path


SQL_PATH = Path("sql/drug_ontology/010_topograph_oncology_kb_schema.sql")


def _sql() -> str:
    return SQL_PATH.read_text()


def _final_view(sql: str) -> str:
    return sql.split(
        "CREATE OR REPLACE VIEW oncology_kb.ctgov_trial_topograph_summary_export AS",
        maxsplit=1,
    )[1]


def test_topograph_sql_uses_actual_ctgov_drug_term_link_table():
    sql = _sql()

    assert "drug_identity.ctgov_intervention_drug_term_link" in sql
    assert "drug_identity.ctgov_intervention_drug_term AS" not in sql
    assert "observed_input_drug_name" not in sql
    assert "observed_input_drug_name_normalized" not in sql


def test_arm_intervention_context_is_built_from_ctgov_staging():
    sql = _sql()

    assert "CREATE OR REPLACE VIEW oncology_kb.ctgov_topograph_arm_intervention_context AS" in sql
    assert "one row per nct_id x arm_group_label x intervention x input drug term" in sql
    assert "s.row_payload->'intervention_armGroupLabels'" in sql
    assert "jsonb_array_elements_text" in sql
    assert "regexp_split_to_table" in sql
    assert "ON s.nct_id = l.nct_id" in sql
    assert "AND s.intervention_index = l.intervention_index" in sql


def test_arm_regimen_context_is_authoritative_ctgov_side_grain():
    sql = _sql()

    assert "CREATE OR REPLACE VIEW oncology_kb.ctgov_topograph_arm_regimen_context AS" in sql
    assert "one row per nct_id x arm_group_label" in sql
    assert "arm_regimen_component_count" in sql
    assert "arm_regimen_set_key" in sql
    assert "string_agg(DISTINCT input_drug_name_normalized, ' || ' ORDER BY input_drug_name_normalized)" in sql


def test_topograph_options_split_semicolon_then_components_split_plus():
    sql = _sql()

    assert "topograph_therapy_option" in sql
    assert "topograph_therapy_component" in sql
    assert "therapy_option_index" in sql
    assert "component_index" in sql
    assert "component_count" in sql


def test_main_link_requires_exact_arm_regimen_set_match_not_any_drug_overlap():
    sql = _sql()

    assert "exact_regimen_hits AS" in sql
    assert "exact_arm_regimen_set_match" in sql
    assert "exact_normalized_ctgov_arm_regimen_set_to_topograph_therapy_option_set" in sql

    # This is the core rule: CTGov arm A|B|C only matches TOPOGRAPH A+B+C.
    assert "o.topograph_regimen_component_count = a.arm_regimen_component_count" in sql
    assert "o.topograph_regimen_set_key = a.arm_regimen_set_key" in sql

    # The main link must not be implemented as an overlap/subset/any-drug match.
    main_link = sql.split(
        "CREATE OR REPLACE VIEW oncology_kb.ctgov_topograph_therapy_link_export AS",
        maxsplit=1,
    )[1].split(
        "CREATE OR REPLACE VIEW oncology_kb.ctgov_topograph_component_review_export AS",
        maxsplit=1,
    )[0]
    assert "component_norm = input_drug_name_normalized" not in main_link
    assert "input_drug_name_normalized = c.component_norm" not in main_link


def test_final_export_is_arm_first_left_join_not_inner_match_only():
    sql = _sql()
    final = _final_view(sql)

    assert "FROM oncology_kb.ctgov_topograph_arm_regimen_context AS a" in final
    assert "LEFT JOIN exact_evidence AS e" in final
    assert "ON e.nct_id = a.nct_id" in final
    assert "AND e.arm_group_label = a.arm_group_label" in final

    # This prevents the final colleague-facing dump from becoming empty merely
    # because no exact TOPOGRAPH regimen-set matches exist.
    assert "FROM oncology_kb.ctgov_topograph_evidence_review_export AS e;" not in final


def test_final_export_has_only_essential_colleague_facing_columns():
    sql = _sql()
    final = _final_view(sql)

    expected_columns = [
        "AS nct_id",
        "AS arm_group_label",
        "AS arm_intervention_names",
        "AS matched_intervention_names",
        "AS topograph_therapy_option",
        "AS topograph_tier",
        "AS topograph_biomarker",
        "AS topograph_alteration",
        "AS topograph_tumour_type",
        "AS topograph_comments",
        "AS topograph_evidence",
    ]
    for column in expected_columns:
        assert column in final

    forbidden_exact_aliases = [
        "AS drug_intervention_count",
        "AS intervention_types",
        "AS intervention_names",
        "AS arm_group_labels",
        "AS arm_input_drug_names",
        "AS matched_intervention_indexes",
        "AS matched_intervention_types",
        "AS matched_input_drug_names",
        "AS topograph_therapy_components",
        "AS ctgov_context_type",
        "AS topograph_match_confidence",
        "AS topograph_match_strategy",
        "AS topograph_source_version",
        "AS topograph_raw_row_index",
    ]
    for forbidden in forbidden_exact_aliases:
        assert forbidden not in final

    assert "topograph_therapy_options" not in final
