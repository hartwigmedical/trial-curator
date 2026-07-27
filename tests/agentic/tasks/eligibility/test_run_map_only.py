"""`run.main(--map-only)` (Step 1 mapping): map the EXISTING store's interpreted cells into the 3 value->vocab
tables, leaving the raw + interpreted content tables untouched. The three map_* workflows are patched (no API); this
pins the map-only ORCHESTRATION, not the mappers (those are covered by test_mapping_*).
"""
from __future__ import annotations

import csv

from aus_trial_universe.agentic import run
import aus_trial_universe.agentic.tasks.eligibility.mapping.workflow as mw
from aus_trial_universe.agentic.tasks.eligibility.mapping.workflow import FindingModelResult, OncotreeResult
from aus_trial_universe.agentic.tasks.eligibility.schema import ArmEligibilityRaw, InterpretedEligibility
from aus_trial_universe.agentic.tasks.eligibility.store import EligStore


def _seed(store_root):
    s = EligStore()
    s.set_trial(
        "NCT1",
        [ArmEligibilityRaw(trial_arm_id="NCT1::A", cancer_type_raw="advanced NSCLC [CONDITIONS]")],
        [InterpretedEligibility(trial_arm_id="NCT1::A", conjunction_index=1,
                                cancer_type_interpreted="advanced NSCLC",
                                gene_alteration_interpreted="EGFR L858R",
                                molecular_signature_interpreted="MSI-H")],
    )
    s.save(store_root / "current_output")


def _read(path):
    return list(csv.DictReader(open(path), delimiter="\t"))


def test_map_only_writes_maps_and_preserves_content(tmp_path, monkeypatch):
    store_root = tmp_path / "elig"
    _seed(store_root)
    monkeypatch.setattr(mw, "map_cancer_types", lambda c, cells, **k: {
        "advanced NSCLC": OncotreeResult("advanced NSCLC", "Non-Small Cell Lung Cancer", "NSCLC", True, 1, [])})
    monkeypatch.setattr(mw, "map_gene_alterations", lambda c, cells, **k: {
        "EGFR L858R": FindingModelResult(
            "EGFR L858R", "SmallVariant[gene=EGFR & transcriptImpact.hgvsProteinImpact=p.L858R]", True, 1, [])})
    monkeypatch.setattr(mw, "map_molecular_signatures", lambda c, cells, **k: {
        "MSI-H": FindingModelResult("MSI-H", "MicrosatelliteStability[PurpleMicrosatelliteStatus=MSI]", True, 1, [])})

    rc = run.main(["--map-only", "--store-root", str(store_root), "--no-cache", "--no-cache-prune", "--workers", "2"])
    assert rc == 0

    cur = store_root / "current_output"
    assert _read(cur / "cancer_type_map.tsv") == [
        {"cancer_type": "advanced NSCLC", "oncotree_name": "Non-Small Cell Lung Cancer", "oncotree_code": "NSCLC"}]
    assert _read(cur / "gene_alteration_map.tsv")[0]["finding_model"].startswith("SmallVariant[gene=EGFR")
    assert _read(cur / "molecular_signature_map.tsv")[0]["molecular_signature"] == "MSI-H"

    # content tables untouched (same rows as seeded)
    interp = _read(cur / "interpreted_eligibility.tsv")
    assert len(interp) == 1 and interp[0]["cancer_type_interpreted"] == "advanced NSCLC"
    raw = _read(cur / "arm_eligibility_raw.tsv")
    assert len(raw) == 1 and raw[0]["trial_arm_id"] == "NCT1::A"
