"""3NF output split (spec §6.1): run.main() writes regime.tsv / eligibility.tsv / combined.tsv.

Uses --extract-only so no mapping/drug stages run (no API); extract_trial + load_trials are patched
on their source modules (run.main imports them lazily inside the function).
"""
from __future__ import annotations

import csv

from aus_trial_universe.agentic import run
from aus_trial_universe.agentic.tasks.extraction.schema import DnfRow
from aus_trial_universe.agentic.tasks.extraction.workflow import ExtractionResult


def _fake_extract(client, *, trial_id, source_text, cohorts, max_attempts, use_judge):
    # two regimes; Arm A has 2 eligibility conjunctions, Arm B has 1
    def row(cohort, arm_type, gene, drug):
        return DnfRow(trialId="NCT1", cohort=cohort, arm_type=arm_type, cancer_type="NSCLC",
                      gene_alteration=gene, molecular_signature="", molecular_biomarker="",
                      prior_therapy="", drug=drug)
    rows = [row("Arm A", "EXPERIMENTAL", "EGFR", "osi"),
            row("Arm A", "EXPERIMENTAL", "ALK", "osi"),
            row("Arm B", "ACTIVE_COMPARATOR", "", "chemo")]
    return ExtractionResult(rows=rows, faithful=True, attempts=1)


def test_run_writes_3nf_masters_and_combined_view(tmp_path, monkeypatch):
    monkeypatch.setattr("aus_trial_universe.agentic.tasks.extraction.loaders.load_trials",
                        lambda **kw: [("ctgov", "NCT1", "text", None)])
    monkeypatch.setattr("aus_trial_universe.agentic.tasks.extraction.workflow.extract_trial", _fake_extract)

    out = tmp_path / "run"
    rc = run.main(["--ids", "NCT1", "--out-dir", str(out), "--extract-only"])
    assert rc == 0

    regime = list(csv.DictReader(open(out / "regime.tsv"), delimiter="\t"))
    elig = list(csv.DictReader(open(out / "eligibility.tsv"), delimiter="\t"))
    combined = list(csv.DictReader(open(out / "combined.tsv"), delimiter="\t"))

    # regime master = distinct (trialId, cohort) with arm_type + drug (no eligibility columns)
    assert [(r["cohort"], r["arm_type"], r["drug"]) for r in regime] == [
        ("Arm A", "EXPERIMENTAL", "osi"), ("Arm B", "ACTIVE_COMPARATOR", "chemo")]
    assert "cancer_type" not in regime[0]
    # eligibility master = per-conjunction cells; conj_id resets per (trialId, cohort); no arm_type
    assert [(e["cohort"], e["conj_id"], e["gene_alteration"]) for e in elig] == [
        ("Arm A", "1", "EGFR"), ("Arm A", "2", "ALK"), ("Arm B", "1", "")]
    assert "arm_type" not in elig[0]
    # combined view = the join (carries regime + eligibility columns + conj_id)
    assert len(combined) == 3
    assert (combined[0]["arm_type"], combined[0]["gene_alteration"], combined[0]["conj_id"]) == \
        ("EXPERIMENTAL", "EGFR", "1")
