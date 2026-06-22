from __future__ import annotations

from aus_trial_universe.drug_utility_path.oncotree_agentic_workflow.oncotree import (
    OncoTree,
    remove_ancestor_codes,
)


def test_loader_reads_yaml_codes_and_paths(tree: OncoTree):
    assert tree.has_code("NSCLC")
    assert tree.get("NSCLC").name == "Non-Small Cell Lung Cancer"
    assert tree.path_names("NSCLC") == ("Lung", "Non-Small Cell Lung Cancer")


def test_root_nodes_and_children(tree: OncoTree):
    root_codes = {node.code for node in tree.root_nodes()}
    assert "LUNG" in root_codes
    assert "BREAST" in root_codes

    lung_children = {node.code for node in tree.children("LUNG")}
    assert "NSCLC" in lung_children
    assert tree.get("NSCLC").parent_code == "LUNG"


def test_children_of_unknown_code_is_empty(tree: OncoTree):
    assert tree.children("NOT_A_CODE") == ()


def test_is_ancestor_and_path(tree: OncoTree):
    assert tree.is_ancestor("LUNG", "NSCLC") is True
    assert tree.is_ancestor("NSCLC", "LUNG") is False


def test_remove_ancestor_codes_keeps_most_specific(tree: OncoTree):
    # LUNG is an ancestor of NSCLC, so only NSCLC survives.
    assert remove_ancestor_codes(["LUNG", "NSCLC"], tree) == ("NSCLC",)


def test_remove_ancestor_codes_keeps_independent_codes(tree: OncoTree):
    kept = remove_ancestor_codes(["BREAST", "LUNG"], tree)
    assert set(kept) == {"BREAST", "LUNG"}


def test_remove_ancestor_codes_keeps_unknown_codes(tree: OncoTree):
    # Fallback values are not in the tree and must not be dropped.
    assert remove_ancestor_codes(["Solid-Tumor"], tree) == ("Solid-Tumor",)
