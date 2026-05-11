CREATE TABLE IF NOT EXISTS curation.load_batch (
    load_batch_id UUID PRIMARY KEY,
    source_name TEXT NOT NULL,
    source_file TEXT NOT NULL,
    source_version TEXT,
    started_at TIMESTAMPTZ DEFAULT now(),
    completed_at TIMESTAMPTZ,
    row_count INTEGER,
    status TEXT DEFAULT 'started',
    note TEXT
);

CREATE TABLE IF NOT EXISTS ctgov.intervention_staging (
    staging_row_id BIGSERIAL PRIMARY KEY,

    load_batch_id UUID NOT NULL
        REFERENCES curation.load_batch(load_batch_id),

    nct_id TEXT NOT NULL,
    intervention_index INTEGER NOT NULL,
    intervention_type TEXT,
    intervention_name TEXT NOT NULL,
    intervention_description TEXT,

    source_file TEXT NOT NULL,
    source_version TEXT,

    row_payload JSONB NOT NULL,

    created_at TIMESTAMPTZ DEFAULT now(),

    UNIQUE (load_batch_id, nct_id, intervention_index)
);

CREATE INDEX IF NOT EXISTS idx_ctgov_intervention_staging_nct_id
    ON ctgov.intervention_staging(nct_id);

CREATE INDEX IF NOT EXISTS idx_ctgov_intervention_staging_name
    ON ctgov.intervention_staging(intervention_name);

CREATE INDEX IF NOT EXISTS idx_ctgov_intervention_staging_payload
    ON ctgov.intervention_staging USING GIN(row_payload);

CREATE INDEX IF NOT EXISTS idx_ctgov_intervention_staging_load_batch
    ON ctgov.intervention_staging(load_batch_id);

CREATE INDEX IF NOT EXISTS idx_ctgov_intervention_staging_source_version
    ON ctgov.intervention_staging(source_version);


-- Human-readable Part 1 review view.
-- This is the accepted TSV export surface for Part 1. The raw staging table
-- retains internal provenance fields; this view exposes user-facing fields only.
CREATE OR REPLACE VIEW ctgov.intervention_staging_review AS
SELECT
    nct_id,
    intervention_index,
    intervention_type,
    intervention_name,
    intervention_description,
    row_payload->>'intervention_otherNames' AS "intervention_otherNames",
    row_payload->>'intervention_armGroupLabels' AS "intervention_armGroupLabels",
    row_payload->>'intervention_all_aliases' AS intervention_all_aliases,
    row_payload->>'intervention_all_aliases_normalised' AS intervention_all_aliases_normalised,
    row_payload->>'intervention_all_aliases_normalised_full' AS intervention_all_aliases_normalised_full
FROM ctgov.intervention_staging;
