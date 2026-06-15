# ANZCTR Eligibility Data

ANZCTR eligibility data is organized under:

```text
data/anzctr/eligibility
```

The expected top-level folders are:

```text
trials
processed
exports
```

## Trials

The selected drug-intervention trial table lives at:

```text
data/anzctr/eligibility/trials/anzctr_field_extractions.csv
```

The drug-annotated table lives at:

```text
data/anzctr/eligibility/trials/anzctr_field_extractions_w_drugs.csv
```

Curated rule files live under:

```text
data/anzctr/eligibility/trials/original_curations
```

The curated folder should contain `ACTRN*.py` files.

## Shared Cancer-Type Inputs

The ANZCTR cancer-type pipeline deliberately uses generic shared column names internally:

```text
trial_id
conditions_original
conditions_oncotree_curation
```

For ANZCTR, these map as follows:

```text
ACTRN -> trial_id
HEALTH CONDITION -> conditions_original
```

`ACTRN` values from the source CSV are normalized to full `ACTRN...` identifiers. Numeric-looking source values such as `12605000003673` become `ACTRN12605000003673`, matching the curated `ACTRN*.py` filenames.

`HEALTH CONDITION` is the ANZCTR equivalent of CTGov registry-level `conditions`: it is the trial-level cancer-type signal used alongside eligibility-derived `PrimaryTumorCriterion` values.

Shared cancer-type mapping resources live under:

```text
data/eligibility/resources/cancer_type
data/eligibility/resources/oncotree.csv
```

The conditions mapping file in `data/eligibility/resources/cancer_type` is shared by CTGov and ANZCTR even though the source registry column names differ.

The reusable cancer-type implementation lives under:

```text
aus_trial_universe/eligibility_utils/cancer_types
```

The ANZCTR module under `aus_trial_universe/anzctr/.../cancer_types` is only the ANZCTR-specific orchestration layer: it normalizes ANZCTR inputs, calls the shared cancer-type logic, and writes ANZCTR output filenames.

## Processed Cancer-Type Outputs

ANZCTR cancer-type outputs are written under:

```text
data/anzctr/eligibility/processed/cancer_type
```

The first pass writes:

```text
01_health_condition_mapping.tsv
02_primary_vs_health_condition.tsv
```

If unresolved `CHECK` relations remain, the pipeline writes:

```text
02a_primary_vs_health_condition_checks.tsv
```

Review the 02a file and fill `manual_overwrite` for ambiguous rows. The accepted values are the same as the CTGov cancer-type workflow:

```text
include both
ignore conditions
ignore primary
```

After rerunning with `--manual_overwrite_file`, the pipeline writes:

```text
03_row_level_cancer_type.tsv
04_trial_level_cancer_type.tsv
```

## Policy

- Keep selected ANZCTR trial tables and curated `ACTRN*.py` files under `trials`.
- Keep shared cancer-type mapping and ontology resources under `data/eligibility/resources`.
- Treat files under `processed` as regenerateable pipeline outputs.
- Treat files under `exports` as final eligibility resource deliverables.
