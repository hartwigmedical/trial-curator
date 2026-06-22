from __future__ import annotations

import argparse
import logging
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

from aus_trial_universe.eligibility_path.shared.cohorts import (
    GENERAL_COHORT,
    build_cohort_base_from_curated_rules,
    normalize_cohort_label,
)
from aus_trial_universe.eligibility_path.ctgov.ii_process_eligibility_criteria.trial_resource.trial_level_resource_pipeline import (
    DEFAULT_ELIGIBILITY_DATA_DIR,
    DEFAULT_TRIALS_FILE,
    DEFAULT_EXPORT_DIR,
    DEFAULT_EXPORT_SUFFIX_FORMAT,
    FINAL_OUTPUT_COLUMNS,
    OUTPUT_NCT_ID_COLUMN,
    _count_nonblank,
    _find_nct_id_column,
    _load_base_trials,
    _normalize_nct_id,
    _read_tabular_file,
    _require_columns,
    _resolve_path,
    _write_tabular_file,
)

LOGGER = logging.getLogger(__name__)

DEFAULT_CURATED_DIR = Path("data/trial_inputs/ctgov/eligibility_curations")

DEFAULT_CANCER_TYPE_FILE = Path("exports/intermediates/ctgov/cancer_type/04b_cancer_type_cohort_level.tsv")
DEFAULT_GENE_ALTERATION_FILE = Path("exports/intermediates/ctgov/gene_alteration/04b_gene_alteration_cohort_level.tsv")
DEFAULT_MOLECULAR_SIGNATURE_FILE = Path(
    "exports/intermediates/ctgov/molecular_signature/03b_molecular_signature_cohort_level.tsv"
)

DEFAULT_EXPORT_STEM = "cohort_resource"

COHORT_COLUMN = "cohort"

COHORT_RESOURCE_KEY_COLUMNS: Sequence[str] = (
    OUTPUT_NCT_ID_COLUMN,
    COHORT_COLUMN,
)


@dataclass(frozen=True)
class CohortResourcePipelineInputs:
    trials_file: Path
    curated_dir: Path
    cancer_type_file: Path
    gene_alteration_file: Path
    molecular_signature_file: Path
    output_file: Path
    cohort_base_file: Optional[Path] = None
    nct_id_column: Optional[str] = None
    fail_on_error: bool = False


@dataclass(frozen=True)
class CohortResourcePipelineOutputs:
    output_file: Path


# =============================================================================
# Generic helpers
# =============================================================================


def _default_export_file(
        eligibility_dir: Path,
        *,
        export_date: Optional[str] = None,
) -> Path:
    if export_date is None:
        export_date = date.today().strftime(DEFAULT_EXPORT_SUFFIX_FORMAT)

    export_date = str(export_date).strip()
    if not re.fullmatch(r"\d{8}", export_date):
        raise ValueError(
            f"export_date must be in ddmmyyyy format, got {export_date!r}"
        )

    return eligibility_dir / DEFAULT_EXPORT_DIR / f"{DEFAULT_EXPORT_STEM}_{export_date}.tsv"


def _normalize_string(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _find_column_case_insensitive(
        df: pd.DataFrame,
        candidates: Sequence[str],
        *,
        label: str,
) -> str:
    normalized_to_original = {
        re.sub(r"[^a-z0-9]+", "", str(column).casefold()): str(column)
        for column in df.columns
    }

    for candidate in candidates:
        normalized = re.sub(r"[^a-z0-9]+", "", candidate.casefold())
        if normalized in normalized_to_original:
            return normalized_to_original[normalized]

    raise ValueError(
        f"Could not find {label} column. Tried candidates: {list(candidates)}. "
        f"Available columns: {list(df.columns)}"
    )


def _cohort_sort_key(cohort: str) -> Tuple[int, str]:
    # Keep the generated '(general)' row first for each trial; preserve explicit
    # cohort order elsewhere by applying this only when we explicitly sort.
    return (0 if cohort == GENERAL_COHORT else 1, cohort)


# =============================================================================
# Cohort base construction
# =============================================================================


def _normalize_cohort_base_table(cohort_base_df: pd.DataFrame) -> pd.DataFrame:
    _require_columns(
        cohort_base_df,
        ["nct_id", COHORT_COLUMN],
        label="Cohort base table",
    )

    rows: List[Dict[str, str]] = []
    seen: set[Tuple[str, str]] = set()

    for _, row in cohort_base_df.iterrows():
        nct_id = _normalize_nct_id(row.get("nct_id", ""))
        cohort = normalize_cohort_label(row.get(COHORT_COLUMN, ""))

        if not nct_id or not cohort:
            continue

        key = (nct_id, cohort)
        if key in seen:
            continue

        seen.add(key)
        rows.append(
            {
                "_nct_id_key": nct_id,
                "_cohort_key": cohort,
                COHORT_COLUMN: cohort,
            }
        )

    return pd.DataFrame(
        rows,
        columns=["_nct_id_key", "_cohort_key", COHORT_COLUMN],
    )


def _load_or_build_cohort_base(
        *,
        curated_dir: Path,
        cohort_base_file: Optional[Path],
        fail_on_error: bool,
) -> pd.DataFrame:
    if cohort_base_file is not None:
        df = _read_tabular_file(cohort_base_file)
        df.columns = [str(column).strip() for column in df.columns]

        nct_col = _find_nct_id_column(
            df,
            "nct_id" if "nct_id" in df.columns else None,
        )
        cohort_col = _find_column_case_insensitive(
            df,
            [COHORT_COLUMN, "cohort_label", "cohort_name"],
            label="cohort",
        )

        normalized = df.rename(columns={nct_col: "nct_id", cohort_col: COHORT_COLUMN})
        out = _normalize_cohort_base_table(normalized)

        LOGGER.info(
            "Loaded cohort base table: %s rows=%d unique_trials=%d",
            cohort_base_file,
            len(out),
            out["_nct_id_key"].nunique() if not out.empty else 0,
        )
        return out

    built = build_cohort_base_from_curated_rules(
        curated_dir=curated_dir,
        fail_on_error=fail_on_error,
    )
    out = _normalize_cohort_base_table(built)

    LOGGER.info(
        "Built cohort base from curated rules: %s rows=%d unique_trials=%d",
        curated_dir,
        len(out),
        out["_nct_id_key"].nunique() if not out.empty else 0,
    )
    return out


def _base_trial_keys_in_order(base_df: pd.DataFrame) -> List[str]:
    keys: List[str] = []
    seen: set[str] = set()

    for value in base_df.get("_nct_id_key", pd.Series(dtype=str)).tolist():
        nct_id = _normalize_nct_id(value)
        if not nct_id or nct_id in seen:
            continue
        seen.add(nct_id)
        keys.append(nct_id)

    return keys


def _complete_cohort_base_for_base_trials(
        *,
        base_df: pd.DataFrame,
        cohort_base_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Return a cohort base restricted to the base trial table, with one guaranteed
    '(general)' row per base trial.

    The final cohort resource is defined on the CTGov field extraction base.
    Curated files or optional cohort-base rows for trials absent from the base
    table are ignored with a warning rather than added to the export.
    """
    base_trial_keys = _base_trial_keys_in_order(base_df)
    base_trial_key_set = set(base_trial_keys)

    cohort_lookup: Dict[str, List[str]] = {}
    extra_trial_keys: set[str] = set()

    for _, row in cohort_base_df.iterrows():
        nct_id = _normalize_nct_id(row.get("_nct_id_key", ""))
        cohort = normalize_cohort_label(row.get(COHORT_COLUMN, ""))

        if not nct_id or not cohort:
            continue

        if nct_id not in base_trial_key_set:
            extra_trial_keys.add(nct_id)
            continue

        cohort_lookup.setdefault(nct_id, [])
        if cohort not in cohort_lookup[nct_id]:
            cohort_lookup[nct_id].append(cohort)

    if extra_trial_keys:
        examples = ", ".join(sorted(extra_trial_keys)[:20])
        LOGGER.warning(
            "Cohort base contains %d trial(s) absent from the base trials file; "
            "these will not be exported. Examples: %s",
            len(extra_trial_keys),
            examples,
        )

    rows: List[Dict[str, str]] = []

    for nct_id in base_trial_keys:
        cohorts = cohort_lookup.get(nct_id, [])

        ordered_cohorts: List[str] = [GENERAL_COHORT]
        ordered_cohorts.extend(
            cohort for cohort in cohorts if cohort and cohort != GENERAL_COHORT
        )

        seen_cohorts: set[str] = set()
        for cohort in ordered_cohorts:
            if cohort in seen_cohorts:
                continue
            seen_cohorts.add(cohort)
            rows.append(
                {
                    "_nct_id_key": nct_id,
                    "_cohort_key": cohort,
                    COHORT_COLUMN: cohort,
                }
            )

    out = pd.DataFrame(
        rows,
        columns=["_nct_id_key", "_cohort_key", COHORT_COLUMN],
    )

    LOGGER.info(
        "Prepared export cohort base: rows=%d unique_trials=%d explicit_cohort_rows=%d",
        len(out),
        out["_nct_id_key"].nunique() if not out.empty else 0,
        int((out[COHORT_COLUMN] != GENERAL_COHORT).sum()) if not out.empty else 0,
    )

    return out


def _expand_base_trials_to_cohorts(
        base_df: pd.DataFrame,
        cohort_base_df: pd.DataFrame,
) -> pd.DataFrame:
    _require_columns(base_df, ["_nct_id_key"], label="Base trials table")
    _require_columns(
        cohort_base_df,
        ["_nct_id_key", "_cohort_key", COHORT_COLUMN],
        label="Cohort base table",
    )

    base_clean = base_df.drop(
        columns=[
            COHORT_COLUMN,
            "_cohort_key",
            *FINAL_OUTPUT_COLUMNS,
        ],
        errors="ignore",
    )

    expanded = base_clean.merge(
        cohort_base_df,
        how="left",
        on="_nct_id_key",
        validate="many_to_many",
    )

    missing_cohort = expanded[COHORT_COLUMN].fillna("").astype(str).str.strip() == ""
    if missing_cohort.any():
        LOGGER.warning(
            "Expanded base has %d row(s) without a cohort assignment; assigning %s",
            int(missing_cohort.sum()),
            GENERAL_COHORT,
        )
        expanded.loc[missing_cohort, COHORT_COLUMN] = GENERAL_COHORT
        expanded.loc[missing_cohort, "_cohort_key"] = GENERAL_COHORT

    expanded[COHORT_COLUMN] = expanded[COHORT_COLUMN].map(normalize_cohort_label)
    expanded["_cohort_key"] = expanded[COHORT_COLUMN].map(normalize_cohort_label)

    LOGGER.info(
        "Expanded base trials to cohort rows: base_rows=%d cohort_rows=%d unique_trials=%d",
        len(base_df),
        len(expanded),
        expanded["_nct_id_key"].replace("", pd.NA).dropna().nunique(),
    )

    return expanded


# =============================================================================
# Component loading / merging
# =============================================================================


def _load_cohort_component_table(
        path: Path,
        *,
        required_value_columns: Sequence[str],
        label: str,
) -> pd.DataFrame:
    df = _read_tabular_file(path)
    df.columns = [str(column).strip() for column in df.columns]

    nct_column = _find_nct_id_column(
        df,
        "nct_id" if "nct_id" in df.columns else None,
    )
    cohort_column = _find_column_case_insensitive(
        df,
        [COHORT_COLUMN, "cohort_label", "cohort_name"],
        label="cohort",
    )

    _require_columns(df, required_value_columns, label=label)

    out = df.copy()
    out["_nct_id_key"] = out[nct_column].map(_normalize_nct_id)
    out["_cohort_key"] = out[cohort_column].map(normalize_cohort_label)
    out = out[
        out["_nct_id_key"].ne("") & out["_cohort_key"].ne("")
        ].copy()

    duplicated = out.loc[
        out.duplicated(["_nct_id_key", "_cohort_key"], keep=False),
        ["_nct_id_key", "_cohort_key"],
    ]
    if not duplicated.empty:
        duplicate_key_rows = duplicated.drop_duplicates().to_numpy().tolist()
        examples = [
            f"{nct_id}/{cohort}"
            for nct_id, cohort in duplicate_key_rows
        ]
        raise ValueError(
            f"{label} has duplicate nct_id + cohort rows for "
            f"{len(examples)} key(s): {', '.join(examples[:20])}"
        )

    out = out.loc[:, ["_nct_id_key", "_cohort_key", *required_value_columns]]

    LOGGER.info(
        "Loaded %s: %s rows=%d unique_trials=%d unique_cohort_keys=%d rows_with_any_value=%d",
        label,
        path,
        len(out),
        out["_nct_id_key"].nunique(),
        out[["_nct_id_key", "_cohort_key"]].drop_duplicates().shape[0],
        _count_nonblank(out, required_value_columns),
    )

    return out


def _component_keys_not_in_base(
        base_df: pd.DataFrame,
        component_df: pd.DataFrame,
) -> List[Tuple[str, str]]:
    if component_df.empty:
        return []

    base_keys = set(
        map(
            tuple,
            base_df.loc[:, ["_nct_id_key", "_cohort_key"]].drop_duplicates().to_numpy(),
        )
    )
    component_keys = set(
        map(
            tuple,
            component_df.loc[:, ["_nct_id_key", "_cohort_key"]]
            .drop_duplicates()
            .to_numpy(),
        )
    )

    return sorted(component_keys - base_keys)


def _merge_cohort_component(
        base_df: pd.DataFrame,
        component_df: pd.DataFrame,
        *,
        value_columns: Sequence[str],
        label: str,
) -> pd.DataFrame:
    unmatched_component_keys = _component_keys_not_in_base(base_df, component_df)
    if unmatched_component_keys:
        examples = ", ".join(
            f"{nct_id}/{cohort}"
            for nct_id, cohort in unmatched_component_keys[:20]
        )
        LOGGER.warning(
            "%s has %d nct_id + cohort key(s) absent from the export base; "
            "these rows will not be exported. Examples: %s",
            label,
            len(unmatched_component_keys),
            examples,
        )

    base_clean = base_df.drop(
        columns=[column for column in value_columns if column in base_df.columns],
        errors="ignore",
    )

    merged = base_clean.merge(
        component_df,
        how="left",
        on=["_nct_id_key", "_cohort_key"],
        validate="many_to_one",
    )

    for column in value_columns:
        merged[column] = merged[column].fillna("").astype(str)

    matched_rows = int(
        merged.loc[:, list(value_columns)]
        .apply(lambda series: series.astype(str).str.strip() != "")
        .any(axis=1)
        .sum()
    )

    LOGGER.info(
        "Merged %s: base_rows=%d rows_with_any_%s_value=%d",
        label,
        len(merged),
        label,
        matched_rows,
    )

    return merged


def _split_component_terms(value: object, delimiter: str) -> List[str]:
    text = _normalize_string(value)
    if not text:
        return []

    return [part.strip() for part in text.split(delimiter) if part.strip()]


def _dedupe_preserve_order(values: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []

    for value in values:
        text = _normalize_string(value)
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)

    return out


def _join_component_terms(values: Iterable[str], delimiter: str) -> str:
    return delimiter.join(_dedupe_preserve_order(values))


def _inherit_general_cancer_type_terms_into_explicit_cohorts(
        df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Ensure explicit cohort cancer-type values inherit the trial's general
    cancer-type terms after the cancer-type component has been merged.

    This is intentionally final-resource and cancer-type specific. It fixes
    rows that exist in the full cohort base but are absent from the sparse
    cancer-type component table, while leaving gene alteration, molecular
    signature, row keys, and row counts unchanged.
    """
    required = [
        "_nct_id_key",
        COHORT_COLUMN,
        "cancer_type_inclusive",
        "cancer_type_exclusive",
    ]
    _require_columns(
        df,
        required,
        label="Cohort resource table before cancer-type inheritance",
    )

    out = df.copy()

    general_rows = out.loc[
        out[COHORT_COLUMN].eq(GENERAL_COHORT),
        [
            "_nct_id_key",
            "cancer_type_inclusive",
            "cancer_type_exclusive",
        ],
    ].copy()

    if general_rows.empty:
        return out

    general_by_trial = general_rows.set_index("_nct_id_key", drop=False)
    changed_rows = 0

    explicit_mask = ~out[COHORT_COLUMN].eq(GENERAL_COHORT)
    for idx, row in out.loc[explicit_mask].iterrows():
        nct_id = _normalize_nct_id(row.get("_nct_id_key", ""))
        if not nct_id or nct_id not in general_by_trial.index:
            continue

        general = general_by_trial.loc[nct_id]

        current_inclusive = _split_component_terms(
            row.get("cancer_type_inclusive", ""),
            " | ",
        )
        general_inclusive = _split_component_terms(
            general.get("cancer_type_inclusive", ""),
            " | ",
        )

        current_exclusive = _split_component_terms(
            row.get("cancer_type_exclusive", ""),
            " & ",
        )
        general_exclusive = _split_component_terms(
            general.get("cancer_type_exclusive", ""),
            " & ",
        )

        new_inclusive = _join_component_terms(
            [*current_inclusive, *general_inclusive],
            " | ",
        )
        new_exclusive = _join_component_terms(
            [*current_exclusive, *general_exclusive],
            " & ",
        )

        if (
                new_inclusive != _normalize_string(row.get("cancer_type_inclusive", ""))
                or new_exclusive != _normalize_string(row.get("cancer_type_exclusive", ""))
        ):
            changed_rows += 1
            out.at[idx, "cancer_type_inclusive"] = new_inclusive
            out.at[idx, "cancer_type_exclusive"] = new_exclusive

    if changed_rows:
        LOGGER.info(
            "Inherited general cancer-type terms into %d explicit cohort row(s)",
            changed_rows,
        )

    return out


def _order_output_columns(out: pd.DataFrame) -> pd.DataFrame:
    base_columns = [
        column
        for column in out.columns
        if column not in {"_nct_id_key", "_cohort_key", *FINAL_OUTPUT_COLUMNS}
           and column != COHORT_COLUMN
    ]

    if OUTPUT_NCT_ID_COLUMN in base_columns:
        insert_at = base_columns.index(OUTPUT_NCT_ID_COLUMN) + 1
    else:
        insert_at = 0

    ordered_base_columns = list(base_columns)
    ordered_base_columns.insert(insert_at, COHORT_COLUMN)

    return out.loc[:, [*ordered_base_columns, *FINAL_OUTPUT_COLUMNS]]


# =============================================================================
# Public table builder
# =============================================================================


def build_cohort_resource_table(
        *,
        trials_file: Path,
        curated_dir: Path,
        cancer_type_file: Path,
        gene_alteration_file: Path,
        molecular_signature_file: Path,
        cohort_base_file: Optional[Path] = None,
        nct_id_column: Optional[str] = None,
        fail_on_error: bool = False,
) -> pd.DataFrame:
    base_df = _load_base_trials(
        trials_file,
        nct_id_column=nct_id_column,
    )

    raw_cohort_base_df = _load_or_build_cohort_base(
        curated_dir=curated_dir,
        cohort_base_file=cohort_base_file,
        fail_on_error=fail_on_error,
    )
    cohort_base_df = _complete_cohort_base_for_base_trials(
        base_df=base_df,
        cohort_base_df=raw_cohort_base_df,
    )

    out = _expand_base_trials_to_cohorts(base_df, cohort_base_df)

    cancer_type_df = _load_cohort_component_table(
        cancer_type_file,
        required_value_columns=(
            "cancer_type_inclusive",
            "cancer_type_exclusive",
        ),
        label="cancer_type",
    )

    gene_alteration_df = _load_cohort_component_table(
        gene_alteration_file,
        required_value_columns=(
            "gene_alteration_inclusive",
            "gene_alteration_exclusive",
        ),
        label="gene_alteration",
    )

    molecular_signature_df = _load_cohort_component_table(
        molecular_signature_file,
        required_value_columns=(
            "molecular_signature_inclusive",
            "molecular_signature_exclusive",
        ),
        label="molecular_signature",
    )

    out = _merge_cohort_component(
        out,
        cancer_type_df,
        value_columns=("cancer_type_inclusive", "cancer_type_exclusive"),
        label="cancer_type",
    )

    out = _inherit_general_cancer_type_terms_into_explicit_cohorts(out)

    out = _merge_cohort_component(
        out,
        gene_alteration_df,
        value_columns=("gene_alteration_inclusive", "gene_alteration_exclusive"),
        label="gene_alteration",
    )

    out = _merge_cohort_component(
        out,
        molecular_signature_df,
        value_columns=("molecular_signature_inclusive", "molecular_signature_exclusive"),
        label="molecular_signature",
    )

    out = _order_output_columns(out)

    output_nct_col = (
        OUTPUT_NCT_ID_COLUMN
        if OUTPUT_NCT_ID_COLUMN in out.columns
        else "nct_id"
    )

    duplicate_keys = out.loc[
        out.duplicated([output_nct_col, COHORT_COLUMN], keep=False),
        [output_nct_col, COHORT_COLUMN],
    ]
    if not duplicate_keys.empty:
        examples = [
            f"{getattr(row, output_nct_col)}/{getattr(row, COHORT_COLUMN)}"
            for row in duplicate_keys.drop_duplicates().itertuples(index=False)
        ]
        LOGGER.warning(
            "Final cohort resource has duplicate nctId + cohort rows for %d key(s). "
            "Examples: %s",
            len(examples),
            ", ".join(examples[:20]),
        )

    LOGGER.info(
        "Built cohort resource table: rows=%d columns=%d unique_trials=%d unique_cohort_keys=%d",
        len(out),
        len(out.columns),
        out[output_nct_col]
        .map(_normalize_nct_id)
        .replace("", pd.NA)
        .dropna()
        .nunique()
        if output_nct_col in out.columns
        else 0,
        out[[output_nct_col, COHORT_COLUMN]].drop_duplicates().shape[0]
        if {output_nct_col, COHORT_COLUMN}.issubset(out.columns)
        else 0,
    )

    return out


# =============================================================================
# Pipeline orchestration
# =============================================================================


def discover_pipeline_inputs(
        *,
        repo_root: Path,
        eligibility_data_dir: Path,
        trials_file: Optional[Path],
        curated_dir: Optional[Path],
        cancer_type_file: Optional[Path],
        gene_alteration_file: Optional[Path],
        molecular_signature_file: Optional[Path],
        cohort_base_file: Optional[Path],
        output_file: Optional[Path],
        export_date: Optional[str],
        nct_id_column: Optional[str],
        fail_on_error: bool,
) -> CohortResourcePipelineInputs:
    repo_root = repo_root.resolve()
    eligibility_dir = _resolve_path(eligibility_data_dir, repo_root)

    resolved_trials_file = _resolve_path(
        trials_file if trials_file is not None else DEFAULT_TRIALS_FILE,
        repo_root,
    )

    resolved_curated_dir = _resolve_path(
        curated_dir if curated_dir is not None else DEFAULT_CURATED_DIR,
        repo_root,
    )

    resolved_cancer_type_file = _resolve_path(
        cancer_type_file
        if cancer_type_file is not None
        else eligibility_dir / DEFAULT_CANCER_TYPE_FILE,
        repo_root,
    )

    resolved_gene_alteration_file = _resolve_path(
        gene_alteration_file
        if gene_alteration_file is not None
        else eligibility_dir / DEFAULT_GENE_ALTERATION_FILE,
        repo_root,
    )

    resolved_molecular_signature_file = _resolve_path(
        molecular_signature_file
        if molecular_signature_file is not None
        else eligibility_dir / DEFAULT_MOLECULAR_SIGNATURE_FILE,
        repo_root,
    )

    resolved_cohort_base_file = (
        _resolve_path(cohort_base_file, repo_root)
        if cohort_base_file is not None
        else None
    )

    resolved_output_file = _resolve_path(
        output_file
        if output_file is not None
        else _default_export_file(
            eligibility_dir,
            export_date=export_date,
        ),
        repo_root,
    )

    inputs = CohortResourcePipelineInputs(
        trials_file=resolved_trials_file,
        curated_dir=resolved_curated_dir,
        cancer_type_file=resolved_cancer_type_file,
        gene_alteration_file=resolved_gene_alteration_file,
        molecular_signature_file=resolved_molecular_signature_file,
        output_file=resolved_output_file,
        cohort_base_file=resolved_cohort_base_file,
        nct_id_column=nct_id_column,
        fail_on_error=fail_on_error,
    )

    validate_pipeline_inputs(inputs)
    return inputs


def validate_pipeline_inputs(inputs: CohortResourcePipelineInputs) -> None:
    required_files = [
        ("base trials file", inputs.trials_file),
        ("cohort-level cancer type file", inputs.cancer_type_file),
        ("cohort-level gene alteration file", inputs.gene_alteration_file),
        ("cohort-level molecular signature file", inputs.molecular_signature_file),
    ]

    for label, path in required_files:
        if not path.exists():
            raise FileNotFoundError(f"{label} does not exist: {path}")
        if not path.is_file():
            raise ValueError(f"{label} is not a file: {path}")

    if inputs.cohort_base_file is None:
        if not inputs.curated_dir.exists():
            raise FileNotFoundError(f"curated rules directory does not exist: {inputs.curated_dir}")
        if not inputs.curated_dir.is_dir() and not inputs.curated_dir.is_file():
            raise ValueError(f"curated rules path is not a directory or file: {inputs.curated_dir}")
    else:
        if not inputs.cohort_base_file.exists():
            raise FileNotFoundError(f"cohort base file does not exist: {inputs.cohort_base_file}")
        if not inputs.cohort_base_file.is_file():
            raise ValueError(f"cohort base path is not a file: {inputs.cohort_base_file}")

    if inputs.output_file.suffix.casefold() != ".tsv":
        raise ValueError(
            f"Cohort resource export must be a TSV file. Got: {inputs.output_file}"
        )


def run_cohort_resource_pipeline(
        inputs: CohortResourcePipelineInputs,
) -> CohortResourcePipelineOutputs:
    LOGGER.info("Cohort-resource pipeline inputs:")
    LOGGER.info("  trials_file:                %s", inputs.trials_file)
    LOGGER.info("  curated_dir:                %s", inputs.curated_dir)
    LOGGER.info("  cohort_base_file:           %s", inputs.cohort_base_file)
    LOGGER.info("  cancer_type_file:           %s", inputs.cancer_type_file)
    LOGGER.info("  gene_alteration_file:       %s", inputs.gene_alteration_file)
    LOGGER.info("  molecular_signature_file:   %s", inputs.molecular_signature_file)
    LOGGER.info("  output_file:                %s", inputs.output_file)
    LOGGER.info("  nct_id_column:              %s", inputs.nct_id_column)
    LOGGER.info("  fail_on_error:              %s", inputs.fail_on_error)

    cohort_resource_df = build_cohort_resource_table(
        trials_file=inputs.trials_file,
        curated_dir=inputs.curated_dir,
        cancer_type_file=inputs.cancer_type_file,
        gene_alteration_file=inputs.gene_alteration_file,
        molecular_signature_file=inputs.molecular_signature_file,
        cohort_base_file=inputs.cohort_base_file,
        nct_id_column=inputs.nct_id_column,
        fail_on_error=inputs.fail_on_error,
    )

    _write_tabular_file(cohort_resource_df, inputs.output_file)
    LOGGER.info(
        "Wrote cohort resource export: %s rows=%d",
        inputs.output_file,
        len(cohort_resource_df),
    )

    return CohortResourcePipelineOutputs(output_file=inputs.output_file)


# =============================================================================
# CLI
# =============================================================================


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build the final CTGov cohort-level resource by expanding the base "
            "CTGov field extraction table to nct_id + cohort rows, then merging "
            "the reviewed cohort-level cancer type, gene alteration, and "
            "molecular signature outputs. The pipeline writes one dated TSV export."
        )
    )

    parser.add_argument(
        "--repo_root",
        type=Path,
        default=Path.cwd(),
        help="Repository root. Defaults to current working directory.",
    )

    parser.add_argument(
        "--eligibility_data_dir",
        type=Path,
        default=DEFAULT_ELIGIBILITY_DATA_DIR,
        help="Eligibility path data root. Defaults to data/eligibility_path.",
    )

    parser.add_argument(
        "--trials_file",
        type=Path,
        default=None,
        help=(
            "Base CTGov field extraction table. Defaults to "
            "data/trial_inputs/ctgov/extracted_trials/ctgov_field_extractions.csv."
        ),
    )

    parser.add_argument(
        "--curated_dir",
        type=Path,
        default=None,
        help=(
            "Curated NCT*.py rules path used to build the cohort base when "
            "--cohort_base_file is not supplied. Defaults to "
            "data/trial_inputs/ctgov/eligibility_curations."
        ),
    )

    parser.add_argument(
        "--cohort_base_file",
        type=Path,
        default=None,
        help=(
            "Optional prebuilt cohort base table with nct_id and cohort columns. "
            "If omitted, the table is built from Rule.cohorts in curated_dir."
        ),
    )

    parser.add_argument(
        "--cancer_type_file",
        type=Path,
        default=None,
        help=(
            "Cohort-level cancer type file. Defaults to "
            "data/eligibility_path/exports/intermediates/ctgov/cancer_type/04b_cancer_type_cohort_level.tsv."
        ),
    )

    parser.add_argument(
        "--gene_alteration_file",
        type=Path,
        default=None,
        help=(
            "Cohort-level gene alteration file. Defaults to "
            "data/eligibility_path/exports/intermediates/ctgov/gene_alteration/04b_gene_alteration_cohort_level.tsv."
        ),
    )

    parser.add_argument(
        "--molecular_signature_file",
        type=Path,
        default=None,
        help=(
            "Cohort-level molecular signature file. Defaults to "
            "data/eligibility_path/exports/intermediates/ctgov/molecular_signature/03b_molecular_signature_cohort_level.tsv."
        ),
    )

    parser.add_argument(
        "--output_file",
        type=Path,
        default=None,
        help=(
            "Output TSV export path. Defaults to "
            "data/eligibility_path/exports/final/ctgov/cohort_resource_<ddmmyyyy>.tsv."
        ),
    )

    parser.add_argument(
        "--export_date",
        default=None,
        help=(
            "Date suffix for the default export filename, in ddmmyyyy format. "
            "Defaults to today's date."
        ),
    )

    parser.add_argument(
        "--nct_id_column",
        default=None,
        help="Optional explicit NCT ID column in the base trials file.",
    )

    parser.add_argument(
        "--fail_on_error",
        action="store_true",
        help="Fail if any curated NCT*.py file cannot be loaded while building the cohort base.",
    )

    parser.add_argument(
        "--log_level",
        default="INFO",
        help="Logging level.",
    )

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    inputs = discover_pipeline_inputs(
        repo_root=args.repo_root,
        eligibility_data_dir=args.eligibility_data_dir,
        trials_file=args.trials_file,
        curated_dir=args.curated_dir,
        cancer_type_file=args.cancer_type_file,
        gene_alteration_file=args.gene_alteration_file,
        molecular_signature_file=args.molecular_signature_file,
        cohort_base_file=args.cohort_base_file,
        output_file=args.output_file,
        export_date=args.export_date,
        nct_id_column=args.nct_id_column,
        fail_on_error=bool(args.fail_on_error),
    )

    outputs = run_cohort_resource_pipeline(inputs)

    LOGGER.info("Cohort-resource pipeline complete.")
    LOGGER.info("output_file:   %s", outputs.output_file)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
