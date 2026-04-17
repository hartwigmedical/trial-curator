#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

import pandas as pd


GENE_LOOKUP_COL = "TrialGeneticMatches"
MOLECULAR_LOOKUP_COL = "TrialMolecularMatchesRaw"
TRIAL_ID_COL = "TrialId"

GENE_OUTPUT_TEXT_COL = "TrialGeneticRetrievedRuleTexts"
GENE_OUTPUT_STATUS_COL = "TrialGeneticRetrieveStatus"
MOLECULAR_OUTPUT_TEXT_COL = "TrialMolecularRetrievedRuleTexts"
MOLECULAR_OUTPUT_STATUS_COL = "TrialMolecularRetrieveStatus"

# The lookup columns in the TSV use comma+space between matched criteria.
LOOKUP_OUTPUT_DELIMITER = ", "
# When a single lookup term maps to multiple rule_text values, keep them grouped
# inside that position using a distinct inner delimiter.
MULTI_RULETEXT_DELIMITER = " || "

GENE_CLASS_NAME = "GeneAlterationCriterion"
GENE_FIELD_NAME = "gene_alteration_curation"
MOLECULAR_CLASS_NAME = "MolecularSignatureCriterion"
MOLECULAR_FIELD_NAME = "molecular_signature_curation"

STATUS_NO_LOOKUP_VALUE = "NO_LOOKUP_VALUE"
STATUS_RULE_FILE_NOT_FOUND = "RULE_FILE_NOT_FOUND"
STATUS_NO_MATCH_IN_RULES = "NO_MATCH_IN_RULES"
STATUS_PARTIAL_MATCH_IN_RULES = "PARTIAL_MATCH_IN_RULES"
STATUS_MATCH_FOUND = "MATCH_FOUND"
STATUS_RULE_PARSE_ERROR = "RULE_PARSE_ERROR"


@dataclass(frozen=True)
class CriterionRecord:
    class_name: str
    curation_value: str


@dataclass(frozen=True)
class RuleRecord:
    rule_text: str
    criteria: tuple[CriterionRecord, ...]


@dataclass(frozen=True)
class ModeConfig:
    lookup_col: str
    output_text_col: str
    output_status_col: str
    criterion_class_name: str
    criterion_field_name: str


GENE_MODE = ModeConfig(
    lookup_col=GENE_LOOKUP_COL,
    output_text_col=GENE_OUTPUT_TEXT_COL,
    output_status_col=GENE_OUTPUT_STATUS_COL,
    criterion_class_name=GENE_CLASS_NAME,
    criterion_field_name=GENE_FIELD_NAME,
)

MOLECULAR_MODE = ModeConfig(
    lookup_col=MOLECULAR_LOOKUP_COL,
    output_text_col=MOLECULAR_OUTPUT_TEXT_COL,
    output_status_col=MOLECULAR_OUTPUT_STATUS_COL,
    criterion_class_name=MOLECULAR_CLASS_NAME,
    criterion_field_name=MOLECULAR_FIELD_NAME,
)


def get_call_name(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def get_constant_str(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def split_top_level(text: str, delimiter: str) -> List[str]:
    """Split on delimiter only when not nested inside (), [], or {} and not in quotes."""
    if not text:
        return []

    parts: List[str] = []
    current: List[str] = []
    bracket_depth = 0
    paren_depth = 0
    brace_depth = 0
    in_single_quote = False
    in_double_quote = False
    escaped = False
    i = 0
    delim_len = len(delimiter)

    while i < len(text):
        ch = text[i]

        if escaped:
            current.append(ch)
            escaped = False
            i += 1
            continue

        if ch == "\\":
            current.append(ch)
            escaped = True
            i += 1
            continue

        if not in_double_quote and ch == "'":
            in_single_quote = not in_single_quote
            current.append(ch)
            i += 1
            continue

        if not in_single_quote and ch == '"':
            in_double_quote = not in_double_quote
            current.append(ch)
            i += 1
            continue

        if not in_single_quote and not in_double_quote:
            if ch == "[":
                bracket_depth += 1
            elif ch == "]":
                bracket_depth = max(0, bracket_depth - 1)
            elif ch == "(":
                paren_depth += 1
            elif ch == ")":
                paren_depth = max(0, paren_depth - 1)
            elif ch == "{":
                brace_depth += 1
            elif ch == "}":
                brace_depth = max(0, brace_depth - 1)

            if (
                bracket_depth == 0
                and paren_depth == 0
                and brace_depth == 0
                and text.startswith(delimiter, i)
            ):
                part = "".join(current).strip()
                if part:
                    parts.append(part)
                current = []
                i += delim_len
                continue

        current.append(ch)
        i += 1

    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def parse_lookup_terms(cell_value: str) -> List[str]:
    value = (cell_value or "").strip()
    if not value:
        return []
    return split_top_level(value, LOOKUP_OUTPUT_DELIMITER)


def split_rule_expression_into_atomic_terms(curation_value: str) -> List[str]:
    value = (curation_value or "").strip()
    if not value:
        return []
    return split_top_level(value, " | ") if " | " in value else split_top_level(value, "|")


def dedupe_preserve_order(values: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    deduped: List[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped


def extract_rule_records_from_source(source_text: str) -> List[RuleRecord]:
    tree = ast.parse(source_text)
    rule_records: List[RuleRecord] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if get_call_name(node.func) != "Rule":
            continue

        rule_text: Optional[str] = None
        criteria: List[CriterionRecord] = []

        for keyword in node.keywords:
            if keyword.arg == "rule_text":
                rule_text = get_constant_str(keyword.value)

        for nested in ast.walk(node):
            if not isinstance(nested, ast.Call):
                continue
            class_name = get_call_name(nested.func)
            if class_name is None or class_name == "Rule":
                continue
            for keyword in nested.keywords:
                if keyword.arg is None:
                    continue
                curation_value = get_constant_str(keyword.value)
                if curation_value is None:
                    continue
                criteria.append(
                    CriterionRecord(
                        class_name=class_name,
                        curation_value=curation_value,
                    )
                )

        if rule_text is None:
            continue

        rule_records.append(
            RuleRecord(
                rule_text=rule_text,
                criteria=tuple(criteria),
            )
        )

    return rule_records


def find_rule_file(rules_dir: Path, trial_id: str) -> Optional[Path]:
    exact_path = rules_dir / f"{trial_id}.py"
    if exact_path.exists():
        return exact_path

    matches = sorted(rules_dir.glob(f"{trial_id}*.py"))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        logging.warning(
            "Multiple rule files found for %s; using first match: %s",
            trial_id,
            matches[0].name,
        )
        return matches[0]
    return None


def retrieve_rule_texts_for_mode(
    lookup_cell_value: str,
    rule_records: Sequence[RuleRecord],
    mode: ModeConfig,
) -> tuple[str, str]:
    """
    Return output aligned to the lookup terms.

    Example:
      lookup terms: A, B, C
      output: text_for_A, text_for_B_1 || text_for_B_2, text_for_C

    The outer delimiter matches the lookup column delimiter, so the output remains
    positionally aligned with the input matched-criterion terms.
    """
    lookup_terms = parse_lookup_terms(lookup_cell_value)
    if not lookup_terms:
        return "", STATUS_NO_LOOKUP_VALUE

    per_term_outputs: List[str] = []
    matched_term_count = 0

    for lookup_term in lookup_terms:
        matched_rule_texts_for_term: List[str] = []

        for rule_record in rule_records:
            for criterion in rule_record.criteria:
                if criterion.class_name != mode.criterion_class_name:
                    continue

                atomic_terms = split_rule_expression_into_atomic_terms(criterion.curation_value)
                if lookup_term in atomic_terms:
                    matched_rule_texts_for_term.append(rule_record.rule_text)
                    break

        matched_rule_texts_for_term = dedupe_preserve_order(matched_rule_texts_for_term)

        if matched_rule_texts_for_term:
            matched_term_count += 1
            per_term_outputs.append(MULTI_RULETEXT_DELIMITER.join(matched_rule_texts_for_term))
        else:
            # Preserve positional alignment even when a term unexpectedly fails.
            per_term_outputs.append("")

    if matched_term_count == 0:
        return "", STATUS_NO_MATCH_IN_RULES

    if matched_term_count < len(lookup_terms):
        return LOOKUP_OUTPUT_DELIMITER.join(per_term_outputs), STATUS_PARTIAL_MATCH_IN_RULES

    return LOOKUP_OUTPUT_DELIMITER.join(per_term_outputs), STATUS_MATCH_FOUND


def process_trial_rows(df: pd.DataFrame, rules_dir: Path) -> pd.DataFrame:
    output_df = df.copy()
    output_df[GENE_OUTPUT_TEXT_COL] = ""
    output_df[GENE_OUTPUT_STATUS_COL] = ""
    output_df[MOLECULAR_OUTPUT_TEXT_COL] = ""
    output_df[MOLECULAR_OUTPUT_STATUS_COL] = ""

    cache: dict[str, tuple[Optional[List[RuleRecord]], str]] = {}

    for idx, row in output_df.iterrows():
        trial_id = str(row.get(TRIAL_ID_COL, "") or "").strip()

        gene_lookup = str(row.get(GENE_LOOKUP_COL, "") or "").strip()
        molecular_lookup = str(row.get(MOLECULAR_LOOKUP_COL, "") or "").strip()

        if not trial_id:
            output_df.at[idx, GENE_OUTPUT_STATUS_COL] = (
                STATUS_RULE_FILE_NOT_FOUND if gene_lookup else STATUS_NO_LOOKUP_VALUE
            )
            output_df.at[idx, MOLECULAR_OUTPUT_STATUS_COL] = (
                STATUS_RULE_FILE_NOT_FOUND if molecular_lookup else STATUS_NO_LOOKUP_VALUE
            )
            continue

        if trial_id not in cache:
            rule_file = find_rule_file(rules_dir, trial_id)
            if rule_file is None:
                cache[trial_id] = (None, STATUS_RULE_FILE_NOT_FOUND)
            else:
                try:
                    rule_records = extract_rule_records_from_source(rule_file.read_text(encoding="utf-8"))
                    cache[trial_id] = (rule_records, "OK")
                except SyntaxError:
                    logging.exception("Failed to parse rule file for %s: %s", trial_id, rule_file)
                    cache[trial_id] = (None, STATUS_RULE_PARSE_ERROR)
                except Exception:
                    logging.exception("Unexpected error while reading rule file for %s: %s", trial_id, rule_file)
                    cache[trial_id] = (None, STATUS_RULE_PARSE_ERROR)

        rule_records, cache_status = cache[trial_id]

        for lookup_value, mode in (
            (gene_lookup, GENE_MODE),
            (molecular_lookup, MOLECULAR_MODE),
        ):
            if not lookup_value:
                output_df.at[idx, mode.output_text_col] = ""
                output_df.at[idx, mode.output_status_col] = STATUS_NO_LOOKUP_VALUE
                continue

            if cache_status != "OK" or rule_records is None:
                output_df.at[idx, mode.output_text_col] = ""
                output_df.at[idx, mode.output_status_col] = cache_status
                continue

            retrieved_texts, status = retrieve_rule_texts_for_mode(
                lookup_cell_value=lookup_value,
                rule_records=rule_records,
                mode=mode,
            )
            output_df.at[idx, mode.output_text_col] = retrieved_texts
            output_df.at[idx, mode.output_status_col] = status

    return output_df


def validate_input_columns(df: pd.DataFrame) -> None:
    missing = [
        column
        for column in (
            TRIAL_ID_COL,
            GENE_LOOKUP_COL,
            MOLECULAR_LOOKUP_COL,
        )
        if column not in df.columns
    ]
    if missing:
        raise ValueError(f"Missing required input columns: {', '.join(missing)}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Retrieve original rule_text values for matched gene alteration and "
            "molecular signature criteria from curated trial rule Python files."
        )
    )
    parser.add_argument(
        "--input_tsv",
        required=True,
        type=Path,
        help="Input TSV containing TrialId, TrialGeneticMatches, and TrialMolecularMatchesRaw.",
    )
    parser.add_argument(
        "--rules_dir",
        required=True,
        type=Path,
        help="Directory containing curated NCT*.py rule files.",
    )
    parser.add_argument(
        "--output_tsv",
        required=True,
        type=Path,
        help="Output TSV path.",
    )
    parser.add_argument(
        "--log_level",
        default="INFO",
        help="Logging level (default: INFO).",
    )
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    logging.info("Reading input TSV: %s", args.input_tsv)
    df = pd.read_csv(args.input_tsv, sep="\t", dtype=str).fillna("")
    validate_input_columns(df)

    logging.info("Processing %d rows using rules directory: %s", len(df), args.rules_dir)
    output_df = process_trial_rows(df, args.rules_dir)

    args.output_tsv.parent.mkdir(parents=True, exist_ok=True)
    logging.info("Writing output TSV: %s", args.output_tsv)
    output_df.to_csv(args.output_tsv, sep="\t", index=False)

    logging.info("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
