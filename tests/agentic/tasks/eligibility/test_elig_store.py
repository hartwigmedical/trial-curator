"""EligStore persistence: accumulating snapshot round-trip, per-trial upsert (re-run replaces rows),
lookup-first value->vocab map cache, and latest-snapshot selection (spec §6.1).

The arm spine (`trial_arms`) is the SHARED registry (see test_trial_arm_store); the eligibility store holds only
its two content tables, keyed by `trial_arm_id` and grouped internally by trialId (derived from the slug)."""
from __future__ import annotations

from aus_trial_universe.core.paths import latest_snapshot_dir
from aus_trial_universe.tasks.eligibility.schema import (
    ArmEligibilityRaw,
    CancerTypeMap,
    GeneAlterationMap,
    InterpretedEligibility,
    MolecularSignatureMap,
)
from aus_trial_universe.tasks.eligibility.store import EligStore
from aus_trial_universe.tasks.shared.cohorts import trial_arm_id


def _raw(trial, arm, cancer):
    return ArmEligibilityRaw(trial_arm_id=trial_arm_id(trial, arm), cancer_type_raw=cancer)


def _interp(trial, arm, idx, cancer, gene=""):
    return InterpretedEligibility(trial_arm_id=trial_arm_id(trial, arm), conjunction_index=idx,
                                  cancer_type_interpreted=cancer, gene_alteration_interpreted=gene)


def test_empty_load_when_no_snapshot(tmp_path):
    store = EligStore.load(tmp_path)
    assert store.raw == {} and store.interpreted == {} and store.cancer_map == {}
    assert store.lookup_cancer_type("melanoma") is None


def test_latest_snapshot_dir_picks_newest(tmp_path):
    (tmp_path / "20260101_000000").mkdir()
    (tmp_path / "20260720_153000").mkdir()
    (tmp_path / "notes.txt").write_text("x")            # non-timestamp entries ignored
    assert latest_snapshot_dir(tmp_path).name == "20260720_153000"
    assert latest_snapshot_dir(tmp_path / "does-not-exist") is None


def test_save_then_load_round_trips_all_tables(tmp_path):
    s = EligStore()
    s.set_trial(
        "NCT01",
        raw=[_raw("NCT01", "Arm A", "metastatic NSCLC [ELIGIBILITY CRITERIA]"),
             _raw("NCT01", "Arm B", "metastatic NSCLC [ELIGIBILITY CRITERIA]")],
        interpreted=[
            _interp("NCT01", "Arm A", 1, "metastatic NSCLC", "EGFR exon 19 del"),
            _interp("NCT01", "Arm A", 2, "metastatic NSCLC"),
        ],
    )
    s.put_cancer_type(CancerTypeMap(cancer_type="metastatic NSCLC", oncotree_name="Lung Adenocarcinoma", oncotree_code="LUAD"))
    s.put_gene_alteration(GeneAlterationMap(gene_alteration="EGFR exon 19 del", finding_model="SmallVariant[gene=EGFR]"))
    s.put_molecular_signature(MolecularSignatureMap(molecular_signature="MSI-H", finding_model="Signature[type=MSI]"))

    run_dir = tmp_path / "20260720_120000"
    s.save(run_dir)
    # the eligibility store holds ONLY its two content tables + the map tables (no trial_arms — that's shared)
    assert not (run_dir / "trial_arms.tsv").exists()
    assert (run_dir / "arm_eligibility_raw.tsv").exists() and (run_dir / "interpreted_eligibility.tsv").exists()
    # stage 1 of OncoTree mapping (2026-08-06): the three-stage table set, stage-suffixed columns
    assert (run_dir / "cancer_type_map_initial.tsv").exists()

    loaded = EligStore.load(tmp_path)
    assert {r.trial_arm_id for r in loaded.raw["NCT01"]} == {
        trial_arm_id("NCT01", "Arm A"), trial_arm_id("NCT01", "Arm B")}
    rows = loaded.interpreted["NCT01"]
    assert len(rows) == 2 and rows[0].conjunction_index == 1 and isinstance(rows[0].conjunction_index, int)
    assert rows[0].trial_arm_id == trial_arm_id("NCT01", "Arm A")
    # lookup-first cache round-trips
    assert loaded.lookup_cancer_type("metastatic NSCLC").oncotree_code == "LUAD"
    assert loaded.lookup_gene_alteration("EGFR exon 19 del").finding_model == "SmallVariant[gene=EGFR]"
    assert loaded.lookup_molecular_signature("MSI-H").finding_model == "Signature[type=MSI]"


def test_set_trial_replaces_rows_on_rerun(tmp_path):
    s = EligStore()
    s.set_trial("NCT01", [_raw("NCT01", "all", "melanoma [CONDITIONS]")],
                [_interp("NCT01", "all", 1, "melanoma")])
    s.set_trial("NCT02", [_raw("NCT02", "all", "NSCLC [CONDITIONS]")],
                [_interp("NCT02", "all", 1, "NSCLC")])
    s.save(tmp_path / "20260720_100000")

    # re-run NCT01 with a fixed value -> its old rows are replaced, NCT02 untouched
    s2 = EligStore.load(tmp_path)
    s2.set_trial("NCT01", [_raw("NCT01", "all", "melanoma [CONDITIONS]")],
                 [_interp("NCT01", "all", 1, "melanoma (fixed)")])
    s2.save(tmp_path / "20260720_110000")

    latest = EligStore.load(tmp_path)
    assert len(latest.interpreted["NCT01"]) == 1
    assert latest.interpreted["NCT01"][0].cancer_type_interpreted == "melanoma (fixed)"
    assert latest.interpreted["NCT02"][0].cancer_type_interpreted == "NSCLC"    # other trials preserved


def test_map_lookup_first_dedups_by_value(tmp_path):
    s = EligStore()
    s.put_cancer_type(CancerTypeMap(cancer_type="melanoma", oncotree_name="Melanoma", oncotree_code="MEL"))
    s.put_cancer_type(CancerTypeMap(cancer_type="melanoma", oncotree_name="Melanoma", oncotree_code="SKCM"))  # re-put wins
    assert s.lookup_cancer_type("melanoma").oncotree_code == "SKCM"
    assert len(s.cancer_map) == 1                                   # one row per distinct value
