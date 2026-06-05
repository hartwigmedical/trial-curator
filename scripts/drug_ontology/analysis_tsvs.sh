#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

run_drug_ontology_tests
preflight_analysis_inputs
build_analysis_processed_inputs
preflight_analysis_processed_inputs

log_step "Building analysis TSV: CTGov -> POTTR drug mapping"
"${PYTHON_BIN}" -m aus_trial_universe.ctgov.drug_ontology.analysis.pottr.ctgov_crosswalk \
  --ctgov-input-json "${CTGOV_INPUT_JSON}" \
  --pottr-raw-dir "${POTTR_RAW_DIR}" \
  --rxnorm-rrf-dir "${RXNORM_RRF_DIR}" \
  --atc-tree-tsv "${ANALYSIS_ATC_TREE_TSV}" \
  --output-tsv "${ANALYSIS_CTGOV_POTTR_CROSSWALK_TSV}" \
  --no-summary

log_step "Building analysis TSV: CTGov -> Topograph drug mapping"
"${PYTHON_BIN}" -m aus_trial_universe.ctgov.drug_ontology.analysis.topograph.ctgov_drug_crosswalk \
  --ctgov-annotated-tsv "${ANALYSIS_CTGOV_ATC_ANNOTATED_TSV}" \
  --topograph-annotated-tsv "${ANALYSIS_TOPOGRAPH_ATC_ANNOTATED_TSV}" \
  --output-tsv "${ANALYSIS_CTGOV_TOPOGRAPH_CROSSWALK_TSV}"

log_step "Drug ontology analysis TSVs complete"
