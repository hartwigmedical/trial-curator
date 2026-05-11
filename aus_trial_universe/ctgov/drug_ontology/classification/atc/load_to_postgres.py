from __future__ import annotations

"""Load ATC mappings for RxNorm ingredient anchors into PostgreSQL.

Input is the simplified Part 2 RxNorm table:

    drug_identity.ctgov_drug_term_rxnorm_mapping

Selection rule:

    match_status = 'MATCHED'
    AND rxnorm_ingredient_rxcui IS NOT NULL

The output is one row per RxNorm ingredient anchor × ATC code.
No Python-side TSV is written. TSV outputs are SQL-derived exports only.
"""

import argparse
import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Sequence

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

from aus_trial_universe.ctgov.drug_ontology.classification.atc.ingredient_to_atc import (
    RXNCONSO_FILENAME,
    AtcResolution,
    AtcTree,
    clean_text,
    load_rxnconso_atc_index,
    resolve_ingredient_to_atc,
)

logger = logging.getLogger(__name__)


FETCH_RXNORM_INGREDIENT_ANCHORS_SQL = text(
    """
    SELECT DISTINCT
        rxnorm_ingredient_rxcui,
        rxnorm_ingredient_name
    FROM drug_identity.ctgov_drug_term_rxnorm_mapping
    WHERE match_status = 'MATCHED'
      AND rxnorm_ingredient_rxcui IS NOT NULL
      AND rxnorm_ingredient_rxcui <> ''
    ORDER BY rxnorm_ingredient_name, rxnorm_ingredient_rxcui
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
        'atc_ingredient_resolution',
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

TRUNCATE_ATC_MAPPING_SQL = text(
    """
    TRUNCATE TABLE drug_classification.rxnorm_ingredient_atc_mapping
    """
)

INSERT_ATC_MAPPING_SQL = text(
    """
    INSERT INTO drug_classification.rxnorm_ingredient_atc_mapping (
        load_batch_id,
        rxnorm_ingredient_rxcui,
        rxnorm_ingredient_name,
        atc_match_status,
        atc_code,
        atc_name,
        atc_level,
        atc_l1_code,
        atc_l1_name,
        atc_l2_code,
        atc_l2_name,
        atc_l3_code,
        atc_l3_name,
        atc_l4_code,
        atc_l4_name,
        atc_l5_code,
        atc_l5_name
    )
    VALUES (
        :load_batch_id,
        :rxnorm_ingredient_rxcui,
        :rxnorm_ingredient_name,
        :atc_match_status,
        :atc_code,
        :atc_name,
        :atc_level,
        :atc_l1_code,
        :atc_l1_name,
        :atc_l2_code,
        :atc_l2_name,
        :atc_l3_code,
        :atc_l3_name,
        :atc_l4_code,
        :atc_l4_name,
        :atc_l5_code,
        :atc_l5_name
    )
    ON CONFLICT (
        rxnorm_ingredient_rxcui,
        atc_match_status,
        atc_code
    )
    DO UPDATE SET
        load_batch_id = EXCLUDED.load_batch_id,
        rxnorm_ingredient_name = EXCLUDED.rxnorm_ingredient_name,
        atc_name = EXCLUDED.atc_name,
        atc_level = EXCLUDED.atc_level,
        atc_l1_code = EXCLUDED.atc_l1_code,
        atc_l1_name = EXCLUDED.atc_l1_name,
        atc_l2_code = EXCLUDED.atc_l2_code,
        atc_l2_name = EXCLUDED.atc_l2_name,
        atc_l3_code = EXCLUDED.atc_l3_code,
        atc_l3_name = EXCLUDED.atc_l3_name,
        atc_l4_code = EXCLUDED.atc_l4_code,
        atc_l4_name = EXCLUDED.atc_l4_name,
        atc_l5_code = EXCLUDED.atc_l5_code,
        atc_l5_name = EXCLUDED.atc_l5_name
    """
)


@dataclass(frozen=True)
class RxNormIngredientAnchor:
    rxnorm_ingredient_rxcui: str
    rxnorm_ingredient_name: str


def fetch_rxnorm_ingredient_anchors(engine: Engine) -> list[RxNormIngredientAnchor]:
    with engine.connect() as conn:
        rows = conn.execute(FETCH_RXNORM_INGREDIENT_ANCHORS_SQL).mappings().all()

    return [
        RxNormIngredientAnchor(
            rxnorm_ingredient_rxcui=clean_text(row["rxnorm_ingredient_rxcui"]),
            rxnorm_ingredient_name=clean_text(row["rxnorm_ingredient_name"]),
        )
        for row in rows
    ]


def create_load_batch(
    conn: Connection,
    load_batch_id: uuid.UUID,
    source_file: str,
    source_version: str,
) -> None:
    conn.execute(
        CREATE_LOAD_BATCH_SQL,
        {
            "load_batch_id": str(load_batch_id),
            "source_file": source_file,
            "source_version": source_version,
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


def mapping_record(
    anchor: RxNormIngredientAnchor,
    resolution: AtcResolution,
    load_batch_id: uuid.UUID,
) -> dict[str, object]:
    return {
        "load_batch_id": str(load_batch_id),
        "rxnorm_ingredient_rxcui": anchor.rxnorm_ingredient_rxcui,
        "rxnorm_ingredient_name": anchor.rxnorm_ingredient_name or None,
        **asdict(resolution),
    }


def load_atc_mappings_to_postgres(
    database_url: str,
    rxnorm_rrf_dir: Path,
    atc_tree_tsv: Path,
    atc_source_version: str,
) -> uuid.UUID:
    engine = create_engine(database_url)
    rxnconso_path = rxnorm_rrf_dir / RXNCONSO_FILENAME

    anchors = fetch_rxnorm_ingredient_anchors(engine)
    logger.info("Fetched %d distinct RxNorm ingredient anchors for ATC mapping", len(anchors))

    target_rxcuis = {
        anchor.rxnorm_ingredient_rxcui
        for anchor in anchors
        if anchor.rxnorm_ingredient_rxcui
    }

    tree = AtcTree.from_tsv(atc_tree_tsv)
    atc_by_rxcui = load_rxnconso_atc_index(
        rxnconso_path=rxnconso_path,
        target_rxcuis=target_rxcuis,
    )

    load_batch_id = uuid.uuid4()
    source_file = json.dumps(
        {
            "rxnorm_rrf_dir": str(rxnorm_rrf_dir),
            "rxnconso_path": str(rxnconso_path),
            "atc_tree_tsv": str(atc_tree_tsv),
        },
        ensure_ascii=False,
    )

    records: list[dict[str, object]] = []
    for anchor in anchors:
        resolutions = resolve_ingredient_to_atc(
            rxnorm_ingredient_rxcui=anchor.rxnorm_ingredient_rxcui,
            atc_by_rxcui=atc_by_rxcui,
            tree=tree,
        )
        for resolution in resolutions:
            records.append(
                mapping_record(
                    anchor=anchor,
                    resolution=resolution,
                    load_batch_id=load_batch_id,
                )
            )

    with engine.begin() as conn:
        create_load_batch(conn, load_batch_id, source_file=source_file, source_version=atc_source_version)
        conn.execute(TRUNCATE_ATC_MAPPING_SQL)
        if records:
            conn.execute(INSERT_ATC_MAPPING_SQL, records)
        complete_load_batch(conn, load_batch_id, row_count=len(records))

    logger.info(
        "Loaded %d ATC mapping rows for %d RxNorm ingredient anchors with load_batch_id=%s",
        len(records),
        len(anchors),
        load_batch_id,
    )
    return load_batch_id


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Load ATC mappings for RxNorm ingredient anchors into PostgreSQL."
    )
    parser.add_argument(
        "--rxnorm_rrf_dir",
        required=True,
        type=Path,
        help="Directory containing RXNCONSO.RRF.",
    )
    parser.add_argument(
        "--atc_tree_tsv",
        required=True,
        type=Path,
        help="Path to ATC tree TSV with ATC code and ATC level name columns.",
    )
    parser.add_argument(
        "--atc_source_version",
        required=True,
        help="ATC source version label, e.g. ATC_22092025.",
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

    load_atc_mappings_to_postgres(
        database_url=database_url,
        rxnorm_rrf_dir=args.rxnorm_rrf_dir,
        atc_tree_tsv=args.atc_tree_tsv,
        atc_source_version=args.atc_source_version,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
