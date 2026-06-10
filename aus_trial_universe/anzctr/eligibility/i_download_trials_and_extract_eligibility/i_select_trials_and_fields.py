from __future__ import annotations

import argparse
import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Iterable

import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_INPUT_XLSX = Path("data/anzctr/trials/version_10062026/anzctr_input.xlsx")
DEFAULT_OUTPUT_CSV = Path("data/anzctr/eligibility/trials/anzctr_field_extractions.csv")

TRIAL_SHEET = "TRIAL"
HEALTH_CONDITION_SHEET = "HEALTH CONDITION"
INTERVENTION_CODE_SHEET = "INTERVENTION CODE"

TRIAL_ID_COLUMN = "TRIAL ID"
HEALTH_CONDITION_COLUMN = "HEALTH CONDITION"
INTERVENTION_CODE_COLUMN = "INTERVENTION CODE"
PURPOSE_COLUMN = "PURPOSE"
STUDY_TYPE_COLUMN = "STUDY TYPE"
DRUG_INTERVENTION_CODE = "Treatment: Drugs"

INTERVENTION_CODES_OUTPUT_COLUMN = "anzctr_intervention_codes"

OUTPUT_TRIAL_COLUMNS = [
    "ACTRN",
    "SUBMIT DATE",
    "APPROVAL DATE",
    "STUDY TITLE",
    "SCIENTIFIC TITLE",
    HEALTH_CONDITION_COLUMN,
    "INTERVENTIONS",
    "COMPARATOR",
    "CONTROL",
    "INCLUSIVE CRITERIA",
    "MIN AGE",
    "MIN AGE TYPE",
    "MAX AGE",
    "MAX AGE TYPE",
    "INCLUSIVE GENDER",
    "EXCLUSIVE CRITERIA",
    "PHASE",
    "RECRUITMENT STATUS",
    "RECRUITMENT COUNTRY",
    "RECRUITMENT STATE",
    "PRIMARY SPONSOR TYPE",
    "PRIMARY SPONSOR NAME",
    "PRIMARY SPONSOR COUNTRY",
]
OUTPUT_COLUMNS = OUTPUT_TRIAL_COLUMNS + [INTERVENTION_CODES_OUTPUT_COLUMN]
TRIAL_SOURCE_COLUMNS = [
    column
    for column in OUTPUT_TRIAL_COLUMNS
    if column != HEALTH_CONDITION_COLUMN
]

DISPLAY_DELIMITER = " | "
WHITESPACE_RE = re.compile(r"\s+")


def clean_text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return WHITESPACE_RE.sub(" ", str(value).strip())


def normalise_code(value: object) -> str:
    return clean_text(value).casefold()


def normalise_trial_id(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return clean_text(value)


def ordered_unique(values: Iterable[object]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        cleaned = clean_text(value)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
    return out


def require_columns(
    frame: pd.DataFrame, columns: Iterable[str], *, sheet_name: str
) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(
            f"{sheet_name!r} sheet is missing required columns: {', '.join(missing)}"
        )


def load_anzctr_workbook(
    input_xlsx: str | Path,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    input_xlsx = Path(input_xlsx)
    trials = pd.read_excel(input_xlsx, sheet_name=TRIAL_SHEET)
    health_conditions = pd.read_excel(input_xlsx, sheet_name=HEALTH_CONDITION_SHEET)
    intervention_codes = pd.read_excel(input_xlsx, sheet_name=INTERVENTION_CODE_SHEET)

    require_columns(
        trials,
        [TRIAL_ID_COLUMN, *TRIAL_SOURCE_COLUMNS],
        sheet_name=TRIAL_SHEET,
    )
    require_columns(
        health_conditions,
        [TRIAL_ID_COLUMN, HEALTH_CONDITION_COLUMN],
        sheet_name=HEALTH_CONDITION_SHEET,
    )
    require_columns(
        intervention_codes,
        [TRIAL_ID_COLUMN, INTERVENTION_CODE_COLUMN],
        sheet_name=INTERVENTION_CODE_SHEET,
    )
    return trials, health_conditions, intervention_codes


def build_health_condition_lookup(
    health_conditions: pd.DataFrame,
) -> dict[str, list[str]]:
    conditions_by_trial: dict[str, list[str]] = defaultdict(list)

    for trial_id_value, condition_value in health_conditions[
        [TRIAL_ID_COLUMN, HEALTH_CONDITION_COLUMN]
    ].itertuples(index=False, name=None):
        trial_id = normalise_trial_id(trial_id_value)
        if not trial_id:
            continue

        condition = clean_text(condition_value)
        if condition:
            conditions_by_trial[trial_id].append(condition)

    return {
        trial_id: ordered_unique(conditions)
        for trial_id, conditions in conditions_by_trial.items()
    }


def build_intervention_code_lookup(
    intervention_codes: pd.DataFrame,
) -> tuple[dict[str, list[str]], set[str]]:
    codes_by_trial: dict[str, list[str]] = defaultdict(list)
    drug_trial_ids: set[str] = set()
    drug_code_key = normalise_code(DRUG_INTERVENTION_CODE)

    for trial_id_value, code_value in intervention_codes[
        [TRIAL_ID_COLUMN, INTERVENTION_CODE_COLUMN]
    ].itertuples(index=False, name=None):
        trial_id = normalise_trial_id(trial_id_value)
        if not trial_id:
            continue

        code = clean_text(code_value)
        if code:
            codes_by_trial[trial_id].append(code)

        if normalise_code(code) == drug_code_key:
            drug_trial_ids.add(trial_id)

    return (
        {trial_id: ordered_unique(codes) for trial_id, codes in codes_by_trial.items()},
        drug_trial_ids,
    )


def extract_drug_intervention_trials(
    trials: pd.DataFrame,
    health_conditions: pd.DataFrame,
    intervention_codes: pd.DataFrame,
) -> pd.DataFrame:
    codes_by_trial, drug_trial_ids = build_intervention_code_lookup(intervention_codes)
    conditions_by_trial = build_health_condition_lookup(health_conditions)

    output_rows: list[dict[str, object]] = []
    trial_output_columns = [TRIAL_ID_COLUMN, *TRIAL_SOURCE_COLUMNS]
    for row_values in trials[trial_output_columns].itertuples(index=False, name=None):
        trial_id = normalise_trial_id(row_values[0])
        if trial_id not in drug_trial_ids:
            continue

        output_row = dict(zip(TRIAL_SOURCE_COLUMNS, row_values[1:]))
        output_row[HEALTH_CONDITION_COLUMN] = DISPLAY_DELIMITER.join(
            conditions_by_trial.get(trial_id, [])
        )
        output_row[INTERVENTION_CODES_OUTPUT_COLUMN] = DISPLAY_DELIMITER.join(
            codes_by_trial.get(trial_id, [])
        )
        output_rows.append(output_row)

    return pd.DataFrame(output_rows, columns=OUTPUT_COLUMNS)


def extract_fields_to_csv(input_xlsx: str | Path, output_csv: str | Path) -> Path:
    trials, health_conditions, intervention_codes = load_anzctr_workbook(input_xlsx)
    extracted = extract_drug_intervention_trials(
        trials,
        health_conditions,
        intervention_codes,
    )

    output_csv = Path(output_csv)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    extracted.to_csv(output_csv, index=False)

    logger.info(
        "Extracted %d ANZCTR drug-intervention trials from %d TRIAL rows.",
        len(extracted),
        len(trials),
    )
    logger.info("Wrote %s", output_csv)
    return output_csv


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract ANZCTR drug-intervention trial rows."
    )
    parser.add_argument(
        "--input_xlsx",
        type=Path,
        default=DEFAULT_INPUT_XLSX,
        help=f"ANZCTR workbook. Default: {DEFAULT_INPUT_XLSX}",
    )
    parser.add_argument(
        "--output_csv",
        type=Path,
        default=DEFAULT_OUTPUT_CSV,
        help=f"Output CSV. Default: {DEFAULT_OUTPUT_CSV}",
    )
    parser.add_argument(
        "--log_level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging level.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    extract_fields_to_csv(args.input_xlsx, args.output_csv)


if __name__ == "__main__":
    main()
