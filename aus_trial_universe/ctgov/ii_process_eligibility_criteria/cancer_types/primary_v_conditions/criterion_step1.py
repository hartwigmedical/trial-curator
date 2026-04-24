from __future__ import annotations

import argparse
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence

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
from aus_trial_universe.ctgov.utils.oncotree.traverse_oncotree import OncoTree

logger = logging.getLogger(__name__)

CHILD_ATTRS: Sequence[str] = (
    "criteria",
    "criterion",
    "condition",
    "then",
    "else_",
)

BASE_CSV_COLUMNS: Sequence[str] = (
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

CONDITIONS_CLEANED_COL = "conditions_oncotree_curation_cleaned"
PRIMARY_SIMPLIFIED_COL = "primary_tumor_oncotree_curation_simplified"
CONDITIONS_CLEANED_SIMPLIFIED_COL = "conditions_oncotree_curation_cleaned_simplified"
CONDITIONS_CLEANED_SIMPLIFIED_PANCANCER_COL = "conditions_oncotree_curation_cleaned_simplified_pancancer"
RELATION_COL = "primary_vs_conditions_relation"

MANUAL_KEY_COLS: Sequence[str] = (
    "nct_id",
    "primary_tumor_type",
    "primary_tumor_location",
    "conditions_original",
)
MANUAL_COL = "manual_overwrite"

NCT_ID_PATTERNS: Sequence[re.Pattern[str]] = (
    re.compile(r"\bNctId\s*=\s*['\"](?P<value>NCT\d+)['\"]"),
    re.compile(r"\bnct_id\s*=\s*['\"](?P<value>NCT\d+)['\"]", re.IGNORECASE),
    re.compile(r"\bNCT_ID\s*=\s*['\"](?P<value>NCT\d+)['\"]"),
)

CODE_RE = re.compile(r"\(([^()]+)\)\s*$")
EXTRACTION_NULL_SENTINELS = {"", "NOT([None])"}
COMPARISON_NULL_SENTINELS = {"", "[None]", "NOT([None])"}
DELIMITER = " | "

REPLACEMENTS = {
    "na√Øve": "naïve",
    "Na√Øve": "Naïve",
    "Waldenstr√∂m's": "Waldenström's",
    "Waldenstr√∂m ": "Waldenström ",
    "HIF-2Œ±": "HIF-2α",
}


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
    if pd.isna(value):
        return ""
    return str(value).strip()


def _blank_if_empty(value: Any) -> str:
    if value is None or is_effectively_empty(value):
        return ""
    s = clean_cell_str(value)
    return "" if s in EXTRACTION_NULL_SENTINELS else s


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


def _read_tabular_file(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[])
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path, dtype=str, keep_default_na=False, na_values=[])
    raise ValueError(f"Unsupported input file type: {path.suffix}")


def _write_tabular_file(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        df.to_csv(path, index=False)
        return
    if suffix in {".xlsx", ".xls"}:
        df.to_excel(path, index=False)
        return
    raise ValueError(f"Unsupported output file type: {path.suffix}")


def _fix_mojibake(text: str) -> str:
    if not text:
        return text
    for bad, good in REPLACEMENTS.items():
        text = text.replace(bad, good)
    return text


def _normalize_for_comparison(value: object) -> str:
    return _fix_mojibake(_normalize_string(value))


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


def _extract_code(curation: str) -> Optional[str]:
    text = _normalize_string(curation)
    if not text or text in COMPARISON_NULL_SENTINELS:
        return None

    match = CODE_RE.search(text)
    if match:
        code = match.group(1).strip()
        return code or None

    return text


def _split_terms_preserve_order(value: object) -> List[str]:
    text = _normalize_string(value)
    if not text:
        return []

    parts = [part.strip() for part in text.split("|")]
    return [part for part in parts if part]


def _dedupe_preserve_order(values: Sequence[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _normalize_term_for_special_cases(term: str) -> str:
    return _normalize_string(term).casefold()


def _is_pan_cancer_term(term: str) -> bool:
    normalized = _normalize_term_for_special_cases(term)
    return normalized == "pan-cancer" or normalized == "pan cancer"


MOLECULAR_SIGNAL_PHRASES: Sequence[str] = (
    "mutation",
    "mutations",
    "mutated",
    "mutant",
    "alteration",
    "alterations",
    "altered",
    "amplification",
    "amplifications",
    "amplified",
    "fusion",
    "fusions",
    "fusion-positive",
    "fusion positive",
    "rearrangement",
    "rearrangements",
    "translocation",
    "translocations",
    "deletion",
    "deletions",
    "deleted",
    "deficiency",
    "deficient",
    "exon",
    "skipping",
    "msi-h",
    "msi high",
    "dmmr",
    "mmr deficiency",
    "mmr deficient",
    "mismatch repair deficient",
    "tumor mutational burden",
    "tumour mutational burden",
    "gene mutation",
    "gene mutations",
    "gene alteration",
    "gene alterations",
    "gene fusion",
    "gene fusions",
    "gene rearrangement",
    "gene rearrangements",
    "gene translocation",
    "gene translocations",
    "gene activation",
    "gene deletion",
    "mtap-null",
    "mtap null",
    "mtap-deleted",
    "mtap deleted",
    "homologous recombination deficiency",
    "her2 mutation",
    "her2 gene mutation",
    "her2 amplification",
    "her2 amplified",
    "her2 expressing/amplified",
    "her2-expressing",
    "her2 expressing",
)

MOLECULAR_SIGNAL_TOKENS: Sequence[str] = (
    "AFP",
    "ALK",
    "BCL2",
    "BCL6",
    "BCR-ABL1",
    "BRAF",
    "CCNE1",
    "CD33",
    "DMMR",
    "EGFR",
    "FGFR2",
    "FGFR3",
    "FLT3",
    "GPC3",
    "HIF-2Α",
    "HLA-A*02:01",
    "HRAS",
    "IDH1",
    "KRAS",
    "MAP2K1",
    "MAPK",
    "MDM2",
    "MECP2",
    "MEK",
    "MET",
    "MMR",
    "MSI-H",
    "MTAP",
    "MYC",
    "NF1",
    "NRAS",
    "NTRK",
    "OPA1",
    "PI3K",
    "PIK3CA",
    "PRAME",
    "PSMA",
    "P53",
    "RAF",
    "RET",
    "SOD1",
    "TMB",
)

MOLECULAR_VARIANT_PATTERNS: Sequence[re.Pattern[str]] = (
    re.compile(r"\bG12[ACDRV]\b", re.IGNORECASE),
    re.compile(r"\bV600[A-Z]?\b", re.IGNORECASE),
    re.compile(r"\bR132[A-Z]?\b", re.IGNORECASE),
    re.compile(r"\bL858R\b", re.IGNORECASE),
    re.compile(r"\bT790M\b", re.IGNORECASE),
    re.compile(r"\bK27M\b", re.IGNORECASE),
    re.compile(r"\bG34[A-Z]?\b", re.IGNORECASE),
    re.compile(r"\bEX20INS\b", re.IGNORECASE),
    re.compile(r"\b1P/19Q\b", re.IGNORECASE),
)

MOLECULAR_SIGNAL_EXCLUSION_PHRASES: Sequence[str] = (
    "diffuse midline glioma, h3 k27-altered",
    "diffuse midline glioma, h3 k27m-mutant",
    "diffuse midline glioma, h3 k27m mutant",
    "h3 k27-altered",
    "h3 k27 altered",
    "h3 k27m-mutant",
    "h3 k27m mutant",
    "h3 k27m",
    "her2+ breast cancer",
    "her2-positive breast cancer",
    "her2 positive breast cancer",
    "her2- breast cancer",
    "her2-negative breast cancer",
    "her2 negative breast cancer",
    "er/pr positive breast cancer",
    "er/pr(+) her2(-) breast cancer",
    "hr+/her2- breast cancer",
    "hormone receptor positive breast cancer",
    "triple negative breast cancer",
    "tnbc",
)


def _has_molecular_or_genetic_signal(conditions_original: object) -> bool:
    text = _fix_mojibake(_normalize_string(conditions_original))
    if not text:
        return False

    text_casefold = text.casefold()

    if any(phrase in text_casefold for phrase in MOLECULAR_SIGNAL_EXCLUSION_PHRASES):
        return False

    if any(phrase in text_casefold for phrase in MOLECULAR_SIGNAL_PHRASES):
        return True

    text_upper = text.upper()
    if any(re.search(rf"\b{re.escape(token)}\b", text_upper) for token in MOLECULAR_SIGNAL_TOKENS):
        return True

    return any(pattern.search(text) for pattern in MOLECULAR_VARIANT_PATTERNS)


def _remove_pan_cancer_if_other_terms_exist(
        value: object,
        *,
        conditions_original: object = "",
) -> str:
    terms = _dedupe_preserve_order(_split_terms_preserve_order(value))
    if len(terms) <= 1:
        return DELIMITER.join(terms)

    has_pan_cancer = any(_is_pan_cancer_term(term) for term in terms)
    if not has_pan_cancer:
        return DELIMITER.join(terms)

    if _has_molecular_or_genetic_signal(conditions_original):
        return "Pan-cancer"

    filtered_terms = [term for term in terms if not _is_pan_cancer_term(term)]
    if not filtered_terms:
        return DELIMITER.join(terms)

    return DELIMITER.join(filtered_terms)


def _is_effectively_missing_conditions_term(term: str) -> bool:
    return _normalize_string(term) in {"", "[None]", "NOT([None])"}


def _clean_conditions_curation(value: object) -> str:
    terms = _split_terms_preserve_order(value)
    deduped_terms = _dedupe_preserve_order(terms)

    if len(deduped_terms) > 1:
        deduped_terms = [term for term in deduped_terms if term != "[None]"]

    return DELIMITER.join(deduped_terms)


def _is_strict_ancestor(tree: OncoTree, ancestor_code: str, descendant_code: str) -> bool:
    if ancestor_code == descendant_code:
        return False
    return ancestor_code in {node.code for node in tree.ancestors(descendant_code)}


def _is_strict_descendant(tree: OncoTree, descendant_code: str, ancestor_code: str) -> bool:
    return _is_strict_ancestor(tree, ancestor_code, descendant_code)


def _simplify_curation_terms(tree: OncoTree, value: object) -> str:
    terms = _dedupe_preserve_order(_split_terms_preserve_order(value))

    if len(terms) == 1 and terms[0] == "[None]":
        return ""

    if len(terms) <= 1:
        return DELIMITER.join(terms)

    codes = [_extract_code(term) for term in terms]
    keep_mask = [True] * len(terms)

    for i, code_i in enumerate(codes):
        if not code_i:
            continue

        for j, code_j in enumerate(codes):
            if i == j or not code_j:
                continue

            if _is_strict_ancestor(tree, code_j, code_i):
                keep_mask[i] = False
                break

    simplified_terms = [term for term, keep in zip(terms, keep_mask) if keep]
    return DELIMITER.join(simplified_terms)


def _classify_primary_term_against_conditions(
        tree: OncoTree,
        primary_term: str,
        condition_terms: Sequence[str],
) -> str:
    primary_code = _extract_code(primary_term)

    for condition_term in condition_terms:
        condition_code = _extract_code(condition_term)

        if primary_term == condition_term:
            return "identical"

        if primary_code and condition_code:
            if _is_strict_ancestor(tree, primary_code, condition_code):
                return "is_ancestor_of"

            if _is_strict_descendant(tree, primary_code, condition_code):
                return "is_descendant_of"

        if _is_pan_cancer_term(condition_term) and primary_term != condition_term:
            return "is_descendant_of"

    return "CHECK"


def classify_relation(
        tree: OncoTree,
        primary_curation: object,
        cleaned_conditions_curation: object,
) -> str:
    primary_terms = _split_terms_preserve_order(primary_curation)
    condition_terms = _split_terms_preserve_order(cleaned_conditions_curation)

    if not condition_terms or all(_is_effectively_missing_conditions_term(term) for term in condition_terms):
        return "conditions_missing"

    if not primary_terms or all(_is_effectively_missing_conditions_term(term) for term in primary_terms):
        return "primary_missing"

    condition_terms_non_missing = [
        term for term in condition_terms if not _is_effectively_missing_conditions_term(term)
    ]

    term_relations = [
        _classify_primary_term_against_conditions(tree, primary_term, condition_terms_non_missing)
        for primary_term in primary_terms
    ]
    return DELIMITER.join(term_relations)


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


# ---------------------------
# Comparison augmentation
# ---------------------------

def rows_to_dataframe(rows: Sequence[PrimaryTumorOccurrenceRow]) -> pd.DataFrame:
    data = [
        {key: _csv_value(asdict(row)[key]) for key in BASE_CSV_COLUMNS}
        for row in rows
    ]
    return pd.DataFrame(data, columns=list(BASE_CSV_COLUMNS))


def add_comparison_columns(
        df: pd.DataFrame,
        tree: OncoTree,
) -> pd.DataFrame:
    required_cols = [
        "primary_tumor_oncotree_curation",
        "conditions_oncotree_curation",
    ]
    missing = [col for col in required_cols if col not in df.columns]
    if missing:
        raise ValueError(f"Input CSV missing required column(s): {missing}")

    out = df.copy()

    cleaned_conditions = [
        _clean_conditions_curation(value)
        for value in out["conditions_oncotree_curation"].tolist()
    ]
    primary_simplified = [
        _simplify_curation_terms(tree, value)
        for value in out["primary_tumor_oncotree_curation"].tolist()
    ]
    cleaned_conditions_simplified = [
        _simplify_curation_terms(tree, value)
        for value in cleaned_conditions
    ]
    cleaned_conditions_simplified_2 = [
        _remove_pan_cancer_if_other_terms_exist(
            value,
            conditions_original=conditions_original,
        )
        for value, conditions_original in zip(
            cleaned_conditions_simplified,
            out["conditions_original"].tolist(),
        )
    ]
    conditions_w_molecular = [
        (
                _has_molecular_or_genetic_signal(conditions_original)
                and simplified_pancancer == "Pan-cancer"
        )
        for conditions_original, simplified_pancancer in zip(
            out["conditions_original"].tolist(),
            cleaned_conditions_simplified_2,
        )
    ]
    relations = [
        classify_relation(
            tree=tree,
            primary_curation=primary_value,
            cleaned_conditions_curation=cleaned_value,
        )
        for primary_value, cleaned_value in zip(
            primary_simplified,
            cleaned_conditions_simplified,
        )
    ]

    conditions_insert_at = out.columns.get_loc("conditions_oncotree_curation") + 1
    out.insert(conditions_insert_at, CONDITIONS_CLEANED_COL, cleaned_conditions)

    primary_insert_at = out.columns.get_loc("primary_tumor_oncotree_curation") + 1
    out.insert(primary_insert_at, PRIMARY_SIMPLIFIED_COL, primary_simplified)

    cleaned_insert_at = out.columns.get_loc(CONDITIONS_CLEANED_COL) + 1
    out.insert(cleaned_insert_at, CONDITIONS_CLEANED_SIMPLIFIED_COL, cleaned_conditions_simplified)

    cleaned_2_insert_at = out.columns.get_loc(CONDITIONS_CLEANED_SIMPLIFIED_COL) + 1
    out.insert(
        cleaned_2_insert_at,
        CONDITIONS_CLEANED_SIMPLIFIED_PANCANCER_COL,
        cleaned_conditions_simplified_2,
    )

    out.insert(
        cleaned_2_insert_at + 1,
        "conditions_w_molecular",
        conditions_w_molecular,
    )

    out[RELATION_COL] = relations
    return out


# ---------------------------
# Manual overwrite horizontal concat
# ---------------------------

def _validate_manual_concat_alignment(input_df: pd.DataFrame, manual_df: pd.DataFrame) -> None:
    missing_input = [col for col in MANUAL_KEY_COLS if col not in input_df.columns]
    if missing_input:
        raise ValueError(f"Input file missing required manual alignment column(s): {missing_input}")

    required_manual = [*MANUAL_KEY_COLS, MANUAL_COL]
    missing_manual = [col for col in required_manual if col not in manual_df.columns]
    if missing_manual:
        raise ValueError(f"Manual overwrite file missing required column(s): {missing_manual}")

    if len(input_df) != len(manual_df):
        raise ValueError(
            "Input file and manual overwrite file must have the same number of rows for horizontal concat. "
            f"Got {len(input_df)} vs {len(manual_df)}."
        )

    mismatch_rows: List[int] = []
    for i in range(len(input_df)):
        input_row = input_df.iloc[i]
        manual_row = manual_df.iloc[i]

        checks = (
            _normalize_for_comparison(input_row.get("nct_id", "")) == _normalize_for_comparison(
                manual_row.get("nct_id", "")),
            _normalize_for_comparison(input_row.get("primary_tumor_type", "")) == _normalize_for_comparison(
                manual_row.get("primary_tumor_type", "")),
            _normalize_for_comparison(input_row.get("primary_tumor_location", "")) == _normalize_for_comparison(
                manual_row.get("primary_tumor_location", "")),
            _normalize_for_comparison(input_row.get("conditions_original", "")) == _normalize_for_comparison(
                manual_row.get("conditions_original", "")),
        )
        if not all(checks):
            mismatch_rows.append(i + 2)
            if len(mismatch_rows) >= 5:
                break

    if mismatch_rows:
        raise ValueError(
            "Input file and manual overwrite file failed row-wise alignment checks for horizontal concat. "
            f"First mismatched Excel-style row number(s): {mismatch_rows}"
        )


def apply_manual_overwrite_concat(df: pd.DataFrame, manual_df: pd.DataFrame) -> pd.DataFrame:
    _validate_manual_concat_alignment(df, manual_df)

    manual_payload = manual_df.copy().drop(columns=list(MANUAL_KEY_COLS), errors="ignore")
    out = pd.concat([df.reset_index(drop=True), manual_payload.reset_index(drop=True)], axis=1)
    return out


# ---------------------------
# Combined workflow
# ---------------------------

def run_combined_workflow(
        *,
        curated_dir: Path,
        conditions_csv: Path,
        mapping_dir: Path,
        oncotree_csv: Path,
        manual_overwrite_file: Path,
        fail_on_error: bool = False,
) -> pd.DataFrame:
    pt_resource = _find_mapping_resource(mapping_dir, "PrimaryTumourCurationResource")
    logger.info("Using primary tumour mapping resource: %s", pt_resource)

    pt_map = build_primary_tumor_map(pt_resource)
    pt_map.pop(("", ""), None)

    conditions_lookup = load_trial_conditions_lookup(conditions_csv)
    logger.info(
        "Loaded trial-level conditions for %d trial(s) from %s",
        len(conditions_lookup),
        conditions_csv,
    )

    logger.info("Loading OncoTree CSV: %s", oncotree_csv)
    tree = OncoTree.from_oncotree_csv(oncotree_csv)

    rows: List[PrimaryTumorOccurrenceRow] = []
    n_files = 0

    for py_path in _iter_input_py_files(curated_dir):
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
            if fail_on_error:
                raise
            logger.exception("Skipping %s due to error: %s", py_path, exc)

    logger.info("Extracted %d row(s) from %d file(s)", len(rows), n_files)

    out = rows_to_dataframe(rows)
    out = add_comparison_columns(out, tree)

    logger.info("Loading manual overwrite file: %s", manual_overwrite_file)
    manual_df = _read_tabular_file(manual_overwrite_file)
    manual_df.columns = [str(col).strip() for col in manual_df.columns]

    logger.info("Applying manual overwrite horizontal concat")
    out = apply_manual_overwrite_concat(out, manual_df)
    return out


# ---------------------------
# CLI
# ---------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Combine criterion-level primary tumor extraction/comparison with manual_overwrite "
            "horizontal concat, while stopping before any final_cancer_1 / iii->v reconciliation logic."
        )
    )
    parser.add_argument(
        "--initial_curated_dir",
        required=True,
        type=Path,
        help="Directory of curated NCT*.py files or a single file",
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
        "--oncotree_csv",
        required=True,
        type=Path,
        help="OncoTree CSV used by aus_trial_universe.ctgov.utils.oncotree.traverse_oncotree.OncoTree",
    )
    parser.add_argument(
        "--manual_overwrite_file",
        required=True,
        type=Path,
        help=(
            "CSV/XLSX containing nct_id, primary_tumor_type, primary_tumor_location, "
            "conditions_original, and manual_overwrite."
        ),
    )
    parser.add_argument(
        "--output_file",
        required=True,
        type=Path,
        help="Path to write the output CSV/XLSX.",
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

    out = run_combined_workflow(
        curated_dir=args.initial_curated_dir,
        conditions_csv=args.conditions_csv,
        mapping_dir=args.mapping_dir,
        oncotree_csv=args.oncotree_csv,
        manual_overwrite_file=args.manual_overwrite_file,
        fail_on_error=args.fail_on_error,
    )

    logger.info("Writing output file: %s", args.output_file)
    _write_tabular_file(out, args.output_file)
    logger.info("Done. Wrote %d rows to %s", len(out), args.output_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
