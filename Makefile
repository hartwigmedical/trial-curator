.PHONY: drug-ontology-pipeline-tsvs drug-ontology-analysis-tsvs eligibility-curate-new eligibility-curate-all eligibility-extract-criteria eligibility-tests

drug-ontology-pipeline-tsvs:
	scripts/drug_ontology/pipeline_tsvs.sh

drug-ontology-analysis-tsvs:
	scripts/drug_ontology/analysis_tsvs.sh

eligibility-curate-new:
	scripts/eligibility/pipeline.sh curate-new

eligibility-curate-all:
	scripts/eligibility/pipeline.sh curate-all

eligibility-extract-criteria:
	scripts/eligibility/pipeline.sh extract-criteria

eligibility-tests:
	scripts/eligibility/pipeline.sh tests
