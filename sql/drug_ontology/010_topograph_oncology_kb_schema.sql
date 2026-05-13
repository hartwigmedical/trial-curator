CREATE SCHEMA IF NOT EXISTS oncology_kb;

-- =============================================================================
-- TOPOGRAPH oncology knowledgebase source tables + CTGov arm-level link/export views
-- =============================================================================
--
-- Source design:
--   TOPOGRAPH-master.tsv raw row
--       -> semicolon-split therapy options
--       -> plus-split therapy components
--
-- Correct CTGov link grain:
--   Link/review views: one row per CTGov trial arm x exact matched TOPOGRAPH therapy option/evidence assertion.
--   Final colleague-facing view: arm-first; one row per CTGov trial arm, with
--   additional rows only when exact TOPOGRAPH evidence matches exist. Arms with
--   no exact TOPOGRAPH regimen match are retained with blank TOPOGRAPH columns.
--
-- Link design:
--   * Build a CTGov arm-intervention context first:
--       nct_id + arm_group_label + intervention/drug term.
--   * CTGov arm A | B | C is treated as the regimen set {A, B, C}.
--   * TOPOGRAPH option A + B + C is treated as the regimen set {A, B, C}.
--   * TOPOGRAPH semicolon alternatives A; B; C remain separate therapy options.
--   * A main link is created only when the CTGov arm regimen set exactly equals
--       the TOPOGRAPH therapy-option set. No subset/overlap/any-drug matching.
--
-- TSV rule:
--   These views are the export surfaces. Dump them directly with \copy.
--   No exported TSV is read back as a pipeline input.

DROP VIEW IF EXISTS oncology_kb.ctgov_trial_topograph_summary_export CASCADE;
DROP VIEW IF EXISTS oncology_kb.ctgov_topograph_evidence_review_export CASCADE;
DROP VIEW IF EXISTS oncology_kb.ctgov_topograph_component_review_export CASCADE;
DROP VIEW IF EXISTS oncology_kb.ctgov_topograph_therapy_link_export CASCADE;
DROP VIEW IF EXISTS oncology_kb.ctgov_topograph_arm_regimen_context CASCADE;
DROP VIEW IF EXISTS oncology_kb.ctgov_topograph_arm_intervention_context CASCADE;
DROP VIEW IF EXISTS oncology_kb.topograph_therapy_option_export CASCADE;

DROP TABLE IF EXISTS oncology_kb.topograph_therapy_component CASCADE;
DROP TABLE IF EXISTS oncology_kb.topograph_therapy_option CASCADE;
DROP TABLE IF EXISTS oncology_kb.topograph_raw_assertion CASCADE;

CREATE TABLE oncology_kb.topograph_raw_assertion (
    topograph_raw_assertion_id BIGSERIAL PRIMARY KEY,

    load_batch_id UUID NOT NULL
        REFERENCES curation.load_batch(load_batch_id),

    raw_assertion_key TEXT NOT NULL UNIQUE,
    topograph_raw_row_index INTEGER NOT NULL,

    tier TEXT NOT NULL,
    biomarker TEXT NOT NULL,
    alteration TEXT NOT NULL,
    tumour_type TEXT NOT NULL,
    drugs TEXT NOT NULL,
    comments TEXT NOT NULL,
    evidence TEXT NOT NULL,
    evidence_direction TEXT NOT NULL,

    topograph_source_version TEXT NOT NULL,

    created_at TIMESTAMPTZ DEFAULT now(),

    UNIQUE (topograph_source_version, topograph_raw_row_index)
);

CREATE INDEX idx_topograph_raw_source_version
    ON oncology_kb.topograph_raw_assertion(topograph_source_version);

CREATE INDEX idx_topograph_raw_tier
    ON oncology_kb.topograph_raw_assertion(tier);

CREATE INDEX idx_topograph_raw_biomarker
    ON oncology_kb.topograph_raw_assertion(biomarker);

CREATE INDEX idx_topograph_raw_tumour_type
    ON oncology_kb.topograph_raw_assertion(tumour_type);


CREATE TABLE oncology_kb.topograph_therapy_option (
    topograph_therapy_option_id BIGSERIAL PRIMARY KEY,

    topograph_raw_assertion_id BIGINT NOT NULL
        REFERENCES oncology_kb.topograph_raw_assertion(topograph_raw_assertion_id)
        ON DELETE CASCADE,

    load_batch_id UUID NOT NULL
        REFERENCES curation.load_batch(load_batch_id),

    therapy_option_key TEXT NOT NULL UNIQUE,
    raw_assertion_key TEXT NOT NULL,
    topograph_raw_row_index INTEGER NOT NULL,
    therapy_option_index INTEGER NOT NULL,

    therapy_option_raw TEXT NOT NULL,
    therapy_option_norm TEXT NOT NULL,
    therapy_option_type TEXT NOT NULL,
    component_count INTEGER NOT NULL,

    topograph_source_version TEXT NOT NULL,

    created_at TIMESTAMPTZ DEFAULT now(),

    UNIQUE (topograph_source_version, topograph_raw_row_index, therapy_option_index)
);

CREATE INDEX idx_topograph_option_source_version
    ON oncology_kb.topograph_therapy_option(topograph_source_version);

CREATE INDEX idx_topograph_option_norm
    ON oncology_kb.topograph_therapy_option(therapy_option_norm);

CREATE INDEX idx_topograph_option_type
    ON oncology_kb.topograph_therapy_option(therapy_option_type);


CREATE TABLE oncology_kb.topograph_therapy_component (
    topograph_therapy_component_id BIGSERIAL PRIMARY KEY,

    topograph_therapy_option_id BIGINT NOT NULL
        REFERENCES oncology_kb.topograph_therapy_option(topograph_therapy_option_id)
        ON DELETE CASCADE,

    load_batch_id UUID NOT NULL
        REFERENCES curation.load_batch(load_batch_id),

    therapy_component_key TEXT NOT NULL UNIQUE,
    therapy_option_key TEXT NOT NULL,
    raw_assertion_key TEXT NOT NULL,
    topograph_raw_row_index INTEGER NOT NULL,
    therapy_option_index INTEGER NOT NULL,
    component_index INTEGER NOT NULL,

    component_raw TEXT NOT NULL,
    component_norm TEXT NOT NULL,

    topograph_source_version TEXT NOT NULL,

    created_at TIMESTAMPTZ DEFAULT now(),

    UNIQUE (therapy_option_key, component_index)
);

CREATE INDEX idx_topograph_component_option_key
    ON oncology_kb.topograph_therapy_component(therapy_option_key);

CREATE INDEX idx_topograph_component_norm
    ON oncology_kb.topograph_therapy_component(component_norm);

CREATE INDEX idx_topograph_component_source_version
    ON oncology_kb.topograph_therapy_component(topograph_source_version);


-- =============================================================================
-- Source-normalized export: one row per semicolon-split TOPOGRAPH therapy option
-- =============================================================================

CREATE OR REPLACE VIEW oncology_kb.topograph_therapy_option_export AS
SELECT
    r.topograph_source_version,
    r.raw_assertion_key,
    r.topograph_raw_row_index,
    o.therapy_option_key,
    o.therapy_option_index,
    regexp_replace(COALESCE(o.therapy_option_raw, ''), E'[\r\n\t]+', ' ', 'g') AS therapy_option,
    regexp_replace(COALESCE(o.therapy_option_norm, ''), E'[\r\n\t]+', ' ', 'g') AS therapy_option_norm,
    o.therapy_option_type,
    o.component_count,
    COALESCE(c.components, '') AS therapy_components,
    regexp_replace(COALESCE(r.tier, ''), E'[\r\n\t]+', ' ', 'g') AS tier,
    r.evidence_direction,
    regexp_replace(COALESCE(r.biomarker, ''), E'[\r\n\t]+', ' ', 'g') AS biomarker,
    regexp_replace(COALESCE(r.alteration, ''), E'[\r\n\t]+', ' ', 'g') AS alteration,
    regexp_replace(COALESCE(r.tumour_type, ''), E'[\r\n\t]+', ' ', 'g') AS tumour_type,
    regexp_replace(COALESCE(r.comments, ''), E'[\r\n\t]+', ' ', 'g') AS comments,
    regexp_replace(COALESCE(r.evidence, ''), E'[\r\n\t]+', ' ', 'g') AS evidence
FROM oncology_kb.topograph_therapy_option AS o
JOIN oncology_kb.topograph_raw_assertion AS r
    ON r.topograph_raw_assertion_id = o.topograph_raw_assertion_id
LEFT JOIN LATERAL (
    SELECT string_agg(component_raw, ' + ' ORDER BY component_index) AS components
    FROM oncology_kb.topograph_therapy_component AS tc
    WHERE tc.topograph_therapy_option_id = o.topograph_therapy_option_id
) AS c ON true;


-- =============================================================================
-- CTGov arm-intervention context.
--
-- Grain:
--   one row per nct_id x arm_group_label x intervention x input drug term.
--
-- This is the critical CTGov context for TOPOGRAPH linking. The final output is
-- arm-first, so we do not copy all trial arms into a matched intervention row.
-- =============================================================================

CREATE OR REPLACE VIEW oncology_kb.ctgov_topograph_arm_intervention_context AS
WITH base AS (
    SELECT DISTINCT
        l.ctgov_intervention_key,
        l.nct_id,
        l.intervention_index,
        s.intervention_type,
        s.intervention_name,
        s.intervention_description,
        l.input_drug_name,
        btrim(
            regexp_replace(
                regexp_replace(
                    regexp_replace(
                        lower(COALESCE(l.input_drug_name, '')),
                        '&',
                        ' and ',
                        'g'
                    ),
                    '[^a-z0-9]+',
                    ' ',
                    'g'
                ),
                '[[:space:]]+',
                ' ',
                'g'
            )
        ) AS input_drug_name_normalized,
        COALESCE(
            s.row_payload->'intervention_armGroupLabels',
            s.row_payload->'armGroupLabels',
            s.row_payload->'arm_group_labels'
        ) AS arm_labels_json
    FROM drug_identity.ctgov_intervention_drug_term_link AS l
    LEFT JOIN ctgov.intervention_staging AS s
        ON s.nct_id = l.nct_id
       AND s.intervention_index = l.intervention_index
    WHERE l.input_drug_name IS NOT NULL
      AND btrim(l.input_drug_name) <> ''
), exploded AS (
    SELECT
        b.*,
        NULLIF(btrim(a.arm_group_label), '') AS arm_group_label
    FROM base AS b
    LEFT JOIN LATERAL (
        SELECT arm_label AS arm_group_label
        FROM (
            SELECT jsonb_array_elements_text(b.arm_labels_json) AS arm_label
            WHERE jsonb_typeof(b.arm_labels_json) = 'array'

            UNION ALL

            SELECT regexp_split_to_table(
                b.arm_labels_json #>> '{}',
                '[[:space:]]*[|][[:space:]]*'
            ) AS arm_label
            WHERE jsonb_typeof(b.arm_labels_json) = 'string'
        ) AS parsed_arm_labels
    ) AS a ON true
)
SELECT DISTINCT
    ctgov_intervention_key,
    nct_id,
    COALESCE(arm_group_label, '') AS arm_group_label,
    intervention_index,
    intervention_type,
    intervention_name,
    intervention_description,
    input_drug_name,
    input_drug_name_normalized
FROM exploded
WHERE input_drug_name_normalized IS NOT NULL
  AND input_drug_name_normalized <> '';


-- =============================================================================
-- CTGov arm regimen context.
--
-- Grain:
--   one row per nct_id x arm_group_label.
--
-- This is the authoritative CTGov-side regimen set used for TOPOGRAPH matching.
-- CTGov arm A | B | C is represented by one sorted normalized component-set key.
-- =============================================================================

CREATE OR REPLACE VIEW oncology_kb.ctgov_topograph_arm_regimen_context AS
WITH
arm_intervention_dedup AS (
    SELECT DISTINCT
        nct_id,
        arm_group_label,
        intervention_index,
        intervention_type,
        intervention_name,
        ctgov_intervention_key
    FROM oncology_kb.ctgov_topograph_arm_intervention_context
),
arm_input_dedup AS (
    SELECT DISTINCT
        nct_id,
        arm_group_label,
        input_drug_name,
        input_drug_name_normalized
    FROM oncology_kb.ctgov_topograph_arm_intervention_context
    WHERE input_drug_name_normalized IS NOT NULL
      AND input_drug_name_normalized <> ''
)
SELECT
    ai.nct_id,
    ai.arm_group_label,
    string_agg(ai.intervention_index::text, ' | ' ORDER BY ai.intervention_index) AS arm_intervention_indexes,
    string_agg(DISTINCT NULLIF(ai.intervention_type, ''), ' | ' ORDER BY NULLIF(ai.intervention_type, '')) AS arm_intervention_types,
    string_agg(DISTINCT NULLIF(ai.intervention_name, ''), ' | ' ORDER BY NULLIF(ai.intervention_name, '')) AS arm_intervention_names,
    string_agg(DISTINCT ai.ctgov_intervention_key, ' | ' ORDER BY ai.ctgov_intervention_key) AS arm_ctgov_intervention_keys,
    id.arm_input_drug_names,
    id.arm_regimen_component_count,
    id.arm_regimen_set_key
FROM arm_intervention_dedup AS ai
JOIN LATERAL (
    SELECT
        string_agg(DISTINCT NULLIF(input_drug_name, ''), ' | ' ORDER BY NULLIF(input_drug_name, '')) AS arm_input_drug_names,
        COUNT(DISTINCT input_drug_name_normalized) AS arm_regimen_component_count,
        string_agg(DISTINCT input_drug_name_normalized, ' || ' ORDER BY input_drug_name_normalized) AS arm_regimen_set_key
    FROM arm_input_dedup AS x
    WHERE x.nct_id = ai.nct_id
      AND x.arm_group_label = ai.arm_group_label
) AS id ON true
GROUP BY
    ai.nct_id,
    ai.arm_group_label,
    id.arm_input_drug_names,
    id.arm_regimen_component_count,
    id.arm_regimen_set_key;


-- =============================================================================
-- Main CTGov arm -> TOPOGRAPH link export.
--
-- Grain:
--   one row per nct_id x arm_group_label x exact matched TOPOGRAPH therapy option.
-- =============================================================================

CREATE OR REPLACE VIEW oncology_kb.ctgov_topograph_therapy_link_export AS
WITH
arm_set AS (
    SELECT *
    FROM oncology_kb.ctgov_topograph_arm_regimen_context
),
topograph_option_set AS (
    SELECT
        o.therapy_option_key,
        o.raw_assertion_key,
        o.topograph_raw_row_index,
        o.therapy_option_raw AS topograph_therapy_option,
        string_agg(c.component_raw, ' + ' ORDER BY c.component_index) AS topograph_therapy_components,
        COUNT(DISTINCT c.component_norm) AS topograph_regimen_component_count,
        string_agg(DISTINCT c.component_norm, ' || ' ORDER BY c.component_norm) AS topograph_regimen_set_key,
        o.topograph_source_version
    FROM oncology_kb.topograph_therapy_option AS o
    JOIN oncology_kb.topograph_therapy_component AS c
        ON c.topograph_therapy_option_id = o.topograph_therapy_option_id
    WHERE c.component_norm IS NOT NULL
      AND c.component_norm <> ''
    GROUP BY
        o.therapy_option_key,
        o.raw_assertion_key,
        o.topograph_raw_row_index,
        o.therapy_option_raw,
        o.topograph_source_version
),
exact_regimen_hits AS (
    SELECT
        a.nct_id,
        a.arm_group_label,
        a.arm_intervention_indexes,
        a.arm_intervention_types,
        a.arm_intervention_names,
        a.arm_input_drug_names,
        a.arm_ctgov_intervention_keys,
        o.therapy_option_key,
        o.raw_assertion_key,
        o.topograph_raw_row_index,
        o.topograph_therapy_option,
        o.topograph_therapy_components,
        o.topograph_source_version
    FROM arm_set AS a
    JOIN topograph_option_set AS o
        ON o.topograph_regimen_component_count = a.arm_regimen_component_count
       AND o.topograph_regimen_set_key = a.arm_regimen_set_key
    WHERE a.arm_regimen_component_count > 0
      AND a.arm_regimen_set_key IS NOT NULL
)
SELECT
    md5(h.nct_id || '|arm|' || h.arm_group_label || '|topograph|' || h.therapy_option_key || '|exact_arm_regimen_set_match') AS topograph_ctgov_link_key,
    h.nct_id,
    h.arm_group_label,

    h.arm_intervention_indexes,
    h.arm_intervention_types,
    h.arm_intervention_names,
    h.arm_input_drug_names,
    h.arm_ctgov_intervention_keys,

    -- Under exact arm-regimen set matching, the matched CTGov therapy is the
    -- whole arm regimen. CTGov arm A | B | C is treated as TOPOGRAPH-style
    -- A + B + C, not as separate monotherapy opportunities.
    h.arm_ctgov_intervention_keys AS matched_ctgov_intervention_keys,
    h.arm_intervention_indexes AS matched_intervention_indexes,
    h.arm_intervention_types AS matched_intervention_types,
    h.arm_intervention_names AS matched_intervention_names,
    h.arm_input_drug_names AS matched_input_drug_names,

    h.therapy_option_key,
    h.raw_assertion_key,
    h.topograph_raw_row_index,
    h.topograph_therapy_option,
    h.topograph_therapy_components,
    'exact_arm_regimen_set_match'::text AS topograph_match_scope,
    'exact_normalized_ctgov_arm_regimen_set_to_topograph_therapy_option_set'::text AS topograph_match_strategy,
    'high'::text AS topograph_match_confidence,
    h.topograph_source_version
FROM exact_regimen_hits AS h;


-- =============================================================================
-- Review-only component hits for combination therapy options.
-- Not main evidence; useful for checking e.g. Afatinib appears in Afatinib + Bevacizumab.
-- =============================================================================

CREATE OR REPLACE VIEW oncology_kb.ctgov_topograph_component_review_export AS
WITH main_links AS (
    SELECT DISTINCT
        nct_id,
        arm_group_label,
        therapy_option_key
    FROM oncology_kb.ctgov_topograph_therapy_link_export
)
SELECT
    t.ctgov_intervention_key,
    t.nct_id,
    regexp_replace(COALESCE(t.arm_group_label, ''), E'[\r\n\t]+', ' ', 'g') AS arm_group_label,
    t.intervention_index,
    regexp_replace(COALESCE(t.intervention_type, ''), E'[\r\n\t]+', ' ', 'g') AS intervention_type,
    regexp_replace(COALESCE(t.intervention_name, ''), E'[\r\n\t]+', ' ', 'g') AS intervention_name,
    regexp_replace(COALESCE(t.input_drug_name, ''), E'[\r\n\t]+', ' ', 'g') AS input_drug_name,
    t.input_drug_name_normalized,
    regexp_replace(COALESCE(c.component_raw, ''), E'[\r\n\t]+', ' ', 'g') AS matched_topograph_component,
    o.therapy_option_key,
    regexp_replace(COALESCE(o.therapy_option_raw, ''), E'[\r\n\t]+', ' ', 'g') AS topograph_combination_option,
    o.component_count,
    r.raw_assertion_key,
    r.topograph_raw_row_index,
    regexp_replace(COALESCE(r.tier, ''), E'[\r\n\t]+', ' ', 'g') AS tier,
    r.evidence_direction,
    regexp_replace(COALESCE(r.biomarker, ''), E'[\r\n\t]+', ' ', 'g') AS biomarker,
    regexp_replace(COALESCE(r.alteration, ''), E'[\r\n\t]+', ' ', 'g') AS alteration,
    regexp_replace(COALESCE(r.tumour_type, ''), E'[\r\n\t]+', ' ', 'g') AS tumour_type,
    regexp_replace(COALESCE(r.comments, ''), E'[\r\n\t]+', ' ', 'g') AS comments,
    regexp_replace(COALESCE(r.evidence, ''), E'[\r\n\t]+', ' ', 'g') AS evidence,
    CASE
        WHEN ml.therapy_option_key IS NOT NULL THEN 'combination_already_has_main_arm_link'
        ELSE 'component_only_not_main_evidence'
    END AS review_status,
    o.topograph_source_version
FROM oncology_kb.ctgov_topograph_arm_intervention_context AS t
JOIN oncology_kb.topograph_therapy_component AS c
    ON c.component_norm = t.input_drug_name_normalized
JOIN oncology_kb.topograph_therapy_option AS o
    ON o.topograph_therapy_option_id = c.topograph_therapy_option_id
JOIN oncology_kb.topograph_raw_assertion AS r
    ON r.topograph_raw_assertion_id = o.topograph_raw_assertion_id
LEFT JOIN main_links AS ml
    ON ml.nct_id = t.nct_id
   AND ml.arm_group_label = t.arm_group_label
   AND ml.therapy_option_key = o.therapy_option_key
WHERE o.component_count > 1;


-- =============================================================================
-- Human-readable main evidence review export
-- =============================================================================

CREATE OR REPLACE VIEW oncology_kb.ctgov_topograph_evidence_review_export AS
SELECT
    l.topograph_ctgov_link_key,
    regexp_replace(COALESCE(l.nct_id, ''), E'[\r\n\t]+', ' ', 'g') AS nct_id,
    regexp_replace(COALESCE(l.arm_group_label, ''), E'[\r\n\t]+', ' ', 'g') AS arm_group_label,

    regexp_replace(COALESCE(l.arm_intervention_indexes, ''), E'[\r\n\t]+', ' ', 'g') AS arm_intervention_indexes,
    regexp_replace(COALESCE(l.arm_intervention_types, ''), E'[\r\n\t]+', ' ', 'g') AS arm_intervention_types,
    regexp_replace(COALESCE(l.arm_intervention_names, ''), E'[\r\n\t]+', ' ', 'g') AS arm_intervention_names,
    regexp_replace(COALESCE(l.arm_input_drug_names, ''), E'[\r\n\t]+', ' ', 'g') AS arm_input_drug_names,
    regexp_replace(COALESCE(l.arm_ctgov_intervention_keys, ''), E'[\r\n\t]+', ' ', 'g') AS arm_ctgov_intervention_keys,

    regexp_replace(COALESCE(l.matched_ctgov_intervention_keys, ''), E'[\r\n\t]+', ' ', 'g') AS matched_ctgov_intervention_keys,
    regexp_replace(COALESCE(l.matched_intervention_indexes, ''), E'[\r\n\t]+', ' ', 'g') AS matched_intervention_indexes,
    regexp_replace(COALESCE(l.matched_intervention_types, ''), E'[\r\n\t]+', ' ', 'g') AS matched_intervention_types,
    regexp_replace(COALESCE(l.matched_intervention_names, ''), E'[\r\n\t]+', ' ', 'g') AS matched_intervention_names,
    regexp_replace(COALESCE(l.matched_input_drug_names, ''), E'[\r\n\t]+', ' ', 'g') AS matched_input_drug_names,

    l.therapy_option_key,
    regexp_replace(COALESCE(l.topograph_therapy_option, ''), E'[\r\n\t]+', ' ', 'g') AS topograph_therapy_option,
    regexp_replace(COALESCE(l.topograph_therapy_components, ''), E'[\r\n\t]+', ' ', 'g') AS topograph_therapy_components,
    l.topograph_match_scope,
    l.topograph_match_strategy,
    l.topograph_match_confidence,

    r.raw_assertion_key,
    r.topograph_raw_row_index,
    regexp_replace(COALESCE(r.tier, ''), E'[\r\n\t]+', ' ', 'g') AS topograph_tier,
    r.evidence_direction AS topograph_evidence_direction,
    regexp_replace(COALESCE(r.biomarker, ''), E'[\r\n\t]+', ' ', 'g') AS topograph_biomarker,
    regexp_replace(COALESCE(r.alteration, ''), E'[\r\n\t]+', ' ', 'g') AS topograph_alteration,
    regexp_replace(COALESCE(r.tumour_type, ''), E'[\r\n\t]+', ' ', 'g') AS topograph_tumour_type,
    regexp_replace(COALESCE(r.comments, ''), E'[\r\n\t]+', ' ', 'g') AS topograph_comments,
    regexp_replace(COALESCE(r.evidence, ''), E'[\r\n\t]+', ' ', 'g') AS topograph_evidence,
    l.topograph_source_version
FROM oncology_kb.ctgov_topograph_therapy_link_export AS l
JOIN oncology_kb.topograph_raw_assertion AS r
    ON r.raw_assertion_key = l.raw_assertion_key;


-- =============================================================================
-- Final colleague-facing CTGov trial-arm x TOPOGRAPH evidence export.
--
-- Grain:
--   one row per nct_id x arm_group_label x matched TOPOGRAPH evidence assertion.
--
-- This intentionally starts with the CTGov arm context. It does not include
-- trial-level aggregate columns or all-trial arm lists, and it does not
-- include internal provenance/debug columns.
-- =============================================================================

CREATE OR REPLACE VIEW oncology_kb.ctgov_trial_topograph_summary_export AS
WITH exact_evidence AS (
    SELECT DISTINCT
        e.nct_id,
        e.arm_group_label,
        e.arm_intervention_names,
        e.matched_intervention_names,
        e.topograph_therapy_option,
        e.topograph_tier,
        e.topograph_biomarker,
        e.topograph_alteration,
        e.topograph_tumour_type,
        e.topograph_comments,
        e.topograph_evidence
    FROM oncology_kb.ctgov_topograph_evidence_review_export AS e
)
SELECT DISTINCT
    -- Essential CTGov arm context. Every CTGov trial arm is retained even when
    -- there is no exact TOPOGRAPH regimen-set match.
    regexp_replace(COALESCE(a.nct_id, ''), E'[\r\n\t]+', ' ', 'g') AS nct_id,
    regexp_replace(COALESCE(a.arm_group_label, ''), E'[\r\n\t]+', ' ', 'g') AS arm_group_label,
    regexp_replace(COALESCE(a.arm_intervention_names, ''), E'[\r\n\t]+', ' ', 'g') AS arm_intervention_names,

    -- Present only when this full CTGov arm regimen exactly matches one
    -- TOPOGRAPH therapy option/regimen. Otherwise blank.
    regexp_replace(COALESCE(e.matched_intervention_names, ''), E'[\r\n\t]+', ' ', 'g') AS matched_intervention_names,
    regexp_replace(COALESCE(e.topograph_therapy_option, ''), E'[\r\n\t]+', ' ', 'g') AS topograph_therapy_option,

    -- Essential TOPOGRAPH biomarker / tumour / evidence assertion. Blank when
    -- the CTGov arm has no exact TOPOGRAPH regimen-set match.
    regexp_replace(COALESCE(e.topograph_tier, ''), E'[\r\n\t]+', ' ', 'g') AS topograph_tier,
    regexp_replace(COALESCE(e.topograph_biomarker, ''), E'[\r\n\t]+', ' ', 'g') AS topograph_biomarker,
    regexp_replace(COALESCE(e.topograph_alteration, ''), E'[\r\n\t]+', ' ', 'g') AS topograph_alteration,
    regexp_replace(COALESCE(e.topograph_tumour_type, ''), E'[\r\n\t]+', ' ', 'g') AS topograph_tumour_type,
    left(regexp_replace(COALESCE(e.topograph_comments, ''), E'[\r\n\t]+', ' ', 'g'), 32000) AS topograph_comments,
    left(regexp_replace(COALESCE(e.topograph_evidence, ''), E'[\r\n\t]+', ' ', 'g'), 32000) AS topograph_evidence
FROM oncology_kb.ctgov_topograph_arm_regimen_context AS a
LEFT JOIN exact_evidence AS e
    ON e.nct_id = a.nct_id
   AND e.arm_group_label = a.arm_group_label;
