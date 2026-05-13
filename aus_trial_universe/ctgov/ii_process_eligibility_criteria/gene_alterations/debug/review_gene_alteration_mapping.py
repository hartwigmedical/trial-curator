from __future__ import annotations

import argparse
import logging
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, DefaultDict, Dict, Iterable, List, Sequence, Tuple

import pandas as pd

from aus_trial_universe.ctgov.i_download_trials_and_extract_eligibility.utils.load_curated_rules import (
    load_curated_rules,
)
from aus_trial_universe.ctgov.utils.general.text_normalisation import (
    clean_cell_str,
    is_effectively_empty,
    norm_cell,
)

from aus_trial_universe.ctgov.ii_process_eligibility_criteria.gene_alterations.mapping.gene_alteration_mapping import (
    GeneAlterationKey,
    load_mapping_resource,
    make_gene_alteration_key,
)
from aus_trial_universe.ctgov.ii_process_eligibility_criteria.gene_alterations.mapping.gene_alteration_overwrite import (
    get_gene_alteration_key_from_node
)

LOGGER = logging.getLogger(__name__)

MAPPING_REQUIRED_COLUMNS: List[str] = [
    "Gene_lookup",
    "Alteration_lookup",
    "Variant_lookup",
    "Gene_curation",
    "Variant_curation",
    "FindingsModel_curation",
    "Gene_type",
    "Fusion_both",
    "Fusion_FivePrime",
    "Fusion_ThreePrime",
    "Mapping_args",
]

OUTPUT_COLUMNS: List[str] = [
    "TrialId",
    "source_file",
    "rule_index",
    "criterion_index",
    "criterion_path",
    "rule_text",
    "rule_exclude",
    "under_not_criterion",
    "gene_input",
    "alteration_input",
    "variant_input",
    "description_input",
    "Gene_curation",
    "Variant_curation",
    "FindingsModel_curation",
    "Gene_type",
    "Fusion_both",
    "Fusion_FivePrime",
    "Fusion_ThreePrime",
    "final_curation",
    "mapping_status",
    "accept_reject",
    "manual_overwrite",
]


@dataclass(frozen=True)
class MappingRecord:
    key: GeneAlterationKey
    gene_curation: str
    variant_curation: str
    findings_model_curation: str
    gene_type: str
    fusion_both: str
    fusion_five_prime: str
    fusion_three_prime: str
    mapping_args: str


MappingIndex = Dict[GeneAlterationKey, List[MappingRecord]]


# =============================================================================
# Generic helpers
# =============================================================================


def _as_display_cell(value: Any) -> str:
    """Return a stable human-readable value for workbook output."""
    return clean_cell_str(value)


def _unique_preserve_order(values: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for value in values:
        value = _as_display_cell(value)
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _join_unique(values: Iterable[str], *, sep: str = " || ") -> str:
    return sep.join(_unique_preserve_order(values))


def _type_name(obj: Any) -> str:
    return type(obj).__name__


def _iter_child_objects(obj: Any) -> Iterable[Tuple[str, Any]]:
    """
    Yield object-graph children for traversal.

    The curated rule loader returns permissive shim objects with useful fields in
    __dict__. This function deliberately walks lists/tuples/dicts and object
    attributes, while skipping primitive scalar values.
    """
    if obj is None or isinstance(obj, (str, bytes, int, float, bool)):
        return

    if isinstance(obj, dict):
        for key, value in obj.items():
            yield str(key), value
        return

    if isinstance(obj, (list, tuple, set)):
        for idx, value in enumerate(obj):
            yield f"[{idx}]", value
        return

    d = getattr(obj, "__dict__", None)
    if isinstance(d, dict):
        for key, value in d.items():
            if key.startswith("_"):
                continue
            yield key, value


def _is_gene_alteration_node(node: Any) -> bool:
    return _type_name(node) == "GeneAlterationCriterion"


def _is_not_node(node: Any) -> bool:
    return _type_name(node) == "NotCriterion"


def _safe_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().upper() == "TRUE"


# =============================================================================
# Mapping workbook loading/indexing
# =============================================================================


def load_mapping_index(mapping_path: Path) -> MappingIndex:
    """
    Load the mapping resource and index rows by normalized
    (Gene_lookup, Alteration_lookup, Variant_lookup).

    This intentionally keeps all rows per key so the review sheet can flag
    ambiguous mapping keys instead of silently applying last-one-wins behavior.
    """
    df = load_mapping_resource(mapping_path)

    missing = [col for col in MAPPING_REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(
            f"Gene alteration mapping resource missing required columns: {missing}"
        )

    index: DefaultDict[GeneAlterationKey, List[MappingRecord]] = defaultdict(list)

    for _, row in df.iterrows():
        key = make_gene_alteration_key(
            norm_cell(row.get("Gene_lookup")),
            norm_cell(row.get("Alteration_lookup")),
            norm_cell(row.get("Variant_lookup")),
        )

        # Skip fully empty lookup rows. They cannot map a real criterion.
        if not any(key):
            continue

        index[key].append(
            MappingRecord(
                key=key,
                gene_curation=_as_display_cell(row.get("Gene_curation")),
                variant_curation=_as_display_cell(row.get("Variant_curation")),
                findings_model_curation=_as_display_cell(row.get("FindingsModel_curation")),
                gene_type=_as_display_cell(row.get("Gene_type")),
                fusion_both=_as_display_cell(row.get("Fusion_both")),
                fusion_five_prime=_as_display_cell(row.get("Fusion_FivePrime")),
                fusion_three_prime=_as_display_cell(row.get("Fusion_ThreePrime")),
                mapping_args=_as_display_cell(row.get("Mapping_args")),
            )
        )

    return dict(index)


def resolve_mapping_for_node(
    node: Any,
    mapping_index: MappingIndex,
) -> Tuple[str, Dict[str, str]]:
    """
    Return (mapping_status, output_fields) for a GeneAlterationCriterion node.
    """
    empty_fields = {
        "Gene_curation": "",
        "Variant_curation": "",
        "FindingsModel_curation": "",
        "Gene_type": "",
        "Fusion_both": "",
        "Fusion_FivePrime": "",
        "Fusion_ThreePrime": "",
        "final_curation": "",
    }

    key = get_gene_alteration_key_from_node(node)
    if key is None:
        return "no_mapping_found", empty_fields

    records = mapping_index.get(key, [])
    if not records:
        return "no_mapping_found", empty_fields

    unique_mapping_args = _unique_preserve_order(r.mapping_args for r in records)
    non_empty_unique_mapping_args = [
        v for v in unique_mapping_args if not is_effectively_empty(v)
    ]

    status = "mapped_exact"
    if len(non_empty_unique_mapping_args) > 1:
        status = "ambiguous_mapping"

    fields = {
        "Gene_curation": _join_unique(r.gene_curation for r in records),
        "Variant_curation": _join_unique(r.variant_curation for r in records),
        "FindingsModel_curation": _join_unique(
            r.findings_model_curation for r in records
        ),
        "Gene_type": _join_unique(r.gene_type for r in records),
        "Fusion_both": _join_unique(r.fusion_both for r in records),
        "Fusion_FivePrime": _join_unique(r.fusion_five_prime for r in records),
        "Fusion_ThreePrime": _join_unique(r.fusion_three_prime for r in records),
        "final_curation": _join_unique(r.mapping_args for r in records),
    }
    return status, fields


# =============================================================================
# Curated rule traversal
# =============================================================================


def iter_gene_alteration_nodes(rule: Any) -> Iterable[Tuple[Any, bool, str]]:
    """
    Yield (node, under_not_criterion, path) for every GeneAlterationCriterion
    under a loaded Rule object.

    `under_not_criterion` reflects syntactic nesting under NotCriterion only.
    It intentionally does not fold in Rule.exclude, which is output separately.
    """
    seen_ids: set[int] = set()

    def walk(obj: Any, *, under_not: bool, path: str) -> Iterable[Tuple[Any, bool, str]]:
        if obj is None or isinstance(obj, (str, bytes, int, float, bool)):
            return

        obj_id = id(obj)
        if obj_id in seen_ids:
            return
        seen_ids.add(obj_id)

        next_under_not = under_not or _is_not_node(obj)

        if _is_gene_alteration_node(obj):
            yield obj, under_not, path
            # GeneAlterationCriterion fields are scalar; no need to recurse further.
            return

        for child_name, child in _iter_child_objects(obj):
            child_path = f"{path}.{child_name}" if path else child_name
            yield from walk(child, under_not=next_under_not, path=child_path)

    yield from walk(rule, under_not=False, path="rule")


def extract_records_from_rules(
    *,
    trial_id: str,
    source_file: str,
    rules: Sequence[Any],
    mapping_index: MappingIndex,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    criterion_counter = 0

    for rule_index, rule in enumerate(rules, start=1):
        rule_text = _as_display_cell(getattr(rule, "rule_text", ""))
        rule_exclude = _safe_bool(getattr(rule, "exclude", False))

        for node, under_not, path in iter_gene_alteration_nodes(rule):
            criterion_counter += 1
            mapping_status, mapping_fields = resolve_mapping_for_node(
                node,
                mapping_index,
            )

            row: Dict[str, Any] = {
                "TrialId": trial_id,
                "source_file": source_file,
                "rule_index": rule_index,
                "criterion_index": criterion_counter,
                "criterion_path": path,
                "rule_text": rule_text,
                "rule_exclude": rule_exclude,
                "under_not_criterion": under_not,
                "gene_input": _as_display_cell(getattr(node, "gene", "")),
                "alteration_input": _as_display_cell(getattr(node, "alteration", "")),
                "variant_input": _as_display_cell(getattr(node, "variant", "")),
                "description_input": _as_display_cell(getattr(node, "description", "")),
                **mapping_fields,
                "mapping_status": mapping_status,
                "accept_reject": "",
                "manual_overwrite": "",
            }
            rows.append(row)

    return rows


def extract_records_from_file(
    *,
    file_path: Path,
    mapping_index: MappingIndex,
) -> List[Dict[str, Any]]:
    rules = load_curated_rules(file_path)
    if rules is None:
        LOGGER.warning("Skipping %s because load_curated_rules returned None", file_path)
        return []

    if not isinstance(rules, Sequence) or isinstance(rules, (str, bytes)):
        LOGGER.warning("Skipping %s because `rules` is not a sequence", file_path)
        return []

    return extract_records_from_rules(
        trial_id=file_path.stem,
        source_file=file_path.name,
        rules=rules,
        mapping_index=mapping_index,
    )


# =============================================================================
# Public build/write API
# =============================================================================


def build_review(*, input_dir: Path, mapping_xlsx: Path) -> pd.DataFrame:
    LOGGER.info("Reading mapping resource: %s", mapping_xlsx)
    mapping_index = load_mapping_index(mapping_xlsx)
    LOGGER.info("Loaded %d unique GeneAlteration mapping key(s)", len(mapping_index))

    py_files = sorted(input_dir.glob("*.py"))
    LOGGER.info("Found %d Python rule file(s)", len(py_files))

    rows: List[Dict[str, Any]] = []
    skipped = 0

    for file_path in py_files:
        try:
            file_rows = extract_records_from_file(
                file_path=file_path,
                mapping_index=mapping_index,
            )
        except Exception as exc:  # keep batch review generation robust
            skipped += 1
            LOGGER.exception("Failed to process %s: %s", file_path, exc)
            continue

        LOGGER.info(
            "%s: extracted %d GeneAlterationCriterion row(s)",
            file_path.name,
            len(file_rows),
        )
        rows.extend(file_rows)

    if skipped:
        LOGGER.warning("Skipped %d file(s) due to processing errors", skipped)

    df = pd.DataFrame(rows)
    for col in OUTPUT_COLUMNS:
        if col not in df.columns:
            df[col] = ""

    return df[OUTPUT_COLUMNS]


def write_review_workbook(df: pd.DataFrame, output_xlsx: Path) -> None:
    output_xlsx.parent.mkdir(parents=True, exist_ok=True)
    LOGGER.info("Writing review workbook: %s", output_xlsx)

    with pd.ExcelWriter(output_xlsx, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="gene_alteration_review")


# =============================================================================
# CLI
# =============================================================================


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a human-review workbook for GeneAlterationCriterion mappings "
            "from curated trial rule Python files."
        )
    )
    parser.add_argument(
        "--input_dir",
        required=True,
        help="Directory containing curated trial rule .py files.",
    )
    parser.add_argument(
        "--mapping_xlsx",
        required=True,
        help="Gene alteration mapping resource workbook/csv.",
    )
    parser.add_argument(
        "--output_xlsx",
        required=True,
        help="Output review workbook path.",
    )
    parser.add_argument(
        "--log_level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    input_dir = Path(args.input_dir)
    mapping_xlsx = Path(args.mapping_xlsx)
    output_xlsx = Path(args.output_xlsx)

    if not input_dir.exists() or not input_dir.is_dir():
        raise NotADirectoryError(f"Input directory does not exist: {input_dir}")
    if not mapping_xlsx.exists() or not mapping_xlsx.is_file():
        raise FileNotFoundError(f"Mapping resource does not exist: {mapping_xlsx}")

    review_df = build_review(input_dir=input_dir, mapping_xlsx=mapping_xlsx)
    write_review_workbook(review_df, output_xlsx)

    status_counts = (
        review_df["mapping_status"].value_counts(dropna=False).to_dict()
        if not review_df.empty
        else {}
    )
    LOGGER.info(
        "Done. Wrote %d review row(s). mapping_status counts=%s",
        len(review_df),
        status_counts,
    )


if __name__ == "__main__":
    main()
