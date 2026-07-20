"""drug_ref persistence: incremental load + versioned save across all tables, incl. the 3NF split of table 1
into intervention_to_canonical (mapping) + trial_to_intervention (provenance) (spec §6.1)."""
from __future__ import annotations

from datetime import date

import pytest

from aus_trial_universe.agentic.core.paths import archive_current_version, current_version_dir
from aus_trial_universe.agentic.tasks.drug_ref.schema import DrugRegulatoryApproval, DrugAnnotationsCore, DrugTargetAction
from aus_trial_universe.agentic.tasks.drug_ref.store import DrugRefStore


def test_current_version_and_archive_helpers(tmp_path):
    """The relocatable-root helpers: current_version_dir resolves the live dir (raises if absent);
    archive_current_version moves it to archive/<label>/ (the archive-on-refresh primitive)."""
    with pytest.raises(FileNotFoundError):
        current_version_dir(tmp_path)                                  # no current_version yet
    (tmp_path / "current_version").mkdir()
    (tmp_path / "current_version" / "x.txt").write_text("hi")
    assert current_version_dir(tmp_path) == tmp_path / "current_version"
    dest = archive_current_version(tmp_path, "20260101")
    assert dest == tmp_path / "archive" / "20260101" and (dest / "x.txt").read_text() == "hi"
    assert not (tmp_path / "current_version").exists()
    assert archive_current_version(tmp_path, "again") is None          # nothing left to archive


def test_empty_load_when_no_versions(tmp_path):
    store = DrugRefStore.load(tmp_path)
    assert store.refs == {} and store.mappings == {} and store.occurrences == {}
    assert store.canonical_ids_for("x") == []


def test_save_then_load_round_trips_all_tables(tmp_path):
    s = DrugRefStore()
    s.set_mapping("Keytruda", [("Keytruda", "rxcui:1547545")])
    s.set_mapping("pembrolizumab", [("", "rxcui:1547545")])   # two raw spellings -> one canonical
    s.add_occurrence("NCT01", "ctgov", "Arm A", "EXPERIMENTAL", "Keytruda")   # provenance: trial+arm uses a name
    s.add_occurrence("NCT02", "ctgov", "Arm A", "EXPERIMENTAL", "Keytruda")
    s.add_occurrence("ACTRN99", "anzctr", "intervention", "EXPERIMENTAL", "pembrolizumab")
    s.put_ref(DrugAnnotationsCore(canonical_id="rxcui:1547545", canonical_name="pembrolizumab", rxcui="1547545",
                      aliases="Keytruda | MK-3475", modality="monoclonal antibody",
                      drug_class="checkpoint inhibitor", pottr_drug_class="cancer_therapy -> anti-PD-1_monoclonal_antibody",
                      atc_code="L01FF02", researched_on="2026-07-13"))
    s.put_targets("rxcui:1547545", [DrugTargetAction(canonical_id="rxcui:1547545", target="PD-1", action="antagonist")])
    s.put_indications("rxcui:1547545", [
        DrugRegulatoryApproval(canonical_id="rxcui:1547545", indication_id="1", cancer_type="melanoma",
                       stage="unresectable Stage III/IV", combination="monotherapy",
                       tga_status="approved", pbs_status="approved"),
        DrugRegulatoryApproval(canonical_id="rxcui:1547545", indication_id="2", cancer_type="NSCLC",
                       biomarker="PD-L1 >=50%", line_of_therapy="1L", combination="in combination with chemotherapy",
                       tga_status="approved", pbs_status="not_approved"),   # cancer+biomarker; TGA-yes/PBS-no
    ])
    vdir = s.save(tmp_path, on=date(2026, 7, 13))
    assert vdir.name == "current_version"                         # fixed live-version folder (date in .version)
    assert (vdir / ".version").read_text().strip() == "2026-07-13"
    assert (vdir / "intervention_to_canonical.tsv").exists() and (vdir / "trial_to_intervention.tsv").exists()

    loaded = DrugRefStore.load(tmp_path)
    assert loaded.canonical_ids_for("Keytruda") == loaded.canonical_ids_for("pembrolizumab") == ["rxcui:1547545"]
    # raw_name_to_map defaults to the whole input for a single-drug mapping
    assert loaded.mappings["pembrolizumab"][0].raw_name_to_map == "pembrolizumab"
    # provenance round-trips (3 distinct trial+arm links, incl. arm + arm_type)
    assert {(o.trialId, o.registry, o.arm, o.arm_type) for o in loaded.occurrences.values()} == {
        ("NCT01", "ctgov", "Arm A", "EXPERIMENTAL"), ("NCT02", "ctgov", "Arm A", "EXPERIMENTAL"),
        ("ACTRN99", "anzctr", "intervention", "EXPERIMENTAL")}
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
    s.put_ref(DrugAnnotationsCore(canonical_id="c1", researched_on="2026-01-01"))
    assert s.is_stale("c1", 30, today=date(2026, 7, 13))            # old -> stale
    assert not s.is_stale("c1", 3650, today=date(2026, 7, 13))      # within window
    assert s.is_stale("absent", 30, today=date(2026, 7, 13))        # missing -> stale


def test_incremental_add_keeps_existing(tmp_path):
    s = DrugRefStore()
    s.set_mapping("Keytruda", [("", "c1")])
    s.put_ref(DrugAnnotationsCore(canonical_id="c1", canonical_name="pembrolizumab", researched_on="2026-07-13"))
    s.save(tmp_path, on=date(2026, 7, 10))

    s2 = DrugRefStore.load(tmp_path)
    assert s2.has_ref("c1")
    s2.set_mapping("Opdivo", [("", "c2")])
    s2.put_ref(DrugAnnotationsCore(canonical_id="c2", canonical_name="nivolumab", researched_on="2026-07-13"))
    s2.save(tmp_path, on=date(2026, 7, 13))

    latest = DrugRefStore.load(tmp_path)
    assert latest.has_ref("c1") and latest.has_ref("c2")


def test_mapping_one_input_maps_to_many_canonicals(tmp_path):
    """A combination input maps to N canonicals (1 input -> N), keeping each component's raw fragment; a non-drug
    input is recorded with no canonical."""
    s = DrugRefStore()
    s.set_mapping("Nivo + Ipi", [("Nivo", "name:nivolumab"), ("Ipi", "name:ipilimumab")])   # combination -> two atoms
    s.set_mapping("Radiotherapy", [])                                                        # non-drug
    s.save(tmp_path, on=date(2026, 7, 13))

    loaded = DrugRefStore.load(tmp_path)
    assert loaded.canonical_ids_for("Nivo + Ipi") == ["name:nivolumab", "name:ipilimumab"]
    frags = {m.canonical_id: m.raw_name_to_map for m in loaded.mappings["Nivo + Ipi"]}
    assert frags == {"name:nivolumab": "Nivo", "name:ipilimumab": "Ipi"}                     # fragments preserved
    assert loaded.has_mapping("Radiotherapy") and loaded.canonical_ids_for("Radiotherapy") == []


def test_legacy_drug_alias_still_loads(tmp_path):
    """Backward compat: a current_version/ holding a pre-split legacy drug_alias.tsv still loads into mappings."""
    vdir = tmp_path / "current_version"
    vdir.mkdir()
    (vdir / "drug_alias.tsv").write_text(
        "raw_name\tcanonical_id\nKeytruda\trxcui:1547545\nNivo + Ipi\tname:nivolumab\nNivo + Ipi\tname:ipilimumab\n")
    loaded = DrugRefStore.load(tmp_path)
    assert loaded.canonical_ids_for("Keytruda") == ["rxcui:1547545"]
    assert loaded.canonical_ids_for("Nivo + Ipi") == ["name:nivolumab", "name:ipilimumab"]
    assert loaded.mappings["Keytruda"][0].raw_name_to_map == "Keytruda"   # legacy: fragment = the whole input
