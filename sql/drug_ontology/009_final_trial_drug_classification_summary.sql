CREATE SCHEMA IF NOT EXISTS drug_classification;

DROP VIEW IF EXISTS drug_classification.final_trial_drug_classification_summary_export CASCADE;


-- =============================================================================
-- Final trial-level combined drug classification summary export
-- =============================================================================
--
-- Grain:
--   one row per CTGov trial.
--
-- Unique key:
--   nct_id
--
-- Design rule:
--   Each evidence source is aggregated to nct_id before joining.
--   This avoids ATC × FDA × POTTR × ChEMBL Cartesian row explosion and avoids
--   re-aggregating pipe-delimited intervention-level summaries.

CREATE OR REPLACE VIEW drug_classification.final_trial_drug_classification_summary_export AS
-- Final output text columns are newline-cleaned so the TSV export has one
-- physical line per trial row.
WITH
trial_base AS (
    SELECT
        f.nct_id,
        COUNT(DISTINCT f.ctgov_intervention_key) AS drug_intervention_count,
        string_agg(DISTINCT NULLIF(f.intervention_type, ''), ' | ' ORDER BY NULLIF(f.intervention_type, '')) AS intervention_types,
        string_agg(DISTINCT NULLIF(f.intervention_name, ''), ' | ' ORDER BY NULLIF(f.intervention_name, '')) AS intervention_names,
        string_agg(DISTINCT NULLIF(f.arm_group_labels, ''), ' | ' ORDER BY NULLIF(f.arm_group_labels, '')) AS arm_group_labels,
        string_agg(
            DISTINCT NULLIF(
                CONCAT_WS(
                    ': ',
                    NULLIF(f.intervention_index::text, ''),
                    NULLIF(f.intervention_name, '')
                ),
                ''
            ),
            ' | '
            ORDER BY NULLIF(
                CONCAT_WS(
                    ': ',
                    NULLIF(f.intervention_index::text, ''),
                    NULLIF(f.intervention_name, '')
                ),
                ''
            )
        ) AS intervention_index_name_pairs
    FROM drug_classification.final_intervention_drug_classification_export AS f
    GROUP BY f.nct_id
),

rxnorm_by_trial AS (
    SELECT
        l.nct_id,

        COUNT(DISTINCT l.input_drug_name) AS input_drug_name_count,
        string_agg(DISTINCT NULLIF(l.input_drug_name, ''), ' | ' ORDER BY NULLIF(l.input_drug_name, '')) AS input_drug_names,

        string_agg(DISTINCT NULLIF(r.match_status, ''), ' | ' ORDER BY NULLIF(r.match_status, '')) AS rxnorm_match_statuses,
        COUNT(DISTINCT l.input_drug_name) FILTER (WHERE r.match_status = 'MATCHED') AS rxnorm_matched_input_drug_name_count,
        COUNT(DISTINCT l.input_drug_name) FILTER (WHERE r.match_status IS DISTINCT FROM 'MATCHED') AS rxnorm_unmatched_or_review_input_drug_name_count,
        string_agg(
            DISTINCT NULLIF(CASE WHEN r.match_status IS DISTINCT FROM 'MATCHED' THEN l.input_drug_name ELSE NULL END, ''),
            ' | '
            ORDER BY NULLIF(CASE WHEN r.match_status IS DISTINCT FROM 'MATCHED' THEN l.input_drug_name ELSE NULL END, '')
        ) AS rxnorm_unmatched_or_review_input_drug_names,

        string_agg(DISTINCT NULLIF(r.matched_term, ''), ' | ' ORDER BY NULLIF(r.matched_term, '')) AS rxnorm_matched_terms,
        string_agg(DISTINCT NULLIF(r.rxnorm_rxcui, ''), ' | ' ORDER BY NULLIF(r.rxnorm_rxcui, '')) AS rxnorm_rxcuis,
        string_agg(DISTINCT NULLIF(r.rxnorm_canonical_name, ''), ' | ' ORDER BY NULLIF(r.rxnorm_canonical_name, '')) AS rxnorm_canonical_names,
        string_agg(DISTINCT NULLIF(r.rxnorm_term_type, ''), ' | ' ORDER BY NULLIF(r.rxnorm_term_type, '')) AS rxnorm_term_types,

        COUNT(DISTINCT NULLIF(r.rxnorm_ingredient_rxcui, '')) AS rxnorm_ingredient_count,
        string_agg(DISTINCT NULLIF(r.rxnorm_ingredient_rxcui, ''), ' | ' ORDER BY NULLIF(r.rxnorm_ingredient_rxcui, '')) AS rxnorm_ingredient_rxcuis,
        string_agg(DISTINCT NULLIF(r.rxnorm_ingredient_name, ''), ' | ' ORDER BY NULLIF(r.rxnorm_ingredient_name, '')) AS rxnorm_ingredient_names,
        string_agg(DISTINCT NULLIF(r.rxnorm_ingredient_term_type, ''), ' | ' ORDER BY NULLIF(r.rxnorm_ingredient_term_type, '')) AS rxnorm_ingredient_term_types
    FROM drug_identity.ctgov_intervention_drug_term_link AS l
    LEFT JOIN drug_identity.ctgov_drug_term_rxnorm_mapping AS r
        ON r.input_drug_name = l.input_drug_name
    GROUP BY l.nct_id
),

atc_by_trial AS (
    SELECT
        l.nct_id,

        COUNT(DISTINCT NULLIF(a.atc_code, '')) AS atc_code_count,
        string_agg(DISTINCT NULLIF(a.atc_match_status, ''), ' | ' ORDER BY NULLIF(a.atc_match_status, '')) AS atc_match_statuses,
        string_agg(DISTINCT NULLIF(a.atc_code, ''), ' | ' ORDER BY NULLIF(a.atc_code, '')) AS atc_codes,
        string_agg(DISTINCT NULLIF(a.atc_name, ''), ' | ' ORDER BY NULLIF(a.atc_name, '')) AS atc_names,
        string_agg(DISTINCT NULLIF(a.atc_l1_code, ''), ' | ' ORDER BY NULLIF(a.atc_l1_code, '')) AS atc_l1_codes,
        string_agg(DISTINCT NULLIF(a.atc_l1_name, ''), ' | ' ORDER BY NULLIF(a.atc_l1_name, '')) AS atc_l1_names,
        string_agg(DISTINCT NULLIF(a.atc_l2_code, ''), ' | ' ORDER BY NULLIF(a.atc_l2_code, '')) AS atc_l2_codes,
        string_agg(DISTINCT NULLIF(a.atc_l2_name, ''), ' | ' ORDER BY NULLIF(a.atc_l2_name, '')) AS atc_l2_names,
        string_agg(DISTINCT NULLIF(a.atc_l3_code, ''), ' | ' ORDER BY NULLIF(a.atc_l3_code, '')) AS atc_l3_codes,
        string_agg(DISTINCT NULLIF(a.atc_l3_name, ''), ' | ' ORDER BY NULLIF(a.atc_l3_name, '')) AS atc_l3_names,
        string_agg(DISTINCT NULLIF(a.atc_l4_code, ''), ' | ' ORDER BY NULLIF(a.atc_l4_code, '')) AS atc_l4_codes,
        string_agg(DISTINCT NULLIF(a.atc_l4_name, ''), ' | ' ORDER BY NULLIF(a.atc_l4_name, '')) AS atc_l4_names,
        string_agg(DISTINCT NULLIF(a.atc_l5_code, ''), ' | ' ORDER BY NULLIF(a.atc_l5_code, '')) AS atc_l5_codes,
        string_agg(DISTINCT NULLIF(a.atc_l5_name, ''), ' | ' ORDER BY NULLIF(a.atc_l5_name, '')) AS atc_l5_names
    FROM drug_classification.ctgov_intervention_atc_link_export AS l
    LEFT JOIN drug_classification.atc_ingredient_mapping_export AS a
        ON a.rxnorm_ingredient_rxcui = l.rxnorm_ingredient_rxcui
    GROUP BY l.nct_id
),

fda_by_trial AS (
    SELECT
        l.nct_id,

        COUNT(DISTINCT NULLIF(l.rxnorm_fda_anchor_rxcui, '')) AS fda_anchor_count,
        COUNT(DISTINCT NULLIF(f.fda_product_component_key, '')) AS fda_product_component_count,
        string_agg(DISTINCT NULLIF(l.rxnorm_fda_anchor_rxcui, ''), ' | ' ORDER BY NULLIF(l.rxnorm_fda_anchor_rxcui, '')) AS fda_anchor_rxcuis,
        string_agg(DISTINCT NULLIF(f.rxnorm_fda_anchor_name, ''), ' | ' ORDER BY NULLIF(f.rxnorm_fda_anchor_name, '')) AS fda_anchor_names,
        string_agg(DISTINCT NULLIF(f.rxnorm_fda_anchor_term_type, ''), ' | ' ORDER BY NULLIF(f.rxnorm_fda_anchor_term_type, '')) AS fda_anchor_term_types,
        string_agg(DISTINCT NULLIF(f.fda_link_status, ''), ' | ' ORDER BY NULLIF(f.fda_link_status, '')) AS fda_link_statuses,
        string_agg(DISTINCT NULLIF(f.drug_name, ''), ' | ' ORDER BY NULLIF(f.drug_name, '')) AS fda_drug_names,
        string_agg(DISTINCT NULLIF(f.active_ingredient_component, ''), ' | ' ORDER BY NULLIF(f.active_ingredient_component, '')) AS fda_active_ingredient_components,
        string_agg(
            DISTINCT NULLIF(CONCAT_WS('', NULLIF(f.appl_type, ''), NULLIF(f.appl_no, '')), ''),
            ' | '
            ORDER BY NULLIF(CONCAT_WS('', NULLIF(f.appl_type, ''), NULLIF(f.appl_no, '')), '')
        ) AS fda_application_numbers,
        string_agg(
            DISTINCT NULLIF(CONCAT_WS('-', NULLIF(CONCAT_WS('', NULLIF(f.appl_type, ''), NULLIF(f.appl_no, '')), ''), NULLIF(f.product_no, '')), ''),
            ' | '
            ORDER BY NULLIF(CONCAT_WS('-', NULLIF(CONCAT_WS('', NULLIF(f.appl_type, ''), NULLIF(f.appl_no, '')), ''), NULLIF(f.product_no, '')), '')
        ) AS fda_application_products,
        string_agg(DISTINCT NULLIF(f.marketing_status_description, ''), ' | ' ORDER BY NULLIF(f.marketing_status_description, '')) AS fda_marketing_statuses,
        string_agg(DISTINCT NULLIF(f.latest_approved_submission_status_date, ''), ' | ' ORDER BY NULLIF(f.latest_approved_submission_status_date, '')) AS fda_latest_approved_submission_status_dates,
        string_agg(DISTINCT NULLIF(f.has_approved_submission::text, ''), ' | ' ORDER BY NULLIF(f.has_approved_submission::text, '')) AS fda_has_approved_submission_values,
        bool_or(COALESCE(f.has_approved_submission, false)) AS fda_has_any_approved_submission,
        string_agg(DISTINCT NULLIF(f.sponsor_name, ''), ' | ' ORDER BY NULLIF(f.sponsor_name, '')) AS fda_sponsor_names,
        string_agg(DISTINCT NULLIF(f.form, ''), ' | ' ORDER BY NULLIF(f.form, '')) AS fda_forms,
        string_agg(DISTINCT NULLIF(f.strength_component, ''), ' | ' ORDER BY NULLIF(f.strength_component, '')) AS fda_strength_components
    FROM drug_classification.ctgov_intervention_fda_link_export AS l
    LEFT JOIN drug_classification.fda_anchor_product_mapping_export AS f
        ON f.rxnorm_fda_anchor_rxcui = l.rxnorm_fda_anchor_rxcui
    GROUP BY l.nct_id
),

pottr_by_trial AS (
    SELECT
        p.nct_id,

        COUNT(*) AS pottr_review_row_count,
        COUNT(*) FILTER (WHERE p.pottr_match_status = 'MATCHED_POTTR_CLASS') AS pottr_matched_class_row_count,
        string_agg(DISTINCT NULLIF(p.pottr_match_status, ''), ' | ' ORDER BY NULLIF(p.pottr_match_status, '')) AS pottr_match_statuses,
        string_agg(DISTINCT NULLIF(p.matched_pottr_drug_name, ''), ' | ' ORDER BY NULLIF(p.matched_pottr_drug_name, '')) AS matched_pottr_drug_names,
        string_agg(DISTINCT NULLIF(p.direct_class_name, ''), ' | ' ORDER BY NULLIF(p.direct_class_name, '')) AS pottr_direct_class_names,
        string_agg(DISTINCT NULLIF(p.pottr_class_name, ''), ' | ' ORDER BY NULLIF(p.pottr_class_name, '')) AS pottr_class_names,
        string_agg(DISTINCT NULLIF(p.class_relation, ''), ' | ' ORDER BY NULLIF(p.class_relation, '')) AS pottr_class_relations,
        string_agg(DISTINCT NULLIF(p.class_path, ''), ' | ' ORDER BY NULLIF(p.class_path, '')) AS pottr_class_paths,
        string_agg(DISTINCT NULLIF(p.class_in_hierarchy::text, ''), ' | ' ORDER BY NULLIF(p.class_in_hierarchy::text, '')) AS pottr_class_in_hierarchy_values
    FROM drug_classification.pottr_intervention_classification_review_export AS p
    GROUP BY p.nct_id
),

chembl_by_trial AS (
    SELECT
        c.nct_id,

        COUNT(*) AS chembl_review_row_count,
        COUNT(*) FILTER (WHERE c.chembl_match_status = 'MATCHED_CHEMBL_MOLECULE') AS chembl_matched_molecule_row_count,
        COUNT(DISTINCT NULLIF(c.matched_chembl_id, '')) AS matched_chembl_molecule_count,

        string_agg(DISTINCT NULLIF(c.chembl_match_status, ''), ' | ' ORDER BY NULLIF(c.chembl_match_status, '')) AS chembl_match_statuses,
        string_agg(DISTINCT NULLIF(c.matched_chembl_id, ''), ' | ' ORDER BY NULLIF(c.matched_chembl_id, '')) AS matched_chembl_ids,
        string_agg(DISTINCT NULLIF(c.matched_chembl_pref_name, ''), ' | ' ORDER BY NULLIF(c.matched_chembl_pref_name, '')) AS matched_chembl_pref_names,
        string_agg(DISTINCT NULLIF(c.molecule_relation_type, ''), ' | ' ORDER BY NULLIF(c.molecule_relation_type, '')) AS chembl_molecule_relation_types,
        string_agg(DISTINCT NULLIF(c.chembl_match_strategy, ''), ' | ' ORDER BY NULLIF(c.chembl_match_strategy, '')) AS chembl_match_strategies,
        string_agg(DISTINCT NULLIF(c.chembl_matched_name, ''), ' | ' ORDER BY NULLIF(c.chembl_matched_name, '')) AS chembl_matched_names,
        string_agg(DISTINCT NULLIF(c.chembl_matched_name_type, ''), ' | ' ORDER BY NULLIF(c.chembl_matched_name_type, '')) AS chembl_matched_name_types,

        MAX(NULLIF(c.chembl_max_phase, '')::numeric) AS chembl_highest_max_phase,
        string_agg(DISTINCT NULLIF(c.chembl_max_phase, ''), ' | ' ORDER BY NULLIF(c.chembl_max_phase, '')) AS chembl_max_phases,
        string_agg(DISTINCT NULLIF(c.first_approval, ''), ' | ' ORDER BY NULLIF(c.first_approval, '')) AS chembl_first_approval_years,
        string_agg(DISTINCT NULLIF(c.molecule_type, ''), ' | ' ORDER BY NULLIF(c.molecule_type, '')) AS chembl_molecule_types,
        string_agg(DISTINCT NULLIF(c.structure_type, ''), ' | ' ORDER BY NULLIF(c.structure_type, '')) AS chembl_structure_types,
        string_agg(DISTINCT NULLIF(c.therapeutic_flag, ''), ' | ' ORDER BY NULLIF(c.therapeutic_flag, '')) AS chembl_therapeutic_flags,
        string_agg(DISTINCT NULLIF(c.dosed_ingredient, ''), ' | ' ORDER BY NULLIF(c.dosed_ingredient, '')) AS chembl_dosed_ingredient_values,
        string_agg(DISTINCT NULLIF(c.first_in_class, ''), ' | ' ORDER BY NULLIF(c.first_in_class, '')) AS chembl_first_in_class_values,
        string_agg(DISTINCT NULLIF(c.prodrug, ''), ' | ' ORDER BY NULLIF(c.prodrug, '')) AS chembl_prodrug_values,
        string_agg(DISTINCT NULLIF(c.chemical_probe, ''), ' | ' ORDER BY NULLIF(c.chemical_probe, '')) AS chembl_chemical_probe_values,
        string_agg(DISTINCT NULLIF(c.oral, ''), ' | ' ORDER BY NULLIF(c.oral, '')) AS chembl_oral_values,
        string_agg(DISTINCT NULLIF(c.parenteral, ''), ' | ' ORDER BY NULLIF(c.parenteral, '')) AS chembl_parenteral_values,
        string_agg(DISTINCT NULLIF(c.topical, ''), ' | ' ORDER BY NULLIF(c.topical, '')) AS chembl_topical_values,
        string_agg(DISTINCT NULLIF(c.black_box_warning, ''), ' | ' ORDER BY NULLIF(c.black_box_warning, '')) AS chembl_black_box_warning_values,
        string_agg(DISTINCT NULLIF(c.withdrawn_flag, ''), ' | ' ORDER BY NULLIF(c.withdrawn_flag, '')) AS chembl_withdrawn_flag_values,
        string_agg(DISTINCT NULLIF(c.orphan, ''), ' | ' ORDER BY NULLIF(c.orphan, '')) AS chembl_orphan_values,
        string_agg(DISTINCT NULLIF(c.full_mwt, ''), ' | ' ORDER BY NULLIF(c.full_mwt, '')) AS chembl_full_mwt_values,
        string_agg(DISTINCT NULLIF(c.full_molformula, ''), ' | ' ORDER BY NULLIF(c.full_molformula, '')) AS chembl_full_molformula_values,

        MAX(c.mechanism_count) AS chembl_max_mechanism_count,
        string_agg(DISTINCT NULLIF(c.mechanism_summary, ''), ' | ' ORDER BY NULLIF(c.mechanism_summary, '')) AS chembl_mechanism_summaries,
        string_agg(DISTINCT NULLIF(c.action_type_summary, ''), ' | ' ORDER BY NULLIF(c.action_type_summary, '')) AS chembl_action_type_summaries,
        string_agg(DISTINCT NULLIF(c.target_chembl_ids_summary, ''), ' | ' ORDER BY NULLIF(c.target_chembl_ids_summary, '')) AS chembl_target_chembl_ids_summary,
        string_agg(DISTINCT NULLIF(c.target_summary, ''), ' | ' ORDER BY NULLIF(c.target_summary, '')) AS chembl_target_summaries,
        string_agg(DISTINCT NULLIF(c.target_type_summary, ''), ' | ' ORDER BY NULLIF(c.target_type_summary, '')) AS chembl_target_type_summaries,
        string_agg(DISTINCT NULLIF(c.target_organism_summary, ''), ' | ' ORDER BY NULLIF(c.target_organism_summary, '')) AS chembl_target_organism_summaries,
        string_agg(DISTINCT NULLIF(c.target_accessions_summary, ''), ' | ' ORDER BY NULLIF(c.target_accessions_summary, '')) AS chembl_target_accessions_summaries,
        string_agg(DISTINCT NULLIF(c.direct_interaction_summary, ''), ' | ' ORDER BY NULLIF(c.direct_interaction_summary, '')) AS chembl_direct_interaction_summaries,
        string_agg(DISTINCT NULLIF(c.molecular_mechanism_summary, ''), ' | ' ORDER BY NULLIF(c.molecular_mechanism_summary, '')) AS chembl_molecular_mechanism_summaries,
        string_agg(DISTINCT NULLIF(c.disease_efficacy_summary, ''), ' | ' ORDER BY NULLIF(c.disease_efficacy_summary, '')) AS chembl_disease_efficacy_summaries,
        string_agg(DISTINCT NULLIF(c.mechanism_comment_summary, ''), ' | ' ORDER BY NULLIF(c.mechanism_comment_summary, '')) AS chembl_mechanism_comment_summaries,
        string_agg(DISTINCT NULLIF(c.selectivity_comment_summary, ''), ' | ' ORDER BY NULLIF(c.selectivity_comment_summary, '')) AS chembl_selectivity_comment_summaries,
        string_agg(DISTINCT NULLIF(c.binding_site_comment_summary, ''), ' | ' ORDER BY NULLIF(c.binding_site_comment_summary, '')) AS chembl_binding_site_comment_summaries,

        MAX(c.indication_count) AS chembl_max_indication_count,
        MAX(c.highest_indication_max_phase) AS chembl_highest_indication_max_phase,
        string_agg(DISTINCT NULLIF(c.indication_phase_terms_summary, ''), ' | ' ORDER BY NULLIF(c.indication_phase_terms_summary, '')) AS chembl_indication_phase_terms_summary,
        string_agg(DISTINCT NULLIF(c.highest_indication_terms_with_phase_summary, ''), ' | ' ORDER BY NULLIF(c.highest_indication_terms_with_phase_summary, '')) AS chembl_highest_indication_terms_with_phase_summary,
        MAX(c.oncology_indication_count) AS chembl_max_oncology_indication_count,
        MAX(c.highest_oncology_indication_max_phase) AS chembl_highest_oncology_indication_max_phase,
        string_agg(DISTINCT NULLIF(c.oncology_indication_phase_terms_summary, ''), ' | ' ORDER BY NULLIF(c.oncology_indication_phase_terms_summary, '')) AS chembl_oncology_indication_phase_terms_summary,
        string_agg(DISTINCT NULLIF(c.highest_oncology_indication_terms_with_phase_summary, ''), ' | ' ORDER BY NULLIF(c.highest_oncology_indication_terms_with_phase_summary, '')) AS chembl_highest_oncology_indication_terms_with_phase_summary
    FROM drug_classification.chembl_intervention_evidence_review_export AS c
    GROUP BY c.nct_id
),

final_raw AS (
    SELECT
        b.nct_id,
        b.drug_intervention_count,
        b.intervention_types,
        b.intervention_names,
        b.arm_group_labels,
        b.intervention_index_name_pairs,

        COALESCE(r.input_drug_name_count, 0) AS input_drug_name_count,
        COALESCE(r.input_drug_names, '') AS input_drug_names,

        COALESCE(r.rxnorm_match_statuses, '') AS rxnorm_match_statuses,
        COALESCE(r.rxnorm_matched_input_drug_name_count, 0) AS rxnorm_matched_input_drug_name_count,
        COALESCE(r.rxnorm_unmatched_or_review_input_drug_name_count, 0) AS rxnorm_unmatched_or_review_input_drug_name_count,
        COALESCE(r.rxnorm_unmatched_or_review_input_drug_names, '') AS rxnorm_unmatched_or_review_input_drug_names,
        COALESCE(r.rxnorm_matched_terms, '') AS rxnorm_matched_terms,
        COALESCE(r.rxnorm_rxcuis, '') AS rxnorm_rxcuis,
        COALESCE(r.rxnorm_canonical_names, '') AS rxnorm_canonical_names,
        COALESCE(r.rxnorm_term_types, '') AS rxnorm_term_types,
        COALESCE(r.rxnorm_ingredient_count, 0) AS rxnorm_ingredient_count,
        COALESCE(r.rxnorm_ingredient_rxcuis, '') AS rxnorm_ingredient_rxcuis,
        COALESCE(r.rxnorm_ingredient_names, '') AS rxnorm_ingredient_names,
        COALESCE(r.rxnorm_ingredient_term_types, '') AS rxnorm_ingredient_term_types,

        COALESCE(a.atc_code_count, 0) AS atc_code_count,
        COALESCE(a.atc_match_statuses, '') AS atc_match_statuses,
        COALESCE(a.atc_codes, '') AS atc_codes,
        COALESCE(a.atc_names, '') AS atc_names,
        COALESCE(a.atc_l1_codes, '') AS atc_l1_codes,
        COALESCE(a.atc_l1_names, '') AS atc_l1_names,
        COALESCE(a.atc_l2_codes, '') AS atc_l2_codes,
        COALESCE(a.atc_l2_names, '') AS atc_l2_names,
        COALESCE(a.atc_l3_codes, '') AS atc_l3_codes,
        COALESCE(a.atc_l3_names, '') AS atc_l3_names,
        COALESCE(a.atc_l4_codes, '') AS atc_l4_codes,
        COALESCE(a.atc_l4_names, '') AS atc_l4_names,
        COALESCE(a.atc_l5_codes, '') AS atc_l5_codes,
        COALESCE(a.atc_l5_names, '') AS atc_l5_names,

        COALESCE(f.fda_anchor_count, 0) AS fda_anchor_count,
        COALESCE(f.fda_product_component_count, 0) AS fda_product_component_count,
        COALESCE(f.fda_anchor_rxcuis, '') AS fda_anchor_rxcuis,
        COALESCE(f.fda_anchor_names, '') AS fda_anchor_names,
        COALESCE(f.fda_anchor_term_types, '') AS fda_anchor_term_types,
        COALESCE(f.fda_link_statuses, '') AS fda_link_statuses,
        COALESCE(f.fda_drug_names, '') AS fda_drug_names,
        COALESCE(f.fda_active_ingredient_components, '') AS fda_active_ingredient_components,
        COALESCE(f.fda_application_numbers, '') AS fda_application_numbers,
        COALESCE(f.fda_application_products, '') AS fda_application_products,
        COALESCE(f.fda_marketing_statuses, '') AS fda_marketing_statuses,
        COALESCE(f.fda_latest_approved_submission_status_dates, '') AS fda_latest_approved_submission_status_dates,
        COALESCE(f.fda_has_approved_submission_values, '') AS fda_has_approved_submission_values,
        COALESCE(f.fda_has_any_approved_submission, false) AS fda_has_any_approved_submission,
        COALESCE(f.fda_sponsor_names, '') AS fda_sponsor_names,
        COALESCE(f.fda_forms, '') AS fda_forms,
        COALESCE(f.fda_strength_components, '') AS fda_strength_components,

        COALESCE(p.pottr_review_row_count, 0) AS pottr_review_row_count,
        COALESCE(p.pottr_matched_class_row_count, 0) AS pottr_matched_class_row_count,
        COALESCE(p.pottr_match_statuses, '') AS pottr_match_statuses,
        COALESCE(p.matched_pottr_drug_names, '') AS matched_pottr_drug_names,
        COALESCE(p.pottr_direct_class_names, '') AS pottr_direct_class_names,
        COALESCE(p.pottr_class_names, '') AS pottr_class_names,
        COALESCE(p.pottr_class_relations, '') AS pottr_class_relations,
        COALESCE(p.pottr_class_paths, '') AS pottr_class_paths,
        COALESCE(p.pottr_class_in_hierarchy_values, '') AS pottr_class_in_hierarchy_values,

        COALESCE(c.chembl_review_row_count, 0) AS chembl_review_row_count,
        COALESCE(c.chembl_matched_molecule_row_count, 0) AS chembl_matched_molecule_row_count,
        COALESCE(c.matched_chembl_molecule_count, 0) AS matched_chembl_molecule_count,
        COALESCE(c.chembl_match_statuses, '') AS chembl_match_statuses,
        COALESCE(c.matched_chembl_ids, '') AS matched_chembl_ids,
        COALESCE(c.matched_chembl_pref_names, '') AS matched_chembl_pref_names,
        COALESCE(c.chembl_molecule_relation_types, '') AS chembl_molecule_relation_types,
        COALESCE(c.chembl_match_strategies, '') AS chembl_match_strategies,
        COALESCE(c.chembl_matched_names, '') AS chembl_matched_names,
        COALESCE(c.chembl_matched_name_types, '') AS chembl_matched_name_types,
        c.chembl_highest_max_phase,
        COALESCE(c.chembl_max_phases, '') AS chembl_max_phases,
        COALESCE(c.chembl_first_approval_years, '') AS chembl_first_approval_years,
        COALESCE(c.chembl_molecule_types, '') AS chembl_molecule_types,
        COALESCE(c.chembl_structure_types, '') AS chembl_structure_types,
        COALESCE(c.chembl_therapeutic_flags, '') AS chembl_therapeutic_flags,
        COALESCE(c.chembl_dosed_ingredient_values, '') AS chembl_dosed_ingredient_values,
        COALESCE(c.chembl_first_in_class_values, '') AS chembl_first_in_class_values,
        COALESCE(c.chembl_prodrug_values, '') AS chembl_prodrug_values,
        COALESCE(c.chembl_chemical_probe_values, '') AS chembl_chemical_probe_values,
        COALESCE(c.chembl_oral_values, '') AS chembl_oral_values,
        COALESCE(c.chembl_parenteral_values, '') AS chembl_parenteral_values,
        COALESCE(c.chembl_topical_values, '') AS chembl_topical_values,
        COALESCE(c.chembl_black_box_warning_values, '') AS chembl_black_box_warning_values,
        COALESCE(c.chembl_withdrawn_flag_values, '') AS chembl_withdrawn_flag_values,
        COALESCE(c.chembl_orphan_values, '') AS chembl_orphan_values,
        COALESCE(c.chembl_full_mwt_values, '') AS chembl_full_mwt_values,
        COALESCE(c.chembl_full_molformula_values, '') AS chembl_full_molformula_values,

        COALESCE(c.chembl_max_mechanism_count, 0) AS chembl_max_mechanism_count,
        COALESCE(c.chembl_mechanism_summaries, '') AS chembl_mechanism_summaries,
        COALESCE(c.chembl_action_type_summaries, '') AS chembl_action_type_summaries,
        COALESCE(c.chembl_target_chembl_ids_summary, '') AS chembl_target_chembl_ids_summary,
        COALESCE(c.chembl_target_summaries, '') AS chembl_target_summaries,
        COALESCE(c.chembl_target_type_summaries, '') AS chembl_target_type_summaries,
        COALESCE(c.chembl_target_organism_summaries, '') AS chembl_target_organism_summaries,
        COALESCE(c.chembl_target_accessions_summaries, '') AS chembl_target_accessions_summaries,
        COALESCE(c.chembl_direct_interaction_summaries, '') AS chembl_direct_interaction_summaries,
        COALESCE(c.chembl_molecular_mechanism_summaries, '') AS chembl_molecular_mechanism_summaries,
        COALESCE(c.chembl_disease_efficacy_summaries, '') AS chembl_disease_efficacy_summaries,
        COALESCE(c.chembl_mechanism_comment_summaries, '') AS chembl_mechanism_comment_summaries,
        COALESCE(c.chembl_selectivity_comment_summaries, '') AS chembl_selectivity_comment_summaries,
        COALESCE(c.chembl_binding_site_comment_summaries, '') AS chembl_binding_site_comment_summaries,

        COALESCE(c.chembl_max_indication_count, 0) AS chembl_max_indication_count,
        c.chembl_highest_indication_max_phase,
        COALESCE(c.chembl_indication_phase_terms_summary, '') AS chembl_indication_phase_terms_summary,
        COALESCE(c.chembl_highest_indication_terms_with_phase_summary, '') AS chembl_highest_indication_terms_with_phase_summary,
        COALESCE(c.chembl_max_oncology_indication_count, 0) AS chembl_max_oncology_indication_count,
        c.chembl_highest_oncology_indication_max_phase,
        COALESCE(c.chembl_oncology_indication_phase_terms_summary, '') AS chembl_oncology_indication_phase_terms_summary,
        COALESCE(c.chembl_highest_oncology_indication_terms_with_phase_summary, '') AS chembl_highest_oncology_indication_terms_with_phase_summary

    FROM trial_base AS b
    LEFT JOIN rxnorm_by_trial AS r
        ON r.nct_id = b.nct_id
    LEFT JOIN atc_by_trial AS a
        ON a.nct_id = b.nct_id
    LEFT JOIN fda_by_trial AS f
        ON f.nct_id = b.nct_id
    LEFT JOIN pottr_by_trial AS p
        ON p.nct_id = b.nct_id
    LEFT JOIN chembl_by_trial AS c
        ON c.nct_id = b.nct_id
)

SELECT
    regexp_replace(COALESCE(final_raw.nct_id, ''), E'[\r\n]+', ' ', 'g') AS nct_id,
    final_raw.drug_intervention_count,
    regexp_replace(COALESCE(final_raw.intervention_types, ''), E'[\r\n]+', ' ', 'g') AS intervention_types,
    regexp_replace(COALESCE(final_raw.intervention_names, ''), E'[\r\n]+', ' ', 'g') AS intervention_names,
    regexp_replace(COALESCE(final_raw.arm_group_labels, ''), E'[\r\n]+', ' ', 'g') AS arm_group_labels,
    regexp_replace(COALESCE(final_raw.intervention_index_name_pairs, ''), E'[\r\n]+', ' ', 'g') AS intervention_index_name_pairs,

    final_raw.input_drug_name_count,
    regexp_replace(COALESCE(final_raw.input_drug_names, ''), E'[\r\n]+', ' ', 'g') AS input_drug_names,

    regexp_replace(COALESCE(final_raw.rxnorm_match_statuses, ''), E'[\r\n]+', ' ', 'g') AS rxnorm_match_statuses,
    final_raw.rxnorm_matched_input_drug_name_count,
    final_raw.rxnorm_unmatched_or_review_input_drug_name_count,
    regexp_replace(COALESCE(final_raw.rxnorm_unmatched_or_review_input_drug_names, ''), E'[\r\n]+', ' ', 'g') AS rxnorm_unmatched_or_review_input_drug_names,
    regexp_replace(COALESCE(final_raw.rxnorm_matched_terms, ''), E'[\r\n]+', ' ', 'g') AS rxnorm_matched_terms,
    regexp_replace(COALESCE(final_raw.rxnorm_rxcuis, ''), E'[\r\n]+', ' ', 'g') AS rxnorm_rxcuis,
    regexp_replace(COALESCE(final_raw.rxnorm_canonical_names, ''), E'[\r\n]+', ' ', 'g') AS rxnorm_canonical_names,
    regexp_replace(COALESCE(final_raw.rxnorm_term_types, ''), E'[\r\n]+', ' ', 'g') AS rxnorm_term_types,
    final_raw.rxnorm_ingredient_count,
    regexp_replace(COALESCE(final_raw.rxnorm_ingredient_rxcuis, ''), E'[\r\n]+', ' ', 'g') AS rxnorm_ingredient_rxcuis,
    regexp_replace(COALESCE(final_raw.rxnorm_ingredient_names, ''), E'[\r\n]+', ' ', 'g') AS rxnorm_ingredient_names,
    regexp_replace(COALESCE(final_raw.rxnorm_ingredient_term_types, ''), E'[\r\n]+', ' ', 'g') AS rxnorm_ingredient_term_types,

    final_raw.atc_code_count,
    regexp_replace(COALESCE(final_raw.atc_match_statuses, ''), E'[\r\n]+', ' ', 'g') AS atc_match_statuses,
    regexp_replace(COALESCE(final_raw.atc_codes, ''), E'[\r\n]+', ' ', 'g') AS atc_codes,
    regexp_replace(COALESCE(final_raw.atc_names, ''), E'[\r\n]+', ' ', 'g') AS atc_names,
    regexp_replace(COALESCE(final_raw.atc_l1_codes, ''), E'[\r\n]+', ' ', 'g') AS atc_l1_codes,
    regexp_replace(COALESCE(final_raw.atc_l1_names, ''), E'[\r\n]+', ' ', 'g') AS atc_l1_names,
    regexp_replace(COALESCE(final_raw.atc_l2_codes, ''), E'[\r\n]+', ' ', 'g') AS atc_l2_codes,
    regexp_replace(COALESCE(final_raw.atc_l2_names, ''), E'[\r\n]+', ' ', 'g') AS atc_l2_names,
    regexp_replace(COALESCE(final_raw.atc_l3_codes, ''), E'[\r\n]+', ' ', 'g') AS atc_l3_codes,
    regexp_replace(COALESCE(final_raw.atc_l3_names, ''), E'[\r\n]+', ' ', 'g') AS atc_l3_names,
    regexp_replace(COALESCE(final_raw.atc_l4_codes, ''), E'[\r\n]+', ' ', 'g') AS atc_l4_codes,
    regexp_replace(COALESCE(final_raw.atc_l4_names, ''), E'[\r\n]+', ' ', 'g') AS atc_l4_names,
    regexp_replace(COALESCE(final_raw.atc_l5_codes, ''), E'[\r\n]+', ' ', 'g') AS atc_l5_codes,
    regexp_replace(COALESCE(final_raw.atc_l5_names, ''), E'[\r\n]+', ' ', 'g') AS atc_l5_names,

    final_raw.fda_anchor_count,
    final_raw.fda_product_component_count,
    regexp_replace(COALESCE(final_raw.fda_anchor_rxcuis, ''), E'[\r\n]+', ' ', 'g') AS fda_anchor_rxcuis,
    regexp_replace(COALESCE(final_raw.fda_anchor_names, ''), E'[\r\n]+', ' ', 'g') AS fda_anchor_names,
    regexp_replace(COALESCE(final_raw.fda_anchor_term_types, ''), E'[\r\n]+', ' ', 'g') AS fda_anchor_term_types,
    regexp_replace(COALESCE(final_raw.fda_link_statuses, ''), E'[\r\n]+', ' ', 'g') AS fda_link_statuses,
    regexp_replace(COALESCE(final_raw.fda_drug_names, ''), E'[\r\n]+', ' ', 'g') AS fda_drug_names,
    regexp_replace(COALESCE(final_raw.fda_active_ingredient_components, ''), E'[\r\n]+', ' ', 'g') AS fda_active_ingredient_components,
    regexp_replace(COALESCE(final_raw.fda_application_numbers, ''), E'[\r\n]+', ' ', 'g') AS fda_application_numbers,
    regexp_replace(COALESCE(final_raw.fda_application_products, ''), E'[\r\n]+', ' ', 'g') AS fda_application_products,
    regexp_replace(COALESCE(final_raw.fda_marketing_statuses, ''), E'[\r\n]+', ' ', 'g') AS fda_marketing_statuses,
    regexp_replace(COALESCE(final_raw.fda_latest_approved_submission_status_dates, ''), E'[\r\n]+', ' ', 'g') AS fda_latest_approved_submission_status_dates,
    regexp_replace(COALESCE(final_raw.fda_has_approved_submission_values, ''), E'[\r\n]+', ' ', 'g') AS fda_has_approved_submission_values,
    final_raw.fda_has_any_approved_submission,
    regexp_replace(COALESCE(final_raw.fda_sponsor_names, ''), E'[\r\n]+', ' ', 'g') AS fda_sponsor_names,
    regexp_replace(COALESCE(final_raw.fda_forms, ''), E'[\r\n]+', ' ', 'g') AS fda_forms,
    regexp_replace(COALESCE(final_raw.fda_strength_components, ''), E'[\r\n]+', ' ', 'g') AS fda_strength_components,

    final_raw.pottr_review_row_count,
    final_raw.pottr_matched_class_row_count,
    regexp_replace(COALESCE(final_raw.pottr_match_statuses, ''), E'[\r\n]+', ' ', 'g') AS pottr_match_statuses,
    regexp_replace(COALESCE(final_raw.matched_pottr_drug_names, ''), E'[\r\n]+', ' ', 'g') AS matched_pottr_drug_names,
    regexp_replace(COALESCE(final_raw.pottr_direct_class_names, ''), E'[\r\n]+', ' ', 'g') AS pottr_direct_class_names,
    regexp_replace(COALESCE(final_raw.pottr_class_names, ''), E'[\r\n]+', ' ', 'g') AS pottr_class_names,
    regexp_replace(COALESCE(final_raw.pottr_class_relations, ''), E'[\r\n]+', ' ', 'g') AS pottr_class_relations,
    regexp_replace(COALESCE(final_raw.pottr_class_paths, ''), E'[\r\n]+', ' ', 'g') AS pottr_class_paths,
    regexp_replace(COALESCE(final_raw.pottr_class_in_hierarchy_values, ''), E'[\r\n]+', ' ', 'g') AS pottr_class_in_hierarchy_values,

    final_raw.chembl_review_row_count,
    final_raw.chembl_matched_molecule_row_count,
    final_raw.matched_chembl_molecule_count,
    regexp_replace(COALESCE(final_raw.chembl_match_statuses, ''), E'[\r\n]+', ' ', 'g') AS chembl_match_statuses,
    regexp_replace(COALESCE(final_raw.matched_chembl_ids, ''), E'[\r\n]+', ' ', 'g') AS matched_chembl_ids,
    regexp_replace(COALESCE(final_raw.matched_chembl_pref_names, ''), E'[\r\n]+', ' ', 'g') AS matched_chembl_pref_names,
    regexp_replace(COALESCE(final_raw.chembl_molecule_relation_types, ''), E'[\r\n]+', ' ', 'g') AS chembl_molecule_relation_types,
    regexp_replace(COALESCE(final_raw.chembl_match_strategies, ''), E'[\r\n]+', ' ', 'g') AS chembl_match_strategies,
    regexp_replace(COALESCE(final_raw.chembl_matched_names, ''), E'[\r\n]+', ' ', 'g') AS chembl_matched_names,
    regexp_replace(COALESCE(final_raw.chembl_matched_name_types, ''), E'[\r\n]+', ' ', 'g') AS chembl_matched_name_types,
    final_raw.chembl_highest_max_phase,
    regexp_replace(COALESCE(final_raw.chembl_max_phases, ''), E'[\r\n]+', ' ', 'g') AS chembl_max_phases,
    regexp_replace(COALESCE(final_raw.chembl_first_approval_years, ''), E'[\r\n]+', ' ', 'g') AS chembl_first_approval_years,
    regexp_replace(COALESCE(final_raw.chembl_molecule_types, ''), E'[\r\n]+', ' ', 'g') AS chembl_molecule_types,
    regexp_replace(COALESCE(final_raw.chembl_structure_types, ''), E'[\r\n]+', ' ', 'g') AS chembl_structure_types,
    regexp_replace(COALESCE(final_raw.chembl_therapeutic_flags, ''), E'[\r\n]+', ' ', 'g') AS chembl_therapeutic_flags,
    regexp_replace(COALESCE(final_raw.chembl_dosed_ingredient_values, ''), E'[\r\n]+', ' ', 'g') AS chembl_dosed_ingredient_values,
    regexp_replace(COALESCE(final_raw.chembl_first_in_class_values, ''), E'[\r\n]+', ' ', 'g') AS chembl_first_in_class_values,
    regexp_replace(COALESCE(final_raw.chembl_prodrug_values, ''), E'[\r\n]+', ' ', 'g') AS chembl_prodrug_values,
    regexp_replace(COALESCE(final_raw.chembl_chemical_probe_values, ''), E'[\r\n]+', ' ', 'g') AS chembl_chemical_probe_values,
    regexp_replace(COALESCE(final_raw.chembl_oral_values, ''), E'[\r\n]+', ' ', 'g') AS chembl_oral_values,
    regexp_replace(COALESCE(final_raw.chembl_parenteral_values, ''), E'[\r\n]+', ' ', 'g') AS chembl_parenteral_values,
    regexp_replace(COALESCE(final_raw.chembl_topical_values, ''), E'[\r\n]+', ' ', 'g') AS chembl_topical_values,
    regexp_replace(COALESCE(final_raw.chembl_black_box_warning_values, ''), E'[\r\n]+', ' ', 'g') AS chembl_black_box_warning_values,
    regexp_replace(COALESCE(final_raw.chembl_withdrawn_flag_values, ''), E'[\r\n]+', ' ', 'g') AS chembl_withdrawn_flag_values,
    regexp_replace(COALESCE(final_raw.chembl_orphan_values, ''), E'[\r\n]+', ' ', 'g') AS chembl_orphan_values,
    regexp_replace(COALESCE(final_raw.chembl_full_mwt_values, ''), E'[\r\n]+', ' ', 'g') AS chembl_full_mwt_values,
    regexp_replace(COALESCE(final_raw.chembl_full_molformula_values, ''), E'[\r\n]+', ' ', 'g') AS chembl_full_molformula_values,

    final_raw.chembl_max_mechanism_count,
    regexp_replace(COALESCE(final_raw.chembl_mechanism_summaries, ''), E'[\r\n]+', ' ', 'g') AS chembl_mechanism_summaries,
    regexp_replace(COALESCE(final_raw.chembl_action_type_summaries, ''), E'[\r\n]+', ' ', 'g') AS chembl_action_type_summaries,
    regexp_replace(COALESCE(final_raw.chembl_target_chembl_ids_summary, ''), E'[\r\n]+', ' ', 'g') AS chembl_target_chembl_ids_summary,
    regexp_replace(COALESCE(final_raw.chembl_target_summaries, ''), E'[\r\n]+', ' ', 'g') AS chembl_target_summaries,
    regexp_replace(COALESCE(final_raw.chembl_target_type_summaries, ''), E'[\r\n]+', ' ', 'g') AS chembl_target_type_summaries,
    regexp_replace(COALESCE(final_raw.chembl_target_organism_summaries, ''), E'[\r\n]+', ' ', 'g') AS chembl_target_organism_summaries,
    regexp_replace(COALESCE(final_raw.chembl_target_accessions_summaries, ''), E'[\r\n]+', ' ', 'g') AS chembl_target_accessions_summaries,
    regexp_replace(COALESCE(final_raw.chembl_direct_interaction_summaries, ''), E'[\r\n]+', ' ', 'g') AS chembl_direct_interaction_summaries,
    regexp_replace(COALESCE(final_raw.chembl_molecular_mechanism_summaries, ''), E'[\r\n]+', ' ', 'g') AS chembl_molecular_mechanism_summaries,
    regexp_replace(COALESCE(final_raw.chembl_disease_efficacy_summaries, ''), E'[\r\n]+', ' ', 'g') AS chembl_disease_efficacy_summaries,
    regexp_replace(COALESCE(final_raw.chembl_mechanism_comment_summaries, ''), E'[\r\n]+', ' ', 'g') AS chembl_mechanism_comment_summaries,
    regexp_replace(COALESCE(final_raw.chembl_selectivity_comment_summaries, ''), E'[\r\n]+', ' ', 'g') AS chembl_selectivity_comment_summaries,
    regexp_replace(COALESCE(final_raw.chembl_binding_site_comment_summaries, ''), E'[\r\n]+', ' ', 'g') AS chembl_binding_site_comment_summaries,

    final_raw.chembl_max_indication_count,
    final_raw.chembl_highest_indication_max_phase,
    regexp_replace(COALESCE(final_raw.chembl_indication_phase_terms_summary, ''), E'[\r\n]+', ' ', 'g') AS chembl_indication_phase_terms_summary,
    regexp_replace(COALESCE(final_raw.chembl_highest_indication_terms_with_phase_summary, ''), E'[\r\n]+', ' ', 'g') AS chembl_highest_indication_terms_with_phase_summary,
    final_raw.chembl_max_oncology_indication_count,
    final_raw.chembl_highest_oncology_indication_max_phase,
    regexp_replace(COALESCE(final_raw.chembl_oncology_indication_phase_terms_summary, ''), E'[\r\n]+', ' ', 'g') AS chembl_oncology_indication_phase_terms_summary,
    regexp_replace(COALESCE(final_raw.chembl_highest_oncology_indication_terms_with_phase_summary, ''), E'[\r\n]+', ' ', 'g') AS chembl_highest_oncology_indication_terms_with_phase_summary

FROM final_raw;
