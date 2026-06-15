from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aus_trial_universe.trial_drug_curation.utils.constants import (
    ANZCTR_TRIAL_COLUMNS,
    CTGOV_PROTOCOL_MODULES,
    MAX_TEXT_FIELD_CHARS,
)
from aus_trial_universe.trial_drug_curation.utils.trial_ids import (
    SkippedTrial,
    normalise_trial_id,
)
from aus_trial_universe.trial_drug_curation.utils.xlsx_reader import (
    clean_cell,
    normalize_excel_scalar,
    read_xlsx_sheets,
)


@dataclass(frozen=True)
class LoadedTrialRecords:
    records_by_registry: dict[str, list[dict[str, Any]]]
    skipped_trials: tuple[SkippedTrial, ...]


def load_trial_records(
    *,
    grouped_trial_ids: dict[str, list[str]],
    resources: dict[str, str],
) -> LoadedTrialRecords:
    records_by_registry: dict[str, list[dict[str, Any]]] = {}
    skipped: list[SkippedTrial] = []

    if "ctgov" in grouped_trial_ids:
        records, missing = load_ctgov_trial_records(
            resources["ctgov_input"], grouped_trial_ids["ctgov"]
        )
        records_by_registry["ctgov"] = records
        skipped.extend(
            SkippedTrial(trial_id=trial_id, registry="ctgov", reason="not_in_local_input")
            for trial_id in missing
        )

    if "anzctr" in grouped_trial_ids:
        records, missing = load_anzctr_trial_records(
            resources["anzctr_input"], grouped_trial_ids["anzctr"]
        )
        records_by_registry["anzctr"] = records
        skipped.extend(
            SkippedTrial(trial_id=trial_id, registry="anzctr", reason="not_in_local_input")
            for trial_id in missing
        )

    return LoadedTrialRecords(
        records_by_registry=records_by_registry,
        skipped_trials=tuple(skipped),
    )


def load_ctgov_trial_records(
    input_json: str | Path, nct_ids: Sequence[str]
) -> tuple[list[dict[str, Any]], list[str]]:
    requested = set(nct_ids)
    with Path(input_json).open(encoding="utf-8") as file:
        data = json.load(file)

    records: list[dict[str, Any]] = []
    found: set[str] = set()
    for trial in data:
        protocol = trial.get("protocolSection", {})
        identification = protocol.get("identificationModule", {})
        nct_id = normalise_trial_id(str(identification.get("nctId", "")))
        if nct_id not in requested:
            continue

        found.add(nct_id)
        records.append(
            {
                "trialId": nct_id,
                "protocolSection": truncate_nested(
                    {
                        module: protocol.get(module, {})
                        for module in CTGOV_PROTOCOL_MODULES
                        if module in protocol
                    }
                ),
            }
        )

    return records, sorted(requested - found)


def load_anzctr_trial_records(
    input_xlsx: str | Path, actrn_ids: Sequence[str]
) -> tuple[list[dict[str, Any]], list[str]]:
    workbook = read_xlsx_sheets(
        str(input_xlsx),
        sheets=("TRIAL", "HEALTH CONDITION", "CONDITION  CODE", "INTERVENTION CODE"),
    )
    trial_rows = workbook["TRIAL"]
    requested = {normalise_actrn_for_lookup(trial_id) for trial_id in actrn_ids}
    found: set[str] = set()

    conditions = rows_by_trial_id(workbook["HEALTH CONDITION"], "HEALTH CONDITION")
    condition_codes = rows_by_trial_id(
        workbook["CONDITION  CODE"],
        "CONDITION CODE",
        extra_columns=("CONDITION CATEGORY",),
    )
    intervention_codes = rows_by_trial_id(
        workbook["INTERVENTION CODE"], "INTERVENTION CODE"
    )

    records: list[dict[str, Any]] = []
    for row in trial_rows:
        lookup_id = normalise_actrn_for_lookup(str(row.get("ACTRN", "")))
        if lookup_id not in requested:
            continue
        found.add(lookup_id)
        trial_join_id = normalize_excel_scalar(row.get("TRIAL ID", ""))
        record = {
            "trialId": f"ACTRN{lookup_id}",
            "trialJoinId": trial_join_id,
            "trial": {
                column: row.get(column, "")
                for column in ANZCTR_TRIAL_COLUMNS
                if clean_cell(row.get(column, ""))
            },
            "healthConditions": conditions.get(trial_join_id, []),
            "conditionCodes": condition_codes.get(trial_join_id, []),
            "interventionCodes": intervention_codes.get(trial_join_id, []),
        }
        records.append(truncate_nested(record))

    missing = sorted(f"ACTRN{trial_id}" for trial_id in requested - found)
    return records, missing


def rows_by_trial_id(
    rows: list[dict[str, str]],
    value_column: str,
    *,
    extra_columns: Sequence[str] = (),
) -> dict[str, list[Any]]:
    grouped: dict[str, list[Any]] = {}
    for row in rows:
        trial_id = normalize_excel_scalar(row.get("TRIAL ID", ""))
        value = clean_cell(row.get(value_column, ""))
        if not trial_id or not value:
            continue
        if extra_columns:
            entry = {value_column: value}
            for column in extra_columns:
                extra_value = clean_cell(row.get(column, ""))
                if extra_value:
                    entry[column] = extra_value
        else:
            entry = value
        grouped.setdefault(trial_id, []).append(entry)
    return grouped


def normalise_actrn_for_lookup(trial_id: str) -> str:
    normalized = normalise_trial_id(trial_id)
    return normalized.removeprefix("ACTRN")


def truncate_nested(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: truncate_nested(item) for key, item in value.items()}
    if isinstance(value, list):
        return [truncate_nested(item) for item in value]
    if isinstance(value, str):
        return truncate_text(value)
    return value


def truncate_text(text: str, max_chars: int = MAX_TEXT_FIELD_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n[TRUNCATED]"
