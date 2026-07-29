"""drug_ref persistence: incremental load + versioned save across all tables, incl. the 3NF split of table 1
into intervention_to_canonical (mapping) + trial_to_intervention (provenance) (spec §6.1)."""
from __future__ import annotations

import os
from datetime import date

import pytest

from aus_trial_universe.core.paths import archive_current_version, current_version_dir
from aus_trial_universe.tasks.drug_utility.schema import (
    DrugRegulatoryApproval, DrugAnnotationsCore, DrugTargetAction, TrialArmDrugRole)
from aus_trial_universe.tasks.drug_utility.store import DrugRefStore
from aus_trial_universe.tasks.shared.cohorts import trial_arm_id


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


def test_archive_label_collision_gets_a_suffix(tmp_path):
    """Two runs on the SAME day reuse the ddmmyyyy label; rename onto a non-empty dir fails (OSError 66), so a
    taken label must fall through to <label>_2, _3, … rather than aborting the run at ingest."""
    for expected in ("29072026", "29072026_2", "29072026_3"):
        (tmp_path / "current_version").mkdir()
        (tmp_path / "current_version" / "x.txt").write_text(expected)
        assert archive_current_version(tmp_path, "29072026") == tmp_path / "archive" / expected
    assert sorted(p.name for p in (tmp_path / "archive").iterdir()) == [
        "29072026", "29072026_2", "29072026_3"]
    assert (tmp_path / "archive" / "29072026" / "x.txt").read_text() == "29072026"   # earlier ones intact


def test_prune_archive_keeps_the_newest_n(tmp_path):
    """Each refresh archives ~230 MB of raw registry input; unattended, nobody notices the creep. Retention is by
    mtime, not name, so the same-day `<label>_2` suffixes cannot mis-order it."""
    from aus_trial_universe.core.paths import prune_archive
    adir = tmp_path / "archive"
    for i, name in enumerate(["29072026", "29072026_2", "30072026", "31072026", "01082026", "02082026"]):
        d = adir / name
        d.mkdir(parents=True)
        (d / "payload.txt").write_text(name)
        os.utime(d, (1_800_000_000 + i * 60, 1_800_000_000 + i * 60))   # ascending mtime == archive order
    removed = prune_archive(tmp_path, keep=3)
    assert sorted(p.name for p in removed) == ["29072026", "29072026_2", "30072026"]
    assert sorted(d.name for d in adir.iterdir()) == ["01082026", "02082026", "31072026"]
    assert prune_archive(tmp_path, keep=3) == []            # idempotent once at the limit
    assert prune_archive(tmp_path / "nope", keep=3) == []   # no archive dir -> no-op, never raises


def test_empty_load_when_no_versions(tmp_path):
    store = DrugRefStore.load(tmp_path)
    assert store.refs == {} and store.mappings == {} and store.occurrences == {}
    assert store.canonical_ids_for("x") == []


def test_save_then_load_round_trips_all_tables(tmp_path):
    s = DrugRefStore()
    s.set_mapping("Keytruda", [("Keytruda", "rxcui:1547545")])
    s.set_mapping("pembrolizumab", [("", "rxcui:1547545")])   # two raw spellings -> one canonical
    s.add_occurrence(trial_arm_id("NCT01", "Arm A"), "Keytruda")   # provenance: a trial arm uses an input name
    s.add_occurrence(trial_arm_id("NCT02", "Arm A"), "Keytruda")
    s.add_occurrence(trial_arm_id("ACTRN99", "intervention"), "pembrolizumab")
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
    # provenance round-trips (3 distinct trial_arm links, keyed by trial_arm_id -> shared trial_arms registry)
    assert {(o.trial_arm_id, o.input_intervention_name) for o in loaded.occurrences.values()} == {
        (trial_arm_id("NCT01", "Arm A"), "Keytruda"), (trial_arm_id("NCT02", "Arm A"), "Keytruda"),
        (trial_arm_id("ACTRN99", "intervention"), "pembrolizumab")}
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


def test_trial_arm_drug_role_round_trips_and_remove_by_trial(tmp_path):
    """Phase-2 table 6: (trial_arm_id, canonical_id) -> role round-trips through save/load; remove_trial_roles
    drops every arm of a trial (overwrite semantics); put_roles([]) removes an arm's roles."""
    a1 = trial_arm_id("NCT01", "Experimental")
    a2 = trial_arm_id("NCT01", "Control")
    b1 = trial_arm_id("NCT02", "Arm A")
    s = DrugRefStore()
    s.put_roles(a1, [TrialArmDrugRole(trial_arm_id=a1, canonical_id="name:gedatolisib", role="main"),
                     TrialArmDrugRole(trial_arm_id=a1, canonical_id="rxcui:111", role="auxiliary")])
    s.put_roles(a2, [TrialArmDrugRole(trial_arm_id=a2, canonical_id="rxcui:111", role="auxiliary")])
    s.put_roles(b1, [TrialArmDrugRole(trial_arm_id=b1, canonical_id="name:druga", role="main")])
    vdir = s.save(tmp_path, on=date(2026, 7, 28))
    assert (vdir / "trial_arm_drug_role.tsv").exists()

    loaded = DrugRefStore.load(tmp_path)
    assert {(r.canonical_id, r.role) for r in loaded.roles_for(a1)} == {
        ("name:gedatolisib", "main"), ("rxcui:111", "auxiliary")}
    assert loaded.role_trial_arm_ids() == {a1, a2, b1}

    # overwrite: drop all arms of NCT01 (both a1 + a2), leaving NCT02 untouched
    removed = loaded.remove_trial_roles("NCT01")
    assert removed == 3 and loaded.role_trial_arm_ids() == {b1}
    # put_roles([]) clears an arm
    loaded.put_roles(b1, [])
    assert loaded.roles_for(b1) == [] and loaded.role_trial_arm_ids() == set()


def test_empty_role_table_is_written_and_reloads_empty(tmp_path):
    """A store with no roles still writes the table (header only) and loads back with no role rows — proves the
    additive table is always present without perturbing the other tables."""
    s = DrugRefStore()
    s.set_mapping("Keytruda", [("", "rxcui:1")])
    vdir = s.save(tmp_path, on=date(2026, 7, 28))
    assert (vdir / "trial_arm_drug_role.tsv").exists()
    assert DrugRefStore.load(tmp_path).roles == {}


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
