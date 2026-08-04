"""Unattended-operation contract: a bad cycle must be VISIBLE to whatever schedules the pipeline.

The failure chain is run.py (rc=3 when trials are missing) -> refresh.py (exit code) -> pipeline.sh (set -euo
pipefail). The middle link is the one that was broken: refresh returned 0 unconditionally, so a partially-curated
store still reported success and still shipped an export. These tests pin the contract so it cannot regress.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from aus_trial_universe import refresh as R
from aus_trial_universe import run_report as RR
from aus_trial_universe.qa import gates as G
from aus_trial_universe.tasks.ingestion.expiry import ExpiryReport


@pytest.fixture
def stub_stages(monkeypatch, tmp_path):
    """Neutralise every stage so only the exit-code / status wiring is under test."""
    from aus_trial_universe import export as export_mod
    from aus_trial_universe import run as run_mod
    from aus_trial_universe.tasks.drug_utility import build as drug_build
    from aus_trial_universe.tasks.drug_utility import map_approvals as approvals_mod
    from aus_trial_universe.tasks.eligibility.store import EligStore
    from aus_trial_universe.tasks.ingestion import expiry as expiry_mod

    export_file = tmp_path / "trial_eligibility.tsv"
    export_file.write_text("trialId\ttrial_arm_id\nNCT1\tNCT1::A\n", encoding="utf-8")

    store = EligStore()
    store.raw = {"NCT1": []}
    monkeypatch.setattr(EligStore, "load", classmethod(lambda cls, *a, **k: store))
    monkeypatch.setattr(expiry_mod, "compute_kept_ids", lambda *, log: {"NCT1"})
    monkeypatch.setattr(expiry_mod, "run_expiry", lambda **k: ExpiryReport(applied=True))
    monkeypatch.setattr(run_mod, "main", lambda argv: 0)
    monkeypatch.setattr(R, "_scope_pass", lambda log, **k: {})
    monkeypatch.setattr(drug_build, "main", lambda argv: 0)
    monkeypatch.setattr(approvals_mod, "main", lambda argv: 0)
    monkeypatch.setattr(export_mod, "run_export", lambda *a, **k: export_file)
    monkeypatch.setattr(RR, "snapshot", lambda: {"trials curated": 1})
    monkeypatch.setattr(RR, "previous_kept_count", lambda *a, **k: None)
    written = {}
    monkeypatch.setattr(RR, "write_report",
                        lambda **kw: (written.update(kw), tmp_path / "report.md")[1])
    return written


def _report(*statuses) -> G.GateReport:
    rep = G.GateReport()
    for i, s in enumerate(statuses):
        rep.add(f"gate{i}", s, "detail")
    return rep


def test_refresh_exits_zero_when_gates_pass(stub_stages, monkeypatch):
    monkeypatch.setattr(G, "run_gates", lambda **k: _report(G.PASS, G.WARN))
    assert R.main(["--skip-ingest"]) == 0


def test_refresh_exits_non_zero_when_a_gate_fails(stub_stages, monkeypatch):
    """The production-critical case: a failing cycle must NOT report success."""
    monkeypatch.setattr(G, "run_gates", lambda **k: _report(G.PASS, G.FAIL))
    assert R.main(["--skip-ingest"]) == 1


def test_refresh_passes_the_eligibility_rc_to_the_gates(stub_stages, monkeypatch):
    """run.py's rc=3 (trials still missing) must reach the gates rather than being swallowed into a log line."""
    from aus_trial_universe import run as run_mod
    monkeypatch.setattr(run_mod, "main", lambda argv: 3)
    seen = {}
    monkeypatch.setattr(G, "run_gates", lambda **k: (seen.update(k), _report(G.PASS))[1])
    R.main(["--skip-ingest"])
    assert seen["elig_rc"] == 3
    assert any("rc=3" in n for n in stub_stages["notes"])       # and it is recorded in the report


def test_status_json_marks_a_failed_cycle(tmp_path, monkeypatch):
    """STATUS.json is what an external monitor polls — it must say `fail` and name the failing gates."""
    monkeypatch.setattr(RR, "_coverage", lambda: {"empty_arms": 0, "empty_trials": []})
    monkeypatch.setattr("aus_trial_universe.qa.arm_consistency.check",
                        lambda: {"registry": 1, "elig_refs": 1, "drug_refs": 1, "role_refs": 1,
                                 "elig_dangling": [], "drug_dangling": [], "role_dangling": [], "unused": []})
    gates = G.GateReport()
    gates.add("fk_integrity", G.PASS, "clean")
    gates.add("empty_output_reasons", G.FAIL, "3 arm(s) UNEXPLAINED")
    common = dict(before={}, after={}, kept={"NCT1"}, expiry=ExpiryReport(applied=True), curated_ids=[],
                  export_path=tmp_path / "none.tsv", settings="refresh", started=datetime(2026, 7, 29, 22, 0, 0),
                  finished=datetime(2026, 7, 29, 22, 30, 0), report_dir=tmp_path / "rr")
    RR.write_report(**common, gates=gates)

    status = json.loads((tmp_path / "rr" / RR.STATUS_FILE).read_text())
    assert status["status"] == "fail" and status["gates_verdict"] == G.FAIL
    assert [g["name"] for g in status["gates"] if g["status"] == G.FAIL] == ["empty_output_reasons"]
    assert status["universe"]["kept"] == 1 and status["duration_min"] == 30.0
    assert "GATES: FAIL" in (tmp_path / "rr" / "refresh_20260729_220000.md").read_text()

    # a clean cycle overwrites it with ok, and previous_kept_count reads the kept size back for the swing gate
    gates_ok = G.GateReport()
    gates_ok.add("fk_integrity", G.PASS, "clean")
    RR.write_report(**common, gates=gates_ok)
    assert json.loads((tmp_path / "rr" / RR.STATUS_FILE).read_text())["status"] == "ok"
    assert RR.previous_kept_count(tmp_path / "rr") == 1
