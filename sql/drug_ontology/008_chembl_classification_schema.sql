-- ChEMBL molecule-level evidence schema.
-- Materialized layer is ChEMBL-anchor-level, not CTGov alias/source-field-level.

CREATE SCHEMA IF NOT EXISTS drug_classification;

DROP VIEW IF EXISTS drug_classification.ctgov_drug_term_chembl_evidence_view;
DROP TABLE IF EXISTS drug_classification.rxnorm_chembl_anchor_mapping CASCADE;
DROP TABLE IF EXISTS drug_classification.ctgov_chembl_anchor_drug_term CASCADE;
DROP TABLE IF EXISTS drug_classification.ctgov_chembl_anchor CASCADE;
DROP TABLE IF EXISTS drug_classification.chembl_molecule_warning CASCADE;
DROP TABLE IF EXISTS drug_classification.chembl_molecule_atc CASCADE;
DROP TABLE IF EXISTS drug_classification.chembl_molecule_indication CASCADE;
DROP TABLE IF EXISTS drug_classification.chembl_molecule_mechanism CASCADE;
DROP TABLE IF EXISTS drug_classification.chembl_molecule_relation CASCADE;
DROP TABLE IF EXISTS drug_classification.chembl_molecule_alias CASCADE;
DROP TABLE IF EXISTS drug_classification.chembl_molecule CASCADE;

CREATE TABLE drug_classification.chembl_molecule (
    chembl_molecule_id BIGSERIAL PRIMARY KEY,
    load_batch_id UUID NOT NULL,
    molregno BIGINT NOT NULL,
    chembl_id TEXT NOT NULL,
    pref_name TEXT,
    max_phase TEXT,
    therapeutic_flag TEXT,
    dosed_ingredient TEXT,
    structure_type TEXT,
    molecule_type TEXT,
    first_approval TEXT,
    oral TEXT,
    parenteral TEXT,
    topical TEXT,
    black_box_warning TEXT,
    first_in_class TEXT,
    prodrug TEXT,
    withdrawn_flag TEXT,
    chemical_probe TEXT,
    orphan TEXT,
    standard_inchi_key TEXT,
    canonical_smiles TEXT,
    full_mwt TEXT,
    full_molformula TEXT,
    chembl_source_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (chembl_source_version, molregno),
    UNIQUE (chembl_source_version, chembl_id)
);

CREATE TABLE drug_classification.chembl_molecule_alias (
    chembl_molecule_alias_id BIGSERIAL PRIMARY KEY,
    chembl_molecule_id BIGINT NOT NULL REFERENCES drug_classification.chembl_molecule(chembl_molecule_id) ON DELETE CASCADE,
    load_batch_id UUID NOT NULL,
    molregno BIGINT NOT NULL,
    synonym TEXT NOT NULL,
    synonym_normalized TEXT NOT NULL,
    syn_type TEXT,
    chembl_source_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (chembl_source_version, molregno, synonym, syn_type)
);

CREATE TABLE drug_classification.chembl_molecule_relation (
    chembl_molecule_relation_id BIGSERIAL PRIMARY KEY,
    chembl_molecule_id BIGINT NOT NULL REFERENCES drug_classification.chembl_molecule(chembl_molecule_id) ON DELETE CASCADE,
    related_chembl_molecule_id BIGINT NOT NULL REFERENCES drug_classification.chembl_molecule(chembl_molecule_id) ON DELETE CASCADE,
    load_batch_id UUID NOT NULL,
    molregno BIGINT NOT NULL,
    related_molregno BIGINT NOT NULL,
    relation_type TEXT NOT NULL,
    chembl_source_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (chembl_source_version, molregno, related_molregno, relation_type)
);

CREATE TABLE drug_classification.chembl_molecule_mechanism (
    chembl_molecule_mechanism_id BIGSERIAL PRIMARY KEY,
    chembl_molecule_id BIGINT NOT NULL REFERENCES drug_classification.chembl_molecule(chembl_molecule_id) ON DELETE CASCADE,
    load_batch_id UUID NOT NULL,
    molregno BIGINT NOT NULL,
    mec_id TEXT,
    record_id TEXT,
    mechanism_of_action TEXT,
    action_type TEXT,
    direct_interaction TEXT,
    molecular_mechanism TEXT,
    disease_efficacy TEXT,
    mechanism_comment TEXT,
    selectivity_comment TEXT,
    binding_site_comment TEXT,
    target_chembl_id TEXT,
    target_pref_name TEXT,
    target_type TEXT,
    target_organism TEXT,
    target_accessions TEXT,
    target_component_descriptions TEXT,
    chembl_source_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (chembl_source_version, molregno, mec_id)
);

CREATE TABLE drug_classification.chembl_molecule_indication (
    chembl_molecule_indication_id BIGSERIAL PRIMARY KEY,
    chembl_molecule_id BIGINT NOT NULL REFERENCES drug_classification.chembl_molecule(chembl_molecule_id) ON DELETE CASCADE,
    load_batch_id UUID NOT NULL,
    molregno BIGINT NOT NULL,
    drugind_id TEXT,
    record_id TEXT,
    max_phase_for_ind TEXT,
    mesh_id TEXT,
    mesh_heading TEXT,
    efo_id TEXT,
    efo_term TEXT,
    is_oncology BOOLEAN NOT NULL DEFAULT false,
    chembl_source_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (chembl_source_version, molregno, drugind_id)
);

CREATE TABLE drug_classification.chembl_molecule_atc (
    chembl_molecule_atc_id BIGSERIAL PRIMARY KEY,
    chembl_molecule_id BIGINT NOT NULL REFERENCES drug_classification.chembl_molecule(chembl_molecule_id) ON DELETE CASCADE,
    load_batch_id UUID NOT NULL,
    molregno BIGINT NOT NULL,
    mol_atc_id TEXT,
    level5 TEXT,
    who_name TEXT,
    level1 TEXT,
    level2 TEXT,
    level3 TEXT,
    level4 TEXT,
    level1_description TEXT,
    level2_description TEXT,
    level3_description TEXT,
    level4_description TEXT,
    chembl_source_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (chembl_source_version, molregno, mol_atc_id)
);

CREATE TABLE drug_classification.chembl_molecule_warning (
    chembl_molecule_warning_id BIGSERIAL PRIMARY KEY,
    chembl_molecule_id BIGINT NOT NULL REFERENCES drug_classification.chembl_molecule(chembl_molecule_id) ON DELETE CASCADE,
    load_batch_id UUID NOT NULL,
    molregno BIGINT NOT NULL,
    warning_id TEXT,
    record_id TEXT,
    warning_type TEXT,
    warning_class TEXT,
    warning_description TEXT,
    warning_country TEXT,
    warning_year TEXT,
    efo_term TEXT,
    efo_id TEXT,
    efo_id_for_warning_class TEXT,
    chembl_source_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (chembl_source_version, molregno, warning_id)
);

CREATE TABLE drug_classification.ctgov_chembl_anchor (
    ctgov_chembl_anchor_id BIGSERIAL PRIMARY KEY,
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
    chembl_source_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (rxnorm_source_version, chembl_source_version, anchor_key)
);

CREATE TABLE drug_classification.ctgov_chembl_anchor_drug_term (
    ctgov_chembl_anchor_drug_term_id BIGSERIAL PRIMARY KEY,
    ctgov_chembl_anchor_id BIGINT NOT NULL REFERENCES drug_classification.ctgov_chembl_anchor(ctgov_chembl_anchor_id) ON DELETE CASCADE,
    ctgov_drug_term_id BIGINT NOT NULL REFERENCES drug_identity.ctgov_drug_term(ctgov_drug_term_id) ON DELETE CASCADE,
    input_drug_name TEXT NOT NULL,
    source_field TEXT,
    term_kind TEXT,
    anchor_selection_strategy TEXT NOT NULL,
    rxnorm_source_version TEXT NOT NULL,
    chembl_source_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (rxnorm_source_version, chembl_source_version, ctgov_drug_term_id)
);

CREATE TABLE drug_classification.rxnorm_chembl_anchor_mapping (
    rxnorm_chembl_anchor_mapping_id BIGSERIAL PRIMARY KEY,
    ctgov_chembl_anchor_id BIGINT NOT NULL REFERENCES drug_classification.ctgov_chembl_anchor(ctgov_chembl_anchor_id) ON DELETE CASCADE,
    chembl_molecule_id BIGINT REFERENCES drug_classification.chembl_molecule(chembl_molecule_id) ON DELETE CASCADE,
    load_batch_id UUID NOT NULL,
    link_status TEXT NOT NULL,
    match_strategy TEXT NOT NULL,
    molecule_relation_type TEXT,
    matched_name TEXT,
    matched_name_type TEXT,
    resolution_payload JSONB,
    rxnorm_source_version TEXT NOT NULL,
    chembl_source_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX rxnorm_chembl_anchor_mapping_uniq
ON drug_classification.rxnorm_chembl_anchor_mapping (
    rxnorm_source_version,
    chembl_source_version,
    ctgov_chembl_anchor_id,
    chembl_molecule_id,
    molecule_relation_type
)
WHERE chembl_molecule_id IS NOT NULL;

CREATE UNIQUE INDEX rxnorm_chembl_anchor_mapping_no_match_uniq
ON drug_classification.rxnorm_chembl_anchor_mapping (
    rxnorm_source_version,
    chembl_source_version,
    ctgov_chembl_anchor_id
)
WHERE chembl_molecule_id IS NULL;

CREATE INDEX chembl_molecule_chembl_id_idx
ON drug_classification.chembl_molecule (chembl_source_version, chembl_id);

CREATE INDEX chembl_molecule_molregno_idx
ON drug_classification.chembl_molecule (chembl_source_version, molregno);

CREATE INDEX chembl_alias_norm_idx
ON drug_classification.chembl_molecule_alias (chembl_source_version, synonym_normalized);

CREATE INDEX ctgov_chembl_anchor_key_idx
ON drug_classification.ctgov_chembl_anchor (rxnorm_source_version, chembl_source_version, anchor_key);

CREATE INDEX rxnorm_chembl_anchor_mapping_anchor_idx
ON drug_classification.rxnorm_chembl_anchor_mapping (rxnorm_source_version, chembl_source_version, ctgov_chembl_anchor_id);

CREATE INDEX chembl_indication_phase_idx
ON drug_classification.chembl_molecule_indication (chembl_source_version, is_oncology, max_phase_for_ind);

CREATE OR REPLACE VIEW drug_classification.ctgov_drug_term_chembl_evidence_view AS
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
    m.molecule_relation_type,
    cm.molregno,
    cm.chembl_id,
    cm.pref_name AS chembl_pref_name,
    cm.max_phase AS chembl_max_phase,
    cm.molecule_type,
    cm.first_approval,
    cm.therapeutic_flag,
    cm.black_box_warning,
    cm.withdrawn_flag,
    a.rxnorm_source_version,
    a.chembl_source_version
FROM drug_classification.ctgov_chembl_anchor_drug_term adt
JOIN drug_classification.ctgov_chembl_anchor a
  ON a.ctgov_chembl_anchor_id = adt.ctgov_chembl_anchor_id
LEFT JOIN drug_classification.rxnorm_chembl_anchor_mapping m
  ON m.ctgov_chembl_anchor_id = a.ctgov_chembl_anchor_id
LEFT JOIN drug_classification.chembl_molecule cm
  ON cm.chembl_molecule_id = m.chembl_molecule_id;
