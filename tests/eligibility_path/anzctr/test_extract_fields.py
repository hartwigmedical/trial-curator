from __future__ import annotations

from pathlib import Path

import pandas as pd

from aus_trial_universe.eligibility_path.anzctr.i_download_trials_and_extract_eligibility.ii_select_trials_and_fields import (
    OUTPUT_COLUMNS,
    default_anzctr_input_files,
    extract_drug_intervention_trials,
    extract_fields_to_csv,
    load_anzctr_workbooks,
)
from aus_trial_universe.trials_to_remove import trials_to_remove as removal_config


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


def test_extract_drug_intervention_trials_keeps_non_drug_pottr_trials():
    # Trial 2 ("Prevention: Drugs") is non-drug, so normally dropped; being
    # POTTR-listed (by ACTRN) exempts it from the drug-intervention filter.
    extracted = extract_drug_intervention_trials(
        trial_rows(),
        health_condition_rows(),
        intervention_code_rows(),
        pottr_trial_ids={"ACTRN2"},
    )

    assert extracted["ACTRN"].tolist() == ["ACTRN1", "ACTRN2", "ACTRN3"]


def test_pottr_exemption_matches_bare_workbook_actrn_against_prefixed_pottr_id():
    # Regression: real ANZCTR workbooks store the ACTRN as a bare number
    # ("12624000110583"), while POTTR keys on the ACTRN-prefixed id. The
    # exemption must canonicalise across that format gap; otherwise non-drug
    # ANZCTR POTTR trials are silently dropped (they never matched the
    # exemption set before the prefix-aware comparison was introduced).
    trials = trial_rows()
    trials["ACTRN"] = ["12600000000001", "12600000000002", "12600000000003"]
    extracted = extract_drug_intervention_trials(
        trials,
        health_condition_rows(),
        intervention_code_rows(),
        pottr_trial_ids={"ACTRN12600000000002"},  # prefixed, as POTTR provides it
    )

    # Trial 2 ("Prevention: Drugs") is non-drug and would normally be dropped;
    # the prefixed POTTR id must still exempt the bare workbook ACTRN.
    assert extracted["ACTRN"].tolist() == [
        "12600000000001",
        "12600000000002",
        "12600000000003",
    ]


def test_pottr_exemption_overrides_manual_removal(monkeypatch):
    # POTTR-listed trials override both the drug filter and the manual-removal
    # list, so a removal-listed POTTR trial (ACTRN3) is still retained.
    monkeypatch.setattr(removal_config, "trials_remove", ["ACTRN3"])

    extracted = extract_drug_intervention_trials(
        trial_rows(),
        health_condition_rows(),
        intervention_code_rows(),
        pottr_trial_ids={"ACTRN2", "ACTRN3"},
    )

    assert extracted["ACTRN"].tolist() == ["ACTRN1", "ACTRN2", "ACTRN3"]


def test_extract_drug_intervention_trials_removes_configured_anzctr_ids(monkeypatch):
    monkeypatch.setattr(removal_config, "trials_remove", ["ACTRN3"])

    extracted = extract_drug_intervention_trials(
        trial_rows(),
        health_condition_rows(),
        intervention_code_rows(),
    )

    assert extracted["ACTRN"].tolist() == ["ACTRN1"]


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


def test_load_anzctr_workbooks_remaps_trial_ids_and_uses_later_actrn_rows(
    tmp_path: Path,
):
    initial_xlsx = tmp_path / "01_initial_search_anzctr_input.xlsx"
    append_xlsx = tmp_path / "02_pottr_append_anzctr_input.xlsx"

    with pd.ExcelWriter(initial_xlsx, engine="openpyxl") as writer:
        trial_rows().iloc[[0]].to_excel(writer, sheet_name="TRIAL", index=False)
        health_condition_rows().iloc[[0]].to_excel(
            writer,
            sheet_name="HEALTH CONDITION",
            index=False,
        )
        intervention_code_rows().iloc[[0]].to_excel(
            writer,
            sheet_name="INTERVENTION CODE",
            index=False,
        )

    later_trial = trial_rows().iloc[[0]].copy()
    later_trial.loc[:, "STUDY TITLE"] = ["Updated Study 1"]
    later_trial.loc[:, "TRIAL ID"] = [1]
    append_health = pd.DataFrame(
        {"TRIAL ID": [1], "HEALTH CONDITION": ["Updated cancer"]}
    )
    append_codes = pd.DataFrame(
        {"TRIAL ID": [1], "INTERVENTION CODE": ["Treatment: Drugs"]}
    )
    with pd.ExcelWriter(append_xlsx, engine="openpyxl") as writer:
        later_trial.to_excel(writer, sheet_name="TRIAL", index=False)
        append_health.to_excel(writer, sheet_name="HEALTH CONDITION", index=False)
        append_codes.to_excel(writer, sheet_name="INTERVENTION CODE", index=False)

    trials, health_conditions, intervention_codes = load_anzctr_workbooks(
        [initial_xlsx, append_xlsx]
    )

    assert trials["TRIAL ID"].tolist() == [1]
    assert trials["STUDY TITLE"].tolist() == ["Updated Study 1"]
    assert health_conditions["TRIAL ID"].tolist() == [1]
    assert health_conditions["HEALTH CONDITION"].tolist() == ["Updated cancer"]
    assert intervention_codes["TRIAL ID"].tolist() == [1]


def test_default_anzctr_input_files_use_newest_version_staged_files(tmp_path: Path):
    root = tmp_path / "data/trial_inputs/anzctr/input_trials"
    older = root / "version_01012026"
    newer = root / "version_02012026"
    older.mkdir(parents=True)
    newer.mkdir(parents=True)
    (older / "anzctr_input.xlsx").write_text("", encoding="utf-8")
    initial = newer / "01_initial_search_anzctr_input.xlsx"
    append = newer / "02_pottr_append_anzctr_input.xlsx"
    initial.write_text("", encoding="utf-8")
    append.write_text("", encoding="utf-8")

    assert default_anzctr_input_files(root) == [initial, append]
