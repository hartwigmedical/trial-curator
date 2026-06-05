from pathlib import Path

from aus_trial_universe.ctgov.drug_ontology.shared.schema import (
    COL_INTERVENTION_ALL_ALIASES,
    COL_INTERVENTION_ALL_ALIASES_NORMALISED,
    COL_INTERVENTION_ALL_ALIASES_NORMALISED_FULL,
    COL_INTERVENTION_ARM_GROUP_LABELS,
    COL_INTERVENTION_DESCRIPTION,
    COL_INTERVENTION_INDEX,
    COL_INTERVENTION_NAME,
    COL_INTERVENTION_OTHER_NAMES,
    COL_INTERVENTION_TYPE,
    COL_NCT_ID,
    CORE_INTERVENTION_COLUMNS,
)
from aus_trial_universe.ctgov.drug_ontology.ctgov.interventions import (
    build_interventions_dataframe,
    extract_intervention_rows,
    normalise_intervention_aliases,
    normalise_intervention_aliases_full,
)
from aus_trial_universe.ctgov.drug_ontology.ctgov.unique_terms import default_output_dir_for_input


def make_study(interventions):
    return {
        "protocolSection": {
            "identificationModule": {"nctId": "NCT00000001"},
            "armsInterventionsModule": {
                "interventions": interventions,
            },
        }
    }


def test_extracts_core_intervention_fields_and_aliases():
    study = make_study(
        [
            {
                "type": "DRUG",
                "name": "Imatinib",
                "description": "Imatinib oral tablet",
                "otherNames": ["Gleevec", "STI571"],
                "armGroupLabels": ["Experimental Arm"],
            }
        ]
    )

    rows = extract_intervention_rows(study)

    assert len(rows) == 1
    row = rows[0]

    assert row[COL_NCT_ID] == "NCT00000001"
    assert row[COL_INTERVENTION_INDEX] == "0"
    assert row[COL_INTERVENTION_TYPE] == "DRUG"
    assert row[COL_INTERVENTION_NAME] == "Imatinib"
    assert row[COL_INTERVENTION_DESCRIPTION] == "Imatinib oral tablet"
    assert row[COL_INTERVENTION_OTHER_NAMES] == "Gleevec | STI571"
    assert row[COL_INTERVENTION_ARM_GROUP_LABELS] == "Experimental Arm"
    assert row[COL_INTERVENTION_ALL_ALIASES] == "Imatinib | Gleevec | STI571"

    assert "Imatinib" in row[COL_INTERVENTION_ALL_ALIASES_NORMALISED]
    assert "Gleevec" in row[COL_INTERVENTION_ALL_ALIASES_NORMALISED]
    assert "STI571" in row[COL_INTERVENTION_ALL_ALIASES_NORMALISED]

    assert "Imatinib" in row[COL_INTERVENTION_ALL_ALIASES_NORMALISED_FULL]
    assert "Gleevec" in row[COL_INTERVENTION_ALL_ALIASES_NORMALISED_FULL]
    assert "STI571" in row[COL_INTERVENTION_ALL_ALIASES_NORMALISED_FULL]


def test_default_ctgov_processed_output_dir_tracks_raw_inputs_version():
    input_json = Path("data/ctgov/drug_ontology/raw_inputs/ctgov/version_13022026/ctgov_input.json")

    assert default_output_dir_for_input(input_json) == Path(
        "data/ctgov/drug_ontology/processed_inputs/analysis/ctgov/version_13022026"
    )


def test_device_intervention_is_retained_but_not_drug_normalised():
    study = make_study(
        [
            {
                "type": "DEVICE",
                "name": "Radiotherapy device",
                "description": "Device intervention",
                "otherNames": ["Device alias"],
                "armGroupLabels": ["Arm A"],
            }
        ]
    )

    rows = extract_intervention_rows(study)

    assert len(rows) == 1
    row = rows[0]

    assert row[COL_INTERVENTION_TYPE] == "DEVICE"
    assert row[COL_INTERVENTION_ALL_ALIASES] == "Radiotherapy device | Device alias"
    assert row[COL_INTERVENTION_ALL_ALIASES_NORMALISED] == ""
    assert row[COL_INTERVENTION_ALL_ALIASES_NORMALISED_FULL] == ""


def test_other_intervention_is_retained_but_not_drug_normalised():
    study = make_study(
        [
            {
                "type": "OTHER",
                "name": "Observation",
                "description": "Observation only",
                "otherNames": ["Usual care"],
                "armGroupLabels": ["Observation Arm"],
            }
        ]
    )

    rows = extract_intervention_rows(study)

    assert len(rows) == 1
    row = rows[0]

    assert row[COL_INTERVENTION_TYPE] == "OTHER"
    assert row[COL_INTERVENTION_ALL_ALIASES] == "Observation | Usual care"
    assert row[COL_INTERVENTION_ALL_ALIASES_NORMALISED] == ""
    assert row[COL_INTERVENTION_ALL_ALIASES_NORMALISED_FULL] == ""


def test_biological_intervention_generates_normalised_aliases():
    study = make_study(
        [
            {
                "type": "BIOLOGICAL",
                "name": "Interferon alfa-2b",
                "description": "Given IV",
                "otherNames": ["recombinant interferon alfa", "Intron-A"],
                "armGroupLabels": ["Interferon Alfa-2b"],
            }
        ]
    )

    row = extract_intervention_rows(study)[0]

    assert row[COL_INTERVENTION_TYPE] == "BIOLOGICAL"
    assert "Interferon alfa-2b" in row[COL_INTERVENTION_ALL_ALIASES_NORMALISED]
    assert "recombinant interferon alfa" in row[COL_INTERVENTION_ALL_ALIASES_NORMALISED]
    assert "Intron-A" in row[COL_INTERVENTION_ALL_ALIASES_NORMALISED]


def test_combination_product_generates_normalised_aliases():
    study = make_study(
        [
            {
                "type": "COMBINATION_PRODUCT",
                "name": "Nivolumab and Relatlimab",
                "description": "Combination product",
                "otherNames": ["Opdualag"],
                "armGroupLabels": ["Combination Arm"],
            }
        ]
    )

    row = extract_intervention_rows(study)[0]

    assert row[COL_INTERVENTION_TYPE] == "COMBINATION_PRODUCT"
    assert "Nivolumab" in row[COL_INTERVENTION_ALL_ALIASES_NORMALISED]
    assert "Relatlimab" in row[COL_INTERVENTION_ALL_ALIASES_NORMALISED]
    assert "Opdualag" in row[COL_INTERVENTION_ALL_ALIASES_NORMALISED]
    assert "Nivolumab and Relatlimab" in row[COL_INTERVENTION_ALL_ALIASES_NORMALISED_FULL]


def test_normalised_aliases_split_combination_but_full_aliases_preserve_combination():
    aliases = "Nivolumab and Relatlimab | Opdualag"

    component_aliases = normalise_intervention_aliases(aliases)
    full_aliases = normalise_intervention_aliases_full(aliases)

    assert "Nivolumab" in component_aliases
    assert "Relatlimab" in component_aliases
    assert "Opdualag" in component_aliases

    assert "Nivolumab and Relatlimab" in full_aliases
    assert "Opdualag" in full_aliases


def test_placebo_and_generic_terms_are_removed_from_normalised_aliases():
    assert normalise_intervention_aliases("Placebo") == ""
    assert normalise_intervention_aliases("Standard of care") == ""
    assert normalise_intervention_aliases("Chemotherapy") == ""


def test_build_interventions_dataframe_from_json_file(tmp_path: Path):
    input_json = tmp_path / "ctgov.json"
    input_json.write_text(
        """
        {
          "protocolSection": {
            "identificationModule": {"nctId": "NCT00000002"},
            "armsInterventionsModule": {
              "interventions": [
                {
                  "type": "DRUG",
                  "name": "Pembrolizumab",
                  "description": "Anti-PD-1 antibody",
                  "otherNames": ["Keytruda"],
                  "armGroupLabels": ["Experimental"]
                }
              ]
            }
          }
        }
        """,
        encoding="utf-8",
    )

    df = build_interventions_dataframe(input_json)

    assert list(df.columns) == CORE_INTERVENTION_COLUMNS
    assert len(df) == 1
    assert df.loc[0, COL_NCT_ID] == "NCT00000002"
    assert df.loc[0, COL_INTERVENTION_NAME] == "Pembrolizumab"
    assert df.loc[0, COL_INTERVENTION_OTHER_NAMES] == "Keytruda"


def test_build_interventions_dataframe_from_ndjson_file(tmp_path: Path):
    input_json = tmp_path / "ctgov.ndjson"
    input_json.write_text(
        "\n".join(
            [
                '{"protocolSection":{"identificationModule":{"nctId":"NCT1"},"armsInterventionsModule":{"interventions":[{"type":"DRUG","name":"Drug A"}]}}}',
                '{"protocolSection":{"identificationModule":{"nctId":"NCT2"},"armsInterventionsModule":{"interventions":[{"type":"BIOLOGICAL","name":"Drug B"}]}}}',
            ]
        ),
        encoding="utf-8",
    )

    df = build_interventions_dataframe(input_json)

    assert len(df) == 2
    assert set(df[COL_NCT_ID]) == {"NCT1", "NCT2"}
