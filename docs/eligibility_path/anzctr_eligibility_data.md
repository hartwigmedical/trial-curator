# ANZCTR Eligibility Path Data

ANZCTR eligibility-path inputs and outputs use the shared trial-input and
eligibility-path folders.

## Source Inputs

```text
data/trial_inputs/anzctr/input_trials/version_<ddmmyyyy>/01_initial_search_anzctr_input.xlsx
data/trial_inputs/anzctr/input_trials/version_<ddmmyyyy>/02_pottr_append_anzctr_input.xlsx
data/trial_inputs/anzctr/input_trials/version_<ddmmyyyy>/03_merged_anzctr_input.xlsx
data/trial_inputs/anzctr/extracted_trials/anzctr_field_extractions.csv
data/trial_inputs/anzctr/eligibility_curations/ACTRN*.py
```

ANZCTR trial downloads are written under
`data/trial_inputs/anzctr/input_trials/version_<ddmmyyyy>`. The dated folder
holds three workbooks:

- `01_initial_search_anzctr_input.xlsx` — the initial advanced-search cohort only.
- `02_pottr_append_anzctr_input.xlsx` — only the POTTR-appended trials (the
  delta: just the appended trials' rows across all workbook sheets). This
  changed: `02` used to be a superset of `01` (initial plus appended); it is now
  the delta only.
- `03_merged_anzctr_input.xlsx` — the union of `01` and `02`. This is the single
  authoritative input the select/extract step reads. It is regenerated after each
  download phase: an initial download sets `03` equal to `01`, and a POTTR-append
  download sets `03` equal to `01 ∪ 02`.

When multiple version folders exist, default extraction uses the newest version
folder and prefers `03_merged_anzctr_input.xlsx`, falling back to `01`+`02` for
older version folders without a merged file. The extracted trial CSV is
generated from the selected workbook(s) and includes the deterministic drug
columns. The `ACTRN*.py` files are pydantic curator outputs and are treated as
source inputs for downstream eligibility processing.

After a fresh download, any `ACTRN*.py` curation whose trial id is no longer in
the latest download (the `03_merged` set) is moved into an
`eligibility_curations/expired_trials/` subfolder, which excludes it from all
downstream processing (the curated-file glob is non-recursive). Files are moved,
not deleted, and POTTR-listed trials are never expired.

The cached whole-registry export and the download manifest are kept alongside
the dated input version:

```text
data/trial_inputs/anzctr/raw_trials/version_<ddmmyyyy>/anzctr_all_trials.zip
data/trial_inputs/anzctr/input_trials/version_<ddmmyyyy>/anzctr_download_manifest_<ddmmyyyy>.tsv
```

If `anzctr_field_extractions.csv` contains completed LLM drug-review
annotations, keep that file when cleaning generated eligibility TSVs. The drug
annotation step preserves existing LLM review columns when rerun without
`--llm_review`, but deleting the file also deletes those annotations.

## Static Resources

```text
data/eligibility_path/resources/cancer_type
data/eligibility_path/resources/gene_alteration
data/eligibility_path/resources/molecular_signature
data/eligibility_path/resources/oncotree
```

These resources are manually maintained/static and shared with CTGov where the
logic is registry-independent.

## Generated Intermediates

```text
data/eligibility_path/exports/intermediates/anzctr/cancer_type
data/eligibility_path/exports/intermediates/anzctr/gene_alteration
data/eligibility_path/exports/intermediates/anzctr/molecular_signature
```

ANZCTR cancer-type processing uses the shared CTGov/ANZCTR cancer-type
resources and shared criterion-level determination logic. A reviewed
ANZCTR-specific manual overwrite file can be passed explicitly to the
cancer-type command, but the default production pipeline does not require a
separate manual-review stage.

## Final Exports

```text
data/eligibility_path/exports/final/anzctr/trial_resource_<ddmmyyyy>.tsv
data/eligibility_path/exports/final/anzctr/cohort_resource_<ddmmyyyy>.tsv
data/eligibility_path/exports/final/eligibility_trial_resource_<ddmmyyyy>.tsv
data/eligibility_path/exports/final/eligibility_cohort_resource_<ddmmyyyy>.tsv
```

The registry-specific files are supporting exports. The top-level
`eligibility_trial_resource_<ddmmyyyy>.tsv` and
`eligibility_cohort_resource_<ddmmyyyy>.tsv` files are the combined
cross-registry final files produced by `make eligibility-path-run-all`.
