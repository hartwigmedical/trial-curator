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

    source_tsv TEXT NOT NULL,
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