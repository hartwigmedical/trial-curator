ALTER TABLE drug_identity.ctgov_drug_term_rxnorm_mapping
    ADD COLUMN IF NOT EXISTS rxnorm_ingredient_rxcui TEXT,
    ADD COLUMN IF NOT EXISTS rxnorm_ingredient_name TEXT,
    ADD COLUMN IF NOT EXISTS rxnorm_ingredient_term_type TEXT,
    ADD COLUMN IF NOT EXISTS rxnorm_ingredient_resolution_stage TEXT,
    ADD COLUMN IF NOT EXISTS rxnorm_ingredient_path TEXT;

CREATE INDEX IF NOT EXISTS idx_ctgov_drug_term_rxnorm_ingredient_rxcui
    ON drug_identity.ctgov_drug_term_rxnorm_mapping(rxnorm_ingredient_rxcui);