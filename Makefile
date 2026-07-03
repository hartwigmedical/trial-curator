.PHONY: drug-ontology-pipeline-tsvs drug-ontology-analysis-tsvs eligibility-path-ctgov eligibility-path-anzctr eligibility-path-run-all-trials-download-w-llm eligibility-path-run-all-trials-download eligibility-path-run-all eligibility-path-resource-audit eligibility-path-pottr-comparison eligibility-path-clean eligibility-path-clean-dry-run eligibility-path-tests agentic-eligibility-extract agentic-tests

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
# Extract a DNF eligibility table (ctgov or anzctr). Runs unit tests first and
# writes a timestamped log under data/agentic/logs/.
#   make agentic-eligibility-extract ID=NCT06881784               # one ctgov trial (default)
#   make agentic-eligibility-extract ID=ACTRN12625... SOURCE=anzctr
#   make agentic-eligibility-extract SELECTED=6                   # 3 ctgov + 3 anzctr
#   optional: MODEL=<name> NO_JUDGE=1
agentic-eligibility-extract:
	ID="$(ID)" SELECTED="$(SELECTED)" SOURCE="$(SOURCE)" MODEL="$(MODEL)" NO_JUDGE="$(NO_JUDGE)" scripts/agentic/pipeline.sh eligibility-extract

agentic-tests:
	scripts/agentic/pipeline.sh tests
