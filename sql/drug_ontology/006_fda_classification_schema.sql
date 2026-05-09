CREATE SCHEMA IF NOT EXISTS drug_classification;

DROP VIEW IF EXISTS drug_classification.ctgov_drug_term_fda_evidence_view CASCADE;
DROP TABLE IF EXISTS drug_classification.ctgov_drug_term_fda_mapping CASCADE;
DROP TABLE IF EXISTS drug_classification.rxnorm_ingredient_fda_mapping CASCADE;
DROP TABLE IF EXISTS drug_classification.rxnorm_fda_anchor_mapping CASCADE;
DROP TABLE IF EXISTS drug_classification.ctgov_drug_term_fda_anchor CASCADE;
DROP TABLE IF EXISTS drug_classification.fda_product_ingredient_component CASCADE;

CREATE TABLE drug_classification.fda_product_ingredient_component (
    fda_product_ingredient_component_id BIGSERIAL PRIMARY KEY,
    load_batch_id UUID NOT NULL REFERENCES curation.load_batch(load_batch_id),
    fda_source_version TEXT NOT NULL,

    appl_no TEXT NOT NULL,
    product_no TEXT NOT NULL,
    ingredient_position INTEGER NOT NULL,
    ingredient_count INTEGER NOT NULL,

    active_ingredient_raw TEXT NOT NULL,
    active_ingredient_component TEXT NOT NULL,
    active_ingredient_component_normalized TEXT NOT NULL,
    strength_raw TEXT,
    strength_component TEXT,
    strength_parse_status TEXT NOT NULL,
    component_parse_status TEXT NOT NULL,

    drug_name TEXT,
    form TEXT,
    reference_drug TEXT,
    reference_standard TEXT,

    appl_type TEXT,
    sponsor_name TEXT,
    appl_public_notes TEXT,

    marketing_status_id TEXT,
    marketing_status_description TEXT,

    original_submission_status TEXT,
    original_submission_status_date DATE,
    latest_submission_status TEXT,
    latest_submission_status_date DATE,
    latest_approved_submission_status_date DATE,
    has_approved_submission BOOLEAN,

    -- Raw FDA component -> RxNorm resolution.
    fda_rxnorm_rxcui TEXT,
    fda_rxnorm_canonical_name TEXT,
    fda_rxnorm_term_type TEXT,
    fda_rxnorm_ingredient_rxcui TEXT,
    fda_rxnorm_ingredient_name TEXT,
    fda_rxnorm_ingredient_term_type TEXT,
    fda_rxnorm_match_stage TEXT,
    fda_rxnorm_match_status TEXT,
    fda_rxnorm_ingredient_resolution_stage TEXT,
    fda_rxnorm_ingredient_path TEXT,

    -- FDA-specific matching anchor. This is intentionally not always the broad
    -- RxNorm ingredient. Simple salts/forms collapse to the broad ingredient;
    -- ADCs/radioligands/biologic conjugates retain a precise active anchor.
    fda_link_anchor_rxcui TEXT,
    fda_link_anchor_name TEXT,
    fda_link_anchor_term_type TEXT,
    fda_link_anchor_strategy TEXT,
    fda_link_anchor_path TEXT,
    fda_link_anchor_manual_review_needed BOOLEAN NOT NULL DEFAULT FALSE,

    fda_manual_review_needed BOOLEAN NOT NULL DEFAULT FALSE,
    resolution_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT fda_product_ingredient_component_unique
        UNIQUE (
            fda_source_version,
            appl_no,
            product_no,
            ingredient_position,
            active_ingredient_component_normalized
        )
);

CREATE INDEX idx_fda_product_component_source
    ON drug_classification.fda_product_ingredient_component (fda_source_version);

CREATE INDEX idx_fda_product_component_product
    ON drug_classification.fda_product_ingredient_component (appl_no, product_no);

CREATE INDEX idx_fda_product_component_raw_ingredient_rxcui
    ON drug_classification.fda_product_ingredient_component (fda_rxnorm_ingredient_rxcui);

CREATE INDEX idx_fda_product_component_link_anchor
    ON drug_classification.fda_product_ingredient_component (fda_link_anchor_rxcui);

CREATE INDEX idx_fda_product_component_link_strategy
    ON drug_classification.fda_product_ingredient_component (fda_link_anchor_strategy);

CREATE INDEX idx_fda_product_component_marketing_status
    ON drug_classification.fda_product_ingredient_component (marketing_status_description);

CREATE TABLE drug_classification.ctgov_drug_term_fda_anchor (
    ctgov_drug_term_fda_anchor_id BIGSERIAL PRIMARY KEY,
    ctgov_drug_term_id BIGINT NOT NULL REFERENCES drug_identity.ctgov_drug_term(ctgov_drug_term_id),
    load_batch_id UUID NOT NULL REFERENCES curation.load_batch(load_batch_id),

    input_drug_name TEXT NOT NULL,
    input_drug_name_normalized TEXT,
    source_field TEXT,
    term_kind TEXT,

    rxnorm_rxcui TEXT,
    rxnorm_canonical_name TEXT,
    rxnorm_term_type TEXT,
    rxnorm_ingredient_rxcui TEXT,
    rxnorm_ingredient_name TEXT,
    rxnorm_ingredient_term_type TEXT,
    rxnorm_mapping_manual_review_needed BOOLEAN NOT NULL DEFAULT FALSE,

    rxnorm_fda_anchor_rxcui TEXT NOT NULL,
    rxnorm_fda_anchor_name TEXT,
    rxnorm_fda_anchor_term_type TEXT,
    rxnorm_fda_anchor_strategy TEXT,
    rxnorm_fda_anchor_path TEXT,
    rxnorm_fda_anchor_manual_review_needed BOOLEAN NOT NULL DEFAULT FALSE,

    rxnorm_source_version TEXT NOT NULL,
    fda_source_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ctgov_drug_term_fda_anchor_unique
        UNIQUE (ctgov_drug_term_id, rxnorm_fda_anchor_rxcui, rxnorm_source_version, fda_source_version)
);

CREATE INDEX idx_ctgov_drug_term_fda_anchor_source
    ON drug_classification.ctgov_drug_term_fda_anchor (rxnorm_source_version, fda_source_version);

CREATE INDEX idx_ctgov_drug_term_fda_anchor_anchor
    ON drug_classification.ctgov_drug_term_fda_anchor (rxnorm_fda_anchor_rxcui);

CREATE INDEX idx_ctgov_drug_term_fda_anchor_term
    ON drug_classification.ctgov_drug_term_fda_anchor (ctgov_drug_term_id);

CREATE TABLE drug_classification.rxnorm_fda_anchor_mapping (
    rxnorm_fda_anchor_mapping_id BIGSERIAL PRIMARY KEY,
    fda_product_ingredient_component_id BIGINT
        REFERENCES drug_classification.fda_product_ingredient_component(fda_product_ingredient_component_id),
    load_batch_id UUID NOT NULL REFERENCES curation.load_batch(load_batch_id),

    rxnorm_fda_anchor_rxcui TEXT NOT NULL,
    rxnorm_fda_anchor_name TEXT,
    rxnorm_fda_anchor_term_type TEXT,
    rxnorm_fda_anchor_strategy TEXT,
    rxnorm_fda_anchor_path TEXT,

    representative_rxnorm_ingredient_rxcui TEXT,
    representative_rxnorm_ingredient_name TEXT,
    representative_rxnorm_ingredient_term_type TEXT,

    ctgov_term_count INTEGER NOT NULL DEFAULT 0,
    ctgov_manual_review_needed_any BOOLEAN NOT NULL DEFAULT FALSE,
    ctgov_input_drug_name_sample TEXT,

    link_status TEXT NOT NULL,
    manual_review_needed BOOLEAN NOT NULL DEFAULT FALSE,
    resolution_payload JSONB NOT NULL DEFAULT '{}'::jsonb,

    rxnorm_source_version TEXT NOT NULL,
    fda_source_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX idx_rxnorm_fda_anchor_mapping_matched_unique
    ON drug_classification.rxnorm_fda_anchor_mapping (
        rxnorm_source_version,
        fda_source_version,
        rxnorm_fda_anchor_rxcui,
        fda_product_ingredient_component_id
    )
    WHERE fda_product_ingredient_component_id IS NOT NULL;

CREATE UNIQUE INDEX idx_rxnorm_fda_anchor_mapping_unmatched_unique
    ON drug_classification.rxnorm_fda_anchor_mapping (
        rxnorm_source_version,
        fda_source_version,
        rxnorm_fda_anchor_rxcui
    )
    WHERE fda_product_ingredient_component_id IS NULL;

CREATE INDEX idx_rxnorm_fda_anchor_mapping_source
    ON drug_classification.rxnorm_fda_anchor_mapping (fda_source_version, rxnorm_source_version);

CREATE INDEX idx_rxnorm_fda_anchor_mapping_anchor
    ON drug_classification.rxnorm_fda_anchor_mapping (rxnorm_fda_anchor_rxcui);

CREATE INDEX idx_rxnorm_fda_anchor_mapping_component
    ON drug_classification.rxnorm_fda_anchor_mapping (fda_product_ingredient_component_id);

CREATE INDEX idx_rxnorm_fda_anchor_mapping_status
    ON drug_classification.rxnorm_fda_anchor_mapping (link_status);

-- Explicit provenance view. This intentionally expands anchor-level FDA evidence
-- back to CTGov input terms using the materialized CTGov-term FDA anchor table.
-- Do not use it as the FDA uniqueness layer.
CREATE VIEW drug_classification.ctgov_drug_term_fda_evidence_view AS
SELECT
    a.ctgov_drug_term_id,
    a.input_drug_name,
    a.input_drug_name_normalized,
    a.source_field,
    a.term_kind,
    a.rxnorm_rxcui,
    a.rxnorm_canonical_name,
    a.rxnorm_term_type,
    a.rxnorm_ingredient_rxcui,
    a.rxnorm_ingredient_name,
    a.rxnorm_ingredient_term_type,
    a.rxnorm_mapping_manual_review_needed,
    a.rxnorm_fda_anchor_rxcui AS ctgov_rxnorm_fda_anchor_rxcui,
    a.rxnorm_fda_anchor_name AS ctgov_rxnorm_fda_anchor_name,
    a.rxnorm_fda_anchor_term_type AS ctgov_rxnorm_fda_anchor_term_type,
    a.rxnorm_fda_anchor_strategy AS ctgov_rxnorm_fda_anchor_strategy,
    f.rxnorm_fda_anchor_mapping_id,
    f.fda_product_ingredient_component_id,
    f.link_status,
    f.manual_review_needed AS fda_mapping_manual_review_needed,
    c.appl_no,
    c.product_no,
    c.ingredient_position,
    c.ingredient_count,
    c.drug_name,
    c.active_ingredient_component,
    c.fda_rxnorm_rxcui,
    c.fda_rxnorm_canonical_name,
    c.fda_rxnorm_term_type,
    c.fda_rxnorm_ingredient_rxcui,
    c.fda_rxnorm_ingredient_name,
    c.fda_link_anchor_rxcui,
    c.fda_link_anchor_name,
    c.fda_link_anchor_strategy,
    c.strength_component,
    c.form,
    c.marketing_status_description,
    a.rxnorm_source_version,
    a.fda_source_version
FROM drug_classification.ctgov_drug_term_fda_anchor a
JOIN drug_classification.rxnorm_fda_anchor_mapping f
  ON f.rxnorm_fda_anchor_rxcui = a.rxnorm_fda_anchor_rxcui
 AND f.rxnorm_source_version = a.rxnorm_source_version
 AND f.fda_source_version = a.fda_source_version
LEFT JOIN drug_classification.fda_product_ingredient_component c
  ON c.fda_product_ingredient_component_id = f.fda_product_ingredient_component_id;
