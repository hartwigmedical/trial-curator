from __future__ import annotations

import csv
import logging
import re
import unicodedata
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

RXNCONSO_FILENAME = "RXNCONSO.RRF"
RXNREL_FILENAME = "RXNREL.RRF"
RRF_DELIMITER = "|"
SUMMARY_DELIMITER = " | "


TTY_PRIORITY: Dict[str, int] = {
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
    "GPCK": 30,
    "BPCK": 31,
    "DF": 40,
    "DFG": 41,
    "SU": 50,
    "SY": 60,
    "PT": 70,
    "FN": 80,
}
DEFAULT_TTY_PRIORITY = 100

SOURCE_PRIORITY: Dict[str, int] = {
    "RXNORM": 0,
    "GS": 1,
    "DRUGBANK": 2,
    "ATC": 3,
    "SNOMEDCT_US": 4,
    "USP": 5,
    "MTHSPL": 6,
    "MMSL": 7,
    "MMX": 8,
    "NDDF": 9,
    "VANDF": 10,
}
DEFAULT_SOURCE_PRIORITY = 50

# Prefer broad ingredient concepts for grouping; keep PIN as fallback when no IN/MIN path exists.
PREFERRED_INGREDIENT_TTYS = {"IN", "MIN"}
INGREDIENT_CANDIDATE_TTYS = {"IN", "PIN", "MIN"}
INGREDIENT_TTY_PRIORITY: Dict[str, int] = {"IN": 0, "MIN": 1, "PIN": 2}

ALLOWED_INGREDIENT_RELAS = {
    "has_ingredient",
    "ingredient_of",
    "has_precise_ingredient",
    "precise_ingredient_of",
    "consists_of",
    "constitutes",
    "has_tradename",
    "tradename_of",
    "isa",
    "inverse_isa",
}

TRADEMARK_SYMBOL_RE = re.compile(r"[®™\ufe0f]")
TRADEMARK_TEXT_RE = re.compile(r"(?i)(?:\(\s*tm\s*\)|\^\s*tm\b|(?<=\w)tm\b)")
MULTISPACE_RE = re.compile(r"\s+")
DASH_RE = re.compile(r"[\u2010\u2011\u2012\u2013\u2014\u2212]")
EDGE_PUNCT_RE = re.compile(r"^[\s\.,;:/\\+\-]+|[\s\.,;:/\\+\-]+$")
PARENS_CONTENT_RE = re.compile(r"\([^)]*\)")
TOKEN_RE = re.compile(r"[a-z0-9]+")

DOSE_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*"
    r"(?:mg|mcg|µg|g|kg|ng|iu|units?|u|ml|mL|l|L|mmol|mEq|gy|gray|auc|%)"
    r"(?:\s*/\s*(?:m\^?2|m2|kg|ml|mL|l|L|day|week|month|\d+(?:\.\d+)?\s*(?:ml|mL|l|L)))?\b",
    re.IGNORECASE,
)
DOSAGE_FORM_ROUTE_RE = re.compile(
    r"\b(?:oral|intravenous|iv|subcutaneous|sc|intramuscular|im|topical|ophthalmic|otic|nasal|"
    r"inhaled|inhalation|infusion|injection|injectable|tablet|tablets|capsule|capsules|"
    r"solution|suspension|powder|lyophili[sz]ed|vial|syringe|implant|cream|gel|drops?)\b",
    re.IGNORECASE,
)
SCHEDULE_RE = re.compile(
    r"\b(?:once\s+daily|twice\s+daily|daily|weekly|monthly|qd|bid|tid|qid|q\d+w|q\d+d|"
    r"cycle|cycles|day|days|week|weeks|month|months|dose\s*level|dl\d+|rp2d|rde)\b",
    re.IGNORECASE,
)
SALT_RE = re.compile(
    r"\b(?:hydrochloride|hcl|sodium|potassium|acetate|maleate|mesylate|besylate|tosylate|"
    r"tartrate|citrate|phosphate|diphosphate|fumarate|succinate|nitrate|sulfate|sulphate|"
    r"hydrobromide|bitartrate|carbonate|bicarbonate|chloride|bromide|iodide|calcium|"
    r"magnesium|zinc|dimaleate|mesilate|erbumine)\b",
    re.IGNORECASE,
)

LOW_VALUE_TERMS = {
    "drug",
    "study drug",
    "chemotherapy",
    "immunotherapy",
    "placebo",
    "standard of care",
    "soc",
    "treatment",
    "therapy",
    "regimen",
    "combination",
    "rescue medication",
}

BROAD_CLASS_TERMS = {
    "alkylating agent",
    "androgen deprivation therapy",
    "antiangiogenic agent",
    "anti-angiogenic agent",
    "antibody",
    "aromatase inhibitor",
    "beta blocker",
    "calcium channel blocker",
    "checkpoint inhibitor",
    "chemotherapeutic agent",
    "endocrine therapy",
    "estrogen receptor antagonist",
    "hormone therapy",
    "immune checkpoint inhibitor",
    "immunomodulator",
    "kinase inhibitor",
    "monoclonal antibody",
    "platinum",
    "platinum agent",
    "platinum-based chemotherapy",
    "statin",
    "taxane",
    "taxane derivative",
    "tyrosine kinase inhibitor",
}

OBVIOUS_FRAGMENT_PATTERNS = (
    re.compile(r"^\d+$"),                 # 474, 782
    re.compile(r"^\d+[a-z]+$"),           # 11beta
    re.compile(r"^\d+-[a-z]+-\d+$"),      # 2-amino-1
)

RAW_SINGLE_AGENT_ABBREVIATION_EXPANSIONS: Dict[str, List[str]] = {
    "5-FU": ["Fluorouracil", "5-Fluorouracil"],
    "FU": ["Fluorouracil", "5-Fluorouracil"],
    "LV": ["Leucovorin", "Levoleucovorin", "Folinic acid", "Calcium folinate"],
    "L-LV": ["Levoleucovorin"],
    "I-LV": ["Levoleucovorin"],
    "TMZ": ["Temozolomide"],
    "T-DXD": ["Trastuzumab Deruxtecan", "Fam-Trastuzumab Deruxtecan-Nxki"],
    "DATO-DXD": ["Datopotamab Deruxtecan", "Datopotamab deruxtecan-dlnk"],
    "HER3-DXD": ["Patritumab Deruxtecan"],
    "T-DM1": ["Trastuzumab Emtansine", "Ado-trastuzumab emtansine"],
    "MIRV": ["Mirvetuximab Soravtansine"],
    "ADT": ["Androgen Deprivation Therapy", "Androgen-deprivation Therapy"],
    "LHRH": ["Luteinizing Hormone-Releasing Hormone"],
    "LHRHA": ["Luteinizing Hormone-Releasing Hormone Agonist"],
    "IVIG": ["Intravenous immunoglobulin", "Immune Globulin Intravenous"],
    "BCG": ["Bacillus Calmette-Guerin", "Bacillus Calmette-Guerin (BCG)"],
    "G-CSF": ["Granulocyte Colony-Stimulating Factor", "Filgrastim", "Pegfilgrastim"],
    "LEE011": ["Ribociclib"],
    "STI571": ["Imatinib"],
    "STI-571": ["Imatinib"],
    "BYL719": ["Alpelisib"],
    "AG-120": ["Ivosidenib"],
    "AG120": ["Ivosidenib"],
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
    "DS8201A": ["Trastuzumab Deruxtecan", "Fam-Trastuzumab Deruxtecan-Nxki"],
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
    "MTX": ["Methotrexate"],
    "VCR": ["Vincristine"],
    "CTX": ["Cyclophosphamide"],
    "CDDP": ["Cisplatin"],
    "CBDCA": ["Carboplatin"],
    "MK-3475": ["Pembrolizumab"],
    "MK3475": ["Pembrolizumab"],
    "BMS-936558": ["Nivolumab"],
    "BMS936558": ["Nivolumab"],
    "ABT-199": ["Venetoclax"],
    "ABT199": ["Venetoclax"],
    "VP-16": ["Etoposide"],
    "VP16": ["Etoposide"],
    "RO5185426": ["Vemurafenib"],
    "PLX4032": ["Vemurafenib"],
    "PLX-4032": ["Vemurafenib"],
}

# Regimen abbreviations are deliberately not used by resolve_term().
# RxNorm Part 2 maps individual normalized drug terms only. Regimens such as
# FOLFOX, FOLFIRI, CAPOX, R-CHOP, and R-CVP must not silently collapse to
# one component drug. Regimen-aware matching belongs in POTTR/knowledgebase layers.
RAW_REGIMEN_ABBREVIATION_EXPANSIONS: Dict[str, List[str]] = {
    "FOLFOX": ["Folinic acid", "Fluorouracil", "Oxaliplatin"],
    "MFOLFOX": ["Folinic acid", "Fluorouracil", "Oxaliplatin"],
    "MFOLFOX6": ["Folinic acid", "Fluorouracil", "Oxaliplatin"],
    "MODIFIED FOLFOX": ["Folinic acid", "Fluorouracil", "Oxaliplatin"],
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

RAW_CURATED_INGREDIENT_RESCUE_EXPANSIONS: Dict[str, List[str]] = {
    # Legacy oncology brands / synonyms that may not have reliable RXNREL ingredient paths.
    "ONCOVIN": ["Vincristine"],
    "NEOSAR": ["Cyclophosphamide"],
    "PLATINOL": ["Cisplatin"],
    "PLATINOL-AQ": ["Cisplatin"],
    "ARA-C": ["Cytarabine"],
    "ARAC": ["Cytarabine"],
    "CYTOSAR": ["Cytarabine"],
    "CYTOSAR-U": ["Cytarabine"],
    "ADRIAMYCIN": ["Doxorubicin"],
    "ADRIAMYCIN PFS": ["Doxorubicin"],
    "FOLEX": ["Methotrexate"],
    "FOLEX PFS": ["Methotrexate"],
    "CERUBIDINE": ["Daunorubicin"],

    # Common oncology brands.
    "TAXOL": ["Paclitaxel"],
    "TAXOTERE": ["Docetaxel"],
    "GEMZAR": ["Gemcitabine"],
    "ELOXATIN": ["Oxaliplatin"],
    "XELODA": ["Capecitabine"],
    "HERCEPTIN": ["Trastuzumab"],
    "AVASTIN": ["Bevacizumab"],
    "RITUXAN": ["Rituximab"],
    "MABTHERA": ["Rituximab"],
    "ERBITUX": ["Cetuximab"],
    "VECTIBIX": ["Panitumumab"],
    "TARCEVA": ["Erlotinib"],
    "IRESSA": ["Gefitinib"],
    "GLEEVEC": ["Imatinib"],
    "GLIVEC": ["Imatinib"],
    "SUTENT": ["Sunitinib"],
    "NEXAVAR": ["Sorafenib"],
    "SPRYCEL": ["Dasatinib"],
    "CAMPTO": ["Irinotecan"],
    "CAMPTOSAR": ["Irinotecan"],

    # Short oncology / biologic terms that should be explicitly allowed.
    "IL-2": ["Aldesleukin", "Interleukin-2"],
    "IL2": ["Aldesleukin", "Interleukin-2"],
}

RAW_SHORT_AMBIGUOUS_TERMS = {
    "GPT",
    "GOT",
    "AST",
    "ALT",
    "HBS",
    "ICG",
    "CYC",
    "LDH",
    "CRP",
    "CEA",
    "AFP",
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
class RxnRelRow:
    rxcui1: str
    rxaui1: str
    stype1: str
    rel: str
    rxcui2: str
    rxaui2: str
    stype2: str
    rela: str
    rui: str
    srui: str
    sab: str
    sl: str
    rg: str
    dir: str
    suppress: str
    cvf: str


@dataclass(frozen=True)
class RxnRelEdge:
    source_rxcui: str
    target_rxcui: str
    rela: str


@dataclass(frozen=True)
class TermResolution:
    input_term: str
    matched_term: str
    match_stage: str
    match_status: str
    rxcui: str
    canonical_name: str
    canonical_tty: str
    canonical_sab: str
    canonical_code: str
    candidate_count: int
    candidate_summary: str
    manual_review_needed: bool


@dataclass(frozen=True)
class IngredientResolution:
    ingredient_rxcui: str
    ingredient_name: str
    ingredient_tty: str
    ingredient_resolution_stage: str
    ingredient_path: str


def strip_trademark_markers(value: object) -> str:
    text = "" if value is None else str(value)
    text = TRADEMARK_SYMBOL_RE.sub("", text)
    text = unicodedata.normalize("NFKC", text)
    text = TRADEMARK_SYMBOL_RE.sub("", text)
    return TRADEMARK_TEXT_RE.sub("", text)


def normalize_abbrev_key(text: object) -> str:
    value = strip_trademark_markers(text)
    value = DASH_RE.sub("-", value)
    return MULTISPACE_RE.sub(" ", value.strip().upper())


def normalize_lookup_text(text: object) -> str:
    value = strip_trademark_markers(text)
    value = DASH_RE.sub("-", value)
    value = value.replace("，", ",").lower().strip()
    return MULTISPACE_RE.sub(" ", value)


def normalize_output_text(text: object) -> str:
    value = strip_trademark_markers(text)
    value = DASH_RE.sub("-", value)
    value = MULTISPACE_RE.sub(" ", value.strip())
    return EDGE_PUNCT_RE.sub("", value).strip()


def normalize_abbreviation_map(raw_map: Dict[str, List[str]]) -> Dict[str, List[str]]:
    return {normalize_abbrev_key(key): values for key, values in raw_map.items()}


SINGLE_AGENT_ABBREVIATION_EXPANSIONS = normalize_abbreviation_map(RAW_SINGLE_AGENT_ABBREVIATION_EXPANSIONS)
REGIMEN_ABBREVIATION_EXPANSIONS = normalize_abbreviation_map(RAW_REGIMEN_ABBREVIATION_EXPANSIONS)
CURATED_INGREDIENT_RESCUE_EXPANSIONS = normalize_abbreviation_map(RAW_CURATED_INGREDIENT_RESCUE_EXPANSIONS)
SHORT_AMBIGUOUS_LOOKUP_TERMS = {normalize_lookup_text(term) for term in RAW_SHORT_AMBIGUOUS_TERMS}


def tokenize(text: str) -> List[str]:
    return TOKEN_RE.findall(normalize_lookup_text(text))


def choose_substring_anchor(tokens: Sequence[str]) -> str:
    return "" if not tokens else sorted(tokens, key=lambda x: (x.isdigit(), -len(x), x))[0]


def token_boundary_pattern(term_norm: str) -> re.Pattern[str]:
    escaped = re.escape(term_norm).replace(r"\ ", r"\s+")
    return re.compile(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", re.IGNORECASE)


def is_safe_for_substring(term_norm: str) -> bool:
    compact = re.sub(r"[^a-z0-9]", "", term_norm)
    return bool(term_norm) and term_norm not in LOW_VALUE_TERMS and len(compact) >= 3


def ordered_unique(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for value in values:
        clean = normalize_output_text(value)
        key = normalize_lookup_text(clean)
        if clean and key not in seen:
            seen.add(key)
            out.append(clean)
    return out


INGREDIENT_COMPATIBILITY_STOP_TOKENS = {
    "acid",
    "base",
    "free",
    "anhydrous",
    "monohydrate",
    "hydrate",
    "sodium",
    "potassium",
    "calcium",
    "magnesium",
    "zinc",
    "hydrochloride",
    "hcl",
    "sulfate",
    "sulphate",
    "acetate",
    "succinate",
    "fumarate",
    "phosphate",
    "chloride",
    "bromide",
    "iodide",
    "liposomal",
    "pegylated",
    "drug",
    "product",
    "agent",
}


def significant_ingredient_tokens(text: object) -> set[str]:
    """Return tokens useful for checking ingredient-name compatibility.

    This is intentionally used only as a safety gate for PIN -> IN/MIN graph
    traversal. It must not participate in term matching.
    """
    normalized = normalize_lookup_text(text)
    salt_stripped = SALT_RE.sub(" ", normalized)
    tokens = set(TOKEN_RE.findall(salt_stripped))
    return {
        token
        for token in tokens
        if len(token) >= 3
        and not token.isdigit()
        and token not in INGREDIENT_COMPATIBILITY_STOP_TOKENS
    }


def _has_shared_token_stem(left_tokens: set[str], right_tokens: set[str], min_prefix: int = 6) -> bool:
    for left in left_tokens:
        for right in right_tokens:
            if left == right:
                return True
            if len(left) >= min_prefix and len(right) >= min_prefix:
                # Handles common salt/ion spelling variants such as clavulanic/clavulanate
                # without letting completely unrelated ingredients pass.
                if left[:min_prefix] == right[:min_prefix]:
                    return True
                if left in right or right in left:
                    return True
    return False


def ingredient_names_are_compatible(left: object, right: object) -> bool:
    left_tokens = significant_ingredient_tokens(left)
    right_tokens = significant_ingredient_tokens(right)
    if not left_tokens or not right_tokens:
        return False
    return _has_shared_token_stem(left_tokens, right_tokens)



def read_rrf_rows(path: Path) -> Iterable[List[str]]:
    if not path.exists():
        raise FileNotFoundError(f"Required RRF file not found: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter=RRF_DELIMITER)
        for row in reader:
            if row and row[-1] == "":
                row = row[:-1]
            yield row


def load_rxnconso(path: Path) -> Iterable[RxnConsoRow]:
    for line_number, row in enumerate(read_rrf_rows(path), start=1):
        if len(row) < 17:
            logger.warning("Skipping malformed RXNCONSO row %d with %d columns", line_number, len(row))
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


def load_rxnrel(path: Path) -> Iterable[RxnRelRow]:
    for line_number, row in enumerate(read_rrf_rows(path), start=1):
        if len(row) < 15:
            logger.warning("Skipping malformed RXNREL row %d with %d columns", line_number, len(row))
            continue
        yield RxnRelRow(
            rxcui1=row[0],
            rxaui1=row[1],
            stype1=row[2],
            rel=row[3],
            rxcui2=row[4],
            rxaui2=row[5],
            stype2=row[6],
            rela=row[7],
            rui=row[8],
            srui=row[9],
            sab=row[10],
            sl=row[11],
            rg=row[12],
            dir=row[13],
            suppress=row[14],
            cvf=row[15] if len(row) > 15 else "",
        )


def rank_conso_row(row: RxnConsoRow) -> Tuple[int, int, int, int, str]:
    return (
        TTY_PRIORITY.get(row.tty, DEFAULT_TTY_PRIORITY),
        SOURCE_PRIORITY.get(row.sab, DEFAULT_SOURCE_PRIORITY),
        0 if row.suppress != "O" else 1,
        0 if row.ispref == "Y" else 1,
        normalize_lookup_text(row.str_value),
    )


def rank_ingredient_candidate(row: RxnConsoRow, path_length: int) -> Tuple[int, int, Tuple[int, int, int, int, str]]:
    return (
        path_length,
        INGREDIENT_TTY_PRIORITY.get(row.tty, 99),
        rank_conso_row(row),
    )


def group_by_rxcui(rows: Sequence[RxnConsoRow]) -> Dict[str, List[RxnConsoRow]]:
    grouped: Dict[str, List[RxnConsoRow]] = defaultdict(list)
    for row in rows:
        grouped[row.rxcui].append(row)
    return grouped


def summarize_candidates(rows: Sequence[RxnConsoRow], limit: int = 8) -> str:
    summaries: List[Tuple[Tuple[int, int, int, int, str], str]] = []
    for rxcui, members in group_by_rxcui(rows).items():
        best = min(members, key=rank_conso_row)
        text = f"{best.str_value} [RXCUI={rxcui}; TTY={best.tty}; SAB={best.sab}]"
        summaries.append((rank_conso_row(best), text))
    return SUMMARY_DELIMITER.join(text for _, text in sorted(summaries, key=lambda x: x[0])[:limit])


def choose_best_rxcui(rows: Sequence[RxnConsoRow], index: "RxnConsoIndex") -> Optional[Tuple[str, RxnConsoRow]]:
    scored: List[Tuple[Tuple[int, int, int, int, str], str, RxnConsoRow]] = []
    for rxcui in group_by_rxcui(rows):
        if best := index.best_row_for_rxcui(rxcui):
            scored.append((rank_conso_row(best), rxcui, best))
    if not scored:
        return None
    _, rxcui, best = min(scored, key=lambda x: x[0])
    return rxcui, best


def is_explicitly_approved_short_term(term: str) -> bool:
    key = normalize_abbrev_key(term)
    return (
        key in SINGLE_AGENT_ABBREVIATION_EXPANSIONS
        or key in CURATED_INGREDIENT_RESCUE_EXPANSIONS
    )


def should_skip_short_ambiguous_term(term: str) -> bool:
    if should_skip_obvious_non_drug_lookup_term(term):
        return True

    norm = normalize_lookup_text(term)
    compact = re.sub(r"[^a-z0-9]", "", norm)

    if norm in SHORT_AMBIGUOUS_LOOKUP_TERMS:
        return not is_explicitly_approved_short_term(term)

    if len(compact) <= 4 and compact.isalpha():
        return not is_explicitly_approved_short_term(term)

    return False


def should_skip_obvious_non_drug_lookup_term(term: str) -> bool:
    """Skip numeric fragments, obvious chemical fragments, and broad drug classes.

    These terms may exist in RxNorm, but they are not acceptable drug anchors for
    this CTGov drug-term mapping table unless explicitly curated as single-agent
    aliases.
    """
    if is_explicitly_approved_short_term(term):
        return False

    norm = normalize_lookup_text(term)
    compact = re.sub(r"[^a-z0-9]", "", norm)

    if not compact:
        return True

    if norm in LOW_VALUE_TERMS:
        return True

    if norm in BROAD_CLASS_TERMS:
        return True

    return any(pattern.match(compact if pattern.pattern != r"^\d+-[a-z]+-\d+$" else norm)
               for pattern in OBVIOUS_FRAGMENT_PATTERNS)


def curated_ingredient_expansions_for(term: str) -> List[str]:
    key = normalize_abbrev_key(term)
    expansions = list(CURATED_INGREDIENT_RESCUE_EXPANSIONS.get(key, []))

    hyphenless_key = re.sub(r"[-\s]+", "", key)
    for known_key, known_expansions in CURATED_INGREDIENT_RESCUE_EXPANSIONS.items():
        if known_key != key and re.sub(r"[-\s]+", "", known_key) == hyphenless_key:
            expansions.extend(known_expansions)

    return ordered_unique(expansions)


def choose_best_ingredient_row(
    rows: Sequence[RxnConsoRow],
    conso_index: "RxnConsoIndex",
) -> Optional[Tuple[str, RxnConsoRow]]:
    candidates: List[Tuple[Tuple[int, Tuple[int, int, int, int, str]], str, RxnConsoRow]] = []
    for rxcui in group_by_rxcui(rows):
        best = conso_index.best_row_for_rxcui(rxcui)
        if best is None or best.tty not in INGREDIENT_CANDIDATE_TTYS:
            continue
        candidates.append(((INGREDIENT_TTY_PRIORITY.get(best.tty, 99), rank_conso_row(best)), rxcui, best))

    if not candidates:
        return None

    _, rxcui, best_row = min(candidates, key=lambda item: item[0])
    return rxcui, best_row


def resolve_curated_ingredient_alias(source_term: str, conso_index: "RxnConsoIndex") -> IngredientResolution:
    for ingredient_name in curated_ingredient_expansions_for(source_term):
        best = choose_best_ingredient_row(conso_index.exact_lookup(ingredient_name), conso_index)
        if best is None:
            continue
        ingredient_rxcui, best_row = best
        return IngredientResolution(
            ingredient_rxcui=ingredient_rxcui,
            ingredient_name=best_row.str_value,
            ingredient_tty=best_row.tty,
            ingredient_resolution_stage="CURATED_INGREDIENT_ALIAS",
            ingredient_path=f"{source_term} -> {ingredient_name} -> {ingredient_rxcui}",
        )

    return IngredientResolution("", "", "", "NO_CURATED_INGREDIENT_ALIAS", "")


def should_try_left_of_comma(term: str) -> bool:
    if "," not in term:
        return False

    pieces = [piece.strip() for piece in term.split(",") if piece.strip()]
    if len(pieces) != 2:
        return False

    left = pieces[0]
    compact_left = re.sub(r"[^a-z0-9]", "", normalize_lookup_text(left))
    if is_explicitly_approved_short_term(left):
        return True
    return len(compact_left) >= 5


class RxnConsoIndex:
    def __init__(self, rows: Sequence[RxnConsoRow]) -> None:
        self.rows = list(rows)
        self.by_normalized_str: Dict[str, List[RxnConsoRow]] = defaultdict(list)
        self.by_rxcui: Dict[str, List[RxnConsoRow]] = defaultdict(list)
        self.token_to_rows: Dict[str, List[RxnConsoRow]] = defaultdict(list)

        for row in self.rows:
            norm = normalize_lookup_text(row.str_value)
            if not norm:
                continue
            self.by_normalized_str[norm].append(row)
            self.by_rxcui[row.rxcui].append(row)
            for token in set(tokenize(norm)):
                self.token_to_rows[token].append(row)

    @classmethod
    def from_rrf_dir(cls, rxnorm_rrf_dir: Path) -> "RxnConsoIndex":
        return cls(list(load_rxnconso(rxnorm_rrf_dir / RXNCONSO_FILENAME)))

    def exact_lookup(self, term: str) -> List[RxnConsoRow]:
        return self.by_normalized_str.get(normalize_lookup_text(term), [])

    def best_row_for_rxcui(self, rxcui: str) -> Optional[RxnConsoRow]:
        rows = self.by_rxcui.get(rxcui, [])
        return min(rows, key=rank_conso_row) if rows else None

    def controlled_substring_lookup(self, term: str, max_candidate_rows: int = 5000) -> List[RxnConsoRow]:
        term_norm = normalize_lookup_text(term)
        if not is_safe_for_substring(term_norm):
            return []

        anchor = choose_substring_anchor(tokenize(term_norm))
        if not anchor:
            return []

        candidates = self.token_to_rows.get(anchor, [])
        if len(candidates) > max_candidate_rows:
            logger.debug(
                "Skipping broad substring lookup for %r because anchor %r has %d candidate rows",
                term,
                anchor,
                len(candidates),
            )
            return []

        pattern = token_boundary_pattern(term_norm)
        return [row for row in candidates if pattern.search(normalize_lookup_text(row.str_value))]


class RxnRelIndex:
    def __init__(self, rows: Sequence[RxnRelRow]) -> None:
        self.rows = list(rows)
        self.adjacency: Dict[str, List[RxnRelEdge]] = defaultdict(list)

        for row in self.rows:
            rela = row.rela or row.rel
            if not row.rxcui1 or not row.rxcui2:
                continue
            if row.sab != "RXNORM" or row.suppress == "O" or rela not in ALLOWED_INGREDIENT_RELAS:
                continue
            self.adjacency[row.rxcui1].append(RxnRelEdge(row.rxcui1, row.rxcui2, rela))
            self.adjacency[row.rxcui2].append(RxnRelEdge(row.rxcui2, row.rxcui1, f"inverse:{rela}"))

    @classmethod
    def from_rrf_dir(cls, rxnorm_rrf_dir: Path) -> "RxnRelIndex":
        return cls(list(load_rxnrel(rxnorm_rrf_dir / RXNREL_FILENAME)))

    def resolve_ingredient(
        self,
        matched_rxcui: str,
        conso_index: RxnConsoIndex,
        source_term: str = "",
        max_depth: int = 5,
    ) -> IngredientResolution:
        if not matched_rxcui:
            return IngredientResolution("", "", "", "NO_MATCHED_RXCUI", "")

        direct_best = conso_index.best_row_for_rxcui(matched_rxcui)
        if direct_best and direct_best.tty in PREFERRED_INGREDIENT_TTYS:
            return IngredientResolution(
                ingredient_rxcui=matched_rxcui,
                ingredient_name=direct_best.str_value,
                ingredient_tty=direct_best.tty,
                ingredient_resolution_stage="MATCHED_RXCUI_IS_INGREDIENT",
                ingredient_path=matched_rxcui,
            )

        graph_result = self._resolve_ingredient_by_graph(
            matched_rxcui,
            conso_index,
            matched_row=direct_best,
            source_term=source_term,
            max_depth=max_depth,
        )
        if graph_result:
            return graph_result

        if source_term:
            curated = resolve_curated_ingredient_alias(source_term, conso_index)
            if curated.ingredient_rxcui:
                return curated

        fallback = self._ingredient_resolution_from_row(
            matched_rxcui,
            direct_best,
            "MATCHED_RXCUI_IS_PRECISE_INGREDIENT_FALLBACK",
        )
        return fallback or IngredientResolution("", "", "", "NO_INGREDIENT_PATH_FOUND", "")

    def _resolve_ingredient_by_graph(
        self,
        matched_rxcui: str,
        conso_index: RxnConsoIndex,
        matched_row: Optional[RxnConsoRow],
        source_term: str,
        max_depth: int,
    ) -> Optional[IngredientResolution]:
        queue: deque[tuple[str, list[str], list[str]]] = deque([(matched_rxcui, [matched_rxcui], [])])
        visited = {matched_rxcui}
        candidates: list[tuple[Tuple[int, int, Tuple[int, int, int, int, str]], str, RxnConsoRow, list[str], list[str]]] = []

        while queue:
            current_rxcui, path, relas = queue.popleft()
            if len(path) - 1 >= max_depth:
                continue

            for edge in self.adjacency.get(current_rxcui, []):
                if edge.target_rxcui in visited:
                    continue
                visited.add(edge.target_rxcui)

                next_path = [*path, edge.target_rxcui]
                next_relas = [*relas, edge.rela]
                best = conso_index.best_row_for_rxcui(edge.target_rxcui)

                if best and best.tty in INGREDIENT_CANDIDATE_TTYS:
                    if self._is_acceptable_ingredient_candidate(matched_row, source_term, best):
                        candidates.append(
                            (
                                rank_ingredient_candidate(best, path_length=len(next_path) - 1),
                                edge.target_rxcui,
                                best,
                                next_path,
                                next_relas,
                            )
                        )

                queue.append((edge.target_rxcui, next_path, next_relas))

        if not candidates:
            return None

        _, ingredient_rxcui, best_row, path, relas = min(candidates, key=lambda x: x[0])
        return IngredientResolution(
            ingredient_rxcui=ingredient_rxcui,
            ingredient_name=best_row.str_value,
            ingredient_tty=best_row.tty,
            ingredient_resolution_stage="RXNREL_GRAPH_TRAVERSAL",
            ingredient_path=format_ingredient_path(path, relas),
        )

    @staticmethod
    def _is_acceptable_ingredient_candidate(
        matched_row: Optional[RxnConsoRow],
        source_term: str,
        candidate_row: RxnConsoRow,
    ) -> bool:
        """Guard against graph drift from precise ingredients into co-ingredients.

        The historical resolver uses RXNREL as an undirected graph. That is useful
        for brands/products, but for a matched precise ingredient (PIN), an IN/MIN
        candidate reached through a product/tradename node is only safe when the
        candidate still looks like the same chemical/biologic name. Otherwise the
        resolver can pick a co-ingredient from a combination product.

        This gate is deliberately narrow: it only filters PIN -> IN/MIN graph
        candidates. Brand-name and product-name ingredient resolution is left
        unchanged to avoid broad regressions.
        """
        if matched_row is None:
            return True
        if matched_row.tty != "PIN":
            return True
        if candidate_row.tty not in PREFERRED_INGREDIENT_TTYS:
            return True

        return (
            ingredient_names_are_compatible(matched_row.str_value, candidate_row.str_value)
            or ingredient_names_are_compatible(source_term, candidate_row.str_value)
        )

    @staticmethod
    def _ingredient_resolution_from_row(
        rxcui: str,
        row: Optional[RxnConsoRow],
        stage: str,
    ) -> Optional[IngredientResolution]:
        if row and row.tty in INGREDIENT_CANDIDATE_TTYS:
            return IngredientResolution(rxcui, row.str_value, row.tty, stage, rxcui)
        return None


def format_ingredient_path(path: Sequence[str], relas: Sequence[str]) -> str:
    pieces: list[str] = []
    for index, rxcui in enumerate(path):
        if index < len(relas):
            pieces.append(f"{rxcui}[{relas[index]}]")
        else:
            pieces.append(rxcui)
    return " -> ".join(pieces)


def clean_for_stage2(term: str) -> List[str]:
    variants = [term]
    current = PARENS_CONTENT_RE.sub(" ", term)
    for pattern in (DOSE_RE, DOSAGE_FORM_ROUTE_RE, SCHEDULE_RE):
        current = pattern.sub(" ", current)
    current = MULTISPACE_RE.sub(" ", current).strip(" ,;:-/")
    if current and normalize_lookup_text(current) != normalize_lookup_text(term):
        variants.append(current)

    salt_removed = MULTISPACE_RE.sub(" ", SALT_RE.sub(" ", current)).strip(" ,;:-/")
    if salt_removed and normalize_lookup_text(salt_removed) != normalize_lookup_text(current):
        variants.append(salt_removed)

    if should_try_left_of_comma(term):
        left_of_comma = term.split(",", 1)[0].strip()
        if left_of_comma and normalize_lookup_text(left_of_comma) != normalize_lookup_text(term):
            variants.append(left_of_comma)

    return ordered_unique(variants)


def expand_for_stage3(term: str) -> List[str]:
    variants = [term]
    key = normalize_abbrev_key(term)
    hyphenless_key = re.sub(r"[-\s]+", "", key)

    for mapping in (
        SINGLE_AGENT_ABBREVIATION_EXPANSIONS,
        CURATED_INGREDIENT_RESCUE_EXPANSIONS,
    ):
        variants.extend(mapping.get(key, []))
        for known_key, expansions in mapping.items():
            if known_key != key and re.sub(r"[-\s]+", "", known_key) == hyphenless_key:
                variants.extend(expansions)

    variants.append(re.sub(r"([A-Za-z]+)[-\s]+(\d+)", r"\1\2", term))
    variants.append(re.sub(r"([A-Za-z]+)(\d+)", r"\1 \2", term))

    for pattern, replacement in SPELLING_VARIANT_SUBS:
        candidate = pattern.sub(replacement, term)
        if candidate != term:
            variants.append(candidate)

    return ordered_unique(variants)


def resolve_rows_to_term(
    input_term: str,
    matched_term: str,
    stage: str,
    rows: Sequence[RxnConsoRow],
    index: RxnConsoIndex,
    allow_multiple_review: bool,
) -> Optional[TermResolution]:
    if not rows:
        return None

    candidate_count = len(group_by_rxcui(rows))
    summary = summarize_candidates(rows)
    best = choose_best_rxcui(rows, index)
    best_row = best[1] if best else None

    if candidate_count > 1 and allow_multiple_review:
        return TermResolution(
            input_term=input_term,
            matched_term=matched_term,
            match_stage=stage,
            match_status="MULTIPLE_CANDIDATES_REVIEW",
            rxcui=best[0] if best else "",
            canonical_name=best_row.str_value if best_row else "",
            canonical_tty=best_row.tty if best_row else "",
            canonical_sab=best_row.sab if best_row else "",
            canonical_code=best_row.code if best_row else "",
            candidate_count=candidate_count,
            candidate_summary=summary,
            manual_review_needed=True,
        )

    if candidate_count > 1 or best is None or best_row is None:
        return None

    return TermResolution(
        input_term=input_term,
        matched_term=matched_term,
        match_stage=stage,
        match_status="MATCHED",
        rxcui=best[0],
        canonical_name=best_row.str_value,
        canonical_tty=best_row.tty,
        canonical_sab=best_row.sab,
        canonical_code=best_row.code,
        candidate_count=candidate_count,
        candidate_summary=summary,
        manual_review_needed=False,
    )


def resolve_term(term: str, index: RxnConsoIndex) -> TermResolution:
    if should_skip_short_ambiguous_term(term):
        return TermResolution(
            input_term=term,
            matched_term="",
            match_stage="SKIPPED_AMBIGUOUS_SHORT_TERM",
            match_status="UNMATCHED",
            rxcui="",
            canonical_name="",
            canonical_tty="",
            canonical_sab="",
            canonical_code="",
            candidate_count=0,
            candidate_summary="",
            manual_review_needed=True,
        )

    stage1 = resolve_rows_to_term(term, term, "STAGE1_EXACT_STR", index.exact_lookup(term), index, True)
    if stage1:
        return stage1

    for cleaned in clean_for_stage2(term):
        if normalize_lookup_text(cleaned) == normalize_lookup_text(term):
            continue
        if resolved := resolve_rows_to_term(
            term,
            cleaned,
            "STAGE2_CLEANED_EXACT_STR",
            index.exact_lookup(cleaned),
            index,
            True,
        ):
            return resolved

    for expanded in expand_for_stage3(term):
        if normalize_lookup_text(expanded) == normalize_lookup_text(term):
            continue
        if resolved := resolve_rows_to_term(
            term,
            expanded,
            "STAGE3_EXPANSION_EXACT_STR",
            index.exact_lookup(expanded),
            index,
            True,
        ):
            return resolved

    rows = index.controlled_substring_lookup(term)
    if resolved := resolve_rows_to_term(term, term, "STAGE4_CONTROLLED_SUBSTRING", rows, index, False):
        return resolved

    if rows:
        best = choose_best_rxcui(rows, index)
        best_row = best[1] if best else None
        return TermResolution(
            input_term=term,
            matched_term=term,
            match_stage="STAGE4_CONTROLLED_SUBSTRING",
            match_status="MULTIPLE_CANDIDATES_REVIEW",
            rxcui=best[0] if best else "",
            canonical_name=best_row.str_value if best_row else "",
            canonical_tty=best_row.tty if best_row else "",
            canonical_sab=best_row.sab if best_row else "",
            canonical_code=best_row.code if best_row else "",
            candidate_count=len(group_by_rxcui(rows)),
            candidate_summary=summarize_candidates(rows),
            manual_review_needed=True,
        )

    return TermResolution(term, "", "UNMATCHED", "UNMATCHED", "", "", "", "", "", 0, "", True)
