from __future__ import annotations

"""Load FDA Drugs@FDA product/component mappings at FDA-specific RxNorm anchor level.

The FDA uniqueness layer is a FDA-specific RxNorm link anchor, not CTGov drug-term ID.
CTGov drug terms deliberately preserve aliases/source fields, so joining FDA
product evidence directly to CTGov terms multiplies the same FDA evidence across
aliases. This loader therefore creates:

    fda_product_ingredient_component
        One row per FDA product active-ingredient component.

    rxnorm_fda_anchor_mapping
        One row per FDA-specific RxNorm link anchor x FDA product component, plus one
        unmatched row per CTGov FDA anchor with no FDA product component.

An explicit SQL view can expand anchor-level FDA evidence back to CTGov terms
when provenance is needed, but the materialized FDA linkage is ingredient-level.
"""

import argparse
import csv
import json
import logging
import os
import re
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

from aus_trial_universe.ctgov.drug_ontology.classification.fda.rxnorm_to_fda import (
    FdaLinkAnchor,
    FdaProductIngredientComponent,
    build_fda_product_ingredient_components,
    clean_text,
    derive_fda_link_anchor_from_values,
    normalize_fda_ingredient_text,
)
from aus_trial_universe.ctgov.drug_ontology.ctgov_to_rxnorm import (
    RXNCONSO_FILENAME,
    RXNREL_FILENAME,
    RxnConsoIndex,
    RxnRelIndex,
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
    ORDER BY m.rxnorm_ingredient_rxcui, t.ctgov_drug_term_id
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
        'fda_classification_resolution',
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

DELETE_EXISTING_ANCHOR_MAPPINGS_SQL = text(
    """
    DELETE FROM drug_classification.rxnorm_fda_anchor_mapping
    WHERE fda_source_version = :fda_source_version
      AND rxnorm_source_version = :rxnorm_source_version
    """
)

DELETE_EXISTING_CT_GOV_ANCHORS_SQL = text(
    """
    DELETE FROM drug_classification.ctgov_drug_term_fda_anchor
    WHERE fda_source_version = :fda_source_version
      AND rxnorm_source_version = :rxnorm_source_version
    """
)

DELETE_EXISTING_COMPONENTS_SQL = text(
    """
    DELETE FROM drug_classification.fda_product_ingredient_component
    WHERE fda_source_version = :fda_source_version
    """
)

INSERT_CT_GOV_ANCHOR_SQL = text(
    """
    INSERT INTO drug_classification.ctgov_drug_term_fda_anchor (
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
        rxnorm_fda_anchor_rxcui,
        rxnorm_fda_anchor_name,
        rxnorm_fda_anchor_term_type,
        rxnorm_fda_anchor_strategy,
        rxnorm_fda_anchor_path,
        rxnorm_fda_anchor_manual_review_needed,
        rxnorm_source_version,
        fda_source_version
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
        :rxnorm_fda_anchor_rxcui,
        :rxnorm_fda_anchor_name,
        :rxnorm_fda_anchor_term_type,
        :rxnorm_fda_anchor_strategy,
        :rxnorm_fda_anchor_path,
        :rxnorm_fda_anchor_manual_review_needed,
        :rxnorm_source_version,
        :fda_source_version
    )
    """
)

INSERT_COMPONENT_SQL = text(
    """
    INSERT INTO drug_classification.fda_product_ingredient_component (
        load_batch_id,
        fda_source_version,
        appl_no,
        product_no,
        ingredient_position,
        ingredient_count,
        active_ingredient_raw,
        active_ingredient_component,
        active_ingredient_component_normalized,
        strength_raw,
        strength_component,
        strength_parse_status,
        component_parse_status,
        drug_name,
        form,
        reference_drug,
        reference_standard,
        appl_type,
        sponsor_name,
        appl_public_notes,
        marketing_status_id,
        marketing_status_description,
        original_submission_status,
        original_submission_status_date,
        latest_submission_status,
        latest_submission_status_date,
        latest_approved_submission_status_date,
        has_approved_submission,
        fda_rxnorm_rxcui,
        fda_rxnorm_canonical_name,
        fda_rxnorm_term_type,
        fda_rxnorm_ingredient_rxcui,
        fda_rxnorm_ingredient_name,
        fda_rxnorm_ingredient_term_type,
        fda_rxnorm_match_stage,
        fda_rxnorm_match_status,
        fda_rxnorm_ingredient_resolution_stage,
        fda_rxnorm_ingredient_path,
        fda_link_anchor_rxcui,
        fda_link_anchor_name,
        fda_link_anchor_term_type,
        fda_link_anchor_strategy,
        fda_link_anchor_path,
        fda_link_anchor_manual_review_needed,
        fda_manual_review_needed,
        resolution_payload
    )
    VALUES (
        :load_batch_id,
        :fda_source_version,
        :appl_no,
        :product_no,
        :ingredient_position,
        :ingredient_count,
        :active_ingredient_raw,
        :active_ingredient_component,
        :active_ingredient_component_normalized,
        :strength_raw,
        :strength_component,
        :strength_parse_status,
        :component_parse_status,
        :drug_name,
        :form,
        :reference_drug,
        :reference_standard,
        :appl_type,
        :sponsor_name,
        :appl_public_notes,
        :marketing_status_id,
        :marketing_status_description,
        :original_submission_status,
        NULLIF(:original_submission_status_date, '')::date,
        :latest_submission_status,
        NULLIF(:latest_submission_status_date, '')::date,
        NULLIF(:latest_approved_submission_status_date, '')::date,
        :has_approved_submission,
        :fda_rxnorm_rxcui,
        :fda_rxnorm_canonical_name,
        :fda_rxnorm_term_type,
        :fda_rxnorm_ingredient_rxcui,
        :fda_rxnorm_ingredient_name,
        :fda_rxnorm_ingredient_term_type,
        :fda_rxnorm_match_stage,
        :fda_rxnorm_match_status,
        :fda_rxnorm_ingredient_resolution_stage,
        :fda_rxnorm_ingredient_path,
        :fda_link_anchor_rxcui,
        :fda_link_anchor_name,
        :fda_link_anchor_term_type,
        :fda_link_anchor_strategy,
        :fda_link_anchor_path,
        :fda_link_anchor_manual_review_needed,
        :fda_manual_review_needed,
        CAST(:resolution_payload AS jsonb)
    )
    RETURNING fda_product_ingredient_component_id
    """
)

INSERT_ANCHOR_MAPPING_SQL = text(
    """
    INSERT INTO drug_classification.rxnorm_fda_anchor_mapping (
        fda_product_ingredient_component_id,
        load_batch_id,
        rxnorm_fda_anchor_rxcui,
        rxnorm_fda_anchor_name,
        rxnorm_fda_anchor_term_type,
        rxnorm_fda_anchor_strategy,
        rxnorm_fda_anchor_path,
        representative_rxnorm_ingredient_rxcui,
        representative_rxnorm_ingredient_name,
        representative_rxnorm_ingredient_term_type,
        ctgov_term_count,
        ctgov_manual_review_needed_any,
        ctgov_input_drug_name_sample,
        link_status,
        manual_review_needed,
        resolution_payload,
        rxnorm_source_version,
        fda_source_version
    )
    VALUES (
        :fda_product_ingredient_component_id,
        :load_batch_id,
        :rxnorm_fda_anchor_rxcui,
        :rxnorm_fda_anchor_name,
        :rxnorm_fda_anchor_term_type,
        :rxnorm_fda_anchor_strategy,
        :rxnorm_fda_anchor_path,
        :representative_rxnorm_ingredient_rxcui,
        :representative_rxnorm_ingredient_name,
        :representative_rxnorm_ingredient_term_type,
        :ctgov_term_count,
        :ctgov_manual_review_needed_any,
        :ctgov_input_drug_name_sample,
        :link_status,
        :manual_review_needed,
        CAST(:resolution_payload AS jsonb),
        :rxnorm_source_version,
        :fda_source_version
    )
    """
)

SUMMARY_DELIMITER = " | "
SAMPLE_LIMIT = 12
MAX_FDA_DRUG_NAME_ANCHORS_PER_CT_GOV_TERM = 8

# FDA-specific equivalence used only as a fallback when direct RxCUI anchoring
# does not find a FDA product component for a CTGov FDA anchor. This is
# intentionally narrow: it normalizes FDA biologic proper-name prefixes and
# trailing four-letter suffixes while preserving payload/linker terms such as
# vedotin, govitecan, deruxtecan, and emtansine.
FDA_PROPER_NAME_PREFIX_RE = re.compile(r"^(?:ado|fam)[-\s]+", re.IGNORECASE)
FDA_BIOLOGIC_SUFFIX_RE = re.compile(r"[-\s]+[a-z]{4}$", re.IGNORECASE)
ANCHOR_EQUIVALENCE_COMPONENT_MATCH = "ANCHOR_EQUIVALENCE_KEY_FALLBACK"
DIRECT_RXCUI_COMPONENT_MATCH = "DIRECT_RXCUI"


@dataclass(frozen=True)
class CtgovDrugTermForFda:
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


@dataclass(frozen=True)
class RxnormFdaAnchorForFda:
    rxnorm_fda_anchor_rxcui: str
    rxnorm_fda_anchor_name: str
    rxnorm_fda_anchor_term_type: str
    rxnorm_fda_anchor_strategy: str
    rxnorm_fda_anchor_path: str
    representative_rxnorm_ingredient_rxcui: str
    representative_rxnorm_ingredient_name: str
    representative_rxnorm_ingredient_term_type: str
    rxnorm_source_version: str
    ctgov_term_count: int
    ctgov_input_drug_name_sample: str
    ctgov_source_field_distribution: str
    ctgov_term_kind_distribution: str
    ctgov_manual_review_needed_any: bool
    representative_rxnorm_rxcui_sample: str
    representative_rxnorm_canonical_name_sample: str


def fetch_ctgov_drug_terms_for_fda(engine: Engine, rxnorm_source_version: str) -> list[CtgovDrugTermForFda]:
    with engine.connect() as conn:
        rows = conn.execute(
            FETCH_CT_GOV_DRUG_TERMS_SQL,
            {"rxnorm_source_version": rxnorm_source_version},
        ).mappings().all()

    return [
        CtgovDrugTermForFda(
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


def sorted_unique(values: Iterable[object]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        cleaned = clean_text(value)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
    return sorted(out)


def format_counts(values: Iterable[object]) -> str:
    counts: Counter[str] = Counter(clean_text(value) or "<blank>" for value in values)
    return SUMMARY_DELIMITER.join(
        f"{key}={counts[key]}" for key in sorted(counts, key=lambda k: (-counts[k], k))
    )


def join_sample(values: Iterable[object], limit: int = SAMPLE_LIMIT) -> str:
    return SUMMARY_DELIMITER.join(sorted_unique(values)[:limit])


def derive_ctgov_fda_anchor(term: CtgovDrugTermForFda, conso_index: RxnConsoIndex) -> FdaLinkAnchor:
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


def dedupe_anchors(anchors: Iterable[FdaLinkAnchor]) -> list[FdaLinkAnchor]:
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


def ctgov_fda_drug_name_lookup_keys(term: CtgovDrugTermForFda) -> list[str]:
    return sorted_unique(
        normalize_fda_ingredient_text(value)
        for value in (term.input_drug_name, term.rxnorm_canonical_name)
        if clean_text(value)
    )


def should_use_fda_drug_name_anchors(
    term: CtgovDrugTermForFda,
    matched_drug_name_anchors: Sequence[FdaLinkAnchor],
) -> bool:
    if not matched_drug_name_anchors:
        return False
    if len(matched_drug_name_anchors) > MAX_FDA_DRUG_NAME_ANCHORS_PER_CT_GOV_TERM:
        return False

    # The most important case is a CTGov term that resolved to an RxNorm brand.
    # For ADCs/conjugates and combination products, the brand's broad RxNorm
    # ingredient may be too blunt; FDA DrugName gives the product-component
    # anchors directly. Example: PADCEV -> ENFORTUMAB VEDOTIN-EJFV, not plain
    # enfortumab.
    if term.rxnorm_term_type == "BN":
        return True

    return False


def derive_ctgov_fda_anchors(
    term: CtgovDrugTermForFda,
    conso_index: RxnConsoIndex,
    fda_drug_name_anchor_index: Mapping[str, Sequence[FdaLinkAnchor]],
) -> list[FdaLinkAnchor]:
    default_anchor = derive_ctgov_fda_anchor(term, conso_index)

    drug_name_anchors: list[FdaLinkAnchor] = []
    for key in ctgov_fda_drug_name_lookup_keys(term):
        drug_name_anchors.extend(fda_drug_name_anchor_index.get(key, []))
    drug_name_anchors = dedupe_anchors(drug_name_anchors)

    if should_use_fda_drug_name_anchors(term, drug_name_anchors):
        return drug_name_anchors

    return [default_anchor]


def ctgov_anchor_insert_records(
    term: CtgovDrugTermForFda,
    conso_index: RxnConsoIndex,
    fda_drug_name_anchor_index: Mapping[str, Sequence[FdaLinkAnchor]],
    load_batch_id: uuid.UUID,
    fda_source_version: str,
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for anchor in derive_ctgov_fda_anchors(term, conso_index, fda_drug_name_anchor_index):
        records.append(
            {
                "ctgov_drug_term_id": term.ctgov_drug_term_id,
                "load_batch_id": str(load_batch_id),
                "input_drug_name": term.input_drug_name,
                "input_drug_name_normalized": term.input_drug_name_normalized,
                "source_field": term.source_field,
                "term_kind": term.term_kind,
                "rxnorm_rxcui": term.rxnorm_rxcui or None,
                "rxnorm_canonical_name": term.rxnorm_canonical_name or None,
                "rxnorm_term_type": term.rxnorm_term_type or None,
                "rxnorm_ingredient_rxcui": term.rxnorm_ingredient_rxcui or None,
                "rxnorm_ingredient_name": term.rxnorm_ingredient_name or None,
                "rxnorm_ingredient_term_type": term.rxnorm_ingredient_term_type or None,
                "rxnorm_mapping_manual_review_needed": term.rxnorm_mapping_manual_review_needed,
                "rxnorm_fda_anchor_rxcui": anchor.link_anchor_rxcui or term.rxnorm_ingredient_rxcui,
                "rxnorm_fda_anchor_name": anchor.link_anchor_name or term.rxnorm_ingredient_name,
                "rxnorm_fda_anchor_term_type": anchor.link_anchor_term_type or term.rxnorm_ingredient_term_type,
                "rxnorm_fda_anchor_strategy": anchor.link_anchor_strategy,
                "rxnorm_fda_anchor_path": anchor.link_anchor_path,
                "rxnorm_fda_anchor_manual_review_needed": anchor.link_anchor_manual_review_needed,
                "rxnorm_source_version": term.rxnorm_source_version,
                "fda_source_version": fda_source_version,
            }
        )
    return records


def insert_ctgov_term_anchors(
    conn: Connection,
    terms: Sequence[CtgovDrugTermForFda],
    conso_index: RxnConsoIndex,
    fda_drug_name_anchor_index: Mapping[str, Sequence[FdaLinkAnchor]],
    load_batch_id: uuid.UUID,
    fda_source_version: str,
) -> None:
    if not terms:
        return

    records: list[dict[str, object]] = []
    for term in terms:
        records.extend(
            ctgov_anchor_insert_records(
                term,
                conso_index,
                fda_drug_name_anchor_index,
                load_batch_id,
                fda_source_version,
            )
        )

    if records:
        conn.execute(INSERT_CT_GOV_ANCHOR_SQL, records)


def group_terms_to_fda_anchors(
    terms: Sequence[CtgovDrugTermForFda],
    conso_index: RxnConsoIndex,
    fda_drug_name_anchor_index: Mapping[str, Sequence[FdaLinkAnchor]],
) -> list[RxnormFdaAnchorForFda]:
    by_anchor: dict[str, list[tuple[CtgovDrugTermForFda, FdaLinkAnchor]]] = defaultdict(list)

    for term in terms:
        for anchor in derive_ctgov_fda_anchors(term, conso_index, fda_drug_name_anchor_index):
            if not anchor.link_anchor_rxcui:
                # This should be rare because Part 2 selection requires matched terms,
                # but keep a conservative fallback to the Part 2 ingredient anchor.
                fallback_key = term.rxnorm_ingredient_rxcui
            else:
                fallback_key = anchor.link_anchor_rxcui
            by_anchor[fallback_key].append((term, anchor))

    anchors: list[RxnormFdaAnchorForFda] = []
    for anchor_rxcui, members_with_anchors in sorted(by_anchor.items(), key=lambda item: item[0]):
        members = [item[0] for item in members_with_anchors]
        anchors_for_members = [item[1] for item in members_with_anchors]
        first_anchor = anchors_for_members[0]

        anchor_name_counts = Counter(anchor.link_anchor_name for anchor in anchors_for_members if anchor.link_anchor_name)
        anchor_tty_counts = Counter(anchor.link_anchor_term_type for anchor in anchors_for_members if anchor.link_anchor_term_type)
        strategy_counts = Counter(anchor.link_anchor_strategy for anchor in anchors_for_members if anchor.link_anchor_strategy)
        path_counts = Counter(anchor.link_anchor_path for anchor in anchors_for_members if anchor.link_anchor_path)
        ingredient_name_counts = Counter(member.rxnorm_ingredient_name for member in members if member.rxnorm_ingredient_name)
        ingredient_tty_counts = Counter(member.rxnorm_ingredient_term_type for member in members if member.rxnorm_ingredient_term_type)

        unique_term_ids = {member.ctgov_drug_term_id for member in members}
        anchors.append(
            RxnormFdaAnchorForFda(
                rxnorm_fda_anchor_rxcui=anchor_rxcui,
                rxnorm_fda_anchor_name=anchor_name_counts.most_common(1)[0][0]
                if anchor_name_counts
                else first_anchor.link_anchor_name,
                rxnorm_fda_anchor_term_type=anchor_tty_counts.most_common(1)[0][0]
                if anchor_tty_counts
                else first_anchor.link_anchor_term_type,
                rxnorm_fda_anchor_strategy=format_counts(anchor.link_anchor_strategy for anchor in anchors_for_members),
                rxnorm_fda_anchor_path=path_counts.most_common(1)[0][0] if path_counts else first_anchor.link_anchor_path,
                representative_rxnorm_ingredient_rxcui=join_sample(member.rxnorm_ingredient_rxcui for member in members),
                representative_rxnorm_ingredient_name=ingredient_name_counts.most_common(1)[0][0]
                if ingredient_name_counts
                else "",
                representative_rxnorm_ingredient_term_type=ingredient_tty_counts.most_common(1)[0][0]
                if ingredient_tty_counts
                else "",
                rxnorm_source_version=members[0].rxnorm_source_version,
                ctgov_term_count=len(unique_term_ids),
                ctgov_input_drug_name_sample=join_sample(member.input_drug_name for member in members),
                ctgov_source_field_distribution=format_counts(member.source_field for member in members),
                ctgov_term_kind_distribution=format_counts(member.term_kind for member in members),
                ctgov_manual_review_needed_any=any(member.rxnorm_mapping_manual_review_needed for member in members)
                or any(anchor.link_anchor_manual_review_needed for anchor in anchors_for_members),
                representative_rxnorm_rxcui_sample=join_sample(member.rxnorm_rxcui for member in members),
                representative_rxnorm_canonical_name_sample=join_sample(member.rxnorm_canonical_name for member in members),
            )
        )
    return anchors


def create_load_batch(conn: Connection, load_batch_id: uuid.UUID, source_file: str, source_version: str) -> None:
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
        {"load_batch_id": str(load_batch_id), "row_count": row_count},
    )


def component_insert_record(
    component: FdaProductIngredientComponent,
    load_batch_id: uuid.UUID,
    fda_source_version: str,
) -> dict[str, object]:
    record = asdict(component)
    record.update({"load_batch_id": str(load_batch_id), "fda_source_version": fda_source_version})
    return record


def component_key(component: FdaProductIngredientComponent) -> tuple[str, str, int, str]:
    return (
        component.appl_no,
        component.product_no,
        component.ingredient_position,
        component.active_ingredient_component_normalized,
    )


def insert_components(
    conn: Connection,
    components: Sequence[FdaProductIngredientComponent],
    load_batch_id: uuid.UUID,
    fda_source_version: str,
) -> dict[tuple[str, str, int, str], int]:
    component_ids: dict[tuple[str, str, int, str], int] = {}
    for component in components:
        result = conn.execute(
            INSERT_COMPONENT_SQL,
            component_insert_record(component, load_batch_id, fda_source_version),
        )
        component_ids[component_key(component)] = int(result.scalar_one())
    return component_ids


def normalize_anchor_equivalence_key(value: object) -> str:
    """Return a conservative FDA biologic/proper-name equivalence key.

    Examples:
      polatuzumab vedotin-piiq -> polatuzumab vedotin
      fam-trastuzumab deruxtecan-nxki -> trastuzumab deruxtecan
      ado-trastuzumab emtansine -> trastuzumab emtansine
      trastuzumab-anns -> trastuzumab

    The key is used only as a fallback after direct RxCUI matching fails.
    """
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


def anchor_mapping_payload(
    anchor: RxnormFdaAnchorForFda,
    component: FdaProductIngredientComponent | None,
    link_status: str,
    fda_component_match_strategy: str,
) -> str:
    return json.dumps(
        {
            "rxnorm_fda_anchor": asdict(anchor),
            "fda_product_ingredient_component": asdict(component) if component else None,
            "link_status": link_status,
            "fda_component_match_strategy": fda_component_match_strategy,
            "ctgov_anchor_equivalence_key": normalize_anchor_equivalence_key(anchor.rxnorm_fda_anchor_name),
            "fda_component_anchor_equivalence_key": normalize_anchor_equivalence_key(component.fda_link_anchor_name)
            if component
            else "",
        },
        ensure_ascii=False,
    )


def anchor_mapping_record(
    anchor: RxnormFdaAnchorForFda,
    component: FdaProductIngredientComponent | None,
    component_id: int | None,
    load_batch_id: uuid.UUID,
    fda_source_version: str,
    fda_component_match_strategy: str,
) -> dict[str, object]:
    matched = component is not None
    link_status = "MATCHED_FDA_PRODUCT_INGREDIENT" if matched else "NO_FDA_PRODUCT_FOR_RXNORM_INGREDIENT"
    fda_manual_review_needed = bool(component.fda_manual_review_needed) if component is not None else False
    manual_review_needed = bool(anchor.ctgov_manual_review_needed_any or fda_manual_review_needed)
    return {
        "fda_product_ingredient_component_id": component_id,
        "load_batch_id": str(load_batch_id),
        "rxnorm_fda_anchor_rxcui": anchor.rxnorm_fda_anchor_rxcui,
        "rxnorm_fda_anchor_name": anchor.rxnorm_fda_anchor_name or None,
        "rxnorm_fda_anchor_term_type": anchor.rxnorm_fda_anchor_term_type or None,
        "rxnorm_fda_anchor_strategy": anchor.rxnorm_fda_anchor_strategy or None,
        "rxnorm_fda_anchor_path": anchor.rxnorm_fda_anchor_path or None,
        "representative_rxnorm_ingredient_rxcui": anchor.representative_rxnorm_ingredient_rxcui or None,
        "representative_rxnorm_ingredient_name": anchor.representative_rxnorm_ingredient_name or None,
        "representative_rxnorm_ingredient_term_type": anchor.representative_rxnorm_ingredient_term_type or None,
        "ctgov_term_count": anchor.ctgov_term_count,
        "ctgov_manual_review_needed_any": anchor.ctgov_manual_review_needed_any,
        "ctgov_input_drug_name_sample": anchor.ctgov_input_drug_name_sample or None,
        "link_status": link_status,
        "manual_review_needed": manual_review_needed,
        "resolution_payload": anchor_mapping_payload(
            anchor,
            component,
            link_status,
            fda_component_match_strategy=fda_component_match_strategy,
        ),
        "rxnorm_source_version": anchor.rxnorm_source_version,
        "fda_source_version": fda_source_version,
    }


def build_component_anchor_indexes(
    components: Sequence[FdaProductIngredientComponent],
) -> tuple[dict[str, list[FdaProductIngredientComponent]], dict[str, list[FdaProductIngredientComponent]]]:
    components_by_anchor_rxcui: dict[str, list[FdaProductIngredientComponent]] = defaultdict(list)
    components_by_anchor_equivalence_key: dict[str, list[FdaProductIngredientComponent]] = defaultdict(list)

    for component in components:
        if component.fda_link_anchor_rxcui:
            components_by_anchor_rxcui[component.fda_link_anchor_rxcui].append(component)

        # The equivalence index is deliberately limited to FDA component anchors
        # whose own anchor name changes under suffix/prefix normalization. This
        # prevents broad name-only joins and targets the known proper-name suffix
        # failure mode, e.g. polatuzumab vedotin-piiq -> polatuzumab vedotin.
        if component.fda_link_anchor_name and anchor_equivalence_key_was_transformed(component.fda_link_anchor_name):
            key = normalize_anchor_equivalence_key(component.fda_link_anchor_name)
            if key:
                components_by_anchor_equivalence_key[key].append(component)

    return components_by_anchor_rxcui, components_by_anchor_equivalence_key


def matching_components_for_anchor(
    anchor: RxnormFdaAnchorForFda,
    components_by_anchor_rxcui: Mapping[str, Sequence[FdaProductIngredientComponent]],
    components_by_anchor_equivalence_key: Mapping[str, Sequence[FdaProductIngredientComponent]],
) -> tuple[list[FdaProductIngredientComponent], str]:
    direct = list(components_by_anchor_rxcui.get(anchor.rxnorm_fda_anchor_rxcui, []))
    if direct:
        return direct, DIRECT_RXCUI_COMPONENT_MATCH

    key = normalize_anchor_equivalence_key(anchor.rxnorm_fda_anchor_name)
    fallback = list(components_by_anchor_equivalence_key.get(key, [])) if key else []
    if fallback:
        return fallback, ANCHOR_EQUIVALENCE_COMPONENT_MATCH

    return [], "NO_COMPONENT_MATCH"


def build_mapping_records(
    anchors: Sequence[RxnormFdaAnchorForFda],
    components: Sequence[FdaProductIngredientComponent],
    component_ids: Mapping[tuple[str, str, int, str], int],
    load_batch_id: uuid.UUID,
    fda_source_version: str,
) -> list[dict[str, object]]:
    components_by_anchor_rxcui, components_by_anchor_equivalence_key = build_component_anchor_indexes(components)

    records: list[dict[str, object]] = []
    for anchor in anchors:
        matching_components, match_strategy = matching_components_for_anchor(
            anchor,
            components_by_anchor_rxcui,
            components_by_anchor_equivalence_key,
        )
        if not matching_components:
            records.append(
                anchor_mapping_record(
                    anchor=anchor,
                    component=None,
                    component_id=None,
                    load_batch_id=load_batch_id,
                    fda_source_version=fda_source_version,
                    fda_component_match_strategy=match_strategy,
                )
            )
            continue

        for component in matching_components:
            records.append(
                anchor_mapping_record(
                    anchor=anchor,
                    component=component,
                    component_id=component_ids[component_key(component)],
                    load_batch_id=load_batch_id,
                    fda_source_version=fda_source_version,
                    fda_component_match_strategy=match_strategy,
                )
            )
    return records

def product_key(component: FdaProductIngredientComponent) -> str:
    return f"{component.appl_no}-{component.product_no}" if component.appl_no and component.product_no else ""


def product_sample(component: FdaProductIngredientComponent) -> str:
    key = product_key(component)
    pieces = [
        key,
        component.drug_name,
        component.active_ingredient_component,
        component.strength_component,
        component.marketing_status_description,
        component.appl_type,
    ]
    return " :: ".join(clean_text(piece) for piece in pieces if clean_text(piece))


def build_component_lookup(
    components: Sequence[FdaProductIngredientComponent],
    component_ids: Mapping[tuple[str, str, int, str], int],
) -> dict[int, FdaProductIngredientComponent]:
    return {component_ids[component_key(component)]: component for component in components}


def record_component_match_strategy(record: Mapping[str, object]) -> str:
    payload_raw = record.get("resolution_payload")
    if not payload_raw:
        return ""
    try:
        payload = json.loads(str(payload_raw))
    except json.JSONDecodeError:
        return ""
    return clean_text(payload.get("fda_component_match_strategy"))


def compact_check_row(
    anchor: RxnormFdaAnchorForFda,
    records_for_ingredient: Sequence[Mapping[str, object]],
    component_by_id: Mapping[int, FdaProductIngredientComponent],
) -> dict[str, object]:
    matched_components: list[FdaProductIngredientComponent] = []
    for record in records_for_ingredient:
        component_id_raw = record.get("fda_product_ingredient_component_id")
        if component_id_raw is None:
            continue
        component = component_by_id.get(int(component_id_raw))
        if component is not None:
            matched_components.append(component)

    strength_issue_components = [
        component
        for component in matched_components
        if component.strength_parse_status
        and component.strength_parse_status not in {"SINGLE_INGREDIENT_RAW_STRENGTH", "POSITIONAL_STRENGTH_MATCH"}
    ]
    component_issue_components = [
        component for component in matched_components if component.component_parse_status and component.component_parse_status != "OK"
    ]
    manual_review_components = [component for component in matched_components if component.fda_manual_review_needed]

    return {
        "rxnorm_fda_anchor_rxcui": anchor.rxnorm_fda_anchor_rxcui,
        "rxnorm_fda_anchor_name": anchor.rxnorm_fda_anchor_name,
        "rxnorm_fda_anchor_term_type": anchor.rxnorm_fda_anchor_term_type,
        "rxnorm_fda_anchor_strategy": anchor.rxnorm_fda_anchor_strategy,
        "rxnorm_fda_anchor_path": anchor.rxnorm_fda_anchor_path,
        "representative_rxnorm_ingredient_rxcui": anchor.representative_rxnorm_ingredient_rxcui,
        "representative_rxnorm_ingredient_name": anchor.representative_rxnorm_ingredient_name,
        "representative_rxnorm_ingredient_term_type": anchor.representative_rxnorm_ingredient_term_type,
        "fda_link_status": (
            "MATCHED_FDA_PRODUCT_INGREDIENT" if matched_components else "NO_FDA_PRODUCT_FOR_RXNORM_INGREDIENT"
        ),
        "fda_mapping_row_count": len(records_for_ingredient),
        "fda_component_match_strategy_distribution": format_counts(
            record_component_match_strategy(record) for record in records_for_ingredient
        ),
        "fda_component_count": len(matched_components),
        "fda_product_count": len({product_key(component) for component in matched_components if product_key(component)}),
        "fda_application_count": len({component.appl_no for component in matched_components if component.appl_no}),
        "fda_active_ingredient_component_count": len(
            {component.active_ingredient_component for component in matched_components if component.active_ingredient_component}
        ),
        "fda_active_ingredient_component_sample": join_sample(
            component.active_ingredient_component for component in matched_components
        ),
        "fda_drug_name_count": len({component.drug_name for component in matched_components if component.drug_name}),
        "fda_drug_name_sample": join_sample(component.drug_name for component in matched_components),
        "marketing_status_distribution": format_counts(component.marketing_status_description for component in matched_components)
        if matched_components
        else "",
        "appl_type_distribution": format_counts(component.appl_type for component in matched_components) if matched_components else "",
        "has_approved_submission_count": sum(1 for component in matched_components if component.has_approved_submission),
        "latest_approved_submission_status_date_max": max(
            [component.latest_approved_submission_status_date for component in matched_components if component.latest_approved_submission_status_date],
            default="",
        ),
        "fda_manual_review_component_count": len(manual_review_components),
        "strength_parse_issue_component_count": len(strength_issue_components),
        "component_parse_issue_component_count": len(component_issue_components),
        "sample_fda_products": SUMMARY_DELIMITER.join(
            sorted_unique(product_sample(component) for component in matched_components)[:SAMPLE_LIMIT]
        ),
        "ctgov_term_count": anchor.ctgov_term_count,
        "ctgov_input_drug_name_sample": anchor.ctgov_input_drug_name_sample,
        "ctgov_source_field_distribution": anchor.ctgov_source_field_distribution,
        "ctgov_term_kind_distribution": anchor.ctgov_term_kind_distribution,
        "ctgov_manual_review_needed_any": anchor.ctgov_manual_review_needed_any,
        "representative_rxnorm_rxcui_sample": anchor.representative_rxnorm_rxcui_sample,
        "representative_rxnorm_canonical_name_sample": anchor.representative_rxnorm_canonical_name_sample,
        "manual_review_needed_any": any(bool(record.get("manual_review_needed")) for record in records_for_ingredient),
        "rxnorm_source_version": anchor.rxnorm_source_version,
        "fda_source_version": clean_text(records_for_ingredient[0].get("fda_source_version")) if records_for_ingredient else "",
    }


def write_check_tsv(
    anchors: Sequence[RxnormFdaAnchorForFda],
    records: Iterable[Mapping[str, object]],
    component_by_id: Mapping[int, FdaProductIngredientComponent],
    output_tsv: Path,
) -> None:
    by_ingredient: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for record in records:
        by_ingredient[clean_text(record["rxnorm_fda_anchor_rxcui"])].append(record)

    anchor_by_rxcui = {anchor.rxnorm_fda_anchor_rxcui: anchor for anchor in anchors}
    rows = [
        compact_check_row(anchor_by_rxcui[rxcui], by_ingredient[rxcui], component_by_id)
        for rxcui in sorted(by_ingredient)
    ]

    output_tsv.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        output_tsv.write_text("", encoding="utf-8")
        return

    with output_tsv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def detail_row(
    record: Mapping[str, object],
    component_by_id: Mapping[int, FdaProductIngredientComponent],
) -> dict[str, object]:
    row = {key: value for key, value in record.items() if key != "resolution_payload"}
    component_id_raw = record.get("fda_product_ingredient_component_id")
    if component_id_raw is None:
        return row
    component = component_by_id.get(int(component_id_raw))
    if component is None:
        return row
    component_fields = asdict(component)
    component_fields.pop("resolution_payload", None)
    row.update(component_fields)
    return row


def write_detail_tsv(
    records: Iterable[Mapping[str, object]],
    component_by_id: Mapping[int, FdaProductIngredientComponent],
    output_tsv: Path,
) -> None:
    rows = [detail_row(record, component_by_id) for record in records]
    output_tsv.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        output_tsv.write_text("", encoding="utf-8")
        return

    with output_tsv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def load_fda_mappings_to_postgres(
    database_url: str,
    fda_raw_dir: Path,
    rxnorm_rrf_dir: Path,
    rxnorm_source_version: str,
    fda_source_version: str,
    output_check_tsv: Path | None,
    output_detail_tsv: Path | None = None,
) -> uuid.UUID:
    engine = create_engine(database_url)

    terms = fetch_ctgov_drug_terms_for_fda(engine, rxnorm_source_version)

    logger.info("Loading RxNorm indexes from %s", rxnorm_rrf_dir)
    conso_index = RxnConsoIndex.from_rrf_dir(rxnorm_rrf_dir)
    rel_index = RxnRelIndex.from_rrf_dir(rxnorm_rrf_dir)

    components = build_fda_product_ingredient_components(
        fda_raw_dir=fda_raw_dir,
        conso_index=conso_index,
        rel_index=rel_index,
    )
    fda_drug_name_anchor_index = build_fda_drug_name_anchor_index(components)
    logger.info("Built FDA DrugName anchor index for %d normalized FDA drug names", len(fda_drug_name_anchor_index))

    anchors = group_terms_to_fda_anchors(terms, conso_index, fda_drug_name_anchor_index)
    logger.info(
        "Fetched %d CTGov drug terms and collapsed them to %d unique FDA RxNorm anchors for FDA mapping",
        len(terms),
        len(anchors),
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
        conn.execute(
            DELETE_EXISTING_ANCHOR_MAPPINGS_SQL,
            {"fda_source_version": fda_source_version, "rxnorm_source_version": rxnorm_source_version},
        )
        conn.execute(
            DELETE_EXISTING_CT_GOV_ANCHORS_SQL,
            {"fda_source_version": fda_source_version, "rxnorm_source_version": rxnorm_source_version},
        )
        conn.execute(DELETE_EXISTING_COMPONENTS_SQL, {"fda_source_version": fda_source_version})
        insert_ctgov_term_anchors(conn, terms, conso_index, fda_drug_name_anchor_index, load_batch_id, fda_source_version)
        component_ids = insert_components(conn, components, load_batch_id, fda_source_version)
        component_by_id = build_component_lookup(components, component_ids)
        records = build_mapping_records(
            anchors=anchors,
            components=components,
            component_ids=component_ids,
            load_batch_id=load_batch_id,
            fda_source_version=fda_source_version,
        )
        if records:
            conn.execute(INSERT_ANCHOR_MAPPING_SQL, records)
        complete_load_batch(conn, load_batch_id, row_count=len(records))

    if output_check_tsv is not None:
        write_check_tsv(anchors, records, component_by_id, output_check_tsv)
        logger.info("Wrote ingredient-level FDA check TSV to %s", output_check_tsv)

    if output_detail_tsv is not None:
        write_detail_tsv(records, component_by_id, output_detail_tsv)
        logger.info("Wrote ingredient-level FDA detail TSV to %s", output_detail_tsv)

    logger.info(
        "Loaded FDA mappings for %d unique FDA RxNorm anchors into %d rows with load_batch_id=%s",
        len(anchors),
        len(records),
        load_batch_id,
    )
    return load_batch_id


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Load FDA Drugs@FDA mappings for RxNorm ingredient anchors into PostgreSQL."
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
        help="Directory containing RXNCONSO.RRF and RXNREL.RRF.",
    )
    parser.add_argument(
        "--rxnorm_source_version",
        required=True,
        help="RxNorm source version to select from Part 2 mappings.",
    )
    parser.add_argument(
        "--fda_source_version",
        required=True,
        help="FDA source version label, e.g. FDA_16042026.",
    )
    parser.add_argument(
        "--output_check_tsv",
        type=Path,
        default=None,
        help="Optional compact one-row-per-RxNorm-ingredient TSV for manual checking.",
    )
    parser.add_argument(
        "--output_detail_tsv",
        type=Path,
        default=None,
        help="Optional RxNorm-ingredient x FDA-product-component evidence TSV.",
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

    load_fda_mappings_to_postgres(
        database_url=database_url,
        fda_raw_dir=args.fda_raw_dir,
        rxnorm_rrf_dir=args.rxnorm_rrf_dir,
        rxnorm_source_version=args.rxnorm_source_version,
        fda_source_version=args.fda_source_version,
        output_check_tsv=args.output_check_tsv,
        output_detail_tsv=args.output_detail_tsv,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
