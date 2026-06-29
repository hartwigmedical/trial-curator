"""Run-to-run diff of the final eligibility resource files.

This is the only QA diff in the eligibility path. Each run it:

  1. snapshots the current final resources
     (``eligibility_trial_resource_<ddmmyyyy>.tsv`` and
     ``eligibility_cohort_resource_<ddmmyyyy>.tsv``) into a minute-stamped
     ``history/`` copy, and
  2. diffs the two most recent snapshots, writing a single
     ``final_resource_diff.tsv`` report (trials added/removed, cells changed).

The canonical final filenames carry only ``<ddmmyyyy>`` (the run-level
``export_date`` is validated elsewhere as exactly 8 digits), so a same-day
re-run overwrites the day's file. The minute-stamped ``history/`` snapshots are
what make consecutive runs — including two on the same day — comparable. The
diff keys the trial resource on ``trialId`` and the cohort resource on
``trialId`` + ``cohort``; no baseline directory is needed.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Sequence

import pandas as pd

LOGGER = logging.getLogger(__name__)

DEFAULT_FINAL_DIR = Path("data/eligibility_path/exports/final")
HISTORY_SUBDIR = "history"
DIFF_FILENAME = "final_resource_diff.tsv"
# Cap on retained per-resource history snapshots, so the folder cannot grow
# without bound across many runs.
HISTORY_KEEP = 60

_CANONICAL_RE = re.compile(r"_(\d{8})\.tsv$")
_HISTORY_RE = re.compile(r"_(\d{8}_\d{4})\.tsv$")
_HISTORY_FMT = "%d%m%Y_%H%M"
_KEY_SEP = "\x1f"
_OCC_SEP = "\x1e"

# (filename stem, key columns) for the top-level combined final resources.
TARGETS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("eligibility_trial_resource", ("trialId",)),
    ("eligibility_cohort_resource", ("trialId", "cohort")),
)

DIFF_COLUMNS = ["file", "change_type", "row_key", "column", "baseline_value", "new_value"]


def read_tsv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False, na_values=[])


def normalize_cell(value: object) -> str:
    return "" if value is None else str(value).strip()


def row_to_json(row: pd.Series) -> str:
    return json.dumps(
        {str(key): normalize_cell(value) for key, value in row.to_dict().items()},
        ensure_ascii=False,
        sort_keys=True,
    )


def add_row_key(df: pd.DataFrame, key_columns: Sequence[str]) -> pd.DataFrame:
    """Per-row diff key; rows sharing a key value get a trailing occurrence index
    so distinct duplicate-key rows are not collapsed into one match."""
    out = df.copy()
    if key_columns:
        out["_diff_key_base"] = out.loc[:, list(key_columns)].apply(
            lambda row: _KEY_SEP.join(normalize_cell(value) for value in row), axis=1
        )
    else:
        out["_diff_key_base"] = [f"row_index={index}" for index in range(len(out))]
    occurrence = out.groupby("_diff_key_base", sort=False).cumcount()
    out["_diff_key"] = out["_diff_key_base"] + _OCC_SEP + occurrence.astype(str)
    return out


def compare_tables(
    filename: str,
    before: pd.DataFrame,
    after: pd.DataFrame,
    key_columns: Sequence[str],
) -> pd.DataFrame:
    """Return a long-form diff (column/row/cell changes) of ``before`` -> ``after``."""
    rows: List[dict[str, object]] = []
    before = before.copy()
    after = after.copy()
    before.columns = [str(column).strip() for column in before.columns]
    after.columns = [str(column).strip() for column in after.columns]

    before_cols = set(before.columns)
    after_cols = set(after.columns)
    for column in sorted(after_cols - before_cols):
        rows.append({"file": filename, "change_type": "column_added", "row_key": "",
                     "column": column, "baseline_value": "", "new_value": column})
    for column in sorted(before_cols - after_cols):
        rows.append({"file": filename, "change_type": "column_removed", "row_key": "",
                     "column": column, "baseline_value": column, "new_value": ""})

    keys = [column for column in key_columns if column in before_cols and column in after_cols]
    before_keyed = add_row_key(before, keys).set_index("_diff_key", drop=False)
    after_keyed = add_row_key(after, keys).set_index("_diff_key", drop=False)
    before_keys = set(before_keyed.index)
    after_keys = set(after_keyed.index)
    helper = ["_diff_key_base", "_diff_key"]

    for key in sorted(after_keys - before_keys):
        rows.append({"file": filename, "change_type": "row_added", "row_key": key, "column": "*",
                     "baseline_value": "", "new_value": row_to_json(after_keyed.loc[key].drop(labels=helper))})
    for key in sorted(before_keys - after_keys):
        rows.append({"file": filename, "change_type": "row_removed", "row_key": key, "column": "*",
                     "baseline_value": row_to_json(before_keyed.loc[key].drop(labels=helper)), "new_value": ""})

    common = [c for c in before.columns if c in after_cols and c not in helper]
    for key in sorted(before_keys & after_keys):
        before_row = before_keyed.loc[key]
        after_row = after_keyed.loc[key]
        for column in common:
            before_value = normalize_cell(before_row.get(column, ""))
            after_value = normalize_cell(after_row.get(column, ""))
            if before_value != after_value:
                rows.append({"file": filename, "change_type": "cell_changed", "row_key": key,
                             "column": column, "baseline_value": before_value, "new_value": after_value})

    return pd.DataFrame(rows, columns=DIFF_COLUMNS)


def latest_canonical(final_dir: Path, stem: str) -> Path | None:
    """The newest ``<stem>_<ddmmyyyy>.tsv`` in ``final_dir`` (this run's output)."""
    dated: List[tuple[datetime, Path]] = []
    for path in final_dir.glob(f"{stem}_*.tsv"):
        match = _CANONICAL_RE.search(path.name)
        if not match:
            continue
        try:
            dated.append((datetime.strptime(match.group(1), "%d%m%Y"), path))
        except ValueError:
            continue
    return max(dated, key=lambda item: item[0])[1] if dated else None


def history_versions(history_dir: Path, stem: str) -> List[tuple[datetime, Path]]:
    """All ``<stem>_<ddmmyyyy>_<HHMM>.tsv`` snapshots, oldest first."""
    versions: List[tuple[datetime, Path]] = []
    if not history_dir.exists():
        return versions
    for path in history_dir.glob(f"{stem}_*.tsv"):
        match = _HISTORY_RE.search(path.name)
        if not match:
            continue
        try:
            versions.append((datetime.strptime(match.group(1), _HISTORY_FMT), path))
        except ValueError:
            continue
    return sorted(versions, key=lambda item: item[0])


def _snapshot_current(final_dir: Path, history_dir: Path, stem: str, now: datetime) -> None:
    current = latest_canonical(final_dir, stem)
    if current is None:
        LOGGER.warning("Final resource diff: no %s_<ddmmyyyy>.tsv under %s; not snapshotted.", stem, final_dir)
        return
    history_dir.mkdir(parents=True, exist_ok=True)
    destination = history_dir / f"{stem}_{now.strftime(_HISTORY_FMT)}.tsv"
    shutil.copy2(current, destination)


def _prune_history(history_dir: Path, stem: str, keep: int) -> None:
    versions = history_versions(history_dir, stem)
    for _stamp, path in versions[:-keep] if keep > 0 else []:
        path.unlink(missing_ok=True)


def _row_keys(diff: pd.DataFrame, change_type: str) -> List[str]:
    if diff.empty:
        return []
    selected = diff.loc[diff["change_type"] == change_type, "row_key"]
    return [str(key).split(_OCC_SEP)[0].replace(_KEY_SEP, "/") for key in selected]


def _log_summary(stem: str, previous: Path, latest: Path, diff: pd.DataFrame) -> None:
    change_type = diff["change_type"] if not diff.empty else pd.Series(dtype="object")
    LOGGER.info(
        "Final resource diff [%s]: %s -> %s | +%d rows, -%d rows, %d cell change(s), +%d/-%d column(s).",
        stem, previous.name, latest.name,
        int(change_type.eq("row_added").sum()), int(change_type.eq("row_removed").sum()),
        int(change_type.eq("cell_changed").sum()), int(change_type.eq("column_added").sum()),
        int(change_type.eq("column_removed").sum()),
    )
    added_ids = _row_keys(diff, "row_added")
    removed_ids = _row_keys(diff, "row_removed")
    if added_ids:
        LOGGER.info("  added (%d): %s", len(added_ids), ", ".join(added_ids))
    if removed_ids:
        LOGGER.info("  removed (%d): %s", len(removed_ids), ", ".join(removed_ids))


def diff_final_resources(final_dir: Path = DEFAULT_FINAL_DIR, *, now: datetime | None = None) -> pd.DataFrame:
    """Snapshot the current final resources, then diff the two most recent snapshots.

    Writes a single combined ``final_resource_diff.tsv`` into ``final_dir`` (only
    when there is a comparable pair) and returns the combined diff frame. Pass
    ``now`` to control the snapshot timestamp (defaults to the wall clock).
    """
    final_dir = Path(final_dir)
    now = now or datetime.now()
    history_dir = final_dir / HISTORY_SUBDIR

    diffs: List[pd.DataFrame] = []
    compared = False
    for stem, keys in TARGETS:
        _snapshot_current(final_dir, history_dir, stem, now)
        _prune_history(history_dir, stem, HISTORY_KEEP)

        versions = history_versions(history_dir, stem)
        if len(versions) < 2:
            LOGGER.info(
                "Final resource diff: only one snapshot of %s so far; nothing to compare yet.", stem
            )
            continue
        compared = True
        previous, latest = versions[-2][1], versions[-1][1]
        diff = compare_tables(f"{stem}.tsv", read_tsv(previous), read_tsv(latest), keys)
        diffs.append(diff)
        _log_summary(stem, previous, latest, diff)

    combined = pd.concat(diffs, ignore_index=True) if diffs else pd.DataFrame(columns=DIFF_COLUMNS)
    if compared:
        out_path = final_dir / DIFF_FILENAME
        out_path.parent.mkdir(parents=True, exist_ok=True)
        combined.to_csv(out_path, sep="\t", index=False)
        LOGGER.info("Final resource diff written: %s (%d change row(s)).", out_path, len(combined))
    return combined


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Snapshot and diff the two most recent final eligibility resources."
    )
    parser.add_argument("--final_dir", type=Path, default=DEFAULT_FINAL_DIR)
    parser.add_argument("--log_level", default="INFO")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    diff_final_resources(args.final_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
