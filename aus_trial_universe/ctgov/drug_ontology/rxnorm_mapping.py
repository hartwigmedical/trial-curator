from __future__ import annotations

import argparse
import csv
import logging
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Set, Tuple

import pandas as pd


logger = logging.getLogger(__name__)

SHEET_INPUT = "interventions"
COL_TYPE = "intervention_type"
COL_ALIASES = "intervention_all_aliases"

TARGET_TYPES = {"DRUG", "BIOLOGICAL", "OTHER", "COMBINATION_PRODUCT"}

RXNCONSO_FILENAME = "RXNCONSO.RRF"
RXNSTY_FILENAME = "RXNSTY.RRF"

RRF_DELIMITER = "|"
AGG_DELIMITER = " | "
MULTISPACE_RE = re.compile(r"\s+")
DASH_RE = re.compile(r"[\u2010\u2011\u2012\u2013\u2014\u2212]")
TRADEMARK_RE = re.compile(r"[®™]")
PUNCT_EDGE_RE = re.compile(r"^[\s\.,;:/\\+\-]+|[\s\.,;:/\\+\-]+$")
PARENS_CONTENT_RE = re.compile(r"\([^)]*\)")
STRENGTH_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|g|kg|mEq|mmol|units|iu|ml|mL|l|L|%)(?:\s*/\s*\d+(?:\.\d+)?\s*(?:ml|mL|l|L))?\b",
    re.IGNORECASE,
)
DOSAGE_FORM_RE = re.compile(
    r"\b(tablet|tablets|capsule|capsules|caplet|caplets|pill|pills|solution|suspension|emulsion|syrup|elixir|ointment|cream|gel|patch|spray|aerosol|powder|granules|infusion|injection|injectable|vial|prefilled syringe|syringe|implant|kit|cream|eye drops|ophthalmic suspension|oral solution|oral tablet|oral capsule)\b",
    re.IGNORECASE,
)
ROUTE_RE = re.compile(
    r"\b(iv|intravenous|sc|subcutaneous|im|intramuscular|oral|po|topical|nasal|inhaled|inhalation|ophthalmic|otic|rectal|transdermal|injection|infusion)\b",
    re.IGNORECASE,
)
SCHEDULE_RE = re.compile(
    r"\b(qd|bid|tid|qid|once daily|twice daily|three times daily|every \d+ (?:day|days|week|weeks)|daily|weekly|monthly|q3w|q2w|bid|qd|rp2d|rde|dl\d+)\b",
    re.IGNORECASE,
)
TRIAL_ANNOTATION_RE = re.compile(
    r"\b(?:arm\s*[A-Z0-9]+|cohort\s*[A-Z0-9]+|group\s*[A-Z0-9]+|part\s*[A-Z0-9]+|dose\s*level\s*[A-Z0-9]+|experimental|control|comparator|monotherapy|combination therapy|phase\s*\d+[a-z]?|recommended dose expansion|dose escalation|dose expansion|investigator'?s choice|physician'?s choice|open-label|single-arm)\b",
    re.IGNORECASE,
)
SALT_RE = re.compile(
    r"\b(hydrochloride|hcl|sodium|potassium|acetate|maleate|mesylate|besylate|tosylate|tartrate|citrate|phosphate|diphosphate|fumarate|succinate|nitrate|sulfate|sulphate|hydrobromide|bitartrate|carbonate|bicarbonate|chloride|bromide|iodide|calcium|magnesium|zinc|dimaleate|fumarate|mesilate|erbumine)\b",
    re.IGNORECASE,
)

CLASS_OR_REGIMEN_RE = re.compile(
    r"\b(inhibitor|agonist|antagonist|antibody-drug conjugate|adc|chemotherapy|immunotherapy|regimen|combination|therapy|biosimilar|monoclonal antibody|checkpoint inhibitor|hormone therapy|endocrine therapy|supportive care)\b",
    re.IGNORECASE,
)
REVIEW_CODE_RE = re.compile(r"\b[A-Z]{2,}-?\d{2,}[A-Z0-9-]*\b")
PROCEDURE_RE = re.compile(
    r"\b(surgery|resection|biopsy|radiation|radiotherapy|transplant|irradiation|ablation|imaging|ultrasound|mri|ct scan|assessment|review|observation|nursing|peer support|remediation|training|fitness testing|pharmacological study|questionnaire|sample collection|specimen collection)\b",
    re.IGNORECASE,
)
BIOMARKER_RE = re.compile(
    r"\b(mutation|fusion|expression|amplification|ihc|pdl1|pd-l1|msi|tmb|biomarker|gene signature|receptor|liquid biopsy|laboratory biomarker analysis|pharmacogenomic study)\b",
    re.IGNORECASE,
)
REGIMEN_CONNECTOR_RE = re.compile(r"\s*(?:\+|/| and | with | plus | or )\s*", re.IGNORECASE)

TTY_PRIORITY = {
    "PIN": 0,
    "IN": 1,
    "MIN": 2,
    "BN": 10,
    "SCDC": 20,
    "SCD": 21,
    "SBDC": 22,
    "SBD": 23,
    "SCDF": 24,
    "SBDF": 25,
    "SCDG": 26,
    "SBDG": 27,
    "GPCK": 28,
    "BPCK": 29,
    "CD": 30,
    "BD": 31,
}
DEFAULT_TTY_PRIORITY = 100

SOURCE_PRIORITY = {
    "RXNORM": 0,
    "MTHSPL": 1,
    "ATC": 2,
    "DRUGBANK": 3,
    "SNOMEDCT_US": 4,
    "MMSL": 5,
    "NDDF": 6,
    "VANDF": 7,
    "GS": 8,
    "USP": 9,
}
DEFAULT_SOURCE_PRIORITY = 50

SEMANTIC_TYPE_PRIORITY = {
    "Pharmacologic Substance": 0,
    "Organic Chemical": 1,
    "Amino Acid, Peptide, or Protein": 2,
    "Hormone": 3,
    "Immunologic Factor": 4,
    "Antibiotic": 5,
    "Clinical Drug": 20,
    "Manufactured Object": 30,
}
DEFAULT_SEMANTIC_TYPE_PRIORITY = 100

RXNCONSO_OUTPUT_FIELDS = [
    "rxcui",
    "lat",
    "ts",
    "lui",
    "stt",
    "sui",
    "ispref",
    "rxaui",
    "saui",
    "scui",
    "sdui",
    "sab",
    "tty",
    "code",
    "str",
    "srl",
    "suppress",
    "cvf",
]

RXNSTY_OUTPUT_FIELDS = ["tui", "stn", "sty", "atui", "cvf"]

RAW_SINGLE_AGENT_ABBREVIATION_EXPANSIONS: Dict[str, List[str]] = {
    "5-FU": ["Fluorouracil", "5-Fluorouracil"],
    "FU": ["Fluorouracil", "5-Fluorouracil"],
    "LV": ["Leucovorin", "Levoleucovorin", "Folinic acid", "Calcium folinate"],
    "L-LV": ["Levoleucovorin"],
    "TMZ": ["Temozolomide"],
    "T-DXD": ["Trastuzumab Deruxtecan", "Fam-Trastuzumab Deruxtecan-Nxki"],
    "DATO-DXD": ["Datopotamab Deruxtecan", "Datopotamab deruxtecan-dlnk"],
    "HER3-DXD": ["Patritumab Deruxtecan"],
    "T-DM1": ["Trastuzumab Emtansine", "Ado-trastuzumab emtansine"],
    "MIRV": ["Mirvetuximab Soravtansine"],
    "ADT": ["Androgen Deprivation Therapy", "Androgen-deprivation Therapy"],
    "LHRH": ["Luteinizing Hormone-Releasing Hormone"],
    "LHRHA": ["Luteinizing Hormone-Releasing Hormone Agonist"],
    "LHRHA OR SURGICAL CASTRATION": ["Luteinizing Hormone-Releasing Hormone Agonist"],
    "IVIG": ["Intravenous immunoglobulin", "Immune Globulin Intravenous"],
    "BCG": ["Bacillus Calmette-Guerin", "Bacillus Calmette-Guerin (BCG)"],
    "G-CSF": ["Granulocyte Colony-Stimulating Factor", "Filgrastim", "Pegfilgrastim"],
    "LEE011": ["Ribociclib"],
    "BYL719": ["Alpelisib"],
    "AG-120": ["Ivosidenib"],
    "AG-221": ["Enasidenib"],
    "MEDI4736": ["Durvalumab"],
    "AZD9291": ["Osimertinib"],
    "TAK-788": ["Mobocertinib"],
    "LOXO-292": ["Selpercatinib"],
    "MRTX849": ["Adagrasib"],
    "ACP-196": ["Acalabrutinib"],
    "LDK378": ["Ceritinib"],
    "TPX-0005": ["Repotrectinib"],
    "DS-8201A": ["Trastuzumab Deruxtecan", "Fam-Trastuzumab Deruxtecan-Nxki"],
    "DS-1062A": ["Datopotamab Deruxtecan", "Datopotamab deruxtecan-dlnk"],
    "SAR439684": ["Cemiplimab"],
    "SAR650984": ["Isatuximab"],
    "PF-06863135": ["Elranatamab"],
    "ALX148": ["Evorpacept"],
    "ARV-471": ["Vepdegestrant"],
    "PF-07850327": ["Vepdegestrant"],
    "BAY1841788": ["Darolutamide"],
    "BAY2757556": ["Larotrectinib"],
    "XL184": ["Cabozantinib"],
    "S95005": ["Trifluridine/Tipiracil", "Trifluridine/tipiracil hydrochloride"],
    "KEYTRUDA": ["Pembrolizumab"],
    "OPDIVO": ["Nivolumab"],
    "VITRAKVI": ["Larotrectinib"],
    "NUBEQA": ["Darolutamide"],
    "XTANDI": ["Enzalutamide"],
    "ZYTIGA": ["Abiraterone Acetate"],
    "ERLEADA": ["Apalutamide"],
    "JEMPERLI": ["Dostarlimab"],
    "PLD": ["Pegylated liposomal doxorubicin"],
}

RAW_REGIMEN_ABBREVIATION_EXPANSIONS: Dict[str, List[str]] = {
    "FOLFOX": ["Folinic acid", "Fluorouracil", "Oxaliplatin"],
    "MFOLFOX": ["Folinic acid", "Fluorouracil", "Oxaliplatin"],
    "MFOLFOX6": ["Folinic acid", "Fluorouracil", "Oxaliplatin"],
    "FOLFIRI": ["Folinic acid", "Fluorouracil", "Irinotecan"],
    "FOLFIRINOX": ["Folinic acid", "Fluorouracil", "Irinotecan", "Oxaliplatin"],
    "MFOLFIRINOX": ["Folinic acid", "Fluorouracil", "Irinotecan", "Oxaliplatin"],
    "CAPEOX": ["Capecitabine", "Oxaliplatin"],
    "CAPOX": ["Capecitabine", "Oxaliplatin"],
    "CAPTEM": ["Capecitabine", "Temozolomide"],
    "R-CHOP": ["Rituximab", "Cyclophosphamide", "Doxorubicin", "Vincristine", "Prednisone"],
    "R-CVP": ["Rituximab", "Cyclophosphamide", "Vincristine", "Prednisone"],
    "BR": ["Bendamustine", "Rituximab"],
}

SPELLING_VARIANT_SUBS: Tuple[Tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\boestrogen\b", re.IGNORECASE), "estrogen"),
    (re.compile(r"\bleukaemia\b", re.IGNORECASE), "leukemia"),
    (re.compile(r"\btumour\b", re.IGNORECASE), "tumor"),
    (re.compile(r"\bfoetal\b", re.IGNORECASE), "fetal"),
    (re.compile(r"\bamoxycillin\b", re.IGNORECASE), "amoxicillin"),
    (re.compile(r"\bplaclitaxel\b", re.IGNORECASE), "paclitaxel"),
    (re.compile(r"\bpredinsone\b", re.IGNORECASE), "prednisone"),
    (re.compile(r"\bansastrozole\b", re.IGNORECASE), "anastrozole"),
    (re.compile(r"\b5-flurouracil\b", re.IGNORECASE), "5-fluorouracil"),
)


@dataclass(frozen=True)
class RxnConsoRow:
    rxcui: str
    lat: str
    ts: str
    lui: str
    stt: str
    sui: str
    ispref: str
    rxaui: str
    saui: str
    scui: str
    sdui: str
    sab: str
    tty: str
    code: str
    str_value: str
    srl: str
    suppress: str
    cvf: str


@dataclass(frozen=True)
class RxnStyRow:
    rxcui: str
    tui: str
    stn: str
    sty: str
    atui: str
    cvf: str


@dataclass(frozen=True)
class MatchCandidate:
    alias: str
    conso: RxnConsoRow
    semantic_types: Tuple[str, ...]


@dataclass(frozen=True)
class ResolvedMatch:
    matched_alias: Optional[str]
    match_status: str
    matched_rxcui: Optional[str]
    candidate_count: int
    candidate_summary: str


class RxNormIndex:
    def __init__(self, conso_rows: Sequence[RxnConsoRow], sty_rows: Sequence[RxnStyRow]) -> None:
        self.conso_rows = list(conso_rows)
        self.sty_rows = list(sty_rows)

        self.by_normalized_string: Dict[str, List[RxnConsoRow]] = defaultdict(list)
        self.by_rxcui: Dict[str, List[RxnConsoRow]] = defaultdict(list)
        self.sty_by_rxcui: Dict[str, List[RxnStyRow]] = defaultdict(list)
        self.semantic_types_by_rxcui: Dict[str, List[str]] = defaultdict(list)

        for row in self.conso_rows:
            key = normalize_lookup_text(row.str_value)
            if not key:
                continue
            self.by_normalized_string[key].append(row)
            self.by_rxcui[row.rxcui].append(row)

        for row in self.sty_rows:
            self.sty_by_rxcui[row.rxcui].append(row)
            self.semantic_types_by_rxcui[row.rxcui].append(row.sty)

    @classmethod
    def from_rrf_dir(cls, rrf_dir: Path) -> "RxNormIndex":
        conso_rows = list(load_rxnconso(rrf_dir / RXNCONSO_FILENAME))
        sty_rows = list(load_rxnsty_rows(rrf_dir / RXNSTY_FILENAME))
        return cls(conso_rows=conso_rows, sty_rows=sty_rows)

    def lookup_exact(self, alias: str) -> List[MatchCandidate]:
        key = normalize_lookup_text(alias)
        if not key:
            return []
        return [
            MatchCandidate(
                alias=alias,
                conso=row,
                semantic_types=tuple(self.semantic_types_by_rxcui.get(row.rxcui, [])),
            )
            for row in self.by_normalized_string.get(key, [])
        ]

    def get_best_row_for_rxcui(self, rxcui: str) -> Optional[RxnConsoRow]:
        rows = self.by_rxcui.get(rxcui, [])
        if not rows:
            return None
        return sorted(rows, key=rank_conso_row)[0]

    def get_rxnconso_aggregate(self, rxcui: str) -> Dict[str, str]:
        return aggregate_rxnconso_rows(self.by_rxcui.get(rxcui, []))

    def get_rxnsty_aggregate(self, rxcui: str) -> Dict[str, str]:
        return aggregate_rxnsty_rows(self.sty_by_rxcui.get(rxcui, []))


def normalize_lookup_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text))
    text = TRADEMARK_RE.sub("", text)
    text = DASH_RE.sub("-", text)
    text = text.strip().lower()
    text = MULTISPACE_RE.sub(" ", text)
    return text


def normalize_abbrev_key(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text))
    text = DASH_RE.sub("-", text)
    text = text.strip().upper()
    text = text.replace("®", "").replace("™", "")
    text = MULTISPACE_RE.sub(" ", text)
    return text


SINGLE_AGENT_ABBREVIATION_EXPANSIONS: Dict[str, List[str]] = {
    normalize_abbrev_key(key): values for key, values in RAW_SINGLE_AGENT_ABBREVIATION_EXPANSIONS.items()
}

REGIMEN_ABBREVIATION_EXPANSIONS: Dict[str, List[str]] = {
    normalize_abbrev_key(key): values for key, values in RAW_REGIMEN_ABBREVIATION_EXPANSIONS.items()
}


def split_pipe(value: object) -> List[str]:
    if value is None or pd.isna(value):
        return []
    parts = [part.strip() for part in str(value).split(AGG_DELIMITER) if part.strip()]
    return ordered_unique(parts)


def ordered_unique(values: Sequence[str]) -> List[str]:
    seen: Set[str] = set()
    ordered: List[str] = []
    for value in values:
        cleaned = str(value).strip()
        if not cleaned:
            continue
        norm = normalize_lookup_text(cleaned)
        if norm in seen:
            continue
        seen.add(norm)
        ordered.append(cleaned)
    return ordered


def unique_join(values: Sequence[str]) -> str:
    return AGG_DELIMITER.join(ordered_unique(values))


def semantic_type_priority(semantic_types: Sequence[str]) -> int:
    if not semantic_types:
        return DEFAULT_SEMANTIC_TYPE_PRIORITY
    return min(SEMANTIC_TYPE_PRIORITY.get(x, DEFAULT_SEMANTIC_TYPE_PRIORITY) for x in semantic_types)


def rank_conso_row(row: RxnConsoRow) -> Tuple[int, int, int, int, str]:
    return (
        TTY_PRIORITY.get(row.tty, DEFAULT_TTY_PRIORITY),
        SOURCE_PRIORITY.get(row.sab, DEFAULT_SOURCE_PRIORITY),
        0 if row.suppress != "O" else 1,
        0 if row.ispref == "Y" else 1,
        row.str_value.lower(),
    )


def rank_candidate(candidate: MatchCandidate) -> Tuple[int, int, int, int, str]:
    return (
        TTY_PRIORITY.get(candidate.conso.tty, DEFAULT_TTY_PRIORITY),
        semantic_type_priority(candidate.semantic_types),
        SOURCE_PRIORITY.get(candidate.conso.sab, DEFAULT_SOURCE_PRIORITY),
        0 if candidate.conso.ispref == "Y" else 1,
        candidate.conso.str_value.lower(),
    )


def deduplicate_candidates(candidates: Sequence[MatchCandidate]) -> List[MatchCandidate]:
    deduped: List[MatchCandidate] = []
    seen: Set[Tuple[str, str]] = set()
    for candidate in sorted(candidates, key=rank_candidate):
        key = (candidate.conso.rxcui, candidate.conso.rxaui)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def choose_best_rxcui(candidates: Sequence[MatchCandidate], index: RxNormIndex) -> Optional[Tuple[str, MatchCandidate, RxnConsoRow]]:
    grouped: Dict[str, List[MatchCandidate]] = defaultdict(list)
    for candidate in candidates:
        grouped[candidate.conso.rxcui].append(candidate)

    scored: List[Tuple[Tuple[int, int, int, str], str, MatchCandidate, RxnConsoRow]] = []
    for rxcui, members in grouped.items():
        best_candidate = sorted(members, key=rank_candidate)[0]
        best_row = index.get_best_row_for_rxcui(rxcui)
        if best_row is None:
            continue
        score_tuple = (
            TTY_PRIORITY.get(best_row.tty, DEFAULT_TTY_PRIORITY),
            semantic_type_priority(index.semantic_types_by_rxcui.get(rxcui, [])),
            SOURCE_PRIORITY.get(best_row.sab, DEFAULT_SOURCE_PRIORITY),
            best_row.str_value.lower(),
        )
        scored.append((score_tuple, rxcui, best_candidate, best_row))

    if not scored:
        return None

    _, winning_rxcui, winning_candidate, winning_row = sorted(scored, key=lambda x: x[0])[0]
    return winning_rxcui, winning_candidate, winning_row


def summarize_candidates(candidates: Sequence[MatchCandidate], index: RxNormIndex, limit: int = 8) -> str:
    grouped: Dict[str, List[MatchCandidate]] = defaultdict(list)
    for candidate in candidates:
        grouped[candidate.conso.rxcui].append(candidate)

    summaries: List[Tuple[Tuple[int, int, int, str], str]] = []
    for rxcui, members in grouped.items():
        best_candidate = sorted(members, key=rank_candidate)[0]
        best_row = index.get_best_row_for_rxcui(rxcui)
        if best_row is None:
            continue
        score_tuple = (
            TTY_PRIORITY.get(best_row.tty, DEFAULT_TTY_PRIORITY),
            semantic_type_priority(index.semantic_types_by_rxcui.get(rxcui, [])),
            SOURCE_PRIORITY.get(best_row.sab, DEFAULT_SOURCE_PRIORITY),
            best_row.str_value.lower(),
        )
        summaries.append(
            (
                score_tuple,
                f"{best_row.str_value} [RXCUI={rxcui}; TTY={best_row.tty}; via={best_candidate.alias}]",
            )
        )

    return AGG_DELIMITER.join(text for _, text in sorted(summaries, key=lambda x: x[0])[:limit])


def aggregate_rxnconso_rows(rows: Sequence[RxnConsoRow]) -> Dict[str, str]:
    if not rows:
        return {f"rx_{field}": "" for field in RXNCONSO_OUTPUT_FIELDS}

    def join_attr(attr: str) -> str:
        return unique_join([getattr(row, attr) for row in rows])

    return {
        "rx_rxcui": join_attr("rxcui"),
        "rx_lat": join_attr("lat"),
        "rx_ts": join_attr("ts"),
        "rx_lui": join_attr("lui"),
        "rx_stt": join_attr("stt"),
        "rx_sui": join_attr("sui"),
        "rx_ispref": join_attr("ispref"),
        "rx_rxaui": join_attr("rxaui"),
        "rx_saui": join_attr("saui"),
        "rx_scui": join_attr("scui"),
        "rx_sdui": join_attr("sdui"),
        "rx_sab": join_attr("sab"),
        "rx_tty": join_attr("tty"),
        "rx_code": join_attr("code"),
        "rx_str": join_attr("str_value"),
        "rx_srl": join_attr("srl"),
        "rx_suppress": join_attr("suppress"),
        "rx_cvf": join_attr("cvf"),
    }


def aggregate_rxnsty_rows(rows: Sequence[RxnStyRow]) -> Dict[str, str]:
    if not rows:
        return {f"rx_{field}": "" for field in RXNSTY_OUTPUT_FIELDS} | {"rx_cvf_sty": ""}

    return {
        "rx_tui": unique_join([row.tui for row in rows]),
        "rx_stn": unique_join([row.stn for row in rows]),
        "rx_sty": unique_join([row.sty for row in rows]),
        "rx_atui": unique_join([row.atui for row in rows]),
        "rx_cvf_sty": unique_join([row.cvf for row in rows]),
    }


def read_rrf_rows(path: Path) -> Iterator[List[str]]:
    if not path.exists():
        raise FileNotFoundError(f"RRF file not found: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter=RRF_DELIMITER)
        for row in reader:
            if row and row[-1] == "":
                row = row[:-1]
            yield row


def load_rxnconso(path: Path) -> Iterator[RxnConsoRow]:
    for row in read_rrf_rows(path):
        if len(row) < 17:
            logger.warning("Skipping malformed RXNCONSO row with %d columns", len(row))
            continue
        yield RxnConsoRow(
            rxcui=row[0],
            lat=row[1],
            ts=row[2],
            lui=row[3],
            stt=row[4],
            sui=row[5],
            ispref=row[6],
            rxaui=row[7],
            saui=row[8],
            scui=row[9],
            sdui=row[10],
            sab=row[11],
            tty=row[12],
            code=row[13],
            str_value=row[14],
            srl=row[15] if len(row) > 15 else "",
            suppress=row[16] if len(row) > 16 else "",
            cvf=row[17] if len(row) > 17 else "",
        )


def load_rxnsty_rows(path: Path) -> Iterator[RxnStyRow]:
    if not path.exists():
        logger.warning("RXNSTY not found at %s; RXNSTY-derived columns will be blank", path)
        return iter(())
    rows: List[RxnStyRow] = []
    for row in read_rrf_rows(path):
        if len(row) < 4:
            logger.warning("Skipping malformed RXNSTY row with %d columns", len(row))
            continue
        rows.append(
            RxnStyRow(
                rxcui=row[0],
                tui=row[1],
                stn=row[2],
                sty=row[3],
                atui=row[4] if len(row) > 4 else "",
                cvf=row[5] if len(row) > 5 else "",
            )
        )
    return iter(rows)


def expand_abbreviations_and_spelling(alias: str) -> List[str]:
    variants = [alias]

    abbrev_key = normalize_abbrev_key(alias)
    if abbrev_key in SINGLE_AGENT_ABBREVIATION_EXPANSIONS:
        variants.extend(SINGLE_AGENT_ABBREVIATION_EXPANSIONS[abbrev_key])
    if abbrev_key in REGIMEN_ABBREVIATION_EXPANSIONS:
        variants.extend(REGIMEN_ABBREVIATION_EXPANSIONS[abbrev_key])

    hyphenless = re.sub(r"[-\s]+", "", abbrev_key)
    for mapping in (SINGLE_AGENT_ABBREVIATION_EXPANSIONS, REGIMEN_ABBREVIATION_EXPANSIONS):
        for key, expansions in mapping.items():
            if re.sub(r"[-\s]+", "", key) == hyphenless and abbrev_key != key:
                variants.extend(expansions)

    compact = re.sub(r"([A-Za-z]+)[-\s]+(\d+)", r"\1\2", alias)
    if compact != alias:
        variants.append(compact)

    spaced = re.sub(r"([A-Za-z]+)(\d+)", r"\1 \2", alias)
    if spaced != alias:
        variants.append(spaced)

    debranded = TRADEMARK_RE.sub("", alias)
    if debranded != alias:
        variants.append(debranded)

    for pattern, replacement in SPELLING_VARIANT_SUBS:
        candidate = pattern.sub(replacement, alias)
        if candidate != alias:
            variants.append(candidate)

    no_edge_punct = PUNCT_EDGE_RE.sub("", alias).strip()
    if no_edge_punct and no_edge_punct != alias:
        variants.append(no_edge_punct)

    return ordered_unique(variants)


def normalize_pharma_alias(alias: str) -> List[str]:
    variants = [alias]

    current = alias
    current = PARENS_CONTENT_RE.sub(" ", current)
    current = STRENGTH_RE.sub(" ", current)
    current = DOSAGE_FORM_RE.sub(" ", current)
    current = ROUTE_RE.sub(" ", current)
    current = SCHEDULE_RE.sub(" ", current)
    current = TRIAL_ANNOTATION_RE.sub(" ", current)
    current = MULTISPACE_RE.sub(" ", current).strip(" ,;:-/")
    if current and current != alias:
        variants.append(current)

    salt_removed = SALT_RE.sub(" ", current)
    salt_removed = MULTISPACE_RE.sub(" ", salt_removed).strip(" ,;:-/")
    if salt_removed and salt_removed not in variants:
        variants.append(salt_removed)

    left_of_comma = alias.split(",", 1)[0].strip()
    if left_of_comma and left_of_comma != alias:
        variants.append(left_of_comma)

    left_of_semicolon = alias.split(";", 1)[0].strip()
    if left_of_semicolon and left_of_semicolon != alias:
        variants.append(left_of_semicolon)

    left_of_paren = re.sub(r"\s*\([^)]*$", "", alias).strip()
    if left_of_paren and left_of_paren != alias:
        variants.append(left_of_paren)

    return ordered_unique(variants)


def classify_unmatched(texts: Sequence[str]) -> str:
    joined = AGG_DELIMITER.join([text for text in texts if text])
    if not joined:
        return "UNKNOWN"
    if PROCEDURE_RE.search(joined):
        return "PROCEDURE"
    if BIOMARKER_RE.search(joined):
        return "BIOMARKER"
    if CLASS_OR_REGIMEN_RE.search(joined):
        if REGIMEN_CONNECTOR_RE.search(joined) or "regimen" in joined.lower() or "combination" in joined.lower():
            return "REGIMEN"
        return "DRUG_CLASS"
    if REVIEW_CODE_RE.search(joined):
        return "DRUG"
    if REGIMEN_CONNECTOR_RE.search(joined):
        return "REGIMEN"
    return "UNKNOWN"


def resolve_stage(aliases: Sequence[str], index: RxNormIndex, stage_name: str) -> ResolvedMatch:
    candidates: List[MatchCandidate] = []
    for alias in aliases:
        candidates.extend(index.lookup_exact(alias))
    candidates = deduplicate_candidates(candidates)

    if not candidates:
        return ResolvedMatch(
            matched_alias=None,
            match_status=f"{stage_name}_NO_MATCH",
            matched_rxcui=None,
            candidate_count=0,
            candidate_summary="",
        )

    winner = choose_best_rxcui(candidates, index)
    if winner is None:
        return ResolvedMatch(
            matched_alias=None,
            match_status=f"{stage_name}_NO_MATCH",
            matched_rxcui=None,
            candidate_count=0,
            candidate_summary="",
        )

    winning_rxcui, winning_candidate, _ = winner
    unique_rxcuis = {candidate.conso.rxcui for candidate in candidates}
    status = f"{stage_name}_MATCHED_MULTIPLE_CANDIDATES" if len(unique_rxcuis) > 1 else f"{stage_name}_MATCHED"
    return ResolvedMatch(
        matched_alias=winning_candidate.alias,
        match_status=status,
        matched_rxcui=winning_rxcui,
        candidate_count=len(unique_rxcuis),
        candidate_summary=summarize_candidates(candidates, index=index),
    )


def blank_rx_payload() -> Dict[str, str]:
    payload = {f"rx_{field}": "" for field in RXNCONSO_OUTPUT_FIELDS}
    payload.update({f"rx_{field}": "" for field in RXNSTY_OUTPUT_FIELDS})
    payload["rx_cvf_sty"] = ""
    return payload


def build_stage_lookup_columns(record: Dict[str, object]) -> Tuple[List[str], List[str], List[str]]:
    original_aliases = split_pipe(record.get(COL_ALIASES, ""))
    stage1_aliases = ordered_unique(original_aliases)

    stage2_aliases: List[str] = []
    for alias in stage1_aliases:
        stage2_aliases.extend(expand_abbreviations_and_spelling(alias))
    stage2_aliases = ordered_unique(stage2_aliases)

    stage3_aliases: List[str] = []
    for alias in stage1_aliases:
        stage3_aliases.extend(normalize_pharma_alias(alias))
    for alias in stage2_aliases:
        stage3_aliases.extend(normalize_pharma_alias(alias))
    stage3_aliases = ordered_unique(stage3_aliases)

    return stage1_aliases, stage2_aliases, stage3_aliases


def map_interventions_dataframe(df_input: pd.DataFrame, index: RxNormIndex) -> pd.DataFrame:
    required = {COL_TYPE, COL_ALIASES}
    missing = required - set(df_input.columns)
    if missing:
        raise KeyError(f"Input workbook missing required columns: {sorted(missing)}")

    output_rows: List[Dict[str, object]] = []

    for row_number, record in enumerate(df_input.fillna("").to_dict(orient="records"), start=1):
        row = dict(record)
        target_type = str(row.get(COL_TYPE, "")).strip().upper()
        stage1_aliases, stage2_aliases, stage3_aliases = build_stage_lookup_columns(row)

        row["rx_lookup_stage1_original"] = unique_join(stage1_aliases)
        row["rx_lookup_stage2_variants"] = unique_join(stage2_aliases)
        row["rx_lookup_stage3_normalized"] = unique_join(stage3_aliases)

        row["rx_stage1_status"] = ""
        row["rx_stage1_match_alias"] = ""
        row["rx_stage1_rxcui"] = ""
        row["rx_stage2_status"] = ""
        row["rx_stage2_match_alias"] = ""
        row["rx_stage2_rxcui"] = ""
        row["rx_stage3_status"] = ""
        row["rx_stage3_match_alias"] = ""
        row["rx_stage3_rxcui"] = ""
        row["rx_resolution_stage"] = ""
        row["rx_match_alias"] = ""
        row["rx_match_status"] = ""
        row["rx_candidate_count"] = 0
        row["rx_candidate_summary"] = ""
        row["rx_unmatched_classification"] = classify_unmatched(stage1_aliases)
        row.update(blank_rx_payload())

        if target_type not in TARGET_TYPES:
            row["rx_match_status"] = "SKIPPED_NON_TARGET_TYPE"
            row["rx_resolution_stage"] = "SKIPPED"
            output_rows.append(row)
            continue

        stage1_result = resolve_stage(stage1_aliases, index=index, stage_name="STAGE1")
        row["rx_stage1_status"] = stage1_result.match_status
        row["rx_stage1_match_alias"] = stage1_result.matched_alias or ""
        row["rx_stage1_rxcui"] = stage1_result.matched_rxcui or ""

        winning_result = stage1_result
        winning_stage = "STAGE1_ORIGINAL"

        if stage1_result.matched_rxcui is None:
            stage2_result = resolve_stage(stage2_aliases, index=index, stage_name="STAGE2")
            row["rx_stage2_status"] = stage2_result.match_status
            row["rx_stage2_match_alias"] = stage2_result.matched_alias or ""
            row["rx_stage2_rxcui"] = stage2_result.matched_rxcui or ""
            if stage2_result.matched_rxcui is not None:
                winning_result = stage2_result
                winning_stage = "STAGE2_VARIANT"
            else:
                stage3_result = resolve_stage(stage3_aliases, index=index, stage_name="STAGE3")
                row["rx_stage3_status"] = stage3_result.match_status
                row["rx_stage3_match_alias"] = stage3_result.matched_alias or ""
                row["rx_stage3_rxcui"] = stage3_result.matched_rxcui or ""
                if stage3_result.matched_rxcui is not None:
                    winning_result = stage3_result
                    winning_stage = "STAGE3_NORMALIZED"
                else:
                    winning_stage = "UNMATCHED"

        if winning_result.matched_rxcui is None:
            row["rx_resolution_stage"] = "UNMATCHED"
            row["rx_match_status"] = "UNMATCHED"
            row["rx_match_alias"] = ""
            row["rx_candidate_count"] = 0
            row["rx_candidate_summary"] = ""
        else:
            row["rx_resolution_stage"] = winning_stage
            row["rx_match_status"] = "MATCHED"
            row["rx_match_alias"] = winning_result.matched_alias or ""
            row["rx_candidate_count"] = winning_result.candidate_count
            row["rx_candidate_summary"] = winning_result.candidate_summary
            row.update(index.get_rxnconso_aggregate(winning_result.matched_rxcui))
            row.update(index.get_rxnsty_aggregate(winning_result.matched_rxcui))

        output_rows.append(row)

        if row_number % 1000 == 0:
            logger.info("Mapped %d intervention rows", row_number)

    return pd.DataFrame(output_rows)


def process_workbook(input_excel: Path, output_excel: Path, rxnorm_rrf_dir: Path, sheet_name: str) -> None:
    if not input_excel.exists():
        raise FileNotFoundError(f"Input workbook not found: {input_excel}")
    if not rxnorm_rrf_dir.exists():
        raise FileNotFoundError(f"RxNorm RRF directory not found: {rxnorm_rrf_dir}")

    xls = pd.ExcelFile(input_excel)
    if sheet_name not in xls.sheet_names:
        raise KeyError(f"Worksheet {sheet_name!r} not found. Available sheets: {xls.sheet_names}")

    logger.info("Loading RxNorm lexical index from %s", rxnorm_rrf_dir)
    index = RxNormIndex.from_rrf_dir(rxnorm_rrf_dir)
    logger.info(
        "Loaded RxNorm lexical index: %d RXNCONSO rows, %d normalized keys, %d RXNSTY rows",
        len(index.conso_rows),
        len(index.by_normalized_string),
        len(index.sty_rows),
    )

    df_input = pd.read_excel(input_excel, sheet_name=sheet_name, dtype="string")
    logger.info("Read %d intervention rows from %s / %s", len(df_input), input_excel, sheet_name)

    df_output = map_interventions_dataframe(df_input=df_input, index=index)

    output_excel.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output_excel, engine="openpyxl") as writer:
        df_output.to_excel(writer, sheet_name=sheet_name, index=False)

    logger.info("Wrote mapped workbook to %s", output_excel)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Stage-wise RxNorm mapping for intervention-centric CT.gov tables")
    parser.add_argument("--input_excel", required=True, type=Path, help="Path to the intervention-centric workbook")
    parser.add_argument("--rxnorm_rrf_dir", required=True, type=Path, help="Directory containing RXNCONSO.RRF and optional RXNSTY.RRF")
    parser.add_argument("--output_excel", required=True, type=Path, help="Output workbook path")
    parser.add_argument("--sheet_name", default=SHEET_INPUT, help=f"Worksheet name to read/write. Defaults to {SHEET_INPUT!r}")
    parser.add_argument("--log_level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
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
        output_excel=args.output_excel,
        rxnorm_rrf_dir=args.rxnorm_rrf_dir,
        sheet_name=args.sheet_name,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
