#!/usr/bin/env bash
#
# ISOLATED live-demo driver for the agentic pipeline (presentation use).
#
# Runs the full pipeline over a tiny picked trial set, one readable stage at a time, with all OUTPUTS confined
# to data/agentic/demo/ (the production stores are never touched) while REUSING the ingested trials + the shared
# LLM cache. See aus_trial_universe/agentic/demo.py for the re-rooting mechanism. Invoked by `make agentic-demo`.
#
#   Vars:  IDS=NCT1,NCT2   trial ids to demo   (default: the two baked into demo.py)
#          RESET=0         keep the previous data/agentic/demo/ contents (default: wipe for a clean run)
#
# Standalone on purpose (does not touch scripts/agentic/pipeline.sh) so it stays a throwaway add-on.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
DEMO_DIR="${REPO_ROOT}/data/agentic/demo"
LOG_DIR="${DEMO_DIR}/log"

export PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
export PYTHONDONTWRITEBYTECODE="${PYTHONDONTWRITEBYTECODE:-1}"
# Unbuffered stdout so the stage banners (print) and the pipeline logs (logging→stderr) interleave in true
# chronological order when merged through `tee` — essential for walking an audience through the log live.
export PYTHONUNBUFFERED=1

# Pick the first Python that actually has the v2 deps (openai + pydantic).
pick_python() {
  local cand
  for cand in "${PYTHON_BIN:-}" python "${HOME}/anaconda3/envs/trial_curator/bin/python" \
              /opt/anaconda3/envs/trial_curator/bin/python python3; do
    [[ -n "${cand}" ]] || continue
    if command -v "${cand}" >/dev/null 2>&1 && "${cand}" -c "import openai, pydantic" >/dev/null 2>&1; then
      echo "${cand}"; return 0
    fi
  done
  return 1
}
if ! PYTHON_BIN="$(pick_python)"; then
  echo "ERROR: no Python with openai+pydantic found. Activate the 'trial_curator' conda env or set PYTHON_BIN." >&2
  exit 1
fi
echo "using python: ${PYTHON_BIN}" >&2

load_env_file() { [[ -f "$1" ]] && { set -a; source "$1"; set +a; } || true; }
load_env_file "${REPO_ROOT}/.env"
load_env_file "${REPO_ROOT}/.env.local"

cd "${REPO_ROOT}"

# Fresh demo folder by default so every run shows the pipeline building from scratch. Scoped STRICTLY to the
# demo output dir — never the shared inputs, resources or cache.
if [[ "${RESET:-1}" != "0" ]]; then
  echo "resetting demo outputs: ${DEMO_DIR}" >&2
  rm -rf "${DEMO_DIR}"
fi
mkdir -p "${LOG_DIR}"

# Seed the demo drug reference from the production annotations. The drug reference is universe-wide reference
# data (like OncoTree / POTTR), so the demo REUSES it — every demo drug is then a pure LOOKUP and the drug stage
# does ZERO web search. The demo still WRITES only under demo/ (this is a copy; production is never touched).
PROD_DRUG="${REPO_ROOT}/data/agentic/drug_annotations/current_version"
DEMO_DRUG="${DEMO_DIR}/drug_annotations/current_version"
if [[ -d "${PROD_DRUG}" && ! -d "${DEMO_DRUG}" ]]; then
  mkdir -p "${DEMO_DIR}/drug_annotations"
  cp -R "${PROD_DRUG}" "${DEMO_DRUG}"
  echo "seeded demo drug reference from production (lookup-only — 0 drug web search)" >&2
elif [[ ! -d "${PROD_DRUG}" ]]; then
  echo "WARNING: no production drug reference at ${PROD_DRUG}; drug stage may fall back to web search." >&2
fi

args=()
if [[ -n "${IDS:-}" ]]; then args+=(--ids "${IDS}"); fi

log_file="${LOG_DIR}/demo_$(date +%Y%m%d_%H%M%S).log"
printf '\n==> agentic live demo (logging to %s)\n' "${log_file}" >&2
"${PYTHON_BIN}" -m aus_trial_universe.agentic.demo "${args[@]}" 2>&1 | tee "${log_file}"
