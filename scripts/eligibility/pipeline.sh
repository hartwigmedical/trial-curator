#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

export PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
export PYTHONDONTWRITEBYTECODE="${PYTHONDONTWRITEBYTECODE:-1}"
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

ELIGIBILITY_LOG_LEVEL="${ELIGIBILITY_LOG_LEVEL:-INFO}"
ELIGIBILITY_OUTPUT_FORMAT="${ELIGIBILITY_OUTPUT_FORMAT:-tsv}"
ELIGIBILITY_EXPORT_DATE="${ELIGIBILITY_EXPORT_DATE:-$(date +%d%m%Y)}"
ELIGIBILITY_RUN_QA_DIFFS="${ELIGIBILITY_RUN_QA_DIFFS:-0}"
ELIGIBILITY_SKIP_TESTS="${ELIGIBILITY_SKIP_TESTS:-0}"
ELIGIBILITY_LLM_WORKERS="${ELIGIBILITY_LLM_WORKERS:-10}"
ELIGIBILITY_LLM_MAX_RETRIES="${ELIGIBILITY_LLM_MAX_RETRIES:-10}"
ELIGIBILITY_LLM_RETRY_INITIAL_DELAY="${ELIGIBILITY_LLM_RETRY_INITIAL_DELAY:-2}"
ELIGIBILITY_LLM_RETRY_MAX_DELAY="${ELIGIBILITY_LLM_RETRY_MAX_DELAY:-60}"
ELIGIBILITY_ANZCTR_TIMEOUT_MS="${ELIGIBILITY_ANZCTR_TIMEOUT_MS:-120000}"
ELIGIBILITY_ANZCTR_SEARCH_RETRIES="${ELIGIBILITY_ANZCTR_SEARCH_RETRIES:-3}"

CTGOV_INTERMEDIATE_DIR="${CTGOV_INTERMEDIATE_DIR:-data/eligibility_path/exports/intermediates/ctgov}"
ANZCTR_INTERMEDIATE_DIR="${ANZCTR_INTERMEDIATE_DIR:-data/eligibility_path/exports/intermediates/anzctr}"
ANZCTR_RXNORM_RRF_DIR="${ANZCTR_RXNORM_RRF_DIR:-data/drug_utility_path/drug_ontology/raw_inputs/RxNorm}"

cd "${REPO_ROOT}"

log_step() {
  printf '\n==> %s\n' "$*"
}

run_module() {
  local module="$1"
  shift
  "${PYTHON_BIN}" -m "${module}" "$@"
}

run_eligibility_tests() {
  if [[ "${ELIGIBILITY_SKIP_TESTS}" == "1" ]]; then
    log_step "Skipping eligibility tests"
    return 0
  fi

  log_step "Running eligibility-path unit tests"
  "${PYTHON_BIN}" -m pytest \
    tests/eligibility_path/ctgov \
    tests/eligibility_path/anzctr \
    tests/eligibility_path/shared
}

run_ctgov_trial_extraction() {
  log_step "CTGov: extracting drug-intervention trials"
  run_module aus_trial_universe.eligibility_path.ctgov.i_download_trials_and_extract_eligibility.ii_extract_fields \
    --log_level "${ELIGIBILITY_LOG_LEVEL}"
}

run_anzctr_trial_extraction() {
  log_step "ANZCTR: extracting drug-intervention trials and annotating RxNorm drugs"
  run_module aus_trial_universe.eligibility_path.anzctr.i_download_trials_and_extract_eligibility.iii_extract_drugs \
    --refresh_input_csv \
    --rxnorm_rrf_dir "${ANZCTR_RXNORM_RRF_DIR}" \
    --log_level "${ELIGIBILITY_LOG_LEVEL}"
}

run_registry_processing() {
  local registry="$1"
  local intermediate_dir="$2"

  log_step "${registry}: cancer-type trial-level processing"
  run_module "aus_trial_universe.eligibility_path.${registry}.ii_process_eligibility_criteria.cancer_types.cancer_type_pipeline" \
    --eligibility_data_dir "${intermediate_dir}" \
    --output_format "${ELIGIBILITY_OUTPUT_FORMAT}" \
    --log_level "${ELIGIBILITY_LOG_LEVEL}"

  log_step "${registry}: cancer-type cohort-level processing"
  run_module "aus_trial_universe.eligibility_path.${registry}.ii_process_eligibility_criteria.cancer_types.cohort_level_cancer_type" \
    --eligibility_data_dir "${intermediate_dir}" \
    --output_format "${ELIGIBILITY_OUTPUT_FORMAT}" \
    --log_level "${ELIGIBILITY_LOG_LEVEL}"

  log_step "${registry}: gene-alteration trial-level processing"
  run_module "aus_trial_universe.eligibility_path.${registry}.ii_process_eligibility_criteria.gene_alterations.gene_alteration_pipeline" \
    --eligibility_data_dir "${intermediate_dir}" \
    --output_format "${ELIGIBILITY_OUTPUT_FORMAT}" \
    --log_level "${ELIGIBILITY_LOG_LEVEL}"

  log_step "${registry}: gene-alteration cohort-level processing"
  run_module "aus_trial_universe.eligibility_path.${registry}.ii_process_eligibility_criteria.gene_alterations.cohort_level_gene_alteration" \
    --eligibility_data_dir "${intermediate_dir}" \
    --output_format "${ELIGIBILITY_OUTPUT_FORMAT}" \
    --log_level "${ELIGIBILITY_LOG_LEVEL}"

  log_step "${registry}: molecular-signature trial-level processing"
  run_module "aus_trial_universe.eligibility_path.${registry}.ii_process_eligibility_criteria.molecular_signature.molecular_signature_pipeline" \
    --eligibility_data_dir "${intermediate_dir}" \
    --output_format "${ELIGIBILITY_OUTPUT_FORMAT}" \
    --log_level "${ELIGIBILITY_LOG_LEVEL}"

  log_step "${registry}: molecular-signature cohort-level processing"
  run_module "aus_trial_universe.eligibility_path.${registry}.ii_process_eligibility_criteria.molecular_signature.cohort_level_molecular_signature" \
    --eligibility_data_dir "${intermediate_dir}" \
    --output_format "${ELIGIBILITY_OUTPUT_FORMAT}" \
    --log_level "${ELIGIBILITY_LOG_LEVEL}"
}

run_registry_qa() {
  local registry="$1"
  if [[ "${ELIGIBILITY_RUN_QA_DIFFS}" != "1" ]]; then
    return 0
  fi

  log_step "${registry}: QA diffs"
  run_module aus_trial_universe.eligibility_path.qa.cancer_type_output_diff \
    --registry "${registry}" \
    --log_level "${ELIGIBILITY_LOG_LEVEL}"
  run_module aus_trial_universe.eligibility_path.qa.gene_alteration_output_diff \
    --registry "${registry}" \
    --log_level "${ELIGIBILITY_LOG_LEVEL}"
  run_module aus_trial_universe.eligibility_path.qa.molecular_signature_output_diff \
    --registry "${registry}" \
    --log_level "${ELIGIBILITY_LOG_LEVEL}"
}

run_registry_exports() {
  local registry="$1"

  log_step "${registry}: trial-level resource export"
  run_module "aus_trial_universe.eligibility_path.${registry}.ii_process_eligibility_criteria.trial_resource.trial_level_resource_pipeline" \
    --export_date "${ELIGIBILITY_EXPORT_DATE}" \
    --log_level "${ELIGIBILITY_LOG_LEVEL}"

  log_step "${registry}: cohort-level resource export"
  run_module "aus_trial_universe.eligibility_path.${registry}.ii_process_eligibility_criteria.trial_resource.cohort_level_resource_pipeline" \
    --export_date "${ELIGIBILITY_EXPORT_DATE}" \
    --log_level "${ELIGIBILITY_LOG_LEVEL}"
}

run_all_trials_download() {
  local workflow_args=(
    --export_date "${ELIGIBILITY_EXPORT_DATE}"
    --python_bin "${PYTHON_BIN}"
    --output_format "${ELIGIBILITY_OUTPUT_FORMAT}"
    --rxnorm_rrf_dir "${ANZCTR_RXNORM_RRF_DIR}"
    --anzctr_timeout_ms "${ELIGIBILITY_ANZCTR_TIMEOUT_MS}"
    --anzctr_search_retries "${ELIGIBILITY_ANZCTR_SEARCH_RETRIES}"    --log_level "${ELIGIBILITY_LOG_LEVEL}"
  )
  log_step "Recursive CTGov/ANZCTR eligibility workflow with fresh trial downloads and POTTR append convergence"
  run_module aus_trial_universe.eligibility_path.shared.workflow.recursive_end_to_end_workflow \
    "${workflow_args[@]}"
}

run_all_trials_download_with_llm_review() {
  local llm_args=(
    --export_date "${ELIGIBILITY_EXPORT_DATE}"
    --python_bin "${PYTHON_BIN}"
    --output_format "${ELIGIBILITY_OUTPUT_FORMAT}"
    --rxnorm_rrf_dir "${ANZCTR_RXNORM_RRF_DIR}"
    --anzctr_timeout_ms "${ELIGIBILITY_ANZCTR_TIMEOUT_MS}"
    --anzctr_search_retries "${ELIGIBILITY_ANZCTR_SEARCH_RETRIES}"    --llm_review
    --llm_workers "${ELIGIBILITY_LLM_WORKERS}"
    --llm_max_retries "${ELIGIBILITY_LLM_MAX_RETRIES}"
    --llm_retry_initial_delay "${ELIGIBILITY_LLM_RETRY_INITIAL_DELAY}"
    --llm_retry_max_delay "${ELIGIBILITY_LLM_RETRY_MAX_DELAY}"
    --log_level "${ELIGIBILITY_LOG_LEVEL}"
  )

  if [[ -n "${ELIGIBILITY_LLM_LIMIT:-}" ]]; then
    llm_args+=(--llm_limit "${ELIGIBILITY_LLM_LIMIT}")
  fi
  if [[ -n "${ELIGIBILITY_LLM_MODEL:-}" ]]; then
    llm_args+=(--llm_model "${ELIGIBILITY_LLM_MODEL}")
  fi
  log_step "Recursive CTGov/ANZCTR eligibility workflow with fresh trial downloads, ANZCTR LLM drug review, and POTTR append convergence"
  run_module aus_trial_universe.eligibility_path.shared.workflow.recursive_end_to_end_workflow \
    "${llm_args[@]}"
}

run_ctgov() {
  run_ctgov_trial_extraction
  run_registry_processing ctgov "${CTGOV_INTERMEDIATE_DIR}"
  run_registry_qa ctgov
  run_registry_exports ctgov
}

run_anzctr() {
  run_anzctr_trial_extraction
  run_registry_processing anzctr "${ANZCTR_INTERMEDIATE_DIR}"
  run_registry_qa anzctr
  run_registry_exports anzctr
}

run_all() {
  local workflow_args=(
    --export_date "${ELIGIBILITY_EXPORT_DATE}"
    --python_bin "${PYTHON_BIN}"
    --skip_initial_downloads
    --output_format "${ELIGIBILITY_OUTPUT_FORMAT}"
    --rxnorm_rrf_dir "${ANZCTR_RXNORM_RRF_DIR}"
    --anzctr_timeout_ms "${ELIGIBILITY_ANZCTR_TIMEOUT_MS}"
    --anzctr_search_retries "${ELIGIBILITY_ANZCTR_SEARCH_RETRIES}"    --log_level "${ELIGIBILITY_LOG_LEVEL}"
  )
  log_step "Recursive CTGov/ANZCTR eligibility workflow using latest existing trial inputs"
  run_module aus_trial_universe.eligibility_path.shared.workflow.recursive_end_to_end_workflow \
    "${workflow_args[@]}"
}

run_resource_accretion() {
  log_step "Accreting filled resource fill-ready templates into new resource versions"
  run_module aus_trial_universe.eligibility_path.shared.resource_curation.audit \
    --phase accrete \
    --export_date "${ELIGIBILITY_EXPORT_DATE}" \
    --log_level "${ELIGIBILITY_LOG_LEVEL}"
}

run_resource_report() {
  log_step "Flagging hand-curated resource coverage gaps and writing dated fill-ready gap templates"
  run_module aus_trial_universe.eligibility_path.shared.resource_curation.audit \
    --phase report \
    --export_date "${ELIGIBILITY_EXPORT_DATE}" \
    --log_level "${ELIGIBILITY_LOG_LEVEL}"
}

run_resource_audit() {
  log_step "Auditing hand-curated resource coverage (accrete + report)"
  run_module aus_trial_universe.eligibility_path.shared.resource_curation.audit \
    --phase all \
    --export_date "${ELIGIBILITY_EXPORT_DATE}" \
    --log_level "${ELIGIBILITY_LOG_LEVEL}"
}

validate_command() {
  case "$1" in
    ctgov|anzctr|all-trials-download-w-llm|all-trials-download|all|resource-audit|tests)
      return 0
      ;;
    *)
      printf 'Unknown eligibility pipeline command: %s\n' "$1" >&2
      printf 'Expected one of: ctgov, anzctr, all-trials-download-w-llm, all-trials-download, all, resource-audit, tests\n' >&2
      return 2
      ;;
  esac
}

main() {
  local command="${1:-all}"

  validate_command "${command}"

  # Tee the entire run (unit tests + workflow) to a timestamped log so failures
  # deep in the pipeline can be diagnosed after the fact.
  local log_dir="${ELIGIBILITY_LOG_DIR:-${REPO_ROOT}/data/eligibility_path/logs}"
  mkdir -p "${log_dir}"
  local run_log="${log_dir}/eligibility_${command}_$(date +%Y%m%d_%H%M%S).log"
  exec > >(tee -a "${run_log}") 2>&1
  printf '==> Logging this run to %s\n' "${run_log}"

  run_eligibility_tests

  # Pre-processing: graduate filled fill-ready templates into new resource
  # versions so this run's processing uses the curator's latest fills.
  case "${command}" in
    ctgov|anzctr|all|all-trials-download|all-trials-download-w-llm)
      run_resource_accretion
      ;;
  esac

  case "${command}" in
    ctgov)
      run_ctgov
      ;;
    anzctr)
      run_anzctr
      ;;
    all-trials-download)
      run_all_trials_download
      ;;
    all-trials-download-w-llm)
      run_all_trials_download_with_llm_review
      ;;
    all)
      run_all
      ;;
    resource-audit)
      run_resource_audit
      ;;
    tests)
      ;;
  esac

  # Post-processing: flag remaining coverage gaps and refresh the fill-ready
  # templates from the freshly-generated intermediates.
  case "${command}" in
    ctgov|anzctr|all|all-trials-download|all-trials-download-w-llm)
      run_resource_report
      ;;
  esac
}

main "$@"
