from __future__ import annotations

import argparse
import csv
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

import pandas as pd

from aus_trial_universe.ctgov.i_download_trials_and_extract_eligibility.utils.load_curated_rules import (
    load_curated_rules,
)
from aus_trial_universe.ctgov.ii_process_eligibility_criteria.cancer_types import (
    PrimaryTumorMap,
    build_primary_tumor_map,
    get_primary_tumor_key_from_node,
)
from aus_trial_universe.ctgov.utils.general.text_normalisation import (
    clean_cell_str,
    is_effectively_empty,
)
from aus_trial_universe.ctgov.utils.general.traverse_curation_tree import (
    normalise_forest_into_list,
)

logger = logging.getLogger(__name__)


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
    "primary_tumor_oncotree_curation",
    "conditions_original",
    "conditions_oncotree_curation",
    "rule_text",
    "inclusive_rule",
    "ancestor_chain",
    "siblings_summary",
)

NCT_ID_PATTERNS: Sequence[re.Pattern[str]] = (
    re.compile(r"\bNctId\s*=\s*['\"](?P<value>NCT\d+)['\"]"),
    re.compile(r"\bnct_id\s*=\s*['\"](?P<value>NCT\d+)['\"]", re.IGNORECASE),
    re.compile(r"\bNCT_ID\s*=\s*['\"](?P<value>NCT\d+)['\"]"),
)

NULL_SENTINELS = {"", "NOT([None])"}


@dataclass(frozen=True)
class PrimaryTumorOccurrenceRow:
    nct_id: str
    primary_tumor_type: str
    primary_tumor_location: str
    primary_tumor_oncotree_curation: str
    conditions_original: str
    conditions_oncotree_curation: str
    rule_text: str
    inclusive_rule: bool
    ancestor_chain: str
    siblings_summary: str


# ---------------------------
# Generic helpers
# ---------------------------

def _iter_input_py_files(curated_dir: Path) -> Iterable[Path]:
    if curated_dir.is_file():
        yield curated_dir
        return
    yield from sorted(p for p in curated_dir.glob("NCT*.py") if p.is_file())


def _pick_most_recent(paths: Sequence[Path]) -> Path:
    if not paths:
        raise ValueError("Internal error: _pick_most_recent called with empty paths")
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


def _normalize_string(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _blank_if_empty(value: Any) -> str:
    if value is None or is_effectively_empty(value):
        return ""
    s = clean_cell_str(value)
    return "" if s in NULL_SENTINELS else s


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, str) and is_effectively_empty(value):
        return ""
    return value


def _find_column_case_insensitive(columns: Sequence[str], target: str) -> str:
    target_norm = target.strip().lower()
    for col in columns:
        if str(col).strip().lower() == target_norm:
            return str(col)
    raise ValueError(f"Could not find column '{target}' in columns: {list(columns)}")


def _strip_criterion_suffix(type_name: str) -> str:
    return type_name.removesuffix("Criterion")


def _node_type_name(node: Any) -> str:
    return _strip_criterion_suffix(type(node).__name__)


# ---------------------------
# Trial-level conditions lookup
# ---------------------------

def load_trial_conditions_lookup(
    conditions_csv: Path,
) -> Dict[str, Dict[str, str]]:
    """
    Read a precomputed trial-level conditions CSV.

    Required columns:
      - trial_id
      - conditions_original
      - conditions_oncotree_curation
    """
    df = pd.read_csv(
        conditions_csv,
        dtype=str,
        keep_default_na=False,
        na_values=[],
    )
    df.columns = [str(c).strip() for c in df.columns]

    trial_id_col = _find_column_case_insensitive(df.columns.tolist(), "trial_id")
    original_col = _find_column_case_insensitive(df.columns.tolist(), "conditions_original")
    curated_col = _find_column_case_insensitive(df.columns.tolist(), "conditions_oncotree_curation")

    lookup: Dict[str, Dict[str, str]] = {}
    skipped_blank_trial_id = 0

    for _, row in df.iterrows():
        nct_id = _normalize_string(row.get(trial_id_col, "")).upper()
        if not nct_id:
            skipped_blank_trial_id += 1
            continue

        lookup[nct_id] = {
            "conditions_original": _blank_if_empty(row.get(original_col, "")),
            "conditions_oncotree_curation": _blank_if_empty(row.get(curated_col, "")),
        }

    if skipped_blank_trial_id:
        logger.warning(
            "Skipped %d row(s) with blank trial_id in %s",
            skipped_blank_trial_id,
            conditions_csv,
        )

    if not lookup:
        logger.warning("No trial-level conditions were loaded from %s", conditions_csv)

    return lookup


# ---------------------------
# NCT ID helpers
# ---------------------------

def get_nct_id(py_path: Path) -> str:
    try:
        text = py_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = py_path.read_text(encoding="utf-8", errors="ignore")

    for pattern in NCT_ID_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group("value").upper()

    stem = py_path.stem.strip()
    if re.fullmatch(r"NCT\d+", stem, flags=re.IGNORECASE):
        return stem.upper()
    return stem


# ---------------------------
# Tree helpers
# ---------------------------

def _is_criterion_node(obj: Any) -> bool:
    return obj is not None and type(obj).__name__.endswith("Criterion")


def _iter_nodes(value: Any) -> Iterator[Any]:
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
        children.extend(_iter_nodes(getattr(node, attr, None)))
    return children


def iter_children(node: Any) -> Iterator[Any]:
    for attr in CHILD_ATTRS:
        yield from _iter_nodes(getattr(node, attr, None))


def summarise_node(node: Any) -> str:
    node_type = _node_type_name(node)
    children = get_immediate_children(node)
    if not children:
        return node_type
    return f"{node_type}[{', '.join(summarise_node(child) for child in children)}]"


def build_ancestor_chain(ancestors: Sequence[Any]) -> str:
    return " -> ".join(_node_type_name(node) for node in ancestors) if ancestors else ""


def build_siblings_summary(node: Any, parent: Optional[Any]) -> str:
    if parent is None:
        return ""
    siblings = [child for child in get_immediate_children(parent) if child is not node]
    return " | ".join(summarise_node(sibling) for sibling in siblings)


# ---------------------------
# OncoTree helpers
# ---------------------------

def resolve_oncotree_curation(node: Any, mapping: PrimaryTumorMap) -> str:
    key = get_primary_tumor_key_from_node(node)
    if key is None:
        return ""
    return _blank_if_empty(mapping.get(key))


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
    trial_conditions: Optional[Dict[str, str]],
) -> PrimaryTumorOccurrenceRow:
    trial_conditions = trial_conditions or {}

    return PrimaryTumorOccurrenceRow(
        nct_id=_blank_if_empty(nct_id),
        primary_tumor_type=_blank_if_empty(getattr(node, "primary_tumor_type", None)),
        primary_tumor_location=_blank_if_empty(getattr(node, "primary_tumor_location", None)),
        primary_tumor_oncotree_curation=resolve_oncotree_curation(node, pt_map),
        conditions_original=_blank_if_empty(trial_conditions.get("conditions_original", "")),
        conditions_oncotree_curation=_blank_if_empty(
            trial_conditions.get("conditions_oncotree_curation", "")
        ),
        rule_text=_blank_if_empty(getattr(rule, "rule_text", None)),
        inclusive_rule=not bool(getattr(rule, "exclude", False)),
        ancestor_chain=build_ancestor_chain(ancestors),
        siblings_summary=build_siblings_summary(node, parent),
    )


def walk_rule_tree(
    *,
    nct_id: str,
    rule: Any,
    node: Any,
    parent: Optional[Any],
    ancestors: List[Any],
    pt_map: PrimaryTumorMap,
    out_rows: List[PrimaryTumorOccurrenceRow],
    trial_conditions: Optional[Dict[str, str]],
    seen: set[int],
) -> None:
    node_id = id(node)
    if node_id in seen:
        return
    seen.add(node_id)

    if type(node).__name__ == "PrimaryTumorCriterion":
        out_rows.append(
            make_row(
                nct_id=nct_id,
                rule=rule,
                node=node,
                ancestors=ancestors,
                parent=parent,
                pt_map=pt_map,
                trial_conditions=trial_conditions,
            )
        )

    next_ancestors = [*ancestors, node]
    for child in iter_children(node):
        walk_rule_tree(
            nct_id=nct_id,
            rule=rule,
            node=child,
            parent=node,
            ancestors=next_ancestors,
            pt_map=pt_map,
            out_rows=out_rows,
            trial_conditions=trial_conditions,
            seen=seen,
        )


def extract_rows_from_rules(
    *,
    nct_id: str,
    rules: Sequence[Any],
    pt_map: PrimaryTumorMap,
    conditions_lookup: Dict[str, Dict[str, str]],
) -> List[PrimaryTumorOccurrenceRow]:
    rows: List[PrimaryTumorOccurrenceRow] = []
    trial_conditions = conditions_lookup.get(nct_id.upper(), {})

    for rule in rules:
        for root in normalise_forest_into_list(rule):
            walk_rule_tree(
                nct_id=nct_id,
                rule=rule,
                node=root,
                parent=None,
                ancestors=[],
                pt_map=pt_map,
                out_rows=rows,
                trial_conditions=trial_conditions,
                seen=set(),
            )

    return rows


def extract_rows_from_trial_file(
    py_path: Path,
    pt_map: PrimaryTumorMap,
    conditions_lookup: Dict[str, Dict[str, str]],
) -> List[PrimaryTumorOccurrenceRow]:
    nct_id = get_nct_id(py_path)
    rules = load_curated_rules(py_path)
    if not rules:
        logger.warning("No rules loaded from %s", py_path)
        return []

    return extract_rows_from_rules(
        nct_id=nct_id,
        rules=rules,
        pt_map=pt_map,
        conditions_lookup=conditions_lookup,
    )


def write_rows_to_csv(rows: Sequence[PrimaryTumorOccurrenceRow], output_csv: Path) -> None:
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
            "NCT*.py files, with trial-level conditions read from a precomputed CSV."
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
        "--conditions_csv",
        required=True,
        type=Path,
        help="CSV containing trial_id, conditions_original, and conditions_oncotree_curation",
    )
    parser.add_argument(
        "--mapping_dir",
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

    pt_resource = _find_mapping_resource(args.mapping_dir, "PrimaryTumourCurationResource")
    logger.info("Using primary tumour mapping resource: %s", pt_resource)

    pt_map = build_primary_tumor_map(pt_resource)
    pt_map.pop(("", ""), None)

    conditions_lookup = load_trial_conditions_lookup(args.conditions_csv)
    logger.info(
        "Loaded trial-level conditions for %d trial(s) from %s",
        len(conditions_lookup),
        args.conditions_csv,
    )

    rows: List[PrimaryTumorOccurrenceRow] = []
    n_files = 0

    for py_path in _iter_input_py_files(args.curated_dir):
        n_files += 1
        try:
            rows.extend(
                extract_rows_from_trial_file(
                    py_path,
                    pt_map=pt_map,
                    conditions_lookup=conditions_lookup,
                )
            )
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