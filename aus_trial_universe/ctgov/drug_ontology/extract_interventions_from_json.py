from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Sequence

import pandas as pd

logger = logging.getLogger(__name__)

MULTI_VALUE_DELIMITER = " | "
OUTPUT_FILENAME = "ctgov_interventions_extracted.xlsx"
OUTPUT_SHEET_NAME = "interventions"

OUTPUT_COLUMNS = [
    "nct_id",
    "intervention_index",
    "intervention_type",
    "intervention_name",
    "intervention_description",
    "intervention_otherNames",
    "intervention_armGroupLabels",
    "intervention_all_aliases",
]


def is_missing(value: object) -> bool:
    return value is None or (isinstance(value, float) and pd.isna(value))


def clean_text(value: object) -> str:
    if is_missing(value):
        return ""
    return str(value).strip()


def ordered_unique_nonblank(values: Sequence[object]) -> List[str]:
    ordered: List[str] = []
    seen = set()

    for value in values:
        cleaned = clean_text(value)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        ordered.append(cleaned)

    return ordered


def join_pipe(values: Sequence[object]) -> str:
    return MULTI_VALUE_DELIMITER.join(ordered_unique_nonblank(values))


def ensure_list(value: object) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def extract_study_records(payload: Any) -> List[Dict[str, Any]]:
    """
    Accept a few common CT.gov JSON container shapes.

    Supported:
    - a list of study dicts
    - {"studies": [...]} or {"Studies": [...]} style wrappers
    - a single study dict containing protocolSection
    """
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if isinstance(payload, dict):
        for key in ("studies", "Studies"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]

        if "protocolSection" in payload:
            return [payload]

    raise ValueError(
        "Unsupported JSON structure. Expected a list of studies, a studies wrapper, or a single study object."
    )


def iter_study_records_from_json(input_json: Path) -> Iterator[Dict[str, Any]]:
    """
    Read either:
    - standard JSON file containing one top-level payload
    - JSON Lines / NDJSON where each line is a study object
    """
    text = input_json.read_text(encoding="utf-8").strip()
    if not text:
        return

    try:
        payload = json.loads(text)
        for record in extract_study_records(payload):
            yield record
        return
    except json.JSONDecodeError:
        pass

    with input_json.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Failed to parse JSON on line {line_number} of {input_json}: {exc}"
                ) from exc

            if not isinstance(payload, dict):
                raise ValueError(
                    f"Expected each JSON line to be an object, got {type(payload).__name__} on line {line_number}"
                )
            yield payload


def build_intervention_all_aliases(intervention_name: str, other_names: Sequence[object]) -> str:
    return join_pipe([intervention_name, *other_names])


def extract_intervention_rows(study_record: Dict[str, Any]) -> List[Dict[str, str]]:
    protocol_section = study_record.get("protocolSection") or {}
    identification_module = protocol_section.get("identificationModule") or {}
    arms_module = protocol_section.get("armsInterventionsModule") or {}

    nct_id = clean_text(identification_module.get("nctId"))
    interventions = ensure_list(arms_module.get("interventions"))

    rows: List[Dict[str, str]] = []
    for intervention_index, intervention in enumerate(interventions):
        if not isinstance(intervention, dict):
            logger.warning(
                "Skipping non-dict intervention at nct_id=%r intervention_index=%d",
                nct_id,
                intervention_index,
            )
            continue

        intervention_type = clean_text(intervention.get("type"))
        intervention_name = clean_text(intervention.get("name"))
        intervention_description = clean_text(intervention.get("description"))

        other_names = ensure_list(intervention.get("otherNames"))
        arm_group_labels = ensure_list(intervention.get("armGroupLabels"))

        row = {
            "nct_id": nct_id,
            "intervention_index": str(intervention_index),
            "intervention_type": intervention_type,
            "intervention_name": intervention_name,
            "intervention_description": intervention_description,
            "intervention_otherNames": join_pipe(other_names),
            "intervention_armGroupLabels": join_pipe(arm_group_labels),
            "intervention_all_aliases": build_intervention_all_aliases(
                intervention_name=intervention_name,
                other_names=other_names,
            ),
        }
        rows.append(row)

    return rows


def build_interventions_dataframe(input_json: Path) -> pd.DataFrame:
    rows: List[Dict[str, str]] = []

    for study_number, study_record in enumerate(iter_study_records_from_json(input_json), start=1):
        rows.extend(extract_intervention_rows(study_record))

        if study_number % 100 == 0:
            logger.info("Processed %d study records", study_number)

    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)


def process_json_to_excel(input_json: Path, output_excel: Path) -> None:
    if not input_json.exists():
        raise FileNotFoundError(f"Input JSON not found: {input_json}")

    logger.info("Reading CT.gov JSON: %s", input_json)
    df = build_interventions_dataframe(input_json)
    logger.info("Extracted %d intervention rows", len(df))

    output_excel.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output_excel, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name=OUTPUT_SHEET_NAME, index=False)

    logger.info("Wrote intervention-centric workbook to %s", output_excel)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read CT.gov JSON and build an intervention-centric table with one row per intervention object"
        )
    )
    parser.add_argument(
        "--input_json",
        required=True,
        type=Path,
        help="Path to CT.gov JSON input (JSON or JSON Lines)",
    )
    parser.add_argument(
        "--output_excel",
        type=Path,
        default=None,
        help=(
            "Output XLSX path. If omitted, writes ctgov_interventions_extracted.xlsx "
            "next to the input file."
        ),
    )
    parser.add_argument(
        "--log_level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging level",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    output_excel = args.output_excel
    if output_excel is None:
        output_excel = args.input_json.with_name(OUTPUT_FILENAME)

    process_json_to_excel(
        input_json=args.input_json,
        output_excel=output_excel,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
