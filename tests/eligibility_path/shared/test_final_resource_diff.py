"""Tests for the single final-resource run-to-run diff (qa/final_resource_diff.py)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from aus_trial_universe.eligibility_path.qa import final_resource_diff as frd

_OCC = "\x1e"
_SEP = "\x1f"


def _write(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, sep="\t", index=False)


def _ids(diff: pd.DataFrame, change_type: str) -> list[str]:
    rows = diff.loc[diff["change_type"] == change_type, "row_key"]
    return [str(k).split(_OCC)[0].replace(_SEP, "/") for k in rows]


def _trial(trial_ids, phases):
    return pd.DataFrame({"trialId": trial_ids, "phase": phases})


def _cohort(trial_ids, cohorts, phases):
    return pd.DataFrame({"trialId": trial_ids, "cohort": cohorts, "phase": phases})


# --- pure diff -----------------------------------------------------------------

def test_compare_tables_detects_added_removed_and_changed():
    before = _trial(["NCT1", "NCT2"], ["1", "2"])
    after = _trial(["NCT2", "NCT3"], ["3", "1"])

    diff = frd.compare_tables("eligibility_trial_resource.tsv", before, after, ("trialId",))

    assert _ids(diff, "row_added") == ["NCT3"]
    assert _ids(diff, "row_removed") == ["NCT1"]
    changed = diff[diff["change_type"] == "cell_changed"]
    assert changed["column"].tolist() == ["phase"]
    assert changed.iloc[0]["baseline_value"] == "2"
    assert changed.iloc[0]["new_value"] == "3"


def test_compare_tables_identical_yields_empty():
    df = _trial(["NCT1"], ["1"])
    assert frd.compare_tables("f.tsv", df, df.copy(), ("trialId",)).empty


# --- canonical / history selection --------------------------------------------

def test_latest_canonical_picks_newest_date(tmp_path):
    for date in ("01012026", "15032026", "02022026"):
        _write(tmp_path / f"eligibility_trial_resource_{date}.tsv", _trial(["NCT1"], ["1"]))
    latest = frd.latest_canonical(tmp_path, "eligibility_trial_resource")
    assert latest.name == "eligibility_trial_resource_15032026.tsv"


def test_history_versions_sorted_oldest_first(tmp_path):
    history = tmp_path / "history"
    for stamp in ("29062026_1000", "29062026_1430", "28062026_0900"):
        _write(history / f"eligibility_trial_resource_{stamp}.tsv", _trial(["NCT1"], ["1"]))
    versions = frd.history_versions(history, "eligibility_trial_resource")
    assert [p.name for _, p in versions] == [
        "eligibility_trial_resource_28062026_0900.tsv",
        "eligibility_trial_resource_29062026_1000.tsv",
        "eligibility_trial_resource_29062026_1430.tsv",
    ]


# --- end-to-end snapshot + diff -----------------------------------------------

def test_first_run_snapshots_but_has_nothing_to_compare(tmp_path):
    _write(tmp_path / "eligibility_trial_resource_29062026.tsv", _trial(["NCT1"], ["1"]))
    _write(tmp_path / "eligibility_cohort_resource_29062026.tsv", _cohort(["NCT1"], ["(general)"], ["1"]))

    combined = frd.diff_final_resources(tmp_path, now=datetime(2026, 6, 29, 10, 0))

    assert combined.empty
    assert not (tmp_path / frd.DIFF_FILENAME).exists()
    # the run was snapshotted for next time
    assert (tmp_path / "history" / "eligibility_trial_resource_29062026_1000.tsv").is_file()


def test_two_runs_same_day_produce_a_diff(tmp_path):
    # run 1 (10:00)
    _write(tmp_path / "eligibility_trial_resource_29062026.tsv", _trial(["NCT1"], ["1"]))
    _write(tmp_path / "eligibility_cohort_resource_29062026.tsv", _cohort(["NCT1"], ["(general)"], ["1"]))
    frd.diff_final_resources(tmp_path, now=datetime(2026, 6, 29, 10, 0))

    # run 2 (14:30, SAME day): the canonical file is overwritten with a new trial
    _write(tmp_path / "eligibility_trial_resource_29062026.tsv", _trial(["NCT1", "NCT2"], ["1", "2"]))
    _write(tmp_path / "eligibility_cohort_resource_29062026.tsv", _cohort(["NCT1"], ["(general)"], ["1"]))
    combined = frd.diff_final_resources(tmp_path, now=datetime(2026, 6, 29, 14, 30))

    trial_added = combined[combined["file"] == "eligibility_trial_resource.tsv"]
    assert _ids(trial_added, "row_added") == ["NCT2"]
    # cohort resource unchanged across runs
    assert combined[combined["file"] == "eligibility_cohort_resource.tsv"].empty
    assert (tmp_path / frd.DIFF_FILENAME).is_file()
    # both same-day runs are preserved as distinct minute-stamped snapshots
    snaps = sorted(p.name for p in (tmp_path / "history").glob("eligibility_trial_resource_*.tsv"))
    assert snaps == [
        "eligibility_trial_resource_29062026_1000.tsv",
        "eligibility_trial_resource_29062026_1430.tsv",
    ]


def test_history_is_pruned_to_the_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(frd, "HISTORY_KEEP", 2)
    _write(tmp_path / "eligibility_trial_resource_29062026.tsv", _trial(["NCT1"], ["1"]))
    _write(tmp_path / "eligibility_cohort_resource_29062026.tsv", _cohort(["NCT1"], ["(general)"], ["1"]))

    for minute in range(4):
        frd.diff_final_resources(tmp_path, now=datetime(2026, 6, 29, 10, minute))

    snaps = list((tmp_path / "history").glob("eligibility_trial_resource_*.tsv"))
    assert len(snaps) == 2  # only the two most recent snapshots are kept
