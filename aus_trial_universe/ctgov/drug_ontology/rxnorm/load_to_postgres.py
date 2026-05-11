from __future__ import annotations

import argparse
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

from aus_trial_universe.ctgov.drug_ontology.common.classification_schema import (
    COL_INTERVENTION_ALL_ALIASES_NORMALISED,
    TARGET_DRUG_INTERVENTION_TYPES,
    clean_text,
    split_display_values,
)
from aus_trial_universe.ctgov.drug_ontology.rxnorm.matcher import (
    IngredientResolution,
    RxnConsoIndex,
    RxnRelIndex,
    TermResolution,
    resolve_term,
)

logger = logging.getLogger(__name__)


FETCH_STAGING_ROWS_SQL = text(
    """
    SELECT
        nct_id,
        intervention_index,
        intervention_type,
        row_payload
    FROM ctgov.intervention_staging
    ORDER BY nct_id, intervention_index
    """
)

CREATE_LOAD_BATCH_SQL = text(
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
        'rxnorm_term_resolution',
        :source_file,
        :source_version,
        'started'
    )
    """
)

COMPLETE_LOAD_BATCH_SQL = text(
    """
    UPDATE curation.load_batch
    SET
        completed_at = now(),
        row_count = :row_count,
        status = 'completed'
    WHERE load_batch_id = :load_batch_id
    """
)

UPSERT_CT_GOV_DRUG_TERM_SQL = text(
    """
    INSERT INTO drug_identity.ctgov_drug_term (
        input_drug_name
    )
    VALUES (
        :input_drug_name
    )
    ON CONFLICT (input_drug_name)
    DO NOTHING
    """
)

INSERT_INTERVENTION_DRUG_TERM_LINK_SQL = text(
    """
    INSERT INTO drug_identity.ctgov_intervention_drug_term_link (
        ctgov_intervention_key,
        nct_id,
        intervention_index,
        input_drug_name
    )
    VALUES (
        :ctgov_intervention_key,
        :nct_id,
        :intervention_index,
        :input_drug_name
    )
    ON CONFLICT (
        ctgov_intervention_key,
        input_drug_name
    )
    DO NOTHING
    """
)

UPSERT_RXNORM_MAPPING_SQL = text(
    """
    INSERT INTO drug_identity.ctgov_drug_term_rxnorm_mapping (
        input_drug_name,
        match_status,
        matched_term,
        rxnorm_rxcui,
        rxnorm_canonical_name,
        rxnorm_term_type,
        rxnorm_ingredient_rxcui,
        rxnorm_ingredient_name,
        rxnorm_ingredient_term_type
    )
    VALUES (
        :input_drug_name,
        :match_status,
        :matched_term,
        :rxnorm_rxcui,
        :rxnorm_canonical_name,
        :rxnorm_term_type,
        :rxnorm_ingredient_rxcui,
        :rxnorm_ingredient_name,
        :rxnorm_ingredient_term_type
    )
    ON CONFLICT (input_drug_name)
    DO UPDATE SET
        match_status = EXCLUDED.match_status,
        matched_term = EXCLUDED.matched_term,
        rxnorm_rxcui = EXCLUDED.rxnorm_rxcui,
        rxnorm_canonical_name = EXCLUDED.rxnorm_canonical_name,
        rxnorm_term_type = EXCLUDED.rxnorm_term_type,
        rxnorm_ingredient_rxcui = EXCLUDED.rxnorm_ingredient_rxcui,
        rxnorm_ingredient_name = EXCLUDED.rxnorm_ingredient_name,
        rxnorm_ingredient_term_type = EXCLUDED.rxnorm_ingredient_term_type
    """
)


def parse_row_payload(payload: object) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload

    if isinstance(payload, str) and payload.strip():
        parsed = json.loads(payload)
        if isinstance(parsed, dict):
            return parsed

    return {}


def ctgov_intervention_key(nct_id: object, intervention_index: object) -> str:
    return f"{clean_text(nct_id)}:{int(intervention_index)}"


def input_drug_names_for_staging_row(row: Mapping[str, Any]) -> list[str]:
    """Return RxNorm lookup terms for one CTGov intervention staging row.

    RxNorm Part 2 deliberately uses only:
      intervention_all_aliases_normalised

    It does not use:
      - raw intervention_name
      - raw intervention_otherNames
      - intervention_all_aliases_normalised_full

    This keeps the RxNorm output as one row per normalized drug term.
    """
    intervention_type = clean_text(row.get("intervention_type")).upper()
    if intervention_type not in TARGET_DRUG_INTERVENTION_TYPES:
        return []

    payload = parse_row_payload(row.get("row_payload"))
    return split_display_values(payload.get(COL_INTERVENTION_ALL_ALIASES_NORMALISED, ""))


def fetch_intervention_staging_rows(engine: Engine) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(FETCH_STAGING_ROWS_SQL).mappings().all()]


def create_load_batch(
    conn: Connection,
    load_batch_id: uuid.UUID,
    rxnorm_rrf_dir: Path,
    rxnorm_source_version: str,
) -> None:
    conn.execute(
        CREATE_LOAD_BATCH_SQL,
        {
            "load_batch_id": str(load_batch_id),
            "source_file": str(rxnorm_rrf_dir),
            "source_version": rxnorm_source_version,
        },
    )


def complete_load_batch(conn: Connection, load_batch_id: uuid.UUID, row_count: int) -> None:
    conn.execute(
        COMPLETE_LOAD_BATCH_SQL,
        {
            "load_batch_id": str(load_batch_id),
            "row_count": row_count,
        },
    )


def upsert_ctgov_drug_term(conn: Connection, input_drug_name: str) -> None:
    conn.execute(
        UPSERT_CT_GOV_DRUG_TERM_SQL,
        {
            "input_drug_name": input_drug_name,
        },
    )


def insert_intervention_drug_term_link(
    conn: Connection,
    staging_row: Mapping[str, Any],
    input_drug_name: str,
) -> None:
    conn.execute(
        INSERT_INTERVENTION_DRUG_TERM_LINK_SQL,
        {
            "ctgov_intervention_key": ctgov_intervention_key(
                staging_row["nct_id"],
                staging_row["intervention_index"],
            ),
            "nct_id": staging_row["nct_id"],
            "intervention_index": staging_row["intervention_index"],
            "input_drug_name": input_drug_name,
        },
    )


def load_rxnorm_indexes(rxnorm_rrf_dir: Path) -> tuple[RxnConsoIndex, RxnRelIndex]:
    logger.info("Loading RxNorm RXNCONSO index from %s", rxnorm_rrf_dir)
    conso_index = RxnConsoIndex.from_rrf_dir(rxnorm_rrf_dir)
    logger.info(
        "Loaded %d RXNCONSO rows and %d normalized string keys",
        len(conso_index.rows),
        len(conso_index.by_normalized_str),
    )

    logger.info("Loading RxNorm RXNREL index from %s", rxnorm_rrf_dir)
    rel_index = RxnRelIndex.from_rrf_dir(rxnorm_rrf_dir)
    logger.info("Loaded %d RXNREL relationship rows", len(rel_index.rows))

    return conso_index, rel_index


def resolve_with_ingredient(
    input_drug_name: str,
    conso_index: RxnConsoIndex,
    rel_index: RxnRelIndex,
) -> tuple[TermResolution, IngredientResolution]:
    resolution = resolve_term(input_drug_name, conso_index)
    ingredient_resolution = rel_index.resolve_ingredient(
        matched_rxcui=resolution.rxcui,
        conso_index=conso_index,
        source_term=input_drug_name,
    )
    return resolution, ingredient_resolution


def upsert_rxnorm_mapping(
    conn: Connection,
    input_drug_name: str,
    resolution: TermResolution,
    ingredient_resolution: IngredientResolution,
) -> None:
    conn.execute(
        UPSERT_RXNORM_MAPPING_SQL,
        {
            "input_drug_name": input_drug_name,
            "match_status": resolution.match_status,
            "matched_term": resolution.matched_term or None,
            "rxnorm_rxcui": resolution.rxcui or None,
            "rxnorm_canonical_name": resolution.canonical_name or None,
            "rxnorm_term_type": resolution.canonical_tty or None,
            "rxnorm_ingredient_rxcui": ingredient_resolution.ingredient_rxcui or None,
            "rxnorm_ingredient_name": ingredient_resolution.ingredient_name or None,
            "rxnorm_ingredient_term_type": ingredient_resolution.ingredient_tty or None,
        },
    )


def resolve_rxnorm_terms_from_postgres(
    database_url: str,
    rxnorm_rrf_dir: Path,
    rxnorm_source_version: str,
) -> uuid.UUID:
    engine = create_engine(database_url)
    conso_index, rel_index = load_rxnorm_indexes(rxnorm_rrf_dir)

    staging_rows = fetch_intervention_staging_rows(engine)
    logger.info("Read %d CTGov intervention staging rows", len(staging_rows))

    load_batch_id = uuid.uuid4()
    unique_input_drug_names: set[str] = set()
    link_count = 0

    with engine.begin() as conn:
        create_load_batch(conn, load_batch_id, rxnorm_rrf_dir, rxnorm_source_version)

        for row_number, staging_row in enumerate(staging_rows, start=1):
            for input_drug_name in input_drug_names_for_staging_row(staging_row):
                upsert_ctgov_drug_term(conn, input_drug_name)
                insert_intervention_drug_term_link(conn, staging_row, input_drug_name)
                unique_input_drug_names.add(input_drug_name)
                link_count += 1

            if row_number % 1000 == 0:
                logger.info("Processed %d staging rows", row_number)

        logger.info("Resolving %d unique input drug names to RxNorm", len(unique_input_drug_names))

        for input_drug_name in sorted(unique_input_drug_names):
            resolution, ingredient_resolution = resolve_with_ingredient(
                input_drug_name=input_drug_name,
                conso_index=conso_index,
                rel_index=rel_index,
            )
            upsert_rxnorm_mapping(
                conn=conn,
                input_drug_name=input_drug_name,
                resolution=resolution,
                ingredient_resolution=ingredient_resolution,
            )

        complete_load_batch(conn, load_batch_id, row_count=len(unique_input_drug_names))

    logger.info(
        "Inserted %d intervention-drug links and resolved %d unique input drug names with load_batch_id=%s",
        link_count,
        len(unique_input_drug_names),
        load_batch_id,
    )
    return load_batch_id


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Resolve SQL-loaded CTGov normalized drug terms to RxNorm."
    )
    parser.add_argument(
        "--rxnorm_rrf_dir",
        required=True,
        type=Path,
        help="Directory containing RXNCONSO.RRF and RXNREL.RRF.",
    )
    parser.add_argument(
        "--rxnorm_source_version",
        required=True,
        help="RxNorm source version label, e.g. RxNorm_full_03022026.",
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
        raise RuntimeError(f"DATABASE_URL is not set. Check {args.env_file}")

    resolve_rxnorm_terms_from_postgres(
        database_url=database_url,
        rxnorm_rrf_dir=args.rxnorm_rrf_dir,
        rxnorm_source_version=args.rxnorm_source_version,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
