"""A failing trial must not kill the batch: run.main() logs it, keeps its output, continues.

main() imports its collaborators lazily (`from ... import ...` inside the function), so patching the
source-module attributes before the call swaps them in.
"""
from __future__ import annotations

import csv

from aus_trial_universe.agentic import run
from aus_trial_universe.agentic.tasks.extraction.schema import DnfRow
from aus_trial_universe.agentic.tasks.extraction.workflow import ExtractionResult
from aus_trial_universe.agentic.tasks.mapping.workflow import DrugCurationResult


def _fake_trials():
    return [
        ("ctgov", "GOOD1", "text", None),
        ("ctgov", "BADX", "text", None),   # this one raises mid-pipeline
        ("ctgov", "GOOD2", "text", None),
    ]


def _fake_extract(client, *, trial_id, source_text, cohorts, max_attempts, use_judge):
    if trial_id == "BADX":
        raise RuntimeError("simulated transient API failure")
    row = DnfRow(trialId=trial_id, cohort="(all)", arm_type="", cancer_type="NSCLC",
                 gene_alteration="", molecular_signature="", molecular_biomarker="",
                 prior_therapy="", drug="")
    return ExtractionResult(rows=[row], faithful=True, attempts=1)


def test_batch_continues_past_a_failing_trial(tmp_path, monkeypatch):
    monkeypatch.setattr("aus_trial_universe.agentic.tasks.extraction.loaders.load_trials",
                        lambda **kw: _fake_trials())
    monkeypatch.setattr("aus_trial_universe.agentic.tasks.extraction.workflow.extract_trial", _fake_extract)
    for fn in ("map_cancer_types", "map_gene_alterations", "map_molecular_signatures"):
        monkeypatch.setattr(f"aus_trial_universe.agentic.tasks.mapping.workflow.{fn}", lambda *a, **k: {})
    monkeypatch.setattr("aus_trial_universe.agentic.tasks.mapping.workflow.curate_drugs",
                        lambda *a, **k: DrugCurationResult())

    out = tmp_path / "out.tsv"
    rc = run.main(["--ids", "GOOD1,BADX,GOOD2", "--out", str(out)])

    assert rc == 0                                  # batch completes despite the failure
    rows = list(csv.DictReader(open(out), delimiter="\t"))
    ids = [r["trialId"] for r in rows]
    assert ids == ["GOOD1", "GOOD2"]                # both good trials written, bad one skipped
