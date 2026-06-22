from __future__ import annotations

"""Load POTTR classifications for simplified CTGov drug ontology outputs.

Input tables:
    drug_identity.ctgov_drug_term_rxnorm_mapping
    drug_identity.ctgov_intervention_drug_term_link

POTTR is regimen/combination-aware, so mapping cannot be reduced to RxNorm
ingredient only. The loader creates a POTTR anchor for each input_drug_name using:

    1. exact/conservative POTTR alias matching, for regimens/products such as
       FOLFOX, CAPOX, R-CHOP, Kadcyla, Enhertu, Opdualag;
    2. RxNorm-derived anchors for ordinary single-agent terms.

Essential outputs are SQL-derived only:
    pottr_anchor_class_mapping_export
    ctgov_intervention_pottr_link_export
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

from aus_trial_universe.drug_utility_path.ctgov.drug_ontology.identity.rxnorm.matcher import RxnConsoIndex, RxnRelIndex
from aus_trial_universe.drug_utility_path.ctgov.drug_ontology.sources.pottr.drug_classes import (
    PottrDrugAlias,
    PottrDrugClassAssignment,
    PottrDrugConcept,
    PottrLinkAnchor,
    build_pottr_records,
    clean_text,
    derive_pottr_link_anchor_from_values,
    normalize_alias_key,
)

logger = logging.getLogger(__name__)


FETCH_RXNORM_DRUG_TERMS_SQL = text(
    """
    SELECT DISTINCT
        input_drug_name,
        match_status,
        rxnorm_rxcui,
        rxnorm_canonical_name,
        rxnorm_term_type,
        rxnorm_ingredient_rxcui,
        rxnorm_ingredient_name,
        rxnorm_ingredient_term_type
    FROM drug_identity.ctgov_drug_term_rxnorm_mapping
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
        'pottr_anchor_class_resolution',
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

TRUNCATE_POTTR_TABLES_SQL = text(
    """
    TRUNCATE TABLE
        drug_classification.input_drug_name_pottr_anchor,
        drug_classification.pottr_anchor_class_mapping,
        drug_classification.pottr_drug_class_assignment,
        drug_classification.pottr_drug_concept
    """
)

INSERT_POTTR_CONCEPT_SQL = text(
    """
    INSERT INTO drug_classification.pottr_drug_concept (
        pottr_concept_key,
        concept_index,
        drug_raw,
        canonical_drug_name,
        aliases_raw,
        direct_classes_raw
    )
    VALUES (
        :pottr_concept_key,
        :concept_index,
        :drug_raw,
        :canonical_drug_name,
        :aliases_raw,
        :direct_classes_raw
    )
    ON CONFLICT (pottr_concept_key)
    DO UPDATE SET
        concept_index = EXCLUDED.concept_index,
        drug_raw = EXCLUDED.drug_raw,
        canonical_drug_name = EXCLUDED.canonical_drug_name,
        aliases_raw = EXCLUDED.aliases_raw,
        direct_classes_raw = EXCLUDED.direct_classes_raw
    """
)

INSERT_POTTR_CLASS_ASSIGNMENT_SQL = text(
    """
    INSERT INTO drug_classification.pottr_drug_class_assignment (
        pottr_class_assignment_key,
        pottr_concept_key,
        direct_class_name,
        pottr_class_name,
        class_relation,
        class_depth_from_direct,
        class_path,
        class_in_hierarchy
    )
    VALUES (
        :pottr_class_assignment_key,
        :pottr_concept_key,
        :direct_class_name,
        :pottr_class_name,
        :class_relation,
        :class_depth_from_direct,
        :class_path,
        :class_in_hierarchy
    )
    ON CONFLICT (pottr_class_assignment_key)
    DO UPDATE SET
        pottr_concept_key = EXCLUDED.pottr_concept_key,
        direct_class_name = EXCLUDED.direct_class_name,
        pottr_class_name = EXCLUDED.pottr_class_name,
        class_relation = EXCLUDED.class_relation,
        class_depth_from_direct = EXCLUDED.class_depth_from_direct,
        class_path = EXCLUDED.class_path,
        class_in_hierarchy = EXCLUDED.class_in_hierarchy
    """
)

INSERT_INPUT_POTTR_ANCHOR_SQL = text(
    """
    INSERT INTO drug_classification.input_drug_name_pottr_anchor (
        input_drug_name,
        pottr_anchor_key
    )
    VALUES (
        :input_drug_name,
        :pottr_anchor_key
    )
    ON CONFLICT (input_drug_name, pottr_anchor_key)
    DO NOTHING
    """
)

INSERT_POTTR_ANCHOR_CLASS_MAPPING_SQL = text(
    """
    INSERT INTO drug_classification.pottr_anchor_class_mapping (
        pottr_anchor_key,
        pottr_anchor_type,
        pottr_anchor_rxcui,
        pottr_anchor_name,
        pottr_link_status,
        pottr_concept_key,
        pottr_canonical_drug_name,
        pottr_class_assignment_key,
        direct_class_name,
        pottr_class_name,
        class_relation,
        class_depth_from_direct,
        class_path,
        class_in_hierarchy
    )
    VALUES (
        :pottr_anchor_key,
        :pottr_anchor_type,
        :pottr_anchor_rxcui,
        :pottr_anchor_name,
        :pottr_link_status,
        :pottr_concept_key,
        :pottr_canonical_drug_name,
        :pottr_class_assignment_key,
        :direct_class_name,
        :pottr_class_name,
        :class_relation,
        :class_depth_from_direct,
        :class_path,
        :class_in_hierarchy
    )
    ON CONFLICT (
        pottr_anchor_key,
        pottr_concept_key,
        pottr_class_assignment_key
    )
    DO UPDATE SET
        pottr_anchor_type = EXCLUDED.pottr_anchor_type,
        pottr_anchor_rxcui = EXCLUDED.pottr_anchor_rxcui,
        pottr_anchor_name = EXCLUDED.pottr_anchor_name,
        pottr_link_status = EXCLUDED.pottr_link_status,
        pottr_canonical_drug_name = EXCLUDED.pottr_canonical_drug_name,
        direct_class_name = EXCLUDED.direct_class_name,
        pottr_class_name = EXCLUDED.pottr_class_name,
        class_relation = EXCLUDED.class_relation,
        class_depth_from_direct = EXCLUDED.class_depth_from_direct,
        class_path = EXCLUDED.class_path,
        class_in_hierarchy = EXCLUDED.class_in_hierarchy
    """
)


COMBO_OR_REGIMEN_RE = re.compile(r"(\s\+\s|\bregimen\b)", re.IGNORECASE)
SPECIFIC_PRODUCT_MARKERS = (
    "antibody-drug_conjugate",
    "drug_conjugate",
    "radioconjugate",
    "radioligand",
    "radioiodine_conjugate",
    "radionuclide",
    "bispecific",
    "trispecific",
    "cell_therapy",
    "car-t",
    "car-nk",
    "fusion_protein",
)
PRECISE_ANCHOR_STRATEGIES = {
    "PRECISE_ACTIVE_CORE_EXACT",
    "PRECISE_ACTIVE_MATCHED_RXCUI",
}
RADIONUCLIDE_PREFIX_TOKENS = {
    "177lu", "lu177", "lu", "lutetium", "177",
    "225ac", "ac225", "ac", "actinium", "225",
    "131i", "i131", "iodine", "iodine131", "i", "131",
    "212pb", "pb212", "pb", "lead", "212",
    "67cu", "cu67", "cu", "copper", "67",
    "111in", "in111", "in", "indium", "111",
    "161tb", "tb161", "tb", "terbium", "161",
}


# Controlled POTTR-only alias equivalences.
# These are deliberately kept out of RxNorm. They are used only for direct POTTR
# alias lookup so that known regimen aliases map to the intended POTTR concept
# without contaminating component-drug RxNorm anchors.
CURATED_POTTR_ALIAS_EQUIVALENCES: dict[str, set[str]] = {
    "capeox": {"capox"},
    "capox": {"capeox"},
}


@dataclass(frozen=True)
class RxNormDrugTermForPottr:
    input_drug_name: str
    match_status: str
    rxnorm_rxcui: str
    rxnorm_canonical_name: str
    rxnorm_term_type: str
    rxnorm_ingredient_rxcui: str
    rxnorm_ingredient_name: str
    rxnorm_ingredient_term_type: str


@dataclass
class PottrAnchorCandidate:
    pottr_anchor_key: str
    pottr_anchor_type: str
    pottr_anchor_rxcui: str
    pottr_anchor_name: str
    concept_keys: set[str]


@dataclass
class PottrAnchorAggregate:
    pottr_anchor_key: str
    pottr_anchor_type: str
    pottr_anchor_rxcui: str
    pottr_anchor_name: str
    concept_keys: set[str]
    input_drug_names: set[str]


def fetch_rxnorm_drug_terms_for_pottr(engine: Engine) -> list[RxNormDrugTermForPottr]:
    with engine.connect() as conn:
        rows = conn.execute(FETCH_RXNORM_DRUG_TERMS_SQL).mappings().all()

    return [
        RxNormDrugTermForPottr(
            input_drug_name=clean_text(row["input_drug_name"]),
            match_status=clean_text(row["match_status"]),
            rxnorm_rxcui=clean_text(row["rxnorm_rxcui"]),
            rxnorm_canonical_name=clean_text(row["rxnorm_canonical_name"]),
            rxnorm_term_type=clean_text(row["rxnorm_term_type"]),
            rxnorm_ingredient_rxcui=clean_text(row["rxnorm_ingredient_rxcui"]),
            rxnorm_ingredient_name=clean_text(row["rxnorm_ingredient_name"]),
            rxnorm_ingredient_term_type=clean_text(row["rxnorm_ingredient_term_type"]),
        )
        for row in rows
    ]


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
        {
            "load_batch_id": str(load_batch_id),
            "row_count": row_count,
        },
    )


def is_safe_radionuclide_stripped_core(tokens: Sequence[str]) -> bool:
    if len(tokens) < 2:
        return False
    if any(any(ch.isdigit() for ch in token) for token in tokens):
        return False
    alpha_long = [token for token in tokens if token.isalpha() and len(token) >= 4]
    return len(alpha_long) >= 2


def alias_equivalence_keys(value: object) -> set[str]:
    base = normalize_alias_key(value)
    keys: set[str] = set()
    if base:
        keys.add(base)

    before_paren = clean_text(base.split("(", 1)[0]) if "(" in base else ""
    if before_paren:
        keys.add(before_paren)

    no_parens = clean_text(re.sub(r"\([^)]*\)", " ", base))
    if no_parens and no_parens != base:
        keys.add(normalize_alias_key(no_parens))

    tokens = [tok for tok in re.findall(r"[a-z0-9]+", normalize_alias_key(no_parens or base)) if tok != "and"]
    tokenized = " ".join(tokens)
    if tokenized:
        keys.add(tokenized)

    stripped_tokens = list(tokens)
    while len(stripped_tokens) > 2 and stripped_tokens[0] in RADIONUCLIDE_PREFIX_TOKENS:
        stripped_tokens = stripped_tokens[1:]
        if is_safe_radionuclide_stripped_core(stripped_tokens):
            stripped = " ".join(stripped_tokens)
            if stripped:
                keys.add(stripped)

    expanded_keys = set(keys)
    for key in list(keys):
        expanded_keys.update(CURATED_POTTR_ALIAS_EQUIVALENCES.get(key, set()))

    return {key for key in expanded_keys if key}


def lookup_alias_concepts(
    value: object,
    pottr_by_alias_exact: Mapping[str, set[str]],
    pottr_by_alias_equiv: Mapping[str, set[str]],
) -> tuple[set[str], str]:
    base_key = normalize_alias_key(value)
    if base_key and base_key in pottr_by_alias_exact:
        return set(pottr_by_alias_exact[base_key]), "DIRECT_ALIAS_EXACT"

    concepts: set[str] = set()
    for key in alias_equivalence_keys(value):
        concepts.update(pottr_by_alias_exact.get(key, set()))
        concepts.update(pottr_by_alias_equiv.get(key, set()))
    if concepts:
        return concepts, "DIRECT_ALIAS_EQUIVALENCE"

    return set(), "DIRECT_ALIAS_NO_MATCH"


def concept_text_for_policy(concept: PottrDrugConcept) -> str:
    return " ".join(
        [
            concept.drug_raw,
            concept.canonical_drug_name,
            concept.aliases_raw,
            concept.direct_classes_raw,
        ]
    ).lower()


def concept_is_combo_or_regimen(concept: PottrDrugConcept) -> bool:
    return bool(COMBO_OR_REGIMEN_RE.search(concept_text_for_policy(concept)))


def concept_is_specific_product(concept: PottrDrugConcept) -> bool:
    text_value = concept_text_for_policy(concept)
    return any(marker in text_value for marker in SPECIFIC_PRODUCT_MARKERS)


def build_pottr_indices(
    concepts: Sequence[PottrDrugConcept],
    aliases: Sequence[PottrDrugAlias],
) -> tuple[dict[str, set[str]], dict[str, set[str]], dict[str, set[str]], dict[str, PottrDrugConcept]]:
    concept_by_key = {concept.concept_key: concept for concept in concepts}
    aliases_by_concept: dict[str, list[PottrDrugAlias]] = defaultdict(list)
    by_rxcui: dict[str, set[str]] = defaultdict(set)
    by_alias_exact: dict[str, set[str]] = defaultdict(set)
    by_alias_equiv: dict[str, set[str]] = defaultdict(set)

    for alias in aliases:
        if alias.alias_normalized:
            by_alias_exact[alias.alias_normalized].add(alias.concept_key)
            for alias_key in alias_equivalence_keys(alias.alias_normalized):
                if alias_key != alias.alias_normalized:
                    by_alias_equiv[alias_key].add(alias.concept_key)
        aliases_by_concept[alias.concept_key].append(alias)

    for concept in concepts:
        safe_aliases = [
            alias
            for alias in aliases_by_concept.get(concept.concept_key, [])
            if alias.pottr_link_anchor_rxcui and not alias.manual_review_needed
        ]
        if not safe_aliases:
            continue

        # Do not let single components inherit regimen/combination classes.
        # Exact direct-alias matching remains available for these concepts.
        if concept_is_combo_or_regimen(concept):
            continue

        precise_aliases = [
            alias
            for alias in safe_aliases
            if alias.pottr_link_anchor_strategy in PRECISE_ANCHOR_STRATEGIES
        ]
        aliases_to_index = precise_aliases if precise_aliases else safe_aliases

        for alias in aliases_to_index:
            by_rxcui[alias.pottr_link_anchor_rxcui].add(alias.concept_key)

    return by_rxcui, by_alias_exact, by_alias_equiv, concept_by_key


def derive_rxnorm_pottr_anchor(
    term: RxNormDrugTermForPottr,
    conso_index: RxnConsoIndex,
) -> PottrLinkAnchor | None:
    if term.match_status != "MATCHED" or not term.rxnorm_rxcui:
        return None

    return derive_pottr_link_anchor_from_values(
        source_text=term.input_drug_name,
        matched_rxcui=term.rxnorm_rxcui,
        canonical_name=term.rxnorm_canonical_name,
        canonical_tty=term.rxnorm_term_type,
        ingredient_rxcui=term.rxnorm_ingredient_rxcui,
        ingredient_name=term.rxnorm_ingredient_name,
        ingredient_tty=term.rxnorm_ingredient_term_type,
        conso_index=conso_index,
    )


def direct_alias_anchor(term: RxNormDrugTermForPottr, alias_concepts: set[str]) -> PottrAnchorCandidate:
    alias_key = normalize_alias_key(term.input_drug_name)
    return PottrAnchorCandidate(
        pottr_anchor_key=f"ALIAS:{alias_key}",
        pottr_anchor_type="DIRECT_ALIAS",
        pottr_anchor_rxcui="",
        pottr_anchor_name=term.input_drug_name,
        concept_keys=set(alias_concepts),
    )


def rxnorm_anchor_candidate(anchor: PottrLinkAnchor, concepts: set[str]) -> PottrAnchorCandidate:
    return PottrAnchorCandidate(
        pottr_anchor_key=f"RXCUI:{anchor.link_anchor_rxcui}",
        pottr_anchor_type="RXNORM",
        pottr_anchor_rxcui=anchor.link_anchor_rxcui,
        pottr_anchor_name=anchor.link_anchor_name,
        concept_keys=set(concepts),
    )


def exact_alias_should_override_rxnorm(
    term: RxNormDrugTermForPottr,
    rx_anchor: PottrLinkAnchor,
    rx_concepts: set[str],
    alias_concepts: set[str],
    concept_by_key: Mapping[str, PottrDrugConcept],
) -> bool:
    if not alias_concepts or not rx_concepts:
        return False
    if alias_concepts.issubset(rx_concepts):
        return False

    input_norm = normalize_alias_key(term.input_drug_name)
    broad_anchor_norms = {
        normalize_alias_key(rx_anchor.link_anchor_name),
        normalize_alias_key(term.rxnorm_ingredient_name),
    }
    if input_norm and input_norm in {value for value in broad_anchor_norms if value}:
        return False

    for concept_key in alias_concepts - rx_concepts:
        concept = concept_by_key.get(concept_key)
        if concept is None:
            continue
        if concept_is_combo_or_regimen(concept) or concept_is_specific_product(concept):
            return True

    return False


def choose_anchor_for_term(
    term: RxNormDrugTermForPottr,
    conso_index: RxnConsoIndex,
    pottr_by_rxcui: Mapping[str, set[str]],
    pottr_by_alias_exact: Mapping[str, set[str]],
    pottr_by_alias_equiv: Mapping[str, set[str]],
    concept_by_key: Mapping[str, PottrDrugConcept],
) -> PottrAnchorCandidate:
    alias_concepts, _alias_strategy = lookup_alias_concepts(
        term.input_drug_name,
        pottr_by_alias_exact,
        pottr_by_alias_equiv,
    )
    rx_anchor = derive_rxnorm_pottr_anchor(term, conso_index)

    if rx_anchor and rx_anchor.link_anchor_rxcui:
        rx_concepts = set(pottr_by_rxcui.get(rx_anchor.link_anchor_rxcui, set()))
        if rx_concepts:
            if exact_alias_should_override_rxnorm(term, rx_anchor, rx_concepts, alias_concepts, concept_by_key):
                return direct_alias_anchor(term, alias_concepts)
            return rxnorm_anchor_candidate(rx_anchor, rx_concepts)

        if alias_concepts:
            return direct_alias_anchor(term, alias_concepts)

        return rxnorm_anchor_candidate(rx_anchor, set())

    return direct_alias_anchor(term, alias_concepts)


def aggregate_input_anchors(
    terms: Sequence[RxNormDrugTermForPottr],
    conso_index: RxnConsoIndex,
    pottr_by_rxcui: Mapping[str, set[str]],
    pottr_by_alias_exact: Mapping[str, set[str]],
    pottr_by_alias_equiv: Mapping[str, set[str]],
    concept_by_key: Mapping[str, PottrDrugConcept],
) -> tuple[list[dict[str, object]], dict[str, PottrAnchorAggregate]]:
    link_records: list[dict[str, object]] = []
    aggregates: dict[str, PottrAnchorAggregate] = {}

    for term in terms:
        candidate = choose_anchor_for_term(
            term=term,
            conso_index=conso_index,
            pottr_by_rxcui=pottr_by_rxcui,
            pottr_by_alias_exact=pottr_by_alias_exact,
            pottr_by_alias_equiv=pottr_by_alias_equiv,
            concept_by_key=concept_by_key,
        )

        link_records.append(
            {
                "input_drug_name": term.input_drug_name,
                "pottr_anchor_key": candidate.pottr_anchor_key,
            }
        )

        aggregate = aggregates.get(candidate.pottr_anchor_key)
        if aggregate is None:
            aggregate = PottrAnchorAggregate(
                pottr_anchor_key=candidate.pottr_anchor_key,
                pottr_anchor_type=candidate.pottr_anchor_type,
                pottr_anchor_rxcui=candidate.pottr_anchor_rxcui,
                pottr_anchor_name=candidate.pottr_anchor_name,
                concept_keys=set(candidate.concept_keys),
                input_drug_names={term.input_drug_name},
            )
            aggregates[candidate.pottr_anchor_key] = aggregate
        else:
            aggregate.concept_keys.update(candidate.concept_keys)
            aggregate.input_drug_names.add(term.input_drug_name)

    return link_records, aggregates


def assignment_rows_by_concept(
    assignments: Sequence[PottrDrugClassAssignment],
) -> dict[str, list[PottrDrugClassAssignment]]:
    out: dict[str, list[PottrDrugClassAssignment]] = defaultdict(list)
    for assignment in assignments:
        out[assignment.concept_key].append(assignment)

    for rows in out.values():
        rows.sort(key=lambda row: (row.direct_class_name, row.class_depth_from_direct, row.pottr_class_name, row.assignment_key))

    return out


def pottr_anchor_class_mapping_records(
    aggregates: Mapping[str, PottrAnchorAggregate],
    concepts_by_key: Mapping[str, PottrDrugConcept],
    assignments_by_concept: Mapping[str, Sequence[PottrDrugClassAssignment]],
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []

    for anchor in sorted(aggregates.values(), key=lambda a: (a.pottr_anchor_type, a.pottr_anchor_name.lower(), a.pottr_anchor_key)):
        if not anchor.concept_keys:
            records.append(
                {
                    "pottr_anchor_key": anchor.pottr_anchor_key,
                    "pottr_anchor_type": anchor.pottr_anchor_type,
                    "pottr_anchor_rxcui": anchor.pottr_anchor_rxcui,
                    "pottr_anchor_name": anchor.pottr_anchor_name,
                    "pottr_link_status": "NO_POTTR_CLASS_FOR_ANCHOR",
                    "pottr_concept_key": "",
                    "pottr_canonical_drug_name": "",
                    "pottr_class_assignment_key": "",
                    "direct_class_name": "",
                    "pottr_class_name": "",
                    "class_relation": "",
                    "class_depth_from_direct": None,
                    "class_path": "",
                    "class_in_hierarchy": None,
                }
            )
            continue

        for concept_key in sorted(anchor.concept_keys):
            concept = concepts_by_key[concept_key]
            assignments = list(assignments_by_concept.get(concept_key, []))

            if not assignments:
                records.append(
                    {
                        "pottr_anchor_key": anchor.pottr_anchor_key,
                        "pottr_anchor_type": anchor.pottr_anchor_type,
                        "pottr_anchor_rxcui": anchor.pottr_anchor_rxcui,
                        "pottr_anchor_name": anchor.pottr_anchor_name,
                        "pottr_link_status": "MATCHED_POTTR_CONCEPT_WITHOUT_CLASS",
                        "pottr_concept_key": concept_key,
                        "pottr_canonical_drug_name": concept.canonical_drug_name,
                        "pottr_class_assignment_key": "",
                        "direct_class_name": "",
                        "pottr_class_name": "",
                        "class_relation": "",
                        "class_depth_from_direct": None,
                        "class_path": "",
                        "class_in_hierarchy": None,
                    }
                )
                continue

            for assignment in assignments:
                records.append(
                    {
                        "pottr_anchor_key": anchor.pottr_anchor_key,
                        "pottr_anchor_type": anchor.pottr_anchor_type,
                        "pottr_anchor_rxcui": anchor.pottr_anchor_rxcui,
                        "pottr_anchor_name": anchor.pottr_anchor_name,
                        "pottr_link_status": "MATCHED_POTTR_CLASS",
                        "pottr_concept_key": concept_key,
                        "pottr_canonical_drug_name": concept.canonical_drug_name,
                        "pottr_class_assignment_key": assignment.assignment_key,
                        "direct_class_name": assignment.direct_class_name,
                        "pottr_class_name": assignment.pottr_class_name,
                        "class_relation": assignment.class_relation,
                        "class_depth_from_direct": assignment.class_depth_from_direct,
                        "class_path": assignment.class_path,
                        "class_in_hierarchy": assignment.class_in_hierarchy,
                    }
                )

    return records


def load_rxnorm_indexes(rxnorm_rrf_dir: Path) -> tuple[RxnConsoIndex, RxnRelIndex]:
    logger.info("Loading RxNorm indexes from %s", rxnorm_rrf_dir)
    return RxnConsoIndex.from_rrf_dir(rxnorm_rrf_dir), RxnRelIndex.from_rrf_dir(rxnorm_rrf_dir)


def load_pottr_mappings_to_postgres(
    database_url: str,
    pottr_raw_dir: Path,
    rxnorm_rrf_dir: Path,
    pottr_source_version: str,
) -> uuid.UUID:
    engine = create_engine(database_url)
    conso_index, rel_index = load_rxnorm_indexes(rxnorm_rrf_dir)

    pottr = build_pottr_records(pottr_raw_dir, conso_index, rel_index)
    pottr_by_rxcui, pottr_by_alias_exact, pottr_by_alias_equiv, concepts_by_key = build_pottr_indices(
        pottr.concepts,
        pottr.aliases,
    )
    assignments_by_concept = assignment_rows_by_concept(pottr.class_assignments)

    terms = fetch_rxnorm_drug_terms_for_pottr(engine)
    link_rows, anchor_aggregates = aggregate_input_anchors(
        terms=terms,
        conso_index=conso_index,
        pottr_by_rxcui=pottr_by_rxcui,
        pottr_by_alias_exact=pottr_by_alias_exact,
        pottr_by_alias_equiv=pottr_by_alias_equiv,
        concept_by_key=concepts_by_key,
    )
    mapping_rows = pottr_anchor_class_mapping_records(anchor_aggregates, concepts_by_key, assignments_by_concept)

    logger.info(
        "Built POTTR load: %d concepts, %d aliases, %d class assignments, %d input-anchor links, %d anchor-class rows",
        len(pottr.concepts),
        len(pottr.aliases),
        len(pottr.class_assignments),
        len(link_rows),
        len(mapping_rows),
    )

    load_batch_id = uuid.uuid4()
    source_file = json.dumps(
        {
            "pottr_raw_dir": str(pottr_raw_dir),
            "drug_database": str(pottr_raw_dir / "drug_database.txt"),
            "drug_class_hierarchy": str(pottr_raw_dir / "drug_class_hierarchy.txt"),
            "rxnorm_rrf_dir": str(rxnorm_rrf_dir),
        },
        ensure_ascii=False,
    )

    with engine.begin() as conn:
        create_load_batch(conn, load_batch_id, source_file=source_file, source_version=pottr_source_version)

        conn.execute(TRUNCATE_POTTR_TABLES_SQL)

        concept_rows = [
            {
                "pottr_concept_key": concept.concept_key,
                "concept_index": concept.concept_index,
                "drug_raw": concept.drug_raw,
                "canonical_drug_name": concept.canonical_drug_name,
                "aliases_raw": concept.aliases_raw,
                "direct_classes_raw": concept.direct_classes_raw,
            }
            for concept in pottr.concepts
        ]
        if concept_rows:
            conn.execute(INSERT_POTTR_CONCEPT_SQL, concept_rows)

        assignment_rows = [
            {
                "pottr_class_assignment_key": assignment.assignment_key,
                "pottr_concept_key": assignment.concept_key,
                "direct_class_name": assignment.direct_class_name,
                "pottr_class_name": assignment.pottr_class_name,
                "class_relation": assignment.class_relation,
                "class_depth_from_direct": assignment.class_depth_from_direct,
                "class_path": assignment.class_path,
                "class_in_hierarchy": assignment.class_in_hierarchy,
            }
            for assignment in pottr.class_assignments
        ]
        if assignment_rows:
            conn.execute(INSERT_POTTR_CLASS_ASSIGNMENT_SQL, assignment_rows)

        if link_rows:
            conn.execute(INSERT_INPUT_POTTR_ANCHOR_SQL, link_rows)

        if mapping_rows:
            conn.execute(INSERT_POTTR_ANCHOR_CLASS_MAPPING_SQL, mapping_rows)

        complete_load_batch(conn, load_batch_id, row_count=len(mapping_rows))

    logger.info("Loaded POTTR with load_batch_id=%s", load_batch_id)
    return load_batch_id


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Load POTTR classifications for simplified CTGov drug ontology outputs."
    )
    parser.add_argument(
        "--pottr_raw_dir",
        required=True,
        type=Path,
        help="Directory containing POTTR drug_database.txt and drug_class_hierarchy.txt.",
    )
    parser.add_argument(
        "--rxnorm_rrf_dir",
        required=True,
        type=Path,
        help="Directory containing RxNorm RRF files.",
    )
    parser.add_argument(
        "--pottr_source_version",
        required=True,
        help="POTTR source version label, usually basename of POTTR raw folder.",
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

    load_pottr_mappings_to_postgres(
        database_url=database_url,
        pottr_raw_dir=args.pottr_raw_dir,
        rxnorm_rrf_dir=args.rxnorm_rrf_dir,
        pottr_source_version=args.pottr_source_version,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
