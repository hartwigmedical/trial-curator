CREATE SCHEMA IF NOT EXISTS drug_classification;

DROP VIEW IF EXISTS drug_classification.pottr_intervention_classification_review_export;
DROP VIEW IF EXISTS drug_classification.ctgov_intervention_pottr_link_export;
DROP VIEW IF EXISTS drug_classification.pottr_anchor_class_mapping_export;

DROP TABLE IF EXISTS drug_classification.pottr_anchor_class_mapping;
DROP TABLE IF EXISTS drug_classification.input_drug_name_pottr_anchor;
DROP TABLE IF EXISTS drug_classification.pottr_drug_class_assignment;
DROP TABLE IF EXISTS drug_classification.pottr_drug_concept;


-- =============================================================================
-- POTTR source concepts
-- =============================================================================

CREATE TABLE drug_classification.pottr_drug_concept (
    pottr_concept_key TEXT PRIMARY KEY,
    concept_index INTEGER,
    drug_raw TEXT,
    canonical_drug_name TEXT,
    aliases_raw TEXT,
    direct_classes_raw TEXT
);

CREATE INDEX idx_pottr_drug_concept_canonical_name
    ON drug_classification.pottr_drug_concept(canonical_drug_name);


-- =============================================================================
-- POTTR concept class assignments
-- =============================================================================

CREATE TABLE drug_classification.pottr_drug_class_assignment (
    pottr_class_assignment_key TEXT PRIMARY KEY,
    pottr_concept_key TEXT NOT NULL
        REFERENCES drug_classification.pottr_drug_concept(pottr_concept_key)
        ON DELETE CASCADE,

    direct_class_name TEXT,
    pottr_class_name TEXT,
    class_relation TEXT,
    class_depth_from_direct INTEGER,
    class_path TEXT,
    class_in_hierarchy BOOLEAN
);

CREATE INDEX idx_pottr_class_assignment_concept
    ON drug_classification.pottr_drug_class_assignment(pottr_concept_key);

CREATE INDEX idx_pottr_class_assignment_class
    ON drug_classification.pottr_drug_class_assignment(pottr_class_name);


-- =============================================================================
-- Input drug term -> POTTR anchor
-- =============================================================================
--
-- Grain:
--   one row per input_drug_name × pottr_anchor_key.
--
-- POTTR anchors can be RxNorm anchors or direct alias anchors.

CREATE TABLE drug_classification.input_drug_name_pottr_anchor (
    input_drug_name TEXT NOT NULL
        REFERENCES drug_identity.ctgov_drug_term(input_drug_name)
        ON DELETE CASCADE,

    pottr_anchor_key TEXT NOT NULL,

    PRIMARY KEY (
        input_drug_name,
        pottr_anchor_key
    )
);

CREATE INDEX idx_input_drug_name_pottr_anchor_key
    ON drug_classification.input_drug_name_pottr_anchor(pottr_anchor_key);


-- =============================================================================
-- POTTR anchor -> POTTR concept/class mapping
-- =============================================================================
--
-- Grain:
--   one row per pottr_anchor_key × POTTR concept × POTTR class assignment.
--
-- If no POTTR concept/class is found:
--   pottr_link_status = 'NO_POTTR_CLASS_FOR_ANCHOR'
--   pottr_concept_key = ''
--   pottr_class_assignment_key = ''

CREATE TABLE drug_classification.pottr_anchor_class_mapping (
    pottr_anchor_key TEXT NOT NULL,
    pottr_anchor_type TEXT,
    pottr_anchor_rxcui TEXT,
    pottr_anchor_name TEXT,

    pottr_link_status TEXT NOT NULL,

    pottr_concept_key TEXT NOT NULL DEFAULT '',
    pottr_canonical_drug_name TEXT,

    pottr_class_assignment_key TEXT NOT NULL DEFAULT '',
    direct_class_name TEXT,
    pottr_class_name TEXT,
    class_relation TEXT,
    class_depth_from_direct INTEGER,
    class_path TEXT,
    class_in_hierarchy BOOLEAN,

    PRIMARY KEY (
        pottr_anchor_key,
        pottr_concept_key,
        pottr_class_assignment_key
    )
);

CREATE INDEX idx_pottr_anchor_class_mapping_anchor
    ON drug_classification.pottr_anchor_class_mapping(pottr_anchor_key);

CREATE INDEX idx_pottr_anchor_class_mapping_status
    ON drug_classification.pottr_anchor_class_mapping(pottr_link_status);

CREATE INDEX idx_pottr_anchor_class_mapping_class
    ON drug_classification.pottr_anchor_class_mapping(pottr_class_name);


-- =============================================================================
-- Essential export 1: POTTR anchor -> POTTR concept/class mapping
-- =============================================================================

CREATE OR REPLACE VIEW drug_classification.pottr_anchor_class_mapping_export AS
SELECT
    pottr_anchor_key,
    pottr_anchor_type,
    pottr_anchor_rxcui,
    pottr_anchor_name,
    pottr_link_status,
    pottr_concept_key,
    pottr_canonical_drug_name,
    pottr_class_assignment_key,
    direct_class_name,
    pottr_class_name,
    class_relation,
    class_depth_from_direct,
    class_path,
    class_in_hierarchy
FROM drug_classification.pottr_anchor_class_mapping;


-- =============================================================================
-- Essential export 2: CTGov intervention/input drug term -> POTTR anchor
-- =============================================================================
--
-- Grain:
--   one row per ctgov_intervention_key × input_drug_name × pottr_anchor_key.
--
-- This view deliberately does not duplicate POTTR class/evidence fields.

CREATE OR REPLACE VIEW drug_classification.ctgov_intervention_pottr_link_export AS
SELECT
    l.ctgov_intervention_key,
    l.nct_id,
    l.intervention_index,
    l.input_drug_name,
    a.pottr_anchor_key
FROM drug_identity.ctgov_intervention_drug_term_link AS l
LEFT JOIN drug_classification.input_drug_name_pottr_anchor AS a
    ON a.input_drug_name = l.input_drug_name;


-- =============================================================================
-- Final human-facing export: CTGov intervention/input drug term -> POTTR classes
-- =============================================================================
--
-- Grain:
--   one row per CTGov intervention × input_drug_name × matched POTTR class row.
--
-- This is the final TSV surface for reviewing POTTR classification. It uses
-- internal anchor keys for joining, but deliberately does not expose anchor,
-- concept, or class-assignment keys.

CREATE OR REPLACE VIEW drug_classification.pottr_intervention_classification_review_export AS
WITH staging AS (
    SELECT DISTINCT ON (nct_id, intervention_index)
        nct_id,
        intervention_index,
        intervention_type,
        intervention_name
    FROM ctgov.intervention_staging
    ORDER BY nct_id, intervention_index, staging_row_id DESC
)
SELECT DISTINCT
    l.nct_id,
    l.intervention_index,
    s.intervention_type,
    s.intervention_name,
    l.input_drug_name,

    COALESCE(m.pottr_link_status, 'NO_POTTR_ANCHOR') AS pottr_match_status,
    COALESCE(m.pottr_canonical_drug_name, '') AS matched_pottr_drug_name,

    COALESCE(m.direct_class_name, '') AS direct_class_name,
    COALESCE(m.pottr_class_name, '') AS pottr_class_name,
    COALESCE(m.class_relation, '') AS class_relation,
    m.class_depth_from_direct,
    COALESCE(m.class_path, '') AS class_path,
    m.class_in_hierarchy
FROM drug_classification.ctgov_intervention_pottr_link_export AS l
LEFT JOIN staging AS s
    ON s.nct_id = l.nct_id
   AND s.intervention_index = l.intervention_index
LEFT JOIN drug_classification.pottr_anchor_class_mapping AS m
    ON m.pottr_anchor_key = l.pottr_anchor_key;
