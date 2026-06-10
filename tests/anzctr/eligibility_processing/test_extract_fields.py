from __future__ import annotations

from pathlib import Path

import pandas as pd

from aus_trial_universe.anzctr.eligibility.i_download_trials_and_extract_eligibility.i_select_trials_and_fields import (
    OUTPUT_COLUMNS,
    extract_drug_intervention_trials,
    extract_fields_to_csv,
)


def trial_rows() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "TRIAL ID": [1, 2, 3],
            "ACTRN": ["ACTRN1", "ACTRN2", "ACTRN3"],
            "SUBMIT DATE": ["2026-01-01", "2026-01-02", "2026-01-03"],
            "APPROVAL DATE": ["2026-02-01", "2026-02-02", "2026-02-03"],
            "STUDY TITLE": ["Study 1", "Study 2", "Study 3"],
            "SCIENTIFIC TITLE": ["Scientific 1", "Scientific 2", "Scientific 3"],
            "PURPOSE": ["Treatment", "Prevention", "Treatment"],
            "STUDY TYPE": ["Interventional", "Interventional", "Observational"],
            "INTERVENTIONS": ["Drug A", "Drug B", "Drug C"],
            "COMPARATOR": ["Comparator 1", "Comparator 2", "Comparator 3"],
            "CONTROL": ["Control 1", "Control 2", "Control 3"],
            "INCLUSIVE CRITERIA": ["Include 1", "Include 2", "Include 3"],
            "MIN AGE": [18, 18, 18],
            "MIN AGE TYPE": ["Years", "Years", "Years"],
            "MAX AGE": [80, 80, 80],
            "MAX AGE TYPE": ["Years", "Years", "Years"],
            "INCLUSIVE GENDER": ["All", "All", "All"],
            "EXCLUSIVE CRITERIA": ["Exclude 1", "Exclude 2", "Exclude 3"],
            "PHASE": ["Phase 2", "Phase 2", "Phase 2"],
            "RECRUITMENT STATUS": ["Recruiting", "Recruiting", "Recruiting"],
            "RECRUITMENT COUNTRY": ["Australia", "Australia", "Australia"],
            "RECRUITMENT STATE": ["NSW", "VIC", "QLD"],
            "PRIMARY SPONSOR TYPE": ["Hospital", "Hospital", "Hospital"],
            "PRIMARY SPONSOR NAME": ["Sponsor 1", "Sponsor 2", "Sponsor 3"],
            "PRIMARY SPONSOR COUNTRY": ["Australia", "Australia", "Australia"],
        }
    )


def intervention_code_rows() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "TRIAL ID": [1, 1, 2, 3],
            "INTERVENTION CODE": [
                "Treatment: Drugs",
                "Treatment: Surgery",
                "Prevention: Drugs",
                "  treatment:   drugs  ",
            ],
        }
    )


def health_condition_rows() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "TRIAL ID": [1, 1, 1, 2],
            "HEALTH CONDITION": [
                "Breast cancer",
                "Metastatic disease",
                "Breast cancer",
                "Colorectal cancer",
            ],
        }
    )


def test_extract_drug_intervention_trials_uses_treatment_drugs_code_only():
    extracted = extract_drug_intervention_trials(
        trial_rows(),
        health_condition_rows(),
        intervention_code_rows(),
    )

    assert list(extracted.columns) == OUTPUT_COLUMNS
    assert OUTPUT_COLUMNS.index("HEALTH CONDITION") == (
        OUTPUT_COLUMNS.index("SCIENTIFIC TITLE") + 1
    )
    assert extracted["ACTRN"].tolist() == ["ACTRN1", "ACTRN3"]
    assert extracted["HEALTH CONDITION"].tolist() == [
        "Breast cancer | Metastatic disease",
        "",
    ]
    assert extracted["anzctr_intervention_codes"].tolist() == [
        "Treatment: Drugs | Treatment: Surgery",
        "treatment: drugs",
    ]


def test_extract_fields_to_csv_writes_selected_trial_rows(tmp_path: Path):
    input_xlsx = tmp_path / "anzctr_input.xlsx"
    output_csv = tmp_path / "anzctr_field_extractions.csv"

    with pd.ExcelWriter(input_xlsx, engine="openpyxl") as writer:
        trial_rows().to_excel(writer, sheet_name="TRIAL", index=False)
        health_condition_rows().to_excel(
            writer,
            sheet_name="HEALTH CONDITION",
            index=False,
        )
        intervention_code_rows().to_excel(
            writer, sheet_name="INTERVENTION CODE", index=False
        )

    path = extract_fields_to_csv(input_xlsx, output_csv)
    written = pd.read_csv(path)

    assert path == output_csv
    assert list(written.columns) == OUTPUT_COLUMNS
    assert written["ACTRN"].tolist() == ["ACTRN1", "ACTRN3"]
    assert written["HEALTH CONDITION"].fillna("").tolist() == [
        "Breast cancer | Metastatic disease",
        "",
    ]
