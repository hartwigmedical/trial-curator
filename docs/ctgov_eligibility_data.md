# CTGov Eligibility Data

CTGov eligibility data is organized under:

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

Registry-specific CTGov eligibility inputs live under:

```text
data/ctgov/eligibility/resources
```

Shared eligibility resources that apply to CTGov and ANZCTR live under:

```text
data/eligibility/resources
```

The shared cancer-type resources are expected under:

```text
data/eligibility/resources/cancer_type
data/eligibility/resources/oncotree.csv
```

The reusable cancer-type implementation lives under:

```text
aus_trial_universe/eligibility_utils/cancer_types
```

The CTGov module under `aus_trial_universe/ctgov/.../cancer_types` is now only the CTGov-specific pipeline/command surface.

The CTGov-specific gene-alteration and molecular-signature resources remain under:

```text
data/ctgov/eligibility/resources/gene_alteration
data/ctgov/eligibility/resources/molecular_signature
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

CTGov eligibility resource exports are written under:

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
- Keep shared cancer-type mapping and ontology resources under `data/eligibility/resources`.
- Keep CTGov-only mapping resources under `data/ctgov/eligibility/resources`.
- Treat files under `processed` as regenerateable pipeline outputs.
- Treat files under `exports` as final eligibility resource deliverables.
