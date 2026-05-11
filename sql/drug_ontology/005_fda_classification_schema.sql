CREATE SCHEMA IF NOT EXISTS drug_classification;

DROP VIEW IF EXISTS drug_classification.ctgov_intervention_fda_link_export;
DROP VIEW IF EXISTS drug_classification.fda_anchor_product_mapping_export;

DROP TABLE IF EXISTS drug_classification.rxnorm_fda_anchor_product_mapping;
DROP TABLE IF EXISTS drug_classification.input_drug_name_fda_anchor;
DROP TABLE IF EXISTS drug_classification.fda_product_ingredient_component;


-- =============================================================================
-- FDA product active-ingredient component source evidence
-- =============================================================================
--
-- Grain:
--   one row per FDA application/product/active-ingredient component.

CREATE TABLE drug_classification.fda_product_ingredient_component (
    fda_product_component_key TEXT PRIMARY KEY,

    load_batch_id UUID NOT NULL
        REFERENCES curation.load_batch(load_batch_id),

    appl_no TEXT NOT NULL,
    product_no TEXT NOT NULL,
    ingredient_position INTEGER NOT NULL,

    active_ingredient_component TEXT,
    drug_name TEXT,
    form TEXT,
    strength_component TEXT,

    appl_type TEXT,
    sponsor_name TEXT,
    marketing_status_description TEXT,
    latest_approved_submission_status_date TEXT,
    has_approved_submission BOOLEAN,

    fda_link_anchor_rxcui TEXT,
    fda_link_anchor_name TEXT,
    fda_link_anchor_term_type TEXT
);

CREATE INDEX idx_fda_product_component_anchor
    ON drug_classification.fda_product_ingredient_component(fda_link_anchor_rxcui);

CREATE INDEX idx_fda_product_component_application
    ON drug_classification.fda_product_ingredient_component(appl_no, product_no);

CREATE INDEX idx_fda_product_component_drug_name
    ON drug_classification.fda_product_ingredient_component(drug_name);


-- =============================================================================
-- Input drug term -> FDA-specific RxNorm anchor
-- =============================================================================
--
-- Grain:
--   one row per input_drug_name × FDA-specific RxNorm anchor.
--
-- This preserves product-specific anchors for cases such as brands, ADCs,
-- biologic suffixes, and simple salt/form collapse.

CREATE TABLE drug_classification.input_drug_name_fda_anchor (
    input_drug_name TEXT NOT NULL
        REFERENCES drug_identity.ctgov_drug_term(input_drug_name)
        ON DELETE CASCADE,

    rxnorm_fda_anchor_rxcui TEXT NOT NULL,
    rxnorm_fda_anchor_name TEXT,
    rxnorm_fda_anchor_term_type TEXT,

    PRIMARY KEY (
        input_drug_name,
        rxnorm_fda_anchor_rxcui
    )
);

CREATE INDEX idx_input_drug_name_fda_anchor_rxcui
    ON drug_classification.input_drug_name_fda_anchor(rxnorm_fda_anchor_rxcui);


-- =============================================================================
-- FDA-specific RxNorm anchor -> FDA product component
-- =============================================================================
--
-- Grain:
--   one row per FDA-specific RxNorm anchor × FDA product component.
--
-- If no FDA component is found for an anchor, fda_product_component_key = ''
-- and fda_link_status = 'NO_FDA_PRODUCT_FOR_RXNORM_ANCHOR'.

CREATE TABLE drug_classification.rxnorm_fda_anchor_product_mapping (
    rxnorm_fda_anchor_rxcui TEXT NOT NULL,
    rxnorm_fda_anchor_name TEXT,
    rxnorm_fda_anchor_term_type TEXT,

    fda_link_status TEXT NOT NULL,
    fda_product_component_key TEXT NOT NULL DEFAULT '',

    PRIMARY KEY (
        rxnorm_fda_anchor_rxcui,
        fda_product_component_key
    )
);

CREATE INDEX idx_rxnorm_fda_anchor_product_mapping_status
    ON drug_classification.rxnorm_fda_anchor_product_mapping(fda_link_status);

CREATE INDEX idx_rxnorm_fda_anchor_product_mapping_component
    ON drug_classification.rxnorm_fda_anchor_product_mapping(fda_product_component_key);


-- =============================================================================
-- Essential export 1: FDA-specific RxNorm anchor -> FDA product component
-- =============================================================================

CREATE OR REPLACE VIEW drug_classification.fda_anchor_product_mapping_export AS
SELECT
    m.rxnorm_fda_anchor_rxcui,
    m.rxnorm_fda_anchor_name,
    m.rxnorm_fda_anchor_term_type,
    m.fda_link_status,

    m.fda_product_component_key,
    c.appl_no,
    c.product_no,
    c.ingredient_position,
    c.drug_name,
    c.active_ingredient_component,
    c.strength_component,
    c.form,
    c.appl_type,
    c.sponsor_name,
    c.marketing_status_description,
    c.latest_approved_submission_status_date,
    c.has_approved_submission
FROM drug_classification.rxnorm_fda_anchor_product_mapping AS m
LEFT JOIN drug_classification.fda_product_ingredient_component AS c
    ON c.fda_product_component_key = m.fda_product_component_key;


-- =============================================================================
-- Essential export 2: CTGov intervention/input drug term -> FDA-specific anchor
-- =============================================================================
--
-- Grain:
--   one row per ctgov_intervention_key × input_drug_name × FDA-specific anchor.
--
-- This view deliberately does not duplicate FDA product/application fields.

CREATE OR REPLACE VIEW drug_classification.ctgov_intervention_fda_link_export AS
SELECT
    l.ctgov_intervention_key,
    l.nct_id,
    l.intervention_index,
    l.input_drug_name,
    a.rxnorm_fda_anchor_rxcui
FROM drug_identity.ctgov_intervention_drug_term_link AS l
LEFT JOIN drug_classification.input_drug_name_fda_anchor AS a
    ON a.input_drug_name = l.input_drug_name;
