from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set

import pandas as pd


logger = logging.getLogger(__name__)

SHEET_NAME = "processed interventions"
ATC_CODE_COL = "ATC code"
ATC_PREFIX = "atc_"
MULTI_VALUE_DELIMITER = " | "
RX_CODE_COL = "rx_code"
RX_SAB_COL = "rx_sab"


def unique_join(values: Sequence[object], delimiter: str = MULTI_VALUE_DELIMITER) -> str:
    ordered: List[str] = []
    seen: Set[str] = set()
    for value in values:
        if value is None or pd.isna(value):
            continue
        cleaned = str(value).strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        ordered.append(cleaned)
    return delimiter.join(ordered)


def join_preserve_order(values: Sequence[object], delimiter: str = MULTI_VALUE_DELIMITER) -> str:
    ordered: List[str] = []
    for value in values:
        if value is None or pd.isna(value):
            ordered.append("")
            continue
        ordered.append(str(value).strip())
    return delimiter.join(ordered)


def split_multi_value_cell(value: object) -> List[str]:
    if value is None or pd.isna(value):
        return []
    return [part.strip() for part in str(value).split(MULTI_VALUE_DELIMITER) if part.strip()]


def load_input_workbook(input_excel: Path, sheet_name: str) -> pd.DataFrame:
    if not input_excel.exists():
        raise FileNotFoundError(f"Input workbook not found: {input_excel}")

    xls = pd.ExcelFile(input_excel)
    if sheet_name not in xls.sheet_names:
        raise KeyError(f"Worksheet {sheet_name!r} not found. Available sheets: {xls.sheet_names}")

    df = pd.read_excel(input_excel, sheet_name=sheet_name, dtype="string")
    required = {RX_CODE_COL, RX_SAB_COL}
    missing = required - set(df.columns)
    if missing:
        raise KeyError(
            f"Input workbook missing required columns: {sorted(missing)}. "
            f"Available columns: {list(df.columns)}"
        )
    return df


def load_atc_tree(atc_tsv: Path) -> pd.DataFrame:
    if not atc_tsv.exists():
        raise FileNotFoundError(f"ATC TSV not found: {atc_tsv}")

    df = pd.read_csv(atc_tsv, sep="\t", dtype="string")
    if ATC_CODE_COL not in df.columns:
        raise KeyError(
            f"ATC TSV missing required column {ATC_CODE_COL!r}. "
            f"Available columns: {list(df.columns)}"
        )
    return df


def build_atc_tree_lookup(atc_df: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    lookup: Dict[str, pd.DataFrame] = {}
    for atc_code, group in atc_df.groupby(ATC_CODE_COL, dropna=False, sort=False):
        key = "" if atc_code is None else str(atc_code).strip()
        if not key:
            continue
        lookup[key] = group.copy()
    return lookup


def build_blank_atc_payload(atc_columns: Sequence[str]) -> Dict[str, str]:
    payload = {f"{ATC_PREFIX}{column}": "" for column in atc_columns}
    payload.update(
        {
            "atc_code_count": "",
            "atc_match_status": "",
        }
    )
    return payload


def atc_source_absent(rx_sab_values: Set[str]) -> bool:
    return "ATC" not in rx_sab_values


def extract_matching_atc_codes(record: Dict[str, object], valid_atc_codes: Set[str]) -> List[str]:
    rx_sab_values = set(split_multi_value_cell(record.get(RX_SAB_COL, "")))
    rx_code_values = split_multi_value_cell(record.get(RX_CODE_COL, ""))

    if atc_source_absent(rx_sab_values):
        return []

    matched_codes: List[str] = []
    seen: Set[str] = set()
    for code in rx_code_values:
        if code in valid_atc_codes and code not in seen:
            seen.add(code)
            matched_codes.append(code)
    return matched_codes


def aggregate_single_code_group(group: pd.DataFrame, atc_tree_columns: Sequence[str]) -> Dict[str, str]:
    aggregated: Dict[str, str] = {}
    for column in atc_tree_columns:
        aggregated[column] = unique_join(group[column].tolist(), delimiter=MULTI_VALUE_DELIMITER)
    return aggregated


def build_code_level_records(
    matched_atc_codes: Sequence[str],
    atc_tree_lookup: Dict[str, pd.DataFrame],
    atc_tree_columns: Sequence[str],
) -> List[Dict[str, str]]:
    records: List[Dict[str, str]] = []
    for code in matched_atc_codes:
        if code not in atc_tree_lookup:
            continue
        group = atc_tree_lookup[code]
        records.append(aggregate_single_code_group(group, atc_tree_columns))
    return records


def build_flattened_atc_payload(
    code_level_records: Sequence[Dict[str, str]],
    atc_tree_columns: Sequence[str],
) -> Dict[str, str]:
    payload: Dict[str, str] = {}
    for column in atc_tree_columns:
        payload[f"{ATC_PREFIX}{column}"] = join_preserve_order(
            [record.get(column, "") for record in code_level_records],
            delimiter=MULTI_VALUE_DELIMITER,
        )
    return payload


def map_rxnorm_to_atc(
    df_input: pd.DataFrame,
    atc_tree_lookup: Dict[str, pd.DataFrame],
    atc_tree_columns: Sequence[str],
) -> pd.DataFrame:
    valid_atc_codes = set(atc_tree_lookup.keys())
    blank_payload = build_blank_atc_payload(atc_tree_columns)
    output_rows: List[Dict[str, object]] = []

    for row_number, record in enumerate(df_input.fillna("").to_dict(orient="records"), start=1):
        merged = dict(record)
        payload = dict(blank_payload)

        matched_atc_codes = extract_matching_atc_codes(record, valid_atc_codes)

        if not str(record.get(RX_CODE_COL, "")).strip():
            payload["atc_match_status"] = "NO_RX_CODE"
        elif atc_source_absent(set(split_multi_value_cell(record.get(RX_SAB_COL, "")))):
            payload["atc_match_status"] = "NO_ATC_IN_RX_SAB"
        elif not matched_atc_codes:
            payload["atc_match_status"] = "NO_ATC_CODE_MATCH_IN_TREE"
        else:
            code_level_records = build_code_level_records(
                matched_atc_codes=matched_atc_codes,
                atc_tree_lookup=atc_tree_lookup,
                atc_tree_columns=atc_tree_columns,
            )
            payload["atc_code_count"] = str(len(matched_atc_codes))
            payload.update(build_flattened_atc_payload(code_level_records, atc_tree_columns))
            payload["atc_match_status"] = "MATCHED"

        merged.update(payload)
        output_rows.append(merged)

        if row_number % 1000 == 0:
            logger.info("Mapped %d rows to ATC", row_number)

    return pd.DataFrame(output_rows)


def process_workbook(
    input_excel: Path,
    atc_tsv: Path,
    output_excel: Path,
    sheet_name: str,
) -> None:
    logger.info("Reading input workbook: %s", input_excel)
    df_input = load_input_workbook(input_excel=input_excel, sheet_name=sheet_name)
    logger.info("Loaded %d rows from input workbook", len(df_input))

    logger.info("Reading ATC tree: %s", atc_tsv)
    atc_df = load_atc_tree(atc_tsv)
    atc_tree_lookup = build_atc_tree_lookup(atc_df)
    logger.info("Loaded %d ATC tree rows covering %d ATC codes", len(atc_df), len(atc_tree_lookup))

    df_output = map_rxnorm_to_atc(
        df_input=df_input,
        atc_tree_lookup=atc_tree_lookup,
        atc_tree_columns=list(atc_df.columns),
    )

    output_excel.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output_excel, engine="openpyxl") as writer:
        df_output.to_excel(writer, sheet_name=sheet_name, index=False)

    logger.info("Wrote workbook with ATC columns to %s", output_excel)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Append ATC tree columns to an RxNorm-mapped interventions workbook "
            "using the RxNorm fields already present in the workbook"
        )
    )
    parser.add_argument(
        "--input_excel",
        required=True,
        type=Path,
        help="Input workbook containing the processed interventions sheet",
    )
    parser.add_argument(
        "--atc_tsv",
        required=True,
        type=Path,
        help="ATC tree TSV file",
    )
    parser.add_argument(
        "--output_excel",
        required=True,
        type=Path,
        help="Output workbook path",
    )
    parser.add_argument(
        "--sheet_name",
        default=SHEET_NAME,
        help=f"Worksheet name to read/write. Defaults to {SHEET_NAME!r}",
    )
    parser.add_argument(
        "--log_level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging level",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    process_workbook(
        input_excel=args.input_excel,
        atc_tsv=args.atc_tsv,
        output_excel=args.output_excel,
        sheet_name=args.sheet_name,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())