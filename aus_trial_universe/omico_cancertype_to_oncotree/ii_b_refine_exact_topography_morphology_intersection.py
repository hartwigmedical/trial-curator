from __future__ import annotations

import argparse
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set

import pandas as pd

logger = logging.getLogger(__name__)

STOPWORDS = {"of", "the", "and"}


@dataclass(frozen=True)
class SynonymRule:
    source_term: str
    target_term: str


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


def require_columns(df: pd.DataFrame, required: Set[str], label: str) -> None:
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{label} missing required columns: {sorted(missing)}")


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


def load_synonym_rules(synonyms_df: pd.DataFrame) -> List[SynonymRule]:
    require_columns(
        synonyms_df,
        {"input_word", "synonyms_expansion"},
        "Synonyms CSV",
    )

    rules: List[SynonymRule] = []
    seen: Set[tuple[str, str]] = set()

    for _, row in synonyms_df.iterrows():
        source_term = normalize_text(row["input_word"])
        target_term = normalize_text(row["synonyms_expansion"])

        if not source_term or not target_term:
            continue

        key = (source_term, target_term)
        if key in seen:
            continue

        seen.add(key)
        rules.append(SynonymRule(source_term=source_term, target_term=target_term))

    return rules


def collect_triggered_targets(
    topography_simplified: str,
    morphology_simplified: str,
    rules: List[SynonymRule],
) -> List[str]:
    topo_norm = normalize_text(topography_simplified)
    morph_norm = normalize_text(morphology_simplified)

    triggered_targets: List[str] = []
    seen_targets: Set[str] = set()

    for rule in rules:
        if contains_whole_phrase(rule.source_term, topo_norm) or contains_whole_phrase(
            rule.source_term, morph_norm
        ):
            if rule.target_term not in seen_targets:
                seen_targets.add(rule.target_term)
                triggered_targets.append(rule.target_term)

    return triggered_targets


def filter_candidates_by_target(candidates: List[str], target_term: str) -> List[str]:
    matches: List[str] = []

    for candidate in candidates:
        if contains_whole_phrase(target_term, normalize_text(candidate)):
            matches.append(candidate)

    return matches


def refine_pruned_matches(
    topography_simplified: str,
    morphology_simplified: str,
    exact_match_topography_morphology_match_pruned: str,
    rules: List[SynonymRule],
) -> str:
    candidates = split_pipe_delimited(exact_match_topography_morphology_match_pruned)
    if not candidates:
        return ""

    triggered_targets = collect_triggered_targets(
        topography_simplified=topography_simplified,
        morphology_simplified=morphology_simplified,
        rules=rules,
    )
    if not triggered_targets:
        return join_pipe_delimited(candidates)

    candidate_set = set(candidates)
    any_nonempty_filter = False

    for target_term in triggered_targets:
        matched_subset = filter_candidates_by_target(candidates, target_term)
        if not matched_subset:
            continue

        any_nonempty_filter = True
        candidate_set &= set(matched_subset)

        if not candidate_set:
            return join_pipe_delimited(candidates)

    if not any_nonempty_filter:
        return join_pipe_delimited(candidates)

    refined_candidates = [candidate for candidate in candidates if candidate in candidate_set]
    return join_pipe_delimited(refined_candidates)


def process_csv(
    input_csv: Path,
    synonyms_csv: Path,
    output_csv: Path,
) -> None:
    df = pd.read_csv(input_csv)
    synonyms_df = pd.read_csv(synonyms_csv)

    require_columns(
        df,
        {
            "topography_simplified",
            "morphology_simplified",
            "topography_morphology_exact_match_pruned",
        },
        "Input CSV",
    )

    rules = load_synonym_rules(synonyms_df)

    out = df.copy()
    out["topography_morphology_exact_match_pruned_refined"] = out.apply(
        lambda row: refine_pruned_matches(
            topography_simplified=row["topography_simplified"],
            morphology_simplified=row["morphology_simplified"],
            exact_match_topography_morphology_match_pruned=row[
                "topography_morphology_exact_match_pruned"
            ],
            rules=rules,
        ),
        axis=1,
    )

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output_csv, index=False)

    n_total = len(out)
    n_scope = int((out["topography_morphology_exact_match_pruned"] != "").sum())
    n_refined_nonblank = int(
        (out["topography_morphology_exact_match_pruned_refined"] != "").sum()
    )
    n_changed = int(
        (
            out["topography_morphology_exact_match_pruned_refined"]
            != out["topography_morphology_exact_match_pruned"]
        ).sum()
    )

    logger.info("Loaded %d synonym rules", len(rules))
    logger.info("Wrote %d rows to %s", n_total, output_csv)
    logger.info("Rows with non-blank topography_morphology_exact_match_pruned: %d", n_scope)
    logger.info(
        "Rows with non-blank topography_morphology_exact_match_pruned_refined: %d",
        n_refined_nonblank,
    )
    logger.info("Rows changed by synonym refinement: %d", n_changed)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input_csv", required=True, type=Path)
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
        synonyms_csv=args.synonyms_csv,
        output_csv=args.output_csv,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
