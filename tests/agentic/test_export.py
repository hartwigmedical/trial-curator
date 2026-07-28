"""Matching-engine export — the join builder (Set A), oncotree-name rendering, and the snapshot bundle."""
from __future__ import annotations

import csv

from aus_trial_universe.agentic import export as EX
from aus_trial_universe.agentic import trial_info as TI
from aus_trial_universe.agentic.tasks.drug_utility.schema import DrugAnnotationsCore, TrialArmDrugRole
from aus_trial_universe.agentic.tasks.drug_utility.store import DrugRefStore
from aus_trial_universe.agentic.tasks.eligibility.schema import InterpretedEligibility
from aus_trial_universe.agentic.tasks.eligibility.store import EligStore
from aus_trial_universe.agentic.tasks.shared.cohorts import trial_arm_id
from aus_trial_universe.agentic.tasks.shared.schema import TrialArm
from aus_trial_universe.agentic.tasks.shared.store import TrialArmStore

_VOCAB = {"NSCLC": "Non-Small Cell Lung Cancer", "LUAD": "Lung Adenocarcinoma", "SCLC": "Small Cell Lung Cancer"}


def test_render_oncotree_name():
    r = EX._render_oncotree_name
    assert r("NSCLC", _VOCAB) == "Non-Small Cell Lung Cancer"                 # single-code fast path
    assert r("LUAD OR SCLC", _VOCAB) == "Lung Adenocarcinoma OR Small Cell Lung Cancer"
    assert r("NSCLC AND NOT(LUAD)", _VOCAB) == "Non-Small Cell Lung Cancer AND NOT(Lung Adenocarcinoma)"
    assert r("Pan-cancer", _VOCAB) == "Pan-cancer"                            # sentinel / unknown token passes through
    assert r("", _VOCAB) == ""


def _write_finalised_maps(elig_dir):
    elig_dir.mkdir(parents=True, exist_ok=True)
    with open(elig_dir / "finalised_cancer_type_map.tsv", "w", newline="") as f:
        w = csv.writer(f, delimiter="\t"); w.writerow(["cancer_type", "oncotree_name", "oncotree_code", "oncotree_code_FINAL"])
        w.writerow(["advanced NSCLC", "Non-Small Cell Lung Cancer", "NSCLC", "NSCLC"])
    with open(elig_dir / "finalised_gene_alteration_map.tsv", "w", newline="") as f:
        w = csv.writer(f, delimiter="\t"); w.writerow(["gene_alteration", "finding_model", "finding_model_FINAL"])
        w.writerow(["EGFR L858R", "SmallVariant[gene=EGFR]", "SmallVariant[gene=EGFR & proteinChange=p.L858R]"])
    with open(elig_dir / "finalised_molecular_signature_map.tsv", "w", newline="") as f:
        w = csv.writer(f, delimiter="\t"); w.writerow(["molecular_signature", "finding_model", "finding_model_FINAL"])


def _stores():
    taid = trial_arm_id("NCT1", "Arm A")
    elig = EligStore()
    elig.interpreted = {"NCT1": [InterpretedEligibility(
        trial_arm_id=taid, conjunction_index=1, cancer_type_interpreted="advanced NSCLC",
        gene_alteration_interpreted="EGFR L858R", molecular_signature_interpreted="",
        molecular_biomarker_interpreted="PD-L1>=50%", prior_therapy_interpreted="NOT(prior EGFR TKI)")]}
    arm = TrialArmStore()
    arm.arms = {"NCT1": [TrialArm(trial_arm_id=taid, trialId="NCT1", registry="ctgov", arm="Arm A", arm_type="EXPERIMENTAL")]}
    drug = DrugRefStore()
    drug.set_mapping("Osimertinib", [("Osimertinib", "rxcui:1")])
    drug.set_mapping("Carboplatin", [("Carboplatin", "rxcui:2")])
    drug.add_occurrence(taid, "Osimertinib")
    drug.add_occurrence(taid, "Carboplatin")
    drug.put_ref(DrugAnnotationsCore(canonical_id="rxcui:1", canonical_name="osimertinib", drug_class="EGFR TKI",
                                     pottr_drug_class="cancer_therapy -> EGFR_inhibitor"))
    drug.put_ref(DrugAnnotationsCore(canonical_id="rxcui:2", canonical_name="carboplatin", drug_class="platinum"))
    drug.put_roles(taid, [TrialArmDrugRole(trial_arm_id=taid, canonical_id="rxcui:1", role="main"),
                          TrialArmDrugRole(trial_arm_id=taid, canonical_id="rxcui:2", role="auxiliary")])
    info = {"NCT1": TI.TrialInfo(trialId="NCT1", registry="ctgov", official_title="A Study", phase="PHASE3",
                                 overall_status="RECRUITING", min_age="18 Years", sex="ALL", has_AU_site="true",
                                 trial_url="https://clinicaltrials.gov/study/NCT1")}
    return elig, arm, drug, info, taid


def test_build_export_rows(tmp_path, monkeypatch):
    monkeypatch.setattr("aus_trial_universe.agentic.tasks.eligibility.tools.oncotree.oncotree_vocab", lambda: _VOCAB)
    _write_finalised_maps(tmp_path)
    elig, arm, drug, info, taid = _stores()
    rows = EX.build_export_rows(elig, arm, drug, info, elig_dir=tmp_path)

    assert len(rows) == 1
    r = rows[0]
    assert set(r.keys()) == set(EX.EXPORT_COLUMNS)                            # exact column set (file order via DictWriter)
    assert (r["trial_arm_id"], r["conjunction_index"], r["trialId"], r["arm_type"]) == (taid, 1, "NCT1", "EXPERIMENTAL")
    # trial info joined
    assert (r["official_title"], r["phase"], r["min_age"], r["sex"], r["has_AU_site"]) == \
        ("A Study", "PHASE3", "18 Years", "ALL", "true")
    # FINAL vocab
    assert (r["oncotree_code"], r["oncotree_name"]) == ("NSCLC", "Non-Small Cell Lung Cancer")
    assert r["gene_alteration_findingmodel"] == "SmallVariant[gene=EGFR & proteinChange=p.L858R]"
    assert r["molecular_signature_findingmodel"] == ""                        # empty cell -> empty
    assert r["molecular_biomarker_interpreted"] == "PD-L1>=50%"               # free text passthrough
    # only the raw-intervention join key into Set B is carried (rollups are derivable via the 3NF drug tables)
    assert r["arm_intervention_names_raw"] == "Osimertinib; Carboplatin"
    for dropped in ("arm_canonical_ids", "arm_main_drugs", "arm_auxiliary_drugs",
                    "arm_main_drug_classes", "arm_main_pottr_classes"):
        assert dropped not in r


def test_run_export_writes_setA_and_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr("aus_trial_universe.agentic.tasks.eligibility.tools.oncotree.oncotree_vocab", lambda: _VOCAB)
    elig, arm, drug, info, _ = _stores()
    # hermetic: no loaders, no real stores
    monkeypatch.setattr(TI, "build_all_trial_info", lambda: info)
    monkeypatch.setattr(TI, "save_trial_info", lambda infos, **k: None)
    monkeypatch.setattr(EligStore, "load", classmethod(lambda cls, *a, **k: elig))
    monkeypatch.setattr(TrialArmStore, "load", classmethod(lambda cls, *a, **k: arm))
    monkeypatch.setattr(DrugRefStore, "load", classmethod(lambda cls, *a, **k: drug))

    elig_root = tmp_path / "eligibility"
    _write_finalised_maps(elig_root / "current_output")
    drug_root = tmp_path / "drug_annotations"
    (drug_root / "current_version").mkdir(parents=True)
    for name in EX.DRUG_TABLES:                                              # dummy drug tables for the snapshot copy
        (drug_root / "current_version" / name).write_text("header\n")
    export_root = tmp_path / "export"

    EX.run_export(snapshot=True, export_root=export_root, elig_root=elig_root, drug_root=drug_root, stamp="test")

    assert (export_root / "trial_eligibility.tsv").exists() and (export_root / "MANIFEST.md").exists()
    with open(export_root / "trial_eligibility.tsv") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        assert reader.fieldnames == EX.EXPORT_COLUMNS                        # written column order IS the contract
        rows = list(reader)
    assert len(rows) == 1 and rows[0]["oncotree_code"] == "NSCLC"
    # snapshot bundle: Set A + a frozen copy of every drug table
    snaps = list(export_root.glob("snapshot_*"))
    assert len(snaps) == 1
    snap = snaps[0]
    assert (snap / "trial_eligibility.tsv").exists() and (snap / "MANIFEST.md").exists()
    for name in EX.DRUG_TABLES:
        assert (snap / name).exists()
