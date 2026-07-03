#!/usr/bin/env bash
#
# Driver for the v2 agentic pipeline (aus_trial_universe/agentic).
# Mirrors scripts/eligibility/pipeline.sh: sets PYTHONPATH, picks a Python that
# has the v2 deps, loads .env, then dispatches a subcommand.
#
# Subcommands:
#   eligibility-extract   Extract a DNF eligibility table. Runs the unit tests first
#                         (aborts on failure) and tees output to a timestamped log.
#                         Vars: ID=<id> [SOURCE=ctgov|anzctr] | SELECTED=<N>  [MODEL=<name>] [NO_JUDGE=1]
#   tests                 Run the agentic unit-test suite (no API calls).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
LOG_DIR="${REPO_ROOT}/data/agentic/logs"

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

RUN_MODULE="aus_trial_universe.agentic.tasks.eligibility_extraction.run"
CMD="${1:-}"
shift || true

case "${CMD}" in
  eligibility-extract)
    # (a) Always run the unit tests first; set -e aborts the run if they fail.
    printf '\n==> agentic unit tests (preflight)\n' >&2
    "${PYTHON_BIN}" -m pytest tests/agentic -q

    SOURCE="${SOURCE:-ctgov}"
    if [[ -n "${ID:-}" ]]; then
      set -- --source "${SOURCE}" --id "${ID}"
      label="${SOURCE}_${ID}"
    elif [[ -n "${SELECTED:-}" ]]; then
      set -- --selected "${SELECTED}"
      label="selected${SELECTED}"
    else
      echo "Usage: make agentic-eligibility-extract ID=NCT06881784              (ctgov, default)" >&2
      echo "       make agentic-eligibility-extract ID=ACTRN12625... SOURCE=anzctr" >&2
      echo "       make agentic-eligibility-extract SELECTED=6                  (3 ctgov + 3 anzctr)" >&2
      echo "       optional: MODEL=<name> NO_JUDGE=1" >&2
      exit 2
    fi
    if [[ -n "${MODEL:-}" ]]; then set -- "$@" --model "${MODEL}"; fi
    if [[ -n "${NO_JUDGE:-}" ]]; then set -- "$@" --no-judge; fi

    # (b) Run the extraction, teeing all output to a timestamped log.
    mkdir -p "${LOG_DIR}"
    log_file="${LOG_DIR}/agentic_eligibility_${label}_$(date +%Y%m%d_%H%M%S).log"
    printf '\n==> extracting (logging to %s)\n' "${log_file}" >&2
    "${PYTHON_BIN}" -m "${RUN_MODULE}" "$@" 2>&1 | tee "${log_file}"
    ;;
  tests)
    exec "${PYTHON_BIN}" -m pytest tests/agentic -q
    ;;
  *)
    echo "Unknown command: '${CMD}'. Use one of: eligibility-extract | tests" >&2
    exit 2
    ;;
esac
