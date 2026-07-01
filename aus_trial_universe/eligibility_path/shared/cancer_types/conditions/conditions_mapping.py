from __future__ import annotations

import argparse
import ast
import csv
import logging
from pathlib import Path
from typing import Dict, Iterable, List, Literal, Mapping, Optional, Sequence, Tuple

import pandas as pd

from aus_trial_universe.eligibility_path.shared.utils.pipeline_io import (
    dated_file_sort_key,
)
from aus_trial_universe.eligibility_path.shared.utils.text_normalisation import (
    blank_safe_str,
)

logger = logging.getLogger(__name__)

ConditionParser = Literal["auto", "python_list", "pipe"]

SUPPORTED_TABULAR_SUFFIXES = {".csv", ".tsv", ".xlsx", ".xls"}
CONDITIONS_LOOKUP_COLUMN = "Conditions_lookup"
ONCOTREE_CURATION_COLUMN = "Oncotree_curation"
OUTPUT_COLUMNS: Sequence[str] = (
    "trial_id",
    "conditions_original",
    "conditions_oncotree_curation",
)


def normalize_cell(value: object) -> str:
    return blank_safe_str(value).strip()


def normalize_key(value: object) -> str:
    return normalize_cell(value).casefold()


def read_tabular_file(path: Path) -> pd.DataFrame:
    suffix = path.suffix.casefold()

    if suffix == ".csv":
        return pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[])
    if suffix == ".tsv":
        return pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False, na_values=[])
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path, dtype=str, keep_default_na=False, na_values=[])

    raise ValueError(f"Unsupported input file type: {path.suffix}")


def find_column_case_insensitive(columns: Iterable[object], candidates: Sequence[str]) -> str:
    normalized_to_original = {
        normalize_key(column): str(column).strip()
        for column in columns
    }

    for candidate in candidates:
        normalized = normalize_key(candidate)
        if normalized in normalized_to_original:
            return normalized_to_original[normalized]

    raise ValueError(
        f"Could not find column. Tried candidates: {list(candidates)}. "
        f"Available columns: {[str(column) for column in columns]}"
    )


def find_conditions_mapping_file(mapping_dir: Path) -> Path:
    matches = [
        path
        for path in mapping_dir.iterdir()
        if path.is_file()
        and not path.name.startswith("~$")
        and path.suffix.casefold() in SUPPORTED_TABULAR_SUFFIXES
        and "condition" in path.stem.casefold()
    ]

    if not matches:
        raise FileNotFoundError(f"Could not find conditions mapping file in {mapping_dir}")

    return max(matches, key=dated_file_sort_key)


def load_mapping(mapping_file: Path) -> Tuple[Dict[str, str], Dict[str, str]]:
    df = read_tabular_file(mapping_file)
    df.columns = [str(column).strip() for column in df.columns]

    lookup_col = find_column_case_insensitive(df.columns, [CONDITIONS_LOOKUP_COLUMN])
    value_col = find_column_case_insensitive(df.columns, [ONCOTREE_CURATION_COLUMN])

    mapping: Dict[str, str] = {}
    mapping_ci: Dict[str, str] = {}

    for _, row in df.iterrows():
        key = normalize_cell(row.get(lookup_col, ""))
        value = normalize_cell(row.get(value_col, ""))
        if not key:
            continue

        mapping[key] = value
        mapping_ci[normalize_key(key)] = value

    return mapping, mapping_ci


def parse_python_list_conditions(raw: object) -> List[str]:
    text = normalize_cell(raw)
    if not text:
        return []

    try:
        parsed = ast.literal_eval(text)
    except Exception:
        return []

    if isinstance(parsed, list):
        return [term for term in (normalize_cell(item) for item in parsed) if term]

    return []


def parse_pipe_conditions(raw: object) -> List[str]:
    text = normalize_cell(raw)
    if not text:
        return []
    return [part.strip() for part in text.split("|") if part.strip()]


def parse_conditions(raw: object, parser: ConditionParser) -> List[str]:
    if parser == "python_list":
        return parse_python_list_conditions(raw)
    if parser == "pipe":
        return parse_pipe_conditions(raw)
    if parser != "auto":
        raise ValueError(f"Unsupported condition parser: {parser!r}")

    python_terms = parse_python_list_conditions(raw)
    if python_terms:
        return python_terms
    return parse_pipe_conditions(raw)


def map_condition_terms(
    terms: Sequence[str],
    *,
    mapping: Mapping[str, str],
    mapping_ci: Mapping[str, str],
) -> str:
    mapped_terms: List[str] = []

    for term in terms:
        mapped = normalize_cell(mapping.get(term) or mapping_ci.get(normalize_key(term)) or "")
        if mapped:
            mapped_terms.append(mapped)

    return " | ".join(dict.fromkeys(mapped_terms))


def build_conditions_mapping_table(
    input_df: pd.DataFrame,
    *,
    trial_id_column: str,
    conditions_column: str,
    mapping: Mapping[str, str],
    mapping_ci: Mapping[str, str],
    parser: ConditionParser,
) -> pd.DataFrame:
    input_df = input_df.copy()
    input_df.columns = [str(column).strip() for column in input_df.columns]

    trial_id_col = find_column_case_insensitive(input_df.columns, [trial_id_column])
    conditions_col = find_column_case_insensitive(input_df.columns, [conditions_column])

    rows: List[dict[str, str]] = []
    for _, row in input_df.iterrows():
        trial_id = normalize_cell(row.get(trial_id_col, "")).upper()
        raw_conditions = normalize_cell(row.get(conditions_col, ""))
        terms = parse_conditions(raw_conditions, parser)
        curated = map_condition_terms(terms, mapping=mapping, mapping_ci=mapping_ci)

        rows.append(
            {
                "trial_id": trial_id,
                "conditions_original": raw_conditions,
                "conditions_oncotree_curation": curated,
            }
        )

    return pd.DataFrame(rows, columns=list(OUTPUT_COLUMNS))


def process(
    *,
    input_file: Path,
    mapping_dir: Path,
    output_csv: Path,
    trial_id_column: str,
    conditions_column: str,
    parser: ConditionParser = "auto",
) -> None:
    mapping_file = find_conditions_mapping_file(mapping_dir)
    logger.info("Using conditions mapping file: %s", mapping_file)

    mapping, mapping_ci = load_mapping(mapping_file)
    input_df = read_tabular_file(input_file)

    out = build_conditions_mapping_table(
        input_df,
        trial_id_column=trial_id_column,
        conditions_column=conditions_column,
        mapping=mapping,
        mapping_ci=mapping_ci,
        parser=parser,
    )

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(OUTPUT_COLUMNS))
        writer.writeheader()
        writer.writerows(out.to_dict("records"))

    logger.info("Wrote %d rows to %s", len(out), output_csv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Map registry-level condition text to OncoTree using the shared "
            "conditions curation resource."
        )
    )
    parser.add_argument("--input_file", required=True, type=Path)
    parser.add_argument("--mapping_dir", required=True, type=Path)
    parser.add_argument("--output_csv", required=True, type=Path)
    parser.add_argument("--trial_id_column", required=True)
    parser.add_argument("--conditions_column", required=True)
    parser.add_argument(
        "--parser",
        default="auto",
        choices=["auto", "python_list", "pipe"],
        help="How to parse condition terms from the source column.",
    )
    parser.add_argument("--log_level", default="INFO")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    process(
        input_file=args.input_file,
        mapping_dir=args.mapping_dir,
        output_csv=args.output_csv,
        trial_id_column=args.trial_id_column,
        conditions_column=args.conditions_column,
        parser=args.parser,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
