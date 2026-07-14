"""drug_ref persistence: incremental load + versioned save across all four tables (spec §6.1)."""
from __future__ import annotations

from datetime import date

from aus_trial_universe.agentic.tasks.drug_ref.schema import DrugIndication, DrugRef, DrugTarget
from aus_trial_universe.agentic.tasks.drug_ref.store import DrugRefStore


def test_empty_load_when_no_versions(tmp_path):
    store = DrugRefStore.load(tmp_path)
    assert store.refs == {} and store.aliases == {} and store.canonical_for("x") is None


def test_save_then_load_round_trips_all_tables(tmp_path):
    s = DrugRefStore()
    s.put_alias("Keytruda", "rxcui:1547545")
    s.put_alias("pembrolizumab", "rxcui:1547545")   # two raw spellings -> one canonical
    s.put_ref(DrugRef(canonical_id="rxcui:1547545", canonical_name="pembrolizumab", rxcui="1547545",
                      aliases="Keytruda | MK-3475", modality="monoclonal antibody",
                      drug_class="checkpoint inhibitor", pottr_drug_class="cancer_therapy -> anti-PD-1_monoclonal_antibody",
                      atc_code="L01FF02", researched_on="2026-07-13"))
    s.put_targets("rxcui:1547545", [DrugTarget(canonical_id="rxcui:1547545", target="PD-1", action="antagonist")])
    s.put_indications("rxcui:1547545", [
        DrugIndication(canonical_id="rxcui:1547545", indication_id="1", cancer_type="melanoma",
                       stage="unresectable Stage III/IV", combination="monotherapy",
                       tga_status="approved", pbs_status="approved"),
        DrugIndication(canonical_id="rxcui:1547545", indication_id="2", cancer_type="NSCLC",
                       biomarker="PD-L1 >=50%", line_of_therapy="1L", combination="in combination with chemotherapy",
                       tga_status="approved", pbs_status="not_approved"),   # cancer+biomarker; TGA-yes/PBS-no
    ])
    vdir = s.save(tmp_path, on=date(2026, 7, 13))
    assert vdir.name == "version_13072026"

    loaded = DrugRefStore.load(tmp_path)
    assert loaded.canonical_for("Keytruda") == loaded.canonical_for("pembrolizumab") == "rxcui:1547545"
    r = loaded.ref("rxcui:1547545")
    assert r.modality == "monoclonal antibody" and r.atc_code == "L01FF02"
    assert loaded.targets_for("rxcui:1547545")[0].target == "PD-1"
    inds = loaded.indications_for("rxcui:1547545")
    assert {i.cancer_type for i in inds} == {"melanoma", "NSCLC"}
    nsclc = next(i for i in inds if i.cancer_type == "NSCLC")
    assert nsclc.biomarker == "PD-L1 >=50%" and nsclc.combination == "in combination with chemotherapy"
    assert (nsclc.tga_status, nsclc.pbs_status) == ("approved", "not_approved")


def test_is_stale(tmp_path):
    s = DrugRefStore()
    s.put_ref(DrugRef(canonical_id="c1", researched_on="2026-01-01"))
    assert s.is_stale("c1", 30, today=date(2026, 7, 13))            # old -> stale
    assert not s.is_stale("c1", 3650, today=date(2026, 7, 13))      # within window
    assert s.is_stale("absent", 30, today=date(2026, 7, 13))        # missing -> stale


def test_incremental_add_keeps_existing(tmp_path):
    s = DrugRefStore()
    s.put_alias("Keytruda", "c1")
    s.put_ref(DrugRef(canonical_id="c1", canonical_name="pembrolizumab", researched_on="2026-07-13"))
    s.save(tmp_path, on=date(2026, 7, 10))

    s2 = DrugRefStore.load(tmp_path)
    assert s2.has_ref("c1")
    s2.put_alias("Opdivo", "c2")
    s2.put_ref(DrugRef(canonical_id="c2", canonical_name="nivolumab", researched_on="2026-07-13"))
    s2.save(tmp_path, on=date(2026, 7, 13))

    latest = DrugRefStore.load(tmp_path)
    assert latest.has_ref("c1") and latest.has_ref("c2")
