"""COMPONENT 2 — the disease-derived alteration inference (no API; the agents are patched).

What is pinned here is the MACHINERY, not the biology: the deterministic gate, the loop's use of the shared
harness, the lookup round-trip, and the fact that an empty answer is a first-class result rather than a failure.
The definitional bar itself lives in the prompt and is verified by review of the live output.
"""
from __future__ import annotations

import aus_trial_universe.analysis.pottr.disease_inference as di
from aus_trial_universe.analysis.pottr.schema import DiseaseDerivedAlteration, DiseaseDerivedRow
from aus_trial_universe.tasks.eligibility.mapping.schema import ReviewVerdict


class _Doer:
    def __init__(self, *answers):
        self._answers = list(answers)
        self.inputs: list[str] = []

    def __call__(self, prompt):
        self.inputs.append(prompt)
        return self._answers.pop(0) if len(self._answers) > 1 else self._answers[0]


def _patch(monkeypatch, doer, verdicts):
    monkeypatch.setattr(di, "build_disease_inference_agent", lambda c, **k: doer)
    it = iter(verdicts)
    monkeypatch.setattr(di, "build_disease_inference_reviewer",
                        lambda c, **k: lambda _p: next(it, ReviewVerdict(faithful=True)))


# --- the deterministic gate -------------------------------------------------- #
def test_empty_answer_is_never_a_problem():
    """`""` is the CORRECT answer for the large majority of cancer types. Treating it as a fault is the failure
    mode this whole column has to avoid, so the gate must stay silent on it."""
    assert di._local_problems(DiseaseDerivedAlteration(derived_alteration="", basis="")) == []


def test_non_empty_answer_must_state_a_basis():
    probs = di._local_problems(DiseaseDerivedAlteration(derived_alteration="VHL alteration", basis=""))
    assert probs and "basis" in probs[0]


def test_finding_model_syntax_in_the_free_text_is_rejected():
    """Grammar conversion is the NEXT stage's job; a doer that jumps ahead hands the gene mapper something it
    would have to un-parse."""
    probs = di._local_problems(
        DiseaseDerivedAlteration(derived_alteration="SmallVariant[gene=VHL]", basis="VHL disease"))
    assert probs and "plain clinical English" in probs[0]


# --- the loop ---------------------------------------------------------------- #
def test_fires_and_records_the_basis(monkeypatch):
    _patch(monkeypatch, _Doer(DiseaseDerivedAlteration(derived_alteration="BCR::ABL1 fusion",
                                                       basis="the diagnosis requires t(9;22)")), [])
    row = di.infer_one(object(), "chronic myeloid leukaemia")
    assert row.derived_alteration == "BCR::ABL1 fusion"
    assert row.basis.startswith("the diagnosis requires")
    assert row.faithful


def test_empty_answer_passes_straight_through(monkeypatch):
    _patch(monkeypatch, _Doer(DiseaseDerivedAlteration()), [])
    row = di.infer_one(object(), "advanced clear cell renal cell carcinoma")
    assert row.derived_alteration == "" and row.faithful


def test_reviewer_rejection_drives_a_repair(monkeypatch):
    """The reviewer's job includes catching an over-fire. Here it rejects a prevalence-based answer and the
    doer's second attempt — the empty one — is what survives."""
    doer = _Doer(DiseaseDerivedAlteration(derived_alteration="VHL alteration", basis="common in ccRCC"),
                 DiseaseDerivedAlteration())
    _patch(monkeypatch, doer, [ReviewVerdict(faithful=False, problems=["prevalence is not definition"]),
                               ReviewVerdict(faithful=True)])
    row = di.infer_one(object(), "clear cell renal cell carcinoma", max_attempts=3)
    assert row.derived_alteration == ""
    assert "prevalence is not definition" in doer.inputs[1]


# --- persistence -------------------------------------------------------------- #
def test_map_round_trip_and_hits_only_view(tmp_path):
    rows = [
        DiseaseDerivedRow("chronic myeloid leukaemia", "BCR::ABL1 fusion", "WHO criterion",
                          "Fusion[geneStart=BCR & geneEnd=ABL1]"),
        DiseaseDerivedRow("advanced solid tumours", "", "", ""),
    ]
    full, hits = tmp_path / "m.tsv", tmp_path / "h.tsv"
    di.write_map(rows, full)
    di.write_map(rows, hits, hits_only=True)
    back = di.read_map(full)
    assert set(back) == {"chronic myeloid leukaemia", "advanced solid tumours"}
    assert back["chronic myeloid leukaemia"].finding_model == "Fusion[geneStart=BCR & geneEnd=ABL1]"
    assert len(di.read_map(hits)) == 1          # the hits view carries only what fired


def test_read_map_of_a_missing_file_is_empty(tmp_path):
    assert di.read_map(tmp_path / "nope.tsv") == {}
