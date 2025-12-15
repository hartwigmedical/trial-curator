import argparse
import sys
import logging
from pathlib import Path
from typing import Any, List, Tuple, Dict, Optional, Callable, Iterable
import math
import numbers
import pandas as pd

from au_trial_universe.ctgov_llm_curation_loader import load_curated_rules

logger = logging.getLogger(__name__)

SEARCHING_CRITERIA = [
    "PrimaryTumorCriterion", "HistologyCriterion",
    "MolecularBiomarkerCriterion",
    "MolecularSignatureCriterion",
    "GeneAlterationCriterion",
]

MOVE_TO_CRITERION: Dict[str, List[str]] = {
    "GeneAlterationCriterion_1": ["gene"],
    "GeneAlterationCriterion_2": ["alteration"],
    "GeneAlterationCriterion_3": ["variant"],
    "MolecularSignatureCriterion": ["signature"],
    "MolecularBiomarkerCriterion": ["biomarker", "expression_type"],
}


# 1. BASIC TREE TRAVERSAL LOGIC
"""
A note on terminology:

    Node: a criterion instance (e.g. GeneAlterationCriterion, AndCriterion)

    Children: any node inside `criteria`, `criterion` or `condition`
    Tree: one root node & its descendants
    Forest: a list of trees inside a Rule() object
    Super-forest: the entire .py file for one trial

Which correspond one-to-one to the functions below

    def iter_children(node):
    def walk_node(node):
    def walk_forest(node):
    def walk_trial(node)
"""


def iter_children(node: Any) -> List[Any]:
    """
    Return all children nodes from a node, where
    Children: any node found in attributes:
        - `criteria`  (a list of nodes)
        - `criterion` (single child node)
        - `condition` (single child node)

    If it is a leaf node (no children), then iter_children() returns an empty list []
    """
    children: List[Any] = []

    multi = getattr(node, "criteria", None)
    if multi is not None:
        if isinstance(multi, (list, tuple)):
            children.extend(multi)
        else:  # to defend against LLM inconsistency
            children.append(multi)

    for attr_name in ("criterion", "condition"):
        child = getattr(node, attr_name, None)
        if child is not None:
            children.append(child)

    return children


def walk_node(node: Any, visit_logic: Callable[[Any, Optional[Any], int], None], parent: Optional[Any] = None,
              depth: int = 0) -> None:
    """
    DFS starting from a single root node from a tree

    node = current node (root at the first call)
    visit_logic = a Callback function
    parent = a parent node (None for the root node)
    depth = depth in the tree (where root = 0).
    """
    # 1. Process the current node
    visit_logic(node, parent, depth)

    # 2. Recurse into all children
    for child in iter_children(node):
        walk_node(child, visit_logic, parent=node, depth=depth + 1)


def walk_forest(forest: Iterable[Any], visit_logic: Callable[[Any, Optional[Any], int], None]) -> None:
    """
    Traverse a Forest which is a list of Tree roots inside a Rule() object.
    """
    for root in forest:
        walk_node(root, visit_logic, parent=None, depth=0)


def _normalise_forest_into_list(rule: Any) -> list[Any]:  # For dealing with LLM inconsistency
    """
    Normalise rule.curation into a list of root nodes.

    Handles three cases:
        - curation is missing or None -> []
        - curation is a list/tuple    -> list(curation)
        - curation is a single node   -> [curation]
    """
    cur = getattr(rule, "curation", None)
    if cur is None:
        return []

    if isinstance(cur, (list, tuple)):
        return list(cur)

    return [cur]


def walk_trial(rules: Iterable[Any], visit_logic: Callable[[Any, Any, Optional[Any], int], None]) -> None:
    """
    Traverse the trial file (a super-forest) which contains all the rule objects
    """
    for rule in rules:
        rule_text = getattr(rule, "rule_text", None)
        if rule_text is None:
            logger.error("Missing rule text")
            raise ValueError("Missing rule text on a Rule object")

        forest = _normalise_forest_into_list(rule)

        def _node_visitor(node: Any, parent: Any, depth: int, _rule=rule) -> None:
            visit_logic(_rule, node, parent, depth)

        walk_forest(forest, _node_visitor)


# 2. CRITERION SEARCHING LOGIC
# 2a. rule selection
def rule_has_search_criterion(rule: Any) -> bool:
    found = False
    forest = _normalise_forest_into_list(rule)

    def _node_search(node: Any, parent: Any, depth: int) -> None:
        nonlocal found
        if found:  # Early exit: already found one, no need to keep walking
            return

        cls_name = type(node).__name__
        if cls_name in SEARCHING_CRITERIA:
            found = True

    walk_forest(forest, _node_search)
    return found


def filter_rules_by_search_criteria(rules: List[Any]) -> List[Any]:
    """
    Keep only Rule() objects that contain at least one searching criterion
    """
    kept_rules: List[Any] = []

    for rule in rules:
        if rule_has_search_criterion(rule):
            kept_rules.append(rule)

    return kept_rules


# 2b. prune subtrees that do not contain any searching criteria
def _prune_node(node: Any) -> bool:
    """
    Recursively prune a node's children so that only subtrees containing at least one SEARCHING_CRITERIA class remain.
    """
    # If this node has no attributes left at all, treat it as non-target and drop it
    node_dict = getattr(node, "__dict__", None)
    if isinstance(node_dict, dict) and not node_dict:
        return False

    cls_name = type(node).__name__
    has_target = cls_name in SEARCHING_CRITERIA

    crit_list = getattr(node, "criteria", None)
    if isinstance(crit_list, (list, tuple)):
        new_children: List[Any] = []

        for child in crit_list:
            if _prune_node(child):
                new_children.append(child)

        # Replace with pruned list
        setattr(node, "criteria", new_children)
        if new_children:
            # If any child subtree has a target, this node's subtree has a target
            has_target = True

    for attr_name in ("criterion", "condition"):
        child = getattr(node, attr_name, None)
        if child is not None:
            keep_child = _prune_node(child)

            if keep_child:
                has_target = True
            else:
                # Drop the child link entirely
                setattr(node, attr_name, None)

    return has_target


def prune_nontarget_criteria_in_rule(rule: Any) -> None:
    """
    For a single Rule(), prune its Forest (rule.curation) so that only trees whose subtrees contain at least one searching criterion remain.
    """
    forest = _normalise_forest_into_list(rule)
    new_forest: List[Any] = []

    for root in forest:
        if _prune_node(root):
            new_forest.append(root)

    # Replace the forest with only the pruned roots
    setattr(rule, "curation", new_forest)


def prune_nontarget_criteria_in_rules(rules: List[Any]) -> None:
    for rule in rules:
        prune_nontarget_criteria_in_rule(rule)


# 2c. remove unused attributes ("description") and fields ("excluded", "flipped")
def remove_descriptions_from_rules(rules: List[Any]) -> None:

    def _remove_description_from_nodes(node: Any) -> None:
        node_dict = getattr(node, "__dict__", None)
        if isinstance(node_dict, dict):
            node_dict.pop("description", None)

    def _visit(rule: Any, node: Any, parent: Any, depth: int) -> None:
        _remove_description_from_nodes(node)

    walk_trial(rules, visit_logic=_visit)


def remove_exclude_and_flipped_from_rules(rules: List[Any]) -> None:
    for rule in rules:
        rule_dict = getattr(rule, "__dict__", None)

        if isinstance(rule_dict, dict):
            rule_dict.pop("exclude", None)
            rule_dict.pop("flipped", None)


# 3. ATTRIBUTE OVERWRITE LOGIC
# 3a. load resource files

# i. helper functions
def _is_empty(x: Any) -> bool:
    if x is None:
        return True
    if isinstance(x, numbers.Real):
        try:
            if math.isnan(x):
                return True
        except TypeError:
            pass
    return str(x).strip() == ""


def _clean_ele(value: str) -> str:
    return value.strip().lower()


def _norm(x: Any) -> str:
    if x is None:
        return ""
    if isinstance(x, numbers.Real):
        try:
            if math.isnan(x):
                return ""
        except TypeError:
            pass
    return _clean_ele(str(x))


def _map_name_for(cur_col: str) -> str:
    """Given '<Prefix>_curation_<suffix>', return '<suffix>'."""
    return cur_col.split("_curation_", 1)[1]


def _collect_lookup_fields(dfs: list[pd.DataFrame], prefix: str) -> set[str]:
    """
    From a list of dataframes, collect all field names that appear after a given `<Criterion>_lookup_` prefix.
    E.g. 'GeneAlterationCriterion_lookup_gene' -> 'gene'.
    """
    relevant_cols: set[str] = set()

    for df in dfs:
        for col in df.columns:
            if col.startswith(prefix):
                relevant_cols.add(col[len(prefix):])

    return relevant_cols


def _drop_non_lookup_fields(node_dict: dict, relevant_fields: set[str], extra_keep: Optional[set[str]] = None) -> None:
    keep_fields = set(relevant_fields)
    if extra_keep:
        keep_fields.update(extra_keep)

    for key in list(node_dict.keys()):
        if key not in keep_fields:
            node_dict.pop(key, None)


# ii. load and organise resource files

# Common mojibake sequences in CSV to correct
MOJIBAKE_REPLACEMENTS: Dict[str, str] = {
    # comparisons
    "‚â•": "≥",
    "â‰¥": "≥",
    "â‰¤": "≤",

    # bullets / dots
    "â€¢": "•",
    "â—": "•",

    # dashes
    "â€“": "-",
    "â€”": "-",

    # quotes
    "â€˜": "'",
    "â€™": "'",
    "â€œ": '"',
    "â€�": '"',

    # misc symbols
    "Ã—": "×",
    "Ã·": "÷",
    "Â±": "±",
    "Â°": "°",

    # stray non-breaking-space marker
    "Â ": " ",
}


def _fix_mojibake_str(value: str) -> str:
    result = value
    for bad, good in MOJIBAKE_REPLACEMENTS.items():
        if bad in result:
            result = result.replace(bad, good)
    return result


def _fix_mojibake_df(df: pd.DataFrame) -> pd.DataFrame:
    obj_cols = df.select_dtypes(include=["object"]).columns
    for col in obj_cols:
        df[col] = df[col].apply(
            lambda v: _fix_mojibake_str(v) if isinstance(v, str) else v
        )
    return df


def load_criterion_resources(resource_dir: Path, criterion_prefix: str) -> Dict[str, Any]:
    dfs: List[pd.DataFrame] = []
    value_maps: Dict[str, Dict[Any, str]] = {}
    move_to_map: Dict[Any, str] = {}

    for csv_path in sorted(resource_dir.glob("*.csv")):
        df = pd.read_csv(csv_path)
        df = _fix_mojibake_df(df)

        lookup_cols = [c for c in df.columns if c.startswith(f"{criterion_prefix}_lookup_")]
        if not lookup_cols:
            continue

        curation_cols = [c for c in df.columns if c.startswith(f"{criterion_prefix}_curation_")]
        has_move_to = "Move_to" in df.columns

        dfs.append(df)

        for _, row in df.iterrows():
            # Build normalised lookup values for this row
            norm_vals: list[str] = []

            for col in lookup_cols:
                raw = row.get(col)
                if _is_empty(raw):
                    norm_vals.append("")
                else:
                    norm_vals.append(_norm(raw))
            if not norm_vals:
                continue

            # Primary key is always the FIRST _lookup_ col
            primary_key = norm_vals[0]
            if primary_key == "":
                continue

            # For OVERWRITE:
            # - If ALL lookup values non-empty -> use full tuple
            # - Else -> use only the first (primary) lookup value
            all_non_empty = all(v != "" for v in norm_vals)
            if len(norm_vals) == 1:
                overwrite_key: Any = primary_key
            elif all_non_empty:
                overwrite_key = tuple(norm_vals)
            else:
                overwrite_key = primary_key

            # Build curation maps using overwrite_key
            for cur_col in curation_cols:
                val = row.get(cur_col)
                if _is_empty(val):
                    continue

                name = _map_name_for(cur_col)  # e.g. "biomarker", "expression_type"
                if name not in value_maps:
                    value_maps[name] = {}

                value_maps[name][overwrite_key] = str(val).strip()

            # Move_to logic
            if has_move_to:
                mv = row.get("Move_to")
                if not _is_empty(mv):
                    move_to_map[primary_key] = str(mv).strip()

    prefix = f"{criterion_prefix}_lookup_"
    if dfs:
        relevant_fields: set[str] = _collect_lookup_fields(dfs, prefix)
    else:
        relevant_fields = set()

    result: Dict[str, Any] = dict(value_maps)
    result["move_to"] = move_to_map
    result["_relevant_fields"] = relevant_fields

    return result


# 3b. ATTRIBUTE OVERWRITE LOGIC - vlookup logic
def _overwrite_criterion(node_dict: Dict[str, Any], maps: Dict[str, Any], lookup_fields: List[str], overwrite_fields: List[str]) -> None:
    """
    Overwrite fields on a node based on criterion resources.

    Key-building rules (must match load_criterion_resources):
      - Primary key is always the FIRST lookup field.
      - If ALL lookup fields are non-empty => use full tuple as key.
      - Otherwise => use only the first (primary) lookup field as key.
    """
    if not lookup_fields:
        return

    # Build normalised lookup values from the node
    norm_vals: list[str] = []

    for field in lookup_fields:
        raw = node_dict.get(field)
        if _is_empty(raw):
            norm_vals.append("")
        else:
            norm_vals.append(_norm(raw))

    primary = norm_vals[0]
    if primary == "":
        for f in overwrite_fields:
            node_dict.pop(f, None)
        return

    if len(norm_vals) == 1:
        key: Any = primary
    else:
        all_non_empty = all(v != "" for v in norm_vals)
        if all_non_empty:
            key = tuple(norm_vals)
        else:
            key = primary

    any_match = False

    # Overwrite each target field using this key
    for field in overwrite_fields:
        lookup_map = maps.get(field, {})
        curated = lookup_map.get(key) if isinstance(lookup_map, dict) else None

        if curated and not _is_empty(curated):
            node_dict[field] = curated
            any_match = True
        else:
            node_dict.pop(field, None)

    if not any_match:
        for f in overwrite_fields:
            node_dict.pop(f, None)


def overwrite_gene_alteration_node(node: Any, ga_maps: Dict[str, Any]) -> None:
    if type(node).__name__ != "GeneAlterationCriterion":
        return

    node_dict = getattr(node, "__dict__", {})

    # 1. gene + type
    _overwrite_criterion(
        node_dict=node_dict,
        maps=ga_maps,
        lookup_fields=["gene"],
        overwrite_fields=["gene", "type"],
    )

    # 2. alteration
    _overwrite_criterion(
        node_dict=node_dict,
        maps=ga_maps,
        lookup_fields=["alteration"],
        overwrite_fields=["alteration"],
    )

    # 3. variant
    _overwrite_criterion(
        node_dict=node_dict,
        maps=ga_maps,
        lookup_fields=["variant"],
        overwrite_fields=["variant"],
    )

    allowed_fields = ga_maps.get("_relevant_fields", set())
    _drop_non_lookup_fields(node_dict, allowed_fields, extra_keep={"type"})


def overwrite_molecular_signature_node(node: Any, ms_maps: Dict[str, Any]) -> None:
    if type(node).__name__ != "MolecularSignatureCriterion":
        return

    node_dict = getattr(node, "__dict__", {})

    _overwrite_criterion(
        node_dict=node_dict,
        maps=ms_maps,
        lookup_fields=["signature"],
        overwrite_fields=["signature", "values"],
    )

    allowed_fields = ms_maps.get("_relevant_fields", set())
    _drop_non_lookup_fields(node_dict, allowed_fields, extra_keep={"values"})


def overwrite_molecular_biomarker_node(node: Any, mb_maps: Dict[str, Any]) -> None:
    if type(node).__name__ != "MolecularBiomarkerCriterion":
        return

    node_dict = getattr(node, "__dict__", {})

    _overwrite_criterion(
        node_dict=node_dict,
        maps=mb_maps,
        lookup_fields=["biomarker", "expression_type"],
        overwrite_fields=["biomarker", "expression_type"],
    )

    allowed_fields = mb_maps.get("_relevant_fields", set())
    _drop_non_lookup_fields(node_dict, allowed_fields)


def apply_overwrites_for_trial(rules: list[Any],
                               ga_maps: Dict[str, Any],
                               ms_maps: Dict[str, Any],
                               mb_maps: Dict[str, Any]) -> None:

    def _visit(rule: Any, node: Any, parent: Any, depth: int) -> None:
        overwrite_gene_alteration_node(node, ga_maps)
        overwrite_molecular_signature_node(node, ms_maps)
        overwrite_molecular_biomarker_node(node, mb_maps)

    walk_trial(rules=rules, visit_logic=_visit)


# 3c. ATTRIBUTE OVERWRITE LOGIC - move_to logic
def _build_key_from_parts(parts: List[str]) -> Optional[Any]:
    """
    Given a list of normalised lookup parts (possibly including empty strings),
    return:
      - None          if all parts are empty
      - a single str  if exactly one part is non-empty
      - a tuple[str]  if 2+ parts are non-empty
    """
    non_empty = [p for p in parts if p != ""]
    if not non_empty:
        return None

    if len(non_empty) == 1:
        return non_empty[0]
    return tuple(non_empty)


def _build_lookup_key(node_dict: dict, lookup_fields: list[str]) -> Optional[Any]:
    """
    Build the lookup key (str or tuple) from a node's attributes and a list of
    lookup_fields, using the same normalisation as for the resource maps.

    Rule:
        - Ignore empty fields.
        - If exactly one non-empty field → return that normalised string.
        - If 2+ non-empty fields      → return a tuple of those strings.
        - If all are empty            → return None.
    """
    raw_parts: list[str] = []

    for field in lookup_fields:
        raw = node_dict.get(field)
        raw_parts.append(_norm(raw))

    non_empty_parts = [k for k in raw_parts if k != ""]

    if not non_empty_parts:
        return None

    if len(non_empty_parts) == 1:
        return non_empty_parts[0]

    return tuple(non_empty_parts)


def _parse_move_to(move_to: str) -> Tuple[str, Optional[str]]:
    """
    Parse a Move_to string like 'GeneAlterationCriterion_1' into: ('GeneAlterationCriterion', '1')
    If it doesn't end in an underscore + digits, returns (move_to, None).
    """
    parts = move_to.rsplit("_", 1)

    if len(parts) == 2 and parts[1].isdigit():
        return parts[0], parts[1]

    return move_to, None


def _create_new_node_like(node: Any, target_type_name: str) -> Optional[Any]:
    """
    Create a new instance of `target_type_name`.

    Strategy:
      1. Try to get the class from the same module as `node`.
      2. Try to get it from `au_trial_universe.ctgov_llm_curation_loader`.
      3. As a last resort, create a simple shim class with that name.

    The shim is enough for:
      - storing attributes in __dict__
      - emitting correct source via obj_to_source()
    """
    target_cls = None

    # 1. Try the module where the current node class lives
    module_name = type(node).__module__
    mod = sys.modules.get(module_name)
    if mod is not None:
        target_cls = getattr(mod, target_type_name, None)

    # 2. Fallback: try the ctgov loader module explicitly
    if target_cls is None:
        try:
            import au_trial_universe.ctgov_llm_curation_loader as loader_mod  # type: ignore[import]
            target_cls = getattr(loader_mod, target_type_name, None)
        except Exception:
            target_cls = None

    # 3. Final fallback: create a minimal shim class with the correct name
    if target_cls is None:
        logger.warning(
            "Cannot find class '%s' in module '%s' or loader; creating simple shim class for serialization.",
            target_type_name, module_name)
        target_cls = type(target_type_name, (), {})  # simple empty class

    try:
        return target_cls()
    except Exception as exc:
        logger.error("Failed to instantiate '%s': %s", target_type_name, exc)
        return None


def _replace_node_in_parent(rule: Any, parent: Optional[Any], old_node: Any, new_node: Any) -> None:
    """
    Replace old_node with new_node within:

        - parent.criteria list/tuple, or
        - parent.criterion / parent.condition, or
        - rule.curation (for root nodes when parent is None).
    """
    if parent is None:
        cur = getattr(rule, "curation", None)

        if isinstance(cur, list):
            for i, child in enumerate(cur):
                if child is old_node:
                    cur[i] = new_node
                    return

        elif isinstance(cur, tuple):
            cur_list = list(cur)

            for i, child in enumerate(cur_list):
                if child is old_node:
                    cur_list[i] = new_node
                    setattr(rule, "curation", cur_list)
                    return

        else:
            if cur is old_node:
                setattr(rule, "curation", new_node)
        return

    # Non-root: inside parent
    crit_list = getattr(parent, "criteria", None)

    if isinstance(crit_list, list):
        for i, child in enumerate(crit_list):
            if child is old_node:
                crit_list[i] = new_node
                return

    elif isinstance(crit_list, tuple):
        crit_list_list = list(crit_list)
        for i, child in enumerate(crit_list_list):
            if child is old_node:
                crit_list_list[i] = new_node
                setattr(parent, "criteria", crit_list_list)
                return

    for attr_name in ("criterion", "condition"):
        if getattr(parent, attr_name, None) is old_node:
            setattr(parent, attr_name, new_node)
            return


def _apply_move_to_for_node(rule: Any, node: Any, parent: Optional[Any],
                            resources_by_prefix: Dict[str, Dict[str, Any]]) -> None:
    cls_name = type(node).__name__
    source_lookup_fields = MOVE_TO_CRITERION.get(cls_name)
    if not source_lookup_fields:
        return

    source_maps = resources_by_prefix.get(cls_name)
    if not source_maps:
        return

    node_dict = getattr(node, "__dict__", {})

    # For Move_to, ALWAYS key by the FIRST lookup field only
    primary_field = source_lookup_fields[0]
    raw_primary = node_dict.get(primary_field)
    if _is_empty(raw_primary):
        return

    primary_key = _norm(raw_primary)

    move_map = source_maps.get("move_to", {})
    move_to = move_map.get(primary_key)
    if not move_to:
        return

    full_key = _build_lookup_key(node_dict, source_lookup_fields)
    lookup_snapshot = {field: node_dict.get(field) for field in source_lookup_fields}
    logger.info(
        "\n\tMove_to:\n\t%s → %s | key=%r | from %s",
        cls_name, move_to, full_key, lookup_snapshot
    )

    target_prefix, _suffix = _parse_move_to(move_to)

    target_maps = resources_by_prefix.get(target_prefix)
    if not target_maps:
        logger.warning(
            "Move_to '%s' refers to unknown target criterion '%s'. Skipping.",
            move_to, target_prefix
        )
        return

    target_key = primary_key

    new_attrs: Dict[str, Any] = {}
    for field_name, field_map in target_maps.items():
        if field_name in ("move_to", "_relevant_fields"):
            continue
        if not isinstance(field_map, dict):
            continue

        curated = field_map.get(target_key)
        if curated and not _is_empty(curated):
            new_attrs[field_name] = curated

    if not new_attrs:
        logger.warning(
            "Move_to '%s' had no curated output for source key=%r (target_key=%r).",
            move_to, full_key, target_key
        )
        return

    # Create the new node of the *target* criterion type
    new_node = _create_new_node_like(node, target_prefix)
    if new_node is None:
        return

    new_node_dict = getattr(new_node, "__dict__", {})
    new_node_dict.update(new_attrs)

    # Replace original node with the new one
    _replace_node_in_parent(rule, parent, node, new_node)


def apply_move_to_for_trial(rules: List[Any], resources_by_prefix: Dict[str, Dict[str, Any]]) -> None:

    def _visit(rule: Any, node: Any, parent: Any, depth: int) -> None:
        _apply_move_to_for_node(rule, node, parent, resources_by_prefix)

    walk_trial(rules=rules, visit_logic=_visit)


# 4. NEW FILE CREATION (Keep everything in memory, then write once)
def obj_to_source(obj: Any, indent: int = 0, inline: bool = False) -> str:
    """
    Convert Python object → source code string.
    If inline=True, the returned string has NO leading spaces.
    """
    # 1. Primitives
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return repr(obj)

    # 2. List / tuple
    if isinstance(obj, (list, tuple)):
        if not obj:
            return "[]"

        inner_indent = indent + 4
        inner_ind = " " * inner_indent

        lines = ["["]
        for item in obj:
            item_src = obj_to_source(item, inner_indent)
            lines.append(f"{inner_ind}{item_src},")

        lines.append(" " * indent + "]")

        return ("\n".join(lines)) if not inline else "[" + ", ".join(
            obj_to_source(item, 0, inline=True) for item in obj) + "]"

    # 3. Dict
    if isinstance(obj, dict):
        if not obj:
            return "{}"

        inner_indent = indent + 4
        inner_ind = " " * inner_indent

        lines = ["{"]
        for k, v in obj.items():
            v_src = obj_to_source(v, inner_indent)
            lines.append(f"{inner_ind}{repr(k)}: {v_src},")

        lines.append(" " * indent + "}")

        return "\n".join(lines)

    # 4. Generic object (Rule, Criterion, Treatment, IntRange, ...)
    cls_name = type(obj).__name__
    attrs = getattr(obj, "__dict__", {})

    lines = [f"{cls_name}("]
    inner_indent = indent + 4
    inner_ind = " " * inner_indent

    for key, val in attrs.items():
        val_src = obj_to_source(val, inner_indent)

        if "\n" not in val_src:
            lines.append(f"{inner_ind}{key}={val_src},")
        else:
            first_line, *rest = val_src.split("\n")
            lines.append(f"{inner_ind}{key}={first_line}")

            for ln in rest:
                lines.append(ln)

            lines[-1] += ","

    lines.append(" " * indent + ")")

    return "\n".join(lines)


def write_rules_py(rules: List[Any], output_path: Path) -> None:
    lines: List[str] = ["rules = ["]

    for rule in rules:
        rule_src = obj_to_source(rule, indent=4)
        lines.append(rule_src + ",")

    lines.append("]")
    lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Filter Rules by searching criteria; apply overwrite & move_to logic")
    parser.add_argument("--curated_dir", type=Path, required=True, help="Directory to read curated NCT*.py files from.")
    parser.add_argument("--resource_dir", type=Path, required=True, help="Directory to resource files for overwriting.")
    parser.add_argument("--overwrite_dir", type=Path, required=True, help="Directory to write *_curated.py files to.")
    parser.add_argument("--log_level", default="INFO",
                        help="Logging level (DEBUG, INFO, WARNING, ERROR). Default: INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    # Load overwrite resource files
    ga_maps = load_criterion_resources(args.resource_dir, "GeneAlterationCriterion")
    ms_maps = load_criterion_resources(args.resource_dir, "MolecularSignatureCriterion")
    mb_maps = load_criterion_resources(args.resource_dir, "MolecularBiomarkerCriterion")

    resources_by_prefix: Dict[str, Dict[str, Any]] = {
        "GeneAlterationCriterion": ga_maps,
        "MolecularSignatureCriterion": ms_maps,
        "MolecularBiomarkerCriterion": mb_maps,
    }

    def _process_file(_input_path: Path, _output_dir: Path) -> None:
        logger.info("Processing %s", _input_path)

        rules = load_curated_rules(_input_path)
        if rules is None:
            logger.error("Failed to load curated rules from %s. Trial skipped.", _input_path)
            return

        rules = list(rules)

        # Rule-level filter: only keep rules that have at least one searching criterion
        filtered_rules = filter_rules_by_search_criteria(rules)
        logger.info("Kept %d/%d rules after filtering", len(filtered_rules), len(rules))

        # Apply Move_to from resource files (before attribute overwrites)
        apply_move_to_for_trial(filtered_rules, resources_by_prefix)

        # Apply overwrites from resource files
        apply_overwrites_for_trial(filtered_rules, ga_maps, ms_maps, mb_maps)

        # Nested criterion selection: prune sibling subtrees without any searching criterion
        prune_nontarget_criteria_in_rules(filtered_rules)

        # Remove unused attributes and fields
        remove_descriptions_from_rules(filtered_rules)
        remove_exclude_and_flipped_from_rules(filtered_rules)

        # Write out new curated file
        _output_dir.mkdir(parents=True, exist_ok=True)

        if len(filtered_rules) == 0:
            output_path = _output_dir / f"{_input_path.stem}_discarded.py"
            write_rules_py(filtered_rules, output_path)
            logger.info("Wrote DISCARDED trial to %s", output_path)
        else:
            output_path = _output_dir / f"{_input_path.stem}_overwrote.py"
            write_rules_py(filtered_rules, output_path)
            logger.info("Wrote OVERWRITTEN trial to %s", output_path)

    for input_path in sorted(args.curated_dir.glob("NCT*.py")):
        _process_file(input_path, args.overwrite_dir)


if __name__ == "__main__":
    main()
