from __future__ import annotations

import argparse
import json
import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

ONCOTREE_LEVEL_COLS = [f"level_{i}" for i in range(1, 8)]
ONCOTREE_MORPHOLOGY_COLS = [f"level_{i}" for i in range(2, 8)]
STOPWORDS = {"of", "the", "and"}


def clean_str(x) -> str:
    if x is None or pd.isna(x):
        return ""
    return str(x).strip()


def normalize_text(s: str) -> str:
    s = clean_str(s).lower()
    if not s:
        return ""

    s = s.replace("&", " and ")
    s = s.replace("/", " ")
    s = s.replace("-", " ")
    s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(r"[^a-z0-9\s]", " ", s)

    tokens = [tok for tok in s.split() if tok and tok not in STOPWORDS]
    return " ".join(tokens)


def contains_whole_phrase(needle: str, haystack: str) -> bool:
    if not needle or not haystack:
        return False

    needle_tokens = needle.split()
    haystack_tokens = haystack.split()

    n = len(needle_tokens)
    h = len(haystack_tokens)

    if n == 0 or n > h:
        return False

    for i in range(h - n + 1):
        if haystack_tokens[i : i + n] == needle_tokens:
            return True

    return False


def split_pipe_delimited(s: str) -> List[str]:
    text = clean_str(s)
    if not text:
        return []
    return [part.strip() for part in text.split("|") if part.strip()]


def join_pipe_delimited(values: List[str]) -> str:
    cleaned = [clean_str(v) for v in values if clean_str(v)]
    return " | ".join(cleaned)


def require_columns(df: pd.DataFrame, required: Set[str], label: str) -> None:
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{label} missing required columns: {sorted(missing)}")


def load_topography_mapping(mapping_df: pd.DataFrame) -> Dict[str, List[str]]:
    require_columns(
        mapping_df,
        {"topography_simplified", "level1_oncotree"},
        "Topography mapping CSV",
    )

    grouped: Dict[str, List[str]] = defaultdict(list)

    for _, row in mapping_df.iterrows():
        topo = clean_str(row["topography_simplified"])
        lvl1 = clean_str(row["level1_oncotree"])
        if topo and lvl1 and lvl1 not in grouped[topo]:
            grouped[topo].append(lvl1)

    return dict(grouped)


def build_morphology_index(
    oncotree_df: pd.DataFrame,
) -> List[Tuple[str, str, str]]:
    require_columns(oncotree_df, set(ONCOTREE_LEVEL_COLS), "OncoTree CSV")

    entries: List[Tuple[str, str, str]] = []

    for _, row in oncotree_df.iterrows():
        level_1 = clean_str(row["level_1"])
        if not level_1:
            continue

        for col in ONCOTREE_MORPHOLOGY_COLS:
            term = clean_str(row[col])
            if not term:
                continue

            norm_term = normalize_text(term)
            if norm_term:
                entries.append((norm_term, level_1, term))

    return entries


def build_ancestor_index(oncotree_df: pd.DataFrame) -> Dict[str, Set[str]]:
    require_columns(oncotree_df, set(ONCOTREE_LEVEL_COLS), "OncoTree CSV")

    ancestors_by_term: Dict[str, Set[str]] = defaultdict(set)

    for _, row in oncotree_df.iterrows():
        path = [clean_str(row[col]) for col in ONCOTREE_LEVEL_COLS]
        path = [term for term in path if term]

        for i, term in enumerate(path):
            if i > 0:
                ancestors_by_term[term].update(path[:i])
            else:
                ancestors_by_term.setdefault(term, set())

    return dict(ancestors_by_term)


def find_morphology_matches_by_lvl1(
    morphology_simplified: str,
    morphology_index: List[Tuple[str, str, str]],
) -> Dict[str, List[str]]:
    norm_morphology = normalize_text(morphology_simplified)
    if not norm_morphology:
        return {}

    grouped: Dict[str, Set[str]] = defaultdict(set)

    for norm_oncotree_term, level_1, original_term in morphology_index:
        if contains_whole_phrase(norm_morphology, norm_oncotree_term):
            grouped[level_1].add(original_term)

    return {
        level_1: sorted(terms)
        for level_1, terms in sorted(grouped.items())
    }


def serialize_morphology_matches(matches: Dict[str, List[str]]) -> str:
    if not matches:
        return ""
    return json.dumps(matches, ensure_ascii=False, sort_keys=True)


def collect_intersection_terms(
    topography_oncotree_lvl1_exact_match: str,
    morphology_oncotree_exact_matches_by_lvl1: str,
) -> List[str]:
    topo_keys = split_pipe_delimited(topography_oncotree_lvl1_exact_match)
    morph_json = clean_str(morphology_oncotree_exact_matches_by_lvl1)

    if not topo_keys or not morph_json:
        return []

    try:
        morph_map = json.loads(morph_json)
    except json.JSONDecodeError:
        logger.warning("Could not parse morphology JSON: %r", morph_json)
        return []

    if not isinstance(morph_map, dict):
        return []

    matches: List[str] = []
    seen: Set[str] = set()

    for key in topo_keys:
        values = morph_map.get(key, [])
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            continue

        for value in values:
            value = clean_str(value)
            if value and value not in seen:
                seen.add(value)
                matches.append(value)

    return matches


def prune_ancestor_terms(
    terms: List[str],
    ancestors_by_term: Dict[str, Set[str]],
) -> List[str]:
    """
    If any returned term is a strict descendant of another returned term,
    keep the descendant and remove the ancestor.

    Example:
      [HNSC, HPHSC, OCSC] -> [HPHSC, OCSC]
    where HNSC is an ancestor of the others.
    """
    cleaned_terms = [clean_str(term) for term in terms if clean_str(term)]
    if len(cleaned_terms) <= 1:
        return cleaned_terms

    term_set = set(cleaned_terms)
    to_remove: Set[str] = set()

    for term in cleaned_terms:
        strict_ancestors = ancestors_by_term.get(term, set())
        for ancestor in strict_ancestors:
            if ancestor in term_set:
                to_remove.add(ancestor)

    return [term for term in cleaned_terms if term not in to_remove]


def process_csv(
    input_csv: Path,
    oncotree_csv: Path,
    topography_mapping_csv: Path,
    output_csv: Path,
) -> None:
    df = pd.read_csv(input_csv)
    oncotree_df = pd.read_csv(oncotree_csv)
    mapping_df = pd.read_csv(topography_mapping_csv)

    require_columns(
        df,
        {"topography_simplified", "morphology_simplified"},
        "Input CSV",
    )

    topography_mapping = load_topography_mapping(mapping_df)
    morphology_index = build_morphology_index(oncotree_df)
    ancestors_by_term = build_ancestor_index(oncotree_df)

    out = df.copy()

    out["topography_oncotree_lvl1_exact_match"] = out["topography_simplified"].map(
        lambda x: join_pipe_delimited(topography_mapping.get(clean_str(x), []))
    )

    out["morphology_oncotree_exact_matches_by_lvl1"] = out["morphology_simplified"].map(
        lambda x: serialize_morphology_matches(
            find_morphology_matches_by_lvl1(x, morphology_index)
        )
    )

    intersection_terms = out.apply(
        lambda row: collect_intersection_terms(
            topography_oncotree_lvl1_exact_match=row["topography_oncotree_lvl1_exact_match"],
            morphology_oncotree_exact_matches_by_lvl1=row["morphology_oncotree_exact_matches_by_lvl1"],
        ),
        axis=1,
    )

    out["topography_morphology_exact_match"] = intersection_terms.map(join_pipe_delimited)
    out["topography_morphology_exact_match_pruned"] = intersection_terms.map(
        lambda terms: join_pipe_delimited(prune_ancestor_terms(terms, ancestors_by_term))
    )

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output_csv, index=False)

    n_total = len(out)
    n_topo = int((out["topography_oncotree_lvl1_exact_match"] != "").sum())
    n_morph = int((out["morphology_oncotree_exact_matches_by_lvl1"] != "").sum())
    n_intersection = int((out["topography_morphology_exact_match"] != "").sum())
    n_pruned = int((out["topography_morphology_exact_match_pruned"] != "").sum())

    logger.info("Wrote %d rows to %s", n_total, output_csv)
    logger.info("Topography level-1 matches populated: %d", n_topo)
    logger.info("Morphology grouped matches populated: %d", n_morph)
    logger.info("Exact match intersections populated: %d", n_intersection)
    logger.info("Pruned exact match intersections populated: %d", n_pruned)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input_csv", required=True, type=Path)
    parser.add_argument("--oncotree_csv", required=True, type=Path)
    parser.add_argument("--topography_mapping_csv", required=True, type=Path)
    parser.add_argument("--output_csv", required=True, type=Path)
    parser.add_argument("--log_level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    process_csv(
        input_csv=args.input_csv,
        oncotree_csv=args.oncotree_csv,
        topography_mapping_csv=args.topography_mapping_csv,
        output_csv=args.output_csv,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
