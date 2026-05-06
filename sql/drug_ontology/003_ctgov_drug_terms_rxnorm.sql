CREATE TABLE IF NOT EXISTS drug_identity.ctgov_drug_term (
    ctgov_drug_term_id BIGSERIAL PRIMARY KEY,

    input_drug_name TEXT NOT NULL,
    input_drug_name_normalized TEXT NOT NULL,

    source_field TEXT NOT NULL,
    term_kind TEXT NOT NULL,

    first_seen_load_batch_id UUID
        REFERENCES curation.load_batch(load_batch_id),

    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),

    UNIQUE (input_drug_name, source_field, term_kind)
);

CREATE INDEX IF NOT EXISTS idx_ctgov_drug_term_input_name
    ON drug_identity.ctgov_drug_term(input_drug_name);

CREATE INDEX IF NOT EXISTS idx_ctgov_drug_term_input_name_normalized
    ON drug_identity.ctgov_drug_term(input_drug_name_normalized);

CREATE TABLE IF NOT EXISTS drug_identity.ctgov_intervention_drug_term (
    ctgov_intervention_drug_term_id BIGSERIAL PRIMARY KEY,

    staging_row_id BIGINT NOT NULL
        REFERENCES ctgov.intervention_staging(staging_row_id)
        ON DELETE CASCADE,

    ctgov_drug_term_id BIGINT NOT NULL
        REFERENCES drug_identity.ctgov_drug_term(ctgov_drug_term_id)
        ON DELETE CASCADE,

    nct_id TEXT NOT NULL,
    intervention_index INTEGER NOT NULL,

    created_at TIMESTAMPTZ DEFAULT now(),

    UNIQUE (staging_row_id, ctgov_drug_term_id)
);

CREATE INDEX IF NOT EXISTS idx_ctgov_intervention_drug_term_staging
    ON drug_identity.ctgov_intervention_drug_term(staging_row_id);

CREATE INDEX IF NOT EXISTS idx_ctgov_intervention_drug_term_term
    ON drug_identity.ctgov_intervention_drug_term(ctgov_drug_term_id);

CREATE TABLE IF NOT EXISTS drug_identity.ctgov_drug_term_rxnorm_mapping (
    rxnorm_mapping_id BIGSERIAL PRIMARY KEY,

    ctgov_drug_term_id BIGINT NOT NULL
        REFERENCES drug_identity.ctgov_drug_term(ctgov_drug_term_id)
        ON DELETE CASCADE,

    load_batch_id UUID NOT NULL
        REFERENCES curation.load_batch(load_batch_id),

    matched_term TEXT,
    match_stage TEXT NOT NULL,
    match_status TEXT NOT NULL,

    rxnorm_rxcui TEXT,
    rxnorm_canonical_name TEXT,
    rxnorm_term_type TEXT,
    rxnorm_canonical_source TEXT,
    rxnorm_canonical_source_code TEXT,

    candidate_count INTEGER,
    candidate_summary TEXT,
    manual_review_needed BOOLEAN NOT NULL DEFAULT false,

    resolution_payload JSONB NOT NULL,

    rxnorm_source_version TEXT NOT NULL,

    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),

    UNIQUE (ctgov_drug_term_id, rxnorm_source_version)
);

CREATE INDEX IF NOT EXISTS idx_ctgov_drug_term_rxnorm_rxcui
    ON drug_identity.ctgov_drug_term_rxnorm_mapping(rxnorm_rxcui);

CREATE INDEX IF NOT EXISTS idx_ctgov_drug_term_rxnorm_status
    ON drug_identity.ctgov_drug_term_rxnorm_mapping(match_status);

CREATE INDEX IF NOT EXISTS idx_ctgov_drug_term_rxnorm_payload
    ON drug_identity.ctgov_drug_term_rxnorm_mapping USING GIN(resolution_payload);