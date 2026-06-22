from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from typing import Any, Mapping

from aus_trial_universe.drug_utility_path.trial_drug_curation.utils.schema import TSV_COLUMNS

# The OncoTree column is assigned downstream by the OncoTree agentic workflow, so
# the curation LLM neither produces it nor is it part of the model's schema.
LLM_OUTPUT_COLUMNS = tuple(column for column in TSV_COLUMNS if column != "Oncotree")


@dataclass(frozen=True)
class TrialDrugRow:
    """One trial drug curation row."""

    trialId: str
    drugName: str
    drugRegime: str
    trialArm: str
    drugEvidenceSource: str
    cancerTypes: str
    Oncotree: str
    standardisedName: str
    pottrDrugClass: str
    cancerDrug: bool
    dosage: str
    TGAStatus: str
    PBSIndicationStatus: str

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "TrialDrugRow":
        # Oncotree is filled downstream, so it is optional in the parsed payload.
        missing = [column for column in LLM_OUTPUT_COLUMNS if column not in row]
        if missing:
            raise ValueError(f"Missing trial drug row column(s): {', '.join(missing)}")
        return cls(
            trialId=_coerce_text(row["trialId"]),
            drugName=_coerce_text(row["drugName"]),
            drugRegime=_coerce_text(row["drugRegime"]),
            trialArm=_coerce_text(row["trialArm"]),
            drugEvidenceSource=_coerce_text(row["drugEvidenceSource"]),
            cancerTypes=_coerce_text(row["cancerTypes"]),
            Oncotree=_coerce_text(row.get("Oncotree", "")),
            standardisedName=_coerce_text(row["standardisedName"]),
            pottrDrugClass=_coerce_text(row["pottrDrugClass"]),
            cancerDrug=_coerce_bool(row["cancerDrug"]),
            dosage=_coerce_text(row["dosage"]),
            TGAStatus=_coerce_text(row["TGAStatus"]),
            PBSIndicationStatus=_coerce_text(row["PBSIndicationStatus"]),
        )

    def to_tsv_record(self) -> list[str]:
        return [
            self.trialId,
            self.drugName,
            self.drugRegime,
            self.trialArm,
            self.drugEvidenceSource,
            self.cancerTypes,
            self.Oncotree,
            self.standardisedName,
            self.pottrDrugClass,
            "True" if self.cancerDrug else "False",
            self.dosage,
            self.TGAStatus,
            self.PBSIndicationStatus,
        ]


@dataclass(frozen=True)
class TrialDrugCurationTable:
    """A tabular trial drug curation result."""

    rows: tuple[TrialDrugRow, ...]

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "TrialDrugCurationTable":
        raw_rows = payload.get("rows")
        if not isinstance(raw_rows, list):
            raise ValueError("Trial drug curation payload must contain a `rows` list.")
        return cls(rows=tuple(TrialDrugRow.from_mapping(row) for row in raw_rows))

    def to_tsv(self) -> str:
        output = io.StringIO()
        writer = csv.writer(output, delimiter="\t", lineterminator="\n")
        writer.writerow(TSV_COLUMNS)
        writer.writerows(row.to_tsv_record() for row in self.rows)
        return output.getvalue()


TRIAL_DRUG_CURATION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["rows"],
    "properties": {
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": list(LLM_OUTPUT_COLUMNS),
                "properties": {
                    "trialId": {"type": "string"},
                    "drugName": {"type": "string"},
                    "drugRegime": {"type": "string"},
                    "trialArm": {
                        "type": "string",
                        "enum": ["Experimental", "Control", "Neither"],
                    },
                    "drugEvidenceSource": {"type": "string"},
                    "cancerTypes": {"type": "string"},
                    "standardisedName": {"type": "string"},
                    "pottrDrugClass": {"type": "string"},
                    "cancerDrug": {"type": "boolean"},
                    "dosage": {"type": "string"},
                    "TGAStatus": {
                        "type": "string",
                        "enum": ["Approved", "Not approved", "Unclear", ""],
                    },
                    "PBSIndicationStatus": {"type": "string"},
                },
            },
        },
    },
}


TRIAL_DRUG_CURATION_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "name": "trial_drug_curation_table",
    "description": "Trial drug curation rows that can be rendered as TSV.",
    "strict": True,
    "schema": TRIAL_DRUG_CURATION_JSON_SCHEMA,
}


TRIAL_DRUG_CURATION_CHAT_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": TRIAL_DRUG_CURATION_RESPONSE_FORMAT["name"],
        "description": TRIAL_DRUG_CURATION_RESPONSE_FORMAT["description"],
        "strict": TRIAL_DRUG_CURATION_RESPONSE_FORMAT["strict"],
        "schema": TRIAL_DRUG_CURATION_JSON_SCHEMA,
    },
}


def table_from_payload(
    payload: Mapping[str, Any] | list[Mapping[str, Any]],
) -> TrialDrugCurationTable:
    if isinstance(payload, list):
        return TrialDrugCurationTable(
            rows=tuple(TrialDrugRow.from_mapping(row) for row in payload)
        )
    return TrialDrugCurationTable.from_mapping(payload)


def tsv_from_payload(payload: Mapping[str, Any] | list[Mapping[str, Any]]) -> str:
    return table_from_payload(payload).to_tsv()


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    if text.strip().upper() in {"NA", "N/A"}:
        return ""
    return text


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    raise ValueError(f"Expected boolean value, got {value!r}")
