-- POTTR classification schema.
-- Materialized layer is POTTR-anchor-level, not CTGov alias/source-field-level.

CREATE SCHEMA IF NOT EXISTS drug_classification;

DROP VIEW IF EXISTS drug_classification.ctgov_drug_term_pottr_evidence_view;
DROP TABLE IF EXISTS drug_classification.pottr_anchor_mapping CASCADE;
DROP TABLE IF EXISTS drug_classification.ctgov_pottr_anchor_drug_term CASCADE;
DROP TABLE IF EXISTS drug_classification.ctgov_pottr_anchor CASCADE;
DROP TABLE IF EXISTS drug_classification.pottr_drug_class_assignment CASCADE;
DROP TABLE IF EXISTS drug_classification.pottr_drug_alias CASCADE;
DROP TABLE IF EXISTS drug_classification.pottr_drug_concept CASCADE;

CREATE TABLE drug_classification.pottr_drug_concept (
    pottr_drug_concept_id BIGSERIAL PRIMARY KEY,
    load_batch_id UUID NOT NULL,
    pottr_concept_key TEXT NOT NULL,
    concept_index INTEGER NOT NULL,
    drug_raw TEXT NOT NULL,
    canonical_drug_name TEXT,
    aliases_raw TEXT,
    direct_classes_raw TEXT,
    pottr_source_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (pottr_source_version, pottr_concept_key)
);

CREATE TABLE drug_classification.pottr_drug_alias (
    pottr_drug_alias_id BIGSERIAL PRIMARY KEY,
    pottr_drug_concept_id BIGINT NOT NULL REFERENCES drug_classification.pottr_drug_concept(pottr_drug_concept_id) ON DELETE CASCADE,
    load_batch_id UUID NOT NULL,
    alias_position INTEGER NOT NULL,
    alias TEXT NOT NULL,
    alias_normalized TEXT NOT NULL,
    rxnorm_rxcui TEXT,
    rxnorm_canonical_name TEXT,
    rxnorm_term_type TEXT,
    rxnorm_match_stage TEXT,
    rxnorm_match_status TEXT,
    rxnorm_ingredient_rxcui TEXT,
    rxnorm_ingredient_name TEXT,
    rxnorm_ingredient_term_type TEXT,
    rxnorm_ingredient_resolution_stage TEXT,
    rxnorm_ingredient_path TEXT,
    pottr_link_anchor_rxcui TEXT,
    pottr_link_anchor_name TEXT,
    pottr_link_anchor_term_type TEXT,
    pottr_link_anchor_strategy TEXT,
    pottr_link_anchor_path TEXT,
    manual_review_needed BOOLEAN NOT NULL DEFAULT false,
    resolution_payload JSONB,
    rxnorm_source_version TEXT NOT NULL,
    pottr_source_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (pottr_source_version, pottr_drug_concept_id, alias_position)
);

CREATE TABLE drug_classification.pottr_drug_class_assignment (
    pottr_drug_class_assignment_id BIGSERIAL PRIMARY KEY,
    pottr_drug_concept_id BIGINT NOT NULL REFERENCES drug_classification.pottr_drug_concept(pottr_drug_concept_id) ON DELETE CASCADE,
    load_batch_id UUID NOT NULL,
    assignment_key TEXT NOT NULL,
    direct_class_name TEXT NOT NULL,
    pottr_class_name TEXT NOT NULL,
    class_relation TEXT NOT NULL,
    class_depth_from_direct INTEGER NOT NULL,
    class_path TEXT,
    class_in_hierarchy BOOLEAN NOT NULL DEFAULT false,
    pottr_source_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (pottr_source_version, assignment_key)
);

CREATE TABLE drug_classification.ctgov_pottr_anchor (
    ctgov_pottr_anchor_id BIGSERIAL PRIMARY KEY,
    load_batch_id UUID NOT NULL,
    anchor_key TEXT NOT NULL,
    anchor_type TEXT NOT NULL,
    anchor_rxcui TEXT,
    anchor_name TEXT NOT NULL,
    anchor_term_type TEXT,
    anchor_strategy TEXT NOT NULL,
    anchor_path TEXT,
    represented_ctgov_drug_term_count INTEGER NOT NULL DEFAULT 0,
    represented_input_names_summary TEXT,
    manual_review_needed BOOLEAN NOT NULL DEFAULT false,
    rxnorm_source_version TEXT NOT NULL,
    pottr_source_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (rxnorm_source_version, pottr_source_version, anchor_key)
);

CREATE TABLE drug_classification.ctgov_pottr_anchor_drug_term (
    ctgov_pottr_anchor_drug_term_id BIGSERIAL PRIMARY KEY,
    ctgov_pottr_anchor_id BIGINT NOT NULL REFERENCES drug_classification.ctgov_pottr_anchor(ctgov_pottr_anchor_id) ON DELETE CASCADE,
    ctgov_drug_term_id BIGINT NOT NULL REFERENCES drug_identity.ctgov_drug_term(ctgov_drug_term_id) ON DELETE CASCADE,
    input_drug_name TEXT NOT NULL,
    source_field TEXT,
    term_kind TEXT,
    anchor_selection_strategy TEXT NOT NULL,
    rxnorm_source_version TEXT NOT NULL,
    pottr_source_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (rxnorm_source_version, pottr_source_version, ctgov_drug_term_id)
);

CREATE TABLE drug_classification.pottr_anchor_mapping (
    pottr_anchor_mapping_id BIGSERIAL PRIMARY KEY,
    ctgov_pottr_anchor_id BIGINT NOT NULL REFERENCES drug_classification.ctgov_pottr_anchor(ctgov_pottr_anchor_id) ON DELETE CASCADE,
    pottr_drug_concept_id BIGINT REFERENCES drug_classification.pottr_drug_concept(pottr_drug_concept_id) ON DELETE CASCADE,
    pottr_drug_class_assignment_id BIGINT REFERENCES drug_classification.pottr_drug_class_assignment(pottr_drug_class_assignment_id) ON DELETE CASCADE,
    load_batch_id UUID NOT NULL,
    link_status TEXT NOT NULL,
    match_strategy TEXT NOT NULL,
    direct_class_name TEXT,
    pottr_class_name TEXT,
    class_relation TEXT,
    class_depth_from_direct INTEGER,
    class_path TEXT,
    class_in_hierarchy BOOLEAN,
    resolution_payload JSONB,
    rxnorm_source_version TEXT NOT NULL,
    pottr_source_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (rxnorm_source_version, pottr_source_version, ctgov_pottr_anchor_id, pottr_drug_class_assignment_id)
);

CREATE UNIQUE INDEX pottr_anchor_mapping_no_match_uniq
ON drug_classification.pottr_anchor_mapping (rxnorm_source_version, pottr_source_version, ctgov_pottr_anchor_id)
WHERE pottr_drug_class_assignment_id IS NULL;

CREATE INDEX pottr_alias_norm_idx
ON drug_classification.pottr_drug_alias (pottr_source_version, alias_normalized);

CREATE INDEX pottr_alias_anchor_idx
ON drug_classification.pottr_drug_alias (rxnorm_source_version, pottr_source_version, pottr_link_anchor_rxcui)
WHERE pottr_link_anchor_rxcui IS NOT NULL;

CREATE INDEX ctgov_pottr_anchor_key_idx
ON drug_classification.ctgov_pottr_anchor (rxnorm_source_version, pottr_source_version, anchor_key);

CREATE INDEX pottr_anchor_mapping_anchor_idx
ON drug_classification.pottr_anchor_mapping (rxnorm_source_version, pottr_source_version, ctgov_pottr_anchor_id);

CREATE OR REPLACE VIEW drug_classification.ctgov_drug_term_pottr_evidence_view AS
SELECT
    adt.ctgov_drug_term_id,
    adt.input_drug_name,
    adt.source_field,
    adt.term_kind,
    a.anchor_key,
    a.anchor_type,
    a.anchor_rxcui,
    a.anchor_name,
    a.anchor_term_type,
    a.anchor_strategy,
    a.anchor_path,
    m.link_status,
    m.match_strategy,
    c.pottr_concept_key,
    c.canonical_drug_name AS pottr_canonical_drug_name,
    c.aliases_raw AS pottr_aliases_raw,
    c.direct_classes_raw AS pottr_direct_classes_raw,
    m.direct_class_name,
    m.pottr_class_name,
    m.class_relation,
    m.class_depth_from_direct,
    m.class_path,
    m.class_in_hierarchy,
    a.rxnorm_source_version,
    a.pottr_source_version
FROM drug_classification.ctgov_pottr_anchor_drug_term adt
JOIN drug_classification.ctgov_pottr_anchor a
  ON a.ctgov_pottr_anchor_id = adt.ctgov_pottr_anchor_id
LEFT JOIN drug_classification.pottr_anchor_mapping m
  ON m.ctgov_pottr_anchor_id = a.ctgov_pottr_anchor_id
LEFT JOIN drug_classification.pottr_drug_concept c
  ON c.pottr_drug_concept_id = m.pottr_drug_concept_id;
