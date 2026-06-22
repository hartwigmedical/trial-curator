import json
from pathlib import Path

import pandas as pd
import pytest

from aus_trial_universe.drug_utility_path.ctgov.drug_ontology.ctgov import load_interventions as load_to_postgres
from aus_trial_universe.drug_utility_path.ctgov.drug_ontology.shared.schema import (
    COL_INTERVENTION_DESCRIPTION,
    COL_INTERVENTION_INDEX,
    COL_INTERVENTION_NAME,
    COL_INTERVENTION_TYPE,
    COL_NCT_ID,
)


def sample_intervention_df():
    return pd.DataFrame(
        [
            {
                COL_NCT_ID: "NCT00000001",
                COL_INTERVENTION_INDEX: "0",
                COL_INTERVENTION_TYPE: "DRUG",
                COL_INTERVENTION_NAME: "Imatinib",
                COL_INTERVENTION_DESCRIPTION: "Imatinib oral tablet",
                "intervention_otherNames": "Gleevec | STI571",
                "intervention_armGroupLabels": "Experimental Arm",
                "intervention_all_aliases": "Imatinib | Gleevec | STI571",
                "intervention_all_aliases_normalised": "Imatinib | Gleevec | STI571",
                "intervention_all_aliases_normalised_full": "Imatinib | Gleevec | STI571",
            }
        ]
    )


def test_build_interventions_from_json_converts_intervention_index(monkeypatch, tmp_path: Path):
    input_json = tmp_path / "ctgov.json"
    input_json.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        load_to_postgres,
        "build_interventions_dataframe",
        lambda _: sample_intervention_df(),
    )

    df = load_to_postgres.build_interventions_from_json(input_json)

    assert df[COL_INTERVENTION_INDEX].dtype.kind in {"i", "u"}
    assert int(df.loc[0, COL_INTERVENTION_INDEX]) == 0


def test_build_interventions_from_json_rejects_missing_required_columns(monkeypatch, tmp_path: Path):
    input_json = tmp_path / "ctgov.json"
    input_json.write_text("{}", encoding="utf-8")

    bad_df = sample_intervention_df().drop(columns=[COL_INTERVENTION_NAME])
    monkeypatch.setattr(load_to_postgres, "build_interventions_dataframe", lambda _: bad_df)

    with pytest.raises(ValueError, match="missing required stable columns"):
        load_to_postgres.build_interventions_from_json(input_json)


class FakeConnection:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((str(sql), params))


class FakeBeginContext:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeEngine:
    def __init__(self):
        self.conn = FakeConnection()

    def begin(self):
        return FakeBeginContext(self.conn)


def test_load_json_to_postgres_inserts_source_file_and_row_payload(monkeypatch, tmp_path: Path):
    input_json = tmp_path / "ctgov.json"
    input_json.write_text("{}", encoding="utf-8")

    fake_engine = FakeEngine()

    monkeypatch.setattr(
        load_to_postgres,
        "build_interventions_from_json",
        lambda _: sample_intervention_df(),
    )
    monkeypatch.setattr(load_to_postgres, "create_engine", lambda database_url: fake_engine)

    load_batch_id = load_to_postgres.load_json_to_postgres(
        input_json=input_json,
        source_version="ctgov_test_snapshot",
        database_url="postgresql://unused",
    )

    assert load_batch_id is not None
    assert len(fake_engine.conn.calls) == 3

    create_batch_params = fake_engine.conn.calls[0][1]
    assert create_batch_params["source_file"] == str(input_json)
    assert create_batch_params["source_version"] == "ctgov_test_snapshot"

    insert_records = fake_engine.conn.calls[1][1]
    assert isinstance(insert_records, list)
    assert len(insert_records) == 1

    record = insert_records[0]
    assert record["nct_id"] == "NCT00000001"
    assert record["intervention_index"] == 0
    assert record["intervention_name"] == "Imatinib"
    assert record["source_file"] == str(input_json)
    assert record["source_version"] == "ctgov_test_snapshot"

    payload = json.loads(record["row_payload"])
    assert payload["intervention_otherNames"] == "Gleevec | STI571"
    assert payload["intervention_all_aliases_normalised"] == "Imatinib | Gleevec | STI571"

    complete_batch_params = fake_engine.conn.calls[2][1]
    assert complete_batch_params["row_count"] == 1
