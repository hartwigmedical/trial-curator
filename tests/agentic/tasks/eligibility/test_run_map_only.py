"""`run.main(--map-only)` (Step 1 mapping): map the EXISTING store's interpreted cells into the 3 value->vocab
tables, leaving the raw + interpreted content tables untouched. The three map_* workflows are patched (no API); this
pins the map-only ORCHESTRATION, not the mappers (those are covered by test_mapping_*).
"""
from __future__ import annotations

import csv

from aus_trial_universe import run
import aus_trial_universe.tasks.eligibility.mapping.workflow as mw
from aus_trial_universe.tasks.eligibility.mapping.workflow import FindingModelResult, OncotreeResult
from aus_trial_universe.tasks.eligibility.schema import ArmEligibilityRaw, InterpretedEligibility
from aus_trial_universe.tasks.eligibility.store import EligStore


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
    s.save(store_root / "current_version")


def _read(path):
    return list(csv.DictReader(open(path), delimiter="\t"))


def test_map_only_writes_maps_and_preserves_content(tmp_path, monkeypatch):
    store_root = tmp_path / "elig"
    _seed(store_root)
    cur = store_root / "current_version"
    # capture the exact bytes of the content tables — the map-only pass must NOT touch them at all.
    raw_bytes = (cur / "arm_eligibility_raw.tsv").read_bytes()
    interp_bytes = (cur / "interpreted_eligibility.tsv").read_bytes()

    # patch the single pooled mapper (map-only now maps all 3 columns in one pool via map_all_columns)
    monkeypatch.setattr(mw, "map_all_columns", lambda c, ct_cells, ga_cells, sig_cells, **k: (
        {"advanced NSCLC": OncotreeResult("advanced NSCLC", "Non-Small Cell Lung Cancer", "NSCLC", True, 1, [])},
        {"EGFR L858R": FindingModelResult(
            "EGFR L858R", "SmallVariant[gene=EGFR & transcriptImpact.hgvsProteinImpact=p.L858R]", True, 1, [])},
        {"MSI-H": FindingModelResult("MSI-H", "MicrosatelliteStability[PurpleMicrosatelliteStatus=MSI]", True, 1, [])},
    ))

    rc = run.main(["--map-only", "--store-root", str(store_root), "--no-cache", "--no-cache-prune", "--workers", "2"])
    assert rc == 0

    # the 3 map tables
    assert _read(cur / "cancer_type_map_initial.tsv") == [
        {"cancer_type": "advanced NSCLC", "oncotree_name_initial": "Non-Small Cell Lung Cancer",
         "oncotree_code_initial": "NSCLC"}]
    assert _read(cur / "gene_alteration_map.tsv")[0]["finding_model"].startswith("SmallVariant[gene=EGFR")
    assert _read(cur / "molecular_signature_map.tsv")[0]["molecular_signature"] == "MSI-H"

    # current_version/ stays strictly 3NF — the denormalized flat view is NOT here
    assert not (cur / "mapped_eligibility.tsv").exists()

    # the per-row flat mapped view lives in the top-level joined/eligibility/ subfolder (sibling of the store root)
    mapped = _read(store_root.parent / "joined" / "eligibility" / "mapped_eligibility.tsv")
    assert len(mapped) == 1
    assert mapped[0]["oncotree_code"] == "NSCLC"
    assert mapped[0]["gene_alteration_findingmodel"].startswith("SmallVariant[gene=EGFR")
    assert mapped[0]["molecular_signature_findingmodel"] == "MicrosatelliteStability[PurpleMicrosatelliteStatus=MSI]"
    assert mapped[0]["prior_therapy_interpreted"] == ""   # free-text passthrough

    # content tables byte-for-byte untouched
    assert (cur / "arm_eligibility_raw.tsv").read_bytes() == raw_bytes
    assert (cur / "interpreted_eligibility.tsv").read_bytes() == interp_bytes
