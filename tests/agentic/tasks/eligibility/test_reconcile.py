"""Step 2 (`run.main(--reconcile)`): reconcile the store's map tables into the finalised set + flat view in
joined/, leaving the 3NF store untouched. The LLM adjudicator is patched (no API); this pins the deterministic
pre-pass (name->code repair, OR-order normalise) + the ORCHESTRATION, not the adjudicator prompt."""
from __future__ import annotations

import csv

from aus_trial_universe import run
import aus_trial_universe.tasks.eligibility.mapping.reconcile as rec
from aus_trial_universe.tasks.eligibility.mapping.reconcile import deterministic_pass
from aus_trial_universe.tasks.eligibility.schema import (
    ArmEligibilityRaw, CancerTypeMap, GeneAlterationMap, InterpretedEligibility, MolecularSignatureMap,
)
from aus_trial_universe.tasks.eligibility.store import EligStore


def _read(path):
    return list(csv.DictReader(open(path), delimiter="\t"))


# --- deterministic pre-pass ------------------------------------------------- #
def test_deterministic_pass_repairs_names_and_canonicalises():
    """Replaces the old `repair_oncotree_code` / `normalize_or_order` pair. Those never fired in practice: the
    repair was gated on `invalid_codes()` (blind to mixed case) and the OR-sort bailed on anything containing
    AND / NOT( / (, i.e. on every expression that could actually diverge."""
    # a leaked NAME now resolves to its code
    assert deterministic_pass("Pancreatic Adenocarcinoma AND NOT(PANET)") == "PAAD"
    # OR branches are ordered, and this one no longer bails just because a NOT() is present
    assert deterministic_pass("MDS OR AML") == "AML OR MDS"
    assert deterministic_pass("DIFG AND NOT(DMG) AND NOT(HGGNOS)") == "DIFG AND NOT(DMG OR HGGNOS)"
    # idempotent
    once = deterministic_pass("Diffuse Glioma AND NOT(DMG) AND NOT(HGGNOS)")
    assert deterministic_pass(once) == once

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
                   ("interpreted_eligibility.tsv", "arm_eligibility_raw.tsv", "cancer_type_map_initial.tsv")}

    # THE GROUP ADJUDICATOR NO LONGER EXISTS. This began as a stub, became a tripwire, and is now a structural
    # assertion — the strongest of the three, because a runtime guard only fires if the path is exercised whereas
    # an absent symbol cannot be called at all. Cross-value LLM re-decision is the churn mechanism every column
    # was rewritten to remove: a value with no defect of its own could be rewritten because an unrelated value
    # entered the corpus. Re-introducing it would fail here immediately.
    assert not hasattr(rec, "reconcile_group"), "cross-value LLM adjudication has been re-introduced"
    assert not hasattr(rec, "repair_value"), "per-value LLM repair belongs to stage 1, not stage 2"

    rc = run.main(["--reconcile", "--store-root", str(store_root), "--no-cache", "--no-cache-prune", "--workers", "2"])
    assert rc == 0

    joined = store_root.parent / "joined" / "eligibility"
    # the finalised map-table SET is 3NF -> it lives in the store (current_version/), NOT joined/
    # the three stage tables are 3NF -> they live in the store (current_version/), NOT joined/
    by = {r["cancer_type"]: r for r in _read(cur / "cancer_type_map_finalised.tsv")}
    # columns accrete, so one row shows the whole chain. Stage 2 is deterministic: IDC is already canonical, so
    # `_reconciled` equals `_initial` and nothing overrides it — the LLM group-unification behaviour this test used
    # to assert no longer exists for cancer_type.
    assert by["metastatic breast cancer"]["oncotree_code_initial"] == "IDC"
    assert by["metastatic breast cancer"]["oncotree_code_reconciled"] == "IDC"
    assert by["metastatic breast cancer"]["oncotree_code_finalised"] == "IDC"
    assert by["metastatic breast cancer"]["oncotree_name_finalised"] == "Breast Invasive Ductal Carcinoma"
    for f in ("cancer_type_map_initial.tsv", "cancer_type_map_reconciled.tsv", "cancer_type_map_finalised.tsv"):
        assert (cur / f).exists() and not (joined / f).exists()

    # only the DENORMALIZED flat view is in joined/
    flat = _read(joined / "finalised_mapped_eligibility.tsv")
    # cancer_type stage 2 is deterministic, so IDC (already canonical) passes through unchanged. This used to
    # assert BREAST, i.e. the LLM group adjudicator unifying "metastatic breast cancer" with "breast cancer".
    assert flat[0]["oncotree_code"] == "IDC" and flat[0]["oncotree_code_FINAL"] == "IDC"

    # the Step-1 3NF tables are byte-for-byte untouched (finalised_* are NEW additions)
    for f, b in maps_before.items():
        assert (cur / f).read_bytes() == b
