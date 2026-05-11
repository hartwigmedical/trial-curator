CREATE SCHEMA IF NOT EXISTS drug_identity;

DROP VIEW IF EXISTS drug_identity.rxnorm_drug_term_mapping_export;
DROP VIEW IF EXISTS drug_identity.ctgov_intervention_drug_term_link_export;

DROP TABLE IF EXISTS drug_identity.ctgov_drug_term_rxnorm_mapping;
DROP TABLE IF EXISTS drug_identity.ctgov_intervention_drug_term_link;
DROP TABLE IF EXISTS drug_identity.ctgov_drug_term;


-- =============================================================================
-- Part 2 canonical drug-term table
-- =============================================================================
--
-- Grain:
--   one row per distinct term from intervention_all_aliases_normalised.
--
-- The unique identifier is input_drug_name.

CREATE TABLE drug_identity.ctgov_drug_term (
    input_drug_name TEXT PRIMARY KEY
);


-- =============================================================================
-- Part 2 intervention-to-drug-term link table
-- =============================================================================
--
-- Grain:
--   one row per CTGov intervention × input_drug_name.
--
-- This table links the distinct RxNorm drug-term mapping back to trial data.

CREATE TABLE drug_identity.ctgov_intervention_drug_term_link (
    ctgov_intervention_key TEXT NOT NULL,
    nct_id TEXT NOT NULL,
    intervention_index INTEGER NOT NULL,
    input_drug_name TEXT NOT NULL
        REFERENCES drug_identity.ctgov_drug_term(input_drug_name)
        ON DELETE CASCADE,

    PRIMARY KEY (ctgov_intervention_key, input_drug_name)
);

CREATE INDEX idx_ctgov_intervention_drug_term_link_nct
    ON drug_identity.ctgov_intervention_drug_term_link(nct_id);

CREATE INDEX idx_ctgov_intervention_drug_term_link_input_drug_name
    ON drug_identity.ctgov_intervention_drug_term_link(input_drug_name);


-- =============================================================================
-- Part 2 RxNorm drug-term mapping table
-- =============================================================================
--
-- Grain:
--   one row per input_drug_name.
--
-- This is the essential RxNorm output.

CREATE TABLE drug_identity.ctgov_drug_term_rxnorm_mapping (
    input_drug_name TEXT PRIMARY KEY
        REFERENCES drug_identity.ctgov_drug_term(input_drug_name)
        ON DELETE CASCADE,

    match_status TEXT NOT NULL,
    matched_term TEXT,

    rxnorm_rxcui TEXT,
    rxnorm_canonical_name TEXT,
    rxnorm_term_type TEXT,

    rxnorm_ingredient_rxcui TEXT,
    rxnorm_ingredient_name TEXT,
    rxnorm_ingredient_term_type TEXT
);

CREATE INDEX idx_ctgov_drug_term_rxnorm_mapping_status
    ON drug_identity.ctgov_drug_term_rxnorm_mapping(match_status);

CREATE INDEX idx_ctgov_drug_term_rxnorm_mapping_rxcui
    ON drug_identity.ctgov_drug_term_rxnorm_mapping(rxnorm_rxcui);

CREATE INDEX idx_ctgov_drug_term_rxnorm_mapping_ingredient_rxcui
    ON drug_identity.ctgov_drug_term_rxnorm_mapping(rxnorm_ingredient_rxcui);


-- =============================================================================
-- Export views
-- =============================================================================

CREATE OR REPLACE VIEW drug_identity.rxnorm_drug_term_mapping_export AS
SELECT
    input_drug_name,
    match_status,
    matched_term,
    rxnorm_rxcui,
    rxnorm_canonical_name,
    rxnorm_term_type,
    rxnorm_ingredient_rxcui,
    rxnorm_ingredient_name,
    rxnorm_ingredient_term_type
FROM drug_identity.ctgov_drug_term_rxnorm_mapping;


CREATE OR REPLACE VIEW drug_identity.ctgov_intervention_drug_term_link_export AS
SELECT
    ctgov_intervention_key,
    nct_id,
    intervention_index,
    input_drug_name
FROM drug_identity.ctgov_intervention_drug_term_link;
