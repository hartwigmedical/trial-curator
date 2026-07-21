#!/usr/bin/env bash
#
# Driver for the v2 agentic pipeline (aus_trial_universe/agentic).
# Sets PYTHONPATH, picks a Python that has the v2 deps, loads .env, dispatches.
#
# Subcommands:
#   run     Full pipeline: Stage I extract -> Stage II map (OncoTree). Runs the unit
#           tests first (aborts on failure) and tees a combined timestamped log.
#           Vars: ID=<id> (one) | IDS=<a,b,c> (a set) | none = ALL trials
#                 optional: MODEL=<name>  NO_JUDGE=1  NO_REVIEW=1  EXTRACT_ONLY=1
#   tests   Run the agentic unit-test suite (no API calls).
#   validate  Independent output validator ("review of the reviewer agents"): re-checks a finished
#             output TSV outside the workflow. Var: OUT=<tsv> (default: newest). No API calls.
#   drug-ref-build  Build/refresh the drug reference resource (spec §6.1). Incremental (existing drugs
#             reused). Vars: DRUGS="a; b" | IDS=NCT1,NCT2 | ALL_TRIALS=1 ; optional LIMIT, REFRESH_DRUGS=1, NO_REVIEW=1, MODEL.
#   clean   Delete transient run artifacts under data/agentic/ (eligibility/, log/, cache/).
#           No API/Python needed; handy for clearing test runs.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
AGENTIC_DATA="${REPO_ROOT}/data/agentic"
LOG_DIR="${AGENTIC_DATA}/log"

# `clean` needs neither Python nor .env; handle it before the interpreter pick.
# Scoped STRICTLY to transient run artifacts (eligibility/, log/, cache/) — it must NEVER touch the colocated
# INPUTS/OUTPUTS (trial_universe/, resources/, drug_annotations/, analysis/) now living under data/agentic/.
if [[ "${1:-}" == "clean" ]]; then
  for sub in eligibility log cache; do
    dir="${AGENTIC_DATA}/${sub}"
    if [[ -d "${dir}" ]]; then
      echo "removing ${dir}" >&2
      rm -rf "${dir}"
    fi
  done
  mkdir -p "${AGENTIC_DATA}/eligibility" "${AGENTIC_DATA}/log"
  echo "cleaned: data/agentic/{eligibility,log,cache}" >&2
  exit 0
fi

export PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
export PYTHONDONTWRITEBYTECODE="${PYTHONDONTWRITEBYTECODE:-1}"

# Pick the first Python that actually has the v2 deps (openai + pydantic), so
# `make` works whether or not the trial_curator conda env is activated.
pick_python() {
  local cand
  for cand in "${PYTHON_BIN:-}" python "${HOME}/anaconda3/envs/trial_curator/bin/python" \
              /opt/anaconda3/envs/trial_curator/bin/python python3; do
    [[ -n "${cand}" ]] || continue
    if command -v "${cand}" >/dev/null 2>&1 && "${cand}" -c "import openai, pydantic" >/dev/null 2>&1; then
      echo "${cand}"
      return 0
    fi
  done
  return 1
}

if ! PYTHON_BIN="$(pick_python)"; then
  echo "ERROR: no Python with openai+pydantic found. Activate the 'trial_curator' conda env or set PYTHON_BIN." >&2
  exit 1
fi
echo "using python: ${PYTHON_BIN}" >&2

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

cd "${REPO_ROOT}"

PIPELINE_MODULE="aus_trial_universe.agentic.run"
CMD="${1:-}"
shift || true

case "${CMD}" in
  run)
    # (a) Always run the unit tests first; set -e aborts the run if they fail.
    printf '\n==> agentic unit tests (preflight)\n' >&2
    "${PYTHON_BIN}" -m pytest tests/agentic -q

    # Modes: ID=<id> (one) | IDS=<a,b,c> (a set) | neither = ALL trials.
    args=()
    if [[ -n "${ID:-}" ]]; then
      args=(--id "${ID}"); label="${ID}"
    elif [[ -n "${IDS:-}" ]]; then
      args=(--ids "${IDS}"); label="ids"
    else
      args=(); label="all"
    fi
    if [[ -n "${MODEL:-}" ]]; then args+=(--model "${MODEL}"); fi
    if [[ -n "${NO_JUDGE:-}" ]]; then args+=(--no-judge); fi
    if [[ -n "${NO_REVIEW:-}" ]]; then args+=(--no-review); fi
    if [[ -n "${EXTRACT_ONLY:-}" ]]; then args+=(--extract-only); fi

    # (b) One process (extract -> map -> drug), streamed to a timestamped output dir; teed to one log.
    mkdir -p "${LOG_DIR}"
    log_file="${LOG_DIR}/agentic_run_${label}_$(date +%Y%m%d_%H%M%S).log"
    printf '\n==> run (logging to %s)\n' "${log_file}" >&2
    "${PYTHON_BIN}" -m "${PIPELINE_MODULE}" "${args[@]}" 2>&1 | tee "${log_file}"
    ;;
  tests)
    exec "${PYTHON_BIN}" -m pytest tests/agentic -q
    ;;
  validate)
    # Independent output validator (review of the reviewer agents). OUT=<tsv> or newest.
    vargs=()
    if [[ -n "${OUT:-}" ]]; then vargs=("${OUT}"); fi
    exec "${PYTHON_BIN}" -m aus_trial_universe.agentic.tasks.eligibility.qa.validate_output "${vargs[@]}"
    ;;
  drug-ref-build)
    # Standalone drug-reference builder (spec §6.1). Incremental: existing drugs are reused (lookup).
    #   Vars: DRUGS="a; b; c" | IDS=NCT1,NCT2 | ALL_TRIALS=1 ; optional LIMIT, REFRESH_DRUGS=1, NO_REVIEW=1, MODEL=<name>
    dargs=()
    if [[ -n "${DRUGS:-}" ]]; then dargs=(--drugs "${DRUGS}")
    elif [[ -n "${IDS:-}" ]]; then dargs=(--from-trials "${IDS}")
    elif [[ -n "${ALL_TRIALS:-}" ]]; then dargs=(--all-trials)
    else echo "drug-ref-build needs DRUGS=\"a; b\" | IDS=NCT1,NCT2 | ALL_TRIALS=1" >&2; exit 2; fi
    if [[ -n "${LIMIT:-}" ]]; then dargs+=(--limit "${LIMIT}"); fi
    if [[ -n "${WORKERS:-}" ]]; then dargs+=(--workers "${WORKERS}"); fi
    if [[ -n "${REFRESH_DRUGS:-}" ]]; then dargs+=(--refresh-drugs); fi
    if [[ -n "${NO_REVIEW:-}" ]]; then dargs+=(--no-review); fi
    if [[ -n "${MODEL:-}" ]]; then dargs+=(--model "${MODEL}"); fi
    mkdir -p "${LOG_DIR}"
    log_file="${LOG_DIR}/drug_ref_build_$(date +%Y%m%d_%H%M%S).log"
    printf '\n==> drug-ref-build (logging to %s)\n' "${log_file}" >&2
    "${PYTHON_BIN}" -m aus_trial_universe.agentic.tasks.drug_utility.build "${dargs[@]}" 2>&1 | tee "${log_file}"
    ;;
  drug-ref-refresh-pottr)
    # Download the current POTTR files from GitHub -> resources/drug_utility/pottr/current_version/ (archives old).
    exec "${PYTHON_BIN}" -m aus_trial_universe.agentic.tasks.drug_utility.pottr
    ;;
  *)
    echo "Unknown command: '${CMD}'. Use: run | tests | validate | drug-ref-build | drug-ref-refresh-pottr" >&2
    exit 2
    ;;
esac
