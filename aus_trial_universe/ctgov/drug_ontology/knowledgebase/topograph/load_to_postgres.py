from __future__ import annotations

import argparse
import logging
import os
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Sequence

from aus_trial_universe.ctgov.drug_ontology.knowledgebase.topograph.topograph import (
    TopographRecords,
    read_topograph_master_tsv,
)

logger = logging.getLogger(__name__)

CREATE_LOAD_BATCH_SQL = """
    INSERT INTO curation.load_batch (
        load_batch_id,
        source_name,
        source_file,
        source_version,
        status
    )
    VALUES (
        :load_batch_id,
        'topograph_master',
        :source_file,
        :source_version,
        'started'
    )
    """

COMPLETE_LOAD_BATCH_SQL = """
    UPDATE curation.load_batch
    SET
        completed_at = now(),
        row_count = :row_count,
        status = 'completed'
    WHERE load_batch_id = :load_batch_id
    """

DELETE_EXISTING_TOPOGRAPH_VERSION_SQL = """
    DELETE FROM oncology_kb.topograph_raw_assertion
    WHERE topograph_source_version = :source_version
    """

INSERT_RAW_ASSERTION_SQL = """
    INSERT INTO oncology_kb.topograph_raw_assertion (
        load_batch_id,
        raw_assertion_key,
        topograph_raw_row_index,
        tier,
        biomarker,
        alteration,
        tumour_type,
        drugs,
        comments,
        evidence,
        evidence_direction,
        topograph_source_version
    )
    VALUES (
        :load_batch_id,
        :raw_assertion_key,
        :topograph_raw_row_index,
        :tier,
        :biomarker,
        :alteration,
        :tumour_type,
        :drugs,
        :comments,
        :evidence,
        :evidence_direction,
        :source_version
    )
    RETURNING topograph_raw_assertion_id
    """

INSERT_THERAPY_OPTION_SQL = """
    INSERT INTO oncology_kb.topograph_therapy_option (
        topograph_raw_assertion_id,
        load_batch_id,
        therapy_option_key,
        raw_assertion_key,
        topograph_raw_row_index,
        therapy_option_index,
        therapy_option_raw,
        therapy_option_norm,
        therapy_option_type,
        component_count,
        topograph_source_version
    )
    VALUES (
        :topograph_raw_assertion_id,
        :load_batch_id,
        :therapy_option_key,
        :raw_assertion_key,
        :topograph_raw_row_index,
        :therapy_option_index,
        :therapy_option_raw,
        :therapy_option_norm,
        :therapy_option_type,
        :component_count,
        :source_version
    )
    RETURNING topograph_therapy_option_id
    """

INSERT_THERAPY_COMPONENT_SQL = """
    INSERT INTO oncology_kb.topograph_therapy_component (
        topograph_therapy_option_id,
        load_batch_id,
        therapy_component_key,
        therapy_option_key,
        raw_assertion_key,
        topograph_raw_row_index,
        therapy_option_index,
        component_index,
        component_raw,
        component_norm,
        topograph_source_version
    )
    VALUES (
        :topograph_therapy_option_id,
        :load_batch_id,
        :therapy_component_key,
        :therapy_option_key,
        :raw_assertion_key,
        :topograph_raw_row_index,
        :therapy_option_index,
        :component_index,
        :component_raw,
        :component_norm,
        :source_version
    )
    """


def _sql(sql: str):
    """Return a SQLAlchemy text clause without making SQLAlchemy a test-import dependency."""
    from sqlalchemy import text

    return text(sql)


def create_load_batch(conn: Any, load_batch_id: uuid.UUID, source_file: str, source_version: str) -> None:
    conn.execute(
        _sql(CREATE_LOAD_BATCH_SQL),
        {
            "load_batch_id": str(load_batch_id),
            "source_file": source_file,
            "source_version": source_version,
        },
    )


def complete_load_batch(conn: Any, load_batch_id: uuid.UUID, row_count: int) -> None:
    conn.execute(_sql(COMPLETE_LOAD_BATCH_SQL), {"load_batch_id": str(load_batch_id), "row_count": row_count})


def insert_topograph_records(conn: Any, records: TopographRecords, load_batch_id: uuid.UUID, source_version: str) -> int:
    raw_id_by_key: dict[str, int] = {}
    option_id_by_key: dict[str, int] = {}

    conn.execute(_sql(DELETE_EXISTING_TOPOGRAPH_VERSION_SQL), {"source_version": source_version})

    for raw in records.raw_assertions:
        params = asdict(raw)
        params["load_batch_id"] = str(load_batch_id)
        result = conn.execute(_sql(INSERT_RAW_ASSERTION_SQL), params)
        raw_id_by_key[raw.raw_assertion_key] = int(result.scalar_one())

    for option in records.therapy_options:
        params = asdict(option)
        params["load_batch_id"] = str(load_batch_id)
        params["topograph_raw_assertion_id"] = raw_id_by_key[option.raw_assertion_key]
        result = conn.execute(_sql(INSERT_THERAPY_OPTION_SQL), params)
        option_id_by_key[option.therapy_option_key] = int(result.scalar_one())

    component_params = []
    for component in records.therapy_components:
        params = asdict(component)
        params["load_batch_id"] = str(load_batch_id)
        params["topograph_therapy_option_id"] = option_id_by_key[component.therapy_option_key]
        component_params.append(params)
    if component_params:
        conn.execute(_sql(INSERT_THERAPY_COMPONENT_SQL), component_params)

    return len(records.raw_assertions) + len(records.therapy_options) + len(records.therapy_components)


def load_topograph_to_postgres(
    database_url: str,
    topograph_master_tsv: Path,
    topograph_source_version: str,
) -> uuid.UUID:
    records = read_topograph_master_tsv(topograph_master_tsv, source_version=topograph_source_version)
    logger.info(
        "Parsed TOPOGRAPH: %d raw assertions, %d therapy options, %d therapy components",
        len(records.raw_assertions),
        len(records.therapy_options),
        len(records.therapy_components),
    )

    from sqlalchemy import create_engine

    engine = create_engine(database_url)
    load_batch_id = uuid.uuid4()
    with engine.begin() as conn:
        create_load_batch(
            conn,
            load_batch_id=load_batch_id,
            source_file=str(topograph_master_tsv),
            source_version=topograph_source_version,
        )
        row_count = insert_topograph_records(conn, records, load_batch_id, topograph_source_version)
        complete_load_batch(conn, load_batch_id=load_batch_id, row_count=row_count)

    logger.info("Loaded TOPOGRAPH source_version=%s; load_batch_id=%s", topograph_source_version, load_batch_id)
    return load_batch_id


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Load TOPOGRAPH-master.tsv into normalized PostgreSQL oncology KB tables.")
    parser.add_argument("--topograph_master_tsv", required=True, type=Path, help="Path to TOPOGRAPH-master.tsv.")
    parser.add_argument("--topograph_source_version", required=True, help="Source version label, e.g. Topograph_13052026.")
    parser.add_argument("--env_file", type=Path, default=Path(".env"), help="Path to .env containing DATABASE_URL.")
    parser.add_argument("--log_level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    from dotenv import load_dotenv

    load_dotenv(dotenv_path=args.env_file, override=True)
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError(f"DATABASE_URL is not set. Check {args.env_file}")

    load_topograph_to_postgres(
        database_url=database_url,
        topograph_master_tsv=args.topograph_master_tsv,
        topograph_source_version=args.topograph_source_version,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
