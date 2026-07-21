"""3NF eligibility output (spec §6.1): run.main() writes the relational masters + the grand flat view.

Uses --extract-only so no mapping/drug stages run (no API); extract_trial + load_trials are patched on their
source modules (run.main imports them lazily inside the function). --store-root points at a fresh tmp dir so the
accumulating store starts empty.
"""
from __future__ import annotations

import csv

from aus_trial_universe.agentic import run
from aus_trial_universe.agentic.tasks.eligibility.extraction.schema import DnfRow
from aus_trial_universe.agentic.tasks.eligibility.extraction.workflow import ExtractionResult


def _fake_extract(client, *, trial_id, source_text, cohorts, max_attempts, use_judge):
    # two regimes; Arm A has 2 eligibility conjunctions, Arm B has 1
    def row(arm, arm_type, gene, drug):
        return DnfRow(trialId="NCT1", cohort=arm, arm_type=arm_type, cancer_type="NSCLC",
                      gene_alteration=gene, molecular_signature="", molecular_biomarker="",
                      prior_therapy="", drug=drug)
    rows = [row("Arm A", "EXPERIMENTAL", "EGFR", "osi"),
            row("Arm A", "EXPERIMENTAL", "ALK", "osi"),
            row("Arm B", "ACTIVE_COMPARATOR", "", "chemo")]
    return ExtractionResult(rows=rows, faithful=True, attempts=1)


def test_run_writes_3nf_masters_and_combined_view(tmp_path, monkeypatch):
    monkeypatch.setattr("aus_trial_universe.agentic.tasks.eligibility.extraction.loaders.load_trials",
                        lambda **kw: [("ctgov", "NCT1", "text", None)])
    monkeypatch.setattr("aus_trial_universe.agentic.tasks.eligibility.extraction.workflow.extract_trial", _fake_extract)

    rc = run.main(["--ids", "NCT1", "--store-root", str(tmp_path), "--extract-only"])
    assert rc == 0
    snap = next(d for d in tmp_path.iterdir() if d.is_dir())   # the run's timestamp snapshot

    regime = list(csv.DictReader(open(snap / "regime.tsv"), delimiter="\t"))
    elig = list(csv.DictReader(open(snap / "extracted_eligibility.tsv"), delimiter="\t"))
    combined = list(csv.DictReader(open(snap / "combined.tsv"), delimiter="\t"))

    # regime master = distinct (trialId, arm, arm_type); NO drug, NO eligibility columns (join key to drug path)
    assert [(r["arm"], r["arm_type"]) for r in regime] == [
        ("Arm A", "EXPERIMENTAL"), ("Arm B", "ACTIVE_COMPARATOR")]
    assert "drug" not in regime[0] and "cancer_type" not in regime[0]
    # extracted_eligibility master = per-conjunction cells; conj_id resets per (trialId, arm); keyed by arm
    assert [(e["arm"], e["conj_id"], e["gene_alteration"]) for e in elig] == [
        ("Arm A", "1", "EGFR"), ("Arm A", "2", "ALK"), ("Arm B", "1", "")]
    # combined view = the flat join (arm + arm_type + conj_id + cells; mapping/drug cols empty under --extract-only)
    assert len(combined) == 3
    assert (combined[0]["arm"], combined[0]["arm_type"], combined[0]["gene_alteration"], combined[0]["conj_id"]) == \
        ("Arm A", "EXPERIMENTAL", "EGFR", "1")
    assert combined[0]["oncotree_code"] == "" and combined[0]["arm_drugs"] == ""   # no mapping/drug under extract-only
