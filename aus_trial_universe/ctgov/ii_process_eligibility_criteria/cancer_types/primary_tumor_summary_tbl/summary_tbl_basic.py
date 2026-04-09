from __future__ import annotations

import argparse
import csv
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, List, Optional, Sequence

from aus_trial_universe.ctgov.i_download_trials_and_extract_eligibility.utils.load_curated_rules import (
    load_curated_rules,
)
from aus_trial_universe.ctgov.ii_process_eligibility_criteria.cancer_types import (
    PrimaryTumorMap,
    build_primary_tumor_map,
    get_primary_tumor_key_from_node,
)
from aus_trial_universe.ctgov.utils.general import (
    clean_cell_str,
    is_effectively_empty,
)
from aus_trial_universe.ctgov.utils.general.traverse_curation_tree import (
    normalise_forest_into_list,
)

logger = logging.getLogger(__name__)

# `iter_children(...)` from traverse_curation_tree intentionally covers only
# `criteria`, `criterion`, and `condition`. For this reporting task we also want
# to inspect `then` and `else_` branches because PrimaryTumorCriterion can appear
# there as well.
CHILD_ATTRS: Sequence[str] = (
    "criteria",
    "criterion",
    "condition",
    "then",
    "else_",
)

CSV_COLUMNS: Sequence[str] = (
    "nct_id",
    "primary_tumor_type",
    "primary_tumor_location",
    "Oncotree_curation",
    "rule_text",
    "inclusive_rule",
    "ancestor_chain",
    "siblings_summary",
    "descendent_chain",
)

NCT_ID_PATTERNS: Sequence[re.Pattern[str]] = (
    re.compile(r"\bNctId\s*=\s*['\"](?P<value>NCT\d+)['\"]"),
    re.compile(r"\bnct_id\s*=\s*['\"](?P<value>NCT\d+)['\"]", re.IGNORECASE),
    re.compile(r"\bNCT_ID\s*=\s*['\"](?P<value>NCT\d+)['\"]"),
)


@dataclass(frozen=True)
class PrimaryTumorOccurrenceRow:
    nct_id: str
    primary_tumor_type: str
    primary_tumor_location: str
    Oncotree_curation: str
    rule_text: str
    inclusive_rule: bool
    ancestor_chain: str
    siblings_summary: str
    descendent_chain: str


# ---------------------------
# Generic helpers
# ---------------------------

def _iter_input_py_files(curated_dir: Path) -> Iterable[Path]:
    if curated_dir.is_file():
        yield curated_dir
        return
    yield from sorted(p for p in curated_dir.glob("NCT*.py") if p.is_file())


def _pick_most_recent(paths: List[Path]) -> Path:
    if not paths:
        raise ValueError("Internal error: _pick_most_recent called with empty list")
    return max(paths, key=lambda p: p.stat().st_mtime)


def _find_mapping_resource(resources_dir: Path, token: str) -> Path:
    allowed_suffixes = {".csv", ".xlsx", ".xls"}
    matches = [
        p
        for p in resources_dir.iterdir()
        if p.is_file()
           and not p.name.startswith("~$")
           and p.suffix.lower() in allowed_suffixes
           and token.lower() in p.stem.lower()
    ]
    if not matches:
        raise FileNotFoundError(
            f"Could not find mapping resource containing '{token}' in {resources_dir}"
        )
    return _pick_most_recent(matches)


# ---------------------------
# Normalisation helpers
# ---------------------------

def _blank_if_empty(value: Any) -> str:
    if is_effectively_empty(value):
        return ""
    return clean_cell_str(value)


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, str) and is_effectively_empty(value):
        return ""
    return value


def _strip_criterion_suffix(type_name: str) -> str:
    return type_name.removesuffix("Criterion")


# ---------------------------
# NCT ID helpers
# ---------------------------

def get_nct_id(py_path: Path) -> str:
    """
    Resolve NCT ID without importing the curated .py file directly.

    Curated rule files are usually not standalone Python modules; they rely on
    names like Rule / AndCriterion / PrimaryTumorCriterion being injected by the
    curated-rules loader. Direct import therefore fails with NameError.
    """
    try:
        text = py_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = py_path.read_text(encoding="utf-8", errors="ignore")

    for pattern in NCT_ID_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group("value")

    stem = py_path.stem.strip()
    if re.fullmatch(r"NCT\d+", stem, flags=re.IGNORECASE):
        return stem.upper()
    return stem


# ---------------------------
# Tree inspection helpers
# ---------------------------

def _is_criterion_node(obj: Any) -> bool:
    return obj is not None and type(obj).__name__.endswith("Criterion")


def _iter_nodes_from_attr_value(value: Any) -> Iterator[Any]:
    if _is_criterion_node(value):
        yield value
        return

    if isinstance(value, (list, tuple)):
        for item in value:
            if _is_criterion_node(item):
                yield item


def get_immediate_children(node: Any) -> List[Any]:
    children: List[Any] = []
    for attr in CHILD_ATTRS:
        children.extend(_iter_nodes_from_attr_value(getattr(node, attr, None)))
    return children


def summarise_node(node: Any) -> str:
    node_type = _strip_criterion_suffix(type(node).__name__)
    children = get_immediate_children(node)
    if not children:
        return node_type
    child_summaries = ", ".join(summarise_node(child) for child in children)
    return f"{node_type}[{child_summaries}]"


def build_ancestor_chain(ancestors: Sequence[Any]) -> str:
    if not ancestors:
        return ""
    return " -> ".join(
        _strip_criterion_suffix(type(node).__name__) for node in ancestors
    )


def build_siblings_summary(node: Any, parent: Optional[Any]) -> str:
    if parent is None:
        return ""

    siblings = [child for child in get_immediate_children(parent) if child is not node]
    if not siblings:
        return ""
    return " | ".join(summarise_node(sibling) for sibling in siblings)


# ---------------------------
# OncoTree helpers
# ---------------------------

def resolve_oncotree_curation(node: Any, mapping: PrimaryTumorMap) -> str:
    key = get_primary_tumor_key_from_node(node)
    if key is None:
        return ""
    value = mapping.get(key)
    return _blank_if_empty(value)


# ---------------------------
# Core extraction
# ---------------------------

def make_row(
        *,
        nct_id: str,
        rule: Any,
        node: Any,
        ancestors: Sequence[Any],
        parent: Optional[Any],
        pt_map: PrimaryTumorMap,
) -> PrimaryTumorOccurrenceRow:
    return PrimaryTumorOccurrenceRow(
        nct_id=_blank_if_empty(nct_id),
        primary_tumor_type=_blank_if_empty(getattr(node, "primary_tumor_type", None)),
        primary_tumor_location=_blank_if_empty(
            getattr(node, "primary_tumor_location", None)
        ),
        Oncotree_curation=resolve_oncotree_curation(node, pt_map),
        rule_text=_blank_if_empty(getattr(rule, "rule_text", None)),
        inclusive_rule=not bool(getattr(rule, "exclude", False)),
        ancestor_chain=build_ancestor_chain(ancestors),
        siblings_summary=build_siblings_summary(node, parent),
        descendent_chain="",
    )


def walk_rule_tree(
        *,
        nct_id: str,
        rule: Any,
        node: Any,
        rows: List[PrimaryTumorOccurrenceRow],
        ancestors: Sequence[Any],
        parent: Optional[Any],
        pt_map: PrimaryTumorMap,
) -> None:
    if type(node).__name__ == "PrimaryTumorCriterion":
        rows.append(
            make_row(
                nct_id=nct_id,
                rule=rule,
                node=node,
                ancestors=ancestors,
                parent=parent,
                pt_map=pt_map,
            )
        )

    next_ancestors = [*ancestors, node]
    for child in get_immediate_children(node):
        walk_rule_tree(
            nct_id=nct_id,
            rule=rule,
            node=child,
            rows=rows,
            ancestors=next_ancestors,
            parent=node,
            pt_map=pt_map,
        )


def extract_rows_from_rules(
        *,
        nct_id: str,
        rules: Iterable[Any],
        pt_map: PrimaryTumorMap,
) -> List[PrimaryTumorOccurrenceRow]:
    rows: List[PrimaryTumorOccurrenceRow] = []

    for rule in rules:
        forest = normalise_forest_into_list(rule)
        for root in forest:
            if not _is_criterion_node(root):
                continue
            walk_rule_tree(
                nct_id=nct_id,
                rule=rule,
                node=root,
                rows=rows,
                ancestors=[],
                parent=None,
                pt_map=pt_map,
            )

    return rows


def extract_rows_from_trial_file(
        py_path: Path,
        *,
        pt_map: PrimaryTumorMap,
) -> List[PrimaryTumorOccurrenceRow]:
    nct_id = get_nct_id(py_path)
    rules = load_curated_rules(py_path)
    if not rules:
        logger.warning("No rules loaded from %s", py_path)
        return []
    return extract_rows_from_rules(nct_id=nct_id, rules=rules, pt_map=pt_map)


def write_rows_to_csv(
        rows: Sequence[PrimaryTumorOccurrenceRow],
        output_csv: Path,
) -> None:
    output_csv.parent.mkdir(parents=True, exist_ok=True)

    with output_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        for row in rows:
            raw = asdict(row)
            writer.writerow({key: _csv_value(raw[key]) for key in CSV_COLUMNS})


# ---------------------------
# CLI
# ---------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Extract one CSV row per PrimaryTumorCriterion occurrence from curated "
            "NCT*.py files."
        )
    )
    parser.add_argument(
        "--curated_dir",
        required=True,
        type=Path,
        help="Directory of curated NCT*.py files or a single file",
    )
    parser.add_argument(
        "--output_csv",
        required=True,
        type=Path,
        help="Path to write the output CSV",
    )
    parser.add_argument(
        "--resources_dir",
        required=True,
        type=Path,
        help="Directory containing mapping resources (.csv/.xlsx/.xls)",
    )
    parser.add_argument(
        "--fail_on_error",
        action="store_true",
        help="Raise immediately on the first file that fails to load or parse",
    )
    parser.add_argument(
        "--log_level",
        default="INFO",
        help="Logging level (INFO/DEBUG/...)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    pt_mapping_path = _find_mapping_resource(
        args.resources_dir,
        "PrimaryTumourCurationResource",
    )
    logger.info("Using mapping resource for primary_tumour: %s", pt_mapping_path)

    pt_map = build_primary_tumor_map(pt_mapping_path)
    pt_map.pop(("", ""), None)

    rows: List[PrimaryTumorOccurrenceRow] = []
    n_files = 0

    for py_path in _iter_input_py_files(args.curated_dir):
        n_files += 1
        try:
            rows.extend(extract_rows_from_trial_file(py_path, pt_map=pt_map))
        except Exception as exc:
            if args.fail_on_error:
                raise
            logger.exception("Skipping %s due to error: %s", py_path, exc)

    write_rows_to_csv(rows, args.output_csv)

    logger.info(
        "Done. Seen %d file(s); wrote %d row(s) to %s.",
        n_files,
        len(rows),
        args.output_csv,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
