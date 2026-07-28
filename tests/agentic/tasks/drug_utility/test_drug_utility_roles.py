"""Phase-2 per-arm drug role (main vs auxiliary) classification (fake client, no API).

Exercises `classify_arm_roles`: the happy path (novel agent = main, backbone = auxiliary), a control arm that is
all-auxiliary, the mixed-bundle case, no-drug arms skipped, lookup-first reuse, and the deterministic completeness
gate (a missing assignment is flagged and the doer re-invoked with feedback)."""
from __future__ import annotations

import re

from aus_trial_universe.agentic.core.client import LlmResult
from aus_trial_universe.agentic.tasks.drug_utility.schema import (
    ArmDrugRole,
    ArmRoleClassification,
    DrugAnnotationsCore,
    ReviewVerdict,
)
from aus_trial_universe.agentic.tasks.drug_utility.store import DrugRefStore
from aus_trial_universe.agentic.tasks.drug_utility.workflow import ArmContext, classify_arm_roles
from aus_trial_universe.agentic.tasks.shared.cohorts import trial_arm_id

# canonical_id renders as `canonical_id=<cid> | name=...`; capture the full id (may contain spaces, e.g. name:x y).
_CID_RE = re.compile(r"canonical_id=(.+?) \| name=")


class _FakeRoleClient:
    """Classifier returns a role per canonical_id it finds in the prompt, via a policy(cid)->role; reviewer says
    faithful. `drop_last_until_feedback` simulates an incomplete first attempt to exercise the completeness gate."""

    def __init__(self, policy, *, reviewer_faithful=True, drop_last_until_feedback=False):
        self.policy = policy
        self.reviewer_faithful = reviewer_faithful
        self.drop_last_until_feedback = drop_last_until_feedback
        self.calls = {"role": 0, "review": 0}

    def _r(self, obj):
        return LlmResult(obj, "fake", "{}", False, 1)

    def parse(self, output_schema, *, instructions, user_input, model=None,
              temperature=None, seed=None, max_completion_tokens=None, **_):
        if output_schema is ArmRoleClassification:
            self.calls["role"] += 1
            cids = _CID_RE.findall(user_input)
            if self.drop_last_until_feedback and "[Reviewer feedback" not in user_input:
                cids = cids[:-1]                                    # incomplete first attempt
            return self._r(ArmRoleClassification(
                assignments=[ArmDrugRole(canonical_id=c, role=self.policy(c)) for c in cids]))
        if output_schema is ReviewVerdict:
            self.calls["review"] += 1
            return self._r(ReviewVerdict(faithful=self.reviewer_faithful))
        raise AssertionError(f"unexpected schema {output_schema}")

    research = parse   # unused (no web_search), but keep the interface uniform


def _store_with_arm(taid, input_name, components):
    """A store where `taid` uses `input_name` which maps to `components` = [(canonical_id, canonical_name), ...]."""
    s = DrugRefStore()
    s.set_mapping(input_name, [(cn, cid) for cid, cn in components])
    s.add_occurrence(taid, input_name)
    for cid, cn in components:
        s.put_ref(DrugAnnotationsCore(canonical_id=cid, canonical_name=cn, drug_class="x", modality="small molecule"))
    return s


def test_novel_agent_is_main_backbone_is_auxiliary():
    taid = trial_arm_id("NCT1", "Experimental")
    store = _store_with_arm(taid, "Gedatolisib + Palbociclib + Fulvestrant", [
        ("name:gedatolisib", "gedatolisib"),          # novel agent (no rxcui) -> main
        ("rxcui:111", "palbociclib"),                 # approved backbone -> auxiliary
        ("rxcui:222", "fulvestrant"),                 # approved backbone -> auxiliary
    ])
    client = _FakeRoleClient(lambda c: "main" if c.startswith("name:") else "auxiliary")
    summary = classify_arm_roles(client, [ArmContext(taid, "Experimental", "EXPERIMENTAL", "novel + backbone")], store)

    assert summary.classified == 1 and summary.no_drugs == 0
    roles = {r.canonical_id: r.role for r in store.roles_for(taid)}
    assert roles == {"name:gedatolisib": "main", "rxcui:111": "auxiliary", "rxcui:222": "auxiliary"}


def test_comparator_arm_all_auxiliary():
    taid = trial_arm_id("NCT1", "Control")
    store = _store_with_arm(taid, "Palbociclib + Fulvestrant", [
        ("rxcui:111", "palbociclib"), ("rxcui:222", "fulvestrant")])
    client = _FakeRoleClient(lambda c: "auxiliary")                 # a control arm: everything is auxiliary
    classify_arm_roles(client, [ArmContext(taid, "Control", "ACTIVE_COMPARATOR", "SoC")], store)

    assert {r.role for r in store.roles_for(taid)} == {"auxiliary"}
    assert len(store.roles_for(taid)) == 2


def test_no_drug_arm_is_skipped():
    taid = trial_arm_id("NCT1", "Placebo")
    s = DrugRefStore()
    s.set_mapping("Placebo", [])                                    # non-drug: no canonical
    s.add_occurrence(taid, "Placebo")
    client = _FakeRoleClient(lambda c: "auxiliary")
    summary = classify_arm_roles(client, [ArmContext(taid, "Placebo", "PLACEBO_COMPARATOR", "")], s)

    assert summary.no_drugs == 1 and summary.classified == 0
    assert s.roles_for(taid) == [] and client.calls["role"] == 0     # nothing to classify -> no LLM call


def test_lookup_first_reuses_already_classified_arm():
    taid = trial_arm_id("NCT1", "Experimental")
    store = _store_with_arm(taid, "DrugA", [("name:druga", "druga")])
    client = _FakeRoleClient(lambda c: "main")
    classify_arm_roles(client, [ArmContext(taid, "Experimental", "EXPERIMENTAL", "")], store)
    assert client.calls["role"] == 1

    # second pass, no refresh -> the arm is reused, no new classifier call
    summary = classify_arm_roles(client, [ArmContext(taid, "Experimental", "EXPERIMENTAL", "")], store)
    assert summary.reused == 1 and summary.classified == 0 and client.calls["role"] == 1

    # refresh -> re-classified
    summary = classify_arm_roles(client, [ArmContext(taid, "Experimental", "EXPERIMENTAL", "")], store, refresh=True)
    assert summary.classified == 1 and client.calls["role"] == 2


def test_completeness_gate_flags_missing_assignment_and_recovers():
    """A first attempt that omits a drug is flagged by the deterministic check; the doer is re-invoked with
    feedback and the final rows cover every drug (no orphan / no missing)."""
    taid = trial_arm_id("NCT1", "Experimental")
    store = _store_with_arm(taid, "A + B", [("name:a", "a"), ("name:b", "b")])
    client = _FakeRoleClient(lambda c: "main", drop_last_until_feedback=True)
    classify_arm_roles(client, [ArmContext(taid, "Experimental", "EXPERIMENTAL", "")], store)

    assert client.calls["role"] >= 2                                # retried after the missing-assignment flag
    assert {r.canonical_id for r in store.roles_for(taid)} == {"name:a", "name:b"}


def test_role_prompt_decisions_present():
    """Guard the locked role rules in the prompts (prompt-only judgement): main = investigational under study,
    auxiliary = backbone/SoC/comparator/placebo, and a control arm is not given a spurious main."""
    from aus_trial_universe.agentic.tasks.drug_utility.agents import (
        ROLE_CLASSIFIER_INSTRUCTIONS as C, ROLE_REVIEWER_INSTRUCTIONS as R)
    assert "main" in C and "auxiliary" in C
    assert "investigational" in C.lower() and "backbone" in C.lower()
    assert "ACTIVE_COMPARATOR" in C and "PLACEBO_COMPARATOR" in C
    assert "spurious" in R.lower() or "control arm" in R.lower()
