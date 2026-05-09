from __future__ import annotations

"""Load ATC mappings for CTGov drug terms into PostgreSQL.

Input is the Part 2 CTGov-anchored RxNorm layer in PostgreSQL:

    drug_identity.ctgov_drug_term
    drug_identity.ctgov_drug_term_rxnorm_mapping

Selection rule:

    match_status = 'MATCHED'
    AND rxnorm_ingredient_rxcui IS NOT NULL

Important: manual_review_needed is intentionally NOT used as an exclusion filter.
"""

import argparse
import csv
import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

from aus_trial_universe.ctgov.drug_ontology.classification.atc.rxnorm_to_atc import (
    RXNCONSO_FILENAME,
    AtcLookupCandidate,
    AtcResolution,
    AtcTree,
    RxnRelIndex,
    atc_manual_review_needed,
    build_atc_lookup_candidates,
    clean_text,
    load_rxnconso_atc_index,
    resolve_candidates_to_atc,
)

logger = logging.getLogger(__name__)

FETCH_CT_GOV_DRUG_TERMS_SQL = text(
    """
    SELECT
        t.ctgov_drug_term_id,
        t.input_drug_name,
        t.input_drug_name_normalized,
        t.source_field,
        t.term_kind,
        m.rxnorm_rxcui,
        m.rxnorm_canonical_name,
        m.rxnorm_term_type,
        m.rxnorm_ingredient_rxcui,
        m.rxnorm_ingredient_name,
        m.rxnorm_ingredient_term_type,
        m.manual_review_needed AS rxnorm_mapping_manual_review_needed,
        m.rxnorm_source_version
    FROM drug_identity.ctgov_drug_term t
    JOIN drug_identity.ctgov_drug_term_rxnorm_mapping m
      ON m.ctgov_drug_term_id = t.ctgov_drug_term_id
    WHERE m.match_status = 'MATCHED'
      AND m.rxnorm_ingredient_rxcui IS NOT NULL
      AND m.rxnorm_source_version = :rxnorm_source_version
    ORDER BY t.ctgov_drug_term_id
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
        'atc_classification_resolution',
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

DELETE_EXISTING_ATC_VERSION_SQL = text(
    """
    DELETE FROM drug_classification.ctgov_drug_term_atc_mapping
    WHERE atc_source_version = :atc_source_version
    """
)

INSERT_ATC_MAPPING_SQL = text(
    """
    INSERT INTO drug_classification.ctgov_drug_term_atc_mapping (
        ctgov_drug_term_id,
        load_batch_id,
        input_drug_name,
        input_drug_name_normalized,
        source_field,
        term_kind,
        rxnorm_rxcui,
        rxnorm_canonical_name,
        rxnorm_term_type,
        rxnorm_ingredient_rxcui,
        rxnorm_ingredient_name,
        rxnorm_ingredient_term_type,
        rxnorm_mapping_manual_review_needed,
        atc_lookup_rxcui,
        atc_lookup_rxcui_kind,
        atc_lookup_path,
        atc_code,
        atc_name_from_rxnconso,
        atc_term_type,
        atc_rxaui,
        atc_suppress,
        atc_tree_name,
        atc_level,
        atc_path,
        atc_l1_code,
        atc_l1_name,
        atc_l2_code,
        atc_l2_name,
        atc_l3_code,
        atc_l3_name,
        atc_l4_code,
        atc_l4_name,
        atc_l5_code,
        atc_l5_name,
        link_status,
        manual_review_needed,
        resolution_payload,
        rxnorm_source_version,
        atc_source_version
    )
    VALUES (
        :ctgov_drug_term_id,
        :load_batch_id,
        :input_drug_name,
        :input_drug_name_normalized,
        :source_field,
        :term_kind,
        :rxnorm_rxcui,
        :rxnorm_canonical_name,
        :rxnorm_term_type,
        :rxnorm_ingredient_rxcui,
        :rxnorm_ingredient_name,
        :rxnorm_ingredient_term_type,
        :rxnorm_mapping_manual_review_needed,
        :atc_lookup_rxcui,
        :atc_lookup_rxcui_kind,
        :atc_lookup_path,
        :atc_code,
        :atc_name_from_rxnconso,
        :atc_term_type,
        :atc_rxaui,
        :atc_suppress,
        :atc_tree_name,
        :atc_level,
        :atc_path,
        :atc_l1_code,
        :atc_l1_name,
        :atc_l2_code,
        :atc_l2_name,
        :atc_l3_code,
        :atc_l3_name,
        :atc_l4_code,
        :atc_l4_name,
        :atc_l5_code,
        :atc_l5_name,
        :link_status,
        :manual_review_needed,
        CAST(:resolution_payload AS jsonb),
        :rxnorm_source_version,
        :atc_source_version
    )
    """
)


@dataclass(frozen=True)
class CtgovDrugTermForAtc:
    ctgov_drug_term_id: int
    input_drug_name: str
    input_drug_name_normalized: str
    source_field: str
    term_kind: str
    rxnorm_rxcui: str
    rxnorm_canonical_name: str
    rxnorm_term_type: str
    rxnorm_ingredient_rxcui: str
    rxnorm_ingredient_name: str
    rxnorm_ingredient_term_type: str
    rxnorm_mapping_manual_review_needed: bool
    rxnorm_source_version: str


def fetch_ctgov_drug_terms_for_atc(
    engine: Engine,
    rxnorm_source_version: str,
) -> list[CtgovDrugTermForAtc]:
    with engine.connect() as conn:
        rows = conn.execute(
            FETCH_CT_GOV_DRUG_TERMS_SQL,
            {"rxnorm_source_version": rxnorm_source_version},
        ).mappings().all()

    return [
        CtgovDrugTermForAtc(
            ctgov_drug_term_id=int(row["ctgov_drug_term_id"]),
            input_drug_name=clean_text(row["input_drug_name"]),
            input_drug_name_normalized=clean_text(row["input_drug_name_normalized"]),
            source_field=clean_text(row["source_field"]),
            term_kind=clean_text(row["term_kind"]),
            rxnorm_rxcui=clean_text(row["rxnorm_rxcui"]),
            rxnorm_canonical_name=clean_text(row["rxnorm_canonical_name"]),
            rxnorm_term_type=clean_text(row["rxnorm_term_type"]),
            rxnorm_ingredient_rxcui=clean_text(row["rxnorm_ingredient_rxcui"]),
            rxnorm_ingredient_name=clean_text(row["rxnorm_ingredient_name"]),
            rxnorm_ingredient_term_type=clean_text(row["rxnorm_ingredient_term_type"]),
            rxnorm_mapping_manual_review_needed=bool(row["rxnorm_mapping_manual_review_needed"]),
            rxnorm_source_version=clean_text(row["rxnorm_source_version"]),
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


def build_candidate_cache(
    terms: Sequence[CtgovDrugTermForAtc],
    rel_index: RxnRelIndex,
) -> dict[int, list[AtcLookupCandidate]]:
    cache: dict[tuple[str, str], list[AtcLookupCandidate]] = {}
    out: dict[int, list[AtcLookupCandidate]] = {}

    for term in terms:
        key = (term.rxnorm_ingredient_rxcui, term.rxnorm_rxcui)
        if key not in cache:
            cache[key] = build_atc_lookup_candidates(
                ingredient_rxcui=term.rxnorm_ingredient_rxcui,
                matched_rxcui=term.rxnorm_rxcui,
                rel_index=rel_index,
            )
        out[term.ctgov_drug_term_id] = cache[key]

    return out


def collect_target_rxcuis(candidate_cache: Mapping[int, Sequence[AtcLookupCandidate]]) -> set[str]:
    return {
        candidate.rxcui
        for candidates in candidate_cache.values()
        for candidate in candidates
        if candidate.rxcui
    }


def build_resolution_payload(
    term: CtgovDrugTermForAtc,
    candidates: Sequence[AtcLookupCandidate],
    resolution: AtcResolution,
) -> str:
    """Build compact audit JSON for PostgreSQL.

    Do not embed the full candidate list for every ATC row. For highly connected
    concepts that makes both PostgreSQL JSONB and the check TSV enormous while
    adding little audit value. The selected lookup RXCUI/path is already stored
    in first-class columns; this payload keeps only counts and the selected
    resolution details.
    """
    candidate_kind_counts: dict[str, int] = {}
    for candidate in candidates:
        candidate_kind_counts[candidate.rxcui_kind] = candidate_kind_counts.get(candidate.rxcui_kind, 0) + 1

    return json.dumps(
        {
            "ctgov_drug_term": asdict(term),
            "candidate_count": len(candidates),
            "candidate_kind_counts": candidate_kind_counts,
            "atc_resolution": asdict(resolution),
        },
        ensure_ascii=False,
    )


def mapping_record(
    term: CtgovDrugTermForAtc,
    candidates: Sequence[AtcLookupCandidate],
    resolution: AtcResolution,
    load_batch_id: uuid.UUID,
    atc_source_version: str,
) -> dict[str, object]:
    return {
        "ctgov_drug_term_id": term.ctgov_drug_term_id,
        "load_batch_id": str(load_batch_id),
        "input_drug_name": term.input_drug_name,
        "input_drug_name_normalized": term.input_drug_name_normalized,
        "source_field": term.source_field,
        "term_kind": term.term_kind,
        "rxnorm_rxcui": term.rxnorm_rxcui or None,
        "rxnorm_canonical_name": term.rxnorm_canonical_name or None,
        "rxnorm_term_type": term.rxnorm_term_type or None,
        "rxnorm_ingredient_rxcui": term.rxnorm_ingredient_rxcui,
        "rxnorm_ingredient_name": term.rxnorm_ingredient_name or None,
        "rxnorm_ingredient_term_type": term.rxnorm_ingredient_term_type or None,
        "rxnorm_mapping_manual_review_needed": term.rxnorm_mapping_manual_review_needed,
        "atc_lookup_rxcui": resolution.atc_lookup_rxcui or None,
        "atc_lookup_rxcui_kind": resolution.atc_lookup_rxcui_kind or None,
        "atc_lookup_path": resolution.atc_lookup_path or None,
        "atc_code": resolution.atc_code,
        "atc_name_from_rxnconso": resolution.atc_name_from_rxnconso or None,
        "atc_term_type": resolution.atc_tty or None,
        "atc_rxaui": resolution.atc_rxaui or None,
        "atc_suppress": resolution.atc_suppress or None,
        "atc_tree_name": resolution.tree_name or None,
        "atc_level": resolution.tree_level,
        "atc_path": resolution.tree_path or None,
        "atc_l1_code": resolution.l1_code or None,
        "atc_l1_name": resolution.l1_name or None,
        "atc_l2_code": resolution.l2_code or None,
        "atc_l2_name": resolution.l2_name or None,
        "atc_l3_code": resolution.l3_code or None,
        "atc_l3_name": resolution.l3_name or None,
        "atc_l4_code": resolution.l4_code or None,
        "atc_l4_name": resolution.l4_name or None,
        "atc_l5_code": resolution.l5_code or None,
        "atc_l5_name": resolution.l5_name or None,
        "link_status": resolution.link_status,
        "manual_review_needed": atc_manual_review_needed(resolution),
        "resolution_payload": build_resolution_payload(term, candidates, resolution),
        "rxnorm_source_version": term.rxnorm_source_version,
        "atc_source_version": atc_source_version,
    }


CHECK_TSV_EXCLUDED_COLUMNS = {"resolution_payload"}


def write_check_tsv(records: Iterable[Mapping[str, object]], output_tsv: Path) -> None:
    rows = [
        {key: value for key, value in record.items() if key not in CHECK_TSV_EXCLUDED_COLUMNS}
        for record in records
    ]
    output_tsv.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        output_tsv.write_text("", encoding="utf-8")
        return

    with output_tsv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def load_atc_mappings_to_postgres(
    database_url: str,
    rxnorm_rrf_dir: Path,
    atc_tree_tsv: Path,
    rxnorm_source_version: str,
    atc_source_version: str,
    output_check_tsv: Path | None,
) -> uuid.UUID:
    engine = create_engine(database_url)
    rxnconso_path = rxnorm_rrf_dir / RXNCONSO_FILENAME

    terms = fetch_ctgov_drug_terms_for_atc(engine, rxnorm_source_version)
    logger.info("Fetched %d CTGov drug terms eligible for ATC mapping", len(terms))

    tree = AtcTree.from_tsv(atc_tree_tsv)

    logger.info("Loading RxNorm relationship index from %s", rxnorm_rrf_dir)
    rel_index = RxnRelIndex.from_rrf_dir(rxnorm_rrf_dir)
    logger.info("Loaded %d RXNREL relationship rows", len(rel_index.rows))

    candidate_cache = build_candidate_cache(terms, rel_index)
    target_rxcuis = collect_target_rxcuis(candidate_cache)
    logger.info("Collected %d unique RxNorm RXCUIs for expanded ATC lookup", len(target_rxcuis))

    atc_by_rxcui = load_rxnconso_atc_index(rxnconso_path=rxnconso_path, target_rxcuis=target_rxcuis)

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
    for term in terms:
        candidates = candidate_cache[term.ctgov_drug_term_id]
        resolutions = resolve_candidates_to_atc(candidates, atc_by_rxcui, tree)
        for resolution in resolutions:
            records.append(
                mapping_record(
                    term=term,
                    candidates=candidates,
                    resolution=resolution,
                    load_batch_id=load_batch_id,
                    atc_source_version=atc_source_version,
                )
            )

    with engine.begin() as conn:
        create_load_batch(conn, load_batch_id, source_file=source_file, source_version=atc_source_version)
        conn.execute(DELETE_EXISTING_ATC_VERSION_SQL, {"atc_source_version": atc_source_version})
        if records:
            conn.execute(INSERT_ATC_MAPPING_SQL, records)
        complete_load_batch(conn, load_batch_id, row_count=len(records))

    if output_check_tsv is not None:
        write_check_tsv(records, output_check_tsv)
        logger.info("Wrote ATC check TSV to %s", output_check_tsv)

    logger.info(
        "Loaded ATC mappings for %d CTGov drug terms into %d rows with load_batch_id=%s",
        len(terms),
        len(records),
        load_batch_id,
    )
    return load_batch_id


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Load ATC mappings for CTGov drug terms into PostgreSQL."
    )
    parser.add_argument(
        "--rxnorm_rrf_dir",
        required=True,
        type=Path,
        help="Directory containing RXNCONSO.RRF and RXNREL.RRF.",
    )
    parser.add_argument(
        "--atc_tree_tsv",
        required=True,
        type=Path,
        help="Path to ATC tree TSV with ATC code and ATC level name columns.",
    )
    parser.add_argument(
        "--rxnorm_source_version",
        required=True,
        help="RxNorm source version to select from Part 2 mappings.",
    )
    parser.add_argument(
        "--atc_source_version",
        required=True,
        help="ATC mapping source version label, e.g. RxNorm_full_03022026_ATC.",
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

    load_atc_mappings_to_postgres(
        database_url=database_url,
        rxnorm_rrf_dir=args.rxnorm_rrf_dir,
        atc_tree_tsv=args.atc_tree_tsv,
        rxnorm_source_version=args.rxnorm_source_version,
        atc_source_version=args.atc_source_version,
        output_check_tsv=args.output_check_tsv,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
