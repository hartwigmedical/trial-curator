# Eligibility Data

Eligibility data is organized under:

```text
data/ctgov/eligibility
```

The expected top-level folders are:

```text
downloads
state
trials
resources
processed
exports
```

## Downloads And State

Each CTGov download run writes a timestamped folder under:

```text
data/ctgov/eligibility/downloads/<YYYYMMDD_HHMMSS>
```

The download folder contains:

```text
ctgov_trials_delta.json
ctgov_trials_merged.json
```

The persistent CTGov downloader state lives under:

```text
data/ctgov/eligibility/state
```

This state is what lets `make eligibility-curate-new` download only new or updated trials.

## Trials

The CTGov field extractor writes the selected drug-trial table to:

```text
data/ctgov/eligibility/trials/ctgov_field_extractions.csv
```

It also writes the review workbook:

```text
data/ctgov/eligibility/trials/ctgov_field_extractions.xlsx
```

Curated rule files live under:

```text
data/ctgov/eligibility/trials/original_curations
```

The curated folder should contain `NCT*.py` files.

## Resources

Shared eligibility resources live under:

```text
data/ctgov/eligibility/resources
```

Domain-specific resources are expected under:

```text
data/ctgov/eligibility/resources/cancer_type
data/ctgov/eligibility/resources/gene_alteration
data/ctgov/eligibility/resources/molecular_signature
```

The cancer-type pipeline also expects:

```text
data/ctgov/eligibility/resources/oncotree.csv
```

## Processed Outputs

Regenerateable criteria outputs are written under:

```text
data/ctgov/eligibility/processed/cancer_type
data/ctgov/eligibility/processed/gene_alteration
data/ctgov/eligibility/processed/molecular_signature
```

The final trial-level processed files used by the trial-resource export are:

```text
processed/cancer_type/04_trial_level_cancer_type.tsv
processed/gene_alteration/04_trial_level_gene_alteration.tsv
processed/molecular_signature/03_trial_level_molecular_signature.tsv
```

The final cohort-level processed files used by the cohort-resource export are:

```text
processed/cancer_type/05_cohort_level_cancer_type.tsv
processed/gene_alteration/05_cohort_level_gene_alteration.tsv
processed/molecular_signature/04_cohort_level_molecular_signature.tsv
```

## Exports

Eligibility resource exports are written under:

```text
data/ctgov/eligibility/exports
```

The default export names include the run date:

```text
exports/trial_resource_<DDMMYYYY>.tsv
exports/cohort_resource_<DDMMYYYY>.tsv
```

## Policy

- Keep downloaded CTGov JSON snapshots under `downloads`.
- Keep persistent downloader metadata under `state`.
- Keep selected trial tables and curated `NCT*.py` files under `trials`.
- Keep mapping and ontology resources under `resources`.
- Treat files under `processed` as regenerateable pipeline outputs.
- Treat files under `exports` as final eligibility resource deliverables.
