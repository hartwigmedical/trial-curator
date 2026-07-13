"""drug_ref build workflow (fake client): alias dedup, once-per-canonical research, incremental reuse."""
from __future__ import annotations

import threading
from datetime import date

from aus_trial_universe.agentic.core.client import LlmResult
from aus_trial_universe.agentic.tasks.drug_ref.schema import (
    ApprovalByIndication,
    ApprovedIndication,
    Canonicalization,
    DrugAnnotation,
    ReviewVerdict,
)
from aus_trial_universe.agentic.tasks.drug_ref.store import DrugRefStore
from aus_trial_universe.agentic.tasks.drug_ref.workflow import build_drug_ref


class _FakeClient:
    """Canned outputs by schema (parse + research alike). Canonicalization is keyed by raw substring."""

    def __init__(self, canon_map, annotation, approval):
        self.canon_map = canon_map
        self.annotation = annotation
        self.approval = approval
        self.calls = {"canon": 0, "annot": 0, "appr": 0}
        self._lock = threading.Lock()  # research() fans out across threads

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
            return self._r(Canonicalization(canonical_name=""))
        if schema is DrugAnnotation:
            self._bump("annot")
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
            "Keytruda": Canonicalization(canonical_name="pembrolizumab", rxcui="1547545", aliases=["Keytruda", "MK-3475"]),
            "MK-3475": Canonicalization(canonical_name="pembrolizumab", rxcui="1547545"),
            "Ris-Rez": Canonicalization(canonical_name="risvutatug rezetecan", is_investigational=True),
            "Radiotherapy": Canonicalization(canonical_name=""),   # not a drug
        },
        annotation=DrugAnnotation(modality="mAb", mechanism="anti-PD-1", drug_class="checkpoint inhibitor",
                                  pottr_drug_class="cancer_therapy -> immunotherapy -> anti-PD-1", atc_code="L01FF02"),
        approval=ApprovalByIndication(indications=[
            ApprovedIndication(indication_raw="unresectable Stage III/IV melanoma", cancer_type="melanoma",
                               tga_status="approved", pbs_status="approved"),
            ApprovedIndication(indication_raw="NSCLC PD-L1>=50% 1L", cancer_type="NSCLC", biomarker="PD-L1 >=50%",
                               line_of_therapy="1L", tga_status="approved", pbs_status="not_approved"),
        ]),
    )


def test_build_dedups_aliases_and_researches_each_canonical_once():
    client = _client()
    store = DrugRefStore()
    summary = build_drug_ref(client, ["Keytruda", "MK-3475", "Ris-Rez", "Radiotherapy"], store, today=date(2026, 7, 13))
    assert summary.canonicalized == 4 and summary.non_drug == 1        # Radiotherapy -> not a drug
    assert summary.researched == 2                                     # 2 real canonicals, not 3 raw drug names
    assert client.calls["annot"] == 2 and client.calls["appr"] == 2    # research is once-per-canonical
    assert store.canonical_for("Keytruda") == store.canonical_for("MK-3475") != ""   # alias dedup
    pid = store.canonical_for("Keytruda")
    assert store.ref(pid).mechanism == "anti-PD-1" and store.ref(pid).atc_code == "L01FF02"
    inds = store.indications_for(pid)
    assert {i.cancer_type for i in inds} == {"melanoma", "NSCLC"}
    nsclc = next(i for i in inds if i.cancer_type == "NSCLC")
    assert nsclc.biomarker == "PD-L1 >=50%" and nsclc.pbs_status == "not_approved"   # cancer+biomarker; per-agency


def test_incremental_reuses_existing(tmp_path):
    client = _client()
    store = DrugRefStore()
    build_drug_ref(client, ["Keytruda", "Ris-Rez"], store, today=date(2026, 7, 13))
    store.save(tmp_path, on=date(2026, 7, 13))
    after_first = dict(client.calls)

    store2 = DrugRefStore.load(tmp_path)              # a later build reads the resource
    summary = build_drug_ref(client, ["Keytruda", "Ris-Rez"], store2, today=date(2026, 7, 13))
    assert summary.reused_alias == 2 and summary.researched == 0 and summary.reused_ref == 2
    assert client.calls == after_first               # pure lookup — no new LLM calls


def test_refresh_forces_re_research(tmp_path):
    client = _client()
    store = DrugRefStore()
    build_drug_ref(client, ["Keytruda"], store, today=date(2026, 7, 13))
    store.save(tmp_path, on=date(2026, 7, 13))
    before = dict(client.calls)

    store2 = DrugRefStore.load(tmp_path)
    summary = build_drug_ref(client, ["Keytruda"], store2, refresh=True, today=date(2026, 7, 13))
    assert summary.researched == 1
    assert client.calls["annot"] == before["annot"] + 1   # re-researched despite already present
