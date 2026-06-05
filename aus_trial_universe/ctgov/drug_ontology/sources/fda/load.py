from __future__ import annotations

"""Load FDA Drugs@FDA mappings for simplified CTGov drug ontology outputs.

Input is the simplified Part 2 RxNorm table:

    drug_identity.ctgov_drug_term_rxnorm_mapping

and the simplified Part 2 trial-link table:

    drug_identity.ctgov_intervention_drug_term_link

FDA mapping is materialized at a FDA-specific RxNorm anchor level. Trial linkage
is handled separately through a lean SQL export view.

No Python-side TSVs are written.
"""

import argparse
import json
import logging
import os
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

from aus_trial_universe.ctgov.drug_ontology.sources.fda.product_components import (
    FdaLinkAnchor,
    FdaProductIngredientComponent,
    build_fda_product_ingredient_components,
    clean_text,
    derive_fda_link_anchor_from_values,
    normalize_fda_ingredient_text,
)
from aus_trial_universe.ctgov.drug_ontology.identity.rxnorm.matcher import (
    RXNCONSO_FILENAME,
    RXNREL_FILENAME,
    RxnConsoIndex,
    RxnRelIndex,
)

logger = logging.getLogger(__name__)

MAX_FDA_DRUG_NAME_ANCHORS_PER_INPUT_TERM = 8

FDA_PROPER_NAME_PREFIX_RE = re.compile(r"^(?:ado|fam)[-\s]+", re.IGNORECASE)
FDA_BIOLOGIC_SUFFIX_RE = re.compile(r"[-\s]+[a-z]{4}$", re.IGNORECASE)


FETCH_RXNORM_DRUG_TERMS_SQL = text(
    """
    SELECT DISTINCT
        input_drug_name,
        rxnorm_rxcui,
        rxnorm_canonical_name,
        rxnorm_term_type,
        rxnorm_ingredient_rxcui,
        rxnorm_ingredient_name,
        rxnorm_ingredient_term_type
    FROM drug_identity.ctgov_drug_term_rxnorm_mapping
    WHERE match_status = 'MATCHED'
      AND rxnorm_ingredient_rxcui IS NOT NULL
      AND rxnorm_ingredient_rxcui <> ''
    ORDER BY input_drug_name
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
        'fda_anchor_product_resolution',
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

TRUNCATE_FDA_TABLES_SQL = [
    text("TRUNCATE TABLE drug_classification.input_drug_name_fda_anchor"),
    text("TRUNCATE TABLE drug_classification.rxnorm_fda_anchor_product_mapping"),
    text("TRUNCATE TABLE drug_classification.fda_product_ingredient_component"),
]

INSERT_COMPONENT_SQL = text(
    """
    INSERT INTO drug_classification.fda_product_ingredient_component (
        fda_product_component_key,
        load_batch_id,
        appl_no,
        product_no,
        ingredient_position,
        active_ingredient_component,
        drug_name,
        form,
        strength_component,
        appl_type,
        sponsor_name,
        marketing_status_description,
        latest_approved_submission_status_date,
        has_approved_submission,
        fda_link_anchor_rxcui,
        fda_link_anchor_name,
        fda_link_anchor_term_type
    )
    VALUES (
        :fda_product_component_key,
        :load_batch_id,
        :appl_no,
        :product_no,
        :ingredient_position,
        :active_ingredient_component,
        :drug_name,
        :form,
        :strength_component,
        :appl_type,
        :sponsor_name,
        :marketing_status_description,
        :latest_approved_submission_status_date,
        :has_approved_submission,
        :fda_link_anchor_rxcui,
        :fda_link_anchor_name,
        :fda_link_anchor_term_type
    )
    ON CONFLICT (fda_product_component_key)
    DO UPDATE SET
        load_batch_id = EXCLUDED.load_batch_id,
        appl_no = EXCLUDED.appl_no,
        product_no = EXCLUDED.product_no,
        ingredient_position = EXCLUDED.ingredient_position,
        active_ingredient_component = EXCLUDED.active_ingredient_component,
        drug_name = EXCLUDED.drug_name,
        form = EXCLUDED.form,
        strength_component = EXCLUDED.strength_component,
        appl_type = EXCLUDED.appl_type,
        sponsor_name = EXCLUDED.sponsor_name,
        marketing_status_description = EXCLUDED.marketing_status_description,
        latest_approved_submission_status_date = EXCLUDED.latest_approved_submission_status_date,
        has_approved_submission = EXCLUDED.has_approved_submission,
        fda_link_anchor_rxcui = EXCLUDED.fda_link_anchor_rxcui,
        fda_link_anchor_name = EXCLUDED.fda_link_anchor_name,
        fda_link_anchor_term_type = EXCLUDED.fda_link_anchor_term_type
    """
)

INSERT_INPUT_ANCHOR_SQL = text(
    """
    INSERT INTO drug_classification.input_drug_name_fda_anchor (
        input_drug_name,
        rxnorm_fda_anchor_rxcui,
        rxnorm_fda_anchor_name,
        rxnorm_fda_anchor_term_type
    )
    VALUES (
        :input_drug_name,
        :rxnorm_fda_anchor_rxcui,
        :rxnorm_fda_anchor_name,
        :rxnorm_fda_anchor_term_type
    )
    ON CONFLICT (
        input_drug_name,
        rxnorm_fda_anchor_rxcui
    )
    DO UPDATE SET
        rxnorm_fda_anchor_name = EXCLUDED.rxnorm_fda_anchor_name,
        rxnorm_fda_anchor_term_type = EXCLUDED.rxnorm_fda_anchor_term_type
    """
)

INSERT_ANCHOR_PRODUCT_MAPPING_SQL = text(
    """
    INSERT INTO drug_classification.rxnorm_fda_anchor_product_mapping (
        rxnorm_fda_anchor_rxcui,
        rxnorm_fda_anchor_name,
        rxnorm_fda_anchor_term_type,
        fda_link_status,
        fda_product_component_key
    )
    VALUES (
        :rxnorm_fda_anchor_rxcui,
        :rxnorm_fda_anchor_name,
        :rxnorm_fda_anchor_term_type,
        :fda_link_status,
        :fda_product_component_key
    )
    ON CONFLICT (
        rxnorm_fda_anchor_rxcui,
        fda_product_component_key
    )
    DO UPDATE SET
        rxnorm_fda_anchor_name = EXCLUDED.rxnorm_fda_anchor_name,
        rxnorm_fda_anchor_term_type = EXCLUDED.rxnorm_fda_anchor_term_type,
        fda_link_status = EXCLUDED.fda_link_status
    """
)


@dataclass(frozen=True)
class RxNormDrugTermForFda:
    input_drug_name: str
    rxnorm_rxcui: str
    rxnorm_canonical_name: str
    rxnorm_term_type: str
    rxnorm_ingredient_rxcui: str
    rxnorm_ingredient_name: str
    rxnorm_ingredient_term_type: str


def fetch_rxnorm_drug_terms_for_fda(engine: Engine) -> list[RxNormDrugTermForFda]:
    with engine.connect() as conn:
        rows = conn.execute(FETCH_RXNORM_DRUG_TERMS_SQL).mappings().all()

    return [
        RxNormDrugTermForFda(
            input_drug_name=clean_text(row["input_drug_name"]),
            rxnorm_rxcui=clean_text(row["rxnorm_rxcui"]),
            rxnorm_canonical_name=clean_text(row["rxnorm_canonical_name"]),
            rxnorm_term_type=clean_text(row["rxnorm_term_type"]),
            rxnorm_ingredient_rxcui=clean_text(row["rxnorm_ingredient_rxcui"]),
            rxnorm_ingredient_name=clean_text(row["rxnorm_ingredient_name"]),
            rxnorm_ingredient_term_type=clean_text(row["rxnorm_ingredient_term_type"]),
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


def fda_product_component_key(component: FdaProductIngredientComponent) -> str:
    normalized_component = normalize_fda_ingredient_text(component.active_ingredient_component)
    return (
        f"{component.appl_no}:"
        f"{component.product_no}:"
        f"{component.ingredient_position}:"
        f"{normalized_component}"
    )


def component_insert_record(
    component: FdaProductIngredientComponent,
    load_batch_id: uuid.UUID,
) -> dict[str, object]:
    return {
        "fda_product_component_key": fda_product_component_key(component),
        "load_batch_id": str(load_batch_id),
        "appl_no": component.appl_no,
        "product_no": component.product_no,
        "ingredient_position": component.ingredient_position,
        "active_ingredient_component": component.active_ingredient_component,
        "drug_name": component.drug_name,
        "form": component.form,
        "strength_component": component.strength_component,
        "appl_type": component.appl_type,
        "sponsor_name": component.sponsor_name,
        "marketing_status_description": component.marketing_status_description,
        "latest_approved_submission_status_date": component.latest_approved_submission_status_date,
        "has_approved_submission": component.has_approved_submission,
        "fda_link_anchor_rxcui": component.fda_link_anchor_rxcui,
        "fda_link_anchor_name": component.fda_link_anchor_name,
        "fda_link_anchor_term_type": component.fda_link_anchor_term_type,
    }


def normalize_anchor_equivalence_key(value: object) -> str:
    key = normalize_fda_ingredient_text(value)
    if not key:
        return ""
    key = FDA_PROPER_NAME_PREFIX_RE.sub("", key).strip(" -")
    key = FDA_BIOLOGIC_SUFFIX_RE.sub("", key).strip(" -")
    return key


def anchor_equivalence_key_was_transformed(value: object) -> bool:
    original = normalize_fda_ingredient_text(value)
    transformed = normalize_anchor_equivalence_key(value)
    return bool(original and transformed and original != transformed)


def anchor_from_fda_component(component: FdaProductIngredientComponent) -> FdaLinkAnchor:
    return FdaLinkAnchor(
        link_anchor_rxcui=component.fda_link_anchor_rxcui,
        link_anchor_name=component.fda_link_anchor_name,
        link_anchor_term_type=component.fda_link_anchor_term_type,
        link_anchor_strategy=f"FDA_DRUG_NAME_TO_PRODUCT_COMPONENT_ANCHOR:{component.fda_link_anchor_strategy}",
        link_anchor_path=(
            f"drug_name({component.drug_name}) -> component({component.active_ingredient_component}) "
            f"-> {component.fda_link_anchor_path}"
        ),
        link_anchor_manual_review_needed=component.fda_link_anchor_manual_review_needed or component.fda_manual_review_needed,
    )


def dedupe_anchors(anchors: Sequence[FdaLinkAnchor]) -> list[FdaLinkAnchor]:
    by_rxcui: dict[str, FdaLinkAnchor] = {}
    for anchor in anchors:
        rxcui = clean_text(anchor.link_anchor_rxcui)
        if not rxcui or rxcui in by_rxcui:
            continue
        by_rxcui[rxcui] = anchor
    return [by_rxcui[rxcui] for rxcui in sorted(by_rxcui)]


def build_fda_drug_name_anchor_index(
    components: Sequence[FdaProductIngredientComponent],
) -> dict[str, list[FdaLinkAnchor]]:
    by_drug_name: dict[str, list[FdaLinkAnchor]] = defaultdict(list)
    for component in components:
        key = normalize_fda_ingredient_text(component.drug_name)
        if not key or not component.fda_link_anchor_rxcui:
            continue
        by_drug_name[key].append(anchor_from_fda_component(component))

    return {key: dedupe_anchors(anchors) for key, anchors in by_drug_name.items()}


def fda_drug_name_lookup_keys(term: RxNormDrugTermForFda) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in (term.input_drug_name, term.rxnorm_canonical_name):
        key = normalize_fda_ingredient_text(value)
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def should_use_fda_drug_name_anchors(
    term: RxNormDrugTermForFda,
    drug_name_anchors: Sequence[FdaLinkAnchor],
) -> bool:
    if not drug_name_anchors:
        return False
    if len(drug_name_anchors) > MAX_FDA_DRUG_NAME_ANCHORS_PER_INPUT_TERM:
        return False
    return term.rxnorm_term_type == "BN"


def default_fda_anchor_for_term(
    term: RxNormDrugTermForFda,
    conso_index: RxnConsoIndex,
) -> FdaLinkAnchor:
    return derive_fda_link_anchor_from_values(
        source_text=term.input_drug_name,
        matched_rxcui=term.rxnorm_rxcui,
        canonical_name=term.rxnorm_canonical_name,
        canonical_tty=term.rxnorm_term_type,
        ingredient_rxcui=term.rxnorm_ingredient_rxcui,
        ingredient_name=term.rxnorm_ingredient_name,
        ingredient_tty=term.rxnorm_ingredient_term_type,
        conso_index=conso_index,
    )


def derive_fda_anchors_for_drug_term(
    term: RxNormDrugTermForFda,
    conso_index: RxnConsoIndex,
    fda_drug_name_anchor_index: Mapping[str, Sequence[FdaLinkAnchor]],
) -> list[FdaLinkAnchor]:
    drug_name_anchors: list[FdaLinkAnchor] = []
    for key in fda_drug_name_lookup_keys(term):
        drug_name_anchors.extend(fda_drug_name_anchor_index.get(key, []))
    drug_name_anchors = dedupe_anchors(drug_name_anchors)

    if should_use_fda_drug_name_anchors(term, drug_name_anchors):
        return drug_name_anchors

    return [default_fda_anchor_for_term(term, conso_index)]


def input_anchor_records(
    terms: Sequence[RxNormDrugTermForFda],
    conso_index: RxnConsoIndex,
    fda_drug_name_anchor_index: Mapping[str, Sequence[FdaLinkAnchor]],
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for term in terms:
        for anchor in derive_fda_anchors_for_drug_term(term, conso_index, fda_drug_name_anchor_index):
            if not anchor.link_anchor_rxcui:
                continue
            records.append(
                {
                    "input_drug_name": term.input_drug_name,
                    "rxnorm_fda_anchor_rxcui": anchor.link_anchor_rxcui,
                    "rxnorm_fda_anchor_name": anchor.link_anchor_name,
                    "rxnorm_fda_anchor_term_type": anchor.link_anchor_term_type,
                }
            )
    return records


def unique_fda_anchors_from_input_records(records: Sequence[Mapping[str, object]]) -> list[dict[str, str]]:
    by_rxcui: dict[str, dict[str, str]] = {}
    for record in records:
        rxcui = clean_text(record.get("rxnorm_fda_anchor_rxcui"))
        if not rxcui or rxcui in by_rxcui:
            continue
        by_rxcui[rxcui] = {
            "rxnorm_fda_anchor_rxcui": rxcui,
            "rxnorm_fda_anchor_name": clean_text(record.get("rxnorm_fda_anchor_name")),
            "rxnorm_fda_anchor_term_type": clean_text(record.get("rxnorm_fda_anchor_term_type")),
        }
    return [by_rxcui[rxcui] for rxcui in sorted(by_rxcui)]


def build_component_anchor_indexes(
    components: Sequence[FdaProductIngredientComponent],
) -> tuple[dict[str, list[FdaProductIngredientComponent]], dict[str, list[FdaProductIngredientComponent]]]:
    components_by_anchor_rxcui: dict[str, list[FdaProductIngredientComponent]] = defaultdict(list)
    components_by_anchor_equivalence_key: dict[str, list[FdaProductIngredientComponent]] = defaultdict(list)

    for component in components:
        if component.fda_link_anchor_rxcui:
            components_by_anchor_rxcui[component.fda_link_anchor_rxcui].append(component)

        if component.fda_link_anchor_name and anchor_equivalence_key_was_transformed(component.fda_link_anchor_name):
            key = normalize_anchor_equivalence_key(component.fda_link_anchor_name)
            if key:
                components_by_anchor_equivalence_key[key].append(component)

    return components_by_anchor_rxcui, components_by_anchor_equivalence_key


def matching_components_for_anchor(
    anchor_record: Mapping[str, object],
    components_by_anchor_rxcui: Mapping[str, Sequence[FdaProductIngredientComponent]],
    components_by_anchor_equivalence_key: Mapping[str, Sequence[FdaProductIngredientComponent]],
) -> list[FdaProductIngredientComponent]:
    anchor_rxcui = clean_text(anchor_record.get("rxnorm_fda_anchor_rxcui"))
    direct = list(components_by_anchor_rxcui.get(anchor_rxcui, []))
    if direct:
        return direct

    key = normalize_anchor_equivalence_key(anchor_record.get("rxnorm_fda_anchor_name"))
    return list(components_by_anchor_equivalence_key.get(key, [])) if key else []


def anchor_product_mapping_records(
    anchor_records: Sequence[Mapping[str, object]],
    components: Sequence[FdaProductIngredientComponent],
) -> list[dict[str, object]]:
    components_by_anchor_rxcui, components_by_anchor_equivalence_key = build_component_anchor_indexes(components)

    records: list[dict[str, object]] = []
    for anchor in anchor_records:
        matched_components = matching_components_for_anchor(
            anchor,
            components_by_anchor_rxcui,
            components_by_anchor_equivalence_key,
        )

        if not matched_components:
            records.append(
                {
                    "rxnorm_fda_anchor_rxcui": anchor["rxnorm_fda_anchor_rxcui"],
                    "rxnorm_fda_anchor_name": anchor["rxnorm_fda_anchor_name"],
                    "rxnorm_fda_anchor_term_type": anchor["rxnorm_fda_anchor_term_type"],
                    "fda_link_status": "NO_FDA_PRODUCT_FOR_RXNORM_ANCHOR",
                    "fda_product_component_key": "",
                }
            )
            continue

        for component in matched_components:
            records.append(
                {
                    "rxnorm_fda_anchor_rxcui": anchor["rxnorm_fda_anchor_rxcui"],
                    "rxnorm_fda_anchor_name": anchor["rxnorm_fda_anchor_name"],
                    "rxnorm_fda_anchor_term_type": anchor["rxnorm_fda_anchor_term_type"],
                    "fda_link_status": "MATCHED_FDA_PRODUCT_COMPONENT",
                    "fda_product_component_key": fda_product_component_key(component),
                }
            )

    return records


def load_fda_mappings_to_postgres(
    database_url: str,
    fda_raw_dir: Path,
    rxnorm_rrf_dir: Path,
    fda_source_version: str,
) -> uuid.UUID:
    engine = create_engine(database_url)

    terms = fetch_rxnorm_drug_terms_for_fda(engine)
    logger.info("Fetched %d distinct RxNorm drug terms eligible for FDA mapping", len(terms))

    logger.info("Loading RxNorm indexes from %s", rxnorm_rrf_dir)
    conso_index = RxnConsoIndex.from_rrf_dir(rxnorm_rrf_dir)
    rel_index = RxnRelIndex.from_rrf_dir(rxnorm_rrf_dir)

    components = build_fda_product_ingredient_components(
        fda_raw_dir=fda_raw_dir,
        conso_index=conso_index,
        rel_index=rel_index,
    )
    logger.info("Built %d FDA product ingredient component rows", len(components))

    fda_drug_name_anchor_index = build_fda_drug_name_anchor_index(components)
    input_anchor_rows = input_anchor_records(terms, conso_index, fda_drug_name_anchor_index)
    unique_anchor_rows = unique_fda_anchors_from_input_records(input_anchor_rows)
    anchor_product_rows = anchor_product_mapping_records(unique_anchor_rows, components)

    logger.info(
        "Collapsed %d input drug terms to %d FDA anchors and %d FDA anchor-product rows",
        len(terms),
        len(unique_anchor_rows),
        len(anchor_product_rows),
    )

    load_batch_id = uuid.uuid4()
    source_file = json.dumps(
        {
            "fda_raw_dir": str(fda_raw_dir),
            "rxnorm_rrf_dir": str(rxnorm_rrf_dir),
            "rxnconso_path": str(rxnorm_rrf_dir / RXNCONSO_FILENAME),
            "rxnrel_path": str(rxnorm_rrf_dir / RXNREL_FILENAME),
        },
        ensure_ascii=False,
    )

    with engine.begin() as conn:
        create_load_batch(conn, load_batch_id, source_file=source_file, source_version=fda_source_version)
        for statement in TRUNCATE_FDA_TABLES_SQL:
            conn.execute(statement)

        component_records = [component_insert_record(component, load_batch_id) for component in components]
        if component_records:
            conn.execute(INSERT_COMPONENT_SQL, component_records)
        if input_anchor_rows:
            conn.execute(INSERT_INPUT_ANCHOR_SQL, input_anchor_rows)
        if anchor_product_rows:
            conn.execute(INSERT_ANCHOR_PRODUCT_MAPPING_SQL, anchor_product_rows)

        complete_load_batch(conn, load_batch_id, row_count=len(anchor_product_rows))

    logger.info(
        "Loaded FDA mapping with %d components, %d input-anchor links, and %d anchor-product rows; load_batch_id=%s",
        len(components),
        len(input_anchor_rows),
        len(anchor_product_rows),
        load_batch_id,
    )
    return load_batch_id


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Load FDA Drugs@FDA mappings for simplified CTGov drug ontology outputs."
    )
    parser.add_argument(
        "--fda_raw_dir",
        required=True,
        type=Path,
        help="Directory containing the FDA Drugs@FDA text files.",
    )
    parser.add_argument(
        "--rxnorm_rrf_dir",
        required=True,
        type=Path,
        help="Directory containing RxNorm RRF files.",
    )
    parser.add_argument(
        "--fda_source_version",
        required=True,
        help="FDA source version label, e.g. FDA_16042026.",
    )
    parser.add_argument(
        "--env_file",
        type=Path,
        default=Path(".env"),
        help="Path to .env containing DATABASE_URL.",
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

    load_fda_mappings_to_postgres(
        database_url=database_url,
        fda_raw_dir=args.fda_raw_dir,
        rxnorm_rrf_dir=args.rxnorm_rrf_dir,
        fda_source_version=args.fda_source_version,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
