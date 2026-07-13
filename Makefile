.PHONY: drug-ontology-pipeline-tsvs drug-ontology-analysis-tsvs eligibility-path-ctgov eligibility-path-anzctr eligibility-path-run-all-trials-download-w-llm eligibility-path-run-all-trials-download eligibility-path-run-all eligibility-path-resource-audit eligibility-path-pottr-comparison eligibility-path-clean eligibility-path-clean-dry-run eligibility-path-tests agentic-run agentic-clean agentic-tests agentic-validate drug-ref-build

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
# Full pipeline (extract -> map -> drug); one timestamped output dir (regime/eligibility/combined) + one
# log per run. Runs unit tests first.
#   make agentic-run ID=NCT06881784          # one trial (source auto-detected)
#   make agentic-run IDS=NCT1,ACTRN2,NCT3    # a specific set of trials
#   make agentic-run                         # ALL trials (ctgov + anzctr)
#   optional: MODEL=<name>  NO_JUDGE=1  NO_REVIEW=1  EXTRACT_ONLY=1 (skip map+drug)
agentic-run:
	ID="$(ID)" IDS="$(IDS)" MODEL="$(MODEL)" NO_JUDGE="$(NO_JUDGE)" NO_REVIEW="$(NO_REVIEW)" EXTRACT_ONLY="$(EXTRACT_ONLY)" scripts/agentic/pipeline.sh run

# Wipe run artifacts under data/agentic/{output,log,cache} (handy between test runs).
agentic-clean:
	scripts/agentic/pipeline.sh clean

agentic-tests:
	scripts/agentic/pipeline.sh tests

# Independent output validator — a "review of the reviewer agents". Deterministic, runs OUTSIDE
# the workflow to catch what the in-loop reviewers let through. Testing-period QA step (not the
# production path). OUT defaults to the newest output TSV.
#   make agentic-validate                 # newest data/agentic/output/<timestamp>/combined.tsv
#   make agentic-validate OUT=<combined.tsv>  # a specific run's combined view
agentic-validate:
	OUT="$(OUT)" scripts/agentic/pipeline.sh validate

# Standalone drug-reference builder (spec §6.1). Incremental — existing drugs are reused (pure lookup);
# only new (or REFRESH_DRUGS=1) drugs are researched. Writes data/agentic/resources/drug_ref/version_<ddmmyyyy>/.
#   make drug-ref-build DRUGS="pembrolizumab; Keytruda; Ris-Rez"
#   make drug-ref-build IDS=NCT07099898,NCT05009992 LIMIT=5
#   optional: REFRESH_DRUGS=1  NO_REVIEW=1  MODEL=<name>
drug-ref-build:
	DRUGS="$(DRUGS)" IDS="$(IDS)" LIMIT="$(LIMIT)" REFRESH_DRUGS="$(REFRESH_DRUGS)" NO_REVIEW="$(NO_REVIEW)" MODEL="$(MODEL)" scripts/agentic/pipeline.sh drug-ref-build
