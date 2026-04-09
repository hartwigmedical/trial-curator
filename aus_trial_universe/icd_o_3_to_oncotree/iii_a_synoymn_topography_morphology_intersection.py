from __future__ import annotations

import argparse
import json
import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

ONCOTREE_LEVEL_COLS = [f"level_{i}" for i in range(1, 8)]
ONCOTREE_MORPHOLOGY_COLS = [f"level_{i}" for i in range(2, 8)]
STOPWORDS = {"of", "the", "and"}


def clean_str(x) -> str:
    if x is None or pd.isna(x):
        return ""
    return str(x).strip()


def is_blank(x) -> bool:
    return clean_str(x) == ""


def require_columns(df: pd.DataFrame, required: Set[str], label: str) -> None:
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{label} missing required columns: {sorted(missing)}")


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


def join_pipe_delimited(values: Iterable[str]) -> str:
    seen: Set[str] = set()
    out: List[str] = []
    for value in values:
        value = clean_str(value)
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return " | ".join(out)


def serialize_morphology_matches(matches: Dict[str, List[str]]) -> str:
    if not matches:
        return ""
    return json.dumps(matches, ensure_ascii=False, sort_keys=True)


def load_synonym_expansions(synonyms_df: pd.DataFrame) -> Dict[str, List[str]]:
    require_columns(
        synonyms_df,
        {"input_word", "synonyms_expansion"},
        "Synonyms CSV",
    )

    grouped: Dict[str, List[str]] = defaultdict(list)
    for _, row in synonyms_df.iterrows():
        source = normalize_text(row["input_word"])
        target = normalize_text(row["synonyms_expansion"])
        if source and target and target not in grouped[source]:
            grouped[source].append(target)
    return dict(grouped)


def build_morphology_index(oncotree_df: pd.DataFrame) -> List[Tuple[str, str, str]]:
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


def collect_triggered_expansions(text: str, synonym_expansions: Dict[str, List[str]]) -> List[str]:
    norm_text = normalize_text(text)
    if not norm_text:
        return []

    triggered: List[str] = []
    seen: Set[str] = set()
    for source, expansions in synonym_expansions.items():
        if not contains_whole_phrase(source, norm_text):
            continue
        for expansion in expansions:
            if expansion not in seen:
                seen.add(expansion)
                triggered.append(expansion)
    return triggered


def find_morphology_matches_by_expansions(
    expansions: Sequence[str],
    morphology_index: List[Tuple[str, str, str]],
) -> Dict[str, List[str]]:
    if not expansions:
        return {}

    grouped: Dict[str, Set[str]] = defaultdict(set)
    for expansion in expansions:
        norm_expansion = normalize_text(expansion)
        if not norm_expansion:
            continue
        for norm_oncotree_term, level_1, original_term in morphology_index:
            if contains_whole_phrase(norm_expansion, norm_oncotree_term):
                grouped[level_1].add(original_term)

    return {
        level_1: sorted(terms)
        for level_1, terms in sorted(grouped.items())
    }


def collect_intersection_terms(
    topography_lvl1_match: str,
    morphology_matches_by_lvl1_json: str,
) -> List[str]:
    topo_keys = split_pipe_delimited(topography_lvl1_match)
    morph_json = clean_str(morphology_matches_by_lvl1_json)
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
    cleaned_terms = [clean_str(term) for term in terms if clean_str(term)]
    if len(cleaned_terms) <= 1:
        return cleaned_terms

    term_set = set(cleaned_terms)
    to_remove: Set[str] = set()
    for term in cleaned_terms:
        for ancestor in ancestors_by_term.get(term, set()):
            if ancestor in term_set:
                to_remove.add(ancestor)
    return [term for term in cleaned_terms if term not in to_remove]


def refine_terms_by_synonyms(
    candidate_terms: List[str],
    topography_simplified: str,
    morphology_simplified: str,
    synonym_expansions: Dict[str, List[str]],
) -> List[str]:
    if not candidate_terms:
        return []

    triggered_targets = set(collect_triggered_expansions(topography_simplified, synonym_expansions))
    triggered_targets.update(collect_triggered_expansions(morphology_simplified, synonym_expansions))
    if not triggered_targets:
        return candidate_terms

    candidate_norm = [(term, normalize_text(term)) for term in candidate_terms]
    non_empty_subsets: List[List[str]] = []

    for target in sorted(triggered_targets):
        subset = [
            term
            for term, norm_term in candidate_norm
            if contains_whole_phrase(target, norm_term)
        ]
        if subset:
            non_empty_subsets.append(subset)

    if not non_empty_subsets:
        return candidate_terms

    intersected = set(non_empty_subsets[0])
    for subset in non_empty_subsets[1:]:
        intersected &= set(subset)

    if not intersected:
        return candidate_terms

    return [term for term in candidate_terms if term in intersected]


def process_csv(
    input_csv: Path,
    oncotree_csv: Path,
    synonyms_csv: Path,
    output_csv: Path,
) -> None:
    df = pd.read_csv(input_csv)
    oncotree_df = pd.read_csv(oncotree_csv)
    synonyms_df = pd.read_csv(synonyms_csv)

    require_columns(
        df,
        {
            "topography_simplified",
            "morphology_simplified",
            "topography_oncotree_lvl1_exact_match",
            "morphology_oncotree_exact_matches_by_lvl1",
        },
        "Input CSV",
    )

    synonym_expansions = load_synonym_expansions(synonyms_df)
    morphology_index = build_morphology_index(oncotree_df)
    ancestors_by_term = build_ancestor_index(oncotree_df)

    out = df.copy()
    out["morphology_oncotree_synonym_matches_by_lvl1"] = ""
    out["topography_morphology_synonym_match"] = ""
    out["topography_morphology_synonym_match_pruned"] = ""
    out["topography_morphology_synonym_match_pruned_refined"] = ""

    eligible_mask = (
        ~out["topography_oncotree_lvl1_exact_match"].apply(is_blank)
        & out["morphology_oncotree_exact_matches_by_lvl1"].apply(is_blank)
    )

    def process_row(row: pd.Series) -> pd.Series:
        expansions = collect_triggered_expansions(row["morphology_simplified"], synonym_expansions)
        morph_matches = find_morphology_matches_by_expansions(expansions, morphology_index)
        morph_matches_json = serialize_morphology_matches(morph_matches)

        intersection_terms = collect_intersection_terms(
            topography_lvl1_match=row["topography_oncotree_lvl1_exact_match"],
            morphology_matches_by_lvl1_json=morph_matches_json,
        )
        pruned_terms = prune_ancestor_terms(intersection_terms, ancestors_by_term)
        refined_terms = refine_terms_by_synonyms(
            candidate_terms=pruned_terms,
            topography_simplified=row["topography_simplified"],
            morphology_simplified=row["morphology_simplified"],
            synonym_expansions=synonym_expansions,
        )

        return pd.Series(
            {
                "morphology_oncotree_synonym_matches_by_lvl1": morph_matches_json,
                "topography_morphology_synonym_match": join_pipe_delimited(intersection_terms),
                "topography_morphology_synonym_match_pruned": join_pipe_delimited(pruned_terms),
                "topography_morphology_synonym_match_pruned_refined": join_pipe_delimited(refined_terms),
            }
        )

    if eligible_mask.any():
        out.loc[eligible_mask, [
            "morphology_oncotree_synonym_matches_by_lvl1",
            "topography_morphology_synonym_match",
            "topography_morphology_synonym_match_pruned",
            "topography_morphology_synonym_match_pruned_refined",
        ]] = out.loc[eligible_mask].apply(process_row, axis=1)

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output_csv, index=False)

    logger.info("Wrote %d rows to %s", len(out), output_csv)
    logger.info("Eligible rows: %d", int(eligible_mask.sum()))
    logger.info(
        "morphology_oncotree_synonym_matches_by_lvl1 populated: %d",
        int((out["morphology_oncotree_synonym_matches_by_lvl1"] != "").sum()),
    )
    logger.info(
        "topography_morphology_synonym_match populated: %d",
        int((out["topography_morphology_synonym_match"] != "").sum()),
    )
    logger.info(
        "topography_morphology_synonym_match_pruned populated: %d",
        int((out["topography_morphology_synonym_match_pruned"] != "").sum()),
    )
    logger.info(
        "topography_morphology_synonym_match_pruned_refined populated: %d",
        int((out["topography_morphology_synonym_match_pruned_refined"] != "").sum()),
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input_csv", required=True, type=Path)
    parser.add_argument("--oncotree_csv", required=True, type=Path)
    parser.add_argument("--synonyms_csv", required=True, type=Path)
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
        synonyms_csv=args.synonyms_csv,
        output_csv=args.output_csv,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
