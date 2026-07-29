"""3NF eligibility output (spec §6.1): run.main() writes the shared trial_arms registry + the eligibility content
masters (arm_eligibility_raw, interpreted_eligibility), all keyed by trial_arm_id.

Uses --extract-only so no mapping/drug/combined stages run (no API; combined.tsv is parked). extract_trial +
load_trials are patched on their source modules (run.main imports them lazily inside the function). --store-root
points at a fresh tmp dir so the eligibility store starts empty; the shared trial_arms registry is redirected to a
tmp dir per test via the autouse fixture.
"""
from __future__ import annotations

import csv

import pytest

from aus_trial_universe import run
from aus_trial_universe.core import paths as _paths
from aus_trial_universe.tasks.eligibility.extraction.schema import DnfRow
from aus_trial_universe.tasks.eligibility.extraction.workflow import ArmRaw, Cohort, ExtractionResult
from aus_trial_universe.tasks.shared.cohorts import trial_arm_id


@pytest.fixture(autouse=True)
def _isolate_trial_arms(tmp_path, monkeypatch):
    """Redirect the SHARED trial_arms registry to a tmp dir so a run's arm writes don't touch real data."""
    monkeypatch.setattr(_paths, "TRIAL_ARMS_ROOT", tmp_path / "trial_arms_registry")


def _trial_arms_rows():
    path = _paths.TRIAL_ARMS_ROOT / "current_version" / "trial_arms.tsv"
    return list(csv.DictReader(open(path), delimiter="\t"))


def _fake_extract(client, *, trial_id, source_text, cohorts, max_attempts, use_judge):
    # two regimes; Arm A has 2 eligibility conjunctions, Arm B has 1
    def row(arm, arm_type, gene, drug):
        return DnfRow(trialId="NCT1", cohort=arm, arm_type=arm_type, cancer_type="NSCLC",
                      gene_alteration=gene, molecular_signature="", molecular_biomarker="",
                      prior_therapy="", drug=drug)
    rows = [row("Arm A", "EXPERIMENTAL", "EGFR", "osi"),
            row("Arm A", "EXPERIMENTAL", "ALK", "osi"),
            row("Arm B", "ACTIVE_COMPARATOR", "", "chemo")]
    arm_raw = [ArmRaw(arm="Arm A", cancer_type_raw="advanced NSCLC [CONDITIONS]"),
               ArmRaw(arm="Arm B", cancer_type_raw="advanced NSCLC [CONDITIONS]")]
    resolved = [Cohort(label="Arm A", arm_type="EXPERIMENTAL"), Cohort(label="Arm B", arm_type="ACTIVE_COMPARATOR")]
    return ExtractionResult(arm_raw=arm_raw, rows=rows, faithful=True, attempts=1, cohorts=resolved)


def test_run_writes_3nf_masters(tmp_path, monkeypatch):
    monkeypatch.setattr("aus_trial_universe.tasks.eligibility.extraction.loaders.load_trials",
                        lambda **kw: [("ctgov", "NCT1", "text", None)])
    monkeypatch.setattr("aus_trial_universe.tasks.eligibility.extraction.workflow.extract_trial", _fake_extract)

    rc = run.main(["--ids", "NCT1", "--store-root", str(tmp_path), "--extract-only"])
    assert rc == 0
    snap = tmp_path / "current_version"                         # the eligibility store (content masters only)

    arms = _trial_arms_rows()                                  # the SHARED registry
    raw = list(csv.DictReader(open(snap / "arm_eligibility_raw.tsv"), delimiter="\t"))
    interp = list(csv.DictReader(open(snap / "interpreted_eligibility.tsv"), delimiter="\t"))
    # trial_arms moved OUT of the eligibility store into the shared registry
    assert not (snap / "trial_arms.tsv").exists()
    # combined.tsv is PARKED — never built under --extract-only
    assert not (snap / "combined.tsv").exists() and not (tmp_path / "combined" / "combined.tsv").exists()

    # shared trial_arms registry = (trial_arm_id, trialId, registry, arm, arm_type); NO drug/eligibility columns
    aa, ab = trial_arm_id("NCT1", "Arm A"), trial_arm_id("NCT1", "Arm B")
    assert [(a["trial_arm_id"], a["trialId"], a["registry"], a["arm"], a["arm_type"]) for a in arms] == [
        (aa, "NCT1", "ctgov", "Arm A", "EXPERIMENTAL"), (ab, "NCT1", "ctgov", "Arm B", "ACTIVE_COMPARATOR")]
    assert "drug" not in arms[0] and "cancer_type" not in arms[0]
    # arm_eligibility_raw links by trial_arm_id (trial-wide text replicated onto each arm), with source tags
    assert [(r["trial_arm_id"], r["cancer_type_raw"]) for r in raw] == [
        (aa, "advanced NSCLC [CONDITIONS]"), (ab, "advanced NSCLC [CONDITIONS]")]
    # interpreted_eligibility links by trial_arm_id; conjunction_index resets per arm
    assert [(e["trial_arm_id"], e["conjunction_index"], e["gene_alteration_interpreted"]) for e in interp] == [
        (aa, "1", "EGFR"), (aa, "2", "ALK"), (ab, "1", "")]
    assert "cancer_type_interpreted" in interp[0] and interp[0]["cancer_type_interpreted"] == "NSCLC"


def _fake_extract_by_id(client, *, trial_id, source_text, cohorts, max_attempts, use_judge):
    row = DnfRow(trialId=trial_id, cohort="all", arm_type="", cancer_type="NSCLC", gene_alteration="",
                 molecular_signature="", molecular_biomarker="", prior_therapy="", drug="")
    return ExtractionResult(arm_raw=[ArmRaw(arm="all", cancer_type_raw="NSCLC [CONDITIONS]")],
                            rows=[row], faithful=True, attempts=1, cohorts=[Cohort(label="all")])


def test_run_accumulates_trials_across_runs(tmp_path, monkeypatch):
    """The accumulating stores: a 2nd run LOADS the 1st run's snapshot (eligibility) and the shared trial_arms
    registry, carrying prior trials forward (regression — run_dir must be created AFTER load)."""
    monkeypatch.setattr("aus_trial_universe.tasks.eligibility.extraction.loaders.load_trials",
                        lambda **kw: [("ctgov", i, "text", None) for i in (kw.get("ids") or [])])
    monkeypatch.setattr("aus_trial_universe.tasks.eligibility.extraction.workflow.extract_trial",
                        _fake_extract_by_id)

    run.main(["--ids", "NCTA", "--store-root", str(tmp_path),
              "--out-dir", str(tmp_path / "20260101_000001"), "--extract-only"])
    run.main(["--ids", "NCTB", "--store-root", str(tmp_path),
              "--out-dir", str(tmp_path / "20260101_000002"), "--extract-only"])

    assert {a["trialId"] for a in _trial_arms_rows()} == {"NCTA", "NCTB"}   # 2nd run carried the 1st forward


def test_anzctr_arms_derived_fresh_land_in_trial_arms(tmp_path, monkeypatch):
    """ANZCTR arms are derived FRESH via the shared cohort module inside extract_trial (cohorts=None passed) — NOT
    adopted from the drug store — and land in the shared trial_arms registry keyed by trial_arm_id (registry=anzctr)."""
    captured: dict = {}

    def _capture_extract(client, *, trial_id, source_text, cohorts, max_attempts, use_judge):
        captured["cohorts_arg"] = cohorts   # ANZCTR must pass None (derive fresh), not adopted cohorts
        resolved = [Cohort(label="intervention", arm_type="EXPERIMENTAL"),
                    Cohort(label="comparator", arm_type="ACTIVE_COMPARATOR")]
        rows = [DnfRow(trialId=trial_id, cohort=c.label, arm_type=c.arm_type, cancer_type="X", gene_alteration="",
                       molecular_signature="", molecular_biomarker="", prior_therapy="", drug="") for c in resolved]
        arm_raw = [ArmRaw(arm=c.label, cancer_type_raw="X [C]") for c in resolved]
        return ExtractionResult(arm_raw=arm_raw, rows=rows, faithful=True, attempts=1, cohorts=resolved)

    monkeypatch.setattr("aus_trial_universe.tasks.eligibility.extraction.loaders.load_trials",
                        lambda **kw: [("anzctr", "ACTRN1", "text", None)])
    monkeypatch.setattr("aus_trial_universe.tasks.eligibility.extraction.workflow.extract_trial",
                        _capture_extract)

    rc = run.main(["--ids", "ACTRN1", "--store-root", str(tmp_path), "--extract-only"])
    assert rc == 0
    assert captured["cohorts_arg"] is None   # fresh derivation, no drug-store adoption
    arms = _trial_arms_rows()
    assert [(a["trial_arm_id"], a["registry"], a["arm"], a["arm_type"]) for a in arms] == [
        (trial_arm_id("ACTRN1", "intervention"), "anzctr", "intervention", "EXPERIMENTAL"),
        (trial_arm_id("ACTRN1", "comparator"), "anzctr", "comparator", "ACTIVE_COMPARATOR")]


def _resumable_extract(calls):
    def _fake(client, *, trial_id, source_text, cohorts, max_attempts, use_judge):
        calls.append(trial_id)
        if trial_id == "FAILME":
            raise RuntimeError("boom")
        row = DnfRow(trialId=trial_id, cohort="all", arm_type="", cancer_type="X", gene_alteration="",
                     molecular_signature="", molecular_biomarker="", prior_therapy="", drug="")
        return ExtractionResult(arm_raw=[ArmRaw(arm="all", cancer_type_raw="X [C]")], rows=[row],
                                faithful=True, attempts=1, cohorts=[Cohort(label="all")])
    return _fake


def test_resume_skips_completed_and_returns_0_when_all_present(tmp_path, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr("aus_trial_universe.tasks.eligibility.extraction.loaders.load_trials",
                        lambda **kw: [("ctgov", i, "text", None) for i in (kw.get("ids") or [])])
    monkeypatch.setattr("aus_trial_universe.tasks.eligibility.extraction.workflow.extract_trial",
                        _resumable_extract(calls))
    out = str(tmp_path / "current_version")
    run.main(["--ids", "NCTA", "--store-root", str(tmp_path), "--out-dir", out, "--extract-only"])
    assert calls == ["NCTA"]
    calls.clear()
    rc = run.main(["--ids", "NCTA,NCTB", "--store-root", str(tmp_path), "--out-dir", out, "--extract-only", "--resume"])
    assert calls == ["NCTB"]   # NCTA skipped (already done); only NCTB processed
    assert rc == 0             # all requested now present


def test_resume_returns_3_when_a_trial_is_missing(tmp_path, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr("aus_trial_universe.tasks.eligibility.extraction.loaders.load_trials",
                        lambda **kw: [("ctgov", i, "text", None) for i in (kw.get("ids") or [])])
    monkeypatch.setattr("aus_trial_universe.tasks.eligibility.extraction.workflow.extract_trial",
                        _resumable_extract(calls))
    rc = run.main(["--ids", "GOODA,FAILME", "--store-root", str(tmp_path),
                   "--out-dir", str(tmp_path / "current_version"), "--extract-only", "--resume"])
    assert rc == 3   # FAILME failed -> still missing -> non-zero so a loop driver retries
