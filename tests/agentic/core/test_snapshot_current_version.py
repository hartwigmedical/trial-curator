"""`snapshot_current_version` — the COPY-based baseline for the accumulating eligibility store.

Distinct from `archive_current_version` (a MOVE) for a reason worth pinning: moving the eligibility store aside
would leave `EligStore.load()` with no `current_version/`, silently falling through to an old snapshot dir.
"""
from __future__ import annotations

from aus_trial_universe.core.paths import ARCHIVE, CURRENT_VERSION, snapshot_current_version


def _store(tmp_path, text="a\tb\n1\t2\n"):
    root = tmp_path / "eligibility"
    (root / CURRENT_VERSION).mkdir(parents=True)
    (root / CURRENT_VERSION / "finalised_cancer_type_map.tsv").write_text(text, encoding="utf-8")
    return root


def test_copies_and_leaves_the_live_version_in_place(tmp_path):
    root = _store(tmp_path)
    dest = snapshot_current_version(root, "05082026")
    assert dest == root / ARCHIVE / "05082026"
    assert (dest / "finalised_cancer_type_map.tsv").exists()
    assert (root / CURRENT_VERSION / "finalised_cancer_type_map.tsv").exists(), "the live store must NOT be moved"


def test_same_day_second_run_gets_a_suffix_instead_of_failing(tmp_path):
    root = _store(tmp_path)
    first = snapshot_current_version(root, "05082026")
    second = snapshot_current_version(root, "05082026")
    assert first.name == "05082026" and second.name == "05082026_2"


def test_returns_none_on_a_first_build(tmp_path):
    root = tmp_path / "eligibility"
    root.mkdir()
    assert snapshot_current_version(root, "05082026") is None
