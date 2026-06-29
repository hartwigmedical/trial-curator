"""Contract tests for the standardized 01/02/03 input files and meta pruning.

- ctgov download keeps only the newest `ctgov_trials_meta_*.json`;
- both registries' extract steps read the merged `03` file as the single
  authoritative source, falling back to staged 01/02 for older version dirs.
"""

from __future__ import annotations

import os

from aus_trial_universe.eligibility_path.ctgov.i_download_trials_and_extract_eligibility.i_api_download import (
    _prune_meta_snapshots,
)
from aus_trial_universe.eligibility_path.ctgov.i_download_trials_and_extract_eligibility.ii_extract_fields import (
    default_ctgov_input_files,
)
from aus_trial_universe.eligibility_path.anzctr.i_download_trials_and_extract_eligibility.ii_select_trials_and_fields import (
    default_anzctr_input_files,
)

META_PATTERN = "ctgov_trials_meta_*.json"


def _make_meta(state_dir, name, mtime):
    path = state_dir / name
    path.write_text("{}", encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


def test_prune_meta_snapshots_keeps_only_newest(tmp_path):
    for i, name in enumerate(
        [
            "ctgov_trials_meta_20260101_000000.json",
            "ctgov_trials_meta_20260102_000000.json",
            "ctgov_trials_meta_20260103_000000.json",
        ]
    ):
        _make_meta(tmp_path, name, 1000 + i)
    unrelated = tmp_path / "ctgov_trials_latest.json"
    unrelated.write_text("{}", encoding="utf-8")

    _prune_meta_snapshots(tmp_path, META_PATTERN)

    assert sorted(p.name for p in tmp_path.glob(META_PATTERN)) == [
        "ctgov_trials_meta_20260103_000000.json"
    ]
    assert unrelated.exists()  # non-meta state files are untouched


def test_prune_meta_snapshots_respects_keep(tmp_path):
    for i in range(4):
        _make_meta(tmp_path, f"ctgov_trials_meta_2026010{i}_000000.json", 1000 + i)
    _prune_meta_snapshots(tmp_path, META_PATTERN, keep=2)
    assert len(list(tmp_path.glob(META_PATTERN))) == 2


def test_default_ctgov_input_files_prefers_merged(tmp_path):
    vdir = tmp_path / "version_01012026"
    vdir.mkdir(parents=True)
    for name in (
        "01_initial_search_ctgov_input.json",
        "02_pottr_append_ctgov_input.json",
        "03_merged_ctgov_input.json",
    ):
        (vdir / name).write_text("[]", encoding="utf-8")
    assert default_ctgov_input_files(tmp_path) == [vdir / "03_merged_ctgov_input.json"]


def test_default_ctgov_input_files_falls_back_to_staged(tmp_path):
    vdir = tmp_path / "version_01012026"
    vdir.mkdir(parents=True)
    (vdir / "01_initial_search_ctgov_input.json").write_text("[]", encoding="utf-8")
    (vdir / "02_pottr_append_ctgov_input.json").write_text("[]", encoding="utf-8")
    assert default_ctgov_input_files(tmp_path) == [
        vdir / "01_initial_search_ctgov_input.json",
        vdir / "02_pottr_append_ctgov_input.json",
    ]


def test_default_anzctr_input_files_prefers_merged(tmp_path):
    vdir = tmp_path / "version_01012026"
    vdir.mkdir(parents=True)
    for name in (
        "01_initial_search_anzctr_input.xlsx",
        "02_pottr_append_anzctr_input.xlsx",
        "03_merged_anzctr_input.xlsx",
    ):
        (vdir / name).write_text("stub", encoding="utf-8")
    assert default_anzctr_input_files(tmp_path) == [vdir / "03_merged_anzctr_input.xlsx"]


# --- POTTR id-alias capture (CT.gov duplicate-registration handling) ----------

def _study(nct_id):
    return {"protocolSection": {"identificationModule": {"nctId": nct_id}}}


def test_download_trials_by_nct_ids_records_canonical_id_aliases(monkeypatch):
    from aus_trial_universe.eligibility_path.ctgov.i_download_trials_and_extract_eligibility import (
        i_api_download as api,
    )

    # CT.gov serves the requested NCT03816254 under its canonical duplicate NCT03783403.
    served = {"NCT00000001": "NCT00000001", "NCT03816254": "NCT03783403"}
    monkeypatch.setattr(
        api, "download_one_trial_from_ctgov", lambda session, nct_id: _study(served[nct_id])
    )

    trials, aliases = api.download_trials_by_nct_ids(
        session=None, nct_ids=["NCT00000001", "NCT03816254"]
    )

    returned = [s["protocolSection"]["identificationModule"]["nctId"] for s in trials]
    assert returned == ["NCT00000001", "NCT03783403"]
    # Only the id whose canonical differs is recorded.
    assert aliases == {"NCT03816254": "NCT03783403"}


def test_write_pottr_alias_table_roundtrips_via_loader(tmp_path):
    from aus_trial_universe.eligibility_path.ctgov.i_download_trials_and_extract_eligibility import (
        i_api_download as api,
    )
    from aus_trial_universe.eligibility_path.shared.trial_resource.combined_trial_resource_export import (
        load_pottr_id_aliases,
    )

    path = tmp_path / "02b_pottr_id_aliases_ctgov.tsv"
    api._write_pottr_alias_table(path, {"NCT03816254": "NCT03783403"})
    assert load_pottr_id_aliases(path) == {"NCT03816254": "NCT03783403"}
    # Empty rewrite clears stale aliases (header only -> {}).
    api._write_pottr_alias_table(path, {})
    assert load_pottr_id_aliases(path) == {}
