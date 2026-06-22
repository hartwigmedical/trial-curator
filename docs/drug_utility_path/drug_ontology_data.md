# Drug Ontology Data

Drug ontology data is organized into four top-level folders under:

```text
data/ctgov/drug_ontology
```

The expected folder layout is:

```text
raw_inputs
processed_inputs
pipeline_outputs
analysis_outputs
comparison_runs
```

## Trial Inputs

Registry trial inputs live outside the drug ontology domain folder because they
feed multiple downstream workflows:

```text
data/ctgov/trials/version_*
```

The CTGov trial input file is:

```text
data/ctgov/trials/version_*/ctgov_input.json
```

## Raw Inputs

`raw_inputs` is the shared read-only root for non-trial drug ontology source
files. Source files should remain in versioned folders:

```text
data/ctgov/drug_ontology/raw_inputs/RxNorm/version_*
data/ctgov/drug_ontology/raw_inputs/ATC/version_*
data/ctgov/drug_ontology/raw_inputs/FDA/version_*
data/ctgov/drug_ontology/raw_inputs/POTTR/version_*
data/ctgov/drug_ontology/raw_inputs/ChEMBL/version_*
data/ctgov/drug_ontology/raw_inputs/Topograph/version_*
```

For sources with multiple versions, scripts choose the latest sortable
`version_*` suffix unless an environment variable overrides the path. Date
suffixes are parsed as `DDMMYYYY`, so `version_01062026` sorts after
`version_29052026`. Numeric suffixes such as `version_36` sort numerically.

## Processed Inputs

`processed_inputs` contains regenerateable intermediate inputs. It is split by
workflow:

```text
data/ctgov/drug_ontology/processed_inputs/pipeline
data/ctgov/drug_ontology/processed_inputs/analysis
```

Versioned processed-input folders inherit the selected source input folder
suffix.
Pipeline processed inputs are limited to intermediates required by the production
pipeline. At the moment that is the generated ATC tree:

```text
raw_inputs/ATC/version_25042026
processed_inputs/pipeline/ATC/version_25042026/atc_tree.tsv
```

Analysis processed inputs contain regenerateable intermediates used only by
mapping/comparison work, such as CTGov unique drugs, Topograph unique drugs, and
ATC-annotated versions of those files. For example:

```text
processed_inputs/analysis/ATC/version_25042026/atc_tree.tsv
processed_inputs/analysis/ctgov/version_13022026/ctgov_unique_drugs.tsv
processed_inputs/analysis/Topograph/version_13052026/topograph_unique_drugs.tsv
```

## Pipeline Outputs

Production deliverables are written under:

```text
data/ctgov/drug_ontology/pipeline_outputs
```

These are the TSVs produced by `make drug-ontology-pipeline-tsvs`.

## Analysis Outputs

Mapping-only analysis deliverables are written under:

```text
data/ctgov/drug_ontology/analysis_outputs
```

These are the TSVs produced by `make drug-ontology-analysis-tsvs`.

## Comparison Runs

Pipeline runs archive the four generated pipeline files under:

```text
data/ctgov/drug_ontology/comparison_runs
```

Each archived run is timestamped:

```text
comparison_runs/run_YYYYMMDD_HHMMSS
```

The run folder preserves each generated file's path relative to
`data/ctgov/drug_ontology` and includes a `manifest.json` with the run time,
file paths, sizes, SHA-256 checksums, and comparison modes. The latest archived
run is recorded in:

```text
comparison_runs/latest_run.json
```

The archived generated files are:

```text
processed_inputs/pipeline/ATC/<atc-version>/atc_tree.tsv
pipeline_outputs/input_drugs_to_rxnorm_mapping_<DDMMYYYY>.tsv
pipeline_outputs/ctgov_drug_intervention_classification_summary.tsv
pipeline_outputs/ctgov_trial_drug_classification_summary.tsv
```

## Policy

- Do not edit or generate files inside `raw_inputs`.
- Do not copy processed inputs into `raw_inputs`.
- Put regenerateable intermediate files under `processed_inputs`.
- Put production TSV deliverables under `pipeline_outputs`.
- Put mapping-only comparison TSVs under `analysis_outputs`.
- Put timestamped generated-file snapshots under `comparison_runs`.
