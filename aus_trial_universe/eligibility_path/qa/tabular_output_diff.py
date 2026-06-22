from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import pandas as pd

LOGGER = logging.getLogger(__name__)

DEFAULT_BASELINE_SUBDIR = "baseline"
DEFAULT_QA_SUBDIR = "qa"
DEFAULT_TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"


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


def read_tsv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False, na_values=[])


def write_tsv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, sep="\t", index=False)


def normalize_cell(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def row_to_json(row: pd.Series) -> str:
    return json.dumps(
        {str(key): normalize_cell(value) for key, value in row.to_dict().items()},
        ensure_ascii=False,
        sort_keys=True,
    )


def choose_key_columns(
    *,
    filename: str,
    before: pd.DataFrame,
    after: pd.DataFrame,
    key_columns_by_file: Dict[str, Sequence[str]],
    fallback_key_columns: Sequence[Sequence[str]],
) -> List[str]:
    preferred = list(key_columns_by_file.get(filename, ()))
    if preferred and all(column in before.columns and column in after.columns for column in preferred):
        return preferred

    for candidate in fallback_key_columns:
        if all(column in before.columns and column in after.columns for column in candidate):
            return list(candidate)

    return []


def add_row_key(df: pd.DataFrame, key_columns: Sequence[str]) -> pd.DataFrame:
    out = df.copy()

    if key_columns:
        out["_diff_key_base"] = out.loc[:, list(key_columns)].apply(
            lambda row: "\x1f".join(normalize_cell(value) for value in row),
            axis=1,
        )
    else:
        out["_diff_key_base"] = [f"row_index={index}" for index in range(len(out))]

    occurrence = out.groupby("_diff_key_base", sort=False).cumcount()
    out["_diff_key"] = out["_diff_key_base"] + "\x1e" + occurrence.astype(str)
    return out


def compare_tables(
    *,
    filename: str,
    baseline_file: Path,
    new_file: Path,
    key_columns_by_file: Dict[str, Sequence[str]],
    fallback_key_columns: Sequence[Sequence[str]] = (),
) -> pd.DataFrame:
    columns = [
        "file",
        "change_type",
        "row_key",
        "column",
        "baseline_value",
        "new_value",
    ]
    rows: List[dict[str, object]] = []

    if not baseline_file.exists() and not new_file.exists():
        return pd.DataFrame(columns=columns)

    if not baseline_file.exists():
        after = read_tsv(new_file)
        for index, row in after.iterrows():
            rows.append(
                {
                    "file": filename,
                    "change_type": "file_added_row",
                    "row_key": f"row_index={index}",
                    "column": "*",
                    "baseline_value": "",
                    "new_value": row_to_json(row),
                }
            )
        return pd.DataFrame(rows, columns=columns)

    if not new_file.exists():
        before = read_tsv(baseline_file)
        for index, row in before.iterrows():
            rows.append(
                {
                    "file": filename,
                    "change_type": "file_removed_row",
                    "row_key": f"row_index={index}",
                    "column": "*",
                    "baseline_value": row_to_json(row),
                    "new_value": "",
                }
            )
        return pd.DataFrame(rows, columns=columns)

    before = read_tsv(baseline_file)
    after = read_tsv(new_file)

    before.columns = [str(column).strip() for column in before.columns]
    after.columns = [str(column).strip() for column in after.columns]

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

    key_columns = choose_key_columns(
        filename=filename,
        before=before,
        after=after,
        key_columns_by_file=key_columns_by_file,
        fallback_key_columns=fallback_key_columns,
    )
    before_keyed = add_row_key(before, key_columns).set_index("_diff_key", drop=False)
    after_keyed = add_row_key(after, key_columns).set_index("_diff_key", drop=False)

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
                "new_value": row_to_json(after_keyed.loc[key].drop(labels=["_diff_key_base", "_diff_key"])),
            }
        )

    for key in sorted(before_keys - after_keys):
        rows.append(
            {
                "file": filename,
                "change_type": "row_removed",
                "row_key": key,
                "column": "*",
                "baseline_value": row_to_json(before_keyed.loc[key].drop(labels=["_diff_key_base", "_diff_key"])),
                "new_value": "",
            }
        )

    common_columns = [
        column
        for column in before_columns
        if column in after_column_set and column not in {"_diff_key_base", "_diff_key"}
    ]

    for key in sorted(before_keys & after_keys):
        before_row = before_keyed.loc[key]
        after_row = after_keyed.loc[key]

        for column in common_columns:
            before_value = normalize_cell(before_row.get(column, ""))
            after_value = normalize_cell(after_row.get(column, ""))

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


def default_snapshot_dir(processed_dir: Path, snapshot_label: Optional[str] = None) -> Path:
    label = snapshot_label or datetime.now().strftime(DEFAULT_TIMESTAMP_FORMAT)
    return Path(processed_dir) / DEFAULT_QA_SUBDIR / label


def create_snapshot_and_diffs(
    *,
    processed_dir: Path,
    baseline_dir: Optional[Path],
    snapshot_dir: Path,
    files: Sequence[str],
    key_columns_by_file: Dict[str, Sequence[str]],
    fallback_key_columns: Sequence[Sequence[str]] = (),
) -> pd.DataFrame:
    processed_dir = Path(processed_dir)
    baseline_dir = Path(baseline_dir) if baseline_dir is not None else processed_dir / DEFAULT_BASELINE_SUBDIR
    snapshot_dir = Path(snapshot_dir)

    if not processed_dir.exists():
        raise FileNotFoundError(f"processed_dir does not exist: {processed_dir}")

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
            LOGGER.warning("Current output file does not exist: %s", current_file)

        diff_df = compare_tables(
            filename=filename,
            baseline_file=baseline_file,
            new_file=snapshot_file,
            key_columns_by_file=key_columns_by_file,
            fallback_key_columns=fallback_key_columns,
        )
        write_tsv(diff_df, diff_file)

        baseline_rows = len(read_tsv(baseline_file)) if baseline_file.exists() else 0
        new_rows = len(read_tsv(snapshot_file)) if snapshot_file.exists() else 0
        added_rows = int((diff_df["change_type"].isin(["row_added", "file_added_row"])).sum()) if not diff_df.empty else 0
        removed_rows = int((diff_df["change_type"].isin(["row_removed", "file_removed_row"])).sum()) if not diff_df.empty else 0
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
    write_tsv(summary_df, snapshot_dir / "diff_summary.tsv")
    LOGGER.info("Wrote diff summary: %s", snapshot_dir / "diff_summary.tsv")
    return summary_df

