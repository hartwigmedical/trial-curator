from __future__ import annotations

"""ChEMBL molecule parsing and conservative RxNorm/ChEMBL anchoring.

This module is database-agnostic with respect to PostgreSQL. It reads ChEMBL's
SQLite database, derives exact/controlled-name matches, and returns molecule and
evidence records for a bounded set of CTGov/RxNorm query terms.

Design lessons carried over from ATC/FDA/POTTR:
- no uncontrolled substring matching;
- specific ADC/conjugate names must not collapse to parent antibodies;
- biologic suffix/proper-name equivalence is allowed only when the payload/core is preserved;
- isotope/radionuclide notation is canonicalized while preserving isotope identity.
"""

import json
import logging
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from aus_trial_universe.drug_utility_path.ctgov.drug_ontology.identity.rxnorm.matcher import (
    RxnConsoIndex,
)

logger = logging.getLogger(__name__)

SUMMARY_DELIMITER = " | "
TOKEN_RE = re.compile(r"[a-z0-9]+")
BIOLOGIC_SUFFIX_RE = re.compile(r"-[a-z]{4}$", re.IGNORECASE)
PROPER_NAME_PREFIXES = {"ado", "fam"}
TRADEMARK_SYMBOL_RE = re.compile(r"[®™\ufe0f]")
TRADEMARK_TEXT_RE = re.compile(r"(?i)(?:\(\s*tm\s*\)|\^\s*tm\b|(?<=\w)tm\b)")

NON_COLLAPSIBLE_ACTIVE_TOKENS = {
    "emtansine", "deruxtecan", "vedotin", "govitecan", "mafodotin", "tesirine",
    "ozogamicin", "soravtansine", "ravtansine", "mertansine", "duocarmazine",
    "pyrrolobenzodiazepine", "pbd", "maytansine", "exatecan", "auristatin",
    "vipivotide", "tetraxetan", "lutetium", "lu", "iobenguane", "dotatate",
    "radioconjugate", "radioligand",
}

SIMPLE_FORM_MODIFIER_TOKENS = {
    "hydrochloride", "hcl", "hydrobromide", "sulfate", "sulphate", "mesylate", "mesilate",
    "tosylate", "besylate", "fumarate", "succinate", "acetate", "phosphate", "diphosphate",
    "nitrate", "citrate", "tartrate", "bitartrate", "maleate", "malate", "calcium", "sodium",
    "potassium", "magnesium", "zinc", "chloride", "bromide", "iodide", "carbonate",
    "bicarbonate", "monohydrate", "dihydrate", "heptahydrate", "anhydrous", "hydrate",
    "base", "recombinant",
}

ONCOLOGY_TERM_RE = re.compile(
    r"\b(cancer|carcinoma|neoplasm|neoplastic|tumou?r|melanoma|leuka?emia|lymphoma|"
    r"myeloma|sarcoma|glioma|blastoma|mesothelioma|malignan|adenoma|leukemic|leukaemic|neoplasms?)\b",
    re.IGNORECASE,
)

ISOTOPE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Preserve isotope identity. These rules canonicalize notation; they do not strip isotope tokens.
    (re.compile(r"\b(?:lutetium\s*)?lu\s*[- ]?\s*177\b", re.IGNORECASE), "177lu"),
    (re.compile(r"\b(?:lutetium\s*)?177\s*[- ]?\s*lu\b", re.IGNORECASE), "177lu"),
    (re.compile(r"[\[\(]?\s*177\s*lu\s*[\]\)]?", re.IGNORECASE), "177lu"),
    (re.compile(r"\b(?:actinium\s*)?ac\s*[- ]?\s*225\b", re.IGNORECASE), "225ac"),
    (re.compile(r"\b(?:actinium\s*)?225\s*[- ]?\s*ac\b", re.IGNORECASE), "225ac"),
    (re.compile(r"[\[\(]?\s*225\s*ac\s*[\]\)]?", re.IGNORECASE), "225ac"),
    (re.compile(r"\b(?:terbium\s*)?tb\s*[- ]?\s*161\b", re.IGNORECASE), "161tb"),
    (re.compile(r"\b(?:terbium\s*)?161\s*[- ]?\s*tb\b", re.IGNORECASE), "161tb"),
    (re.compile(r"[\[\(]?\s*161\s*tb\s*[\]\)]?", re.IGNORECASE), "161tb"),
    (re.compile(r"\b(?:indium\s*)?in\s*[- ]?\s*111\b", re.IGNORECASE), "111in"),
    (re.compile(r"\b(?:indium\s*)?111\s*[- ]?\s*in\b", re.IGNORECASE), "111in"),
    (re.compile(r"[\[\(]?\s*111\s*in\s*[\]\)]?", re.IGNORECASE), "111in"),
    (re.compile(r"\b(?:iodine\s*)?i\s*[- ]?\s*131\b", re.IGNORECASE), "131i"),
    (re.compile(r"\b(?:iodine\s*)?131\s*[- ]?\s*i\b", re.IGNORECASE), "131i"),
    (re.compile(r"[\[\(]?\s*131\s*i\s*[\]\)]?", re.IGNORECASE), "131i"),
)


@dataclass(frozen=True)
class ChemblLinkAnchor:
    link_anchor_rxcui: str
    link_anchor_name: str
    link_anchor_term_type: str
    link_anchor_strategy: str
    link_anchor_path: str
    link_anchor_manual_review_needed: bool


@dataclass(frozen=True)
class ChemblNameMatch:
    molregno: int
    chembl_id: str
    pref_name: str
    matched_name: str
    matched_name_type: str
    match_strategy: str
    match_key: str


@dataclass(frozen=True)
class ChemblMolecule:
    molregno: int
    chembl_id: str
    pref_name: str
    max_phase: str
    therapeutic_flag: str
    dosed_ingredient: str
    structure_type: str
    molecule_type: str
    first_approval: str
    oral: str
    parenteral: str
    topical: str
    black_box_warning: str
    first_in_class: str
    prodrug: str
    withdrawn_flag: str
    chemical_probe: str
    orphan: str
    standard_inchi_key: str
    canonical_smiles: str
    full_mwt: str
    full_molformula: str


@dataclass(frozen=True)
class ChemblAlias:
    molregno: int
    synonym: str
    synonym_normalized: str
    syn_type: str


@dataclass(frozen=True)
class ChemblMoleculeRelation:
    molregno: int
    related_molregno: int
    relation_type: str


@dataclass(frozen=True)
class ChemblMechanism:
    molregno: int
    mec_id: str
    record_id: str
    mechanism_of_action: str
    action_type: str
    direct_interaction: str
    molecular_mechanism: str
    disease_efficacy: str
    mechanism_comment: str
    selectivity_comment: str
    binding_site_comment: str
    target_chembl_id: str
    target_pref_name: str
    target_type: str
    target_organism: str
    target_accessions: str
    target_component_descriptions: str


@dataclass(frozen=True)
class ChemblIndication:
    molregno: int
    drugind_id: str
    record_id: str
    max_phase_for_ind: str
    mesh_id: str
    mesh_heading: str
    efo_id: str
    efo_term: str
    is_oncology: bool


@dataclass(frozen=True)
class ChemblAtc:
    molregno: int
    mol_atc_id: str
    level5: str
    who_name: str
    level1: str
    level2: str
    level3: str
    level4: str
    level1_description: str
    level2_description: str
    level3_description: str
    level4_description: str


@dataclass(frozen=True)
class ChemblWarning:
    molregno: int
    warning_id: str
    record_id: str
    warning_type: str
    warning_class: str
    warning_description: str
    warning_country: str
    warning_year: str
    efo_term: str
    efo_id: str
    efo_id_for_warning_class: str


@dataclass(frozen=True)
class ChemblBuildResult:
    molecules: list[ChemblMolecule]
    aliases: list[ChemblAlias]
    relations: list[ChemblMoleculeRelation]
    mechanisms: list[ChemblMechanism]
    indications: list[ChemblIndication]
    atc_rows: list[ChemblAtc]
    warnings: list[ChemblWarning]
    exact_matches_by_query_key: dict[str, list[ChemblNameMatch]]
    equivalent_matches_by_query_key: dict[str, list[ChemblNameMatch]]


def clean_text(value: object) -> str:
    if value is None:
        return ""
    text_value = str(value).strip()
    return "" if text_value.lower() in {"", "nan", "none", "<na>"} else text_value


def normalize_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", clean_text(value))
    # FDA/POTTR showed that trademark markers often survive as symbols,
    # text fragments, or variation-selector sequences. Strip them before
    # keying names so Trodelvy™, KEYTRUDA®, etc. match exact ChEMBL synonyms.
    text = TRADEMARK_SYMBOL_RE.sub("", text)
    text = TRADEMARK_TEXT_RE.sub("", text)
    for pattern, replacement in ISOTOPE_PATTERNS:
        text = pattern.sub(f" {replacement} ", text)
    text = re.sub(r"[\u2010\u2011\u2012\u2013\u2014\u2212]", "-", text)
    text = text.lower()
    text = re.sub(r"[/_,;:()\[\]{}]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_match_key(value: object) -> str:
    text = normalize_text(value)
    # Preserve hyphen for exact key? We normalize it to spaces for robustness across ChEMBL/RxNorm formatting.
    text = text.replace("-", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def tokens(value: object) -> list[str]:
    return TOKEN_RE.findall(normalize_match_key(value))


def remove_proper_prefix_and_suffix(value: object) -> str:
    text = normalize_text(value)
    first_piece, sep, rest = text.partition("-")
    if sep and first_piece in PROPER_NAME_PREFIXES and rest:
        text = rest
    text = BIOLOGIC_SUFFIX_RE.sub("", text)
    text = text.replace("-", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def has_non_collapsible_active_modifier(value: object) -> bool:
    return bool(set(tokens(value)) & NON_COLLAPSIBLE_ACTIVE_TOKENS)


def equivalence_keys(value: object) -> list[str]:
    """Controlled ChEMBL name equivalence keys.

    These are deliberately narrow. They canonicalize biologic proper-name suffixes
    while preserving ADC/conjugate payload words and isotope identity.
    """
    base = normalize_match_key(value)
    keys = [base] if base else []
    stripped = remove_proper_prefix_and_suffix(value)
    if stripped and stripped != base:
        keys.append(stripped)

    # Parenthetical trimming for aliases like "FOLFOX (...)" but do not use this
    # as a substring rule; it only creates another full-name key.
    no_parens = re.sub(r"\([^)]*\)", " ", clean_text(value))
    no_parens_key = normalize_match_key(no_parens)
    if no_parens_key and no_parens_key not in keys:
        keys.append(no_parens_key)

    # And/plus/hyphen equivalence for explicit full-name combinations.
    combo = re.sub(r"\b(and|with)\b", " ", base)
    combo = combo.replace("+", " ").replace("-", " ")
    combo = re.sub(r"\s+", " ", combo).strip()
    if combo and combo not in keys:
        keys.append(combo)

    return ordered_unique(keys)


def ordered_unique(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = clean_text(value)
        key = normalize_match_key(cleaned)
        if cleaned and key not in seen:
            seen.add(key)
            out.append(cleaned)
    return out


def summarize_values(values: Iterable[str], limit: int = 12) -> str:
    unique = ordered_unique(values)
    if len(unique) <= limit:
        return SUMMARY_DELIMITER.join(unique)
    return SUMMARY_DELIMITER.join(unique[:limit]) + f" | ... (+{len(unique) - limit} more)"


def parse_int(value: object, default: int = -1) -> int:
    text = clean_text(value)
    if not text:
        return default
    try:
        return int(float(text))
    except ValueError:
        return default


def is_oncology_indication(mesh_heading: object, efo_term: object) -> bool:
    text = f"{clean_text(mesh_heading)} {clean_text(efo_term)}"
    return bool(ONCOLOGY_TERM_RE.search(text))


def derive_chembl_link_anchor_from_values(
    *,
    source_text: str,
    matched_rxcui: str,
    canonical_name: str,
    canonical_tty: str,
    ingredient_rxcui: str,
    ingredient_name: str,
    ingredient_tty: str,
    conso_index: RxnConsoIndex,
) -> ChemblLinkAnchor:
    """Derive a conservative ChEMBL lookup anchor from a Part 2 RxNorm mapping.

    This mirrors FDA/POTTR anchoring: broad ingredients stay broad, simple salts/forms
    may collapse to broad ingredients, and ADC/radioligand/conjugate cores are preserved.
    """
    matched_rxcui = clean_text(matched_rxcui)
    canonical_name = clean_text(canonical_name)
    canonical_tty = clean_text(canonical_tty)
    ingredient_rxcui = clean_text(ingredient_rxcui)
    ingredient_name = clean_text(ingredient_name)
    ingredient_tty = clean_text(ingredient_tty)
    source_text = clean_text(source_text)

    if not matched_rxcui:
        return ChemblLinkAnchor("", "", "", "NO_MATCHED_RXCUI", "", True)

    if canonical_tty in {"IN", "MIN"}:
        return ChemblLinkAnchor(
            matched_rxcui,
            canonical_name,
            canonical_tty,
            "MATCHED_RXCUI_IS_BROAD_INGREDIENT",
            matched_rxcui,
            False,
        )

    precise_name = canonical_name or source_text
    preserve_precise = (
        has_non_collapsible_active_modifier(precise_name)
        or has_non_collapsible_active_modifier(source_text)
        or bool(BIOLOGIC_SUFFIX_RE.search(normalize_text(precise_name)))
    )

    if canonical_tty == "PIN" and preserve_precise:
        # Try suffix-normalized exact RxNorm core first.
        for variant in equivalence_keys(precise_name):
            for row in conso_index.exact_lookup(variant):
                best = conso_index.best_row_for_rxcui(row.rxcui)
                if best is not None and best.tty in {"PIN", "IN", "MIN"} and best.rxcui != ingredient_rxcui:
                    return ChemblLinkAnchor(
                        best.rxcui,
                        best.str_value,
                        best.tty,
                        "PRECISE_ACTIVE_CORE_EXACT",
                        f"{matched_rxcui} -> exact({variant}) -> {best.rxcui}",
                        False,
                    )
        return ChemblLinkAnchor(matched_rxcui, canonical_name, canonical_tty, "PRECISE_ACTIVE_MATCHED_RXCUI", matched_rxcui, False)

    if canonical_tty == "BN" and ingredient_rxcui:
        # The loader can override this by exact ChEMBL trade-name match when a brand
        # points to a specific product/conjugate molecule.
        return ChemblLinkAnchor(
            ingredient_rxcui,
            ingredient_name,
            ingredient_tty,
            "BRAND_TO_RXNORM_INGREDIENT",
            f"{matched_rxcui} -> {ingredient_rxcui}",
            False,
        )

    # Simple salt/form collapse only when the broad ingredient tokens are compatible.
    canonical_tokens = set(tokens(precise_name))
    ingredient_tokens = set(tokens(ingredient_name))
    if (
        canonical_tty == "PIN"
        and ingredient_rxcui
        and ingredient_tty in {"IN", "MIN"}
        and ingredient_tokens
        and ingredient_tokens.issubset(canonical_tokens)
        and (canonical_tokens - ingredient_tokens).issubset(SIMPLE_FORM_MODIFIER_TOKENS)
    ):
        return ChemblLinkAnchor(
            ingredient_rxcui,
            ingredient_name,
            ingredient_tty,
            "SIMPLE_FORM_TO_BROAD_INGREDIENT",
            f"{matched_rxcui} -> {ingredient_rxcui}",
            False,
        )

    if ingredient_rxcui and ingredient_tty in {"IN", "MIN"} and canonical_tty not in {"PIN"}:
        return ChemblLinkAnchor(
            ingredient_rxcui,
            ingredient_name,
            ingredient_tty,
            "NON_PIN_TO_RXNORM_INGREDIENT",
            f"{matched_rxcui} -> {ingredient_rxcui}",
            False,
        )

    return ChemblLinkAnchor(matched_rxcui, canonical_name, canonical_tty, "MATCHED_RXCUI_PRECISE_FALLBACK", matched_rxcui, canonical_tty not in {"IN", "MIN", "PIN"})


class ChemblNameIndex:
    def __init__(self) -> None:
        self.exact: dict[str, list[ChemblNameMatch]] = {}
        self.equivalent: dict[str, list[ChemblNameMatch]] = {}

    def add(self, match: ChemblNameMatch) -> None:
        self.exact.setdefault(match.match_key, []).append(match)
        for key in equivalence_keys(match.matched_name):
            if key and key != match.match_key:
                self.equivalent.setdefault(key, []).append(
                    ChemblNameMatch(
                        molregno=match.molregno,
                        chembl_id=match.chembl_id,
                        pref_name=match.pref_name,
                        matched_name=match.matched_name,
                        matched_name_type=match.matched_name_type,
                        match_strategy="CONTROLLED_NAME_EQUIVALENCE",
                        match_key=key,
                    )
                )

    def matches_for(self, query: object) -> tuple[list[ChemblNameMatch], str]:
        exact_key = normalize_match_key(query)
        exact_matches = dedupe_matches(self.exact.get(exact_key, []))
        if exact_matches:
            return exact_matches, "EXACT_NORMALIZED_NAME"
        equivalent_matches: list[ChemblNameMatch] = []
        for key in equivalence_keys(query):
            equivalent_matches.extend(self.equivalent.get(key, []))
        return dedupe_matches(equivalent_matches), "CONTROLLED_NAME_EQUIVALENCE"


def dedupe_matches(matches: Sequence[ChemblNameMatch]) -> list[ChemblNameMatch]:
    seen: set[tuple[int, str, str]] = set()
    out: list[ChemblNameMatch] = []
    for match in matches:
        key = (match.molregno, match.matched_name_type, match.matched_name)
        if key in seen:
            continue
        seen.add(key)
        out.append(match)
    return sorted(out, key=lambda m: (m.chembl_id, m.matched_name_type, m.matched_name))


def build_name_index(conn: sqlite3.Connection) -> ChemblNameIndex:
    index = ChemblNameIndex()
    conn.row_factory = sqlite3.Row

    logger.info("Scanning ChEMBL molecule preferred names")
    for row in conn.execute(
        """
        SELECT molregno, chembl_id, COALESCE(pref_name, '') AS pref_name
        FROM molecule_dictionary
        WHERE pref_name IS NOT NULL AND TRIM(pref_name) <> ''
        """
    ):
        pref_name = clean_text(row["pref_name"])
        index.add(
            ChemblNameMatch(
                molregno=int(row["molregno"]),
                chembl_id=clean_text(row["chembl_id"]),
                pref_name=pref_name,
                matched_name=pref_name,
                matched_name_type="PREF_NAME",
                match_strategy="EXACT_NORMALIZED_NAME",
                match_key=normalize_match_key(pref_name),
            )
        )

    logger.info("Scanning ChEMBL molecule synonyms")
    for row in conn.execute(
        """
        SELECT
            ms.molregno,
            md.chembl_id,
            COALESCE(md.pref_name, '') AS pref_name,
            COALESCE(ms.synonyms, '') AS synonyms,
            COALESCE(ms.syn_type, '') AS syn_type
        FROM molecule_synonyms ms
        JOIN molecule_dictionary md ON md.molregno = ms.molregno
        WHERE ms.synonyms IS NOT NULL AND TRIM(ms.synonyms) <> ''
        """
    ):
        synonym = clean_text(row["synonyms"])
        syn_type = clean_text(row["syn_type"])
        index.add(
            ChemblNameMatch(
                molregno=int(row["molregno"]),
                chembl_id=clean_text(row["chembl_id"]),
                pref_name=clean_text(row["pref_name"]),
                matched_name=synonym,
                matched_name_type=f"SYNONYM:{syn_type}" if syn_type else "SYNONYM",
                match_strategy="EXACT_NORMALIZED_NAME",
                match_key=normalize_match_key(synonym),
            )
        )

    logger.info("Built ChEMBL name index with %d exact keys and %d equivalence keys", len(index.exact), len(index.equivalent))
    return index


def placeholders(values: Sequence[object]) -> str:
    return ",".join("?" for _ in values)


def fetch_molecules(conn: sqlite3.Connection, molregnos: Sequence[int]) -> list[ChemblMolecule]:
    if not molregnos:
        return []
    sql = f"""
    SELECT
        md.molregno,
        md.chembl_id,
        COALESCE(md.pref_name, '') AS pref_name,
        COALESCE(CAST(md.max_phase AS TEXT), '') AS max_phase,
        COALESCE(CAST(md.therapeutic_flag AS TEXT), '') AS therapeutic_flag,
        COALESCE(CAST(md.dosed_ingredient AS TEXT), '') AS dosed_ingredient,
        COALESCE(md.structure_type, '') AS structure_type,
        COALESCE(md.molecule_type, '') AS molecule_type,
        COALESCE(CAST(md.first_approval AS TEXT), '') AS first_approval,
        COALESCE(CAST(md.oral AS TEXT), '') AS oral,
        COALESCE(CAST(md.parenteral AS TEXT), '') AS parenteral,
        COALESCE(CAST(md.topical AS TEXT), '') AS topical,
        COALESCE(CAST(md.black_box_warning AS TEXT), '') AS black_box_warning,
        COALESCE(CAST(md.first_in_class AS TEXT), '') AS first_in_class,
        COALESCE(CAST(md.prodrug AS TEXT), '') AS prodrug,
        COALESCE(CAST(md.withdrawn_flag AS TEXT), '') AS withdrawn_flag,
        COALESCE(CAST(md.chemical_probe AS TEXT), '') AS chemical_probe,
        COALESCE(CAST(md.orphan AS TEXT), '') AS orphan,
        COALESCE(cs.standard_inchi_key, '') AS standard_inchi_key,
        COALESCE(cs.canonical_smiles, '') AS canonical_smiles,
        COALESCE(CAST(cp.full_mwt AS TEXT), '') AS full_mwt,
        COALESCE(cp.full_molformula, '') AS full_molformula
    FROM molecule_dictionary md
    LEFT JOIN compound_structures cs ON cs.molregno = md.molregno
    LEFT JOIN compound_properties cp ON cp.molregno = md.molregno
    WHERE md.molregno IN ({placeholders(molregnos)})
    ORDER BY md.chembl_id
    """
    rows = []
    for row in conn.execute(sql, tuple(molregnos)):
        rows.append(ChemblMolecule(**{key: clean_text(row[key]) for key in row.keys()} | {"molregno": int(row["molregno"])}))
    return rows


def fetch_aliases(conn: sqlite3.Connection, molregnos: Sequence[int]) -> list[ChemblAlias]:
    if not molregnos:
        return []
    sql = f"""
    SELECT molregno, COALESCE(synonyms, '') AS synonym, COALESCE(syn_type, '') AS syn_type
    FROM molecule_synonyms
    WHERE molregno IN ({placeholders(molregnos)})
    ORDER BY molregno, syn_type, synonyms
    """
    return [
        ChemblAlias(
            molregno=int(row["molregno"]),
            synonym=clean_text(row["synonym"]),
            synonym_normalized=normalize_match_key(row["synonym"]),
            syn_type=clean_text(row["syn_type"]),
        )
        for row in conn.execute(sql, tuple(molregnos))
    ]


def fetch_relations(conn: sqlite3.Connection, molregnos: Sequence[int]) -> list[ChemblMoleculeRelation]:
    if not molregnos:
        return []
    sql = f"""
    SELECT molregno, parent_molregno, active_molregno
    FROM molecule_hierarchy
    WHERE molregno IN ({placeholders(molregnos)})
    """
    out: list[ChemblMoleculeRelation] = []
    for row in conn.execute(sql, tuple(molregnos)):
        molregno = int(row["molregno"])
        parent = int(row["parent_molregno"]) if row["parent_molregno"] is not None else molregno
        active = int(row["active_molregno"]) if row["active_molregno"] is not None else molregno
        out.append(ChemblMoleculeRelation(molregno=molregno, related_molregno=molregno, relation_type="EXACT"))
        if parent != molregno:
            out.append(ChemblMoleculeRelation(molregno=molregno, related_molregno=parent, relation_type="PARENT"))
        if active != molregno and active != parent:
            out.append(ChemblMoleculeRelation(molregno=molregno, related_molregno=active, relation_type="ACTIVE"))
    return dedupe_relations(out)


def dedupe_relations(rows: Sequence[ChemblMoleculeRelation]) -> list[ChemblMoleculeRelation]:
    seen = set()
    out = []
    for row in rows:
        key = (row.molregno, row.related_molregno, row.relation_type)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return sorted(out, key=lambda r: (r.molregno, r.relation_type, r.related_molregno))


def fetch_mechanisms(conn: sqlite3.Connection, molregnos: Sequence[int]) -> list[ChemblMechanism]:
    if not molregnos:
        return []
    sql = f"""
    SELECT
        dm.molregno,
        COALESCE(CAST(dm.mec_id AS TEXT), '') AS mec_id,
        COALESCE(CAST(dm.record_id AS TEXT), '') AS record_id,
        COALESCE(dm.mechanism_of_action, '') AS mechanism_of_action,
        COALESCE(dm.action_type, '') AS action_type,
        COALESCE(CAST(dm.direct_interaction AS TEXT), '') AS direct_interaction,
        COALESCE(CAST(dm.molecular_mechanism AS TEXT), '') AS molecular_mechanism,
        COALESCE(CAST(dm.disease_efficacy AS TEXT), '') AS disease_efficacy,
        COALESCE(dm.mechanism_comment, '') AS mechanism_comment,
        COALESCE(dm.selectivity_comment, '') AS selectivity_comment,
        COALESCE(dm.binding_site_comment, '') AS binding_site_comment,
        COALESCE(td.chembl_id, '') AS target_chembl_id,
        COALESCE(td.pref_name, '') AS target_pref_name,
        COALESCE(td.target_type, '') AS target_type,
        COALESCE(td.organism, '') AS target_organism,
        COALESCE(GROUP_CONCAT(DISTINCT cs.accession), '') AS target_accessions,
        COALESCE(GROUP_CONCAT(DISTINCT cs.description), '') AS target_component_descriptions
    FROM drug_mechanism dm
    LEFT JOIN target_dictionary td ON td.tid = dm.tid
    LEFT JOIN target_components tc ON tc.tid = dm.tid
    LEFT JOIN component_sequences cs ON cs.component_id = tc.component_id
    WHERE dm.molregno IN ({placeholders(molregnos)})
    GROUP BY dm.mec_id
    ORDER BY dm.molregno, dm.mec_id
    """
    return [ChemblMechanism(**{key: clean_text(row[key]) for key in row.keys()} | {"molregno": int(row["molregno"])}) for row in conn.execute(sql, tuple(molregnos))]


def fetch_indications(conn: sqlite3.Connection, molregnos: Sequence[int]) -> list[ChemblIndication]:
    if not molregnos:
        return []
    sql = f"""
    SELECT
        molregno,
        COALESCE(CAST(drugind_id AS TEXT), '') AS drugind_id,
        COALESCE(CAST(record_id AS TEXT), '') AS record_id,
        COALESCE(CAST(max_phase_for_ind AS TEXT), '') AS max_phase_for_ind,
        COALESCE(mesh_id, '') AS mesh_id,
        COALESCE(mesh_heading, '') AS mesh_heading,
        COALESCE(efo_id, '') AS efo_id,
        COALESCE(efo_term, '') AS efo_term
    FROM drug_indication
    WHERE molregno IN ({placeholders(molregnos)})
    ORDER BY molregno, CAST(COALESCE(max_phase_for_ind, 0) AS INTEGER) DESC, efo_term, mesh_heading
    """
    out = []
    for row in conn.execute(sql, tuple(molregnos)):
        out.append(
            ChemblIndication(
                molregno=int(row["molregno"]),
                drugind_id=clean_text(row["drugind_id"]),
                record_id=clean_text(row["record_id"]),
                max_phase_for_ind=clean_text(row["max_phase_for_ind"]),
                mesh_id=clean_text(row["mesh_id"]),
                mesh_heading=clean_text(row["mesh_heading"]),
                efo_id=clean_text(row["efo_id"]),
                efo_term=clean_text(row["efo_term"]),
                is_oncology=is_oncology_indication(row["mesh_heading"], row["efo_term"]),
            )
        )
    return out


def fetch_atc(conn: sqlite3.Connection, molregnos: Sequence[int]) -> list[ChemblAtc]:
    if not molregnos:
        return []
    sql = f"""
    SELECT
        mac.molregno,
        COALESCE(CAST(mac.mol_atc_id AS TEXT), '') AS mol_atc_id,
        COALESCE(mac.level5, '') AS level5,
        COALESCE(ac.who_name, '') AS who_name,
        COALESCE(ac.level1, '') AS level1,
        COALESCE(ac.level2, '') AS level2,
        COALESCE(ac.level3, '') AS level3,
        COALESCE(ac.level4, '') AS level4,
        COALESCE(ac.level1_description, '') AS level1_description,
        COALESCE(ac.level2_description, '') AS level2_description,
        COALESCE(ac.level3_description, '') AS level3_description,
        COALESCE(ac.level4_description, '') AS level4_description
    FROM molecule_atc_classification mac
    LEFT JOIN atc_classification ac ON ac.level5 = mac.level5
    WHERE mac.molregno IN ({placeholders(molregnos)})
    ORDER BY mac.molregno, mac.level5
    """
    return [ChemblAtc(**{key: clean_text(row[key]) for key in row.keys()} | {"molregno": int(row["molregno"])}) for row in conn.execute(sql, tuple(molregnos))]


def fetch_warnings(conn: sqlite3.Connection, molregnos: Sequence[int]) -> list[ChemblWarning]:
    if not molregnos:
        return []
    sql = f"""
    SELECT
        molregno,
        COALESCE(CAST(warning_id AS TEXT), '') AS warning_id,
        COALESCE(CAST(record_id AS TEXT), '') AS record_id,
        COALESCE(warning_type, '') AS warning_type,
        COALESCE(warning_class, '') AS warning_class,
        COALESCE(warning_description, '') AS warning_description,
        COALESCE(warning_country, '') AS warning_country,
        COALESCE(CAST(warning_year AS TEXT), '') AS warning_year,
        COALESCE(efo_term, '') AS efo_term,
        COALESCE(efo_id, '') AS efo_id,
        COALESCE(efo_id_for_warning_class, '') AS efo_id_for_warning_class
    FROM drug_warning
    WHERE molregno IN ({placeholders(molregnos)})
    ORDER BY molregno, warning_type, warning_class
    """
    return [ChemblWarning(**{key: clean_text(row[key]) for key in row.keys()} | {"molregno": int(row["molregno"])}) for row in conn.execute(sql, tuple(molregnos))]


def build_chembl_records(sqlite_path: Path, query_values: Sequence[str]) -> ChemblBuildResult:
    if not sqlite_path.exists():
        raise FileNotFoundError(f"ChEMBL SQLite database not found: {sqlite_path}")

    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    try:
        name_index = build_name_index(conn)

        exact_by_query: dict[str, list[ChemblNameMatch]] = {}
        equiv_by_query: dict[str, list[ChemblNameMatch]] = {}
        matched_molregnos: set[int] = set()

        for query in ordered_unique(query_values):
            key = normalize_match_key(query)
            matches, strategy = name_index.matches_for(query)
            if strategy == "EXACT_NORMALIZED_NAME":
                exact_by_query[key] = matches
            else:
                equiv_by_query[key] = matches
            matched_molregnos.update(match.molregno for match in matches)

        relations = fetch_relations(conn, sorted(matched_molregnos))
        related_molregnos = {rel.related_molregno for rel in relations}
        all_molregnos = sorted(matched_molregnos | related_molregnos)

        molecules = fetch_molecules(conn, all_molregnos)
        aliases = fetch_aliases(conn, all_molregnos)
        mechanisms = fetch_mechanisms(conn, all_molregnos)
        indications = fetch_indications(conn, all_molregnos)
        atc_rows = fetch_atc(conn, all_molregnos)
        warnings = fetch_warnings(conn, all_molregnos)

        logger.info(
            "Built ChEMBL records: %d query values, %d directly matched molecules, %d molecules incl hierarchy, %d indications, %d mechanisms",
            len(ordered_unique(query_values)),
            len(matched_molregnos),
            len(molecules),
            len(indications),
            len(mechanisms),
        )
        return ChemblBuildResult(
            molecules=molecules,
            aliases=aliases,
            relations=relations,
            mechanisms=mechanisms,
            indications=indications,
            atc_rows=atc_rows,
            warnings=warnings,
            exact_matches_by_query_key=exact_by_query,
            equivalent_matches_by_query_key=equiv_by_query,
        )
    finally:
        conn.close()


def to_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
