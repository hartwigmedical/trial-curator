"""A failing trial must not kill the batch: run.main() logs it, keeps the others' output, continues.

main() imports its collaborators lazily (`from ... import ...` inside the function), so patching the
source-module attributes before the call swaps them in. --extract-only keeps it hermetic (no mapping/drug/API);
--store-root points at a fresh tmp dir.
"""
from __future__ import annotations

import csv

from aus_trial_universe.agentic import run
from aus_trial_universe.agentic.tasks.eligibility.extraction.schema import DnfRow
from aus_trial_universe.agentic.tasks.eligibility.extraction.workflow import ExtractionResult


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
    return ExtractionResult(rows=[row], faithful=True, attempts=1)


def test_batch_continues_past_a_failing_trial(tmp_path, monkeypatch):
    monkeypatch.setattr("aus_trial_universe.agentic.tasks.eligibility.extraction.loaders.load_trials",
                        lambda **kw: _fake_trials())
    monkeypatch.setattr("aus_trial_universe.agentic.tasks.eligibility.extraction.workflow.extract_trial", _fake_extract)

    rc = run.main(["--ids", "GOOD1,BADX,GOOD2", "--store-root", str(tmp_path), "--extract-only"])

    assert rc == 0                                  # batch completes despite the failure
    snap = next(d for d in tmp_path.iterdir() if d.is_dir())
    rows = list(csv.DictReader(open(snap / "combined.tsv"), delimiter="\t"))
    assert sorted({r["trialId"] for r in rows}) == ["GOOD1", "GOOD2"]   # both good trials written, bad one skipped
    # the 3NF masters are written alongside the combined view (spec §6.1)
    assert (snap / "regime.tsv").exists() and (snap / "extracted_eligibility.tsv").exists()
