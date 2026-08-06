"""THE MIGRATION GUARDS — `qa/prompt_harness/apply_reviewed.py`.

These two guards are the last thing standing between a reviewed artifact and the production store, and both have
already caught a real error rather than a hypothetical one:

  · EVERY CHANGED ROW EXPLAINED caught the 2026-08-06 review gap. The review set had been built by INTERSECTING
    version key-sets, which silently dropped the 4 values that postdate the oldest baseline — one of which the run
    changed. Nothing else noticed: the tests passed, the gates were green, and the value would have shipped
    without ever appearing in what the user reviewed.
  · KEY-SET IDENTITY is the same class of protection one step earlier: if the store moved under the artifact, the
    thing being applied is no longer the thing that was reviewed.

A guard that is never exercised is a guard nobody knows is broken, so each is tested in BOTH directions — it
refuses the bad case AND permits the good one.
"""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from aus_trial_universe.qa.prompt_harness import apply_reviewed as AR


def _tsv(path: Path, columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, delimiter="\t", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """A store + an artifact + a review, all in tmp, with the module pointed at them."""
    store = tmp_path / "current_version"
    out = tmp_path / "review"
    from aus_trial_universe.tasks.eligibility.mapping import stage_tables as ST

    spec = ST.MOLECULAR_SIGNATURE
    initial = {"MSI-H": "MicrosatelliteStability[PurpleMicrosatelliteStatus=MSI]", "adverse biology": ""}
    spec.write_all(store, initial=initial, reconciled=dict(initial), finalised=dict(initial))

    monkeypatch.setattr(AR, "current_version_dir", lambda _root: store)

    class _Spec:
        name = key = register = "molecular_signature"
        out_dir = out
    monkeypatch.setattr(AR, "spec", lambda _c: _Spec())
    return store, out, initial


def _write_artifact(out: Path, mapping: dict[str, str]) -> None:
    _tsv(out / "remap_raw_output.tsv", ["molecular_signature", "new_stage1"],
         [{"molecular_signature": v, "new_stage1": e} for v, e in mapping.items()])


def _write_review(out: Path, changed: dict[str, str]) -> None:
    _tsv(out / "three_way_review.tsv", ["molecular_signature", "changed_by"],
         [{"molecular_signature": v, "changed_by": c} for v, c in changed.items()])


def test_refuses_when_the_store_moved_under_the_artifact(sandbox):
    """A key-set mismatch means the artifact no longer describes the corpus it is about to overwrite."""
    store, out, initial = sandbox
    _write_artifact(out, {**initial, "a value that did not exist": ""})
    _write_review(out, {})
    with pytest.raises(SystemExit, match="KEY SET MISMATCH"):
        AR.main(["--column", "molecular_signature", "--apply"])


def test_refuses_a_changed_row_the_review_never_showed(sandbox):
    """THE GAP THAT ACTUALLY HAPPENED: a value the run changes but the review file omits."""
    store, out, initial = sandbox
    _write_artifact(out, {**initial, "MSI-H": "MicrosatelliteStability[PurpleMicrosatelliteStatus=MSS]"})
    _write_review(out, {"MSI-H": ""})            # present, but NOT marked as this run's change
    with pytest.raises(SystemExit, match="every changed row must be explained"):
        AR.main(["--column", "molecular_signature", "--apply"])


def test_permits_a_changed_row_the_review_did_show(sandbox):
    """The good case must still pass, or the guard is just an obstacle."""
    store, out, initial = sandbox
    changed = {**initial, "MSI-H": "MicrosatelliteStability[PurpleMicrosatelliteStatus=MSS]"}
    _write_artifact(out, changed)
    _write_review(out, {"MSI-H": "this_run"})
    assert AR.main(["--column", "molecular_signature", "--apply"]) == 0

    from aus_trial_universe.tasks.eligibility.mapping import stage_tables as ST
    assert ST.MOLECULAR_SIGNATURE.load(store, "finalised")["MSI-H"].endswith("MSS]")


def test_a_dry_run_writes_nothing(sandbox):
    """`--apply` is opt-in; the default must be inspectable without side effects."""
    store, out, initial = sandbox
    before = (store / "molecular_signature_map_finalised.tsv").read_bytes()
    _write_artifact(out, {**initial, "MSI-H": "MicrosatelliteStability[PurpleMicrosatelliteStatus=MSS]"})
    _write_review(out, {"MSI-H": "this_run"})
    assert AR.main(["--column", "molecular_signature"]) == 0
    assert (store / "molecular_signature_map_finalised.tsv").read_bytes() == before


def test_a_register_ruling_explains_a_changed_row_on_its_own(sandbox, monkeypatch):
    """A ruling added AFTER the artifact was written is a legitimate explanation — that is the normal order, since
    the register holds the residue the prompt could not reach."""
    store, out, initial = sandbox
    _write_artifact(out, initial)
    _write_review(out, {})

    from aus_trial_universe.qa.adjudications import Adjudication
    monkeypatch.setattr(AR.adjudications, "for_column",
                        lambda _c: {"adverse biology": Adjudication(
                            value="adverse biology", final="tumorMutationBurden[Status=HIGH]",
                            rationale="test", approved="test")})
    assert AR.main(["--column", "molecular_signature", "--apply"]) == 0
