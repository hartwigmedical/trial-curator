.PHONY: agentic-run agentic-ingest agentic-refresh agentic-gates agentic-demo agentic-clean agentic-tests agentic-cache-prune agentic-arm-consistency agentic-export agentic-drug-migrate-trial-arms agentic-validate drug-ref-build drug-ref-map-approvals drug-ref-refresh-pottr

# --- v2 agentic pipeline (aus_trial_universe) — the only pipeline; legacy eligibility_path/drug_utility_path retired ---
# Full pipeline (extract -> map -> drug); the pure-3NF store in masters/eligibility/current_version/ + the joined
# flat views in derived/joined/ (kept out of the store) + one log per run. Runs unit tests first.
#   make agentic-run ID=NCT06881784          # one trial (source auto-detected)
#   make agentic-run IDS=NCT1,ACTRN2,NCT3    # a specific set of trials
#   make agentic-run                         # ALL trials (ctgov + anzctr)
#   optional: MODEL=<name>  NO_JUDGE=1  NO_REVIEW=1  EXTRACT_ONLY=1 (skip map+drug)
#             WORKERS=<n>  MAX_CONCURRENCY=<n> (global LLM-call cap for large runs)
#             RESUME=1 (auto-resuming loop: skip done trials, re-run until complete — survives interruptions;
#                       leave it running and it picks up on reconnect. RESUME_BACKOFF=<secs> between retries.)
agentic-run:
	ID="$(ID)" IDS="$(IDS)" MODEL="$(MODEL)" NO_JUDGE="$(NO_JUDGE)" NO_REVIEW="$(NO_REVIEW)" EXTRACT_ONLY="$(EXTRACT_ONLY)" WORKERS="$(WORKERS)" MAX_CONCURRENCY="$(MAX_CONCURRENCY)" RESUME="$(RESUME)" RESUME_BACKOFF="$(RESUME_BACKOFF)" scripts/agentic/pipeline.sh run

# Stage-I trial INGESTION (self-contained download → filter → POTTR → version). Full download each run; writes the
# merged input to inputs/trial_universe/<registry>/current_version/ (archiving the previous). No LLM. No curation —
# run `make agentic-run` after to curate the new trials. Wrap long runs in caffeinate.
#   make agentic-ingest REGISTRY=ctgov      # (anzctr/all land in Phase 3)
agentic-ingest:
	REGISTRY="$(REGISTRY)" scripts/agentic/pipeline.sh ingest

# End-to-end PERIODIC REFRESH (the self-contained pipeline): ingest (full download, both registries) → expire/restore
# → curate ONLY new trials (eligibility extract+map+reconcile) → drug utility (facts+roles for new/restored) →
# approval vocab → export. Incremental: unchanged trials re-hit the cache, existing drugs skip web search. Wrap in
# caffeinate. Optional: WORKERS=<n> MAX_CONCURRENCY=<n> NO_REVIEW=1 SKIP_INGEST=1 DRY_RUN_EXPIRY=1
#   make agentic-refresh WORKERS=80 MAX_CONCURRENCY=500
agentic-refresh:
	WORKERS="$(WORKERS)" MAX_CONCURRENCY="$(MAX_CONCURRENCY)" NO_REVIEW="$(NO_REVIEW)" SKIP_INGEST="$(SKIP_INGEST)" DRY_RUN_EXPIRY="$(DRY_RUN_EXPIRY)" scripts/agentic/pipeline.sh refresh

# Isolated LIVE DEMO — runs the FULL pipeline (extract -> map Step 1 -> map Step 2 -> drug + role -> export)
# over 2 picked trials, one readable stage at a time, for walking an audience through the logs. All OUTPUTS go
# to data/agentic/demo/ (the production stores are NEVER touched); ingested trials + the shared LLM cache are
# reused so a run is near-instant. Standalone add-on (own script + module; no core code changed).
#   make agentic-demo                       # the two baked-in trials, fresh demo folder
#   make agentic-demo IDS=NCT1,NCT2         # a specific set
#   make agentic-demo RESET=0               # keep the previous demo/ contents (accumulate)
agentic-demo:
	IDS="$(IDS)" RESET="$(RESET)" scripts/agentic/demo.sh

# Wipe the transient bucket data/agentic/transient/{cache,log} (handy between test runs). Never touches inputs/,
# masters/ (incl. the curated eligibility store), derived/, or analysis/.
agentic-clean:
	scripts/agentic/pipeline.sh clean

agentic-tests:
	scripts/agentic/pipeline.sh tests

# Prune the LLM response cache (data/agentic/transient/cache/) of entries from OUTDATED prompts — an entry whose agent
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

# PRODUCTION GATES — the deterministic trust decision for an unattended cycle: FK integrity · expiry completeness ·
# additive safety · curation completeness · empty-output reasons · export integrity. Run automatically as the last
# stage of `agentic-refresh` (a FAIL there makes the refresh exit non-zero); this target gates the CURRENT on-disk
# state on demand. Exit 0 = trustworthy, 1 = at least one FAIL. Deterministic, no API.
#   make agentic-gates
agentic-gates:
	scripts/agentic/pipeline.sh gates

# Build the matching-engine EXPORT. Set A = the wide flat trial_eligibility.tsv (trial info + eligibility +
# intervention, one row per (trial_arm_id, conjunction)); Set B = the drug 3NF tables, referenced in place (a
# MANIFEST points at them). Deterministic join; no API. SNAPSHOT=1 also mints an immutable, self-contained
# export/snapshot_<ts>/ bundle (Set A + a frozen copy of the drug tables) for hand-off.
#   make agentic-export                # -> data/agentic/derived/export/{trial_eligibility.tsv, MANIFEST.md}
#   make agentic-export SNAPSHOT=1     # + export/snapshot_<ts>/ (frozen bundle)
agentic-export:
	SNAPSHOT="$(SNAPSHOT)" scripts/agentic/pipeline.sh export

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
# NOTE: still points at the RETIRED combined.tsv path — pending a repoint to derived/export/trial_eligibility.tsv
# (tracked as an optional cleanup in the handover). OUT=<tsv> overrides.
agentic-validate:
	OUT="$(OUT)" scripts/agentic/pipeline.sh validate

# Standalone drug-reference builder (spec §6.1). Incremental — existing drugs are reused (pure lookup);
# only new (or REFRESH_DRUGS=1) drugs are researched. Writes data/agentic/masters/drug_annotations/current_version/.
#   make drug-ref-build DRUGS="pembrolizumab; Keytruda; Ris-Rez"
#   make drug-ref-build IDS=NCT07099898,NCT05009992 LIMIT=5
#   make drug-ref-build ALL_TRIALS=1                 # every distinct drug across all ctgov + anzctr
#   optional: LIMIT=<n>  WORKERS=<n> (concurrency, default 8)  REFRESH_DRUGS=1  NO_REVIEW=1  MODEL=<name>
drug-ref-build:
	DRUGS="$(DRUGS)" IDS="$(IDS)" ALL_TRIALS="$(ALL_TRIALS)" LIMIT="$(LIMIT)" WORKERS="$(WORKERS)" REFRESH_DRUGS="$(REFRESH_DRUGS)" NO_REVIEW="$(NO_REVIEW)" MODEL="$(MODEL)" scripts/agentic/pipeline.sh drug-ref-build

# Symmetric-match: map the drug_regulatory_approvals free-text cancer_type/biomarker into the eligibility vocab
# (OncoTree + finding-model), reusing the signed-off mappers. Additive — writes only the 2 approval-map tables into
# drug_annotations/current_version/ (the 6 core drug tables are untouched). Seeds from the trial FINAL maps for
# cross-domain code identity. Requires a populated drug_annotations (run drug-ref-build first).
#   make drug-ref-map-approvals
#   optional: WORKERS=<n>  MAX_CONCURRENCY=<n>  NO_REVIEW=1  NO_SEED=1  LIMIT=<n> (smoke)  MODEL=<name>
drug-ref-map-approvals:
	WORKERS="$(WORKERS)" MAX_CONCURRENCY="$(MAX_CONCURRENCY)" NO_REVIEW="$(NO_REVIEW)" NO_SEED="$(NO_SEED)" LIMIT="$(LIMIT)" MODEL="$(MODEL)" scripts/agentic/pipeline.sh map-approvals

# Refresh the POTTR reference data from GitHub (public) into resources/drug_utility/pottr/current_version/,
# archiving the previous version. RxNorm stays a manual drop-in (UMLS-licensed).
#   make drug-ref-refresh-pottr
drug-ref-refresh-pottr:
	scripts/agentic/pipeline.sh drug-ref-refresh-pottr

