from __future__ import annotations

import argparse
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Sequence

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from aus_trial_universe.ctgov.drug_ontology.shared.schema import (
    COL_INTERVENTION_DESCRIPTION,
    COL_INTERVENTION_INDEX,
    COL_INTERVENTION_NAME,
    COL_INTERVENTION_TYPE,
    COL_NCT_ID,
    REQUIRED_STABLE_INTERVENTION_COLUMNS,
)
from aus_trial_universe.ctgov.drug_ontology.ctgov.interventions import (
    build_interventions_dataframe,
)

logger = logging.getLogger(__name__)


def build_interventions_from_json(input_json: Path) -> pd.DataFrame:
    if not input_json.exists():
        raise FileNotFoundError(f"Input JSON not found: {input_json}")

    df = build_interventions_dataframe(input_json)

    missing = [col for col in REQUIRED_STABLE_INTERVENTION_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(
            f"Extracted intervention dataframe is missing required stable columns: {missing}. "
            f"Found columns: {list(df.columns)}"
        )

    df[COL_INTERVENTION_INDEX] = pd.to_numeric(
        df[COL_INTERVENTION_INDEX],
        errors="raise",
    ).astype(int)

    return df


def load_json_to_postgres(
    input_json: Path,
    source_version: str | None,
    database_url: str,
) -> uuid.UUID:
    df = build_interventions_from_json(input_json)
    load_batch_id = uuid.uuid4()

    records = []
    for row in df.to_dict(orient="records"):
        records.append(
            {
                "load_batch_id": str(load_batch_id),
                "nct_id": row[COL_NCT_ID],
                "intervention_index": int(row[COL_INTERVENTION_INDEX]),
                "intervention_type": row.get(COL_INTERVENTION_TYPE, ""),
                "intervention_name": row.get(COL_INTERVENTION_NAME, ""),
                "intervention_description": row.get(COL_INTERVENTION_DESCRIPTION, ""),
                "source_file": str(input_json),
                "source_version": source_version,
                "row_payload": json.dumps(row, ensure_ascii=False),
            }
        )

    engine = create_engine(database_url)

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO curation.load_batch (
                    load_batch_id,
                    source_name,
                    source_file,
                    source_version,
                    status
                )
                VALUES (
                    :load_batch_id,
                    'ctgov_intervention_json',
                    :source_file,
                    :source_version,
                    'started'
                )
                """
            ),
            {
                "load_batch_id": str(load_batch_id),
                "source_file": str(input_json),
                "source_version": source_version,
            },
        )

        if records:
            conn.execute(
                text(
                    """
                    INSERT INTO ctgov.intervention_staging (
                        load_batch_id,
                        nct_id,
                        intervention_index,
                        intervention_type,
                        intervention_name,
                        intervention_description,
                        source_file,
                        source_version,
                        row_payload
                    )
                    VALUES (
                        :load_batch_id,
                        :nct_id,
                        :intervention_index,
                        :intervention_type,
                        :intervention_name,
                        :intervention_description,
                        :source_file,
                        :source_version,
                        CAST(:row_payload AS jsonb)
                    )
                    """
                ),
                records,
            )

        conn.execute(
            text(
                """
                UPDATE curation.load_batch
                SET
                    completed_at = now(),
                    row_count = :row_count,
                    status = 'completed'
                WHERE load_batch_id = :load_batch_id
                """
            ),
            {
                "row_count": len(records),
                "load_batch_id": str(load_batch_id),
            },
        )

    return load_batch_id


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Extract CTGov interventions from JSON/NDJSON and load them directly "
            "into PostgreSQL. No accepted TSV is written by this loader."
        )
    )
    parser.add_argument(
        "--input_json",
        required=True,
        type=Path,
        help="Path to CTGov JSON input file. Supports JSON and JSON Lines / NDJSON.",
    )
    parser.add_argument(
        "--source_version",
        default=None,
        help="Optional source version label, e.g. ctgov_snapshot_2026_05_06.",
    )
    parser.add_argument(
        "--env_file",
        type=Path,
        default=Path(".env"),
        help="Path to .env file containing DATABASE_URL. Default: .env",
    )
    parser.add_argument(
        "--log_level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    load_dotenv(dotenv_path=args.env_file, override=True)

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set. Check your .env file.")

    load_batch_id = load_json_to_postgres(
        input_json=args.input_json,
        source_version=args.source_version,
        database_url=database_url,
    )

    logger.info("Loaded CTGov JSON into PostgreSQL with load_batch_id=%s", load_batch_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
