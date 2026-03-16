from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import pandas as pd

from i_interventions_processing import (
    COL_PROCESSED,
    normalize_processed_row,
    split_top_level_pipes,
)

REF_DRUG_COL = "drug"
REF_CLASS_COL = "drug_class"

OUTPUT_CT_COL = "ctgov_drugs"
OUTPUT_POTTR_DRUG_COL = "pottr_drug"
OUTPUT_CLASS_COL = "pottr_drug_class"

OUTPUT_FILENAME = "ctgov_to_pottr_drug_class_with_hierarchy.xlsx"
MATCHED_SHEET_NAME = "matched_ctgov_to_pottr"
UNMATCHED_SUMMARY_SHEET_NAME = "unique_unmatched_with_hierarchy"
UNMATCHED_UNIQUE_COL = "unmatched_pottr_drug_class"

logger = logging.getLogger(__name__)


def ordered_unique(values: Sequence[str]) -> List[str]:
    seen: dict[str, None] = {}
    for value in values:
        cleaned = str(value).strip()
        if cleaned and cleaned not in seen:
            seen[cleaned] = None
    return list(seen.keys())


def normalize_pipe_separated_terms(value: str) -> List[str]:
    if not value:
        return []

    normalized = normalize_processed_row(str(value).strip())
    return [term for term in split_top_level_pipes(normalized) if term]


def read_ctgov_xlsx(ctgov_xlsx: Path) -> pd.DataFrame:
    df = pd.read_excel(ctgov_xlsx, dtype="string")

    if COL_PROCESSED not in df.columns:
        raise KeyError(
            f"Required column '{COL_PROCESSED}' not found in {ctgov_xlsx}. "
            f"Columns present: {list(df.columns)}"
        )

    return df[[COL_PROCESSED]].copy()


def read_drug_database(drug_database_txt: Path) -> pd.DataFrame:
    df = pd.read_csv(
        drug_database_txt,
        sep="\t",
        dtype="string",
        keep_default_na=False,
    )

    missing = {REF_DRUG_COL, REF_CLASS_COL} - set(df.columns)
    if missing:
        raise KeyError(
            f"Reference file missing required columns: {sorted(missing)}. "
            f"Columns present: {list(df.columns)}"
        )

    return df[[REF_DRUG_COL, REF_CLASS_COL]].copy()


def read_drug_class_hierarchy(
    hierarchy_txt: Path,
) -> Tuple[Dict[str, List[List[str]]], int]:
    """
    Read a ragged tab-delimited hierarchy file where each row is a lineage from
    broader class to more specific class.

    Returns:
        class_to_paths:
            normalized_class_term -> list of ancestor paths
            each path is ordered from broader to more specific, excluding the class itself
        max_ancestor_depth:
            maximum number of ancestor levels seen across all paths
    """
    class_to_paths: Dict[str, List[List[str]]] = {}
    max_ancestor_depth = 0

    with hierarchy_txt.open("r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.rstrip("\n\r")
            if not line.strip():
                continue

            raw_terms = [part.strip() for part in line.split("\t")]
            raw_terms = [term for term in raw_terms if term]
            if not raw_terms:
                continue

            normalized_lineage_cells: List[str] = []
            for raw_term in raw_terms:
                normalized_term = normalize_processed_row(raw_term)
                if normalized_term:
                    normalized_lineage_cells.append(normalized_term)

            if not normalized_lineage_cells:
                continue

            for idx, normalized_term in enumerate(normalized_lineage_cells):
                ancestors = normalized_lineage_cells[:idx]
                max_ancestor_depth = max(max_ancestor_depth, len(ancestors))

                aliases = split_top_level_pipes(normalized_term)
                for alias in aliases:
                    if not alias:
                        continue
                    class_to_paths.setdefault(alias, [])
                    if ancestors not in class_to_paths[alias]:
                        class_to_paths[alias].append(list(ancestors))

    return class_to_paths, max_ancestor_depth


def build_alias_to_pottr_records(reference_df: pd.DataFrame) -> Dict[str, List[Dict[str, str]]]:
    """
    Build:
        normalized_drug_alias -> ordered list of matched POTTR records

    Each POTTR record preserves:
    - pottr_drug: original 'drug' cell
    - pottr_drug_class_raw: original 'drug_class' cell
    """
    alias_to_records: Dict[str, List[Dict[str, str]]] = {}

    for row in reference_df.itertuples(index=False):
        raw_drug = str(getattr(row, REF_DRUG_COL)).strip()
        raw_class = str(getattr(row, REF_CLASS_COL)).strip()

        if not raw_drug or not raw_class:
            continue

        normalized_aliases = normalize_pipe_separated_terms(raw_drug)
        record = {
            OUTPUT_POTTR_DRUG_COL: raw_drug,
            "pottr_drug_class_raw": raw_class,
        }

        for alias in normalized_aliases:
            alias_to_records.setdefault(alias, [])
            if record not in alias_to_records[alias]:
                alias_to_records[alias].append(record)

    return alias_to_records


def format_hierarchy_levels(
    ancestors: Sequence[str],
    max_ancestor_depth: int,
) -> Dict[str, str]:
    return {
        f"{OUTPUT_CLASS_COL}_level_{level}": (
            ancestors[level - 1] if level <= len(ancestors) else ""
        )
        for level in range(1, max_ancestor_depth + 1)
    }


def build_matched_df(
    ctgov_df: pd.DataFrame,
    alias_to_pottr_records: Dict[str, List[Dict[str, str]]],
    class_to_paths: Dict[str, List[List[str]]],
    max_ancestor_depth: int,
) -> pd.DataFrame:
    rows: List[Dict[str, str]] = []
    hierarchy_cols = [
        f"{OUTPUT_CLASS_COL}_level_{level}"
        for level in range(1, max_ancestor_depth + 1)
    ]

    for row in ctgov_df.itertuples(index=False):
        ctgov_value = getattr(row, COL_PROCESSED)
        ctgov_value = "" if pd.isna(ctgov_value) else str(ctgov_value).strip()

        normalized_ctgov_terms = normalize_pipe_separated_terms(ctgov_value)

        matched_records: List[Dict[str, str]] = []
        for term in normalized_ctgov_terms:
            for record in alias_to_pottr_records.get(term, []):
                if record not in matched_records:
                    matched_records.append(record)

        if not matched_records:
            blank_row = {
                OUTPUT_CT_COL: ctgov_value,
                OUTPUT_POTTR_DRUG_COL: "",
                OUTPUT_CLASS_COL: "",
            }
            blank_row.update({col: "" for col in hierarchy_cols})
            rows.append(blank_row)
            continue

        emitted_rows_for_ctgov: List[Dict[str, str]] = []

        for record in matched_records:
            pottr_drug = record[OUTPUT_POTTR_DRUG_COL]
            raw_class_cell = record["pottr_drug_class_raw"]

            class_terms = normalize_pipe_separated_terms(raw_class_cell)
            if not class_terms:
                row_out = {
                    OUTPUT_CT_COL: ctgov_value,
                    OUTPUT_POTTR_DRUG_COL: pottr_drug,
                    OUTPUT_CLASS_COL: "",
                }
                row_out.update({col: "" for col in hierarchy_cols})
                if row_out not in emitted_rows_for_ctgov:
                    emitted_rows_for_ctgov.append(row_out)
                continue

            for class_term in class_terms:
                hierarchy_paths = class_to_paths.get(class_term, [])

                if not hierarchy_paths:
                    row_out = {
                        OUTPUT_CT_COL: ctgov_value,
                        OUTPUT_POTTR_DRUG_COL: pottr_drug,
                        OUTPUT_CLASS_COL: class_term,
                    }
                    row_out.update({col: "" for col in hierarchy_cols})
                    if row_out not in emitted_rows_for_ctgov:
                        emitted_rows_for_ctgov.append(row_out)
                    continue

                for ancestors in hierarchy_paths:
                    row_out = {
                        OUTPUT_CT_COL: ctgov_value,
                        OUTPUT_POTTR_DRUG_COL: pottr_drug,
                        OUTPUT_CLASS_COL: class_term,
                    }
                    row_out.update(format_hierarchy_levels(ancestors, max_ancestor_depth))
                    if row_out not in emitted_rows_for_ctgov:
                        emitted_rows_for_ctgov.append(row_out)

        rows.extend(emitted_rows_for_ctgov)

    columns = [
        OUTPUT_CT_COL,
        OUTPUT_POTTR_DRUG_COL,
        OUTPUT_CLASS_COL,
        *hierarchy_cols,
    ]
    matched_df = pd.DataFrame(rows, columns=columns)
    return blank_repeated_leading_cells(matched_df)


def blank_repeated_leading_cells(df: pd.DataFrame) -> pd.DataFrame:
    """
    Presentation formatting for worksheet 1 only.

    Blank repeated values in leading columns when consecutive rows belong to the
    same logical group:
    - blank ctgov_drugs if same as previous row
    - blank pottr_drug if ctgov_drugs and pottr_drug are same as previous row
    - blank pottr_drug_class if ctgov_drugs, pottr_drug, and pottr_drug_class are
      same as previous row
    """
    if df.empty:
        return df

    result_df = df.copy()
    prev_ctgov = None
    prev_pottr_drug = None
    prev_pottr_class = None

    for idx in result_df.index:
        curr_ctgov = result_df.at[idx, OUTPUT_CT_COL]
        curr_pottr_drug = result_df.at[idx, OUTPUT_POTTR_DRUG_COL]
        curr_pottr_class = result_df.at[idx, OUTPUT_CLASS_COL]

        same_ctgov = curr_ctgov == prev_ctgov
        same_pottr_drug = same_ctgov and curr_pottr_drug == prev_pottr_drug
        same_pottr_class = same_pottr_drug and curr_pottr_class == prev_pottr_class

        if same_ctgov:
            result_df.at[idx, OUTPUT_CT_COL] = ""
        if same_pottr_drug:
            result_df.at[idx, OUTPUT_POTTR_DRUG_COL] = ""
        if same_pottr_class:
            result_df.at[idx, OUTPUT_CLASS_COL] = ""

        prev_ctgov = curr_ctgov
        prev_pottr_drug = curr_pottr_drug
        prev_pottr_class = curr_pottr_class

    return result_df


def build_unmatched_classes_df(
    reference_df: pd.DataFrame,
    matched_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Unique POTTR drug classes that were never matched by any CTGov row.
    """
    all_pottr_classes: List[str] = []
    for value in reference_df[REF_CLASS_COL]:
        class_terms = normalize_pipe_separated_terms(str(value).strip())
        for class_term in class_terms:
            if class_term not in all_pottr_classes:
                all_pottr_classes.append(class_term)

    matched_classes: List[str] = []
    for value in matched_df[OUTPUT_CLASS_COL].fillna("").astype(str).tolist():
        cleaned = value.strip()
        if cleaned and cleaned not in matched_classes:
            matched_classes.append(cleaned)

    unmatched_classes = [
        class_term
        for class_term in all_pottr_classes
        if class_term not in matched_classes
    ]

    return pd.DataFrame({UNMATCHED_UNIQUE_COL: unmatched_classes})


def build_unique_unmatched_summary_df(
    unmatched_classes_df: pd.DataFrame,
    class_to_paths: Dict[str, List[List[str]]],
    max_ancestor_depth: int,
) -> pd.DataFrame:
    """
    Worksheet 2 semantics:

    col 1:
        all unique unmatched drug classes
    col 2:
        unique immediate parents of col 1 terms across all hierarchy paths
    col 3:
        unique immediate parents of col 2 terms across all hierarchy paths
    continue until no more parents remain

    Each column is an independent list. Rows are not intended to connect.
    """
    hierarchy_columns: List[List[str]] = []
    current_terms = ordered_unique(
        unmatched_classes_df[UNMATCHED_UNIQUE_COL].fillna("").astype(str).tolist()
    )

    if not current_terms:
        columns = [UNMATCHED_UNIQUE_COL] + [
            f"{UNMATCHED_UNIQUE_COL}_level_{level}"
            for level in range(1, max_ancestor_depth + 1)
        ]
        return pd.DataFrame(columns=columns)

    hierarchy_columns.append(current_terms)

    for _ in range(max_ancestor_depth):
        next_terms: List[str] = []

        for term in current_terms:
            normalized_terms = normalize_pipe_separated_terms(term)

            for normalized_term in normalized_terms:
                for ancestors in class_to_paths.get(normalized_term, []):
                    if ancestors:
                        immediate_parent = ancestors[-1]
                        if immediate_parent not in next_terms:
                            next_terms.append(immediate_parent)

        if not next_terms:
            break

        hierarchy_columns.append(next_terms)
        current_terms = next_terms

    data: Dict[str, pd.Series] = {
        UNMATCHED_UNIQUE_COL: pd.Series(hierarchy_columns[0], dtype="string")
    }

    for level_idx, values in enumerate(hierarchy_columns[1:], start=1):
        data[f"{UNMATCHED_UNIQUE_COL}_level_{level_idx}"] = pd.Series(
            values,
            dtype="string",
        )

    return pd.DataFrame(data)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Match CT.gov processed interventions to POTTR drugs and drug classes, "
            "then expand matched classes and unmatched class summaries through the "
            "POTTR drug class hierarchy."
        )
    )
    parser.add_argument(
        "--ctgov_processed_xlsx",
        required=True,
        help="Path to ctgov_interventions_processed.xlsx",
    )
    parser.add_argument(
        "--pottr_drug_database_txt",
        required=True,
        help="Path to POTTR drug database txt containing drug and drug_class columns",
    )
    parser.add_argument(
        "--pottr_drug_class_hierarchy_txt",
        required=True,
        help="Path to POTTR drug_class_hierarchy.txt",
    )
    parser.add_argument(
        "--output_dir",
        required=True,
        help="Directory where the output XLSX will be written",
    )
    parser.add_argument(
        "--log_level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging level",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    ctgov_path = Path(args.ctgov_processed_xlsx)
    drug_db_path = Path(args.pottr_drug_database_txt)
    hierarchy_path = Path(args.pottr_drug_class_hierarchy_txt)
    output_dir = Path(args.output_dir)

    if not ctgov_path.exists():
        raise FileNotFoundError(f"CTGov processed workbook not found: {ctgov_path}")

    if not drug_db_path.exists():
        raise FileNotFoundError(f"POTTR drug database not found: {drug_db_path}")

    if not hierarchy_path.exists():
        raise FileNotFoundError(f"POTTR drug class hierarchy file not found: {hierarchy_path}")

    logger.info("Reading CTGov processed interventions: %s", ctgov_path)
    ctgov_df = read_ctgov_xlsx(ctgov_path)

    logger.info("Reading POTTR drug database: %s", drug_db_path)
    ref_df = read_drug_database(drug_db_path)

    logger.info("Reading POTTR drug class hierarchy: %s", hierarchy_path)
    class_to_paths, max_ancestor_depth = read_drug_class_hierarchy(hierarchy_path)

    logger.info("Building drug alias to POTTR record lookup")
    alias_to_pottr_records = build_alias_to_pottr_records(ref_df)

    logger.info("Building matched worksheet")
    matched_df = build_matched_df(
        ctgov_df=ctgov_df,
        alias_to_pottr_records=alias_to_pottr_records,
        class_to_paths=class_to_paths,
        max_ancestor_depth=max_ancestor_depth,
    )

    logger.info("Building unmatched class list")
    unmatched_classes_df = build_unmatched_classes_df(
        reference_df=ref_df,
        matched_df=matched_df,
    )

    logger.info("Building unique unmatched summary worksheet")
    unique_unmatched_summary_df = build_unique_unmatched_summary_df(
        unmatched_classes_df=unmatched_classes_df,
        class_to_paths=class_to_paths,
        max_ancestor_depth=max_ancestor_depth,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / OUTPUT_FILENAME

    logger.info("Writing workbook: %s", output_path)
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        matched_df.to_excel(
            writer,
            sheet_name=MATCHED_SHEET_NAME,
            index=False,
        )
        unique_unmatched_summary_df.to_excel(
            writer,
            sheet_name=UNMATCHED_SUMMARY_SHEET_NAME,
            index=False,
        )

    logger.info("Wrote output workbook to %s", output_path)


if __name__ == "__main__":
    main()