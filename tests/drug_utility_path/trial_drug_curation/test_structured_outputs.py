from aus_trial_universe.drug_utility_path.trial_drug_curation.llm.structured_outputs import (
    tsv_from_payload,
)


def test_tsv_from_payload_writes_blank_cells_for_na_and_null_values():
    tsv = tsv_from_payload(
        {
            "rows": [
                {
                    "trialId": "NCT00000001",
                    "drugName": "Drug A",
                    "drugRegime": "NA",
                    "trialArm": "Experimental",
                    "drugEvidenceSource": "intervention",
                    "cancerTypes": "Cancer",
                    "Oncotree": "N/A",
                    "standardisedName": None,
                    "pottrDrugClass": "",
                    "cancerDrug": True,
                    "dosage": "NA",
                    "TGAStatus": "",
                    "PBSIndicationStatus": "N/A",
                }
            ]
        }
    )

    lines = tsv.splitlines()
    row = lines[1].split("\t")
    assert row[2] == ""
    assert row[6] == ""
    assert row[7] == ""
    assert row[10] == ""
    assert row[12] == ""
