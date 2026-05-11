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

logger = logging.getLogger(__name__)

REQUIRED_STABLE_COLUMNS = [
    "nct_id",
    "intervention_index",
    "intervention_type",
    "intervention_name",
    "intervention_description",
]


def read_interventions_tsv(input_tsv: Path) -> pd.DataFrame:
    if not input_tsv.exists():
        raise FileNotFoundError(f"Input TSV not found: {input_tsv}")

    df = pd.read_csv(
        input_tsv,
        sep="\t",
        dtype=str,
        keep_default_na=False,
        encoding="utf-8",
    )

    missing = [col for col in REQUIRED_STABLE_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(
            f"Input TSV is missing required stable columns: {missing}. "
            f"Found columns: {list(df.columns)}"
        )

    df["intervention_index"] = pd.to_numeric(
        df["intervention_index"],
        errors="raise",
    ).astype(int)

    return df


def load_tsv_to_postgres(
    input_tsv: Path,
    source_version: str | None,
    database_url: str,
) -> uuid.UUID:
    df = read_interventions_tsv(input_tsv)
    load_batch_id = uuid.uuid4()

    records = []
    for row in df.to_dict(orient="records"):
        records.append(
            {
                "load_batch_id": str(load_batch_id),
                "nct_id": row["nct_id"],
                "intervention_index": int(row["intervention_index"]),
                "intervention_type": row.get("intervention_type", ""),
                "intervention_name": row.get("intervention_name", ""),
                "intervention_description": row.get("intervention_description", ""),
                "source_tsv": str(input_tsv),
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
                    'ctgov_intervention_tsv',
                    :source_file,
                    :source_version,
                    'started'
                )
                """
            ),
            {
                "load_batch_id": str(load_batch_id),
                "source_file": str(input_tsv),
                "source_version": source_version,
            },
        )

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
                    source_tsv,
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
                    :source_tsv,
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
        description="Load the canonical CTGov interventions TSV into PostgreSQL."
    )
    parser.add_argument(
        "--input_tsv",
        required=True,
        type=Path,
        help="Path to TSV produced by extract.py.",
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

    load_batch_id = load_tsv_to_postgres(
        input_tsv=args.input_tsv,
        source_version=args.source_version,
        database_url=database_url,
    )

    logger.info("Loaded TSV into PostgreSQL with load_batch_id=%s", load_batch_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())