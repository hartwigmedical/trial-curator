from __future__ import annotations

import argparse
import logging
import re
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Iterable

import pandas as pd

from aus_trial_universe.trials_to_remove.trials_to_remove import should_remove_trial
from aus_trial_universe.eligibility_path.shared.utils.pipeline_io import (
    existing_files,
    latest_version_dir,
)
from aus_trial_universe.eligibility_path.shared.trial_resource.combined_trial_resource_export import (
    load_pottr_trial_ids_best_effort,
    normalize_trial_id as normalize_pottr_trial_id,
)

logger = logging.getLogger(__name__)

DEFAULT_INPUT_XLSX = Path("data/trial_inputs/anzctr/input_trials/version_10062026/anzctr_input.xlsx")
DEFAULT_INPUT_ROOT = Path("data/trial_inputs/anzctr/input_trials")
DEFAULT_OUTPUT_CSV = Path("data/trial_inputs/anzctr/extracted_trials/anzctr_field_extractions.csv")

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
DEFAULT_ANZCTR_INPUT_FILENAMES = (
    "01_initial_search_anzctr_input.xlsx",
    "02_pottr_append_anzctr_input.xlsx",
    "anzctr_input.xlsx",
)

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


def load_anzctr_workbooks(
    input_xlsx: str | Path | Sequence[str | Path],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if isinstance(input_xlsx, (str, Path)):
        paths = [Path(input_xlsx)]
    else:
        paths = [Path(path) for path in input_xlsx]

    if len(paths) == 1:
        return load_anzctr_workbook(paths[0])

    trial_by_actrn: dict[str, dict[str, object]] = {}
    conditions_by_actrn: dict[str, list[str]] = {}
    codes_by_actrn: dict[str, list[str]] = {}
    actrn_order: list[str] = []
    trial_columns: list[str] = []
    health_columns: list[str] = []
    intervention_code_columns: list[str] = []

    for path in paths:
        trials, health_conditions, intervention_codes = load_anzctr_workbook(path)
        trial_columns = list(trials.columns)
        health_columns = list(health_conditions.columns)
        intervention_code_columns = list(intervention_codes.columns)
        conditions_by_trial = build_health_condition_lookup(health_conditions)
        codes_by_trial, _drug_trial_ids = build_intervention_code_lookup(
            intervention_codes
        )

        for row in trials.to_dict("records"):
            source_trial_id = normalise_trial_id(row.get(TRIAL_ID_COLUMN))
            actrn = normalise_trial_id(row.get("ACTRN"))
            if not actrn:
                continue
            if actrn not in trial_by_actrn:
                actrn_order.append(actrn)
            trial_by_actrn[actrn] = dict(row)
            conditions_by_actrn[actrn] = conditions_by_trial.get(source_trial_id, [])
            codes_by_actrn[actrn] = codes_by_trial.get(source_trial_id, [])

    trial_rows: list[dict[str, object]] = []
    health_rows: list[dict[str, object]] = []
    code_rows: list[dict[str, object]] = []
    for canonical_trial_id, actrn in enumerate(actrn_order, start=1):
        row = dict(trial_by_actrn[actrn])
        row[TRIAL_ID_COLUMN] = canonical_trial_id
        trial_rows.append(row)
        health_rows.extend(
            {TRIAL_ID_COLUMN: canonical_trial_id, HEALTH_CONDITION_COLUMN: condition}
            for condition in conditions_by_actrn.get(actrn, [])
        )
        code_rows.extend(
            {TRIAL_ID_COLUMN: canonical_trial_id, INTERVENTION_CODE_COLUMN: code}
            for code in codes_by_actrn.get(actrn, [])
        )

    return (
        pd.DataFrame(trial_rows, columns=trial_columns),
        pd.DataFrame(health_rows, columns=health_columns),
        pd.DataFrame(code_rows, columns=intervention_code_columns),
    )


def default_anzctr_input_files(input_root: Path = DEFAULT_INPUT_ROOT) -> list[Path]:
    try:
        version_dir = latest_version_dir(input_root)
    except FileNotFoundError:
        return [DEFAULT_INPUT_XLSX]

    staged = existing_files(
        [
            version_dir / "01_initial_search_anzctr_input.xlsx",
            version_dir / "02_pottr_append_anzctr_input.xlsx",
        ]
    )
    if staged:
        return staged

    fallback = existing_files(
        [version_dir / filename for filename in DEFAULT_ANZCTR_INPUT_FILENAMES]
    )
    return fallback or [DEFAULT_INPUT_XLSX]


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
    pottr_trial_ids: set[str] | None = None,
) -> pd.DataFrame:
    codes_by_trial, drug_trial_ids = build_intervention_code_lookup(intervention_codes)
    conditions_by_trial = build_health_condition_lookup(health_conditions)
    pottr_ids = pottr_trial_ids or set()

    output_rows: list[dict[str, object]] = []
    pottr_exempt = 0
    trial_output_columns = [TRIAL_ID_COLUMN, *TRIAL_SOURCE_COLUMNS]
    for row_values in trials[trial_output_columns].itertuples(index=False, name=None):
        trial_id = normalise_trial_id(row_values[0])
        output_row = dict(zip(TRIAL_SOURCE_COLUMNS, row_values[1:]))

        # POTTR-listed trials must always reach the final output, so they are
        # exempt from the drug-intervention cohort filter. POTTR keys by ACTRN,
        # not the internal "TRIAL ID" used for drug-code matching.
        is_pottr = normalize_pottr_trial_id(output_row.get("ACTRN")) in pottr_ids
        if trial_id not in drug_trial_ids:
            if is_pottr:
                pottr_exempt += 1
            else:
                continue

        if should_remove_trial(output_row.get("ACTRN"), registry="anzctr"):
            logger.info(
                "Skipping manually removed ANZCTR trial: %s",
                output_row.get("ACTRN"),
            )
            continue

        output_row[HEALTH_CONDITION_COLUMN] = DISPLAY_DELIMITER.join(
            conditions_by_trial.get(trial_id, [])
        )
        output_row[INTERVENTION_CODES_OUTPUT_COLUMN] = DISPLAY_DELIMITER.join(
            codes_by_trial.get(trial_id, [])
        )
        output_rows.append(output_row)

    if pottr_exempt:
        logger.info(
            "Retained %d non-drug trial(s) exempted as POTTR-listed.", pottr_exempt
        )
    return pd.DataFrame(output_rows, columns=OUTPUT_COLUMNS)


def extract_fields_to_csv(
    input_xlsx: str | Path | Sequence[str | Path],
    output_csv: str | Path,
    pottr_trial_ids: set[str] | None = None,
) -> Path:
    trials, health_conditions, intervention_codes = load_anzctr_workbooks(input_xlsx)
    extracted = extract_drug_intervention_trials(
        trials,
        health_conditions,
        intervention_codes,
        pottr_trial_ids=pottr_trial_ids,
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
        description=(
            "Refresh the canonical ANZCTR extracted-trials CSV. The output "
            "includes drug annotation columns."
        )
    )
    parser.add_argument(
        "--input_xlsx",
        type=Path,
        nargs="+",
        default=None,
        help=(
            "ANZCTR workbook(s). Defaults to the newest "
            "data/trial_inputs/anzctr/input_trials/version_<ddmmyyyy> files."
        ),
    )
    parser.add_argument(
        "--output_csv",
        type=Path,
        default=DEFAULT_OUTPUT_CSV,
        help=f"Output CSV. Default: {DEFAULT_OUTPUT_CSV}",
    )
    parser.add_argument(
        "--rxnorm_rrf_dir",
        type=Path,
        default=None,
        help=(
            "RxNorm RRF directory/root for drug annotation. Defaults to "
            "iii_extract_drugs.py's RxNorm setting."
        ),
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

    from aus_trial_universe.eligibility_path.anzctr.i_download_trials_and_extract_eligibility.iii_extract_drugs import (
        DEFAULT_RXNORM_RRF_DIR,
        extract_drugs_to_csv,
    )

    extract_drugs_to_csv(
        args.output_csv,
        args.output_csv,
        input_xlsx=args.input_xlsx or default_anzctr_input_files(),
        refresh_input_csv=True,
        rxnorm_rrf_dir=args.rxnorm_rrf_dir or DEFAULT_RXNORM_RRF_DIR,
        pottr_trial_ids=load_pottr_trial_ids_best_effort(registry="anzctr"),
    )


if __name__ == "__main__":
    main()
