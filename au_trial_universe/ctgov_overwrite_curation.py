import argparse
import logging
from pathlib import Path
from typing import Any, List, Dict, Optional, Callable, Iterable, Mapping
import pandas as pd

from au_trial_universe.ctgov_llm_curation_loader import load_curated_rules

logger = logging.getLogger(__name__)

SEARCHING_CRITERIA = [
    "PrimaryTumorCriterion",
    "MolecularBiomarkerCriterion",
    "MolecularSignatureCriterion",
    "GeneAlterationCriterion",
]


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


def walk_node(node: Any, visit_logic: Callable[[Any, Optional[Any], int], None], parent: Optional[Any] = None, depth: int = 0) -> None:
    """
    DFS starting from a single root node from a tree

    node = current node (root at the first call)
    visit_logic = a Callback function
    parent = a parent node (None for the root node)
    depth = Depth in the tree (where root = 0).
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


def walk_trial(rules: Iterable[Any], visit_logic: Callable[[Any, Any, Optional[Any], int], None]) -> None:
    """
    Traverse the trial file (a super-forest) which all the rule objects
    """
    for rule in rules:
        rule_text = getattr(rule, "rule_text", None)
        if rule_text is None:
            logger.error("Missing rule text")
            raise ValueError("Missing rule text on a Rule object")

        forest = getattr(rule, "curation", None) or []

        def _node_visitor(node: Any, parent: Any, depth: int, _rule=rule) -> None:
            visit_logic(_rule, node, parent, depth)

        walk_forest(forest, _node_visitor)


# 2a. CRITERION SEARCHING LOGIC - rule selection

def rule_has_search_criterion(rule: Any) -> bool:
    found = False
    forest = getattr(rule, "curation", None) or []

    def visit(node: Any, parent: Any, depth: int) -> None:
        nonlocal found

        if found:  # Early exit: already found one, no need to keep walking
            return

        cls_name = type(node).__name__
        if cls_name in SEARCHING_CRITERIA:
            found = True

    walk_forest(forest, visit)
    return found


def filter_rules_by_search_criteria(rules: List[Any]) -> List[Any]:
    """
    Keep only Rule() objects that contain at least one target criterion
    """
    kept_rules: List[Any] = []

    for rule in rules:
        if rule_has_search_criterion(rule):
            kept_rules.append(rule)

    return kept_rules


# 2b. CRITERION SEARCHING LOGIC - prune subtrees that do not contain any target criteria
def _prune_node_inplace(node: Any) -> bool:
    """
    Recursively prune a node's children so that only subtrees containing at least one SEARCHING_CRITERIA class remain.
    """
    cls_name = type(node).__name__
    has_target = cls_name in SEARCHING_CRITERIA

    crit_list = getattr(node, "criteria", None)
    if isinstance(crit_list, (list, tuple)):
        new_children: List[Any] = []
        for child in crit_list:
            if _prune_node_inplace(child):
                new_children.append(child)

        # Replace with pruned list
        setattr(node, "criteria", new_children)
        if new_children:
            # If any child subtree has a target, this node's subtree has a target
            has_target = True or has_target

    for attr_name in ("criterion", "condition"):
        child = getattr(node, attr_name, None)
        if child is not None:
            keep_child = _prune_node_inplace(child)
            if keep_child:
                has_target = True or has_target
            else:
                # Drop the child link entirely
                setattr(node, attr_name, None)

    return has_target


def prune_nontarget_criteria_in_rule(rule: Any) -> None:
    """
    For a single Rule(), prune its Forest (rule.curation) so that only trees whose subtrees contain at least one target criterion remain.
    """
    forest = getattr(rule, "curation", None) or []
    new_forest: List[Any] = []

    for root in forest:
        if _prune_node_inplace(root):
            new_forest.append(root)

    # Replace the forest with only the pruned roots
    setattr(rule, "curation", new_forest)


def prune_nontarget_criteria_in_rules(rules: List[Any]) -> None:
    for rule in rules:
        prune_nontarget_criteria_in_rule(rule)


# 2c. CRITERION SEARCHING LOGIC - "description" attribute removal

def remove_description_from_nodes(node: Any) -> None:
    node_dict = getattr(node, "__dict__", None)

    if isinstance(node_dict, dict):
        node_dict.pop("description", None)


def remove_descriptions_from_rules(rules: List[Any]) -> None:
    def visit(rule: Any, node: Any, parent: Any, depth: int) -> None:
        remove_description_from_nodes(node)

    walk_trial(rules, visit_logic=visit)


def remove_exclude_and_flipped_from_rules(rules: List[Any]) -> None:
    for rule in rules:
        rule_dict = getattr(rule, "__dict__", None)
        if isinstance(rule_dict, dict):
            rule_dict.pop("exclude", None)
            rule_dict.pop("flipped", None)


# 3. ATTRIBUTE OVERWRITE LOGIC - part a: load resource files

def _norm_key(value: str) -> str:
    return value.strip().lower()


def load_gene_alteration_resources(resource_dir: Path) -> Dict[str, Dict[str, str]]:
    """
    Load the 3 GeneAlterationCurationResource_*.csv files into lookup maps.

    Returns a dict with keys:
        - "gene"          : lookup_gene  -> curation_gene
        - "gene_type"     : lookup_gene  -> curation_type
        - "alteration"    : lookup_alt   -> curation_alteration
        - "variant"       : lookup_var   -> curation_variant
        - "_allowed_fields": set of attribute names that appear in ANY _lookup_ column
    """
    # 1. Gene: vlookup both curation_gene and curation_type
    gene_path = resource_dir / "GeneAlterationCurationResource_1.csv"
    df_gene = pd.read_csv(gene_path)

    gene_map: Dict[str, str] = {}
    gene_type_map: Dict[str, str] = {}

    for _, row in df_gene.iterrows():
        lookup = row.get("GeneAlterationCriterion_lookup_gene")
        curated_gene = row.get("GeneAlterationCriterion_curation_gene")
        curated_type = row.get("GeneAlterationCriterion_curation_type")

        if pd.isna(lookup) or pd.isna(curated_gene):
            continue

        key = _norm_key(str(lookup))
        gene_map[key] = str(curated_gene)

        if not pd.isna(curated_type):
            gene_type_map[key] = str(curated_type).strip()

    # 2. Alteration
    alt_path = resource_dir / "GeneAlterationCurationResource_2.csv"
    df_alt = pd.read_csv(alt_path)

    alt_map: Dict[str, str] = {}
    for _, row in df_alt.iterrows():
        lookup = row.get("GeneAlterationCriterion_lookup_alteration")
        curated = row.get("GeneAlterationCriterion_curation_alteration")

        if pd.isna(lookup) or pd.isna(curated):
            continue

        key = _norm_key(str(lookup))
        alt_map[key] = str(curated).strip()

    # 3. Variant
    var_path = resource_dir / "GeneAlterationCurationResource_3.csv"
    df_var = pd.read_csv(var_path)

    var_map: Dict[str, str] = {}
    for _, row in df_var.iterrows():
        lookup = row.get("GeneAlterationCriterion_lookup_variant")
        curated = row.get("GeneAlterationCriterion_curation_variant")

        if pd.isna(lookup) or pd.isna(curated):
            continue

        key = _norm_key(str(lookup))
        var_map[key] = str(curated).strip()

    # 4. Derive allowed attribute names from ANY `_lookup_` column
    allowed_fields: set[str] = set()
    prefix = "GeneAlterationCriterion_lookup_"
    for df in (df_gene, df_alt, df_var):
        for col in df.columns:
            if col.startswith(prefix):
                field_name = col[len(prefix) :]
                allowed_fields.add(field_name)

    return {
        "gene": gene_map,
        "gene_type": gene_type_map,
        "alteration": alt_map,
        "variant": var_map,
        "_allowed_fields": allowed_fields,
    }


def _overwrite_single_field(node_dict: dict, field_name: str, lookup_map: Mapping[str, str]) -> None:
    """
    For a given field (gene / alteration / variant):

        - If node doesn't have it -> do nothing.
        - If there is a curated value in lookup_map -> overwrite.
        - If not found -> drop the field from the node.
    """
    if field_name not in node_dict:
        return

    raw_val = node_dict[field_name]
    if raw_val is None:
        node_dict.pop(field_name, None)
        return

    key = _norm_key(str(raw_val))
    curated = lookup_map.get(key)

    if not curated:
        # No curated value => drop the field
        node_dict.pop(field_name, None)
    else:
        node_dict[field_name] = curated


def _overwrite_gene_and_type(node_dict: dict, ga_maps: Dict[str, Dict[str, str]]) -> None:
    """
    Handle the special case for gene:

        - If `gene` is present and has a lookup match:
            * set node_dict["gene"] = curation_gene
            * set node_dict["type"] = curation_type (new attribute), if available
        - If no curated gene is found:
            * drop `gene`
            * drop any existing `type` (since it's now untrustworthy / unused)
    """
    if "gene" not in node_dict:
        return

    raw_val = node_dict["gene"]
    if raw_val is None:
        node_dict.pop("gene", None)
        node_dict.pop("type", None)
        return

    key = _norm_key(str(raw_val))
    gene_map = ga_maps.get("gene", {})
    gene_type_map = ga_maps.get("gene_type", {})

    curated_gene = gene_map.get(key)

    if not curated_gene:
        # No curated value => drop gene (and any existing type)
        node_dict.pop("gene", None)
        node_dict.pop("type", None)
        return

    # Overwrite gene with curated symbol/string
    node_dict["gene"] = curated_gene

    # If we have a curated type, create/update the new attribute
    curated_type = gene_type_map.get(key)
    if curated_type:
        node_dict["type"] = curated_type
    else:
        # No curated type for this entry; make sure we don't leave a stale one
        node_dict.pop("type", None)


def _drop_non_lookup_fields(node_dict: dict, ga_maps: Dict[str, Dict[str, str]]) -> None:
    """
    Remove any attributes on a GeneAlterationCriterion that do NOT correspond
    to known lookup-based fields.

    Rule:
        - Start from all field names that appear in ANY `_lookup_` column.
        - Also always keep the curated `type` field.
        - Drop everything else (e.g. detection_method, method, inside_andcriterion, etc.).
    """
    allowed_from_resources = set(ga_maps.get("_allowed_fields", set()))
    # We also want to keep the curated type field, even though it doesn't come from a _lookup_ column.
    allowed = allowed_from_resources | {"type"}

    for key in list(node_dict.keys()):
        if key not in allowed:
            node_dict.pop(key, None)


def overwrite_gene_alteration_node(node: Any, ga_maps: Dict[str, Dict[str, str]]) -> None:
    """
    Mutate a GeneAlterationCriterion node in-place using the gene/alteration/variant maps.

    Behaviour:
        - For `gene`:
            * If lookup match -> overwrite `gene` and set new `type` attribute.
            * If no match      -> drop `gene` and any existing `type`.
        - For `alteration` and `variant`:
            * If curated value exists -> overwrite.
            * If no curated value     -> drop that attribute from the node.
        - After that, drop any other attributes not backed by a lookup column
          (e.g. detection_method).
    """
    if type(node).__name__ != "GeneAlterationCriterion":
        return

    node_dict = getattr(node, "__dict__", {})

    # 1. Handle gene + new type attribute
    _overwrite_gene_and_type(node_dict, ga_maps)

    # 2. Overwrite fields using lookup tables
    _overwrite_single_field(node_dict, "alteration", ga_maps["alteration"])
    _overwrite_single_field(node_dict, "variant", ga_maps["variant"])

    # 3. Drop anything that isn't part of the lookup-driven schema
    _drop_non_lookup_fields(node_dict, ga_maps)


def apply_overwrites_for_trial(rules: list[Any], ga_maps: Dict[str, Dict[str, str]]) -> None:
    """
    Traverse the Super-forest (all Rule objects for a trial) and
    apply in-place overwrites specific to GeneAlterationCriterion.
    """

    def visit(rule: Any, node: Any, parent: Any, depth: int) -> None:
        overwrite_gene_alteration_node(node, ga_maps)
        # Later: plug in more overwrite types here

    walk_trial(rules, visit_logic=visit)


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
            obj_to_source(item, 0, inline=True) for item in obj
        ) + "]"

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
    parser = argparse.ArgumentParser(description=("Retain only Rules that contain at least one of the target criteria and write them to *_curated.py files."))
    parser.add_argument("--curated_dir", type=Path, required=True, help="Directory to read curated NCT*.py files from.")
    parser.add_argument("--resource_dir", type=Path, required=True, help="Directory to resource files for overwriting.")
    parser.add_argument("--overwrite_dir", type=Path, required=True, help="Directory to write *_curated.py files to.")
    parser.add_argument("--log_level", default="INFO", help="Logging level (DEBUG, INFO, WARNING, ERROR). Default: INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    # Load GA resources ONCE
    ga_maps = load_gene_alteration_resources(args.resource_dir)

    def _process_file(_input_path: Path, _output_dir: Path) -> None:
        logger.info("Processing %s", _input_path)

        rules = load_curated_rules(_input_path)
        if rules is None:
            logger.error("Failed to load curated rules from %s; skipping", _input_path)
            return

        rules = list(rules)

        # 2a. Rule-level filter: only keep rules that mention a target criterion anywhere
        filtered = filter_rules_by_search_criteria(rules)
        logger.info("  Kept %d/%d rules after filtering", len(filtered), len(rules))

        # 2b. Nested criterion selection: prune sibling subtrees without any target
        prune_nontarget_criteria_in_rules(filtered)

        # 3. Apply GeneAlteration overwrites
        apply_overwrites_for_trial(filtered, ga_maps)

        # 2c. Remove descriptions from all nodes + remove exclude/flipped from rules
        remove_descriptions_from_rules(filtered)
        remove_exclude_and_flipped_from_rules(filtered)

        # 4. Write out new curated file
        _output_dir.mkdir(parents=True, exist_ok=True)
        output_path = _output_dir / f"{_input_path.stem}_curated.py"
        write_rules_py(filtered, output_path)
        logger.info("  Wrote %s", output_path)

    for input_path in sorted(args.curated_dir.glob("NCT*.py")):
        _process_file(input_path, args.overwrite_dir)


if __name__ == "__main__":
    main()
