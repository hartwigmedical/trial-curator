from __future__ import annotations

from typing import Any, List, Optional

from aus_trial_universe.ctgov.utils.general.text_normalisation import (
    is_effectively_empty,
    norm_cell,
)
from aus_trial_universe.ctgov.utils.general.traverse_curation_tree import (
    walk_trial,
)

from .gene_alteration_mapping import (
    GeneAlterationKey,
    GeneAlterationMap,
    make_gene_alteration_key,
)


def get_gene_alteration_key_from_node(node: Any) -> Optional[GeneAlterationKey]:
    gene = norm_cell(getattr(node, "gene", None))
    alteration = norm_cell(getattr(node, "alteration", None))
    variant = norm_cell(getattr(node, "variant", None))

    if not gene and not alteration and not variant:
        return None

    return make_gene_alteration_key(gene, alteration, variant)


def rewrite_gene_alteration_node(
    node: Any,
    gene_alteration_curation: Optional[str],
) -> None:
    d = getattr(node, "__dict__", None)
    if not isinstance(d, dict):
        raise TypeError(f"GeneAlterationCriterion node has no __dict__: {node}")

    d.clear()
    if gene_alteration_curation and not is_effectively_empty(gene_alteration_curation):
        d["gene_alteration_curation"] = gene_alteration_curation


def overwrite_gene_alteration_in_rules(
    rules: List[Any],
    *,
    mapping: GeneAlterationMap,
) -> None:
    def _visit(_rule: Any, node: Any, _parent: Optional[Any], _depth: int) -> None:
        if type(node).__name__ != "GeneAlterationCriterion":
            return

        key = get_gene_alteration_key_from_node(node)
        mapped = None if key is None else mapping.get(key)

        if mapped is not None and is_effectively_empty(mapped):
            mapped = None

        rewrite_gene_alteration_node(node, gene_alteration_curation=mapped)

    walk_trial(rules, _visit)