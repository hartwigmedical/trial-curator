"""Cross-path arm referential-integrity check (arm_consistency.check) — every trial_arm_id referenced by the
eligibility tables and the drug path's trial_to_intervention must exist in the shared trial_arms registry.
Fake the three id sources via monkeypatch (no stores/API)."""
from __future__ import annotations

from aus_trial_universe.agentic.tasks.eligibility.qa import arm_consistency as ac


def _patch(monkeypatch, registry, elig, drug, roles=None):
    class _AS:
        @staticmethod
        def ids():
            return set(registry)
    monkeypatch.setattr(ac.TrialArmStore, "load", classmethod(lambda cls, *a, **k: _AS()))
    monkeypatch.setattr(ac, "_eligibility_arm_ids", lambda: set(elig))

    class _DS:
        @staticmethod
        def trial_arm_ids():
            return set(drug)

        @staticmethod
        def role_trial_arm_ids():
            return set(roles if roles is not None else drug)   # default: roles mirror the drug refs
    monkeypatch.setattr(ac.DrugRefStore, "load", classmethod(lambda cls, *a, **k: _DS()))


def test_dangling_refs_break_the_join(monkeypatch):
    # registry has T1::a; eligibility references a missing T1::b; drug references a missing T9::x
    _patch(monkeypatch, registry={"T1::a"}, elig={"T1::a", "T1::b"}, drug={"T1::a", "T9::x"}, roles={"T1::a"})
    r = ac.check()
    assert r["elig_dangling"] == ["T1::b"]
    assert r["drug_dangling"] == ["T9::x"]
    assert r["role_dangling"] == []


def test_dangling_role_ref_breaks_the_join(monkeypatch):
    # a trial_arm_drug_role row references an arm absent from the registry -> caught
    _patch(monkeypatch, registry={"T1::a"}, elig={"T1::a"}, drug={"T1::a"}, roles={"T1::a", "T5::z"})
    r = ac.check()
    assert r["role_dangling"] == ["T5::z"]
    assert not r["elig_dangling"] and not r["drug_dangling"]


def test_unused_registry_arm_is_informational(monkeypatch):
    _patch(monkeypatch, registry={"T1::a", "T1::b"}, elig={"T1::a"}, drug={"T1::a"}, roles={"T1::a"})
    r = ac.check()
    assert not r["elig_dangling"] and not r["drug_dangling"] and not r["role_dangling"]
    assert r["unused"] == ["T1::b"]           # in the registry, referenced by neither path yet


def test_fully_consistent(monkeypatch):
    _patch(monkeypatch, registry={"T1::a", "T2::i", "T2::c"},
           elig={"T1::a", "T2::i", "T2::c"}, drug={"T1::a", "T2::i", "T2::c"})
    r = ac.check()
    assert not r["elig_dangling"] and not r["drug_dangling"] and not r["role_dangling"] and not r["unused"]
    assert r["registry"] == 3 and r["elig_refs"] == 3 and r["drug_refs"] == 3 and r["role_refs"] == 3
