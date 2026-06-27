.PHONY: drug-ontology-pipeline-tsvs drug-ontology-analysis-tsvs eligibility-path-ctgov eligibility-path-anzctr eligibility-path-run-all-trials-download-w-llm eligibility-path-run-all-trials-download eligibility-path-run-all eligibility-path-resource-audit eligibility-path-clean eligibility-path-clean-dry-run eligibility-path-tests

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

eligibility-path-clean:
	scripts/eligibility/clean_outputs.sh --yes

eligibility-path-clean-dry-run:
	scripts/eligibility/clean_outputs.sh --dry-run

eligibility-path-tests:
	scripts/eligibility/pipeline.sh tests
