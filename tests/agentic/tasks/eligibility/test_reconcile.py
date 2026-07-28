"""Step 2 (`run.main(--reconcile)`): reconcile the store's map tables into the finalised set + flat view in
joined/, leaving the 3NF store untouched. The LLM adjudicator is patched (no API); this pins the deterministic
pre-pass (name->code repair, OR-order normalise) + the ORCHESTRATION, not the adjudicator prompt."""
from __future__ import annotations

import csv

from aus_trial_universe.agentic import run
import aus_trial_universe.agentic.tasks.eligibility.mapping.reconcile as rec
from aus_trial_universe.agentic.tasks.eligibility.mapping.reconcile import normalize_or_order, repair_oncotree_code
from aus_trial_universe.agentic.tasks.eligibility.schema import (
    ArmEligibilityRaw, CancerTypeMap, GeneAlterationMap, InterpretedEligibility, MolecularSignatureMap,
)
from aus_trial_universe.agentic.tasks.eligibility.store import EligStore


def _read(path):
    return list(csv.DictReader(open(path), delimiter="\t"))


# --- deterministic pre-pass ------------------------------------------------- #
def test_name_to_code_repair_and_or_order():
    from aus_trial_universe.agentic.tasks.eligibility.tools.oncotree import oncotree_vocab, invalid_codes
    # a residual where a NAME leaked into the code field (the leaked name carries an all-caps token 'NOS' that
    # invalid_codes flags) is repaired to its CODE.
    name = oncotree_vocab()["DLBCLNOS"]          # 'Diffuse Large B-Cell Lymphoma, NOS'
    expr = f"AML AND NOT({name})"
    assert invalid_codes(expr)                   # the leaked name triggers repair
    repaired, changed, unresolved = repair_oncotree_code(expr)
    assert changed and unresolved == [] and repaired == "AML AND NOT(DLBCLNOS)"
    # already-valid code is untouched
    assert repair_oncotree_code("NSCLC") == ("NSCLC", False, [])
    # OR-order normalisation makes a flat OR canonical
    assert normalize_or_order("UTUC OR BLCA OR UCU") == normalize_or_order("BLCA OR UTUC OR UCU")
    assert normalize_or_order("Solid tumour AND NOT(MEL)") == "Solid tumour AND NOT(MEL)"   # AND/NOT untouched


# --- orchestration ---------------------------------------------------------- #
def _seed(store_root):
    # "breast cancer" and "metastatic breast cancer" share a canonical key (metastatic is stripped) but got
    # different Step-1 codes -> a genuine inconsistent group for Step 2 to reconcile.
    s = EligStore()
    s.set_trial("NCT1",
                [ArmEligibilityRaw(trial_arm_id="NCT1::A", cancer_type_raw="x")],
                [InterpretedEligibility(trial_arm_id="NCT1::A", conjunction_index=1,
                                        cancer_type_interpreted="metastatic breast cancer")])
    s.cancer_map = {"breast cancer": CancerTypeMap("breast cancer", "Breast", "BREAST"),
                    "metastatic breast cancer": CancerTypeMap("metastatic breast cancer",
                                                              "Breast Invasive Ductal Carcinoma", "IDC")}
    s.gene_map = {"KRAS mutation": GeneAlterationMap("KRAS mutation", "SmallVariant[gene=KRAS]")}
    s.signature_map = {"MSI-H": MolecularSignatureMap("MSI-H", "MicrosatelliteStability[PurpleMicrosatelliteStatus=MSI]")}
    s.save(store_root / "current_version")


def test_reconcile_writes_finalised_and_leaves_store_untouched(tmp_path, monkeypatch):
    store_root = tmp_path / "elig"
    _seed(store_root)
    cur = store_root / "current_version"
    maps_before = {f: (cur / f).read_bytes() for f in
                   ("interpreted_eligibility.tsv", "arm_eligibility_raw.tsv", "cancer_type_map.tsv")}

    # patch the adjudicator: unify each flagged oncotree group to BREAST
    def fake_adjudicate(client, members, bd, br, *, is_oncotree, max_attempts, use_reviewer):
        return {v: ("BREAST" if is_oncotree else c) for v, c in members}
    monkeypatch.setattr(rec, "adjudicate_group", fake_adjudicate)

    rc = run.main(["--reconcile", "--store-root", str(store_root), "--no-cache", "--no-cache-prune", "--workers", "2"])
    assert rc == 0

    joined = store_root.parent / "joined" / "eligibility"
    # the finalised map-table SET is 3NF -> it lives in the store (current_version/), NOT joined/
    by = {r["cancer_type"]: r for r in _read(cur / "finalised_cancer_type_map.tsv")}
    assert by["metastatic breast cancer"]["oncotree_code"] == "IDC"           # Step-1 preserved
    assert by["metastatic breast cancer"]["oncotree_code_FINAL"] == "BREAST"  # reconciled in the added col
    assert by["breast cancer"]["oncotree_code_FINAL"] == "BREAST"
    assert not (joined / "finalised_cancer_type_map.tsv").exists()            # NOT in joined/

    # only the DENORMALIZED flat view is in joined/
    flat = _read(joined / "finalised_mapped_eligibility.tsv")
    assert flat[0]["oncotree_code"] == "IDC" and flat[0]["oncotree_code_FINAL"] == "BREAST"

    # the Step-1 3NF tables are byte-for-byte untouched (finalised_* are NEW additions)
    for f, b in maps_before.items():
        assert (cur / f).read_bytes() == b
