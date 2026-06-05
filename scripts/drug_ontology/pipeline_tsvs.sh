#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

run_drug_ontology_tests
ensure_pipeline_output_dir
preflight_core_inputs
build_processed_inputs
preflight_generated_processed_inputs

if [[ "${DRUG_ONTOLOGY_START_POSTGRES:-1}" != "0" ]]; then
  start_postgres
fi

log_step "Resetting drug ontology database schemas"
psql_sql "DROP SCHEMA IF EXISTS ctgov, drug_identity, drug_classification, curation CASCADE;"

log_step "Applying base schemas"
psql_file "001_create_schemas.sql"

log_step "Applying CTGov intervention staging schema"
psql_file "002_ctgov_interventions_staging.sql"

log_step "Loading CTGov interventions"
"${PYTHON_BIN}" -m aus_trial_universe.ctgov.drug_ontology.ctgov.load_interventions \
  --input_json "${CTGOV_INPUT_JSON}" \
  --source_version "${CTGOV_SOURCE_VERSION}"

log_step "Applying RxNorm identity schema"
psql_file "003_ctgov_drug_terms_rxnorm.sql"

log_step "Resolving CTGov drug terms to RxNorm"
"${PYTHON_BIN}" -m aus_trial_universe.ctgov.drug_ontology.identity.rxnorm.load_mapping \
  --rxnorm_rrf_dir "${RXNORM_RRF_DIR}" \
  --rxnorm_source_version "${RXNORM_SOURCE_VERSION}"

log_step "Applying ATC classification schema"
psql_file "004_atc_classification_schema.sql"

log_step "Loading ATC mappings"
"${PYTHON_BIN}" -m aus_trial_universe.ctgov.drug_ontology.sources.atc.load \
  --rxnorm_rrf_dir "${RXNORM_RRF_DIR}" \
  --atc_tree_tsv "${ATC_TREE_TSV}" \
  --atc_source_version "${ATC_SOURCE_VERSION}"

log_step "Applying FDA classification schema"
psql_file "005_fda_classification_schema.sql"

log_step "Loading FDA mappings"
"${PYTHON_BIN}" -m aus_trial_universe.ctgov.drug_ontology.sources.fda.load \
  --fda_raw_dir "${FDA_RAW_DIR}" \
  --rxnorm_rrf_dir "${RXNORM_RRF_DIR}" \
  --fda_source_version "${FDA_SOURCE_VERSION}"

log_step "Applying POTTR classification schema"
psql_file "006_pottr_classification_schema.sql"

log_step "Loading POTTR mappings"
"${PYTHON_BIN}" -m aus_trial_universe.ctgov.drug_ontology.sources.pottr.load \
  --pottr_raw_dir "${POTTR_RAW_DIR}" \
  --rxnorm_rrf_dir "${RXNORM_RRF_DIR}" \
  --pottr_source_version "${POTTR_SOURCE_VERSION}"

log_step "Applying ChEMBL classification schema"
psql_file "007_chembl_classification_schema.sql"

log_step "Loading ChEMBL mappings"
"${PYTHON_BIN}" -m aus_trial_universe.ctgov.drug_ontology.sources.chembl.load \
  --chembl_sqlite_path "${CHEMBL_SQLITE_PATH}" \
  --rxnorm_rrf_dir "${RXNORM_RRF_DIR}" \
  --chembl_source_version "${CHEMBL_SOURCE_VERSION}"

log_step "Exporting input drug to RxNorm mapping"
export_input_drugs_rxnorm_mapping

log_step "Applying final intervention-level SQL view"
psql_file "008_final_intervention_drug_classification.sql"

export_query_tsv \
  "${CTGOV_DRUG_INTERVENTION_CLASSIFICATION_EXPORT}" \
  "SELECT *
   FROM drug_classification.final_intervention_drug_classification_export
   ORDER BY nct_id, intervention_index"

log_step "Applying final trial-level SQL view"
psql_file "009_final_trial_drug_classification_summary.sql"

export_query_tsv \
  "${CTGOV_TRIAL_DRUG_CLASSIFICATION_EXPORT}" \
  "SELECT *
   FROM drug_classification.final_trial_drug_classification_summary_export
   ORDER BY nct_id"

archive_pipeline_generated_files

log_step "Full drug ontology pipeline complete"
