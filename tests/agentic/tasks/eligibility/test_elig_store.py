"""EligStore persistence: accumulating snapshot round-trip, per-trial upsert (re-run replaces rows),
lookup-first value->vocab map cache, and latest-snapshot selection (spec §6.1)."""
from __future__ import annotations

from aus_trial_universe.agentic.core.paths import latest_snapshot_dir
from aus_trial_universe.agentic.tasks.eligibility.schema import (
    ArmEligibilityRaw,
    CancerTypeMap,
    GeneAlterationMap,
    InterpretedEligibility,
    MolecularSignatureMap,
    TrialArm,
)
from aus_trial_universe.agentic.tasks.eligibility.store import EligStore


def _arms(trial, *labels):
    return [TrialArm(trialId=trial, arm=l, arm_type="EXPERIMENTAL") for l in labels]


def _raw(trial, arm, cancer):
    return ArmEligibilityRaw(trialId=trial, arm=arm, cancer_type_raw=cancer)


def test_empty_load_when_no_snapshot(tmp_path):
    store = EligStore.load(tmp_path)
    assert store.arms == {} and store.raw == {} and store.interpreted == {} and store.cancer_map == {}
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
        arms=[TrialArm(trialId="NCT01", arm="Arm A", arm_type="EXPERIMENTAL"),
              TrialArm(trialId="NCT01", arm="Arm B", arm_type="ACTIVE_COMPARATOR")],
        raw=[_raw("NCT01", "Arm A", "metastatic NSCLC [ELIGIBILITY CRITERIA]"),
             _raw("NCT01", "Arm B", "metastatic NSCLC [ELIGIBILITY CRITERIA]")],
        interpreted=[
            InterpretedEligibility(trialId="NCT01", arm="Arm A", conjunction_index=1,
                                   cancer_type_interpreted="metastatic NSCLC",
                                   gene_alteration_interpreted="EGFR exon 19 del"),
            InterpretedEligibility(trialId="NCT01", arm="Arm A", conjunction_index=2,
                                   cancer_type_interpreted="metastatic NSCLC"),
        ],
    )
    s.put_cancer_type(CancerTypeMap(cancer_type="metastatic NSCLC", oncotree_name="Lung Adenocarcinoma", oncotree_code="LUAD"))
    s.put_gene_alteration(GeneAlterationMap(gene_alteration="EGFR exon 19 del", finding_model="SmallVariant[gene=EGFR]"))
    s.put_molecular_signature(MolecularSignatureMap(molecular_signature="MSI-H", finding_model="Signature[type=MSI]"))

    run_dir = tmp_path / "20260720_120000"
    s.save(run_dir)
    assert (run_dir / "trial_arms.tsv").exists() and (run_dir / "interpreted_eligibility.tsv").exists()
    assert (run_dir / "arm_eligibility_raw.tsv").exists() and (run_dir / "cancer_type_map.tsv").exists()

    loaded = EligStore.load(tmp_path)
    assert {a.arm for a in loaded.arms["NCT01"]} == {"Arm A", "Arm B"}
    assert {r.arm for r in loaded.raw["NCT01"]} == {"Arm A", "Arm B"}
    rows = loaded.interpreted["NCT01"]
    assert len(rows) == 2 and rows[0].conjunction_index == 1 and isinstance(rows[0].conjunction_index, int)
    # lookup-first cache round-trips
    assert loaded.lookup_cancer_type("metastatic NSCLC").oncotree_code == "LUAD"
    assert loaded.lookup_gene_alteration("EGFR exon 19 del").finding_model == "SmallVariant[gene=EGFR]"
    assert loaded.lookup_molecular_signature("MSI-H").finding_model == "Signature[type=MSI]"


def test_set_trial_replaces_rows_on_rerun(tmp_path):
    s = EligStore()
    s.set_trial("NCT01", _arms("NCT01", "all"), [_raw("NCT01", "all", "melanoma [CONDITIONS]")],
                [InterpretedEligibility(trialId="NCT01", arm="all", conjunction_index=1, cancer_type_interpreted="melanoma")])
    s.set_trial("NCT02", _arms("NCT02", "all"), [_raw("NCT02", "all", "NSCLC [CONDITIONS]")],
                [InterpretedEligibility(trialId="NCT02", arm="all", conjunction_index=1, cancer_type_interpreted="NSCLC")])
    s.save(tmp_path / "20260720_100000")

    # re-run NCT01 with a fixed value -> its old rows are replaced, NCT02 untouched
    s2 = EligStore.load(tmp_path)
    s2.set_trial("NCT01", _arms("NCT01", "all"), [_raw("NCT01", "all", "melanoma [CONDITIONS]")],
                 [InterpretedEligibility(trialId="NCT01", arm="all", conjunction_index=1, cancer_type_interpreted="melanoma (fixed)")])
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
