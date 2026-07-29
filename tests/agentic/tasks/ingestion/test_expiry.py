"""Expiry decision logic + store pop/restore mechanics (no network, no LLM)."""
from __future__ import annotations

from aus_trial_universe.tasks.eligibility.schema import ArmEligibilityRaw, InterpretedEligibility
from aus_trial_universe.tasks.eligibility.store import EligStore
from aus_trial_universe.tasks.ingestion.expiry import decide_expiry
from aus_trial_universe.tasks.shared.schema import TrialArm
from aus_trial_universe.tasks.shared.store import TrialArmStore


# --------------------------------------------------------------------------- #
# decide_expiry — the pure decision
# --------------------------------------------------------------------------- #
def test_decide_expiry_drops_only_fallen_out_trials():
    expired, restored, guarded = decide_expiry(
        stored={"A", "B", "C"}, kept={"A"}, pottr=set(), previously_expired=set(), max_fraction=0.9)
    assert expired == ["B", "C"] and restored == [] and guarded is False


def test_decide_expiry_pottr_never_expires():
    # B and C fell out of kept, but C is POTTR-listed -> only B expires.
    expired, _restored, _guarded = decide_expiry(
        stored={"A", "B", "C"}, kept={"A"}, pottr={"C"}, previously_expired=set(), max_fraction=0.9)
    assert expired == ["B"]


def test_decide_expiry_restores_reappeared():
    _expired, restored, _guarded = decide_expiry(
        stored={"A"}, kept={"A", "X"}, pottr=set(), previously_expired={"X", "Y"}, max_fraction=0.9)
    assert restored == ["X"]              # X is back in kept; Y stays expired


def test_decide_expiry_guard_trips_on_too_many():
    # 3 of 4 would expire (>50%) -> guarded.
    _expired, _restored, guarded = decide_expiry(
        stored={"A", "B", "C", "D"}, kept={"A"}, pottr=set(), previously_expired=set(), max_fraction=0.5)
    assert guarded is True


# --------------------------------------------------------------------------- #
# store pop + expired-area round-trip mechanics
# --------------------------------------------------------------------------- #
def _elig_trial(trial_id: str) -> tuple[list, list]:
    taid = f"{trial_id}::armA"
    raw = [ArmEligibilityRaw(trial_arm_id=taid, cancer_type_raw="breast [T]")]
    interp = [InterpretedEligibility(trial_arm_id=taid, conjunction_index=0, cancer_type_interpreted="breast cancer")]
    return raw, interp


def test_eligstore_pop_then_expired_area_roundtrip(tmp_path):
    live = EligStore()
    for tid in ("NCT_A", "NCT_B"):
        live.set_trial(tid, *_elig_trial(tid))
    raw, interp = live.pop_trial("NCT_B")                 # expire NCT_B
    assert not live.has_trial("NCT_B") and live.has_trial("NCT_A")

    expired = EligStore()
    expired.set_trial("NCT_B", raw, interp)
    expired.save_content(tmp_path / "expired")
    reloaded = EligStore.load_dir(tmp_path / "expired")
    assert reloaded.has_trial("NCT_B")
    assert reloaded.interpreted["NCT_B"][0].cancer_type_interpreted == "breast cancer"

    # restore: pop from expired -> back to live
    r2, i2 = reloaded.pop_trial("NCT_B")
    live.set_trial("NCT_B", r2, i2)
    assert live.has_trial("NCT_B") and not reloaded.has_trial("NCT_B")


def test_trialarmstore_pop_and_dir_roundtrip(tmp_path):
    live = TrialArmStore()
    live.set_trial_arms("NCT_A", [TrialArm(trial_arm_id="NCT_A::armA", trialId="NCT_A", registry="ctgov",
                                           arm="armA", arm_type="EXPERIMENTAL")])
    live.set_trial_arms("NCT_B", [TrialArm(trial_arm_id="NCT_B::armA", trialId="NCT_B", registry="ctgov",
                                           arm="armA", arm_type="EXPERIMENTAL")])
    popped = live.pop_trial("NCT_B")
    assert not live.has_trial("NCT_B") and len(popped) == 1

    expired = TrialArmStore()
    expired.set_trial_arms("NCT_B", popped)
    expired.save_dir(tmp_path / "arms_expired")
    reloaded = TrialArmStore.load_dir(tmp_path / "arms_expired")
    assert reloaded.has_trial("NCT_B") and reloaded.arms_for("NCT_B")[0].arm_type == "EXPERIMENTAL"
