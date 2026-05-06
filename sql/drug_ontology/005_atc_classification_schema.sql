CREATE SCHEMA IF NOT EXISTS drug_classification;

DROP TABLE IF EXISTS drug_classification.ctgov_drug_term_atc_mapping;

CREATE TABLE drug_classification.ctgov_drug_term_atc_mapping (
    atc_mapping_id BIGSERIAL PRIMARY KEY,

    ctgov_drug_term_id BIGINT NOT NULL
        REFERENCES drug_identity.ctgov_drug_term(ctgov_drug_term_id)
        ON DELETE CASCADE,

    load_batch_id UUID NOT NULL
        REFERENCES curation.load_batch(load_batch_id),

    input_drug_name TEXT NOT NULL,
    input_drug_name_normalized TEXT,
    source_field TEXT,
    term_kind TEXT,

    rxnorm_rxcui TEXT,
    rxnorm_canonical_name TEXT,
    rxnorm_term_type TEXT,

    rxnorm_ingredient_rxcui TEXT NOT NULL,
    rxnorm_ingredient_name TEXT,
    rxnorm_ingredient_term_type TEXT,
    rxnorm_mapping_manual_review_needed BOOLEAN,

    atc_lookup_rxcui TEXT,
    atc_lookup_rxcui_kind TEXT,
    atc_lookup_path TEXT,

    atc_code TEXT NOT NULL DEFAULT '',
    atc_name_from_rxnconso TEXT,
    atc_term_type TEXT,
    atc_rxaui TEXT,
    atc_suppress TEXT,

    atc_tree_name TEXT,
    atc_level INTEGER,
    atc_path TEXT,

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

    link_status TEXT NOT NULL,
    manual_review_needed BOOLEAN NOT NULL DEFAULT false,

    resolution_payload JSONB NOT NULL,
    rxnorm_source_version TEXT NOT NULL,
    atc_source_version TEXT NOT NULL,

    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),

    UNIQUE (
        ctgov_drug_term_id,
        rxnorm_ingredient_rxcui,
        atc_lookup_rxcui,
        atc_code,
        atc_source_version
    )
);

CREATE INDEX IF NOT EXISTS idx_ctgov_drug_term_atc_term_id
    ON drug_classification.ctgov_drug_term_atc_mapping(ctgov_drug_term_id);

CREATE INDEX IF NOT EXISTS idx_ctgov_drug_term_atc_ingredient_rxcui
    ON drug_classification.ctgov_drug_term_atc_mapping(rxnorm_ingredient_rxcui);

CREATE INDEX IF NOT EXISTS idx_ctgov_drug_term_atc_lookup_rxcui
    ON drug_classification.ctgov_drug_term_atc_mapping(atc_lookup_rxcui);

CREATE INDEX IF NOT EXISTS idx_ctgov_drug_term_atc_lookup_kind
    ON drug_classification.ctgov_drug_term_atc_mapping(atc_lookup_rxcui_kind);

CREATE INDEX IF NOT EXISTS idx_ctgov_drug_term_atc_code
    ON drug_classification.ctgov_drug_term_atc_mapping(atc_code);

CREATE INDEX IF NOT EXISTS idx_ctgov_drug_term_atc_status
    ON drug_classification.ctgov_drug_term_atc_mapping(link_status);

CREATE INDEX IF NOT EXISTS idx_ctgov_drug_term_atc_source_version
    ON drug_classification.ctgov_drug_term_atc_mapping(atc_source_version);

CREATE INDEX IF NOT EXISTS idx_ctgov_drug_term_atc_payload
    ON drug_classification.ctgov_drug_term_atc_mapping USING GIN(resolution_payload);
