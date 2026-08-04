# Drug Ontology Commands

There are exactly two repo-owned Make commands for drug ontology TSVs:

```bash
make drug-ontology-pipeline-tsvs
make drug-ontology-analysis-tsvs
```

Both commands run the unit tests before generating TSVs:

```bash
python -m pytest tests/drug_ontology
```

## Pipeline TSVs

Run:

```bash
make drug-ontology-pipeline-tsvs
```

This command rebuilds the production drug ontology outputs. It:

1. Runs `tests/drug_ontology`.
2. Resolves the latest sortable `version_*` folder for each raw input source.
3. Builds pipeline processed inputs.
4. Starts PostgreSQL through `docker-compose.postgres.yml` unless disabled.
5. Rebuilds the drug ontology database schemas.
6. Loads CTGov, RxNorm, ATC, FDA, POTTR, and ChEMBL data.
7. Exports the approved production TSVs.
8. Archives the four generated pipeline files into a timestamped comparison run.

Outputs:

```text
data/ctgov/drug_ontology/pipeline_outputs/input_drugs_to_rxnorm_mapping_<DDMMYYYY>.tsv
data/ctgov/drug_ontology/pipeline_outputs/ctgov_drug_intervention_classification_summary.tsv
data/ctgov/drug_ontology/pipeline_outputs/ctgov_trial_drug_classification_summary.tsv
```

Comparison archive:

```text
data/ctgov/drug_ontology/comparison_runs/run_YYYYMMDD_HHMMSS
data/ctgov/drug_ontology/comparison_runs/latest_run.json
```

Compare the current generated files against the latest archived run:

```bash
PYTHONPATH=. python -m aus_trial_universe.drug_utility_path.ctgov.drug_ontology.shared.run_archive \
  --data-dir data/ctgov/drug_ontology \
  compare-latest
```

Compare against a specific archived run:

```bash
PYTHONPATH=. python -m aus_trial_universe.drug_utility_path.ctgov.drug_ontology.shared.run_archive \
  --data-dir data/ctgov/drug_ontology \
  compare-latest \
  --manifest data/ctgov/drug_ontology/comparison_runs/run_YYYYMMDD_HHMMSS/manifest.json
```

Skip Docker startup if the PostgreSQL container is already running:

```bash
DRUG_ONTOLOGY_START_POSTGRES=0 make drug-ontology-pipeline-tsvs
```

Skip comparison-run archiving for a local scratch run:

```bash
DRUG_ONTOLOGY_ARCHIVE_PIPELINE_RUN=0 make drug-ontology-pipeline-tsvs
```

## Analysis TSVs

Run:

```bash
make drug-ontology-analysis-tsvs
```

This command rebuilds the mapping-only analysis TSVs. It:

1. Runs `tests/drug_ontology`.
2. Resolves the latest sortable `version_*` folder for the raw inputs it uses.
3. Builds analysis processed inputs.
4. Exports the CTGov to POTTR drug mapping.
5. Exports the CTGov to Topograph drug mapping.

Outputs:

```text
data/ctgov/drug_ontology/analysis_outputs/pottr/<pottr-raw-version>/ctgov_vs_pottr_drugs.tsv
data/ctgov/drug_ontology/analysis_outputs/topograph/<topograph-raw-version>/ctgov_vs_topograph_drugs.tsv
```

Example override:

```bash
POTTR_RAW_DIR=data/ctgov/drug_ontology/raw_inputs/POTTR/version_06032026 \
make drug-ontology-analysis-tsvs
```
