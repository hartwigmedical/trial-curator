from __future__ import annotations

from typing import Any, List, Optional

from aus_trial_universe.ctgov.ii_process_eligibility_criteria.general_utils.text_normalisation import (
    is_effectively_empty,
    norm_cell,
)
from aus_trial_universe.ctgov.ii_process_eligibility_criteria.general_utils.traverse_curation_tree import (
    walk_trial,
)

from .i_primary_tumor_mapping import (
    PrimaryTumorKey,
    PrimaryTumorMap,
    make_primary_tumor_key,
)


def get_primary_tumor_key_from_node(node: Any) -> Optional[PrimaryTumorKey]:
    raw_type = getattr(node, "primary_tumor_type", None)
    raw_location = getattr(node, "primary_tumor_location", None)

    tumor_type = norm_cell(raw_type)
    tumor_location = norm_cell(raw_location)

    if not tumor_type and not tumor_location:
        return None
    if tumor_type and tumor_location:
        return make_primary_tumor_key(tumor_type, tumor_location)
    if tumor_type:
        return make_primary_tumor_key(tumor_type, "")
    return make_primary_tumor_key("", tumor_location)


def rewrite_primary_tumor_node(
    node: Any,
    oncotree_curation: Optional[str],
) -> None:
    d = getattr(node, "__dict__", None)
    if not isinstance(d, dict):
        raise TypeError(f"PrimaryTumorCriterion node has no __dict__: {node}")

    d.clear()
    if oncotree_curation and not is_effectively_empty(oncotree_curation):
        d["Oncotree_curation"] = oncotree_curation


def overwrite_primary_tumour_in_rules(
    rules: List[Any],
    *,
    mapping: PrimaryTumorMap,
) -> None:
    def _visit(_rule: Any, node: Any, _parent: Optional[Any], _depth: int) -> None:
        if type(node).__name__ != "PrimaryTumorCriterion":
            return

        key = get_primary_tumor_key_from_node(node)
        mapped = None if key is None else mapping.get(key)

        if mapped is not None and is_effectively_empty(mapped):
            mapped = None

        rewrite_primary_tumor_node(node, oncotree_curation=mapped)

    walk_trial(rules, _visit)
