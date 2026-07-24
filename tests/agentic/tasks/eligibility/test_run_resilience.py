"""A failing trial must not kill the batch: run.main() logs it, keeps the others' output, continues.

main() imports its collaborators lazily (`from ... import ...` inside the function), so patching the
source-module attributes before the call swaps them in. --extract-only keeps it hermetic (no mapping/drug/API);
--store-root points at a fresh tmp dir.
"""
from __future__ import annotations

import csv

from aus_trial_universe.agentic import run
from aus_trial_universe.agentic.tasks.eligibility.extraction.schema import DnfRow
from aus_trial_universe.agentic.tasks.eligibility.extraction.workflow import ArmRaw, ExtractionResult


def _fake_trials():
    return [
        ("ctgov", "GOOD1", "text", None),
        ("ctgov", "BADX", "text", None),   # this one raises mid-pipeline
        ("ctgov", "GOOD2", "text", None),
    ]


def _fake_extract(client, *, trial_id, source_text, cohorts, max_attempts, use_judge):
    if trial_id == "BADX":
        raise RuntimeError("simulated transient API failure")
    row = DnfRow(trialId=trial_id, cohort="all", arm_type="", cancer_type="NSCLC",
                 gene_alteration="", molecular_signature="", molecular_biomarker="",
                 prior_therapy="", drug="")
    return ExtractionResult(arm_raw=[ArmRaw(arm="all", cancer_type_raw="NSCLC [CONDITIONS]")],
                            rows=[row], faithful=True, attempts=1)


def test_batch_continues_past_a_failing_trial(tmp_path, monkeypatch):
    monkeypatch.setattr("aus_trial_universe.agentic.tasks.eligibility.extraction.loaders.load_trials",
                        lambda **kw: _fake_trials())
    monkeypatch.setattr("aus_trial_universe.agentic.tasks.eligibility.extraction.workflow.extract_trial", _fake_extract)

    rc = run.main(["--ids", "GOOD1,BADX,GOOD2", "--store-root", str(tmp_path), "--extract-only"])

    assert rc == 0                                  # batch completes despite the failure
    snap = tmp_path / "current_output"              # the 3NF store
    interp = list(csv.DictReader(open(snap / "interpreted_eligibility.tsv"), delimiter="\t"))
    assert sorted({r["trialId"] for r in interp}) == ["GOOD1", "GOOD2"]   # both good written, bad one skipped
    assert (snap / "trial_arms.tsv").exists() and (snap / "arm_eligibility_raw.tsv").exists()
    assert not (snap / "combined.tsv").exists()     # combined parked under --extract-only
