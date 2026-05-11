-- ChEMBL molecule-level evidence schema.
-- Materialized layer is ChEMBL-anchor-level, not CTGov alias/source-field-level.

CREATE SCHEMA IF NOT EXISTS drug_classification;

DROP VIEW IF EXISTS drug_classification.chembl_intervention_evidence_review_export;
DROP VIEW IF EXISTS drug_classification.ctgov_intervention_chembl_link_export;

DROP TABLE IF EXISTS drug_classification.chembl_anchor_molecule_mapping;
DROP TABLE IF EXISTS drug_classification.input_drug_name_chembl_anchor;


DROP TABLE IF EXISTS drug_classification.chembl_molecule_warning CASCADE;
DROP TABLE IF EXISTS drug_classification.chembl_molecule_atc CASCADE;
DROP TABLE IF EXISTS drug_classification.chembl_molecule_indication CASCADE;
DROP TABLE IF EXISTS drug_classification.chembl_molecule_mechanism CASCADE;
DROP TABLE IF EXISTS drug_classification.chembl_molecule_relation CASCADE;
DROP TABLE IF EXISTS drug_classification.chembl_molecule_alias CASCADE;
DROP TABLE IF EXISTS drug_classification.chembl_molecule CASCADE;

CREATE TABLE drug_classification.chembl_molecule (
    chembl_molecule_id BIGSERIAL PRIMARY KEY,
    load_batch_id UUID NOT NULL,
    molregno BIGINT NOT NULL,
    chembl_id TEXT NOT NULL,
    pref_name TEXT,
    max_phase TEXT,
    therapeutic_flag TEXT,
    dosed_ingredient TEXT,
    structure_type TEXT,
    molecule_type TEXT,
    first_approval TEXT,
    oral TEXT,
    parenteral TEXT,
    topical TEXT,
    black_box_warning TEXT,
    first_in_class TEXT,
    prodrug TEXT,
    withdrawn_flag TEXT,
    chemical_probe TEXT,
    orphan TEXT,
    standard_inchi_key TEXT,
    canonical_smiles TEXT,
    full_mwt TEXT,
    full_molformula TEXT,
    chembl_source_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (chembl_source_version, molregno),
    UNIQUE (chembl_source_version, chembl_id)
);

CREATE TABLE drug_classification.chembl_molecule_alias (
    chembl_molecule_alias_id BIGSERIAL PRIMARY KEY,
    chembl_molecule_id BIGINT NOT NULL REFERENCES drug_classification.chembl_molecule(chembl_molecule_id) ON DELETE CASCADE,
    load_batch_id UUID NOT NULL,
    molregno BIGINT NOT NULL,
    synonym TEXT NOT NULL,
    synonym_normalized TEXT NOT NULL,
    syn_type TEXT,
    chembl_source_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (chembl_source_version, molregno, synonym, syn_type)
);

CREATE TABLE drug_classification.chembl_molecule_relation (
    chembl_molecule_relation_id BIGSERIAL PRIMARY KEY,
    chembl_molecule_id BIGINT NOT NULL REFERENCES drug_classification.chembl_molecule(chembl_molecule_id) ON DELETE CASCADE,
    related_chembl_molecule_id BIGINT NOT NULL REFERENCES drug_classification.chembl_molecule(chembl_molecule_id) ON DELETE CASCADE,
    load_batch_id UUID NOT NULL,
    molregno BIGINT NOT NULL,
    related_molregno BIGINT NOT NULL,
    relation_type TEXT NOT NULL,
    chembl_source_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (chembl_source_version, molregno, related_molregno, relation_type)
);

CREATE TABLE drug_classification.chembl_molecule_mechanism (
    chembl_molecule_mechanism_id BIGSERIAL PRIMARY KEY,
    chembl_molecule_id BIGINT NOT NULL REFERENCES drug_classification.chembl_molecule(chembl_molecule_id) ON DELETE CASCADE,
    load_batch_id UUID NOT NULL,
    molregno BIGINT NOT NULL,
    mec_id TEXT,
    record_id TEXT,
    mechanism_of_action TEXT,
    action_type TEXT,
    direct_interaction TEXT,
    molecular_mechanism TEXT,
    disease_efficacy TEXT,
    mechanism_comment TEXT,
    selectivity_comment TEXT,
    binding_site_comment TEXT,
    target_chembl_id TEXT,
    target_pref_name TEXT,
    target_type TEXT,
    target_organism TEXT,
    target_accessions TEXT,
    target_component_descriptions TEXT,
    chembl_source_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (chembl_source_version, molregno, mec_id)
);

CREATE TABLE drug_classification.chembl_molecule_indication (
    chembl_molecule_indication_id BIGSERIAL PRIMARY KEY,
    chembl_molecule_id BIGINT NOT NULL REFERENCES drug_classification.chembl_molecule(chembl_molecule_id) ON DELETE CASCADE,
    load_batch_id UUID NOT NULL,
    molregno BIGINT NOT NULL,
    drugind_id TEXT,
    record_id TEXT,
    max_phase_for_ind TEXT,
    mesh_id TEXT,
    mesh_heading TEXT,
    efo_id TEXT,
    efo_term TEXT,
    is_oncology BOOLEAN NOT NULL DEFAULT false,
    chembl_source_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (chembl_source_version, molregno, drugind_id)
);

CREATE TABLE drug_classification.chembl_molecule_atc (
    chembl_molecule_atc_id BIGSERIAL PRIMARY KEY,
    chembl_molecule_id BIGINT NOT NULL REFERENCES drug_classification.chembl_molecule(chembl_molecule_id) ON DELETE CASCADE,
    load_batch_id UUID NOT NULL,
    molregno BIGINT NOT NULL,
    mol_atc_id TEXT,
    level5 TEXT,
    who_name TEXT,
    level1 TEXT,
    level2 TEXT,
    level3 TEXT,
    level4 TEXT,
    level1_description TEXT,
    level2_description TEXT,
    level3_description TEXT,
    level4_description TEXT,
    chembl_source_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (chembl_source_version, molregno, mol_atc_id)
);

CREATE TABLE drug_classification.chembl_molecule_warning (
    chembl_molecule_warning_id BIGSERIAL PRIMARY KEY,
    chembl_molecule_id BIGINT NOT NULL REFERENCES drug_classification.chembl_molecule(chembl_molecule_id) ON DELETE CASCADE,
    load_batch_id UUID NOT NULL,
    molregno BIGINT NOT NULL,
    warning_id TEXT,
    record_id TEXT,
    warning_type TEXT,
    warning_class TEXT,
    warning_description TEXT,
    warning_country TEXT,
    warning_year TEXT,
    efo_term TEXT,
    efo_id TEXT,
    efo_id_for_warning_class TEXT,
    chembl_source_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (chembl_source_version, molregno, warning_id)
);


-- =============================================================================
-- Input drug term -> ChEMBL anchor
-- =============================================================================
--
-- Grain:
--   one row per input_drug_name × chembl_anchor_key.

CREATE TABLE drug_classification.input_drug_name_chembl_anchor (
    input_drug_name TEXT NOT NULL
        REFERENCES drug_identity.ctgov_drug_term(input_drug_name)
        ON DELETE CASCADE,

    chembl_anchor_key TEXT NOT NULL,

    PRIMARY KEY (
        input_drug_name,
        chembl_anchor_key
    )
);

CREATE INDEX idx_input_drug_name_chembl_anchor_key
    ON drug_classification.input_drug_name_chembl_anchor(chembl_anchor_key);


-- =============================================================================
-- ChEMBL anchor -> ChEMBL molecule mapping
-- =============================================================================
--
-- Grain:
--   one row per chembl_anchor_key × ChEMBL molecule × molecule relation type.
--
-- If no ChEMBL molecule is found:
--   chembl_link_status = 'NO_CHEMBL_MOLECULE_FOR_ANCHOR'
--   chembl_molecule_key = ''
--   molecule_relation_type = ''

CREATE TABLE drug_classification.chembl_anchor_molecule_mapping (
    chembl_anchor_key TEXT NOT NULL,
    chembl_anchor_type TEXT,
    chembl_anchor_rxcui TEXT,
    chembl_anchor_name TEXT,

    chembl_link_status TEXT NOT NULL,

    chembl_molecule_key TEXT NOT NULL DEFAULT '',
    chembl_molecule_id BIGINT
        REFERENCES drug_classification.chembl_molecule(chembl_molecule_id)
        ON DELETE CASCADE,

    molecule_relation_type TEXT NOT NULL DEFAULT '',
    match_strategy TEXT,
    matched_name TEXT,
    matched_name_type TEXT,

    PRIMARY KEY (
        chembl_anchor_key,
        chembl_molecule_key,
        molecule_relation_type
    )
);

CREATE INDEX idx_chembl_anchor_molecule_mapping_anchor
    ON drug_classification.chembl_anchor_molecule_mapping(chembl_anchor_key);

CREATE INDEX idx_chembl_anchor_molecule_mapping_status
    ON drug_classification.chembl_anchor_molecule_mapping(chembl_link_status);

CREATE INDEX idx_chembl_anchor_molecule_mapping_molecule
    ON drug_classification.chembl_anchor_molecule_mapping(chembl_molecule_id);


-- =============================================================================
-- Essential export 1: CTGov intervention/input drug term -> ChEMBL anchor
-- =============================================================================
--
-- Grain:
--   one row per ctgov_intervention_key × input_drug_name × chembl_anchor_key.
--
-- This view deliberately does not duplicate ChEMBL molecule/evidence fields.

CREATE OR REPLACE VIEW drug_classification.ctgov_intervention_chembl_link_export AS
SELECT
    l.ctgov_intervention_key,
    l.nct_id,
    l.intervention_index,
    l.input_drug_name,
    a.chembl_anchor_key
FROM drug_identity.ctgov_intervention_drug_term_link AS l
LEFT JOIN drug_classification.input_drug_name_chembl_anchor AS a
    ON a.input_drug_name = l.input_drug_name;


-- =============================================================================
-- Essential export 2: human-facing ChEMBL evidence review
-- =============================================================================
--
-- Grain:
--   one CTGov intervention × input_drug_name × matched ChEMBL molecule/relation row.
--
-- This is the final review/reporting TSV. It hides internal anchor and molecule
-- row IDs but keeps the ChEMBL identifiers and evidence summaries.

CREATE OR REPLACE VIEW drug_classification.chembl_intervention_evidence_review_export AS
WITH staging AS (
    SELECT DISTINCT ON (nct_id, intervention_index)
        nct_id,
        intervention_index,
        intervention_type,
        intervention_name
    FROM ctgov.intervention_staging
    ORDER BY nct_id, intervention_index, staging_row_id DESC
),
mechanism_summary AS (
    SELECT
        chembl_molecule_id,
        COUNT(*) AS mechanism_count,
        string_agg(DISTINCT NULLIF(mechanism_of_action, ''), ' | ') AS mechanism_summary,
        string_agg(DISTINCT NULLIF(action_type, ''), ' | ') AS action_type_summary,
        string_agg(DISTINCT NULLIF(target_chembl_id, ''), ' | ') AS target_chembl_ids_summary,
        string_agg(DISTINCT NULLIF(target_pref_name, ''), ' | ') AS target_summary,
        string_agg(DISTINCT NULLIF(target_type, ''), ' | ') AS target_type_summary,
        string_agg(DISTINCT NULLIF(target_organism, ''), ' | ') AS target_organism_summary,
        string_agg(DISTINCT NULLIF(target_accessions, ''), ' | ') AS target_accessions_summary,
        string_agg(DISTINCT NULLIF(direct_interaction, ''), ' | ') AS direct_interaction_summary,
        string_agg(DISTINCT NULLIF(molecular_mechanism, ''), ' | ') AS molecular_mechanism_summary,
        string_agg(DISTINCT NULLIF(disease_efficacy, ''), ' | ') AS disease_efficacy_summary,
        string_agg(DISTINCT NULLIF(mechanism_comment, ''), ' | ') AS mechanism_comment_summary,
        string_agg(DISTINCT NULLIF(selectivity_comment, ''), ' | ') AS selectivity_comment_summary,
        string_agg(DISTINCT NULLIF(binding_site_comment, ''), ' | ') AS binding_site_comment_summary
    FROM drug_classification.chembl_molecule_mechanism
    GROUP BY chembl_molecule_id
),
indication_base AS (
    SELECT
        chembl_molecule_id,
        NULLIF(max_phase_for_ind, '')::numeric AS phase_value,
        COALESCE(NULLIF(efo_term, ''), NULLIF(mesh_heading, '')) AS indication_term,
        NULLIF(efo_id, '') AS efo_id,
        NULLIF(mesh_id, '') AS mesh_id,
        is_oncology
    FROM drug_classification.chembl_molecule_indication
),
indication_summary AS (
    SELECT
        chembl_molecule_id,
        COUNT(*) AS indication_count,
        MAX(phase_value) AS highest_indication_max_phase,
        COUNT(*) FILTER (WHERE is_oncology) AS oncology_indication_count,
        MAX(phase_value) FILTER (WHERE is_oncology) AS highest_oncology_indication_max_phase,
        string_agg(
            DISTINCT
            CASE
                WHEN indication_term IS NULL THEN NULL
                ELSE CONCAT(
                    indication_term,
                    ' [phase ',
                    COALESCE(phase_value::text, 'unknown'),
                    CASE
                        WHEN efo_id IS NOT NULL THEN '; ' || efo_id
                        WHEN mesh_id IS NOT NULL THEN '; ' || mesh_id
                        ELSE ''
                    END,
                    ']'
                )
            END,
            ' | '
        ) AS indication_phase_terms_summary,
        string_agg(
            DISTINCT
            CASE
                WHEN is_oncology AND indication_term IS NOT NULL THEN CONCAT(
                    indication_term,
                    ' [phase ',
                    COALESCE(phase_value::text, 'unknown'),
                    CASE
                        WHEN efo_id IS NOT NULL THEN '; ' || efo_id
                        WHEN mesh_id IS NOT NULL THEN '; ' || mesh_id
                        ELSE ''
                    END,
                    ']'
                )
                ELSE NULL
            END,
            ' | '
        ) AS oncology_indication_phase_terms_summary,
        string_agg(
            DISTINCT
            CASE
                WHEN indication_term IS NOT NULL
                 AND phase_value = (
                    SELECT MAX(ib2.phase_value)
                    FROM indication_base AS ib2
                    WHERE ib2.chembl_molecule_id = indication_base.chembl_molecule_id
                 )
                THEN CONCAT(
                    indication_term,
                    ' [phase ',
                    COALESCE(phase_value::text, 'unknown'),
                    CASE
                        WHEN efo_id IS NOT NULL THEN '; ' || efo_id
                        WHEN mesh_id IS NOT NULL THEN '; ' || mesh_id
                        ELSE ''
                    END,
                    ']'
                )
                ELSE NULL
            END,
            ' | '
        ) AS highest_indication_terms_with_phase_summary,
        string_agg(
            DISTINCT
            CASE
                WHEN is_oncology
                 AND indication_term IS NOT NULL
                 AND phase_value = (
                    SELECT MAX(ib2.phase_value)
                    FROM indication_base AS ib2
                    WHERE ib2.chembl_molecule_id = indication_base.chembl_molecule_id
                      AND ib2.is_oncology
                 )
                THEN CONCAT(
                    indication_term,
                    ' [phase ',
                    COALESCE(phase_value::text, 'unknown'),
                    CASE
                        WHEN efo_id IS NOT NULL THEN '; ' || efo_id
                        WHEN mesh_id IS NOT NULL THEN '; ' || mesh_id
                        ELSE ''
                    END,
                    ']'
                )
                ELSE NULL
            END,
            ' | '
        ) AS highest_oncology_indication_terms_with_phase_summary
    FROM indication_base
    GROUP BY chembl_molecule_id
)
SELECT DISTINCT
    l.nct_id,
    l.intervention_index,
    s.intervention_type,
    s.intervention_name,
    l.input_drug_name,

    COALESCE(m.chembl_link_status, 'NO_CHEMBL_ANCHOR') AS chembl_match_status,
    COALESCE(cm.chembl_id, '') AS matched_chembl_id,
    COALESCE(cm.pref_name, '') AS matched_chembl_pref_name,
    COALESCE(m.molecule_relation_type, '') AS molecule_relation_type,
    COALESCE(m.match_strategy, '') AS chembl_match_strategy,
    COALESCE(m.matched_name, '') AS chembl_matched_name,
    COALESCE(m.matched_name_type, '') AS chembl_matched_name_type,

    NULLIF(cm.max_phase, '-1') AS chembl_max_phase,
    cm.first_approval,
    cm.molecule_type,
    cm.structure_type,
    cm.therapeutic_flag,
    cm.dosed_ingredient,
    cm.first_in_class,
    cm.prodrug,
    cm.chemical_probe,
    cm.full_mwt,
    cm.full_molformula,
    cm.oral,
    cm.parenteral,
    cm.topical,
    cm.black_box_warning,
    cm.withdrawn_flag,
    NULLIF(cm.orphan, '-1') AS orphan,

    COALESCE(ms.mechanism_count, 0) AS mechanism_count,
    COALESCE(ms.mechanism_summary, '') AS mechanism_summary,
    COALESCE(ms.action_type_summary, '') AS action_type_summary,
    COALESCE(ms.target_chembl_ids_summary, '') AS target_chembl_ids_summary,
    COALESCE(ms.target_summary, '') AS target_summary,
    COALESCE(ms.target_type_summary, '') AS target_type_summary,
    COALESCE(ms.target_organism_summary, '') AS target_organism_summary,
    COALESCE(ms.target_accessions_summary, '') AS target_accessions_summary,
    COALESCE(ms.direct_interaction_summary, '') AS direct_interaction_summary,
    COALESCE(ms.molecular_mechanism_summary, '') AS molecular_mechanism_summary,
    COALESCE(ms.disease_efficacy_summary, '') AS disease_efficacy_summary,
    COALESCE(ms.mechanism_comment_summary, '') AS mechanism_comment_summary,
    COALESCE(ms.selectivity_comment_summary, '') AS selectivity_comment_summary,
    COALESCE(ms.binding_site_comment_summary, '') AS binding_site_comment_summary,

    COALESCE(ins.indication_count, 0) AS indication_count,
    ins.highest_indication_max_phase,
    COALESCE(ins.indication_phase_terms_summary, '') AS indication_phase_terms_summary,
    COALESCE(ins.highest_indication_terms_with_phase_summary, '') AS highest_indication_terms_with_phase_summary,
    COALESCE(ins.oncology_indication_count, 0) AS oncology_indication_count,
    ins.highest_oncology_indication_max_phase,
    COALESCE(ins.oncology_indication_phase_terms_summary, '') AS oncology_indication_phase_terms_summary,
    COALESCE(ins.highest_oncology_indication_terms_with_phase_summary, '') AS highest_oncology_indication_terms_with_phase_summary
FROM drug_classification.ctgov_intervention_chembl_link_export AS l
LEFT JOIN staging AS s
    ON s.nct_id = l.nct_id
   AND s.intervention_index = l.intervention_index
LEFT JOIN drug_classification.chembl_anchor_molecule_mapping AS m
    ON m.chembl_anchor_key = l.chembl_anchor_key
LEFT JOIN drug_classification.chembl_molecule AS cm
    ON cm.chembl_molecule_id = m.chembl_molecule_id
LEFT JOIN mechanism_summary AS ms
    ON ms.chembl_molecule_id = cm.chembl_molecule_id
LEFT JOIN indication_summary AS ins
    ON ins.chembl_molecule_id = cm.chembl_molecule_id;
