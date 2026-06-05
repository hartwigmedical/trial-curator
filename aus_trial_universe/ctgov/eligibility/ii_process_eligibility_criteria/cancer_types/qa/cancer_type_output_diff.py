from __future__ import annotations

import argparse
import json
import logging
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

import pandas as pd

LOGGER = logging.getLogger(__name__)

DEFAULT_PROCESSED_DIR = Path("data/ctgov/eligibility/processed/cancer_type")
DEFAULT_BASELINE_SUBDIR = "baseline"
DEFAULT_DATE_FORMAT = "%d%m%Y"

OUTPUT_FILES: Sequence[str] = (
    "01_conditions_mapping.tsv",
    "02_primary_vs_conditions.tsv",
    "03_row_level_cancer_type.tsv",
    "04_trial_level_cancer_type.tsv",
)

KEY_COLUMNS_BY_FILE: Dict[str, Sequence[str]] = {
    "01_conditions_mapping.tsv": (
        "trial_id",
    ),
    "02_primary_vs_conditions.tsv": (
        "nct_id",
        "primary_tumor_type",
        "primary_tumor_location",
        "conditions_original",
        "rule_text",
        "ancestor_chain",
        "siblings_summary",
    ),
    "03_row_level_cancer_type.tsv": (
        "nct_id",
        "primary_tumor_type",
        "primary_tumor_location",
        "conditions_original",
        "rule_text",
        "ancestor_chain",
        "siblings_summary",
    ),
    "04_trial_level_cancer_type.tsv": (
        "nct_id",
    ),
}


@dataclass(frozen=True)
class DiffSummaryRow:
    file: str
    baseline_rows: int
    new_rows: int
    added_rows: int
    removed_rows: int
    changed_cells: int
    added_columns: int
    removed_columns: int
    diff_rows: int
    identical: bool
    diff_file: str


def _read_tsv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False, na_values=[])


def _write_tsv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, sep="\t", index=False)


def _normalize_cell(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _row_to_json(row: pd.Series) -> str:
    return json.dumps(
        {str(k): _normalize_cell(v) for k, v in row.to_dict().items()},
        ensure_ascii=False,
        sort_keys=True,
    )


def _choose_key_columns(
    filename: str,
    before: pd.DataFrame,
    after: pd.DataFrame,
) -> List[str]:
    preferred = list(KEY_COLUMNS_BY_FILE.get(filename, ()))

    if preferred and all(c in before.columns and c in after.columns for c in preferred):
        return preferred

    fallback_sets: List[Sequence[str]] = [
        ("nct_id",),
        ("trial_id",),
    ]

    for candidate in fallback_sets:
        if all(c in before.columns and c in after.columns for c in candidate):
            return list(candidate)

    return []


def _add_row_key(df: pd.DataFrame, key_columns: Sequence[str]) -> pd.DataFrame:
    out = df.copy()

    if key_columns:
        out["_diff_key_base"] = out.loc[:, list(key_columns)].apply(
            lambda row: "\x1f".join(_normalize_cell(value) for value in row),
            axis=1,
        )
    else:
        out["_diff_key_base"] = [f"row_index={i}" for i in range(len(out))]

    occurrence = out.groupby("_diff_key_base", sort=False).cumcount()
    out["_diff_key"] = out["_diff_key_base"] + "\x1e" + occurrence.astype(str)

    return out


def _compare_tables(
    *,
    filename: str,
    baseline_file: Path,
    new_file: Path,
) -> pd.DataFrame:
    rows: List[dict[str, object]] = []

    columns = [
        "file",
        "change_type",
        "row_key",
        "column",
        "baseline_value",
        "new_value",
    ]

    if not baseline_file.exists() and not new_file.exists():
        return pd.DataFrame(columns=columns)

    if not baseline_file.exists():
        after = _read_tsv(new_file)
        for idx, row in after.iterrows():
            rows.append(
                {
                    "file": filename,
                    "change_type": "file_added_row",
                    "row_key": f"row_index={idx}",
                    "column": "*",
                    "baseline_value": "",
                    "new_value": _row_to_json(row),
                }
            )
        return pd.DataFrame(rows, columns=columns)

    if not new_file.exists():
        before = _read_tsv(baseline_file)
        for idx, row in before.iterrows():
            rows.append(
                {
                    "file": filename,
                    "change_type": "file_removed_row",
                    "row_key": f"row_index={idx}",
                    "column": "*",
                    "baseline_value": _row_to_json(row),
                    "new_value": "",
                }
            )
        return pd.DataFrame(rows, columns=columns)

    before = _read_tsv(baseline_file)
    after = _read_tsv(new_file)

    before.columns = [str(c).strip() for c in before.columns]
    after.columns = [str(c).strip() for c in after.columns]

    before_columns = list(before.columns)
    after_columns = list(after.columns)

    before_column_set = set(before_columns)
    after_column_set = set(after_columns)

    for column in sorted(after_column_set - before_column_set):
        rows.append(
            {
                "file": filename,
                "change_type": "column_added",
                "row_key": "",
                "column": column,
                "baseline_value": "",
                "new_value": column,
            }
        )

    for column in sorted(before_column_set - after_column_set):
        rows.append(
            {
                "file": filename,
                "change_type": "column_removed",
                "row_key": "",
                "column": column,
                "baseline_value": column,
                "new_value": "",
            }
        )

    key_columns = _choose_key_columns(filename, before, after)
    before_keyed = _add_row_key(before, key_columns).set_index("_diff_key", drop=False)
    after_keyed = _add_row_key(after, key_columns).set_index("_diff_key", drop=False)

    before_keys = set(before_keyed.index)
    after_keys = set(after_keyed.index)

    for key in sorted(after_keys - before_keys):
        rows.append(
            {
                "file": filename,
                "change_type": "row_added",
                "row_key": key,
                "column": "*",
                "baseline_value": "",
                "new_value": _row_to_json(
                    after_keyed.loc[key].drop(labels=["_diff_key_base", "_diff_key"])
                ),
            }
        )

    for key in sorted(before_keys - after_keys):
        rows.append(
            {
                "file": filename,
                "change_type": "row_removed",
                "row_key": key,
                "column": "*",
                "baseline_value": _row_to_json(
                    before_keyed.loc[key].drop(labels=["_diff_key_base", "_diff_key"])
                ),
                "new_value": "",
            }
        )

    common_columns = [
        c for c in before_columns
        if c in after_column_set and c not in {"_diff_key_base", "_diff_key"}
    ]

    for key in sorted(before_keys & after_keys):
        before_row = before_keyed.loc[key]
        after_row = after_keyed.loc[key]

        for column in common_columns:
            before_value = _normalize_cell(before_row.get(column, ""))
            after_value = _normalize_cell(after_row.get(column, ""))

            if before_value != after_value:
                rows.append(
                    {
                        "file": filename,
                        "change_type": "cell_changed",
                        "row_key": key,
                        "column": column,
                        "baseline_value": before_value,
                        "new_value": after_value,
                    }
                )

    return pd.DataFrame(rows, columns=columns)


def create_cancer_type_snapshot_and_diffs(
    *,
    processed_dir: Path,
    baseline_dir: Path,
    snapshot_dir: Path,
    files: Sequence[str] = OUTPUT_FILES,
) -> pd.DataFrame:
    processed_dir = Path(processed_dir)
    baseline_dir = Path(baseline_dir)
    snapshot_dir = Path(snapshot_dir)

    if not processed_dir.exists():
        raise FileNotFoundError(f"processed_dir does not exist: {processed_dir}")

    if not baseline_dir.exists():
        raise FileNotFoundError(f"baseline_dir does not exist: {baseline_dir}")

    snapshot_dir.mkdir(parents=True, exist_ok=True)

    summary_rows: List[DiffSummaryRow] = []

    for filename in files:
        current_file = processed_dir / filename
        snapshot_file = snapshot_dir / filename
        baseline_file = baseline_dir / filename
        diff_file = snapshot_dir / f"diff_{filename}"

        if current_file.exists():
            shutil.copy2(current_file, snapshot_file)
            LOGGER.info("Copied current output to snapshot: %s", snapshot_file)
        else:
            LOGGER.warning(
                "Current output file does not exist and will not be copied: %s",
                current_file,
            )

        diff_df = _compare_tables(
            filename=filename,
            baseline_file=baseline_file,
            new_file=snapshot_file,
        )
        _write_tsv(diff_df, diff_file)

        baseline_rows = len(_read_tsv(baseline_file)) if baseline_file.exists() else 0
        new_rows = len(_read_tsv(snapshot_file)) if snapshot_file.exists() else 0

        added_rows = int((diff_df["change_type"] == "row_added").sum()) if not diff_df.empty else 0
        removed_rows = int((diff_df["change_type"] == "row_removed").sum()) if not diff_df.empty else 0
        changed_cells = int((diff_df["change_type"] == "cell_changed").sum()) if not diff_df.empty else 0
        added_columns = int((diff_df["change_type"] == "column_added").sum()) if not diff_df.empty else 0
        removed_columns = int((diff_df["change_type"] == "column_removed").sum()) if not diff_df.empty else 0

        summary_rows.append(
            DiffSummaryRow(
                file=filename,
                baseline_rows=baseline_rows,
                new_rows=new_rows,
                added_rows=added_rows,
                removed_rows=removed_rows,
                changed_cells=changed_cells,
                added_columns=added_columns,
                removed_columns=removed_columns,
                diff_rows=len(diff_df),
                identical=len(diff_df) == 0,
                diff_file=diff_file.name,
            )
        )

        LOGGER.info("Wrote diff file: %s rows=%d", diff_file, len(diff_df))

    summary_df = pd.DataFrame([row.__dict__ for row in summary_rows])
    _write_tsv(summary_df, snapshot_dir / "diff_summary.tsv")

    LOGGER.info("Wrote diff summary: %s", snapshot_dir / "diff_summary.tsv")
    return summary_df


def _default_snapshot_dir(processed_dir: Path, snapshot_date: Optional[str]) -> Path:
    if snapshot_date is None:
        snapshot_date = date.today().strftime(DEFAULT_DATE_FORMAT)

    snapshot_date = str(snapshot_date).strip()
    if not snapshot_date or not snapshot_date.isdigit() or len(snapshot_date) != 8:
        raise ValueError(f"snapshot_date must be ddmmyyyy, got {snapshot_date!r}")

    return processed_dir / snapshot_date


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Snapshot current cancer-type processed outputs into a dated folder "
            "and write per-file TSV diffs against a baseline folder."
        )
    )

    parser.add_argument(
        "--processed_dir",
        type=Path,
        default=DEFAULT_PROCESSED_DIR,
        help="Cancer-type processed output directory.",
    )

    parser.add_argument(
        "--baseline_dir",
        type=Path,
        default=None,
        help="Baseline directory to compare against. Defaults to processed_dir/baseline.",
    )

    parser.add_argument(
        "--snapshot_dir",
        type=Path,
        default=None,
        help="Output snapshot directory. Defaults to processed_dir/<ddmmyyyy>.",
    )

    parser.add_argument(
        "--snapshot_date",
        default=None,
        help="Date suffix for default snapshot_dir, in ddmmyyyy format.",
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

    processed_dir = args.processed_dir
    baseline_dir = args.baseline_dir or processed_dir / DEFAULT_BASELINE_SUBDIR
    snapshot_dir = args.snapshot_dir or _default_snapshot_dir(
        processed_dir,
        args.snapshot_date,
    )

    summary_df = create_cancer_type_snapshot_and_diffs(
        processed_dir=processed_dir,
        baseline_dir=baseline_dir,
        snapshot_dir=snapshot_dir,
    )

    LOGGER.info("Snapshot/diff complete.")
    LOGGER.info("snapshot_dir: %s", snapshot_dir)
    LOGGER.info("\n%s", summary_df.to_string(index=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())