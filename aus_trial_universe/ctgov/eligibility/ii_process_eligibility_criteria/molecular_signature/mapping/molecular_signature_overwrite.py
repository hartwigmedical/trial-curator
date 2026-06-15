from __future__ import annotations

from typing import Any, List, Optional

from aus_trial_universe.eligibility_utils.general import (
    is_effectively_empty,
    norm_cell,
)
from aus_trial_universe.eligibility_utils.general import (
    walk_trial,
)

from .molecular_signature_mapping import (
    MolecularSignatureKey,
    MolecularSignatureMap,
    make_molecular_signature_key,
)


def get_molecular_signature_key_from_node(
    node: Any,
) -> Optional[MolecularSignatureKey]:
    signature = norm_cell(getattr(node, "signature", None))

    if not signature:
        return None

    return make_molecular_signature_key(signature)


def rewrite_molecular_signature_node(
    node: Any,
    molecular_signature_curation: Optional[str],
) -> None:
    d = getattr(node, "__dict__", None)
    if not isinstance(d, dict):
        raise TypeError(f"MolecularSignatureCriterion node has no __dict__: {node}")

    d.clear()

    if (
        molecular_signature_curation
        and not is_effectively_empty(molecular_signature_curation)
    ):
        d["molecular_signature_curation"] = molecular_signature_curation


def overwrite_molecular_signature_in_rules(
    rules: List[Any],
    *,
    mapping: MolecularSignatureMap,
) -> None:
    def _visit(_rule: Any, node: Any, _parent: Optional[Any], _depth: int) -> None:
        if type(node).__name__ != "MolecularSignatureCriterion":
            return

        key = get_molecular_signature_key_from_node(node)
        mapped = None if key is None else mapping.get(key)

        if mapped is not None and is_effectively_empty(mapped):
            mapped = None

        rewrite_molecular_signature_node(
            node,
            molecular_signature_curation=mapped,
        )

    walk_trial(rules, _visit)