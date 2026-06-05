from __future__ import annotations

"""Load ChEMBL molecule-level evidence for simplified CTGov drug ontology outputs.

Input tables:
    drug_identity.ctgov_drug_term_rxnorm_mapping
    drug_identity.ctgov_intervention_drug_term_link

Essential outputs are SQL-derived only:
    dbdump_ctgov_intervention_chembl_link.tsv
    dbdump_chembl_intervention_evidence_review.tsv

No Python-side TSVs are written.
"""

import argparse
import json
import logging
import os
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Sequence

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

from aus_trial_universe.ctgov.drug_ontology.identity.rxnorm.matcher import RxnConsoIndex
from aus_trial_universe.ctgov.drug_ontology.sources.chembl.molecules import (
    ChemblAtc,
    ChemblBuildResult,
    ChemblIndication,
    ChemblLinkAnchor,
    ChemblMechanism,
    ChemblMolecule,
    ChemblMoleculeRelation,
    ChemblNameMatch,
    ChemblWarning,
    build_chembl_records,
    clean_text,
    derive_chembl_link_anchor_from_values,
    normalize_match_key,
    parse_int,
    summarize_values,
)

logger = logging.getLogger(__name__)
SUMMARY_DELIMITER = " | "


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
        'chembl_molecule_resolution',
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

TRUNCATE_CHEMBL_TABLES_SQL = text(
    """
    TRUNCATE TABLE
        drug_classification.input_drug_name_chembl_anchor,
        drug_classification.chembl_anchor_molecule_mapping,
        drug_classification.chembl_molecule_warning,
        drug_classification.chembl_molecule_atc,
        drug_classification.chembl_molecule_indication,
        drug_classification.chembl_molecule_mechanism,
        drug_classification.chembl_molecule_relation,
        drug_classification.chembl_molecule_alias,
        drug_classification.chembl_molecule
    """
)

INSERT_CHEMBL_MOLECULE_SQL = text(
    """
    INSERT INTO drug_classification.chembl_molecule (
        load_batch_id,
        molregno,
        chembl_id,
        pref_name,
        max_phase,
        therapeutic_flag,
        dosed_ingredient,
        structure_type,
        molecule_type,
        first_approval,
        oral,
        parenteral,
        topical,
        black_box_warning,
        first_in_class,
        prodrug,
        withdrawn_flag,
        chemical_probe,
        orphan,
        standard_inchi_key,
        canonical_smiles,
        full_mwt,
        full_molformula,
        chembl_source_version
    )
    VALUES (
        :load_batch_id,
        :molregno,
        :chembl_id,
        :pref_name,
        :max_phase,
        :therapeutic_flag,
        :dosed_ingredient,
        :structure_type,
        :molecule_type,
        :first_approval,
        :oral,
        :parenteral,
        :topical,
        :black_box_warning,
        :first_in_class,
        :prodrug,
        :withdrawn_flag,
        :chemical_probe,
        :orphan,
        :standard_inchi_key,
        :canonical_smiles,
        :full_mwt,
        :full_molformula,
        :chembl_source_version
    )
    RETURNING chembl_molecule_id
    """
)

INSERT_CHEMBL_ALIAS_SQL = text(
    """
    INSERT INTO drug_classification.chembl_molecule_alias (
        chembl_molecule_id,
        load_batch_id,
        molregno,
        synonym,
        synonym_normalized,
        syn_type,
        chembl_source_version
    )
    VALUES (
        :chembl_molecule_id,
        :load_batch_id,
        :molregno,
        :synonym,
        :synonym_normalized,
        :syn_type,
        :chembl_source_version
    )
    """
)

INSERT_CHEMBL_RELATION_SQL = text(
    """
    INSERT INTO drug_classification.chembl_molecule_relation (
        chembl_molecule_id,
        related_chembl_molecule_id,
        load_batch_id,
        molregno,
        related_molregno,
        relation_type,
        chembl_source_version
    )
    VALUES (
        :chembl_molecule_id,
        :related_chembl_molecule_id,
        :load_batch_id,
        :molregno,
        :related_molregno,
        :relation_type,
        :chembl_source_version
    )
    """
)

INSERT_CHEMBL_MECHANISM_SQL = text(
    """
    INSERT INTO drug_classification.chembl_molecule_mechanism (
        chembl_molecule_id,
        load_batch_id,
        molregno,
        mec_id,
        record_id,
        mechanism_of_action,
        action_type,
        direct_interaction,
        molecular_mechanism,
        disease_efficacy,
        mechanism_comment,
        selectivity_comment,
        binding_site_comment,
        target_chembl_id,
        target_pref_name,
        target_type,
        target_organism,
        target_accessions,
        target_component_descriptions,
        chembl_source_version
    )
    VALUES (
        :chembl_molecule_id,
        :load_batch_id,
        :molregno,
        :mec_id,
        :record_id,
        :mechanism_of_action,
        :action_type,
        :direct_interaction,
        :molecular_mechanism,
        :disease_efficacy,
        :mechanism_comment,
        :selectivity_comment,
        :binding_site_comment,
        :target_chembl_id,
        :target_pref_name,
        :target_type,
        :target_organism,
        :target_accessions,
        :target_component_descriptions,
        :chembl_source_version
    )
    """
)

INSERT_CHEMBL_INDICATION_SQL = text(
    """
    INSERT INTO drug_classification.chembl_molecule_indication (
        chembl_molecule_id,
        load_batch_id,
        molregno,
        drugind_id,
        record_id,
        max_phase_for_ind,
        mesh_id,
        mesh_heading,
        efo_id,
        efo_term,
        is_oncology,
        chembl_source_version
    )
    VALUES (
        :chembl_molecule_id,
        :load_batch_id,
        :molregno,
        :drugind_id,
        :record_id,
        :max_phase_for_ind,
        :mesh_id,
        :mesh_heading,
        :efo_id,
        :efo_term,
        :is_oncology,
        :chembl_source_version
    )
    """
)

INSERT_CHEMBL_ATC_SQL = text(
    """
    INSERT INTO drug_classification.chembl_molecule_atc (
        chembl_molecule_id,
        load_batch_id,
        molregno,
        mol_atc_id,
        level5,
        who_name,
        level1,
        level2,
        level3,
        level4,
        level1_description,
        level2_description,
        level3_description,
        level4_description,
        chembl_source_version
    )
    VALUES (
        :chembl_molecule_id,
        :load_batch_id,
        :molregno,
        :mol_atc_id,
        :level5,
        :who_name,
        :level1,
        :level2,
        :level3,
        :level4,
        :level1_description,
        :level2_description,
        :level3_description,
        :level4_description,
        :chembl_source_version
    )
    """
)

INSERT_CHEMBL_WARNING_SQL = text(
    """
    INSERT INTO drug_classification.chembl_molecule_warning (
        chembl_molecule_id,
        load_batch_id,
        molregno,
        warning_id,
        record_id,
        warning_type,
        warning_class,
        warning_description,
        warning_country,
        warning_year,
        efo_term,
        efo_id,
        efo_id_for_warning_class,
        chembl_source_version
    )
    VALUES (
        :chembl_molecule_id,
        :load_batch_id,
        :molregno,
        :warning_id,
        :record_id,
        :warning_type,
        :warning_class,
        :warning_description,
        :warning_country,
        :warning_year,
        :efo_term,
        :efo_id,
        :efo_id_for_warning_class,
        :chembl_source_version
    )
    """
)

INSERT_INPUT_CHEMBL_ANCHOR_SQL = text(
    """
    INSERT INTO drug_classification.input_drug_name_chembl_anchor (
        input_drug_name,
        chembl_anchor_key
    )
    VALUES (
        :input_drug_name,
        :chembl_anchor_key
    )
    ON CONFLICT (input_drug_name, chembl_anchor_key)
    DO NOTHING
    """
)

INSERT_CHEMBL_ANCHOR_MAPPING_SQL = text(
    """
    INSERT INTO drug_classification.chembl_anchor_molecule_mapping (
        chembl_anchor_key,
        chembl_anchor_type,
        chembl_anchor_rxcui,
        chembl_anchor_name,
        chembl_link_status,
        chembl_molecule_key,
        chembl_molecule_id,
        molecule_relation_type,
        match_strategy,
        matched_name,
        matched_name_type
    )
    VALUES (
        :chembl_anchor_key,
        :chembl_anchor_type,
        :chembl_anchor_rxcui,
        :chembl_anchor_name,
        :chembl_link_status,
        :chembl_molecule_key,
        :chembl_molecule_id,
        :molecule_relation_type,
        :match_strategy,
        :matched_name,
        :matched_name_type
    )
    ON CONFLICT (
        chembl_anchor_key,
        chembl_molecule_key,
        molecule_relation_type
    )
    DO UPDATE SET
        chembl_anchor_type = EXCLUDED.chembl_anchor_type,
        chembl_anchor_rxcui = EXCLUDED.chembl_anchor_rxcui,
        chembl_anchor_name = EXCLUDED.chembl_anchor_name,
        chembl_link_status = EXCLUDED.chembl_link_status,
        chembl_molecule_id = EXCLUDED.chembl_molecule_id,
        match_strategy = EXCLUDED.match_strategy,
        matched_name = EXCLUDED.matched_name,
        matched_name_type = EXCLUDED.matched_name_type
    """
)


@dataclass(frozen=True)
class RxNormDrugTermForChembl:
    input_drug_name: str
    match_status: str
    rxnorm_rxcui: str
    rxnorm_canonical_name: str
    rxnorm_term_type: str
    rxnorm_ingredient_rxcui: str
    rxnorm_ingredient_name: str
    rxnorm_ingredient_term_type: str
    rxnorm_mapping_manual_review_needed: bool = False


@dataclass
class ChemblAnchorCandidate:
    anchor_key: str
    anchor_type: str
    anchor_rxcui: str
    anchor_name: str
    anchor_term_type: str
    anchor_strategy: str
    anchor_path: str
    manual_review_needed: bool
    matched_molregnos: set[int]
    matched_name: str
    matched_name_type: str
    anchor_selection_strategy: str


@dataclass
class ChemblAnchorAggregate:
    anchor_key: str
    anchor_type: str
    anchor_rxcui: str
    anchor_name: str
    anchor_term_type: str
    anchor_strategy: str
    anchor_path: str
    manual_review_needed: bool
    matched_molregnos: set[int]
    matched_names: set[str]
    matched_name_types: set[str]
    anchor_selection_strategies: Counter
    input_drug_names: set[str]


def fetch_rxnorm_drug_terms_for_chembl(engine: Engine) -> list[RxNormDrugTermForChembl]:
    with engine.connect() as conn:
        rows = conn.execute(FETCH_RXNORM_DRUG_TERMS_SQL).mappings().all()

    return [
        RxNormDrugTermForChembl(
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
        {"load_batch_id": str(load_batch_id), "source_file": source_file, "source_version": source_version},
    )


def complete_load_batch(conn: Connection, load_batch_id: uuid.UUID, row_count: int) -> None:
    conn.execute(COMPLETE_LOAD_BATCH_SQL, {"load_batch_id": str(load_batch_id), "row_count": row_count})


def load_rxnorm_index(rxnorm_rrf_dir: Path) -> RxnConsoIndex:
    logger.info("Loading RxNorm CONSO index from %s", rxnorm_rrf_dir)
    return RxnConsoIndex.from_rrf_dir(rxnorm_rrf_dir)


    logger.info("Loading RxNorm CONSO index from %s", rxnorm_rrf_dir)
    return RxnConsoIndex.from_rrf_dir(rxnorm_rrf_dir)


def derive_rxnorm_chembl_anchor(term: RxNormDrugTermForChembl, conso_index: RxnConsoIndex) -> ChemblLinkAnchor | None:
    if term.match_status != "MATCHED" or not term.rxnorm_rxcui:
        return None
    return derive_chembl_link_anchor_from_values(
        source_text=term.input_drug_name,
        matched_rxcui=term.rxnorm_rxcui,
        canonical_name=term.rxnorm_canonical_name,
        canonical_tty=term.rxnorm_term_type,
        ingredient_rxcui=term.rxnorm_ingredient_rxcui,
        ingredient_name=term.rxnorm_ingredient_name,
        ingredient_tty=term.rxnorm_ingredient_term_type,
        conso_index=conso_index,
    )


def query_values_for_terms(terms: Sequence[RxNormDrugTermForChembl], conso_index: RxnConsoIndex) -> list[str]:
    values: list[str] = []
    for term in terms:
        values.append(term.input_drug_name)
        values.append(term.rxnorm_canonical_name)
        values.append(term.rxnorm_ingredient_name)
        anchor = derive_rxnorm_chembl_anchor(term, conso_index)
        if anchor:
            values.append(anchor.link_anchor_name)
    return [value for value in values if clean_text(value)]


def chembl_matches_for_query(result: ChemblBuildResult, query: str) -> tuple[list[ChemblNameMatch], str]:
    key = normalize_match_key(query)
    exact = result.exact_matches_by_query_key.get(key, [])
    if exact:
        return exact, "EXACT_NORMALIZED_NAME"
    equivalent = result.equivalent_matches_by_query_key.get(key, [])
    if equivalent:
        return equivalent, "CONTROLLED_NAME_EQUIVALENCE"
    return [], "NO_CHEMBL_NAME_MATCH"


def molecule_pref_summary(matches: Sequence[ChemblNameMatch]) -> str:
    return summarize_values(f"{m.chembl_id} {m.pref_name}" for m in matches)


# ChEMBL name matches can be exact but still semantically unsafe.
# Examples found in v1:
#   Kadcyla -> TRASTUZUMAB and TRASTUZUMAB EMTANSINE
#   Besponsa -> INOTUZUMAB and INOTUZUMAB OZOGAMICIN
#   ADCT-402 -> LONCASTUXIMAB and LONCASTUXIMAB TESIRINE
# Prefer the specific ADC/conjugate/radioligand molecule when the exact
# ChEMBL synonym points to both a payload-bearing product and the broad parent.
SPECIFIC_PRODUCT_TOKENS = {
    "emtansine", "deruxtecan", "vedotin", "govitecan", "mafodotin", "tesirine",
    "ozogamicin", "soravtansine", "ravtansine", "mertansine", "duocarmazine",
    "pyrrolobenzodiazepine", "pbd", "maytansine", "exatecan", "auristatin",
    "vipivotide", "tetraxetan", "lutetium", "lu", "iobenguane", "dotatate",
}

# Known combination brands where ChEMBL may expose component synonyms but not a
# full combination molecule. Do not let these collapse to one component.
# This is intentionally tiny and auditable; a later combination model can expand
# these into component-level evidence.
COMBINATION_BRAND_NO_SINGLE_MOLECULE = {
    "opdualag",
    "rituxan hycela",
}


def token_set(value: object) -> set[str]:
    return set(normalize_match_key(value).split())


def direct_match_specificity_score(match: ChemblNameMatch) -> int:
    text_tokens = token_set(f"{match.pref_name} {match.matched_name}")
    score = 10 * len(text_tokens & SPECIFIC_PRODUCT_TOKENS)
    # If ChEMBL preferred name itself carries the specific payload term, prefer it
    # over broad component molecules whose synonym merely mentions the brand.
    pref_tokens = token_set(match.pref_name)
    score += 5 * len(pref_tokens & SPECIFIC_PRODUCT_TOKENS)
    return score


def prefer_specific_direct_matches(matches: Sequence[ChemblNameMatch]) -> list[ChemblNameMatch]:
    matches = list(matches)
    if len({m.molregno for m in matches}) <= 1:
        return matches
    scored = [(direct_match_specificity_score(match), match) for match in matches]
    top_score = max(score for score, _ in scored)
    if top_score <= 0:
        return matches
    return [match for score, match in scored if score == top_score]


def is_blocked_combination_brand(value: object) -> bool:
    return normalize_match_key(value) in COMBINATION_BRAND_NO_SINGLE_MOLECULE


def choose_anchor_for_term(
    term: RxNormDrugTermForChembl,
    conso_index: RxnConsoIndex,
    chembl_result: ChemblBuildResult,
) -> ChemblAnchorCandidate:
    # Direct ChEMBL name/synonym match has highest priority. This handles brands like
    # PADCEV/Trodelvy/Keytruda without collapsing them to a broad RxNorm ingredient.
    # But known combination brands must not collapse to a single component molecule.
    if is_blocked_combination_brand(term.input_drug_name):
        alias_key = normalize_match_key(term.input_drug_name)
        return ChemblAnchorCandidate(
            anchor_key=f"UNMATCHED_ALIAS:{alias_key}",
            anchor_type="UNMATCHED_ALIAS",
            anchor_rxcui=term.rxnorm_ingredient_rxcui or term.rxnorm_rxcui,
            anchor_name=term.input_drug_name,
            anchor_term_type=term.rxnorm_ingredient_term_type or term.rxnorm_term_type,
            anchor_strategy="COMBINATION_BRAND_BLOCKED_NO_SINGLE_CHEMBL_MOLECULE",
            anchor_path=alias_key,
            manual_review_needed=True,
            matched_molregnos=set(),
            matched_name="",
            matched_name_type="",
            anchor_selection_strategy="COMBINATION_BRAND_BLOCKED_NO_SINGLE_CHEMBL_MOLECULE",
        )

    raw_direct_matches, direct_strategy = chembl_matches_for_query(chembl_result, term.input_drug_name)
    direct_matches = prefer_specific_direct_matches(raw_direct_matches)
    if raw_direct_matches and len({m.molregno for m in direct_matches}) < len({m.molregno for m in raw_direct_matches}):
        direct_strategy = f"{direct_strategy}:SPECIFIC_PRODUCT_FILTER"
    if direct_matches:
        molregnos = {match.molregno for match in direct_matches}
        if len(molregnos) == 1:
            match = direct_matches[0]
            return ChemblAnchorCandidate(
                anchor_key=f"CHEMBL:{match.molregno}",
                anchor_type="CHEMBL_MOLECULE",
                anchor_rxcui=term.rxnorm_ingredient_rxcui or term.rxnorm_rxcui,
                anchor_name=match.pref_name or term.input_drug_name,
                anchor_term_type=term.rxnorm_ingredient_term_type or term.rxnorm_term_type,
                anchor_strategy=f"DIRECT_CHEMBL_NAME:{direct_strategy}",
                anchor_path=f"input({term.input_drug_name}) -> {match.chembl_id}",
                manual_review_needed=term.rxnorm_mapping_manual_review_needed,
                matched_molregnos=molregnos,
                matched_name=match.matched_name,
                matched_name_type=match.matched_name_type,
                anchor_selection_strategy=f"DIRECT_CHEMBL_NAME:{direct_strategy}",
            )
        return ChemblAnchorCandidate(
            anchor_key=f"CHEMBL_ALIAS:{normalize_match_key(term.input_drug_name)}",
            anchor_type="CHEMBL_ALIAS_AMBIGUOUS",
            anchor_rxcui=term.rxnorm_ingredient_rxcui or term.rxnorm_rxcui,
            anchor_name=term.input_drug_name,
            anchor_term_type=term.rxnorm_ingredient_term_type or term.rxnorm_term_type,
            anchor_strategy=f"DIRECT_CHEMBL_NAME_AMBIGUOUS:{direct_strategy}",
            anchor_path=molecule_pref_summary(direct_matches),
            manual_review_needed=True,
            matched_molregnos=molregnos,
            matched_name=term.input_drug_name,
            matched_name_type="DIRECT_INPUT",
            anchor_selection_strategy=f"DIRECT_CHEMBL_NAME_AMBIGUOUS:{direct_strategy}",
        )

    rx_anchor = derive_rxnorm_chembl_anchor(term, conso_index)
    if rx_anchor and rx_anchor.link_anchor_name:
        rx_matches, rx_strategy = chembl_matches_for_query(chembl_result, rx_anchor.link_anchor_name)
        if rx_matches:
            molregnos = {match.molregno for match in rx_matches}
            if len(molregnos) == 1:
                match = rx_matches[0]
                return ChemblAnchorCandidate(
                    anchor_key=f"CHEMBL:{match.molregno}",
                    anchor_type="CHEMBL_MOLECULE",
                    anchor_rxcui=rx_anchor.link_anchor_rxcui,
                    anchor_name=match.pref_name or rx_anchor.link_anchor_name,
                    anchor_term_type=rx_anchor.link_anchor_term_type,
                    anchor_strategy=f"RXNORM_ANCHOR_TO_CHEMBL_NAME:{rx_strategy}:{rx_anchor.link_anchor_strategy}",
                    anchor_path=f"{rx_anchor.link_anchor_path} -> {match.chembl_id}",
                    manual_review_needed=term.rxnorm_mapping_manual_review_needed or rx_anchor.link_anchor_manual_review_needed,
                    matched_molregnos=molregnos,
                    matched_name=match.matched_name,
                    matched_name_type=match.matched_name_type,
                    anchor_selection_strategy=f"RXNORM_ANCHOR_TO_CHEMBL_NAME:{rx_strategy}",
                )
            return ChemblAnchorCandidate(
                anchor_key=f"RXCUI:{rx_anchor.link_anchor_rxcui}",
                anchor_type="RXNORM_ANCHOR_AMBIGUOUS_CHEMBL",
                anchor_rxcui=rx_anchor.link_anchor_rxcui,
                anchor_name=rx_anchor.link_anchor_name,
                anchor_term_type=rx_anchor.link_anchor_term_type,
                anchor_strategy=f"RXNORM_ANCHOR_AMBIGUOUS_CHEMBL:{rx_strategy}:{rx_anchor.link_anchor_strategy}",
                anchor_path=molecule_pref_summary(rx_matches),
                manual_review_needed=True,
                matched_molregnos=molregnos,
                matched_name=rx_anchor.link_anchor_name,
                matched_name_type="RXNORM_ANCHOR_NAME",
                anchor_selection_strategy=f"RXNORM_ANCHOR_AMBIGUOUS_CHEMBL:{rx_strategy}",
            )
        return ChemblAnchorCandidate(
            anchor_key=f"RXCUI:{rx_anchor.link_anchor_rxcui}",
            anchor_type="RXNORM_ANCHOR",
            anchor_rxcui=rx_anchor.link_anchor_rxcui,
            anchor_name=rx_anchor.link_anchor_name,
            anchor_term_type=rx_anchor.link_anchor_term_type,
            anchor_strategy=rx_anchor.link_anchor_strategy,
            anchor_path=rx_anchor.link_anchor_path,
            manual_review_needed=True,
            matched_molregnos=set(),
            matched_name="",
            matched_name_type="",
            anchor_selection_strategy="RXNORM_ANCHOR_NO_CHEMBL_MATCH",
        )

    alias_key = normalize_match_key(term.input_drug_name)
    return ChemblAnchorCandidate(
        anchor_key=f"UNMATCHED_ALIAS:{alias_key}",
        anchor_type="UNMATCHED_ALIAS",
        anchor_rxcui="",
        anchor_name=term.input_drug_name,
        anchor_term_type="",
        anchor_strategy="NO_RXNORM_OR_CHEMBL_MATCH",
        anchor_path=alias_key,
        manual_review_needed=True,
        matched_molregnos=set(),
        matched_name="",
        matched_name_type="",
        anchor_selection_strategy="NO_RXNORM_OR_CHEMBL_MATCH",
    )


def index_by_molregno(rows: Sequence) -> dict[int, list]:
    out: dict[int, list] = defaultdict(list)
    for row in rows:
        out[int(row.molregno)].append(row)
    return out


def molecule_by_molregno(rows: Sequence[ChemblMolecule]) -> dict[int, ChemblMolecule]:
    return {int(row.molregno): row for row in rows}


def relation_targets_for_anchor(anchor: ChemblAnchorAggregate, relations_by_source: Mapping[int, Sequence[ChemblMoleculeRelation]]) -> list[ChemblMoleculeRelation]:
    out: list[ChemblMoleculeRelation] = []
    for molregno in sorted(anchor.matched_molregnos):
        rels = list(relations_by_source.get(molregno, []))
        if rels:
            out.extend(rels)
        else:
            out.append(ChemblMoleculeRelation(molregno=molregno, related_molregno=molregno, relation_type="EXACT"))
    seen = set()
    deduped = []
    for rel in out:
        key = (rel.related_molregno, rel.relation_type)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(rel)
    return sorted(deduped, key=lambda r: (r.relation_type != "EXACT", r.relation_type, r.related_molregno))


def payload_for_mapping(anchor: ChemblAnchorAggregate, molecule: ChemblMolecule | None, relation_type: str | None) -> str:
    return json.dumps(
        {
            "ctgov_chembl_anchor": {
                "anchor_key": anchor.anchor_key,
                "anchor_type": anchor.anchor_type,
                "anchor_rxcui": anchor.anchor_rxcui,
                "anchor_name": anchor.anchor_name,
                "anchor_strategy": anchor.anchor_strategy,
                "anchor_selection_strategies": dict(anchor.anchor_selection_strategies),
                "represented_ctgov_drug_term_count": len(anchor.ctgov_terms),
            },
            "chembl_molecule": asdict(molecule) if molecule else None,
            "molecule_relation_type": relation_type,
        },
        ensure_ascii=False,
    )


def phase_sort_key(value: object) -> int:
    return parse_int(value, default=-1)


def indication_json(indications: Sequence[ChemblIndication], molecules_by_molregno: Mapping[int, ChemblMolecule]) -> str:
    rows = []
    for ind in sorted(indications, key=lambda x: (-phase_sort_key(x.max_phase_for_ind), x.efo_term, x.mesh_heading, x.molregno)):
        mol = molecules_by_molregno.get(ind.molregno)
        rows.append(
            {
                "max_phase_for_ind": parse_int(ind.max_phase_for_ind, default=0),
                "efo_term": ind.efo_term,
                "efo_id": ind.efo_id,
                "mesh_heading": ind.mesh_heading,
                "mesh_id": ind.mesh_id,
                "chembl_id": mol.chembl_id if mol else "",
                "pref_name": mol.pref_name if mol else "",
                "is_oncology": ind.is_oncology,
            }
        )
    return json.dumps(rows, ensure_ascii=False)


def mechanism_json(mechanisms: Sequence[ChemblMechanism], molecules_by_molregno: Mapping[int, ChemblMolecule]) -> str:
    rows = []
    for mech in sorted(mechanisms, key=lambda x: (x.mechanism_of_action, x.target_pref_name, x.molregno)):
        mol = molecules_by_molregno.get(mech.molregno)
        rows.append(
            {
                "mechanism_of_action": mech.mechanism_of_action,
                "action_type": mech.action_type,
                "target_chembl_id": mech.target_chembl_id,
                "target_pref_name": mech.target_pref_name,
                "target_type": mech.target_type,
                "target_organism": mech.target_organism,
                "target_accessions": mech.target_accessions,
                "direct_interaction": parse_int(mech.direct_interaction, default=0),
                "molecular_mechanism": parse_int(mech.molecular_mechanism, default=0),
                "disease_efficacy": parse_int(mech.disease_efficacy, default=0),
                "chembl_id": mol.chembl_id if mol else "",
                "pref_name": mol.pref_name if mol else "",
            }
        )
    return json.dumps(rows, ensure_ascii=False)


def warning_json(warnings: Sequence[ChemblWarning], molecules_by_molregno: Mapping[int, ChemblMolecule]) -> str:
    rows = []
    for warning in sorted(warnings, key=lambda x: (x.warning_type, x.warning_class, x.molregno)):
        mol = molecules_by_molregno.get(warning.molregno)
        rows.append(
            {
                "warning_type": warning.warning_type,
                "warning_class": warning.warning_class,
                "warning_description": warning.warning_description,
                "warning_country": warning.warning_country,
                "warning_year": warning.warning_year,
                "efo_term": warning.efo_term,
                "efo_id": warning.efo_id,
                "chembl_id": mol.chembl_id if mol else "",
                "pref_name": mol.pref_name if mol else "",
            }
        )
    return json.dumps(rows, ensure_ascii=False)


def indication_phase_summary(indications: Sequence[ChemblIndication]) -> str:
    by_phase: dict[int, list[str]] = defaultdict(list)
    for ind in indications:
        phase = phase_sort_key(ind.max_phase_for_ind)
        label = ind.efo_term or ind.mesh_heading
        identifier = ind.efo_id or ind.mesh_id
        if label:
            by_phase[phase].append(f"{label} [{identifier}]" if identifier else label)
    pieces = []
    for phase in sorted(by_phase.keys(), reverse=True):
        if phase < 0:
            continue
        pieces.append(f"phase {phase}: {summarize_values(by_phase[phase], limit=10)}")
    return SUMMARY_DELIMITER.join(pieces)


def aggregate_input_chembl_anchors(
    terms: Sequence[RxNormDrugTermForChembl],
    conso_index: RxnConsoIndex,
    chembl_result: ChemblBuildResult,
) -> tuple[list[dict[str, object]], dict[str, ChemblAnchorAggregate]]:
    link_records: list[dict[str, object]] = []
    aggregates: dict[str, ChemblAnchorAggregate] = {}

    for term in terms:
        candidate = choose_anchor_for_term(term, conso_index, chembl_result)

        link_records.append(
            {
                "input_drug_name": term.input_drug_name,
                "chembl_anchor_key": candidate.anchor_key,
            }
        )

        aggregate = aggregates.get(candidate.anchor_key)
        if aggregate is None:
            aggregate = ChemblAnchorAggregate(
                anchor_key=candidate.anchor_key,
                anchor_type=candidate.anchor_type,
                anchor_rxcui=candidate.anchor_rxcui,
                anchor_name=candidate.anchor_name,
                anchor_term_type=candidate.anchor_term_type,
                anchor_strategy=candidate.anchor_strategy,
                anchor_path=candidate.anchor_path,
                manual_review_needed=candidate.manual_review_needed,
                matched_molregnos=set(candidate.matched_molregnos),
                matched_names=set(),
                matched_name_types=set(),
                anchor_selection_strategies=Counter(),
                input_drug_names={term.input_drug_name},
            )
            aggregates[candidate.anchor_key] = aggregate
        else:
            aggregate.matched_molregnos.update(candidate.matched_molregnos)
            aggregate.input_drug_names.add(term.input_drug_name)

        if candidate.matched_name:
            aggregate.matched_names.add(candidate.matched_name)
        if candidate.matched_name_type:
            aggregate.matched_name_types.add(candidate.matched_name_type)
        aggregate.anchor_selection_strategies[candidate.anchor_selection_strategy] += 1
        aggregate.manual_review_needed = aggregate.manual_review_needed or candidate.manual_review_needed or len(aggregate.matched_molregnos) > 1

    return link_records, aggregates


def query_values_for_terms(terms: Sequence[RxNormDrugTermForChembl], conso_index: RxnConsoIndex) -> list[str]:
    values: list[str] = []
    for term in terms:
        values.append(term.input_drug_name)
        values.append(term.rxnorm_canonical_name)
        values.append(term.rxnorm_ingredient_name)
        anchor = derive_rxnorm_chembl_anchor(term, conso_index)
        if anchor:
            values.append(anchor.link_anchor_name)
    return [value for value in values if clean_text(value)]


def molecule_relation_mapping_records(
    anchors: Mapping[str, ChemblAnchorAggregate],
    molecule_id_by_molregno: Mapping[int, int],
    molecules_by_molregno: Mapping[int, ChemblMolecule],
    relations_by_source: Mapping[int, Sequence[ChemblMoleculeRelation]],
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []

    for anchor in sorted(anchors.values(), key=lambda a: (a.anchor_type, a.anchor_name.lower(), a.anchor_key)):
        rels = relation_targets_for_anchor(anchor, relations_by_source)
        target_molregnos = [
            rel.related_molregno
            for rel in rels
            if rel.related_molregno in molecule_id_by_molregno
        ]

        if not target_molregnos:
            records.append(
                {
                    "chembl_anchor_key": anchor.anchor_key,
                    "chembl_anchor_type": anchor.anchor_type,
                    "chembl_anchor_rxcui": anchor.anchor_rxcui,
                    "chembl_anchor_name": anchor.anchor_name,
                    "chembl_link_status": "NO_CHEMBL_MOLECULE_FOR_ANCHOR",
                    "chembl_molecule_key": "",
                    "chembl_molecule_id": None,
                    "molecule_relation_type": "",
                    "match_strategy": ";".join(f"{k}={v}" for k, v in sorted(anchor.anchor_selection_strategies.items())),
                    "matched_name": summarize_values(anchor.matched_names),
                    "matched_name_type": summarize_values(anchor.matched_name_types),
                }
            )
            continue

        for rel in rels:
            molecule = molecules_by_molregno.get(rel.related_molregno)
            if molecule is None or rel.related_molregno not in molecule_id_by_molregno:
                continue

            records.append(
                {
                    "chembl_anchor_key": anchor.anchor_key,
                    "chembl_anchor_type": anchor.anchor_type,
                    "chembl_anchor_rxcui": anchor.anchor_rxcui,
                    "chembl_anchor_name": anchor.anchor_name,
                    "chembl_link_status": "MATCHED_CHEMBL_MOLECULE",
                    "chembl_molecule_key": molecule.chembl_id,
                    "chembl_molecule_id": molecule_id_by_molregno[rel.related_molregno],
                    "molecule_relation_type": rel.relation_type,
                    "match_strategy": ";".join(f"{k}={v}" for k, v in sorted(anchor.anchor_selection_strategies.items())),
                    "matched_name": summarize_values(anchor.matched_names),
                    "matched_name_type": summarize_values(anchor.matched_name_types),
                }
            )

    return records


def insert_chembl_records(
    conn: Connection,
    chembl: ChemblBuildResult,
    load_batch_id: uuid.UUID,
    chembl_source_version: str,
) -> dict[int, int]:
    molecule_id_by_molregno: dict[int, int] = {}
    for molecule in chembl.molecules:
        molecule_id = conn.execute(
            INSERT_CHEMBL_MOLECULE_SQL,
            {**asdict(molecule), "load_batch_id": str(load_batch_id), "chembl_source_version": chembl_source_version},
        ).scalar_one()
        molecule_id_by_molregno[int(molecule.molregno)] = int(molecule_id)

    alias_records = [
        {
            **asdict(alias),
            "chembl_molecule_id": molecule_id_by_molregno[alias.molregno],
            "load_batch_id": str(load_batch_id),
            "chembl_source_version": chembl_source_version,
        }
        for alias in chembl.aliases
        if alias.molregno in molecule_id_by_molregno
    ]
    if alias_records:
        conn.execute(INSERT_CHEMBL_ALIAS_SQL, alias_records)

    relation_records = [
        {
            **asdict(rel),
            "chembl_molecule_id": molecule_id_by_molregno[rel.molregno],
            "related_chembl_molecule_id": molecule_id_by_molregno[rel.related_molregno],
            "load_batch_id": str(load_batch_id),
            "chembl_source_version": chembl_source_version,
        }
        for rel in chembl.relations
        if rel.molregno in molecule_id_by_molregno and rel.related_molregno in molecule_id_by_molregno
    ]
    if relation_records:
        conn.execute(INSERT_CHEMBL_RELATION_SQL, relation_records)

    mechanism_records = [
        {
            **asdict(mech),
            "chembl_molecule_id": molecule_id_by_molregno[mech.molregno],
            "load_batch_id": str(load_batch_id),
            "chembl_source_version": chembl_source_version,
        }
        for mech in chembl.mechanisms
        if mech.molregno in molecule_id_by_molregno
    ]
    if mechanism_records:
        conn.execute(INSERT_CHEMBL_MECHANISM_SQL, mechanism_records)

    indication_records = [
        {
            **asdict(ind),
            "chembl_molecule_id": molecule_id_by_molregno[ind.molregno],
            "load_batch_id": str(load_batch_id),
            "chembl_source_version": chembl_source_version,
        }
        for ind in chembl.indications
        if ind.molregno in molecule_id_by_molregno
    ]
    if indication_records:
        conn.execute(INSERT_CHEMBL_INDICATION_SQL, indication_records)

    atc_records = [
        {
            **asdict(atc),
            "chembl_molecule_id": molecule_id_by_molregno[atc.molregno],
            "load_batch_id": str(load_batch_id),
            "chembl_source_version": chembl_source_version,
        }
        for atc in chembl.atc_rows
        if atc.molregno in molecule_id_by_molregno
    ]
    if atc_records:
        conn.execute(INSERT_CHEMBL_ATC_SQL, atc_records)

    warning_records = [
        {
            **asdict(warning),
            "chembl_molecule_id": molecule_id_by_molregno[warning.molregno],
            "load_batch_id": str(load_batch_id),
            "chembl_source_version": chembl_source_version,
        }
        for warning in chembl.warnings
        if warning.molregno in molecule_id_by_molregno
    ]
    if warning_records:
        conn.execute(INSERT_CHEMBL_WARNING_SQL, warning_records)

    return molecule_id_by_molregno


def load_chembl_mappings_to_postgres(
    database_url: str,
    chembl_sqlite_path: Path,
    rxnorm_rrf_dir: Path,
    chembl_source_version: str,
) -> uuid.UUID:
    engine = create_engine(database_url)
    conso_index = load_rxnorm_index(rxnorm_rrf_dir)

    terms = fetch_rxnorm_drug_terms_for_chembl(engine)
    logger.info("Fetched %d simplified RxNorm drug terms for ChEMBL linking", len(terms))

    query_values = query_values_for_terms(terms, conso_index)
    chembl = build_chembl_records(chembl_sqlite_path, query_values)
    link_rows, anchors = aggregate_input_chembl_anchors(terms, conso_index, chembl)

    logger.info(
        "Collapsed %d input drug terms to %d ChEMBL anchors",
        len(terms),
        len(anchors),
    )

    molecules_by_molregno = molecule_by_molregno(chembl.molecules)
    relations_by_source = index_by_molregno(chembl.relations)

    load_batch_id = uuid.uuid4()
    source_file = json.dumps(
        {
            "chembl_sqlite_path": str(chembl_sqlite_path),
            "rxnorm_rrf_dir": str(rxnorm_rrf_dir),
        },
        ensure_ascii=False,
    )

    with engine.begin() as conn:
        create_load_batch(conn, load_batch_id, source_file=source_file, source_version=chembl_source_version)
        conn.execute(TRUNCATE_CHEMBL_TABLES_SQL)

        molecule_id_by_molregno = insert_chembl_records(conn, chembl, load_batch_id, chembl_source_version)

        if link_rows:
            conn.execute(INSERT_INPUT_CHEMBL_ANCHOR_SQL, link_rows)

        mapping_rows = molecule_relation_mapping_records(
            anchors=anchors,
            molecule_id_by_molregno=molecule_id_by_molregno,
            molecules_by_molregno=molecules_by_molregno,
            relations_by_source=relations_by_source,
        )
        if mapping_rows:
            conn.execute(INSERT_CHEMBL_ANCHOR_MAPPING_SQL, mapping_rows)

        complete_load_batch(conn, load_batch_id, row_count=len(mapping_rows))

    logger.info(
        "Loaded ChEMBL: %d molecules, %d anchors, %d input-anchor links, %d anchor-molecule rows; load_batch_id=%s",
        len(chembl.molecules),
        len(anchors),
        len(link_rows),
        len(mapping_rows),
        load_batch_id,
    )
    return load_batch_id


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Load ChEMBL evidence for simplified CTGov drug ontology outputs."
    )
    parser.add_argument(
        "--chembl_sqlite_path",
        required=True,
        type=Path,
        help="Path to ChEMBL SQLite database, e.g. chembl_36.db.",
    )
    parser.add_argument(
        "--rxnorm_rrf_dir",
        required=True,
        type=Path,
        help="Directory containing RxNorm RRF files.",
    )
    parser.add_argument(
        "--chembl_source_version",
        required=True,
        help="ChEMBL source version label, usually basename of ChEMBL raw folder or DB stem.",
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

    load_chembl_mappings_to_postgres(
        database_url=database_url,
        chembl_sqlite_path=args.chembl_sqlite_path,
        rxnorm_rrf_dir=args.rxnorm_rrf_dir,
        chembl_source_version=args.chembl_source_version,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
