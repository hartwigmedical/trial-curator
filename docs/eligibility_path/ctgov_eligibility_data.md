# CTGov Eligibility Path Data

CTGov eligibility-path inputs and outputs use the shared trial-input and
eligibility-path folders.

## Source Inputs

```text
data/trial_inputs/ctgov/input_trials/version_<ddmmyyyy>/01_initial_search_ctgov_input.json
data/trial_inputs/ctgov/input_trials/version_<ddmmyyyy>/02_pottr_append_ctgov_input.json
data/trial_inputs/ctgov/input_trials/version_<ddmmyyyy>/03_merged_ctgov_input.json
data/trial_inputs/ctgov/download_state/ctgov_trials_latest.json
data/trial_inputs/ctgov/download_state/ctgov_trials_meta_<timestamp>.json
data/trial_inputs/ctgov/extracted_trials/ctgov_field_extractions.csv
data/trial_inputs/ctgov/eligibility_curations/NCT*.py
```

Each `version_<ddmmyyyy>` folder holds three input JSON files:

- `01_initial_search_ctgov_input.json` — the initial advanced-search cohort only.
- `02_pottr_append_ctgov_input.json` — only the POTTR-appended trials (the delta).
- `03_merged_ctgov_input.json` — the union of `01` and `02`. This is the single
  authoritative input the extract step reads (it replaces the older
  `ctgov_trials_merged.json` name). It is regenerated after each download phase:
  an initial `--all` download sets `03` equal to `01`, and a POTTR-append
  `--trial_ids` download sets `03` equal to `01 ∪ 02`.

When multiple `version_<ddmmyyyy>` folders exist, default extraction uses the
newest version folder and prefers `03_merged_ctgov_input.json`, falling back to
`01`+`02` for older version folders without a merged file. The `NCT*.py` files
are pydantic curator outputs and are treated as source inputs for the downstream
eligibility processing stages.

`data/trial_inputs/ctgov/download_state` holds the persistent download cache and
meta snapshot. `ctgov_trials_latest.json` is the persistent whole-coverage cache
and is unchanged across runs. Only the newest `ctgov_trials_meta_*.json` snapshot
is retained; older meta snapshots are pruned automatically after each
`--all`/`--incremental` download.

After a fresh download, any `NCT*.py` curation whose trial id is no longer in the
latest download (the `03_merged` set) is moved into an
`eligibility_curations/expired_trials/` subfolder, which excludes it from all
downstream processing (the curated-file glob is non-recursive). Files are moved,
not deleted, and POTTR-listed trials are never expired.

## Static Resources

```text
data/eligibility_path/resources/cancer_type
data/eligibility_path/resources/gene_alteration
data/eligibility_path/resources/molecular_signature
data/eligibility_path/resources/oncotree
```

These resources are manually maintained/static. They are read by both CTGov and
ANZCTR where applicable.

## Generated Intermediates

```text
data/eligibility_path/exports/intermediates/ctgov/cancer_type
data/eligibility_path/exports/intermediates/ctgov/gene_alteration
data/eligibility_path/exports/intermediates/ctgov/molecular_signature
```

The downstream resource exporters consume trial-level and cohort-level outputs
from these folders.

## Final Exports

```text
data/eligibility_path/exports/final/ctgov/trial_resource_<ddmmyyyy>.tsv
data/eligibility_path/exports/final/ctgov/cohort_resource_<ddmmyyyy>.tsv
data/eligibility_path/exports/final/eligibility_trial_resource_<ddmmyyyy>.tsv
data/eligibility_path/exports/final/eligibility_cohort_resource_<ddmmyyyy>.tsv
```

The registry-specific files are supporting exports. The top-level
`eligibility_trial_resource_<ddmmyyyy>.tsv` and
`eligibility_cohort_resource_<ddmmyyyy>.tsv` files are the combined
cross-registry final files produced by `make eligibility-path-run-all`.
