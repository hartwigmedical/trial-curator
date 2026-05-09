from __future__ import annotations

import argparse
import json
import logging
import os
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

from aus_trial_universe.ctgov.drug_ontology.ctgov_to_rxnorm import (
    IngredientResolution,
    RxnConsoIndex,
    RxnRelIndex,
    TermResolution,
    normalize_lookup_text,
    resolve_term,
)
from aus_trial_universe.ctgov.drug_ontology.drug_ontology_schema import (
    COL_INTERVENTION_ALL_ALIASES_NORMALISED,
    COL_INTERVENTION_ALL_ALIASES_NORMALISED_FULL,
    COL_INTERVENTION_OTHER_NAMES,
    DISPLAY_DELIMITER,
)

logger = logging.getLogger(__name__)

TARGET_INTERVENTION_TYPES = {"DRUG", "BIOLOGICAL", "OTHER", "COMBINATION_PRODUCT"}
INGREDIENT_TTYS = {"IN", "PIN", "MIN"}

FETCH_STAGING_ROWS_SQL = text(
    """
    SELECT
        staging_row_id,
        load_batch_id,
        nct_id,
        intervention_index,
        intervention_type,
        intervention_name,
        intervention_description,
        source_version,
        row_payload
    FROM ctgov.intervention_staging
    ORDER BY nct_id, intervention_index, staging_row_id
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
        input_drug_name,
        input_drug_name_normalized,
        source_field,
        term_kind,
        first_seen_load_batch_id
    )
    VALUES (
        :input_drug_name,
        :input_drug_name_normalized,
        :source_field,
        :term_kind,
        :first_seen_load_batch_id
    )
    ON CONFLICT (input_drug_name, source_field, term_kind)
    DO UPDATE SET
        input_drug_name_normalized = EXCLUDED.input_drug_name_normalized,
        updated_at = now()
    RETURNING ctgov_drug_term_id
    """
)

INSERT_OCCURRENCE_SQL = text(
    """
    INSERT INTO drug_identity.ctgov_intervention_drug_term (
        staging_row_id,
        ctgov_drug_term_id,
        nct_id,
        intervention_index
    )
    VALUES (
        :staging_row_id,
        :ctgov_drug_term_id,
        :nct_id,
        :intervention_index
    )
    ON CONFLICT (staging_row_id, ctgov_drug_term_id)
    DO NOTHING
    """
)

UPSERT_RXNORM_MAPPING_SQL = text(
    """
    INSERT INTO drug_identity.ctgov_drug_term_rxnorm_mapping (
        ctgov_drug_term_id,
        load_batch_id,
        matched_term,
        match_stage,
        match_status,
        rxnorm_rxcui,
        rxnorm_canonical_name,
        rxnorm_term_type,
        rxnorm_canonical_source,
        rxnorm_canonical_source_code,
        rxnorm_ingredient_rxcui,
        rxnorm_ingredient_name,
        rxnorm_ingredient_term_type,
        rxnorm_ingredient_resolution_stage,
        rxnorm_ingredient_path,
        candidate_count,
        candidate_summary,
        manual_review_needed,
        resolution_payload,
        rxnorm_source_version
    )
    VALUES (
        :ctgov_drug_term_id,
        :load_batch_id,
        :matched_term,
        :match_stage,
        :match_status,
        :rxnorm_rxcui,
        :rxnorm_canonical_name,
        :rxnorm_term_type,
        :rxnorm_canonical_source,
        :rxnorm_canonical_source_code,
        :rxnorm_ingredient_rxcui,
        :rxnorm_ingredient_name,
        :rxnorm_ingredient_term_type,
        :rxnorm_ingredient_resolution_stage,
        :rxnorm_ingredient_path,
        :candidate_count,
        :candidate_summary,
        :manual_review_needed,
        CAST(:resolution_payload AS jsonb),
        :rxnorm_source_version
    )
    ON CONFLICT (ctgov_drug_term_id, rxnorm_source_version)
    DO UPDATE SET
        load_batch_id = EXCLUDED.load_batch_id,
        matched_term = EXCLUDED.matched_term,
        match_stage = EXCLUDED.match_stage,
        match_status = EXCLUDED.match_status,
        rxnorm_rxcui = EXCLUDED.rxnorm_rxcui,
        rxnorm_canonical_name = EXCLUDED.rxnorm_canonical_name,
        rxnorm_term_type = EXCLUDED.rxnorm_term_type,
        rxnorm_canonical_source = EXCLUDED.rxnorm_canonical_source,
        rxnorm_canonical_source_code = EXCLUDED.rxnorm_canonical_source_code,
        rxnorm_ingredient_rxcui = EXCLUDED.rxnorm_ingredient_rxcui,
        rxnorm_ingredient_name = EXCLUDED.rxnorm_ingredient_name,
        rxnorm_ingredient_term_type = EXCLUDED.rxnorm_ingredient_term_type,
        rxnorm_ingredient_resolution_stage = EXCLUDED.rxnorm_ingredient_resolution_stage,
        rxnorm_ingredient_path = EXCLUDED.rxnorm_ingredient_path,
        candidate_count = EXCLUDED.candidate_count,
        candidate_summary = EXCLUDED.candidate_summary,
        manual_review_needed = EXCLUDED.manual_review_needed,
        resolution_payload = EXCLUDED.resolution_payload,
        updated_at = now()
    """
)


def clean_text(value: object) -> str:
    if value is None:
        return ""
    text_value = str(value).strip()
    return "" if text_value.lower() in {"", "nan", "none", "<na>"} else text_value


def split_display_values(value: object) -> list[str]:
    text_value = clean_text(value)
    if not text_value:
        return []

    values: list[str] = []
    seen: set[str] = set()
    for part in text_value.split(DISPLAY_DELIMITER):
        cleaned = clean_text(part)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            values.append(cleaned)
    return values


def parse_row_payload(payload: object) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str) and payload.strip():
        parsed = json.loads(payload)
        if isinstance(parsed, dict):
            return parsed
    return {}


def add_term_record(
    records: list[dict[str, str]],
    input_drug_name: object,
    source_field: str,
    term_kind: str,
) -> None:
    cleaned = clean_text(input_drug_name)
    if not cleaned:
        return
    records.append(
        {
            "input_drug_name": cleaned,
            "source_field": source_field,
            "term_kind": term_kind,
        }
    )


def unique_term_records_for_staging_row(row: Mapping[str, Any]) -> list[dict[str, str]]:
    intervention_type = clean_text(row.get("intervention_type")).upper()
    if intervention_type not in TARGET_INTERVENTION_TYPES:
        return []

    payload = parse_row_payload(row.get("row_payload"))
    candidate_terms: list[dict[str, str]] = []

    add_term_record(
        candidate_terms,
        row.get("intervention_name"),
        source_field="intervention_name",
        term_kind="original_intervention_name",
    )

    for term in split_display_values(payload.get(COL_INTERVENTION_OTHER_NAMES, "")):
        add_term_record(candidate_terms, term, COL_INTERVENTION_OTHER_NAMES, "other_name")

    for term in split_display_values(payload.get(COL_INTERVENTION_ALL_ALIASES_NORMALISED, "")):
        add_term_record(
            candidate_terms,
            term,
            COL_INTERVENTION_ALL_ALIASES_NORMALISED,
            "normalised_alias_component",
        )

    for term in split_display_values(payload.get(COL_INTERVENTION_ALL_ALIASES_NORMALISED_FULL, "")):
        add_term_record(
            candidate_terms,
            term,
            COL_INTERVENTION_ALL_ALIASES_NORMALISED_FULL,
            "normalised_full_alias",
        )

    unique_records: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for term_record in candidate_terms:
        key = (
            term_record["input_drug_name"],
            term_record["source_field"],
            term_record["term_kind"],
        )
        if key in seen:
            continue
        seen.add(key)
        unique_records.append(
            {
                **term_record,
                "input_drug_name_normalized": normalize_lookup_text(term_record["input_drug_name"]),
            }
        )

    return unique_records


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


def upsert_ctgov_drug_term(
    conn: Connection,
    term_record: Mapping[str, str],
    first_seen_load_batch_id: uuid.UUID,
) -> int:
    result = conn.execute(
        UPSERT_CT_GOV_DRUG_TERM_SQL,
        {
            **term_record,
            "first_seen_load_batch_id": str(first_seen_load_batch_id),
        },
    )
    return int(result.scalar_one())


def insert_occurrence(
    conn: Connection,
    staging_row: Mapping[str, Any],
    ctgov_drug_term_id: int,
) -> None:
    conn.execute(
        INSERT_OCCURRENCE_SQL,
        {
            "staging_row_id": staging_row["staging_row_id"],
            "ctgov_drug_term_id": ctgov_drug_term_id,
            "nct_id": staging_row["nct_id"],
            "intervention_index": staging_row["intervention_index"],
        },
    )


def needs_manual_review(
    resolution: TermResolution,
    ingredient_resolution: IngredientResolution,
) -> bool:
    if resolution.manual_review_needed:
        return True
    if resolution.match_status != "MATCHED":
        return True
    if not ingredient_resolution.ingredient_rxcui:
        return True
    if ingredient_resolution.ingredient_resolution_stage in {
        "NO_MATCHED_RXCUI",
        "NO_INGREDIENT_PATH_FOUND",
        "NO_CURATED_INGREDIENT_ALIAS",
    }:
        return True
    return False


def build_resolution_payload(
    resolution: TermResolution,
    ingredient_resolution: IngredientResolution,
) -> str:
    return json.dumps(
        {
            "term_resolution": asdict(resolution),
            "ingredient_resolution": asdict(ingredient_resolution),
        },
        ensure_ascii=False,
    )


def upsert_rxnorm_mapping(
    conn: Connection,
    ctgov_drug_term_id: int,
    resolution: TermResolution,
    ingredient_resolution: IngredientResolution,
    load_batch_id: uuid.UUID,
    rxnorm_source_version: str,
) -> None:
    conn.execute(
        UPSERT_RXNORM_MAPPING_SQL,
        {
            "ctgov_drug_term_id": ctgov_drug_term_id,
            "load_batch_id": str(load_batch_id),
            "matched_term": resolution.matched_term or None,
            "match_stage": resolution.match_stage,
            "match_status": resolution.match_status,
            "rxnorm_rxcui": resolution.rxcui or None,
            "rxnorm_canonical_name": resolution.canonical_name or None,
            "rxnorm_term_type": resolution.canonical_tty or None,
            "rxnorm_canonical_source": resolution.canonical_sab or None,
            "rxnorm_canonical_source_code": resolution.canonical_code or None,
            "rxnorm_ingredient_rxcui": ingredient_resolution.ingredient_rxcui or None,
            "rxnorm_ingredient_name": ingredient_resolution.ingredient_name or None,
            "rxnorm_ingredient_term_type": ingredient_resolution.ingredient_tty or None,
            "rxnorm_ingredient_resolution_stage": ingredient_resolution.ingredient_resolution_stage,
            "rxnorm_ingredient_path": ingredient_resolution.ingredient_path or None,
            "candidate_count": resolution.candidate_count,
            "candidate_summary": resolution.candidate_summary,
            "manual_review_needed": needs_manual_review(resolution, ingredient_resolution),
            "resolution_payload": build_resolution_payload(resolution, ingredient_resolution),
            "rxnorm_source_version": rxnorm_source_version,
        },
    )


def build_check_row(
    staging_row: Mapping[str, Any],
    term_record: Mapping[str, str],
    resolution: TermResolution,
    ingredient_resolution: IngredientResolution,
) -> dict[str, object]:
    return {
        "nct_id": staging_row["nct_id"],
        "intervention_index": staging_row["intervention_index"],
        "intervention_type": staging_row["intervention_type"],
        "intervention_name": staging_row["intervention_name"],
        "input_drug_name": term_record["input_drug_name"],
        "source_field": term_record["source_field"],
        "term_kind": term_record["term_kind"],
        "input_drug_name_normalized": term_record["input_drug_name_normalized"],
        "matched_term": resolution.matched_term,
        "match_stage": resolution.match_stage,
        "match_status": resolution.match_status,
        "rxnorm_rxcui": resolution.rxcui,
        "rxnorm_canonical_name": resolution.canonical_name,
        "rxnorm_term_type": resolution.canonical_tty,
        "rxnorm_canonical_source": resolution.canonical_sab,
        "candidate_count": resolution.candidate_count,
        "manual_review_needed": needs_manual_review(resolution, ingredient_resolution),
        "rxnorm_term_manual_review_needed": resolution.manual_review_needed,
        "rxnorm_ingredient_rxcui": ingredient_resolution.ingredient_rxcui,
        "rxnorm_ingredient_name": ingredient_resolution.ingredient_name,
        "rxnorm_ingredient_term_type": ingredient_resolution.ingredient_tty,
        "rxnorm_ingredient_resolution_stage": ingredient_resolution.ingredient_resolution_stage,
        "rxnorm_ingredient_path": ingredient_resolution.ingredient_path,
    }


def write_check_tsv(rows: Iterable[Mapping[str, object]], output_tsv: Path) -> None:
    output_tsv.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(
        output_tsv,
        sep="\t",
        index=False,
        encoding="utf-8",
        lineterminator="\n",
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


def resolve_rxnorm_terms_from_postgres(
    database_url: str,
    rxnorm_rrf_dir: Path,
    rxnorm_source_version: str,
    output_check_tsv: Path | None,
) -> uuid.UUID:
    engine = create_engine(database_url)
    conso_index, rel_index = load_rxnorm_indexes(rxnorm_rrf_dir)

    staging_rows = fetch_intervention_staging_rows(engine)
    logger.info("Read %d CTGov intervention staging rows", len(staging_rows))

    load_batch_id = uuid.uuid4()
    check_rows: list[dict[str, object]] = []
    resolved_term_count = 0

    term_id_cache: dict[tuple[str, str, str], int] = {}
    resolution_cache: dict[tuple[str, str, str], tuple[TermResolution, IngredientResolution]] = {}

    with engine.begin() as conn:
        create_load_batch(conn, load_batch_id, rxnorm_rrf_dir, rxnorm_source_version)

        for row_number, staging_row in enumerate(staging_rows, start=1):
            for term_record in unique_term_records_for_staging_row(staging_row):
                term_key = (
                    term_record["input_drug_name"],
                    term_record["source_field"],
                    term_record["term_kind"],
                )

                ctgov_drug_term_id = term_id_cache.get(term_key)
                if ctgov_drug_term_id is None:
                    ctgov_drug_term_id = upsert_ctgov_drug_term(
                        conn,
                        term_record,
                        first_seen_load_batch_id=load_batch_id,
                    )
                    term_id_cache[term_key] = ctgov_drug_term_id

                insert_occurrence(conn, staging_row, ctgov_drug_term_id)

                if term_key not in resolution_cache:
                    resolution_cache[term_key] = resolve_with_ingredient(
                        input_drug_name=term_record["input_drug_name"],
                        conso_index=conso_index,
                        rel_index=rel_index,
                    )
                    resolution, ingredient_resolution = resolution_cache[term_key]
                    upsert_rxnorm_mapping(
                        conn,
                        ctgov_drug_term_id=ctgov_drug_term_id,
                        resolution=resolution,
                        ingredient_resolution=ingredient_resolution,
                        load_batch_id=load_batch_id,
                        rxnorm_source_version=rxnorm_source_version,
                    )
                    resolved_term_count += 1

                resolution, ingredient_resolution = resolution_cache[term_key]
                check_rows.append(
                    build_check_row(
                        staging_row=staging_row,
                        term_record=term_record,
                        resolution=resolution,
                        ingredient_resolution=ingredient_resolution,
                    )
                )

            if row_number % 1000 == 0:
                logger.info("Processed %d staging rows", row_number)

        complete_load_batch(conn, load_batch_id, row_count=resolved_term_count)

    if output_check_tsv is not None:
        write_check_tsv(check_rows, output_check_tsv)
        logger.info("Wrote RxNorm check TSV to %s", output_check_tsv)

    logger.info(
        "Resolved %d unique CTGov drug terms with load_batch_id=%s",
        resolved_term_count,
        load_batch_id,
    )
    return load_batch_id


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Resolve CTGov input drug terms from PostgreSQL to RxNorm RXCUIs."
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
        "--output_check_tsv",
        type=Path,
        default=None,
        help="Optional TSV for manual checking.",
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
        output_check_tsv=args.output_check_tsv,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
