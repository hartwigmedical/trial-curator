from __future__ import annotations

from aus_trial_universe.drug_utility_path.oncotree_agentic_workflow.navigation import NavigatorMapper
from aus_trial_universe.drug_utility_path.oncotree_agentic_workflow.oncotree import OncoTree
from tests.drug_utility_path.oncotree_agentic_workflow.fakes import (
    ScriptedNavClient,
    node_decision,
    root_decision,
)


def _first_leaf_under(tree: OncoTree, code: str) -> str:
    for child in tree.children(code):
        if not tree.children(child.code):
            return child.code
    raise AssertionError(f"No leaf child found under {code}.")


def _node_codes_called(client: ScriptedNavClient) -> list[str]:
    return [
        ScriptedNavClient._node_code(prompt)
        for name, prompt in client.calls
        if name == "oncotree_node_decision"
    ]


def test_navigator_descends_and_stops_at_entailed_node(tree: OncoTree):
    client = ScriptedNavClient(
        root=root_decision("specific", LUNG="descend"),
        nodes={
            "LUNG": [node_decision(NSCLC="descend")],
            "NSCLC": [node_decision(stop_here=True)],
        },
    )

    outcome = NavigatorMapper(client, tree).map(input_text="non-small cell lung cancer")

    assert [m.code for m in outcome.proposed_mappings] == ["NSCLC"]
    assert outcome.trace.scope == "specific"
    # root + LUNG + NSCLC = 3 recorded steps.
    assert len(outcome.trace.steps) == 3


def test_navigator_supports_multi_label_fork(tree: OncoTree):
    client = ScriptedNavClient(
        root=root_decision("specific", BREAST="descend", LUNG="descend"),
        nodes={
            "BREAST": [node_decision(stop_here=True)],
            "LUNG": [node_decision(stop_here=True)],
        },
    )

    outcome = NavigatorMapper(client, tree).map(input_text="breast and lung cancer")

    assert {m.code for m in outcome.proposed_mappings} == {"BREAST", "LUNG"}


def test_navigator_emits_leaf_reached_by_descent_without_extra_call(tree: OncoTree):
    leaf = _first_leaf_under(tree, "LUNG")
    client = ScriptedNavClient(
        root=root_decision("specific", LUNG="descend"),
        nodes={"LUNG": [node_decision(**{leaf: "descend"})]},
    )

    outcome = NavigatorMapper(client, tree).map(input_text="some lung subtype")

    assert [m.code for m in outcome.proposed_mappings] == [leaf]
    # No node call was made for the leaf (it has no children).
    assert leaf not in _node_codes_called(client)


def test_navigator_emits_parent_via_stop_here_when_no_child_added(tree: OncoTree):
    client = ScriptedNavClient(
        root=root_decision("specific", LUNG="descend"),
        nodes={"LUNG": [node_decision(stop_here=True)]},
    )

    outcome = NavigatorMapper(client, tree).map(input_text="lung cancer")

    assert [m.code for m in outcome.proposed_mappings] == ["LUNG"]


def test_navigator_abstains_on_non_cancer_scope(tree: OncoTree):
    client = ScriptedNavClient(root=root_decision("non_cancer"))

    outcome = NavigatorMapper(client, tree).map(input_text="benign lung condition")

    assert outcome.proposed_mappings == ()
    assert outcome.trace.scope == "non_cancer"


def test_navigator_short_circuits_pan_cancer_scope(tree: OncoTree):
    client = ScriptedNavClient(root=root_decision("pan_cancer"))

    outcome = NavigatorMapper(client, tree).map(input_text="any NTRK-fusion cancer")

    assert [m.code for m in outcome.proposed_mappings] == ["Pan-cancer"]


def test_navigator_short_circuits_solid_tumour_scope(tree: OncoTree):
    client = ScriptedNavClient(root=root_decision("solid_tumour"))

    outcome = NavigatorMapper(client, tree).map(input_text="advanced solid tumours")

    assert [m.code for m in outcome.proposed_mappings] == ["Solid-Tumor"]


def test_navigator_emits_fallback_alongside_specific_codes(tree: OncoTree):
    # "Solid Tumor | Mesothelioma | NSCLC" -> Solid-Tumor plus specific codes.
    client = ScriptedNavClient(
        root=root_decision("specific", include_fallback="solid_tumour", LUNG="descend"),
        nodes={"LUNG": [node_decision(NSCLC="emit")]},
    )

    outcome = NavigatorMapper(client, tree).map(input_text="solid tumour | nsclc")

    codes = [m.code for m in outcome.proposed_mappings]
    assert "Solid-Tumor" in codes
    assert "NSCLC" in codes


def test_navigator_dedupes_ancestor_and_descendant(tree: OncoTree):
    # The model emits both the organ and a deeper node on the same path; only
    # the most specific should survive.
    client = ScriptedNavClient(
        root=root_decision("specific", LUNG="descend"),
        nodes={
            "LUNG": [node_decision(stop_here=True, NSCLC="descend")],
            "NSCLC": [node_decision(stop_here=True)],
        },
    )

    outcome = NavigatorMapper(client, tree).map(input_text="lung cancer")

    assert [m.code for m in outcome.proposed_mappings] == ["NSCLC"]


def test_navigator_honours_max_nodes_cap_and_flags_truncation(tree: OncoTree):
    client = ScriptedNavClient(
        root=root_decision("specific", LUNG="descend"),
        nodes={"LUNG": [node_decision(NSCLC="descend")]},
    )

    outcome = NavigatorMapper(client, tree, max_nodes=1).map(input_text="lung cancer")

    assert outcome.trace.truncated is True
