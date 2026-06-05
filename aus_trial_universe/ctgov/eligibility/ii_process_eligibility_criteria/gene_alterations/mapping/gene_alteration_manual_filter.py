from __future__ import annotations

import argparse
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, FrozenSet, Iterable, Iterator, List, Optional, Sequence, Tuple

import pandas as pd

from aus_trial_universe.ctgov.eligibility.shared.general.load_curated_rules import (
    load_curated_rules,
)
from aus_trial_universe.ctgov.eligibility.ii_process_eligibility_criteria.cohort_utils import (
    build_cohort_base_from_curated_rules,
    expand_to_effective_cohort_rows,
    serialize_rule_cohorts,
)
from aus_trial_universe.ctgov.eligibility.shared.general.text_normalisation import (
    clean_cell_str,
    is_effectively_empty,
)

LOGGER = logging.getLogger(__name__)

REQUIRED_MANUAL_COLUMNS: Sequence[str] = (
    "TrialId",
    "rule_text",
    "input_text",
    "accept_reject",
    "manual_overwrite_mappingStatus",
)

TRIAL_LEVEL_OUTPUT_COLUMNS: Sequence[str] = (
    "TrialId",
    "source_file",
    "rule_index",
    "criterion_index",
    "criterion_path",
    "rule_text",
    "exclusive_rule",
    "cohorts",
    "under_not_criterion",
    "input_text",
    "gene_input",
    "alteration_input",
    "variant_input",
    "description_input",
    "manual_filter_action",
    "manual_filter_reason",
)

COHORT_LEVEL_OUTPUT_COLUMNS: Sequence[str] = (
    "TrialId",
    "cohort",
    "source_file",
    "rule_index",
    "criterion_index",
    "criterion_path",
    "rule_text",
    "exclusive_rule",
    "cohorts",
    "under_not_criterion",
    "input_text",
    "gene_input",
    "alteration_input",
    "variant_input",
    "description_input",
    "manual_filter_action",
    "manual_filter_reason",
)

# Backward-compatible alias for existing imports/tests.
OUTPUT_COLUMNS: Sequence[str] = TRIAL_LEVEL_OUTPUT_COLUMNS


@dataclass(frozen=True)
class ManualRejectKey:
    trial_id: str
    rule_text: str
    input_text: str


@dataclass(frozen=True)
class GeneAlterationCriterionRecord:
    TrialId: str
    source_file: str
    rule_index: int
    criterion_index: int
    criterion_path: str
    rule_text: str
    exclusive_rule: bool
    cohorts: str
    under_not_criterion: bool
    input_text: str
    gene_input: str
    alteration_input: str
    variant_input: str
    description_input: str
    manual_filter_action: str
    manual_filter_reason: str


# =============================================================================
# Generic helpers
# =============================================================================


def _normalize_string(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _display_cell(value: object) -> str:
    if value is None or is_effectively_empty(value):
        return ""
    return clean_cell_str(value)


def _normalize_key_text(value: object) -> str:
    text = _display_cell(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\s+", " ", text).strip()
    return text.casefold()


def _normalize_trial_id(value: object) -> str:
    return _normalize_string(value).upper()


def _read_tabular_file(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()

    if suffix == ".csv":
        return pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[])

    if suffix == ".tsv":
        return pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False, na_values=[])

    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path, dtype=str, keep_default_na=False, na_values=[])

    raise ValueError(f"Unsupported input file type: {path.suffix}")


def _write_tabular_file(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()

    if suffix == ".csv":
        df.to_csv(path, index=False)
        return

    if suffix == ".tsv":
        df.to_csv(path, sep="\t", index=False)
        return

    if suffix in {".xlsx", ".xls"}:
        df.to_excel(path, index=False)
        return

    raise ValueError(f"Unsupported output file type: {path.suffix}")


def _find_column_case_insensitive(columns: Sequence[str], target: str) -> str:
    target_norm = target.strip().casefold()
    for column in columns:
        if str(column).strip().casefold() == target_norm:
            return str(column)
    raise ValueError(f"Could not find column {target!r}. Found columns: {list(columns)}")


def _safe_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    text = _normalize_string(value).casefold()
    return text in {"true", "1", "yes", "y"}


def _iter_input_py_files(input_dir: Path) -> Iterable[Path]:
    if input_dir.is_file():
        yield input_dir
        return

    yield from sorted(path for path in input_dir.glob("NCT*.py") if path.is_file())


def _type_name(obj: Any) -> str:
    return type(obj).__name__


def _is_gene_alteration_node(node: Any) -> bool:
    return _type_name(node) == "GeneAlterationCriterion"


def _is_not_node(node: Any) -> bool:
    return _type_name(node) == "NotCriterion"


def _iter_child_objects(obj: Any) -> Iterable[Tuple[str, Any]]:
    if obj is None or isinstance(obj, (str, bytes, int, float, bool)):
        return

    if isinstance(obj, dict):
        for key, value in obj.items():
            yield str(key), value
        return

    if isinstance(obj, (list, tuple, set)):
        for idx, value in enumerate(obj):
            yield f"[{idx}]", value
        return

    attrs = getattr(obj, "__dict__", None)
    if isinstance(attrs, dict):
        for key, value in attrs.items():
            if key.startswith("_"):
                continue
            yield str(key), value


# =============================================================================
# Manual reject/not_mapped key loading
# =============================================================================


def make_manual_reject_key(
    *,
    trial_id: object,
    rule_text: object,
    input_text: object,
) -> ManualRejectKey:
    return ManualRejectKey(
        trial_id=_normalize_trial_id(trial_id),
        rule_text=_normalize_key_text(rule_text),
        input_text=_normalize_key_text(input_text),
    )


def load_manual_reject_not_mapped_keys(manual_overwrite_file: Path) -> FrozenSet[ManualRejectKey]:
    df = _read_tabular_file(manual_overwrite_file)
    df.columns = [str(column).strip() for column in df.columns]

    column_map = {
        required: _find_column_case_insensitive(df.columns.tolist(), required)
        for required in REQUIRED_MANUAL_COLUMNS
    }

    reject_keys: set[ManualRejectKey] = set()

    for _, row in df.iterrows():
        accept_reject = _normalize_string(row.get(column_map["accept_reject"], "")).casefold()
        mapping_status = _normalize_string(
            row.get(column_map["manual_overwrite_mappingStatus"], "")
        ).casefold()

        if accept_reject != "reject" or mapping_status != "not_mapped":
            continue

        key = make_manual_reject_key(
            trial_id=row.get(column_map["TrialId"], ""),
            rule_text=row.get(column_map["rule_text"], ""),
            input_text=row.get(column_map["input_text"], ""),
        )

        if not key.trial_id or not key.rule_text or not key.input_text:
            LOGGER.warning(
                "Skipping incomplete manual reject/not_mapped key: trial_id=%r rule_text=%r input_text=%r",
                row.get(column_map["TrialId"], ""),
                row.get(column_map["rule_text"], ""),
                row.get(column_map["input_text"], ""),
            )
            continue

        reject_keys.add(key)

    LOGGER.info(
        "Loaded %d unique manual reject/not_mapped key(s) from %s",
        len(reject_keys),
        manual_overwrite_file,
    )
    return frozenset(reject_keys)


# =============================================================================
# GeneAlterationCriterion traversal and key derivation
# =============================================================================


def iter_gene_alteration_nodes(rule: Any) -> Iterator[Tuple[Any, bool, str]]:
    """
    Yield (node, under_not_criterion, criterion_path) for every
    GeneAlterationCriterion under a loaded Rule object.

    under_not_criterion reflects syntactic nesting under NotCriterion only.
    Rule.exclude is recorded separately.
    """
    seen_ids: set[int] = set()

    def walk(obj: Any, *, under_not: bool, path: str) -> Iterator[Tuple[Any, bool, str]]:
        if obj is None or isinstance(obj, (str, bytes, int, float, bool)):
            return

        obj_id = id(obj)
        if obj_id in seen_ids:
            return
        seen_ids.add(obj_id)

        next_under_not = under_not or _is_not_node(obj)

        if _is_gene_alteration_node(obj):
            yield obj, under_not, path
            return

        for child_name, child in _iter_child_objects(obj):
            child_path = f"{path}.{child_name}" if path else child_name
            yield from walk(child, under_not=next_under_not, path=child_path)

    yield from walk(rule, under_not=False, path="rule")


def derive_input_text_from_node(node: Any) -> str:
    """
    Reproduce the manual-overwrite input_text key for a GeneAlterationCriterion.

    The review/manual workbook keys are description-like free text. In normal
    extracted criteria this is node.description. If description is missing, fall
    back to a stable pipe-joined representation of the scalar criterion fields so
    the record remains auditable.
    """
    description = _display_cell(getattr(node, "description", ""))
    if description:
        return description

    parts = [
        _display_cell(getattr(node, "gene", "")),
        _display_cell(getattr(node, "alteration", "")),
        _display_cell(getattr(node, "variant", "")),
    ]
    parts = [part for part in parts if part]
    return " | ".join(parts)


def should_drop_gene_alteration_criterion(
    *,
    trial_id: str,
    rule_text: str,
    input_text: str,
    reject_keys: FrozenSet[ManualRejectKey],
) -> bool:
    key = make_manual_reject_key(
        trial_id=trial_id,
        rule_text=rule_text,
        input_text=input_text,
    )
    return key in reject_keys


def extract_filter_records_from_rules(
    *,
    trial_id: str,
    source_file: str,
    rules: Sequence[Any],
    reject_keys: FrozenSet[ManualRejectKey],
) -> List[GeneAlterationCriterionRecord]:
    records: List[GeneAlterationCriterionRecord] = []
    criterion_counter = 0

    for rule_index, rule in enumerate(rules, start=1):
        rule_text = _display_cell(getattr(rule, "rule_text", ""))
        rule_exclude = _safe_bool(getattr(rule, "exclude", False))
        cohorts = serialize_rule_cohorts(rule)

        for node, under_not, path in iter_gene_alteration_nodes(rule):
            criterion_counter += 1

            input_text = derive_input_text_from_node(node)
            should_drop = should_drop_gene_alteration_criterion(
                trial_id=trial_id,
                rule_text=rule_text,
                input_text=input_text,
                reject_keys=reject_keys,
            )

            records.append(
                GeneAlterationCriterionRecord(
                    TrialId=trial_id,
                    source_file=source_file,
                    rule_index=rule_index,
                    criterion_index=criterion_counter,
                    criterion_path=path,
                    rule_text=rule_text,
                    exclusive_rule=rule_exclude,
                    cohorts=cohorts,
                    under_not_criterion=under_not,
                    input_text=input_text,
                    gene_input=_display_cell(getattr(node, "gene", "")),
                    alteration_input=_display_cell(getattr(node, "alteration", "")),
                    variant_input=_display_cell(getattr(node, "variant", "")),
                    description_input=_display_cell(getattr(node, "description", "")),
                    manual_filter_action="drop" if should_drop else "keep",
                    manual_filter_reason="reject_not_mapped" if should_drop else "",
                )
            )

    return records


def extract_filter_records_from_file(
    *,
    file_path: Path,
    reject_keys: FrozenSet[ManualRejectKey],
) -> List[GeneAlterationCriterionRecord]:
    rules = load_curated_rules(file_path)
    if rules is None:
        LOGGER.warning("Skipping %s because load_curated_rules returned None", file_path)
        return []

    if not isinstance(rules, Sequence) or isinstance(rules, (str, bytes)):
        LOGGER.warning("Skipping %s because loaded rules are not a sequence", file_path)
        return []

    return extract_filter_records_from_rules(
        trial_id=file_path.stem.upper(),
        source_file=file_path.name,
        rules=rules,
        reject_keys=reject_keys,
    )


# =============================================================================
# Public report API
# =============================================================================


def build_manual_filter_report(
    *,
    input_dir: Path,
    manual_overwrite_file: Path,
    fail_on_error: bool = False,
) -> pd.DataFrame:
    reject_keys = load_manual_reject_not_mapped_keys(manual_overwrite_file)

    rows: List[GeneAlterationCriterionRecord] = []
    skipped_files = 0

    py_files = list(_iter_input_py_files(input_dir))
    LOGGER.info("Found %d curated trial Python file(s) in %s", len(py_files), input_dir)

    for file_path in py_files:
        try:
            file_rows = extract_filter_records_from_file(
                file_path=file_path,
                reject_keys=reject_keys,
            )
        except Exception as exc:
            if fail_on_error:
                raise
            skipped_files += 1
            LOGGER.exception("Skipping %s due to error: %s", file_path, exc)
            continue

        rows.extend(file_rows)

    if skipped_files:
        LOGGER.warning("Skipped %d file(s) due to processing errors", skipped_files)

    df = pd.DataFrame([asdict(row) for row in rows])

    for column in OUTPUT_COLUMNS:
        if column not in df.columns:
            df[column] = ""

    df = df.loc[:, list(OUTPUT_COLUMNS)]

    LOGGER.info(
        "Built manual filter report: rows=%d keep=%d drop=%d",
        len(df),
        int((df["manual_filter_action"] == "keep").sum()) if not df.empty else 0,
        int((df["manual_filter_action"] == "drop").sum()) if not df.empty else 0,
    )

    return df


def _cohort_base_with_trial_id_column(cohort_base_df: pd.DataFrame) -> pd.DataFrame:
    """Return cohort base table with TrialId/cohort columns for manual-filter expansion."""
    if "TrialId" in cohort_base_df.columns:
        return cohort_base_df.copy()

    if "nct_id" not in cohort_base_df.columns:
        raise ValueError(
            "Cohort base table must contain either 'nct_id' or 'TrialId'. "
            f"Found columns: {list(cohort_base_df.columns)}"
        )

    return cohort_base_df.rename(columns={"nct_id": "TrialId"}).copy()


def build_manual_filter_cohort_level_report(
    *,
    trial_level_df: pd.DataFrame,
    input_dir: Optional[Path] = None,
    cohort_base_df: Optional[pd.DataFrame] = None,
    fail_on_error: bool = False,
) -> pd.DataFrame:
    """
    Expand the trial/raw manual-filter report to effective nct_id + cohort rows.

    Input convention:
      - blank ``cohorts`` means the source Rule is general/trial-wide
      - nonblank ``cohorts`` contains serialized Rule.cohorts labels

    Output convention:
      - ``cohort == "(general)"`` receives only general rows
      - each explicit cohort receives general rows plus its own explicit rows

    ``trial_level_df`` is usually the output of build_manual_filter_report().
    """
    for column in TRIAL_LEVEL_OUTPUT_COLUMNS:
        if column not in trial_level_df.columns:
            trial_level_df[column] = ""

    trial_level_df = trial_level_df.loc[:, list(TRIAL_LEVEL_OUTPUT_COLUMNS)].copy()

    if cohort_base_df is None:
        if input_dir is None:
            raise ValueError(
                "Either input_dir or cohort_base_df must be supplied to build "
                "the cohort-level manual filter report."
            )
        cohort_base_df = build_cohort_base_from_curated_rules(
            curated_dir=input_dir,
            fail_on_error=fail_on_error,
        )

    cohort_base_for_manual = _cohort_base_with_trial_id_column(cohort_base_df)

    expanded = expand_to_effective_cohort_rows(
        trial_level_df,
        cohort_base_for_manual,
        nct_col="TrialId",
        cohorts_col="cohorts",
        cohort_col="cohort",
    )

    for column in COHORT_LEVEL_OUTPUT_COLUMNS:
        if column not in expanded.columns:
            expanded[column] = ""

    expanded = expanded.loc[:, list(COHORT_LEVEL_OUTPUT_COLUMNS)]

    LOGGER.info(
        "Built cohort-level manual filter report: rows=%d keep=%d drop=%d",
        len(expanded),
        int((expanded["manual_filter_action"] == "keep").sum()) if not expanded.empty else 0,
        int((expanded["manual_filter_action"] == "drop").sum()) if not expanded.empty else 0,
    )

    return expanded


def build_manual_filter_reports(
    *,
    input_dir: Path,
    manual_overwrite_file: Path,
    fail_on_error: bool = False,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Return (trial_level_df, cohort_level_df) manual-filter reports."""
    trial_level_df = build_manual_filter_report(
        input_dir=input_dir,
        manual_overwrite_file=manual_overwrite_file,
        fail_on_error=fail_on_error,
    )
    cohort_level_df = build_manual_filter_cohort_level_report(
        trial_level_df=trial_level_df,
        input_dir=input_dir,
        fail_on_error=fail_on_error,
    )
    return trial_level_df, cohort_level_df


# =============================================================================
# CLI
# =============================================================================


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build the first-stage GeneAlterationCriterion manual filter report. "
            "Criteria whose TrialId + rule_text + input_text match manual overwrite "
            "rows with accept_reject=reject and manual_overwrite_mappingStatus=not_mapped "
            "are marked drop."
        )
    )
    parser.add_argument(
        "--input_dir",
        required=True,
        type=Path,
        help="Directory containing original curated NCT*.py files, or a single NCT*.py file.",
    )
    parser.add_argument(
        "--manual_overwrite_file",
        required=True,
        type=Path,
        help="Manual overwrite workbook/CSV/TSV containing reject/not_mapped decisions.",
    )
    parser.add_argument(
        "--output_file",
        required=True,
        type=Path,
        help="Trial-level/raw output report path: .tsv, .csv, .xlsx, or .xls.",
    )
    parser.add_argument(
        "--cohort_output_file",
        required=False,
        type=Path,
        default=None,
        help=(
            "Optional cohort-level/effective output report path. If supplied, "
            "writes the general-plus-cohort-expanded manual-filter report."
        ),
    )
    parser.add_argument(
        "--fail_on_error",
        action="store_true",
        help="Raise immediately on the first curated rule file that fails to load or parse.",
    )
    parser.add_argument(
        "--log_level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
    )

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    trial_level_df = build_manual_filter_report(
        input_dir=args.input_dir,
        manual_overwrite_file=args.manual_overwrite_file,
        fail_on_error=args.fail_on_error,
    )

    LOGGER.info("Writing trial-level manual filter report: %s", args.output_file)
    _write_tabular_file(trial_level_df, args.output_file)
    LOGGER.info("Done. Wrote %d row(s) to %s", len(trial_level_df), args.output_file)

    if args.cohort_output_file is not None:
        cohort_level_df = build_manual_filter_cohort_level_report(
            trial_level_df=trial_level_df,
            input_dir=args.input_dir,
            fail_on_error=args.fail_on_error,
        )
        LOGGER.info(
            "Writing cohort-level manual filter report: %s",
            args.cohort_output_file,
        )
        _write_tabular_file(cohort_level_df, args.cohort_output_file)
        LOGGER.info(
            "Done. Wrote %d row(s) to %s",
            len(cohort_level_df),
            args.cohort_output_file,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
