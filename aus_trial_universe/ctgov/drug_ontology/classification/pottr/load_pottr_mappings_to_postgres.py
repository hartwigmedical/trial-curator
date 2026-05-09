from __future__ import annotations

"""Load POTTR classifications into PostgreSQL at anchor-level granularity.

Key design decision, learned from FDA linking:
- Materialize POTTR evidence by CTGov/POTTR anchor, not by CTGov alias/source-field row.
- Preserve CTGov term expansion through ctgov_pottr_anchor_drug_term and a view.
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

from aus_trial_universe.ctgov.drug_ontology.ctgov_to_rxnorm import (
    RxnConsoIndex,
    RxnRelIndex,
)
from aus_trial_universe.ctgov.drug_ontology.classification.pottr.rxnorm_to_pottr import (
    PottrDrugAlias,
    PottrDrugClassAssignment,
    PottrDrugConcept,
    PottrLinkAnchor,
    build_pottr_records,
    clean_text,
    derive_pottr_link_anchor_from_values,
    normalize_alias_key,
    stable_key,
    summarize_values,
)

logger = logging.getLogger(__name__)
SUMMARY_DELIMITER = " | "

FETCH_CT_GOV_DRUG_TERMS_SQL = text(
    """
    SELECT
        t.ctgov_drug_term_id,
        t.input_drug_name,
        t.input_drug_name_normalized,
        t.source_field,
        t.term_kind,
        m.match_status,
        m.rxnorm_rxcui,
        m.rxnorm_canonical_name,
        m.rxnorm_term_type,
        m.rxnorm_ingredient_rxcui,
        m.rxnorm_ingredient_name,
        m.rxnorm_ingredient_term_type,
        m.rxnorm_ingredient_resolution_stage,
        m.rxnorm_ingredient_path,
        m.manual_review_needed AS rxnorm_mapping_manual_review_needed,
        m.rxnorm_source_version
    FROM drug_identity.ctgov_drug_term t
    JOIN drug_identity.ctgov_drug_term_rxnorm_mapping m
      ON m.ctgov_drug_term_id = t.ctgov_drug_term_id
    WHERE m.rxnorm_source_version = :rxnorm_source_version
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
        'pottr_classification_resolution',
        :source_file,
        :source_version,
        'started'
    )
    """
)

COMPLETE_LOAD_BATCH_SQL = text(
    """
    UPDATE curation.load_batch
    SET completed_at = now(), row_count = :row_count, status = 'completed'
    WHERE load_batch_id = :load_batch_id
    """
)

DELETE_EXISTING_POTTR_CONCEPTS_SQL = text(
    """
    DELETE FROM drug_classification.pottr_drug_concept
    WHERE pottr_source_version = :pottr_source_version
    """
)

DELETE_EXISTING_CT_GOV_POTTR_ANCHORS_SQL = text(
    """
    DELETE FROM drug_classification.ctgov_pottr_anchor
    WHERE rxnorm_source_version = :rxnorm_source_version
      AND pottr_source_version = :pottr_source_version
    """
)

INSERT_POTTR_CONCEPT_SQL = text(
    """
    INSERT INTO drug_classification.pottr_drug_concept (
        load_batch_id,
        pottr_concept_key,
        concept_index,
        drug_raw,
        canonical_drug_name,
        aliases_raw,
        direct_classes_raw,
        pottr_source_version
    )
    VALUES (
        :load_batch_id,
        :pottr_concept_key,
        :concept_index,
        :drug_raw,
        :canonical_drug_name,
        :aliases_raw,
        :direct_classes_raw,
        :pottr_source_version
    )
    RETURNING pottr_drug_concept_id
    """
)

INSERT_POTTR_ALIAS_SQL = text(
    """
    INSERT INTO drug_classification.pottr_drug_alias (
        pottr_drug_concept_id,
        load_batch_id,
        alias_position,
        alias,
        alias_normalized,
        rxnorm_rxcui,
        rxnorm_canonical_name,
        rxnorm_term_type,
        rxnorm_match_stage,
        rxnorm_match_status,
        rxnorm_ingredient_rxcui,
        rxnorm_ingredient_name,
        rxnorm_ingredient_term_type,
        rxnorm_ingredient_resolution_stage,
        rxnorm_ingredient_path,
        pottr_link_anchor_rxcui,
        pottr_link_anchor_name,
        pottr_link_anchor_term_type,
        pottr_link_anchor_strategy,
        pottr_link_anchor_path,
        manual_review_needed,
        resolution_payload,
        rxnorm_source_version,
        pottr_source_version
    )
    VALUES (
        :pottr_drug_concept_id,
        :load_batch_id,
        :alias_position,
        :alias,
        :alias_normalized,
        :rxnorm_rxcui,
        :rxnorm_canonical_name,
        :rxnorm_term_type,
        :rxnorm_match_stage,
        :rxnorm_match_status,
        :rxnorm_ingredient_rxcui,
        :rxnorm_ingredient_name,
        :rxnorm_ingredient_term_type,
        :rxnorm_ingredient_resolution_stage,
        :rxnorm_ingredient_path,
        :pottr_link_anchor_rxcui,
        :pottr_link_anchor_name,
        :pottr_link_anchor_term_type,
        :pottr_link_anchor_strategy,
        :pottr_link_anchor_path,
        :manual_review_needed,
        CAST(:resolution_payload AS jsonb),
        :rxnorm_source_version,
        :pottr_source_version
    )
    """
)

INSERT_POTTR_CLASS_SQL = text(
    """
    INSERT INTO drug_classification.pottr_drug_class_assignment (
        pottr_drug_concept_id,
        load_batch_id,
        assignment_key,
        direct_class_name,
        pottr_class_name,
        class_relation,
        class_depth_from_direct,
        class_path,
        class_in_hierarchy,
        pottr_source_version
    )
    VALUES (
        :pottr_drug_concept_id,
        :load_batch_id,
        :assignment_key,
        :direct_class_name,
        :pottr_class_name,
        :class_relation,
        :class_depth_from_direct,
        :class_path,
        :class_in_hierarchy,
        :pottr_source_version
    )
    RETURNING pottr_drug_class_assignment_id
    """
)

INSERT_CT_GOV_POTTR_ANCHOR_SQL = text(
    """
    INSERT INTO drug_classification.ctgov_pottr_anchor (
        load_batch_id,
        anchor_key,
        anchor_type,
        anchor_rxcui,
        anchor_name,
        anchor_term_type,
        anchor_strategy,
        anchor_path,
        represented_ctgov_drug_term_count,
        represented_input_names_summary,
        manual_review_needed,
        rxnorm_source_version,
        pottr_source_version
    )
    VALUES (
        :load_batch_id,
        :anchor_key,
        :anchor_type,
        :anchor_rxcui,
        :anchor_name,
        :anchor_term_type,
        :anchor_strategy,
        :anchor_path,
        :represented_ctgov_drug_term_count,
        :represented_input_names_summary,
        :manual_review_needed,
        :rxnorm_source_version,
        :pottr_source_version
    )
    RETURNING ctgov_pottr_anchor_id
    """
)

INSERT_CT_GOV_ANCHOR_TERM_SQL = text(
    """
    INSERT INTO drug_classification.ctgov_pottr_anchor_drug_term (
        ctgov_pottr_anchor_id,
        ctgov_drug_term_id,
        input_drug_name,
        source_field,
        term_kind,
        anchor_selection_strategy,
        rxnorm_source_version,
        pottr_source_version
    )
    VALUES (
        :ctgov_pottr_anchor_id,
        :ctgov_drug_term_id,
        :input_drug_name,
        :source_field,
        :term_kind,
        :anchor_selection_strategy,
        :rxnorm_source_version,
        :pottr_source_version
    )
    """
)

INSERT_POTTR_ANCHOR_MAPPING_SQL = text(
    """
    INSERT INTO drug_classification.pottr_anchor_mapping (
        ctgov_pottr_anchor_id,
        pottr_drug_concept_id,
        pottr_drug_class_assignment_id,
        load_batch_id,
        link_status,
        match_strategy,
        direct_class_name,
        pottr_class_name,
        class_relation,
        class_depth_from_direct,
        class_path,
        class_in_hierarchy,
        resolution_payload,
        rxnorm_source_version,
        pottr_source_version
    )
    VALUES (
        :ctgov_pottr_anchor_id,
        :pottr_drug_concept_id,
        :pottr_drug_class_assignment_id,
        :load_batch_id,
        :link_status,
        :match_strategy,
        :direct_class_name,
        :pottr_class_name,
        :class_relation,
        :class_depth_from_direct,
        :class_path,
        :class_in_hierarchy,
        CAST(:resolution_payload AS jsonb),
        :rxnorm_source_version,
        :pottr_source_version
    )
    """
)


@dataclass(frozen=True)
class CtgovDrugTermForPottr:
    ctgov_drug_term_id: int
    input_drug_name: str
    input_drug_name_normalized: str
    source_field: str
    term_kind: str
    match_status: str
    rxnorm_rxcui: str
    rxnorm_canonical_name: str
    rxnorm_term_type: str
    rxnorm_ingredient_rxcui: str
    rxnorm_ingredient_name: str
    rxnorm_ingredient_term_type: str
    rxnorm_ingredient_resolution_stage: str
    rxnorm_ingredient_path: str
    rxnorm_mapping_manual_review_needed: bool
    rxnorm_source_version: str


@dataclass
class CtgovAnchorCandidate:
    anchor_key: str
    anchor_type: str
    anchor_rxcui: str
    anchor_name: str
    anchor_term_type: str
    anchor_strategy: str
    anchor_path: str
    manual_review_needed: bool
    concept_keys: set[str]
    anchor_selection_strategy: str


@dataclass
class CtgovAnchorAggregate:
    anchor_key: str
    anchor_type: str
    anchor_rxcui: str
    anchor_name: str
    anchor_term_type: str
    anchor_strategy: str
    anchor_path: str
    manual_review_needed: bool
    concept_keys: set[str]
    anchor_selection_strategies: Counter
    ctgov_terms: dict[int, CtgovDrugTermForPottr]


def fetch_ctgov_drug_terms_for_pottr(engine: Engine, rxnorm_source_version: str) -> list[CtgovDrugTermForPottr]:
    with engine.connect() as conn:
        rows = conn.execute(FETCH_CT_GOV_DRUG_TERMS_SQL, {"rxnorm_source_version": rxnorm_source_version}).mappings().all()
    return [
        CtgovDrugTermForPottr(
            ctgov_drug_term_id=int(row["ctgov_drug_term_id"]),
            input_drug_name=clean_text(row["input_drug_name"]),
            input_drug_name_normalized=clean_text(row["input_drug_name_normalized"]),
            source_field=clean_text(row["source_field"]),
            term_kind=clean_text(row["term_kind"]),
            match_status=clean_text(row["match_status"]),
            rxnorm_rxcui=clean_text(row["rxnorm_rxcui"]),
            rxnorm_canonical_name=clean_text(row["rxnorm_canonical_name"]),
            rxnorm_term_type=clean_text(row["rxnorm_term_type"]),
            rxnorm_ingredient_rxcui=clean_text(row["rxnorm_ingredient_rxcui"]),
            rxnorm_ingredient_name=clean_text(row["rxnorm_ingredient_name"]),
            rxnorm_ingredient_term_type=clean_text(row["rxnorm_ingredient_term_type"]),
            rxnorm_ingredient_resolution_stage=clean_text(row["rxnorm_ingredient_resolution_stage"]),
            rxnorm_ingredient_path=clean_text(row["rxnorm_ingredient_path"]),
            rxnorm_mapping_manual_review_needed=bool(row["rxnorm_mapping_manual_review_needed"]),
            rxnorm_source_version=clean_text(row["rxnorm_source_version"]),
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
    conn.execute(COMPLETE_LOAD_BATCH_SQL, {"load_batch_id": str(load_batch_id), "row_count": row_count})


def load_rxnorm_indexes(rxnorm_rrf_dir: Path) -> tuple[RxnConsoIndex, RxnRelIndex]:
    logger.info("Loading RxNorm indexes from %s", rxnorm_rrf_dir)
    conso_index = RxnConsoIndex.from_rrf_dir(rxnorm_rrf_dir)
    rel_index = RxnRelIndex.from_rrf_dir(rxnorm_rrf_dir)
    return conso_index, rel_index


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


def is_safe_radionuclide_stripped_core(tokens: Sequence[str]) -> bool:
    """Return True only for human-readable drug cores, not code-like radioligand IDs.

    This prevents cross-isotope/code contamination such as:
      [111In]In-FL-020 -> 225Ac-FL-020
      [225Ac]Ac-PSMA-617 -> Lu-177 vipivotide tetraxetan
    while still allowing:
      (177Lu) vipivotide tetraxetan -> vipivotide tetraxetan
    """
    if len(tokens) < 2:
        return False
    if any(any(ch.isdigit() for ch in token) for token in tokens):
        return False
    alpha_long = [token for token in tokens if token.isalpha() and len(token) >= 4]
    return len(alpha_long) >= 2


def alias_equivalence_keys(value: object) -> set[str]:
    """Conservative alias-equivalence keys for direct-alias fallback only.

    This deliberately does not feed the broad RxNorm-anchor index. It lets
    punctuation/parenthetical variants match, and it strips radionuclide prefixes
    only when the remaining core is a human-readable drug name, not a code-like
    radioligand identifier.
    """
    base = normalize_alias_key(value)
    keys: set[str] = set()
    if base:
        keys.add(base)

    # Keep the leading named alias before explanatory parentheticals, e.g.
    # "FOLFOX (folinic acid + fluorouracil + oxaliplatin)" -> "folfox".
    before_paren = clean_text(base.split("(", 1)[0]) if "(" in base else ""
    if before_paren:
        keys.add(before_paren)

    no_parens = clean_text(re.sub(r"\([^)]*\)", " ", base))
    if no_parens and no_parens != base:
        keys.add(normalize_alias_key(no_parens))

    def token_key(text: str) -> str:
        tokens = [tok for tok in re.findall(r"[a-z0-9]+", normalize_alias_key(text)) if tok != "and"]
        return " ".join(tokens)

    # Punctuation equivalence: nivolumab-relatlimab <-> nivolumab and relatlimab.
    tokenized = token_key(base)
    if tokenized:
        keys.add(tokenized)

    tokens = [tok for tok in re.findall(r"[a-z0-9]+", normalize_alias_key(no_parens or base)) if tok != "and"]
    # Drop leading radionuclide/isotope tokens only for readable drug cores.
    # Do NOT strip to code-like cores such as psma 617, fl 020, or psma i t.
    stripped_tokens = list(tokens)
    while len(stripped_tokens) > 2 and stripped_tokens[0] in RADIONUCLIDE_PREFIX_TOKENS:
        stripped_tokens = stripped_tokens[1:]
        if is_safe_radionuclide_stripped_core(stripped_tokens):
            stripped = " ".join(stripped_tokens)
            if stripped:
                keys.add(stripped)

    return {key for key in keys if key}

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
        # A transformed CTGov key may exactly equal a POTTR alias key.
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
    text_value = concept_text_for_policy(concept)
    return bool(COMBO_OR_REGIMEN_RE.search(text_value))


def concept_is_specific_product(concept: PottrDrugConcept) -> bool:
    text_value = concept_text_for_policy(concept)
    return any(marker in text_value for marker in SPECIFIC_PRODUCT_MARKERS)


def build_pottr_indices(
    concepts: Sequence[PottrDrugConcept],
    aliases: Sequence[PottrDrugAlias],
) -> tuple[dict[str, set[str]], dict[str, set[str]], dict[str, set[str]], dict[str, PottrDrugConcept]]:
    """Build POTTR lookup indices.

    Important guardrail:
    - all aliases remain available for exact direct-alias lookup;
    - RxNorm-anchor lookup deliberately excludes regimen/combination concepts so
      single ingredients do not inherit classes such as FOLFOX/R-CHOP/CAPOX;
    - if a POTTR concept has precise ADC/radioligand/conjugate anchors, index
      only those precise anchors rather than broad parent-antibody brand anchors.
    """
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

        # Do not let component aliases on regimen/combination concepts contaminate
        # single-agent RxNorm anchors. Exact aliases can still match these concepts.
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


def derive_ctgov_rxnorm_anchor(term: CtgovDrugTermForPottr, conso_index: RxnConsoIndex) -> PottrLinkAnchor | None:
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


def direct_alias_anchor(term: CtgovDrugTermForPottr, alias_concepts: set[str]) -> CtgovAnchorCandidate:
    alias_key = normalize_alias_key(term.input_drug_name)
    return CtgovAnchorCandidate(
        anchor_key=f"ALIAS:{alias_key}",
        anchor_type="DIRECT_ALIAS",
        anchor_rxcui="",
        anchor_name=term.input_drug_name,
        anchor_term_type="",
        anchor_strategy="DIRECT_ALIAS_NORMALIZED",
        anchor_path=alias_key,
        manual_review_needed=False,
        concept_keys=set(alias_concepts),
        anchor_selection_strategy="DIRECT_ALIAS_EXACT" if alias_concepts else "DIRECT_ALIAS_NO_MATCH",
    )


def rxnorm_anchor_candidate(anchor: PottrLinkAnchor, concepts: set[str]) -> CtgovAnchorCandidate:
    return CtgovAnchorCandidate(
        anchor_key=f"RXCUI:{anchor.link_anchor_rxcui}",
        anchor_type="RXNORM",
        anchor_rxcui=anchor.link_anchor_rxcui,
        anchor_name=anchor.link_anchor_name,
        anchor_term_type=anchor.link_anchor_term_type,
        anchor_strategy=anchor.link_anchor_strategy,
        anchor_path=anchor.link_anchor_path,
        manual_review_needed=anchor.link_anchor_manual_review_needed,
        concept_keys=set(concepts),
        anchor_selection_strategy="RXNORM_ANCHOR" if concepts else "RXNORM_ANCHOR_NO_MATCH",
    )


def exact_alias_should_override_rxnorm(
    term: CtgovDrugTermForPottr,
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
    # A generic ingredient term such as "capecitabine" should stay on the RxNorm
    # ingredient anchor even if a regimen concept also lists it as a component.
    if input_norm and input_norm in {value for value in broad_anchor_norms if value}:
        return False

    # Exact CTGov aliases such as CAPOX, FOLFOX, R-CHOP, Kadcyla, Enhertu, or
    # Opdualag should be allowed to land on their exact POTTR concept instead of
    # being forced through a broad component ingredient anchor.
    for concept_key in alias_concepts - rx_concepts:
        concept = concept_by_key.get(concept_key)
        if concept is None:
            continue
        if concept_is_combo_or_regimen(concept) or concept_is_specific_product(concept):
            return True
    return False


def choose_anchor_for_term(
    term: CtgovDrugTermForPottr,
    conso_index: RxnConsoIndex,
    pottr_by_rxcui: Mapping[str, set[str]],
    pottr_by_alias_exact: Mapping[str, set[str]],
    pottr_by_alias_equiv: Mapping[str, set[str]],
    concept_by_key: Mapping[str, PottrDrugConcept],
) -> CtgovAnchorCandidate:
    alias_concepts, alias_match_strategy = lookup_alias_concepts(
        term.input_drug_name,
        pottr_by_alias_exact,
        pottr_by_alias_equiv,
    )
    rx_anchor = derive_ctgov_rxnorm_anchor(term, conso_index)

    if rx_anchor and rx_anchor.link_anchor_rxcui:
        rx_concepts = set(pottr_by_rxcui.get(rx_anchor.link_anchor_rxcui, set()))
        if rx_concepts:
            if exact_alias_should_override_rxnorm(term, rx_anchor, rx_concepts, alias_concepts, concept_by_key):
                candidate = direct_alias_anchor(term, alias_concepts)
                candidate.anchor_selection_strategy = "DIRECT_ALIAS_EXACT_OVERRIDE_BROAD_RXNORM"
                return candidate
            return rxnorm_anchor_candidate(rx_anchor, rx_concepts)
        if alias_concepts:
            candidate = direct_alias_anchor(term, alias_concepts)
            candidate.anchor_selection_strategy = f"{alias_match_strategy}_FALLBACK_AFTER_RXNORM_NO_MATCH"
            return candidate
        return rxnorm_anchor_candidate(rx_anchor, set())

    candidate = direct_alias_anchor(term, alias_concepts)
    candidate.anchor_selection_strategy = alias_match_strategy
    return candidate


def aggregate_ctgov_anchors(
    terms: Sequence[CtgovDrugTermForPottr],
    conso_index: RxnConsoIndex,
    pottr_by_rxcui: Mapping[str, set[str]],
    pottr_by_alias_exact: Mapping[str, set[str]],
    pottr_by_alias_equiv: Mapping[str, set[str]],
    concept_by_key: Mapping[str, PottrDrugConcept],
) -> dict[str, CtgovAnchorAggregate]:
    aggregates: dict[str, CtgovAnchorAggregate] = {}
    for term in terms:
        candidate = choose_anchor_for_term(
            term,
            conso_index,
            pottr_by_rxcui,
            pottr_by_alias_exact,
            pottr_by_alias_equiv,
            concept_by_key,
        )
        agg = aggregates.get(candidate.anchor_key)
        if agg is None:
            agg = CtgovAnchorAggregate(
                anchor_key=candidate.anchor_key,
                anchor_type=candidate.anchor_type,
                anchor_rxcui=candidate.anchor_rxcui,
                anchor_name=candidate.anchor_name,
                anchor_term_type=candidate.anchor_term_type,
                anchor_strategy=candidate.anchor_strategy,
                anchor_path=candidate.anchor_path,
                manual_review_needed=candidate.manual_review_needed,
                concept_keys=set(candidate.concept_keys),
                anchor_selection_strategies=Counter(),
                ctgov_terms={},
            )
            aggregates[candidate.anchor_key] = agg
        agg.concept_keys.update(candidate.concept_keys)
        agg.anchor_selection_strategies[candidate.anchor_selection_strategy] += 1
        agg.ctgov_terms[term.ctgov_drug_term_id] = term
    return aggregates


def assignment_rows_by_concept(
    assignments: Sequence[PottrDrugClassAssignment],
) -> dict[str, list[PottrDrugClassAssignment]]:
    out: dict[str, list[PottrDrugClassAssignment]] = defaultdict(list)
    for assignment in assignments:
        out[assignment.concept_key].append(assignment)
    for rows in out.values():
        rows.sort(key=lambda x: (x.direct_class_name, x.class_depth_from_direct, x.pottr_class_name, x.assignment_key))
    return out


def payload_for_mapping(anchor: CtgovAnchorAggregate, concept: PottrDrugConcept | None, assignment: PottrDrugClassAssignment | None) -> str:
    return json.dumps(
        {
            "ctgov_pottr_anchor": {
                "anchor_key": anchor.anchor_key,
                "anchor_type": anchor.anchor_type,
                "anchor_rxcui": anchor.anchor_rxcui,
                "anchor_name": anchor.anchor_name,
                "anchor_strategy": anchor.anchor_strategy,
                "anchor_selection_strategies": dict(anchor.anchor_selection_strategies),
                "represented_ctgov_drug_term_count": len(anchor.ctgov_terms),
            },
            "pottr_concept": asdict(concept) if concept else None,
            "pottr_class_assignment": asdict(assignment) if assignment else None,
        },
        ensure_ascii=False,
    )


def build_anchor_check_rows(
    anchors: Mapping[str, CtgovAnchorAggregate],
    concepts_by_key: Mapping[str, PottrDrugConcept],
    assignments_by_concept: Mapping[str, Sequence[PottrDrugClassAssignment]],
    mapping_row_counts: Mapping[str, int],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for anchor in sorted(anchors.values(), key=lambda a: (a.anchor_type, a.anchor_name.lower(), a.anchor_key)):
        concept_keys = sorted(anchor.concept_keys)
        concept_rows = [concepts_by_key[key] for key in concept_keys if key in concepts_by_key]
        direct_classes = []
        expanded_classes = []
        for concept_key in concept_keys:
            for assignment in assignments_by_concept.get(concept_key, []):
                if assignment.class_relation == "DIRECT":
                    direct_classes.append(assignment.direct_class_name)
                expanded_classes.append(assignment.pottr_class_name)
        link_status = "MATCHED_POTTR_CLASS" if concept_keys else "NO_POTTR_CLASS_FOR_ANCHOR"
        rows.append(
            {
                "anchor_key": anchor.anchor_key,
                "anchor_type": anchor.anchor_type,
                "anchor_rxcui": anchor.anchor_rxcui,
                "anchor_name": anchor.anchor_name,
                "anchor_term_type": anchor.anchor_term_type,
                "anchor_strategy": anchor.anchor_strategy,
                "anchor_path": anchor.anchor_path,
                "manual_review_needed": anchor.manual_review_needed or len(concept_keys) > 1,
                "link_status": link_status,
                "represented_ctgov_drug_term_count": len(anchor.ctgov_terms),
                "represented_input_names_summary": summarize_values(term.input_drug_name for term in anchor.ctgov_terms.values()),
                "anchor_selection_strategy_distribution": json.dumps(dict(anchor.anchor_selection_strategies), ensure_ascii=False, sort_keys=True),
                "pottr_concept_count": len(concept_keys),
                "pottr_concepts_summary": summarize_values(c.canonical_drug_name for c in concept_rows),
                "pottr_direct_class_count": len(set(direct_classes)),
                "pottr_direct_classes_summary": summarize_values(direct_classes),
                "pottr_expanded_class_count": len(set(expanded_classes)),
                "pottr_expanded_classes_summary": summarize_values(expanded_classes),
                "mapping_row_count": mapping_row_counts.get(anchor.anchor_key, 0),
            }
        )
    return rows


def write_check_tsv(rows: Iterable[Mapping[str, object]], output_tsv: Path) -> None:
    rows = list(rows)
    output_tsv.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        output_tsv.write_text("", encoding="utf-8")
        return
    with output_tsv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def load_pottr_mappings_to_postgres(
    database_url: str,
    pottr_raw_dir: Path,
    rxnorm_rrf_dir: Path,
    rxnorm_source_version: str,
    pottr_source_version: str,
    output_check_tsv: Path | None,
    output_detail_tsv: Path | None = None,
) -> uuid.UUID:
    engine = create_engine(database_url)
    conso_index, rel_index = load_rxnorm_indexes(rxnorm_rrf_dir)

    pottr = build_pottr_records(pottr_raw_dir, conso_index, rel_index)
    pottr_by_rxcui, pottr_by_alias_exact, pottr_by_alias_equiv, concepts_by_key = build_pottr_indices(
        pottr.concepts,
        pottr.aliases,
    )
    logger.info(
        "Built POTTR indices with %d RxNorm anchors, %d exact normalized aliases, and %d equivalence alias keys",
        len(pottr_by_rxcui),
        len(pottr_by_alias_exact),
        len(pottr_by_alias_equiv),
    )

    ctgov_terms = fetch_ctgov_drug_terms_for_pottr(engine, rxnorm_source_version)
    anchors = aggregate_ctgov_anchors(
        ctgov_terms,
        conso_index,
        pottr_by_rxcui,
        pottr_by_alias_exact,
        pottr_by_alias_equiv,
        concepts_by_key,
    )
    logger.info("Fetched %d CTGov terms and collapsed them to %d unique POTTR anchors", len(ctgov_terms), len(anchors))

    assignments_by_key = {assignment.assignment_key: assignment for assignment in pottr.class_assignments}
    assignments_by_concept = assignment_rows_by_concept(pottr.class_assignments)

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

    mapping_row_counts: Counter[str] = Counter()
    detail_rows: list[dict[str, object]] = []

    with engine.begin() as conn:
        create_load_batch(conn, load_batch_id, source_file=source_file, source_version=pottr_source_version)
        conn.execute(
            DELETE_EXISTING_CT_GOV_POTTR_ANCHORS_SQL,
            {"rxnorm_source_version": rxnorm_source_version, "pottr_source_version": pottr_source_version},
        )
        conn.execute(
            DELETE_EXISTING_POTTR_CONCEPTS_SQL,
            {"pottr_source_version": pottr_source_version},
        )

        concept_id_by_key: dict[str, int] = {}
        for concept in pottr.concepts:
            concept_id = conn.execute(
                INSERT_POTTR_CONCEPT_SQL,
                {
                    "load_batch_id": str(load_batch_id),
                    "pottr_concept_key": concept.concept_key,
                    "concept_index": concept.concept_index,
                    "drug_raw": concept.drug_raw,
                    "canonical_drug_name": concept.canonical_drug_name,
                    "aliases_raw": concept.aliases_raw,
                    "direct_classes_raw": concept.direct_classes_raw,
                    "pottr_source_version": pottr_source_version,
                },
            ).scalar_one()
            concept_id_by_key[concept.concept_key] = int(concept_id)

        alias_records = []
        for alias in pottr.aliases:
            alias_records.append(
                {
                    **asdict(alias),
                    "pottr_drug_concept_id": concept_id_by_key[alias.concept_key],
                    "load_batch_id": str(load_batch_id),
                    "rxnorm_source_version": rxnorm_source_version,
                    "pottr_source_version": pottr_source_version,
                }
            )
        if alias_records:
            conn.execute(INSERT_POTTR_ALIAS_SQL, alias_records)

        assignment_id_by_key: dict[str, int] = {}
        for assignment in pottr.class_assignments:
            assignment_id = conn.execute(
                INSERT_POTTR_CLASS_SQL,
                {
                    **asdict(assignment),
                    "pottr_drug_concept_id": concept_id_by_key[assignment.concept_key],
                    "load_batch_id": str(load_batch_id),
                    "pottr_source_version": pottr_source_version,
                },
            ).scalar_one()
            assignment_id_by_key[assignment.assignment_key] = int(assignment_id)

        anchor_id_by_key: dict[str, int] = {}
        for anchor in anchors.values():
            anchor_id = conn.execute(
                INSERT_CT_GOV_POTTR_ANCHOR_SQL,
                {
                    "load_batch_id": str(load_batch_id),
                    "anchor_key": anchor.anchor_key,
                    "anchor_type": anchor.anchor_type,
                    "anchor_rxcui": anchor.anchor_rxcui or None,
                    "anchor_name": anchor.anchor_name,
                    "anchor_term_type": anchor.anchor_term_type or None,
                    "anchor_strategy": anchor.anchor_strategy,
                    "anchor_path": anchor.anchor_path or None,
                    "represented_ctgov_drug_term_count": len(anchor.ctgov_terms),
                    "represented_input_names_summary": summarize_values(term.input_drug_name for term in anchor.ctgov_terms.values()),
                    "manual_review_needed": anchor.manual_review_needed or len(anchor.concept_keys) > 1,
                    "rxnorm_source_version": rxnorm_source_version,
                    "pottr_source_version": pottr_source_version,
                },
            ).scalar_one()
            anchor_id_by_key[anchor.anchor_key] = int(anchor_id)

        anchor_term_records = []
        for anchor in anchors.values():
            selection_strategy = ";".join(f"{k}={v}" for k, v in sorted(anchor.anchor_selection_strategies.items()))
            for term in anchor.ctgov_terms.values():
                anchor_term_records.append(
                    {
                        "ctgov_pottr_anchor_id": anchor_id_by_key[anchor.anchor_key],
                        "ctgov_drug_term_id": term.ctgov_drug_term_id,
                        "input_drug_name": term.input_drug_name,
                        "source_field": term.source_field,
                        "term_kind": term.term_kind,
                        "anchor_selection_strategy": selection_strategy,
                        "rxnorm_source_version": rxnorm_source_version,
                        "pottr_source_version": pottr_source_version,
                    }
                )
        if anchor_term_records:
            conn.execute(INSERT_CT_GOV_ANCHOR_TERM_SQL, anchor_term_records)

        mapping_records = []
        for anchor in anchors.values():
            anchor_id = anchor_id_by_key[anchor.anchor_key]
            if not anchor.concept_keys:
                mapping_records.append(
                    {
                        "ctgov_pottr_anchor_id": anchor_id,
                        "pottr_drug_concept_id": None,
                        "pottr_drug_class_assignment_id": None,
                        "load_batch_id": str(load_batch_id),
                        "link_status": "NO_POTTR_CLASS_FOR_ANCHOR",
                        "match_strategy": "NO_MATCH",
                        "direct_class_name": None,
                        "pottr_class_name": None,
                        "class_relation": None,
                        "class_depth_from_direct": None,
                        "class_path": None,
                        "class_in_hierarchy": None,
                        "resolution_payload": payload_for_mapping(anchor, None, None),
                        "rxnorm_source_version": rxnorm_source_version,
                        "pottr_source_version": pottr_source_version,
                    }
                )
                mapping_row_counts[anchor.anchor_key] += 1
                continue

            for concept_key in sorted(anchor.concept_keys):
                concept = concepts_by_key[concept_key]
                assignments = assignments_by_concept.get(concept_key, [])
                if not assignments:
                    mapping_records.append(
                        {
                            "ctgov_pottr_anchor_id": anchor_id,
                            "pottr_drug_concept_id": concept_id_by_key[concept_key],
                            "pottr_drug_class_assignment_id": None,
                            "load_batch_id": str(load_batch_id),
                            "link_status": "MATCHED_POTTR_CONCEPT_WITHOUT_CLASS",
                            "match_strategy": "RXNORM_OR_ALIAS_ANCHOR",
                            "direct_class_name": None,
                            "pottr_class_name": None,
                            "class_relation": None,
                            "class_depth_from_direct": None,
                            "class_path": None,
                            "class_in_hierarchy": None,
                            "resolution_payload": payload_for_mapping(anchor, concept, None),
                            "rxnorm_source_version": rxnorm_source_version,
                            "pottr_source_version": pottr_source_version,
                        }
                    )
                    mapping_row_counts[anchor.anchor_key] += 1
                    continue

                for assignment in assignments:
                    mapping_records.append(
                        {
                            "ctgov_pottr_anchor_id": anchor_id,
                            "pottr_drug_concept_id": concept_id_by_key[concept_key],
                            "pottr_drug_class_assignment_id": assignment_id_by_key[assignment.assignment_key],
                            "load_batch_id": str(load_batch_id),
                            "link_status": "MATCHED_POTTR_CLASS",
                            "match_strategy": "RXNORM_OR_ALIAS_ANCHOR",
                            "direct_class_name": assignment.direct_class_name,
                            "pottr_class_name": assignment.pottr_class_name,
                            "class_relation": assignment.class_relation,
                            "class_depth_from_direct": assignment.class_depth_from_direct,
                            "class_path": assignment.class_path,
                            "class_in_hierarchy": assignment.class_in_hierarchy,
                            "resolution_payload": payload_for_mapping(anchor, concept, assignment),
                            "rxnorm_source_version": rxnorm_source_version,
                            "pottr_source_version": pottr_source_version,
                        }
                    )
                    mapping_row_counts[anchor.anchor_key] += 1

                    if output_detail_tsv is not None:
                        detail_rows.append(
                            {
                                "anchor_key": anchor.anchor_key,
                                "anchor_type": anchor.anchor_type,
                                "anchor_rxcui": anchor.anchor_rxcui,
                                "anchor_name": anchor.anchor_name,
                                "pottr_canonical_drug_name": concept.canonical_drug_name,
                                "pottr_aliases_raw": concept.aliases_raw,
                                "direct_class_name": assignment.direct_class_name,
                                "pottr_class_name": assignment.pottr_class_name,
                                "class_relation": assignment.class_relation,
                                "class_depth_from_direct": assignment.class_depth_from_direct,
                                "class_path": assignment.class_path,
                            }
                        )

        if mapping_records:
            conn.execute(INSERT_POTTR_ANCHOR_MAPPING_SQL, mapping_records)

        complete_load_batch(conn, load_batch_id, row_count=len(mapping_records))

    check_rows = build_anchor_check_rows(anchors, concepts_by_key, assignments_by_concept, mapping_row_counts)
    if output_check_tsv is not None:
        write_check_tsv(check_rows, output_check_tsv)
        logger.info("Wrote POTTR compact check TSV to %s", output_check_tsv)
    if output_detail_tsv is not None:
        write_check_tsv(detail_rows, output_detail_tsv)
        logger.info("Wrote POTTR detail TSV to %s", output_detail_tsv)

    logger.info(
        "Loaded POTTR: %d concepts, %d aliases, %d class assignments, %d CTGov anchors, %d mapping rows; load_batch_id=%s",
        len(pottr.concepts),
        len(pottr.aliases),
        len(pottr.class_assignments),
        len(anchors),
        sum(mapping_row_counts.values()),
        load_batch_id,
    )
    return load_batch_id


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Load POTTR classifications for CTGov/RxNorm drug anchors into PostgreSQL.")
    parser.add_argument("--pottr_raw_dir", required=True, type=Path, help="Directory containing POTTR drug_database.txt and drug_class_hierarchy.txt.")
    parser.add_argument("--rxnorm_rrf_dir", required=True, type=Path, help="Directory containing RxNorm RRF files.")
    parser.add_argument("--rxnorm_source_version", required=True, help="RxNorm source version to select from Part 2 mappings.")
    parser.add_argument("--pottr_source_version", required=True, help="POTTR source version label, usually basename of POTTR raw folder.")
    parser.add_argument("--output_check_tsv", type=Path, default=None, help="Optional compact TSV, one row per CTGov/POTTR anchor.")
    parser.add_argument("--output_detail_tsv", type=Path, default=None, help="Optional detail TSV with anchor-concept-class rows.")
    parser.add_argument("--env_file", type=Path, default=Path(".env"), help="Path to .env containing DATABASE_URL.")
    parser.add_argument("--log_level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
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
        rxnorm_source_version=args.rxnorm_source_version,
        pottr_source_version=args.pottr_source_version,
        output_check_tsv=args.output_check_tsv,
        output_detail_tsv=args.output_detail_tsv,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
