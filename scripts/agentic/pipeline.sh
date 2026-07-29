#!/usr/bin/env bash
#
# Driver for the v2 agentic pipeline (aus_trial_universe).
# Sets PYTHONPATH, picks a Python that has the v2 deps, loads .env, dispatches.
#
# Subcommands:
#   run     Full pipeline: Stage I extract -> Stage II map (OncoTree). Runs the unit
#           tests first (aborts on failure) and tees a combined timestamped log.
#           Vars: ID=<id> (one) | IDS=<a,b,c> (a set) | none = ALL trials
#                 optional: MODEL=<name>  NO_JUDGE=1  NO_REVIEW=1  EXTRACT_ONLY=1
#   tests   Run the agentic unit-test suite (no API calls).
#   cache-prune  GC the shared LLM response cache of entries from OUTDATED prompts (both paths). Dry-run by
#             default; APPLY=1 deletes, PURGE_UNKNOWN=1 also drops legacy/untagged entries. No API calls.
#   validate  Independent output validator ("review of the reviewer agents"): re-checks a finished
#             output TSV outside the workflow. Var: OUT=<tsv> (default: newest). No API calls.
#   export  Build the matching-engine export: Set A (trial_eligibility.tsv) + MANIFEST; Set B (drug tables)
#             referenced in place. Deterministic; no API. SNAPSHOT=1 also mints export/snapshot_<ts>/.
#   drug-ref-build  Build/refresh the drug reference resource (spec §6.1). Incremental (existing drugs
#             reused). Vars: DRUGS="a; b" | IDS=NCT1,NCT2 | ALL_TRIALS=1 ; optional LIMIT, REFRESH_DRUGS=1, NO_REVIEW=1, MODEL.
#   clean   Delete transient run artifacts under data/agentic/ (eligibility/, log/, cache/).
#           No API/Python needed; handy for clearing test runs.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
AGENTIC_DATA="${REPO_ROOT}/data/agentic"
LOG_DIR="${AGENTIC_DATA}/transient/log"

# `clean` needs neither Python nor .env; handle it before the interpreter pick.
# Scoped STRICTLY to the `transient/` bucket (cache/ + log/) — the only wipeable, regenerable operational data.
# It must NEVER touch inputs/ (trial_universe, resources), masters/ (the produced stores incl. the curated
# eligibility output), derived/ (joined, export), or analysis/. (Reset a master explicitly by archiving it.)
if [[ "${1:-}" == "clean" ]]; then
  for sub in transient/cache transient/log; do
    dir="${AGENTIC_DATA}/${sub}"
    if [[ -d "${dir}" ]]; then
      echo "removing ${dir}" >&2
      rm -rf "${dir}"
    fi
  done
  mkdir -p "${AGENTIC_DATA}/transient/log"
  echo "cleaned: data/agentic/transient/{cache,log}" >&2
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

PIPELINE_MODULE="aus_trial_universe.run"
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
    if [[ -n "${WORKERS:-}" ]]; then args+=(--workers "${WORKERS}"); fi
    if [[ -n "${MAX_CONCURRENCY:-}" ]]; then args+=(--max-concurrency "${MAX_CONCURRENCY}"); fi

    # (b) Stream to a timestamped log. RESUME=1 → auto-resuming loop: re-run --resume (skip done trials) until it
    # reports COMPLETE (exit 0). Survives interruptions/outages — leave it running and it picks up on reconnect.
    mkdir -p "${LOG_DIR}"
    log_file="${LOG_DIR}/agentic_run_${label}_$(date +%Y%m%d_%H%M%S).log"
    printf '\n==> run (logging to %s)\n' "${log_file}" >&2
    if [[ -n "${RESUME:-}" ]]; then
      args+=(--resume)
      attempt=0
      while true; do
        attempt=$((attempt + 1))
        printf '\n==> resume attempt %d\n' "${attempt}" >&2
        set +e
        "${PYTHON_BIN}" -m "${PIPELINE_MODULE}" "${args[@]}" 2>&1 | tee -a "${log_file}"
        rc=${PIPESTATUS[0]}
        set -e
        [[ "${rc}" -eq 0 ]] && { printf '\n==> COMPLETE after %d attempt(s)\n' "${attempt}" >&2; break; }
        printf '\n==> incomplete (rc=%s); waiting %ss before resuming …\n' "${rc}" "${RESUME_BACKOFF:-120}" >&2
        sleep "${RESUME_BACKOFF:-120}"
      done
    else
      "${PYTHON_BIN}" -m "${PIPELINE_MODULE}" "${args[@]}" 2>&1 | tee "${log_file}"
    fi
    ;;
  tests)
    exec "${PYTHON_BIN}" -m pytest tests/agentic -q
    ;;
  gates)
    # Production gates on the current on-disk state (exit 1 on any FAIL). Deterministic, no API.
    exec "${PYTHON_BIN}" -m aus_trial_universe.qa.gates
    ;;
  arm-consistency)
    # Verify the (trialId, arm) split is identical between the eligibility (trial_arms) and drug
    # (trial_to_intervention) paths — the shared join key. No API calls.
    exec "${PYTHON_BIN}" -m aus_trial_universe.tasks.eligibility.qa.arm_consistency
    ;;
  export)
    # Build the matching-engine export: Set A (trial_eligibility.tsv) + MANIFEST; Set B (drug tables) referenced
    # in place. Deterministic join, no API. SNAPSHOT=1 also writes an immutable export/snapshot_<ts>/ bundle.
    eargs=()
    if [[ -n "${SNAPSHOT:-}" ]]; then eargs+=(--snapshot); fi
    exec "${PYTHON_BIN}" -m aus_trial_universe.export "${eargs[@]}"
    ;;
  cache-prune)
    # Prune the shared LLM response cache of entries from OUTDATED prompts (both paths).
    # Dry-run by default; APPLY=1 deletes; PURGE_UNKNOWN=1 also drops legacy/untagged entries.
    cargs=()
    if [[ -n "${APPLY:-}" ]]; then cargs+=(--apply); fi
    if [[ -n "${PURGE_UNKNOWN:-}" ]]; then cargs+=(--purge-unknown); fi
    exec "${PYTHON_BIN}" -m aus_trial_universe.core.cache_prune "${cargs[@]}"
    ;;
  drug-migrate-trial-arms)
    # Re-key the drug path's trial_to_intervention to trial_arm_id against the fresh trial_arms registry, and
    # report intervention-input additions/deletions. Dry-run by default; APPLY=1 rewrites ONLY that drug file.
    margs=()
    if [[ -n "${APPLY:-}" ]]; then margs+=(--apply); fi
    exec "${PYTHON_BIN}" -m aus_trial_universe.tasks.drug_utility.migrate_trial_arms "${margs[@]}"
    ;;
  validate)
    # Independent output validator (review of the reviewer agents). OUT=<tsv> or newest.
    vargs=()
    if [[ -n "${OUT:-}" ]]; then vargs=("${OUT}"); fi
    exec "${PYTHON_BIN}" -m aus_trial_universe.tasks.eligibility.qa.validate_output "${vargs[@]}"
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
    "${PYTHON_BIN}" -m aus_trial_universe.tasks.drug_utility.build "${dargs[@]}" 2>&1 | tee "${log_file}"
    ;;
  ingest)
    # Stage-I trial ingestion (download + filter + POTTR + version). No LLM. Vars: REGISTRY=ctgov|anzctr|all.
    iargs=()
    if [[ -n "${REGISTRY:-}" ]]; then iargs+=(--registry "${REGISTRY}"); fi
    mkdir -p "${LOG_DIR}"
    log_file="${LOG_DIR}/ingest_$(date +%Y%m%d_%H%M%S).log"
    printf '\n==> ingest (logging to %s)\n' "${log_file}" >&2
    "${PYTHON_BIN}" -m aus_trial_universe.ingest "${iargs[@]}" 2>&1 | tee "${log_file}"
    ;;
  refresh)
    # End-to-end periodic refresh: ingest → expire/restore → curate new → drug → approval vocab → export.
    rargs=()
    if [[ -n "${WORKERS:-}" ]]; then rargs+=(--workers "${WORKERS}"); fi
    if [[ -n "${MAX_CONCURRENCY:-}" ]]; then rargs+=(--max-concurrency "${MAX_CONCURRENCY}"); fi
    if [[ -n "${NO_REVIEW:-}" ]]; then rargs+=(--no-review); fi
    if [[ -n "${SKIP_INGEST:-}" ]]; then rargs+=(--skip-ingest); fi
    if [[ -n "${DRY_RUN_EXPIRY:-}" ]]; then rargs+=(--dry-run-expiry); fi
    mkdir -p "${LOG_DIR}"
    log_file="${LOG_DIR}/refresh_$(date +%Y%m%d_%H%M%S).log"
    printf '\n==> refresh (logging to %s)\n' "${log_file}" >&2
    "${PYTHON_BIN}" -m aus_trial_universe.refresh "${rargs[@]}" 2>&1 | tee "${log_file}"
    ;;
  map-approvals)
    # Symmetric-match: map drug_regulatory_approvals cancer_type/biomarker into the eligibility vocab (additive —
    # writes only the 2 approval-map tables). Vars: optional WORKERS, MAX_CONCURRENCY, NO_REVIEW=1, NO_SEED=1, LIMIT, MODEL.
    margs=()
    if [[ -n "${WORKERS:-}" ]]; then margs+=(--workers "${WORKERS}"); fi
    if [[ -n "${MAX_CONCURRENCY:-}" ]]; then margs+=(--max-concurrency "${MAX_CONCURRENCY}"); fi
    if [[ -n "${NO_REVIEW:-}" ]]; then margs+=(--no-review); fi
    if [[ -n "${NO_SEED:-}" ]]; then margs+=(--no-seed); fi
    if [[ -n "${LIMIT:-}" ]]; then margs+=(--limit "${LIMIT}"); fi
    if [[ -n "${MODEL:-}" ]]; then margs+=(--model "${MODEL}"); fi
    mkdir -p "${LOG_DIR}"
    log_file="${LOG_DIR}/map_approvals_$(date +%Y%m%d_%H%M%S).log"
    printf '\n==> map-approvals (logging to %s)\n' "${log_file}" >&2
    "${PYTHON_BIN}" -m aus_trial_universe.tasks.drug_utility.map_approvals "${margs[@]}" 2>&1 | tee "${log_file}"
    ;;
  drug-ref-refresh-pottr)
    # Download the current POTTR files from GitHub -> resources/drug_utility/pottr/current_version/ (archives old).
    exec "${PYTHON_BIN}" -m aus_trial_universe.tasks.drug_utility.pottr
    ;;
  *)
    echo "Unknown command: '${CMD}'. Use: run | ingest | refresh | tests | cache-prune | validate | drug-ref-build | map-approvals | drug-ref-refresh-pottr" >&2
    exit 2
    ;;
esac
