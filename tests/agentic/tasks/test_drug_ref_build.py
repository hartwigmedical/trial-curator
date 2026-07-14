"""drug_ref build workflow (fake client): alias dedup, once-per-canonical research, deterministic lookups
wired in (mocked here — OOTB-safe), namespaced canonical_id, drug_target pairs, incremental reuse."""
from __future__ import annotations

import threading
from datetime import date

import pytest

from aus_trial_universe.agentic.core.client import LlmResult
from aus_trial_universe.agentic.tasks.drug_ref import pottr, rxnorm
from aus_trial_universe.agentic.tasks.drug_ref.schema import (
    ApprovalByIndication,
    ApprovedIndication,
    CanonicalComponent,
    Canonicalization,
    DrugAnnotation,
    ReviewVerdict,
    TargetAction,
)
from aus_trial_universe.agentic.tasks.drug_ref.store import DrugRefStore
from aus_trial_universe.agentic.tasks.drug_ref.workflow import build_drug_ref


class _FakeClient:
    """Canned outputs by schema (parse + research alike). Canonicalization is keyed by raw substring."""

    def __init__(self, canon_map, annotation, approval, raise_annot_for=None):
        self.canon_map = canon_map
        self.annotation = annotation
        self.approval = approval
        self.raise_annot_for = raise_annot_for   # simulate an unrecoverable failure for one drug
        self.calls = {"canon": 0, "annot": 0, "appr": 0}
        self._lock = threading.Lock()

    def _r(self, obj):
        return LlmResult(obj, "fake", "{}", False, 1)

    def _bump(self, key):
        with self._lock:
            self.calls[key] += 1

    def parse(self, output_schema, *, instructions, user_input, model=None,
              temperature=None, seed=None, max_completion_tokens=None):
        return self._dispatch(output_schema, user_input)

    def research(self, output_schema, *, instructions, user_input, model=None, max_completion_tokens=None):
        return self._dispatch(output_schema, user_input)

    def _dispatch(self, schema, user_input):
        if schema is Canonicalization:
            self._bump("canon")
            for key, c in self.canon_map.items():
                if key in user_input:
                    return self._r(c)
            return self._r(Canonicalization(components=[]))
        if schema is DrugAnnotation:
            self._bump("annot")
            if self.raise_annot_for and self.raise_annot_for in user_input:
                raise RuntimeError("simulated unrecoverable API failure")
            return self._r(self.annotation)
        if schema is ApprovalByIndication:
            self._bump("appr")
            return self._r(self.approval)
        if schema is ReviewVerdict:
            return self._r(ReviewVerdict(faithful=True))
        raise AssertionError(f"unexpected schema {schema}")


def _client():
    return _FakeClient(
        canon_map={
            "Keytruda": Canonicalization(components=[
                CanonicalComponent(canonical_name="pembrolizumab", aliases=["Keytruda", "MK-3475"])]),
            "MK-3475": Canonicalization(components=[CanonicalComponent(canonical_name="pembrolizumab")]),
            "Ris-Rez": Canonicalization(components=[
                CanonicalComponent(canonical_name="risvutatug rezetecan", is_investigational=True)]),
            "Radiotherapy": Canonicalization(components=[]),   # not a drug
        },
        annotation=DrugAnnotation(modality="monoclonal antibody",
                                  targets=[TargetAction(target="PD-1", action="antagonist")],
                                  drug_class="checkpoint inhibitor",
                                  fda_status="approved (2014): melanoma | NSCLC", ema_status="approved (2015)"),
        approval=ApprovalByIndication(indications=[
            ApprovedIndication(indication_raw="unresectable Stage III/IV melanoma", cancer_type="melanoma",
                               combination="monotherapy", tga_status="approved", pbs_status="approved"),
            ApprovedIndication(indication_raw="NSCLC PD-L1>=50% 1L", cancer_type="NSCLC", biomarker="PD-L1 >=50%",
                               line_of_therapy="1L", combination="in combination with chemotherapy",
                               tga_status="approved", pbs_status="not_approved"),
        ]),
    )


@pytest.fixture(autouse=True)
def _mock_deterministic(monkeypatch):
    """rxcui / atc / pottr are deterministic lookups over big gitignored data — mock them for unit tests."""
    rx = {"pembrolizumab": "1547545"}
    atc = {"pembrolizumab": "L01FF02"}
    pt = {"pembrolizumab": "cancer_therapy -> anti-PD-1_monoclonal_antibody"}
    monkeypatch.setattr(rxnorm, "resolve_rxcui", lambda n: rx.get(n.strip().lower(), ""))
    monkeypatch.setattr(rxnorm, "atc_code_for", lambda n: atc.get(n.strip().lower(), ""))
    monkeypatch.setattr(pottr, "pottr_class_for", lambda n: pt.get(n.strip().lower(), ""))


def test_build_dedups_researches_once_and_wires_deterministic_facts():
    client = _client()
    store = DrugRefStore()
    summary = build_drug_ref(client, ["Keytruda", "MK-3475", "Ris-Rez", "Radiotherapy"], store, today=date(2026, 7, 13))
    assert summary.canonicalized == 4 and summary.non_drug == 1        # Radiotherapy -> not a drug
    assert summary.researched == 2                                     # 2 real canonicals, not 3 raw names
    assert client.calls["annot"] == 2 and client.calls["appr"] == 2    # once-per-canonical

    # namespaced canonical_id: rxcui:<n> when resolved, name:<x> for investigational
    pid = store.canonical_ids_for("Keytruda")[0]
    assert store.canonical_ids_for("Keytruda") == store.canonical_ids_for("MK-3475") == ["rxcui:1547545"]  # dedup + rxcui id
    assert store.canonical_ids_for("Ris-Rez") == ["name:risvutatug rezetecan"]

    r = store.ref(pid)
    assert r.rxcui == "1547545" and r.atc_code == "L01FF02"            # deterministic lookups wired in
    assert r.pottr_drug_class == "cancer_therapy -> anti-PD-1_monoclonal_antibody"
    assert r.modality == "monoclonal antibody" and r.aliases == "Keytruda | MK-3475"
    assert store.targets_for(pid)[0].target == "PD-1"                  # drug_target pairs
    inds = store.indications_for(pid)
    nsclc = next(i for i in inds if i.cancer_type == "NSCLC")
    assert nsclc.biomarker == "PD-L1 >=50%" and nsclc.pbs_status == "not_approved" and \
        nsclc.combination == "in combination with chemotherapy"


def test_incremental_reuses_existing(tmp_path):
    client = _client()
    store = DrugRefStore()
    build_drug_ref(client, ["Keytruda", "Ris-Rez"], store, today=date(2026, 7, 13))
    store.save(tmp_path, on=date(2026, 7, 13))
    after_first = dict(client.calls)

    store2 = DrugRefStore.load(tmp_path)
    summary = build_drug_ref(client, ["Keytruda", "Ris-Rez"], store2, today=date(2026, 7, 13))
    assert summary.reused_alias == 2 and summary.researched == 0 and summary.reused_ref == 2
    assert client.calls == after_first               # pure lookup — no new LLM calls


def test_build_soft_fails_one_drug_and_continues():
    """A single unrecoverable drug failure must not kill the run — it is skipped, counted, others proceed."""
    client = _FakeClient(
        canon_map={"DrugA": Canonicalization(components=[CanonicalComponent(canonical_name="druga")]),
                   "DrugB": Canonicalization(components=[CanonicalComponent(canonical_name="drugb")])},
        annotation=DrugAnnotation(modality="small molecule", drug_class="x"),
        approval=ApprovalByIndication(indications=[]),
        raise_annot_for="drugb",   # DrugB's annotate blows up
    )
    store = DrugRefStore()
    summary = build_drug_ref(client, ["DrugA", "DrugB"], store, today=date(2026, 7, 13), workers=1)
    assert summary.researched == 1 and summary.failed == 1        # DrugA done; DrugB failed but did not crash
    assert store.ref("name:druga").modality == "small molecule"   # the good drug is fully researched
    assert store.ref("name:drugb").researched_on == ""            # the failed drug stays seed-only (retried next run)


def test_checkpoint_called_per_batch():
    client = _client()
    store = DrugRefStore()
    calls = {"n": 0}
    build_drug_ref(client, ["Keytruda", "Ris-Rez"], store, today=date(2026, 7, 13),
                   workers=1, checkpoint=lambda: calls.__setitem__("n", calls["n"] + 1))
    assert calls["n"] >= 3                            # per Stage-1 batch + per research batch (workers=1 -> per drug)


def test_patient_population_generic_patients_normalized_to_empty():
    """The uninformative generic 'patients' population value is dropped; real values are kept (spec §6.1)."""
    from aus_trial_universe.agentic.tasks.drug_ref.workflow import _clean_pp
    assert _clean_pp("patients") == "" and _clean_pp("  Patients ") == ""
    assert _clean_pp("adult") == "adult" and _clean_pp("pediatric >=1 year") == "pediatric >=1 year"


def test_combination_raw_splits_into_multiple_canonicals():
    """A combination raw token maps to N standalone canonicals (1 raw -> N), each researched once."""
    client = _FakeClient(
        canon_map={"Nivo + Ipi": Canonicalization(components=[
            CanonicalComponent(canonical_name="nivolumab"),
            CanonicalComponent(canonical_name="ipilimumab")])},
        annotation=DrugAnnotation(modality="monoclonal antibody", drug_class="checkpoint inhibitor"),
        approval=ApprovalByIndication(indications=[]),
    )
    store = DrugRefStore()
    summary = build_drug_ref(client, ["Nivo + Ipi"], store, today=date(2026, 7, 13), workers=2)
    assert store.canonical_ids_for("Nivo + Ipi") == ["name:nivolumab", "name:ipilimumab"]   # 1 raw -> 2 canonicals
    assert summary.researched == 2 and client.calls["annot"] == 2                            # each atom researched once
    assert store.ref("name:nivolumab") and store.ref("name:ipilimumab")
