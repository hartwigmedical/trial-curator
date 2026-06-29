"""Unit tests for the QA diff engine in qa/tabular_output_diff.py.

Previously only the baseline-absent path of ``create_snapshot_and_diffs`` ran.
These exercise the real diff logic curators rely on: ``compare_tables`` change
detection, ``add_row_key`` duplicate-key disambiguation, and
``choose_key_columns`` preferred/fallback selection.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from aus_trial_universe.eligibility_path.qa.tabular_output_diff import (
    add_row_key,
    choose_key_columns,
    compare_tables,
)

KEYS = {"f.tsv": ("id",)}
FALLBACK = (("id",),)


def _write_tsv(path: Path, df: pd.DataFrame) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, sep="\t", index=False)
    return path


def _compare(tmp_path: Path, before: pd.DataFrame, after: pd.DataFrame) -> pd.DataFrame:
    b = _write_tsv(tmp_path / "baseline" / "f.tsv", before)
    a = _write_tsv(tmp_path / "new" / "f.tsv", after)
    return compare_tables(
        filename="f.tsv",
        baseline_file=b,
        new_file=a,
        key_columns_by_file=KEYS,
        fallback_key_columns=FALLBACK,
    )


def test_compare_tables_detects_cell_change(tmp_path):
    diff = _compare(
        tmp_path,
        pd.DataFrame({"id": ["A"], "v": ["1"]}),
        pd.DataFrame({"id": ["A"], "v": ["2"]}),
    )
    assert list(diff["change_type"]) == ["cell_changed"]
    row = diff.iloc[0]
    assert row["column"] == "v"
    assert row["baseline_value"] == "1"
    assert row["new_value"] == "2"


def test_compare_tables_detects_row_added_and_removed(tmp_path):
    diff = _compare(
        tmp_path,
        pd.DataFrame({"id": ["A"], "v": ["1"]}),
        pd.DataFrame({"id": ["B"], "v": ["1"]}),
    )
    assert set(diff["change_type"]) == {"row_added", "row_removed"}


def test_compare_tables_detects_column_add_and_remove(tmp_path):
    diff = _compare(
        tmp_path,
        pd.DataFrame({"id": ["A"], "old": ["x"]}),
        pd.DataFrame({"id": ["A"], "new": ["y"]}),
    )
    added = diff[diff["change_type"] == "column_added"]["column"].tolist()
    removed = diff[diff["change_type"] == "column_removed"]["column"].tolist()
    assert added == ["new"]
    assert removed == ["old"]


def test_compare_tables_identical_tables_yield_no_diff(tmp_path):
    df = pd.DataFrame({"id": ["A", "B"], "v": ["1", "2"]})
    diff = _compare(tmp_path, df, df.copy())
    assert diff.empty


def test_compare_tables_handles_missing_files(tmp_path):
    df = pd.DataFrame({"id": ["A"], "v": ["1"]})
    missing = tmp_path / "missing.tsv"

    # both absent -> empty frame
    assert compare_tables(
        filename="f.tsv", baseline_file=missing, new_file=tmp_path / "missing2.tsv",
        key_columns_by_file=KEYS, fallback_key_columns=(),
    ).empty

    # baseline absent -> every new row is file_added_row
    new_file = _write_tsv(tmp_path / "new" / "f.tsv", df)
    added = compare_tables(
        filename="f.tsv", baseline_file=missing, new_file=new_file,
        key_columns_by_file=KEYS, fallback_key_columns=(),
    )
    assert set(added["change_type"]) == {"file_added_row"}

    # new absent -> every baseline row is file_removed_row
    base_file = _write_tsv(tmp_path / "base2" / "f.tsv", df)
    removed = compare_tables(
        filename="f.tsv", baseline_file=base_file, new_file=missing,
        key_columns_by_file=KEYS, fallback_key_columns=(),
    )
    assert set(removed["change_type"]) == {"file_removed_row"}


def test_add_row_key_disambiguates_duplicate_key_rows():
    # Two rows share the same key value; they must not collapse to one key,
    # otherwise compare_tables would treat distinct rows as a single (false) match.
    df = pd.DataFrame({"id": ["A", "A", "B"], "v": ["1", "2", "3"]})
    keyed = add_row_key(df, ["id"])
    assert keyed["_diff_key"].nunique() == 3
    a_keys = keyed.loc[keyed["id"] == "A", "_diff_key"].tolist()
    assert a_keys[0] != a_keys[1]


def test_add_row_key_without_key_columns_falls_back_to_row_index():
    keyed = add_row_key(pd.DataFrame({"v": ["1", "2"]}), [])
    assert keyed["_diff_key"].nunique() == 2


def test_choose_key_columns_prefers_then_falls_back_then_empty():
    cols = pd.DataFrame(columns=["id", "v"])
    # preferred present -> preferred
    assert choose_key_columns(
        filename="f.tsv", before=cols, after=cols,
        key_columns_by_file={"f.tsv": ("id",)}, fallback_key_columns=(("nope",),),
    ) == ["id"]
    # preferred absent -> first matching fallback
    assert choose_key_columns(
        filename="f.tsv", before=cols, after=cols,
        key_columns_by_file={"f.tsv": ("missing",)}, fallback_key_columns=(("nope",), ("v",)),
    ) == ["v"]
    # nothing matches -> []
    assert choose_key_columns(
        filename="f.tsv", before=cols, after=cols,
        key_columns_by_file={"f.tsv": ("missing",)}, fallback_key_columns=(("nope",),),
    ) == []
