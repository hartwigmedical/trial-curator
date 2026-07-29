"""Empty-output scope verdicts — the mechanism that tells "legitimately out of scope" from "extraction missed it"."""
from __future__ import annotations

from aus_trial_universe.tasks.eligibility import scope as SC
from aus_trial_universe.tasks.eligibility.schema import (
    SCOPE_HEALTHY_VOLUNTEERS,
    SCOPE_NO_ELIGIBILITY_TEXT,
    SCOPE_NOT_CANCER_SELECTIVE,
    SCOPE_UNEXPLAINED,
    ArmEligibilityRaw,
    ArmScope,
)
from aus_trial_universe.tasks.eligibility.store import EligStore
from aus_trial_universe.tasks.shared.cohorts import trial_arm_id


def test_healthy_volunteer_matcher_is_tight():
    """It must catch the real phrasings without firing on incidental uses of "healthy" — a false positive would
    mask a genuine extraction miss."""
    for hit in ["Healthy adult male volunteers aged 18 to 65",
                "Medically healthy without clinically significant abnormalities",
                "1. Healthy as judged by medical examination",
                "Male or female participants who are overtly healthy",
                "healthy volunteers in the opinion of the investigator"]:
        assert SC.healthy_volunteer_text(hit), hit
    for miss in ["Patients with a healthy diet are eligible",
                 "adequate organ function and otherwise healthy tissue margins",
                 "ECOG 0-1 with advanced NSCLC", ""]:
        assert SC.healthy_volunteer_text(miss) is None, miss


def test_deterministic_verdict_precedence():
    d = SC.deterministic_verdict
    # CTGov's structured flag wins outright — no text needed.
    v = d(source_text="advanced NSCLC patients", ctgov_healthy_volunteers=True)
    assert v.scope_verdict == SCOPE_HEALTHY_VOLUNTEERS and v.source == "deterministic"
    # Then the text rule.
    v = d(source_text="Healthy adult males 18-45", ctgov_healthy_volunteers=None)
    assert v.scope_verdict == SCOPE_HEALTHY_VOLUNTEERS and "healthy" in v.scope_reason.lower()
    # No healthy-volunteer evidence => a judgement call, NOT a deterministic guess. In particular an EMPTY raw row
    # must NOT be self-excusing: "the extractor found nothing" is the problem, not an explanation of it.
    assert d(source_text="Parotid cancer | Neck cancer", ctgov_healthy_volunteers=None) is None
    assert d(source_text="prose with no eligibility at all", ctgov_healthy_volunteers=False) is None


def _store_with_empty_arm(trial="NCT1", arm="A", **raw_cells) -> tuple[EligStore, str]:
    taid = trial_arm_id(trial, arm)
    store = EligStore()
    store.raw = {trial: [ArmEligibilityRaw(trial_arm_id=taid, **raw_cells)]}
    store.interpreted = {}                       # no conjunctions -> an empty arm
    return store, taid


def test_classify_without_a_client_marks_unexplained_rather_than_guessing():
    """Tier-1-only mode must never invent an in-scope reason — an unexplainable empty has to fail loud."""
    store, taid = _store_with_empty_arm(cancer_type_raw="Parotid cancer")
    counts = SC.classify_empty_arms(None, store, {taid})
    assert counts == {SCOPE_UNEXPLAINED: 1}
    assert SC.unexplained_arms(store, {taid}) == [taid]


def test_classify_uses_deterministic_rule_and_skips_already_verdicted():
    store, taid = _store_with_empty_arm(**{"cancer_type_raw": "history of malignancy [EXCLUSION]"})
    counts = SC.classify_empty_arms(None, store, {taid},
                                    source_text_for={"NCT1": "Healthy adult male volunteers, 18 to 45"})
    assert counts == {SCOPE_HEALTHY_VOLUNTEERS: 1} and not SC.unexplained_arms(store, {taid})
    # A second pass is a no-op (idempotent / lookup-first).
    assert SC.classify_empty_arms(None, store, {taid}) == {}


def test_classify_consults_the_llm_only_for_the_residue(monkeypatch):
    """Deterministic arms cost nothing; only the judgement cases reach the model, and an off-enum answer is
    treated as unexplained (fail-loud) rather than accepted."""
    store = EligStore()
    det = trial_arm_id("NCT_HV", "A")            # explained by a rule
    judge = trial_arm_id("ACTRN1", "intervention")  # needs the LLM
    bogus = trial_arm_id("ACTRN2", "intervention")  # LLM returns nonsense
    store.raw = {"NCT_HV": [ArmEligibilityRaw(trial_arm_id=det, cancer_type_raw="x")],
                 "ACTRN1": [ArmEligibilityRaw(trial_arm_id=judge, cancer_type_raw="Parotid cancer")],
                 "ACTRN2": [ArmEligibilityRaw(trial_arm_id=bogus, cancer_type_raw="Neck cancer")]}
    asked: list[str] = []

    def fake_llm(agent, arm_id, text):
        asked.append(arm_id)
        if arm_id == bogus:
            return ArmScope(trial_arm_id=arm_id, scope_verdict="something_else", source="llm")
        return ArmScope(trial_arm_id=arm_id, scope_verdict=SCOPE_NOT_CANCER_SELECTIVE,
                        scope_reason="undergoing parotidectomy, benign or malignant", source="llm")

    monkeypatch.setattr(SC, "_llm_verdict", fake_llm)
    monkeypatch.setattr(SC, "build_scope_classifier", lambda client, **kw: object())
    counts = SC.classify_empty_arms(object(), store, {det, judge, bogus},
                                   source_text_for={"NCT_HV": "Healthy volunteers aged 18-45"}, workers=2)
    assert sorted(asked) == sorted([judge, bogus])            # the deterministic arm never reached the model
    assert counts[SCOPE_HEALTHY_VOLUNTEERS] == 1 and counts[SCOPE_NOT_CANCER_SELECTIVE] == 1
    assert store.scope[bogus].scope_verdict == SCOPE_UNEXPLAINED   # off-enum -> fail loud
    assert SC.unexplained_arms(store, {det, judge, bogus}) == [bogus]


def test_scope_round_trips_through_the_store(tmp_path):
    """arm_scope persists as its own additive table; save_scope must not write the content tables."""
    store, taid = _store_with_empty_arm()
    store.put_scope(ArmScope(trial_arm_id=taid, scope_verdict=SCOPE_HEALTHY_VOLUNTEERS,
                             scope_reason="healthy volunteers", source="deterministic"))
    vdir = store.save_scope(tmp_path / "current_version")
    assert (vdir / "arm_scope.tsv").exists()
    assert not (vdir / "arm_eligibility_raw.tsv").exists()      # content tables untouched
    assert not (vdir / "interpreted_eligibility.tsv").exists()
    rows = (vdir / "arm_scope.tsv").read_text().strip().splitlines()
    assert rows[0].split("\t") == ["trial_arm_id", "scope_verdict", "scope_reason", "source"]
    assert taid in rows[1] and SCOPE_HEALTHY_VOLUNTEERS in rows[1]
    # and it comes back on load (the store root holds current_version/)
    assert EligStore.load(tmp_path).scope[taid].scope_verdict == SCOPE_HEALTHY_VOLUNTEERS
