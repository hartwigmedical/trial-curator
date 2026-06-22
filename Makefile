.PHONY: drug-ontology-pipeline-tsvs drug-ontology-analysis-tsvs eligibility-path-ctgov eligibility-path-anzctr eligibility-path-run-all eligibility-path-run-all-w-llm eligibility-path-clean eligibility-path-clean-dry-run eligibility-path-tests eligibility-extract-criteria eligibility-tests

drug-ontology-pipeline-tsvs:
	scripts/drug_ontology/pipeline_tsvs.sh

drug-ontology-analysis-tsvs:
	scripts/drug_ontology/analysis_tsvs.sh

eligibility-path-ctgov:
	scripts/eligibility/pipeline.sh ctgov

eligibility-path-anzctr:
	scripts/eligibility/pipeline.sh anzctr

eligibility-path-run-all:
	scripts/eligibility/pipeline.sh all

eligibility-path-run-all-w-llm:
	scripts/eligibility/pipeline.sh all-w-llm

eligibility-path-clean:
	scripts/eligibility/clean_outputs.sh --yes

eligibility-path-clean-dry-run:
	scripts/eligibility/clean_outputs.sh --dry-run

eligibility-extract-criteria:
	scripts/eligibility/pipeline.sh all

eligibility-tests:
	scripts/eligibility/pipeline.sh tests

eligibility-path-tests:
	scripts/eligibility/pipeline.sh tests
