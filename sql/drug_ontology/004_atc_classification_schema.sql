CREATE SCHEMA IF NOT EXISTS drug_classification;

DROP VIEW IF EXISTS drug_classification.ctgov_intervention_atc_link_export;
DROP VIEW IF EXISTS drug_classification.atc_ingredient_mapping_export;

DROP TABLE IF EXISTS drug_classification.rxnorm_ingredient_atc_mapping;


-- =============================================================================
-- Part 3A ATC mapping table
-- =============================================================================
--
-- Grain:
--   one row per RxNorm ingredient anchor × ATC code.
--
-- Rows with no direct ATC atom are retained with:
--   atc_match_status = 'NO_ATC_MATCH'
--   atc_code = ''

CREATE TABLE drug_classification.rxnorm_ingredient_atc_mapping (
    load_batch_id UUID NOT NULL
        REFERENCES curation.load_batch(load_batch_id),

    rxnorm_ingredient_rxcui TEXT NOT NULL,
    rxnorm_ingredient_name TEXT,

    atc_match_status TEXT NOT NULL,
    atc_code TEXT NOT NULL DEFAULT '',
    atc_name TEXT,
    atc_level INTEGER,

    atc_l1_code TEXT,
    atc_l1_name TEXT,
    atc_l2_code TEXT,
    atc_l2_name TEXT,
    atc_l3_code TEXT,
    atc_l3_name TEXT,
    atc_l4_code TEXT,
    atc_l4_name TEXT,
    atc_l5_code TEXT,
    atc_l5_name TEXT,

    PRIMARY KEY (
        rxnorm_ingredient_rxcui,
        atc_match_status,
        atc_code
    )
);

CREATE INDEX idx_rxnorm_ingredient_atc_mapping_rxcui
    ON drug_classification.rxnorm_ingredient_atc_mapping(rxnorm_ingredient_rxcui);

CREATE INDEX idx_rxnorm_ingredient_atc_mapping_code
    ON drug_classification.rxnorm_ingredient_atc_mapping(atc_code);

CREATE INDEX idx_rxnorm_ingredient_atc_mapping_status
    ON drug_classification.rxnorm_ingredient_atc_mapping(atc_match_status);


-- =============================================================================
-- Essential export 1: RxNorm ingredient -> ATC mapping
-- =============================================================================

CREATE OR REPLACE VIEW drug_classification.atc_ingredient_mapping_export AS
SELECT
    rxnorm_ingredient_rxcui,
    rxnorm_ingredient_name,
    atc_match_status,
    atc_code,
    atc_name,
    atc_level,
    atc_l1_code,
    atc_l1_name,
    atc_l2_code,
    atc_l2_name,
    atc_l3_code,
    atc_l3_name,
    atc_l4_code,
    atc_l4_name,
    atc_l5_code,
    atc_l5_name
FROM drug_classification.rxnorm_ingredient_atc_mapping;


-- =============================================================================
-- Essential export 2: CTGov intervention/drug term -> ATC link
-- =============================================================================
--
-- Grain:
--   one row per ctgov_intervention_key × input_drug_name × ATC row.
--
-- Terms that did not resolve to a RxNorm ingredient are retained with:
--   atc_match_status = 'NO_RXNORM_INGREDIENT'

CREATE OR REPLACE VIEW drug_classification.ctgov_intervention_atc_link_export AS
SELECT
    l.ctgov_intervention_key,
    l.nct_id,
    l.intervention_index,
    l.input_drug_name,
    CASE
        WHEN m.match_status = 'MATCHED'
         AND m.rxnorm_ingredient_rxcui IS NOT NULL
         AND m.rxnorm_ingredient_rxcui <> ''
            THEN m.rxnorm_ingredient_rxcui
        ELSE NULL
    END AS rxnorm_ingredient_rxcui
FROM drug_identity.ctgov_intervention_drug_term_link AS l
LEFT JOIN drug_identity.ctgov_drug_term_rxnorm_mapping AS m
    ON m.input_drug_name = l.input_drug_name;
