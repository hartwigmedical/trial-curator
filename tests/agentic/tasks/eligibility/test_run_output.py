"""3NF eligibility output (spec §6.1): run.main() writes the relational masters (trial_arms,
arm_eligibility_raw, interpreted_eligibility).

Uses --extract-only so no mapping/drug/combined stages run (no API; combined.tsv is parked). extract_trial +
load_trials are patched on their source modules (run.main imports them lazily inside the function). --store-root
points at a fresh tmp dir so the accumulating store starts empty.
"""
from __future__ import annotations

import csv

from aus_trial_universe.agentic import run
from aus_trial_universe.agentic.tasks.eligibility.extraction.schema import DnfRow
from aus_trial_universe.agentic.tasks.eligibility.extraction.workflow import ArmRaw, ExtractionResult


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
    return ExtractionResult(arm_raw=arm_raw, rows=rows, faithful=True, attempts=1)


def test_run_writes_3nf_masters(tmp_path, monkeypatch):
    monkeypatch.setattr("aus_trial_universe.agentic.tasks.eligibility.extraction.loaders.load_trials",
                        lambda **kw: [("ctgov", "NCT1", "text", None)])
    monkeypatch.setattr("aus_trial_universe.agentic.tasks.eligibility.extraction.workflow.extract_trial", _fake_extract)

    rc = run.main(["--ids", "NCT1", "--store-root", str(tmp_path), "--extract-only"])
    assert rc == 0
    snap = tmp_path / "current_output"                         # the 3NF store (pure-3NF masters only)

    arms = list(csv.DictReader(open(snap / "trial_arms.tsv"), delimiter="\t"))
    raw = list(csv.DictReader(open(snap / "arm_eligibility_raw.tsv"), delimiter="\t"))
    interp = list(csv.DictReader(open(snap / "interpreted_eligibility.tsv"), delimiter="\t"))
    # combined.tsv is PARKED — never built under --extract-only
    assert not (snap / "combined.tsv").exists()
    assert not (tmp_path / "combined" / "combined.tsv").exists()

    # trial_arms master = distinct (trialId, arm, arm_type); NO drug, NO eligibility columns (join key to drug path)
    assert [(a["arm"], a["arm_type"]) for a in arms] == [
        ("Arm A", "EXPERIMENTAL"), ("Arm B", "ACTIVE_COMPARATOR")]
    assert "drug" not in arms[0] and "cancer_type" not in arms[0]
    # arm_eligibility_raw master = per-arm verbatim text (trial-wide replicated onto each arm), with source tags
    assert [(r["arm"], r["cancer_type_raw"]) for r in raw] == [
        ("Arm A", "advanced NSCLC [CONDITIONS]"), ("Arm B", "advanced NSCLC [CONDITIONS]")]
    # interpreted_eligibility master = per-conjunction cells (no source tags); conjunction_index resets per arm
    assert [(e["arm"], e["conjunction_index"], e["gene_alteration_interpreted"]) for e in interp] == [
        ("Arm A", "1", "EGFR"), ("Arm A", "2", "ALK"), ("Arm B", "1", "")]
    assert "cancer_type_interpreted" in interp[0] and interp[0]["cancer_type_interpreted"] == "NSCLC"


def _fake_extract_by_id(client, *, trial_id, source_text, cohorts, max_attempts, use_judge):
    row = DnfRow(trialId=trial_id, cohort="all", arm_type="", cancer_type="NSCLC", gene_alteration="",
                 molecular_signature="", molecular_biomarker="", prior_therapy="", drug="")
    return ExtractionResult(arm_raw=[ArmRaw(arm="all", cancer_type_raw="NSCLC [CONDITIONS]")],
                            rows=[row], faithful=True, attempts=1)


def test_run_accumulates_trials_across_runs(tmp_path, monkeypatch):
    """The accumulating store: a 2nd run into the same store-root LOADS the 1st run's snapshot and carries its
    trials forward (regression — run_dir must be created AFTER load, or the empty new dir shadows the latest)."""
    monkeypatch.setattr("aus_trial_universe.agentic.tasks.eligibility.extraction.loaders.load_trials",
                        lambda **kw: [("ctgov", i, "text", None) for i in (kw.get("ids") or [])])
    monkeypatch.setattr("aus_trial_universe.agentic.tasks.eligibility.extraction.workflow.extract_trial",
                        _fake_extract_by_id)

    run.main(["--ids", "NCTA", "--store-root", str(tmp_path),
              "--out-dir", str(tmp_path / "20260101_000001"), "--extract-only"])
    run.main(["--ids", "NCTB", "--store-root", str(tmp_path),
              "--out-dir", str(tmp_path / "20260101_000002"), "--extract-only"])

    arms = list(csv.DictReader(open(tmp_path / "20260101_000002" / "trial_arms.tsv"), delimiter="\t"))
    assert {a["trialId"] for a in arms} == {"NCTA", "NCTB"}   # 2nd run carried the 1st forward
