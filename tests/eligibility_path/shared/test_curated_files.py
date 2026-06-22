from pathlib import Path

from aus_trial_universe.eligibility_path.shared.utils.curated_files import (
    has_curated_py_files,
    iter_curated_py_files,
)


def test_curated_file_helpers_filter_by_trial_id_prefix(tmp_path: Path):
    nct_file = tmp_path / "NCT00000001.py"
    actrn_file = tmp_path / "ACTRN12600000000000.py"
    other_file = tmp_path / "notes.py"

    nct_file.write_text("# nct", encoding="utf-8")
    actrn_file.write_text("# actrn", encoding="utf-8")
    other_file.write_text("# notes", encoding="utf-8")

    assert has_curated_py_files(tmp_path, trial_id_prefix="NCT")
    assert [path.name for path in iter_curated_py_files(tmp_path, trial_id_prefix="NCT")] == [
        "NCT00000001.py"
    ]
    assert [path.name for path in iter_curated_py_files(tmp_path, trial_id_prefix="ACTRN")] == [
        "ACTRN12600000000000.py"
    ]


def test_curated_file_helpers_accept_single_matching_file(tmp_path: Path):
    py_file = tmp_path / "ACTRN12600000000000.py"
    py_file.write_text("# actrn", encoding="utf-8")

    assert has_curated_py_files(py_file, trial_id_prefix="ACTRN")
    assert list(iter_curated_py_files(py_file, trial_id_prefix="ACTRN")) == [py_file]
