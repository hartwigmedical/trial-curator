.PHONY: drug-ontology-pipeline-tsvs drug-ontology-analysis-tsvs eligibility-path-ctgov eligibility-path-anzctr eligibility-path-run-all-trials-download-w-llm eligibility-path-run-all-trials-download eligibility-path-run-all eligibility-path-resource-audit eligibility-path-pottr-comparison eligibility-path-clean eligibility-path-clean-dry-run eligibility-path-tests agentic-run agentic-clean agentic-tests agentic-cache-prune agentic-arm-consistency agentic-drug-migrate-trial-arms agentic-validate drug-ref-build drug-ref-refresh-pottr

drug-ontology-pipeline-tsvs:
	scripts/drug_ontology/pipeline_tsvs.sh

drug-ontology-analysis-tsvs:
	scripts/drug_ontology/analysis_tsvs.sh

eligibility-path-ctgov:
	scripts/eligibility/pipeline.sh ctgov

eligibility-path-anzctr:
	scripts/eligibility/pipeline.sh anzctr

eligibility-path-run-all-trials-download:
	scripts/eligibility/pipeline.sh all-trials-download

eligibility-path-run-all-trials-download-w-llm:
	scripts/eligibility/pipeline.sh all-trials-download-w-llm

eligibility-path-run-all:
	scripts/eligibility/pipeline.sh all

eligibility-path-resource-audit:
	scripts/eligibility/pipeline.sh resource-audit

eligibility-path-pottr-comparison:
	scripts/eligibility/pipeline.sh pottr-comparison

eligibility-path-clean:
	scripts/eligibility/clean_outputs.sh --yes

eligibility-path-clean-dry-run:
	scripts/eligibility/clean_outputs.sh --dry-run

eligibility-path-tests:
	scripts/eligibility/pipeline.sh tests

# --- v2 agentic pipeline (aus_trial_universe/agentic) ---
# Full pipeline (extract -> map -> drug); the pure-3NF store in eligibility/current_output/ + the joined flat
# view in eligibility/combined/combined.tsv (kept out of the store) + one log per run. Runs unit tests first.
#   make agentic-run ID=NCT06881784          # one trial (source auto-detected)
#   make agentic-run IDS=NCT1,ACTRN2,NCT3    # a specific set of trials
#   make agentic-run                         # ALL trials (ctgov + anzctr)
#   optional: MODEL=<name>  NO_JUDGE=1  NO_REVIEW=1  EXTRACT_ONLY=1 (skip map+drug)
#             WORKERS=<n>  MAX_CONCURRENCY=<n> (global LLM-call cap for large runs)
#             RESUME=1 (auto-resuming loop: skip done trials, re-run until complete — survives interruptions;
#                       leave it running and it picks up on reconnect. RESUME_BACKOFF=<secs> between retries.)
agentic-run:
	ID="$(ID)" IDS="$(IDS)" MODEL="$(MODEL)" NO_JUDGE="$(NO_JUDGE)" NO_REVIEW="$(NO_REVIEW)" EXTRACT_ONLY="$(EXTRACT_ONLY)" WORKERS="$(WORKERS)" MAX_CONCURRENCY="$(MAX_CONCURRENCY)" RESUME="$(RESUME)" RESUME_BACKOFF="$(RESUME_BACKOFF)" scripts/agentic/pipeline.sh run

# Wipe run artifacts under data/agentic/{output,log,cache} (handy between test runs).
agentic-clean:
	scripts/agentic/pipeline.sh clean

agentic-tests:
	scripts/agentic/pipeline.sh tests

# Prune the LLM response cache (data/agentic/cache/) of entries from OUTDATED prompts — an entry whose agent
# prompt has since changed (or whose agent was removed). Covers BOTH paths (they share one cache). Dry-run by
# default; APPLY=1 to delete; PURGE_UNKNOWN=1 to also drop legacy/untagged (pre-provenance) entries.
#   make agentic-cache-prune                        # dry-run report
#   make agentic-cache-prune APPLY=1                # delete stale entries
#   make agentic-cache-prune APPLY=1 PURGE_UNKNOWN=1
agentic-cache-prune:
	APPLY="$(APPLY)" PURGE_UNKNOWN="$(PURGE_UNKNOWN)" scripts/agentic/pipeline.sh cache-prune

# Verify referential integrity of the arm join key: every trial_arm_id referenced by the eligibility tables and
# the drug path's trial_to_intervention EXISTS in the shared trial_arms registry. Exit 0 = consistent. No API.
#   make agentic-arm-consistency
agentic-arm-consistency:
	scripts/agentic/pipeline.sh arm-consistency

# Re-key the drug path's trial_to_intervention to trial_arm_id against the fresh trial_arms registry, and report
# intervention-input additions/deletions (ANZCTR arm drift). Rewrites ONLY trial_to_intervention.tsv; the other
# four drug tables are left untouched (reconcile separately after reviewing the report). Dry-run by default.
#   make agentic-drug-migrate-trial-arms            # dry-run + report -> data/agentic/analysis/
#   make agentic-drug-migrate-trial-arms APPLY=1    # also rewrite trial_to_intervention.tsv
agentic-drug-migrate-trial-arms:
	APPLY="$(APPLY)" scripts/agentic/pipeline.sh drug-migrate-trial-arms

# Independent output validator — a "review of the reviewer agents". Deterministic, runs OUTSIDE
# the workflow to catch what the in-loop reviewers let through. Testing-period QA step (not the
# production path). OUT defaults to the newest output TSV.
#   make agentic-validate                 # data/agentic/eligibility/combined/combined.tsv
#   make agentic-validate OUT=<combined.tsv>  # a specific run's combined view
agentic-validate:
	OUT="$(OUT)" scripts/agentic/pipeline.sh validate

# Standalone drug-reference builder (spec §6.1). Incremental — existing drugs are reused (pure lookup);
# only new (or REFRESH_DRUGS=1) drugs are researched. Writes data/agentic/drug_annotations/current_version/.
#   make drug-ref-build DRUGS="pembrolizumab; Keytruda; Ris-Rez"
#   make drug-ref-build IDS=NCT07099898,NCT05009992 LIMIT=5
#   make drug-ref-build ALL_TRIALS=1                 # every distinct drug across all ctgov + anzctr
#   optional: LIMIT=<n>  WORKERS=<n> (concurrency, default 8)  REFRESH_DRUGS=1  NO_REVIEW=1  MODEL=<name>
drug-ref-build:
	DRUGS="$(DRUGS)" IDS="$(IDS)" ALL_TRIALS="$(ALL_TRIALS)" LIMIT="$(LIMIT)" WORKERS="$(WORKERS)" REFRESH_DRUGS="$(REFRESH_DRUGS)" NO_REVIEW="$(NO_REVIEW)" MODEL="$(MODEL)" scripts/agentic/pipeline.sh drug-ref-build

# Refresh the POTTR reference data from GitHub (public) into resources/drug_utility/pottr/current_version/,
# archiving the previous version. RxNorm stays a manual drop-in (UMLS-licensed).
#   make drug-ref-refresh-pottr
drug-ref-refresh-pottr:
	scripts/agentic/pipeline.sh drug-ref-refresh-pottr
