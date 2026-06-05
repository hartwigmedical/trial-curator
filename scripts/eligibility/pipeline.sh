#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

export PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
PYTHON_BIN="${PYTHON_BIN:-python}"
if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  if [[ -x "${HOME}/anaconda3/bin/python" ]]; then
    PYTHON_BIN="${HOME}/anaconda3/bin/python"
  else
    PYTHON_BIN="python3"
  fi
fi

load_env_file() {
  local env_file="$1"
  if [[ -f "${env_file}" ]]; then
    set -a
    # shellcheck source=/dev/null
    source "${env_file}"
    set +a
  fi
}

load_env_file "${REPO_ROOT}/.env"
load_env_file "${REPO_ROOT}/.env.local"

ELIGIBILITY_DATA_DIR="${ELIGIBILITY_DATA_DIR:-data/ctgov/eligibility}"
ELIGIBILITY_RUN_STAMP="${ELIGIBILITY_RUN_STAMP:-$(date +%Y%m%d_%H%M%S)}"
ELIGIBILITY_DOWNLOAD_RUN_DIR="${ELIGIBILITY_DOWNLOAD_RUN_DIR:-${ELIGIBILITY_DATA_DIR}/downloads/${ELIGIBILITY_RUN_STAMP}}"
ELIGIBILITY_STATE_DIR="${ELIGIBILITY_STATE_DIR:-${ELIGIBILITY_DATA_DIR}/state}"
ELIGIBILITY_TRIALS_DIR="${ELIGIBILITY_TRIALS_DIR:-${ELIGIBILITY_DATA_DIR}/trials}"
ELIGIBILITY_CURATED_DIR="${ELIGIBILITY_CURATED_DIR:-${ELIGIBILITY_TRIALS_DIR}/original_curations}"
ELIGIBILITY_OUTPUT_FORMAT="${ELIGIBILITY_OUTPUT_FORMAT:-tsv}"
ELIGIBILITY_LOG_LEVEL="${ELIGIBILITY_LOG_LEVEL:-INFO}"
ELIGIBILITY_MAX_WORKERS="${ELIGIBILITY_MAX_WORKERS:-1}"
ELIGIBILITY_EXPORT_DATE="${ELIGIBILITY_EXPORT_DATE:-$(date +%d%m%Y)}"
ELIGIBILITY_RUN_QA_DIFFS="${ELIGIBILITY_RUN_QA_DIFFS:-0}"
ELIGIBILITY_QA_SNAPSHOT_DATE="${ELIGIBILITY_QA_SNAPSHOT_DATE:-${ELIGIBILITY_EXPORT_DATE}}"
ELIGIBILITY_CANCER_TYPE_BASELINE_DIR="${ELIGIBILITY_CANCER_TYPE_BASELINE_DIR:-${ELIGIBILITY_DATA_DIR}/processed/cancer_type/baseline}"
ELIGIBILITY_GENE_ALTERATION_BASELINE_DIR="${ELIGIBILITY_GENE_ALTERATION_BASELINE_DIR:-${ELIGIBILITY_DATA_DIR}/processed/gene_alteration/baseline}"

cd "${REPO_ROOT}"

log_step() {
  printf '\n==> %s\n' "$*"
}

run_eligibility_tests() {
  log_step "Running CTGov eligibility unit tests"
  "${PYTHON_BIN}" -m pytest tests/ctgov/eligibility_processing
}

run_module() {
  local module="$1"
  shift
  "${PYTHON_BIN}" -m "${module}" "$@"
}

run_ctgov_download_and_curator() {
  local mode="$1"
  local ctgov_flag
  local curator_input_json
  local field_extraction_input_json
  local curator_args=()

  case "${mode}" in
    new)
      ctgov_flag="--incremental"
      curator_input_json="${ELIGIBILITY_DOWNLOAD_RUN_DIR}/ctgov_trials_delta.json"
      ;;
    all)
      ctgov_flag="--all"
      curator_input_json="${ELIGIBILITY_DOWNLOAD_RUN_DIR}/ctgov_trials_merged.json"
      curator_args+=(--overwrite_existing)
      ;;
    *)
      printf 'Unknown CTGov curation mode: %s\n' "${mode}" >&2
      return 2
      ;;
  esac
  field_extraction_input_json="${ELIGIBILITY_DOWNLOAD_RUN_DIR}/ctgov_trials_merged.json"

  mkdir -p "${ELIGIBILITY_DOWNLOAD_RUN_DIR}" "${ELIGIBILITY_STATE_DIR}" "${ELIGIBILITY_TRIALS_DIR}" "${ELIGIBILITY_CURATED_DIR}"

  log_step "Downloading CTGov trials (${mode})"
  run_module aus_trial_universe.ctgov.eligibility.i_download_trials_and_extract_eligibility.i_api_download \
    "${ctgov_flag}" \
    --output_dir "${ELIGIBILITY_DOWNLOAD_RUN_DIR}" \
    --state_dir "${ELIGIBILITY_STATE_DIR}" \
    --log_level "${ELIGIBILITY_LOG_LEVEL}"

  log_step "Extracting drug-trial field table for curator filtering"
  run_module aus_trial_universe.ctgov.eligibility.i_download_trials_and_extract_eligibility.ii_extract_fields \
    --ctgov_filepath "${field_extraction_input_json}" \
    --output_dir "${ELIGIBILITY_TRIALS_DIR}" \
    --log_level "${ELIGIBILITY_LOG_LEVEL}"

  log_step "Running eligibility LLM curator (${mode})"
  if [[ -n "${ELIGIBILITY_CURATOR_LIMIT:-}" ]]; then
    curator_args+=(--limit "${ELIGIBILITY_CURATOR_LIMIT}")
  fi
  if [[ -n "${ELIGIBILITY_TRIAL_ID:-}" ]]; then
    curator_args+=(--trial_id "${ELIGIBILITY_TRIAL_ID}")
  fi

  run_module aus_trial_universe.ctgov.eligibility.i_download_trials_and_extract_eligibility.iii_pydantic_curator_batch_run \
    --input_json "${curator_input_json}" \
    --selected_trial_csv "${ELIGIBILITY_TRIALS_DIR}/ctgov_field_extractions.csv" \
    --output_dir "${ELIGIBILITY_CURATED_DIR}" \
    --max_workers "${ELIGIBILITY_MAX_WORKERS}" \
    --log_level "${ELIGIBILITY_LOG_LEVEL}" \
    "${curator_args[@]}"
}

run_cancer_type() {
  log_step "Running eligibility cancer-type pipeline"
  run_module aus_trial_universe.ctgov.eligibility.ii_process_eligibility_criteria.cancer_types.cancer_type_pipeline \
    --eligibility_data_dir "${ELIGIBILITY_DATA_DIR}" \
    --output_format "${ELIGIBILITY_OUTPUT_FORMAT}" \
    --log_level "${ELIGIBILITY_LOG_LEVEL}"

  log_step "Running eligibility cohort-level cancer-type pipeline"
  run_module aus_trial_universe.ctgov.eligibility.ii_process_eligibility_criteria.cancer_types.cohort_level_cancer_type \
    --eligibility_data_dir "${ELIGIBILITY_DATA_DIR}" \
    --output_format "${ELIGIBILITY_OUTPUT_FORMAT}" \
    --log_level "${ELIGIBILITY_LOG_LEVEL}"
}

run_gene_alteration() {
  log_step "Running eligibility gene-alteration pipeline"
  run_module aus_trial_universe.ctgov.eligibility.ii_process_eligibility_criteria.gene_alterations.gene_alteration_pipeline \
    --eligibility_data_dir "${ELIGIBILITY_DATA_DIR}" \
    --output_format "${ELIGIBILITY_OUTPUT_FORMAT}" \
    --log_level "${ELIGIBILITY_LOG_LEVEL}"

  log_step "Running eligibility cohort-level gene-alteration pipeline"
  run_module aus_trial_universe.ctgov.eligibility.ii_process_eligibility_criteria.gene_alterations.cohort_level_gene_alteration \
    --eligibility_data_dir "${ELIGIBILITY_DATA_DIR}" \
    --output_format "${ELIGIBILITY_OUTPUT_FORMAT}" \
    --log_level "${ELIGIBILITY_LOG_LEVEL}"
}

run_molecular_signature() {
  log_step "Running eligibility molecular-signature pipeline"
  run_module aus_trial_universe.ctgov.eligibility.ii_process_eligibility_criteria.molecular_signature.molecular_signature_pipeline \
    --eligibility_data_dir "${ELIGIBILITY_DATA_DIR}" \
    --output_format "${ELIGIBILITY_OUTPUT_FORMAT}" \
    --log_level "${ELIGIBILITY_LOG_LEVEL}"

  log_step "Running eligibility cohort-level molecular-signature pipeline"
  run_module aus_trial_universe.ctgov.eligibility.ii_process_eligibility_criteria.molecular_signature.cohort_level_molecular_signature \
    --eligibility_data_dir "${ELIGIBILITY_DATA_DIR}" \
    --output_format "${ELIGIBILITY_OUTPUT_FORMAT}" \
    --log_level "${ELIGIBILITY_LOG_LEVEL}"
}

run_optional_qa_diffs() {
  if [[ "${ELIGIBILITY_RUN_QA_DIFFS}" != "1" ]]; then
    return 0
  fi

  log_step "Running eligibility cancer-type QA diff"
  run_module aus_trial_universe.ctgov.eligibility.ii_process_eligibility_criteria.cancer_types.qa.cancer_type_output_diff \
    --baseline_dir "${ELIGIBILITY_CANCER_TYPE_BASELINE_DIR}" \
    --snapshot_date "${ELIGIBILITY_QA_SNAPSHOT_DATE}" \
    --log_level "${ELIGIBILITY_LOG_LEVEL}"

  log_step "Running eligibility gene-alteration QA diff"
  run_module aus_trial_universe.ctgov.eligibility.ii_process_eligibility_criteria.gene_alterations.qa.gene_alteration_output_diff \
    --baseline_dir "${ELIGIBILITY_GENE_ALTERATION_BASELINE_DIR}" \
    --snapshot_date "${ELIGIBILITY_QA_SNAPSHOT_DATE}" \
    --log_level "${ELIGIBILITY_LOG_LEVEL}"
}

run_resource_exports() {
  log_step "Running eligibility trial-resource export"
  run_module aus_trial_universe.ctgov.eligibility.ii_process_eligibility_criteria.trial_resource.trial_level_resource_pipeline \
    --eligibility_data_dir "${ELIGIBILITY_DATA_DIR}" \
    --export_date "${ELIGIBILITY_EXPORT_DATE}" \
    --log_level "${ELIGIBILITY_LOG_LEVEL}"

  log_step "Running eligibility cohort-resource export"
  run_module aus_trial_universe.ctgov.eligibility.ii_process_eligibility_criteria.trial_resource.cohort_level_resource_pipeline \
    --eligibility_data_dir "${ELIGIBILITY_DATA_DIR}" \
    --export_date "${ELIGIBILITY_EXPORT_DATE}" \
    --log_level "${ELIGIBILITY_LOG_LEVEL}"
}

run_extract_criteria() {
  run_cancer_type
  run_gene_alteration
  run_molecular_signature
  run_optional_qa_diffs
  run_resource_exports
}

validate_command() {
  case "$1" in
    curate-new|curate-all|extract-criteria|tests)
      return 0
      ;;
    *)
      printf 'Unknown eligibility pipeline command: %s\n' "$1" >&2
      printf 'Expected one of: curate-new, curate-all, extract-criteria, tests\n' >&2
      return 2
      ;;
  esac
}

main() {
  local command="${1:-extract-criteria}"

  validate_command "${command}"
  run_eligibility_tests

  case "${command}" in
    curate-new)
      run_ctgov_download_and_curator new
      ;;
    curate-all)
      run_ctgov_download_and_curator all
      ;;
    extract-criteria)
      run_extract_criteria
      ;;
    tests)
      ;;
  esac
}

main "$@"
