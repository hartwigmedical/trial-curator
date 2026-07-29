"""Per-run refresh report — the durable per-cycle record written after each e2e run."""
from __future__ import annotations

import csv
from datetime import datetime

from aus_trial_universe import run_report as RR
from aus_trial_universe.tasks.ingestion.expiry import ExpiryReport


def test_id_list_caps_and_formats():
    assert RR._ids([]) == "_none_"
    assert RR._ids(["NCT2", "NCT1"]) == "`NCT1`, `NCT2`"                       # sorted, backticked
    many = RR._ids([f"NCT{i:04d}" for i in range(RR._ID_LIST_CAP + 5)])
    assert many.endswith("… and 5 more") and many.count("`") == RR._ID_LIST_CAP * 2


def test_delta_table_marks_changes_and_no_ops():
    lines = RR._delta_table({"a": 10, "b": 5}, {"a": 12, "b": 5, "c": 3})
    body = "\n".join(lines)
    assert "| a | 10 | 12 | +2 |" in body
    assert "| b | 5 | 5 | — |" in body                                        # unchanged renders as a dash
    assert "| c | 0 | 3 | +3 |" in body                                       # a new entity counts from zero


def test_export_shape_parses_the_written_file(tmp_path):
    p = tmp_path / "trial_eligibility.tsv"
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(["trialId", "trial_arm_id", "conjunction_index"])
        w.writerow(["NCT1", "NCT1::A", "1"])
        w.writerow(["NCT1", "NCT1::A", "2"])                                  # same arm, second conjunction
        w.writerow(["NCT1", "NCT1::B", "1"])
        w.writerow(["NCT2", "NCT2::A", "1"])
    assert RR._export_shape(p) | {"trial_ids": None} == {
        "rows": 4, "trials": 2, "arms": 3, "columns": 3, "trial_ids": None}
    assert RR._export_shape(tmp_path / "absent.tsv") == {}


def test_write_report_records_churn_deltas_and_gap(tmp_path, monkeypatch):
    """The report must name the expired/curated ids, show the store delta, and surface curated-but-not-exported
    trials (an empty interpreted DNF yields no export row — silent without this)."""
    monkeypatch.setattr(RR, "_coverage", lambda: {"empty_arms": 2, "empty_trials": ["NCT9", "NCT_GONE"]})
    monkeypatch.setattr("aus_trial_universe.tasks.eligibility.qa.arm_consistency.check",
                        lambda: {"registry": 10, "elig_refs": 10, "drug_refs": 9, "role_refs": 9,
                                 "elig_dangling": [], "drug_dangling": [], "role_dangling": [], "unused": ["NCT1::X"]})
    export = tmp_path / "trial_eligibility.tsv"
    with open(export, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(["trialId", "trial_arm_id"]); w.writerow(["NCT1", "NCT1::A"])

    path = RR.write_report(
        before={"trials curated": 100, "drugs (canonical)": 50},
        after={"trials curated": 105, "drugs (canonical)": 50},
        kept={"NCT1", "NCT9", "ACTRN1"},                      # NCT_GONE is expired, so it must NOT count as a gap
        expiry=ExpiryReport(expired=["NCT_GONE"], restored=[], applied=True),
        curated_ids=["NCT9", "ACTRN1"],
        export_path=export, settings="refresh --workers 80",
        started=datetime(2026, 7, 29, 14, 0, 0), finished=datetime(2026, 7, 29, 14, 41, 0),
        notes=["eligibility run returned rc=1 (incomplete) — curation may be partial"],
        report_dir=tmp_path / "run_report")

    assert path.name == "refresh_20260729_140000.md"
    body = path.read_text()
    assert "duration **41 min**" in body and "refresh --workers 80" in body
    assert "| ctgov | 2 |" in body and "| anzctr | 1 |" in body               # split by id prefix
    assert "**expired 1**" in body and "`NCT_GONE`" in body
    assert "dry-run" not in body                                              # a real run must never claim dry-run
    assert "**newly curated 2**" in body and "`ACTRN1`, `NCT9`" in body
    assert "| trials curated | 100 | 105 | +5 |" in body
    assert "**1 rows · 1 trials · 1 arms · 2 cols**" in body
    assert "**CONSISTENT**" in body
    assert "empty interpreted DNF (contribute no export row): 2" in body
    assert "ABSENT from the export: 1**" in body and "`NCT9`" in body         # NCT_GONE excluded (expired)
    assert "rc=1 (incomplete)" in body


def test_dry_run_note_comes_from_the_caller_not_the_expiry_flag(tmp_path, monkeypatch):
    """ExpiryReport.applied is False both for a real dry-run AND when nothing needed moving, so the note must
    follow the caller's --dry-run-expiry intent."""
    monkeypatch.setattr(RR, "_coverage", lambda: {"empty_arms": 0, "empty_trials": []})
    monkeypatch.setattr("aus_trial_universe.tasks.eligibility.qa.arm_consistency.check",
                        lambda: {"registry": 0, "elig_refs": 0, "drug_refs": 0, "role_refs": 0,
                                 "elig_dangling": [], "drug_dangling": [], "role_dangling": [], "unused": []})
    common = dict(before={}, after={}, kept=set(), curated_ids=[], export_path=tmp_path / "x.tsv",
                  settings="refresh", started=datetime(2026, 7, 29, 9, 0, 0), finished=datetime(2026, 7, 29, 9, 5, 0))
    nothing_to_do = ExpiryReport(applied=False)                     # a REAL run with no churn
    assert "dry-run" not in RR.write_report(**common, expiry=nothing_to_do, report_dir=tmp_path / "a").read_text()
    assert "dry-run" in RR.write_report(**common, expiry=nothing_to_do, dry_run=True,
                                        report_dir=tmp_path / "b").read_text()


def test_reports_accumulate_and_retain_the_newest_five(tmp_path):
    """Per-run reports are timestamped, so they accumulate rather than overwrite — retention (keep 5, by mtime,
    same rule as the input archives) is what bounds them. STATUS.json is NOT a report and is never pruned."""
    import os
    d = tmp_path / "rr"
    d.mkdir()
    (d / RR.STATUS_FILE).write_text("{}")
    for i in range(8):
        p = d / f"refresh_2026072{i}_120000.md"
        p.write_text(f"report {i}")
        os.utime(p, (1_800_000_000 + i * 60, 1_800_000_000 + i * 60))
    removed = RR.prune_reports(d)
    assert len(removed) == 3
    kept = sorted(p.name for p in d.glob("refresh_*.md"))
    assert kept == [f"refresh_2026072{i}_120000.md" for i in range(3, 8)]      # the 5 newest survive
    assert (d / RR.STATUS_FILE).exists()                                       # status file untouched
    assert RR.prune_reports(d) == []                                           # idempotent at the limit


def test_write_report_prunes_after_writing_status(tmp_path, monkeypatch):
    """Order matters: prune runs AFTER the current run's report and STATUS.json are on disk, so a prune failure
    can never cost the artefacts of the run that just completed."""
    monkeypatch.setattr(RR, "_coverage", lambda: {"empty_arms": 0, "empty_trials": []})
    monkeypatch.setattr("aus_trial_universe.tasks.eligibility.qa.arm_consistency.check",
                        lambda: {"registry": 0, "elig_refs": 0, "drug_refs": 0, "role_refs": 0,
                                 "elig_dangling": [], "drug_dangling": [], "role_dangling": [], "unused": []})
    d = tmp_path / "rr"
    d.mkdir()
    for i in range(6):
        (d / f"refresh_2026070{i}_090000.md").write_text("old")
    path = RR.write_report(
        before={}, after={}, kept=set(), expiry=ExpiryReport(), curated_ids=[],
        export_path=tmp_path / "x.tsv", settings="refresh", started=datetime(2026, 7, 30, 10, 0, 0),
        finished=datetime(2026, 7, 30, 10, 5, 0), report_dir=d)
    assert path.exists() and (d / RR.STATUS_FILE).exists()
    assert len(list(d.glob("refresh_*.md"))) == 5 and path.name in {p.name for p in d.glob("refresh_*.md")}


def test_write_report_survives_a_broken_integrity_check(tmp_path, monkeypatch):
    """A report must never turn a completed run into a failure."""
    monkeypatch.setattr(RR, "_coverage", lambda: {"empty_arms": 0, "empty_trials": []})
    monkeypatch.setattr("aus_trial_universe.tasks.eligibility.qa.arm_consistency.check",
                        lambda: (_ for _ in ()).throw(RuntimeError("store missing")))
    path = RR.write_report(
        before={}, after={}, kept=set(), expiry=ExpiryReport(), curated_ids=[],
        export_path=tmp_path / "nope.tsv", settings="refresh", started=datetime(2026, 7, 29, 1, 2, 3),
        finished=datetime(2026, 7, 29, 1, 3, 3), report_dir=tmp_path / "rr")
    body = path.read_text()
    assert "integrity check unavailable: `store missing`" in body and "MISSING" in body
