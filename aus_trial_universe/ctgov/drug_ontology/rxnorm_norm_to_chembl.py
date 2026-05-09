from __future__ import annotations

"""Link RxNorm-normalized CT.gov intervention rows to ChEMBL.

This module is downstream of:
    CT.gov intervention extraction/normalisation
    -> RXNCONSO/RXCUI mapping
    -> ATC/FDA/POTTR enrichment, if enabled

ChEMBL is not used as an RxNorm crosswalk. It is linked by controlled exact
lexical matching from the identity layer into ChEMBL molecule names/synonyms, then
enriched with mechanism, target, and indication data from the ChEMBL SQLite dump.

Design rules:
    - Do not use substring matching.
    - Prefer resolved_drug_names, then resolved_input_terms.
    - Include preserved full aliases only as exact-match candidates.
    - Match exact normalized molecule_dictionary.pref_name first.
    - Match exact normalized molecule_synonyms.synonyms second.
    - Try salt/form-stripped exact matching only as fallback.
    - Keep compact feature columns on interventions and detailed evidence on chembl_debug.
    - Any paired/1:1 facts in the main sheet must be represented as aligned pair strings,
      not as independently de-duplicated parallel columns.
"""

import argparse
import json
import logging
import re
import sqlite3
import tarfile
import unicodedata
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

from drug_ontology_schema import (
    CHEMBL_MAIN_OUTPUT_COLUMNS,
    COL_CHEMBL_ACTION_TYPES,
    COL_CHEMBL_IDS,
    COL_CHEMBL_INDICATION_MAX_PHASES,
    COL_CHEMBL_INDICATION_PHASE_PAIRS,
    COL_CHEMBL_INDICATIONS,
    COL_CHEMBL_LINK_STATUS,
    COL_CHEMBL_LOOKUP_TERMS,
    COL_CHEMBL_MANUAL_REVIEW_NEEDED,
    COL_CHEMBL_MATCHED_TERMS,
    COL_CHEMBL_MAX_PHASES,
    COL_CHEMBL_MECHANISM_TARGET_PAIRS,
    COL_CHEMBL_MECHANISMS,
    COL_CHEMBL_MOLREGNOS,
    COL_CHEMBL_MOLECULE_TYPES,
    COL_CHEMBL_PREF_NAMES,
    COL_CHEMBL_TARGET_IDS,
    COL_CHEMBL_TARGET_NAMES,
    COL_CHEMBL_TARGET_ORGANISMS,
    COL_CHEMBL_TARGET_TYPES,
    COL_CHEMBL_THERAPEUTIC_FLAGS,
    COL_CHEMBL_WITHDRAWN_FLAGS,
    COL_INTERVENTION_ALL_ALIASES_NORMALISED_FULL,
    COL_INTERVENTION_INDEX,
    COL_NCT_ID,
    COL_RESOLVED_DRUG_NAMES,
    COL_RESOLVED_INPUT_TERMS,
    COL_RXNORM_RXCUIS,
    SHEET_CHEMBL_DEBUG,
    SHEET_INTERVENTIONS,
    join_display_values,
    split_display_values,
)

logger = logging.getLogger(__name__)

TRADEMARK_RE = re.compile(r"[®™\ufe0f]")
DASH_RE = re.compile(r"[\u2010\u2011\u2012\u2013\u2014\u2212]")
MULTISPACE_RE = re.compile(r"\s+")
EDGE_PUNCT_RE = re.compile(r"^[\s\.,;:/\\+\-]+|[\s\.,;:/\\+\-]+$")
SQLITE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}

SALT_RE = re.compile(
    r"\b(?:hydrochloride|hcl|sodium|potassium|acetate|maleate|mesylate|besylate|tosylate|"
    r"tartrate|citrate|phosphate|diphosphate|fumarate|succinate|nitrate|sulfate|sulphate|"
    r"hydrobromide|bitartrate|carbonate|bicarbonate|chloride|bromide|iodide|calcium|"
    r"magnesium|zinc|dimaleate|mesilate|erbumine|hemifumarate|dihydrochloride|"
    r"monohydrate|hydrate|anhydrous)\b",
    re.IGNORECASE,
)

LOW_VALUE_LOOKUP_TERMS = {
    "placebo",
    "standard of care",
    "soc",
    "chemotherapy",
    "immunotherapy",
    "therapy",
    "treatment",
    "drug",
    "study drug",
    "regimen",
    "combination",
    "rescue medication",
    "supportive care",
}


@dataclass(frozen=True)
class ChemblMolecule:
    molregno: int
    chembl_id: str
    pref_name: str
    molecule_type: str
    max_phase: str
    therapeutic_flag: str
    withdrawn_flag: str


@dataclass(frozen=True)
class ChemblAliasHit:
    input_term: str
    matched_chembl_term: str
    match_stage: str
    match_status: str
    molregno: int
    chembl_id: str
    pref_name: str
    synonym_type: str
    candidate_count: int


@dataclass(frozen=True)
class ChemblMechanism:
    molregno: int
    mechanism_of_action: str
    action_type: str
    target_chembl_id: str
    target_name: str
    target_type: str
    target_organism: str


@dataclass(frozen=True)
class ChemblIndication:
    molregno: int
    mesh_id: str
    mesh_heading: str
    efo_id: str
    efo_term: str
    max_phase_for_ind: str


class ChemblIndex:
    def __init__(
        self,
        molecules: Dict[int, ChemblMolecule],
        pref_name_index: Dict[str, List[int]],
        synonym_index: Dict[str, List[Tuple[int, str, str]]],
        mechanisms_by_molregno: Dict[int, List[ChemblMechanism]],
        indications_by_molregno: Dict[int, List[ChemblIndication]],
    ) -> None:
        self.molecules = molecules
        self.pref_name_index = pref_name_index
        self.synonym_index = synonym_index
        self.mechanisms_by_molregno = mechanisms_by_molregno
        self.indications_by_molregno = indications_by_molregno
        self.pref_name_salt_stripped_index = build_salt_stripped_pref_index(pref_name_index)
        self.synonym_salt_stripped_index = build_salt_stripped_synonym_index(synonym_index)

    @classmethod
    def from_sqlite(cls, sqlite_path: Path) -> "ChemblIndex":
        db_path = resolve_chembl_sqlite_path(sqlite_path)
        logger.info("Opening ChEMBL SQLite database: %s", db_path)

        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            validate_required_tables(conn)
            molecules, pref_name_index = load_molecules(conn)
            synonym_index = load_synonyms(conn)
            mechanisms_by_molregno = load_mechanisms(conn)
            indications_by_molregno = load_indications(conn)

        logger.info(
            "Loaded ChEMBL index: %d molecules, %d pref-name keys, %d synonym keys, "
            "%d mechanism molecule groups, %d indication molecule groups",
            len(molecules),
            len(pref_name_index),
            len(synonym_index),
            len(mechanisms_by_molregno),
            len(indications_by_molregno),
        )
        return cls(
            molecules=molecules,
            pref_name_index=pref_name_index,
            synonym_index=synonym_index,
            mechanisms_by_molregno=mechanisms_by_molregno,
            indications_by_molregno=indications_by_molregno,
        )

    def resolve_term(self, term: str) -> List[ChemblAliasHit]:
        term_clean = normalize_output_text(term)
        term_key = normalize_lookup_text(term_clean)
        if not term_key or term_key in LOW_VALUE_LOOKUP_TERMS:
            return []

        stage_candidates: List[Tuple[str, List[Tuple[int, str, str]]]] = [
            (
                "CHEMBL_STAGE1_PREF_NAME_EXACT",
                [
                    (molregno, self.molecules[molregno].pref_name, "PREF_NAME")
                    for molregno in self.pref_name_index.get(term_key, [])
                    if molregno in self.molecules
                ],
            ),
            ("CHEMBL_STAGE2_SYNONYM_EXACT", self.synonym_index.get(term_key, [])),
        ]

        stripped_key = strip_salts(term_key)
        if stripped_key and stripped_key != term_key:
            stage_candidates.extend(
                [
                    (
                        "CHEMBL_STAGE3_PREF_NAME_SALT_STRIPPED_EXACT",
                        [
                            (molregno, self.molecules[molregno].pref_name, "PREF_NAME_SALT_STRIPPED")
                            for molregno in self.pref_name_salt_stripped_index.get(stripped_key, [])
                            if molregno in self.molecules
                        ],
                    ),
                    (
                        "CHEMBL_STAGE4_SYNONYM_SALT_STRIPPED_EXACT",
                        self.synonym_salt_stripped_index.get(stripped_key, []),
                    ),
                ]
            )

        for stage, candidates in stage_candidates:
            candidates = dedupe_alias_candidates(candidates)
            if not candidates:
                continue
            candidate_count = len({molregno for molregno, _, _ in candidates})
            status = "MATCHED" if candidate_count == 1 else "MULTIPLE_CANDIDATES_REVIEW"
            return [
                ChemblAliasHit(
                    input_term=term_clean,
                    matched_chembl_term=matched_alias,
                    match_stage=stage,
                    match_status=status,
                    molregno=molregno,
                    chembl_id=self.molecules[molregno].chembl_id,
                    pref_name=self.molecules[molregno].pref_name,
                    synonym_type=synonym_type,
                    candidate_count=candidate_count,
                )
                for molregno, matched_alias, synonym_type in candidates
                if molregno in self.molecules
            ]

        return []


# -----------------------------------------------------------------------------
# SQLite loading
# -----------------------------------------------------------------------------


def resolve_chembl_sqlite_path(path: Path) -> Path:
    if not path.exists():
        raise FileNotFoundError(f"ChEMBL SQLite/archive path not found: {path}")

    if path.is_file() and path.suffix.lower() in SQLITE_SUFFIXES:
        return path

    if path.is_file() and path.name.endswith(".tar.gz"):
        extract_dir = path.with_suffix("").with_suffix("")
        extract_dir.mkdir(parents=True, exist_ok=True)
        sqlite_candidates = find_sqlite_files(extract_dir)
        if not sqlite_candidates:
            logger.info("Extracting ChEMBL archive %s to %s", path, extract_dir)
            safe_extract_tar_gz(path, extract_dir)
            sqlite_candidates = find_sqlite_files(extract_dir)
        if not sqlite_candidates:
            raise FileNotFoundError(f"No SQLite database found after extracting {path} to {extract_dir}")
        return sqlite_candidates[0]

    if path.is_dir():
        sqlite_candidates = find_sqlite_files(path)
        if sqlite_candidates:
            return sqlite_candidates[0]

    raise ValueError(
        f"Unsupported ChEMBL input {path}. Provide a .db/.sqlite file, a directory containing one, "
        "or chembl_*_sqlite.tar.gz."
    )


def safe_extract_tar_gz(archive_path: Path, extract_dir: Path) -> None:
    """Extract a tar.gz while preventing path traversal."""
    extract_root = extract_dir.resolve()
    with tarfile.open(archive_path, "r:gz") as tar:
        for member in tar.getmembers():
            target = (extract_root / member.name).resolve()
            if extract_root not in (target, *target.parents):
                raise ValueError(f"Unsafe path in ChEMBL archive: {member.name}")
        tar.extractall(path=extract_root)


def find_sqlite_files(directory: Path) -> List[Path]:
    return sorted(
        [p for p in directory.rglob("*") if p.is_file() and p.suffix.lower() in SQLITE_SUFFIXES],
        key=lambda p: (len(p.parts), str(p)),
    )


def validate_required_tables(conn: sqlite3.Connection) -> None:
    tables = set(row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall())
    required = {
        "molecule_dictionary",
        "molecule_synonyms",
        "drug_mechanism",
        "target_dictionary",
        "drug_indication",
    }
    missing = required - tables
    if missing:
        raise KeyError(f"ChEMBL SQLite database missing required tables: {sorted(missing)}")


def table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def sql_select_columns(conn: sqlite3.Connection, table_name: str, columns: Sequence[str]) -> str:
    available = table_columns(conn, table_name)
    pieces = [col if col in available else f"'' AS {col}" for col in columns]
    return ", ".join(pieces)


def load_molecules(conn: sqlite3.Connection) -> Tuple[Dict[int, ChemblMolecule], Dict[str, List[int]]]:
    columns = [
        "molregno",
        "chembl_id",
        "pref_name",
        "molecule_type",
        "max_phase",
        "therapeutic_flag",
        "withdrawn_flag",
    ]
    sql = f"SELECT {sql_select_columns(conn, 'molecule_dictionary', columns)} FROM molecule_dictionary"
    molecules: Dict[int, ChemblMolecule] = {}
    pref_name_index: Dict[str, List[int]] = defaultdict(list)

    for row in conn.execute(sql):
        molregno = int(row["molregno"])
        molecule = ChemblMolecule(
            molregno=molregno,
            chembl_id=clean_text(row["chembl_id"]),
            pref_name=normalize_output_text(row["pref_name"]),
            molecule_type=clean_text(row["molecule_type"]),
            max_phase=clean_text(row["max_phase"]),
            therapeutic_flag=clean_text(row["therapeutic_flag"]),
            withdrawn_flag=clean_text(row["withdrawn_flag"]),
        )
        molecules[molregno] = molecule
        key = normalize_lookup_text(molecule.pref_name)
        if key:
            pref_name_index[key].append(molregno)

    return molecules, pref_name_index


def load_synonyms(conn: sqlite3.Connection) -> Dict[str, List[Tuple[int, str, str]]]:
    columns = ["molregno", "synonyms", "syn_type"]
    sql = f"SELECT {sql_select_columns(conn, 'molecule_synonyms', columns)} FROM molecule_synonyms"
    synonym_index: Dict[str, List[Tuple[int, str, str]]] = defaultdict(list)

    for row in conn.execute(sql):
        synonym = normalize_output_text(row["synonyms"])
        key = normalize_lookup_text(synonym)
        if not key:
            continue
        synonym_index[key].append((int(row["molregno"]), synonym, clean_text(row["syn_type"])))

    return synonym_index


def load_mechanisms(conn: sqlite3.Connection) -> Dict[int, List[ChemblMechanism]]:
    mech_cols = ["molregno", "mechanism_of_action", "action_type", "tid"]
    target_cols = ["tid", "chembl_id", "pref_name", "target_type", "organism"]
    mech_select = sql_select_columns(conn, "drug_mechanism", mech_cols)
    target_select = sql_select_columns(conn, "target_dictionary", target_cols)

    target_by_tid: Dict[int, sqlite3.Row] = {}
    for row in conn.execute(f"SELECT {target_select} FROM target_dictionary"):
        try:
            target_by_tid[int(row["tid"])] = row
        except (TypeError, ValueError):
            continue

    out: Dict[int, List[ChemblMechanism]] = defaultdict(list)
    for row in conn.execute(f"SELECT {mech_select} FROM drug_mechanism"):
        try:
            molregno = int(row["molregno"])
        except (TypeError, ValueError):
            continue
        try:
            tid = int(row["tid"])
        except (TypeError, ValueError):
            tid = -1
        target = target_by_tid.get(tid)
        out[molregno].append(
            ChemblMechanism(
                molregno=molregno,
                mechanism_of_action=clean_text(row["mechanism_of_action"]),
                action_type=clean_text(row["action_type"]),
                target_chembl_id=clean_text(target["chembl_id"]) if target is not None else "",
                target_name=clean_text(target["pref_name"]) if target is not None else "",
                target_type=clean_text(target["target_type"]) if target is not None else "",
                target_organism=clean_text(target["organism"]) if target is not None else "",
            )
        )

    return out


def load_indications(conn: sqlite3.Connection) -> Dict[int, List[ChemblIndication]]:
    columns = ["molregno", "mesh_id", "mesh_heading", "efo_id", "efo_term", "max_phase_for_ind"]
    sql = f"SELECT {sql_select_columns(conn, 'drug_indication', columns)} FROM drug_indication"
    out: Dict[int, List[ChemblIndication]] = defaultdict(list)

    for row in conn.execute(sql):
        try:
            molregno = int(row["molregno"])
        except (TypeError, ValueError):
            continue
        out[molregno].append(
            ChemblIndication(
                molregno=molregno,
                mesh_id=clean_text(row["mesh_id"]),
                mesh_heading=clean_text(row["mesh_heading"]),
                efo_id=clean_text(row["efo_id"]),
                efo_term=clean_text(row["efo_term"]),
                max_phase_for_ind=clean_text(row["max_phase_for_ind"]),
            )
        )

    return out


# -----------------------------------------------------------------------------
# Matching and normalization
# -----------------------------------------------------------------------------


def clean_text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "<na>"}:
        return ""
    return text


def normalize_lookup_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", clean_text(value))
    text = TRADEMARK_RE.sub("", text)
    text = DASH_RE.sub("-", text)
    text = text.replace("，", ",")
    text = text.lower().strip()
    text = re.sub(r"\s*/\s*", "/", text)
    text = re.sub(r"\s*;\s*", "; ", text)
    text = re.sub(r"\s*,\s*", ", ", text)
    text = MULTISPACE_RE.sub(" ", text)
    return EDGE_PUNCT_RE.sub("", text).strip()


def normalize_output_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", clean_text(value))
    text = TRADEMARK_RE.sub("", text)
    text = DASH_RE.sub("-", text)
    text = MULTISPACE_RE.sub(" ", text.strip())
    return EDGE_PUNCT_RE.sub("", text).strip()


def strip_salts(value: object) -> str:
    text = normalize_lookup_text(value)
    stripped = SALT_RE.sub(" ", text)
    stripped = MULTISPACE_RE.sub(" ", stripped)
    return EDGE_PUNCT_RE.sub("", stripped).strip()


def ordered_unique_ints(values: Iterable[int]) -> List[int]:
    out: List[int] = []
    seen = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def ordered_unique_normalized(values: Iterable[object]) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values:
        clean = normalize_output_text(value)
        if not clean:
            continue
        key = normalize_lookup_text(clean)
        if key in seen:
            continue
        seen.add(key)
        out.append(clean)
    return out


def build_salt_stripped_pref_index(pref_index: Dict[str, List[int]]) -> Dict[str, List[int]]:
    out: Dict[str, List[int]] = defaultdict(list)
    for key, molregnos in pref_index.items():
        stripped = strip_salts(key)
        if stripped and stripped != key:
            out[stripped].extend(molregnos)
    return {key: ordered_unique_ints(values) for key, values in out.items()}


def build_salt_stripped_synonym_index(
    synonym_index: Dict[str, List[Tuple[int, str, str]]]
) -> Dict[str, List[Tuple[int, str, str]]]:
    out: Dict[str, List[Tuple[int, str, str]]] = defaultdict(list)
    for key, candidates in synonym_index.items():
        stripped = strip_salts(key)
        if stripped and stripped != key:
            out[stripped].extend(candidates)
    return {key: dedupe_alias_candidates(values) for key, values in out.items()}


def dedupe_alias_candidates(candidates: Sequence[Tuple[int, str, str]]) -> List[Tuple[int, str, str]]:
    out: List[Tuple[int, str, str]] = []
    seen = set()
    for molregno, matched_alias, synonym_type in candidates:
        key = (molregno, normalize_lookup_text(matched_alias), normalize_lookup_text(synonym_type))
        if key in seen:
            continue
        seen.add(key)
        out.append((molregno, matched_alias, synonym_type))
    return sorted(out, key=lambda x: (x[0], normalize_lookup_text(x[1]), normalize_lookup_text(x[2])))


def is_low_value_lookup_term(term: str) -> bool:
    key = normalize_lookup_text(term)
    return not key or key in LOW_VALUE_LOOKUP_TERMS


def collect_lookup_terms(record: Dict[str, object]) -> List[str]:
    terms: List[str] = []
    for col in (
        COL_RESOLVED_DRUG_NAMES,
        COL_RESOLVED_INPUT_TERMS,
        COL_INTERVENTION_ALL_ALIASES_NORMALISED_FULL,
    ):
        terms.extend(split_display_values(record.get(col, "")))
    return [term for term in ordered_unique_normalized(terms) if not is_low_value_lookup_term(term)]


def resolve_terms(terms: Sequence[str], index: ChemblIndex) -> Tuple[List[ChemblAliasHit], List[str], Dict[str, int]]:
    hits: List[ChemblAliasHit] = []
    unmatched: List[str] = []
    candidate_counts: Dict[str, int] = {}

    for term in terms:
        term_hits = index.resolve_term(term)
        candidate_counts[term] = len({hit.molregno for hit in term_hits})
        if term_hits:
            hits.extend(term_hits)
        else:
            unmatched.append(term)

    return dedupe_hits(hits), ordered_unique_normalized(unmatched), candidate_counts


def dedupe_hits(hits: Sequence[ChemblAliasHit]) -> List[ChemblAliasHit]:
    out: List[ChemblAliasHit] = []
    seen = set()
    for hit in hits:
        key = (
            normalize_lookup_text(hit.input_term),
            hit.molregno,
            normalize_lookup_text(hit.matched_chembl_term),
            normalize_lookup_text(hit.match_stage),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(hit)
    return out


def matched_molregnos(hits: Sequence[ChemblAliasHit]) -> List[int]:
    return ordered_unique_ints(hit.molregno for hit in hits)


def chembl_link_status(lookup_terms: Sequence[str], hits: Sequence[ChemblAliasHit]) -> str:
    if not lookup_terms:
        return "NO_LOOKUP_TERMS"
    if not hits:
        return "NO_CHEMBL_MATCH"

    hit_statuses = {hit.match_status for hit in hits}
    matched_terms = {normalize_lookup_text(hit.input_term) for hit in hits}
    lookup_keys = {normalize_lookup_text(term) for term in lookup_terms}

    if "MULTIPLE_CANDIDATES_REVIEW" in hit_statuses:
        if matched_terms == lookup_keys:
            return "MATCHED_CHEMBL_WITH_REVIEW"
        return "PARTIAL_CHEMBL_MATCH_WITH_REVIEW"
    if matched_terms == lookup_keys:
        return "MATCHED_CHEMBL"
    return "PARTIAL_CHEMBL_MATCH"


# -----------------------------------------------------------------------------
# Alignment-aware display helpers
# -----------------------------------------------------------------------------


def indication_name(indication: ChemblIndication) -> str:
    return clean_text(indication.mesh_heading or indication.efo_term)


def indication_phase_pair(indication: ChemblIndication) -> str:
    name = indication_name(indication)
    if not name:
        return ""
    phase = clean_text(indication.max_phase_for_ind)
    return f"{name} [phase={phase}]" if phase else name


def mechanism_target_pair(mechanism: ChemblMechanism) -> str:
    pieces: List[str] = []
    if mechanism.mechanism_of_action:
        pieces.append(mechanism.mechanism_of_action)
    if mechanism.action_type:
        pieces.append(f"action={mechanism.action_type}")
    if mechanism.target_name:
        target = mechanism.target_name
        if mechanism.target_chembl_id:
            target = f"{target} ({mechanism.target_chembl_id})"
        pieces.append(f"target={target}")
    return " ; ".join(pieces)


def build_mapping_json(
    hits: Sequence[ChemblAliasHit],
    mechanisms: Sequence[ChemblMechanism],
    indications: Sequence[ChemblIndication],
    candidate_counts: Dict[str, int],
) -> str:
    payload = {
        "alias_matches": [asdict(hit) for hit in hits],
        "mechanisms": [asdict(mechanism) for mechanism in mechanisms],
        "indications": [asdict(indication) for indication in indications],
        "candidate_counts": candidate_counts,
    }
    return json.dumps(payload, ensure_ascii=False)


# -----------------------------------------------------------------------------
# DataFrame / workbook output
# -----------------------------------------------------------------------------


def build_main_row(
    record: Dict[str, object],
    lookup_terms: Sequence[str],
    hits: Sequence[ChemblAliasHit],
    index: ChemblIndex,
) -> Dict[str, object]:
    molregnos = matched_molregnos(hits)
    molecules = [index.molecules[molregno] for molregno in molregnos if molregno in index.molecules]
    mechanisms = [m for molregno in molregnos for m in index.mechanisms_by_molregno.get(molregno, [])]
    indications = [i for molregno in molregnos for i in index.indications_by_molregno.get(molregno, [])]
    status = chembl_link_status(lookup_terms, hits)

    row = dict(record)
    row[COL_CHEMBL_LINK_STATUS] = status
    row[COL_CHEMBL_MANUAL_REVIEW_NEEDED] = status in {
        "NO_CHEMBL_MATCH",
        "PARTIAL_CHEMBL_MATCH",
        "MATCHED_CHEMBL_WITH_REVIEW",
        "PARTIAL_CHEMBL_MATCH_WITH_REVIEW",
    }
    row[COL_CHEMBL_LOOKUP_TERMS] = join_display_values(lookup_terms)
    row[COL_CHEMBL_MATCHED_TERMS] = join_display_values(hit.input_term for hit in hits)
    row[COL_CHEMBL_MOLREGNOS] = join_display_values(str(molregno) for molregno in molregnos)
    row[COL_CHEMBL_IDS] = join_display_values(molecule.chembl_id for molecule in molecules)
    row[COL_CHEMBL_PREF_NAMES] = join_display_values(molecule.pref_name for molecule in molecules)
    row[COL_CHEMBL_MAX_PHASES] = join_display_values(molecule.max_phase for molecule in molecules)
    row[COL_CHEMBL_MOLECULE_TYPES] = join_display_values(molecule.molecule_type for molecule in molecules)
    row[COL_CHEMBL_THERAPEUTIC_FLAGS] = join_display_values(molecule.therapeutic_flag for molecule in molecules)
    row[COL_CHEMBL_WITHDRAWN_FLAGS] = join_display_values(molecule.withdrawn_flag for molecule in molecules)
    row[COL_CHEMBL_MECHANISMS] = join_display_values(m.mechanism_of_action for m in mechanisms)
    row[COL_CHEMBL_ACTION_TYPES] = join_display_values(m.action_type for m in mechanisms)
    row[COL_CHEMBL_TARGET_IDS] = join_display_values(m.target_chembl_id for m in mechanisms)
    row[COL_CHEMBL_TARGET_NAMES] = join_display_values(m.target_name for m in mechanisms)
    row[COL_CHEMBL_TARGET_TYPES] = join_display_values(m.target_type for m in mechanisms)
    row[COL_CHEMBL_TARGET_ORGANISMS] = join_display_values(m.target_organism for m in mechanisms)
    row[COL_CHEMBL_MECHANISM_TARGET_PAIRS] = join_display_values(
        mechanism_target_pair(mechanism) for mechanism in mechanisms
    )
    row[COL_CHEMBL_INDICATIONS] = join_display_values(indication_name(indication) for indication in indications)
    row[COL_CHEMBL_INDICATION_PHASE_PAIRS] = join_display_values(
        indication_phase_pair(indication) for indication in indications
    )
    # Summary only: unique phase values across all indications. Do not interpret as positionally aligned.
    row[COL_CHEMBL_INDICATION_MAX_PHASES] = join_display_values(
        indication.max_phase_for_ind for indication in indications
    )
    return row


def build_debug_rows(
    record: Dict[str, object],
    lookup_terms: Sequence[str],
    hits: Sequence[ChemblAliasHit],
    unmatched_terms: Sequence[str],
    candidate_counts: Dict[str, int],
    index: ChemblIndex,
) -> List[Dict[str, object]]:
    molregnos = matched_molregnos(hits)
    mechanisms = [m for molregno in molregnos for m in index.mechanisms_by_molregno.get(molregno, [])]
    indications = [i for molregno in molregnos for i in index.indications_by_molregno.get(molregno, [])]
    status = chembl_link_status(lookup_terms, hits)
    mapping_payload = build_mapping_json(hits, mechanisms, indications, candidate_counts)

    base = {
        COL_NCT_ID: record.get(COL_NCT_ID, ""),
        COL_INTERVENTION_INDEX: record.get(COL_INTERVENTION_INDEX, ""),
        COL_RXNORM_RXCUIS: record.get(COL_RXNORM_RXCUIS, ""),
        COL_CHEMBL_LINK_STATUS: status,
        "lookup_terms": join_display_values(lookup_terms),
        "unmatched_terms": join_display_values(unmatched_terms),
        "candidate_counts_json": json.dumps(candidate_counts, ensure_ascii=False),
        "mapping_json": mapping_payload,
    }

    if not hits:
        return [
            {
                **base,
                "input_term": "",
                "matched_chembl_term": "",
                "match_stage": "NO_MATCH" if lookup_terms else "NO_LOOKUP_TERMS",
                "match_status": status,
                "molregno": "",
                "chembl_id": "",
                "pref_name": "",
                "synonym_type": "",
                "candidate_count": 0,
                "molecule_json": "{}",
                "mechanisms": "",
                "mechanism_target_pairs": "",
                "indications": "",
                "indication_phase_pairs": "",
            }
        ]

    rows: List[Dict[str, object]] = []
    for hit in hits:
        molecule = index.molecules.get(hit.molregno)
        hit_mechanisms = index.mechanisms_by_molregno.get(hit.molregno, [])
        hit_indications = index.indications_by_molregno.get(hit.molregno, [])
        rows.append(
            {
                **base,
                "input_term": hit.input_term,
                "matched_chembl_term": hit.matched_chembl_term,
                "match_stage": hit.match_stage,
                "match_status": hit.match_status,
                "molregno": hit.molregno,
                "chembl_id": hit.chembl_id,
                "pref_name": hit.pref_name,
                "synonym_type": hit.synonym_type,
                "candidate_count": hit.candidate_count,
                "molecule_json": json.dumps(asdict(molecule), ensure_ascii=False) if molecule else "{}",
                "mechanisms": join_display_values(m.mechanism_of_action for m in hit_mechanisms),
                "action_types": join_display_values(m.action_type for m in hit_mechanisms),
                "targets": join_display_values(m.target_name for m in hit_mechanisms),
                "target_ids": join_display_values(m.target_chembl_id for m in hit_mechanisms),
                "mechanism_target_pairs": join_display_values(mechanism_target_pair(m) for m in hit_mechanisms),
                "indications": join_display_values(indication_name(i) for i in hit_indications),
                "indication_phase_pairs": join_display_values(indication_phase_pair(i) for i in hit_indications),
                "indication_max_phases_summary": join_display_values(i.max_phase_for_ind for i in hit_indications),
            }
        )
    return rows


def map_chembl_dataframe(df_input: pd.DataFrame, index: ChemblIndex) -> Tuple[pd.DataFrame, pd.DataFrame]:
    missing = [col for col in (COL_RESOLVED_DRUG_NAMES, COL_RESOLVED_INPUT_TERMS) if col not in df_input.columns]
    if missing:
        raise KeyError(
            "Input workbook is missing required RxNorm identity-layer ChEMBL lookup columns: "
            f"{missing}. Run ctgov_to_rxnconso.py first."
        )

    main_rows: List[Dict[str, object]] = []
    debug_rows: List[Dict[str, object]] = []

    for row_number, record in enumerate(df_input.fillna("").to_dict(orient="records"), start=1):
        lookup_terms = collect_lookup_terms(record)
        hits, unmatched_terms, candidate_counts = resolve_terms(lookup_terms, index)
        main_rows.append(build_main_row(record, lookup_terms, hits, index))
        debug_rows.extend(build_debug_rows(record, lookup_terms, hits, unmatched_terms, candidate_counts, index))

        if row_number % 1000 == 0:
            logger.info("ChEMBL-linked %d intervention rows", row_number)

    df_main = pd.DataFrame(main_rows)
    leading = [col for col in df_main.columns if col not in CHEMBL_MAIN_OUTPUT_COLUMNS]
    chembl_cols = [col for col in CHEMBL_MAIN_OUTPUT_COLUMNS if col in df_main.columns]
    df_main = df_main[leading + chembl_cols]
    return df_main, pd.DataFrame(debug_rows)


def copy_non_target_sheets(input_excel: Path, writer: pd.ExcelWriter, replace_sheets: set[str]) -> None:
    xls = pd.ExcelFile(input_excel)
    for sheet in xls.sheet_names:
        if sheet in replace_sheets:
            continue
        df_sheet = pd.read_excel(input_excel, sheet_name=sheet, dtype="string").fillna("")
        df_sheet.to_excel(writer, sheet_name=sheet, index=False)


def process_workbook(
    input_excel: Path,
    chembl_sqlite: Path,
    output_excel: Path,
    sheet_name: str = SHEET_INTERVENTIONS,
) -> None:
    if not input_excel.exists():
        raise FileNotFoundError(f"Input workbook not found: {input_excel}")

    xls = pd.ExcelFile(input_excel)
    if sheet_name not in xls.sheet_names:
        raise KeyError(f"Worksheet {sheet_name!r} not found. Available sheets: {xls.sheet_names}")

    logger.info("Loading ChEMBL from %s", chembl_sqlite)
    index = ChemblIndex.from_sqlite(chembl_sqlite)

    df_input = pd.read_excel(input_excel, sheet_name=sheet_name, dtype="string").fillna("")
    logger.info("Read %d rows from %s / %s", len(df_input), input_excel, sheet_name)
    df_main, df_debug = map_chembl_dataframe(df_input, index)

    output_excel.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output_excel, engine="openpyxl") as writer:
        df_main.to_excel(writer, sheet_name=sheet_name, index=False)
        df_debug.to_excel(writer, sheet_name=SHEET_CHEMBL_DEBUG, index=False)
        copy_non_target_sheets(input_excel, writer, replace_sheets={sheet_name, SHEET_CHEMBL_DEBUG})

    logger.info("Wrote ChEMBL-linked workbook to %s", output_excel)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Link RxNorm-normalized CT.gov intervention rows to ChEMBL molecules using exact "
            "name/synonym matching, then append mechanism, target, and indication features."
        )
    )
    parser.add_argument("--input_excel", required=True, type=Path, help="Path to current enriched workbook")
    parser.add_argument(
        "--chembl_sqlite",
        required=True,
        type=Path,
        help="Path to ChEMBL SQLite DB, extracted directory, or chembl_*_sqlite.tar.gz archive",
    )
    parser.add_argument("--output_excel", required=True, type=Path, help="Output workbook path")
    parser.add_argument("--sheet_name", default=SHEET_INTERVENTIONS, help=f"Worksheet name. Default: {SHEET_INTERVENTIONS!r}")
    parser.add_argument(
        "--log_level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    process_workbook(
        input_excel=args.input_excel,
        chembl_sqlite=args.chembl_sqlite,
        output_excel=args.output_excel,
        sheet_name=args.sheet_name,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
