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

SHEET_INPUT = "processed interventions"
COL_INPUT = "unique_interventions_processed"

RXNCONSO_FILENAME = "RXNCONSO.RRF"
RXNSTY_FILENAME = "RXNSTY.RRF"

RRF_DELIMITER = "|"
AGG_DELIMITER = " | "
REMOVE_PARENS_RE = re.compile(r"\s*\([^)]*\)")
MULTISPACE_RE = re.compile(r"\s+")
DASH_RE = re.compile(r"[\u2010\u2011\u2012\u2013\u2014\u2212]")
TRADEMARK_RE = re.compile(r"[®™]")
PUNCT_EDGE_RE = re.compile(r"^[\s\.,;:/\\+\-]+|[\s\.,;:/\\+\-]+$")

DEFAULT_FUZZY_THRESHOLD = 0.94

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

REVIEW_CODE_RE = re.compile(r"\b[A-Z]{2,}-?\d{2,}[A-Z0-9-]*\b")
CLASS_OR_REGIMEN_RE = re.compile(
    r"\b(inhibitor|agonist|antagonist|antibody-drug conjugate|adc|chemotherapy|immunotherapy|regimen|combination)\b",
    re.IGNORECASE,
)

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
    score: float
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
    def __init__(
        self,
        conso_rows: Sequence[RxnConsoRow],
        sty_rows: Sequence[RxnStyRow],
    ) -> None:
        self.conso_rows = list(conso_rows)
        self.sty_rows = list(sty_rows)

        self.by_normalized_string: Dict[str, List[RxnConsoRow]] = defaultdict(list)
        self.by_rxcui: Dict[str, List[RxnConsoRow]] = defaultdict(list)
        self.sty_by_rxcui: Dict[str, List[RxnStyRow]] = defaultdict(list)
        self.semantic_types_by_rxcui: Dict[str, List[str]] = defaultdict(list)
        self.token_to_keys: Dict[str, Set[str]] = defaultdict(set)

        for row in self.conso_rows:
            key = normalize_lookup_text(row.str_value)
            if not key:
                continue
            self.by_normalized_string[key].append(row)
            self.by_rxcui[row.rxcui].append(row)
            for token in tokenize_normalized_string(key):
                self.token_to_keys[token].add(key)

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
        return self._rows_to_candidates(alias=alias, rows=self.by_normalized_string.get(key, []), score=1.0)

    def lookup_variants(self, alias: str) -> List[MatchCandidate]:
        variants = generate_lookup_variants(alias)
        candidates: List[MatchCandidate] = []
        seen: Set[Tuple[str, str]] = set()
        for variant in variants:
            key = normalize_lookup_text(variant)
            if not key:
                continue
            for row in self.by_normalized_string.get(key, []):
                dedup_key = (row.rxcui, row.rxaui)
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)
                candidates.append(
                    MatchCandidate(
                        alias=alias,
                        score=0.99,
                        conso=row,
                        semantic_types=tuple(self.semantic_types_by_rxcui.get(row.rxcui, [])),
                    )
                )
        return candidates

    def lookup_fuzzy(
        self,
        alias: str,
        threshold: float = DEFAULT_FUZZY_THRESHOLD,
        max_candidates: int = 10,
        max_keys_to_score: int = 5000,
    ) -> List[MatchCandidate]:
        alias_norm = normalize_lookup_text(alias)
        if not alias_norm:
            return []

        alias_tokens = tokenize_normalized_string(alias_norm)
        if not alias_tokens:
            return []

        candidate_keys: Set[str] = set()
        for token in alias_tokens:
            candidate_keys.update(self.token_to_keys.get(token, set()))

        if len(candidate_keys) > max_keys_to_score:
            narrowed = [key for key in candidate_keys if key.startswith(alias_tokens[0])]
            candidate_keys = set(narrowed[:max_keys_to_score]) if narrowed else set(list(candidate_keys)[:max_keys_to_score])

        scored: List[MatchCandidate] = []
        for key in candidate_keys:
            score = jaccard_token_similarity(alias_norm, key)
            if score < threshold:
                continue
            for row in self.by_normalized_string.get(key, []):
                scored.append(
                    MatchCandidate(
                        alias=alias,
                        score=score,
                        conso=row,
                        semantic_types=tuple(self.semantic_types_by_rxcui.get(row.rxcui, [])),
                    )
                )

        deduped = deduplicate_candidates(scored)
        return sorted(deduped, key=rank_candidate)[:max_candidates]

    def get_best_row_for_rxcui(self, rxcui: str) -> Optional[RxnConsoRow]:
        rows = self.by_rxcui.get(rxcui, [])
        if not rows:
            return None
        return sorted(rows, key=rank_conso_row)[0]

    def get_rxnconso_aggregate(self, rxcui: str) -> Dict[str, str]:
        rows = self.by_rxcui.get(rxcui, [])
        return aggregate_rxnconso_rows(rows)

    def get_rxnsty_aggregate(self, rxcui: str) -> Dict[str, str]:
        rows = self.sty_by_rxcui.get(rxcui, [])
        return aggregate_rxnsty_rows(rows)

    def _rows_to_candidates(
        self,
        alias: str,
        rows: Sequence[RxnConsoRow],
        score: float,
    ) -> List[MatchCandidate]:
        return [
            MatchCandidate(
                alias=alias,
                score=score,
                conso=row,
                semantic_types=tuple(self.semantic_types_by_rxcui.get(row.rxcui, [])),
            )
            for row in rows
        ]


def normalize_lookup_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text))
    text = TRADEMARK_RE.sub("", text)
    text = DASH_RE.sub("-", text)
    text = text.strip().lower()
    text = MULTISPACE_RE.sub(" ", text)
    return text


def tokenize_normalized_string(text: str) -> List[str]:
    return [token for token in re.split(r"[^a-z0-9]+", text) if token]


def generate_lookup_variants(text: str) -> List[str]:
    raw = str(text).strip()
    variants = [raw]

    without_parens = REMOVE_PARENS_RE.sub("", raw).strip()
    if without_parens and without_parens != raw:
        variants.append(without_parens)

    without_edge_punct = PUNCT_EDGE_RE.sub("", raw).strip()
    if without_edge_punct and without_edge_punct != raw:
        variants.append(without_edge_punct)

    hyphen_space = raw.replace("-", " ")
    if hyphen_space != raw:
        variants.append(hyphen_space)

    with_spaced_symbols = MULTISPACE_RE.sub(" ", raw.replace("/", " / ").replace(",", " , ")).strip()
    if with_spaced_symbols != raw:
        variants.append(with_spaced_symbols)

    deduped: List[str] = []
    seen = set()
    for value in variants:
        norm = normalize_lookup_text(value)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        deduped.append(value)
    return deduped


def split_top_level_pipes(text: str) -> List[str]:
    return [part.strip() for part in re.split(r"\s*\|\s*", str(text)) if part.strip()]


def jaccard_token_similarity(a: str, b: str) -> float:
    tokens_a = set(tokenize_normalized_string(a))
    tokens_b = set(tokenize_normalized_string(b))
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


def semantic_type_priority(semantic_types: Sequence[str]) -> int:
    if not semantic_types:
        return DEFAULT_SEMANTIC_TYPE_PRIORITY
    return min(SEMANTIC_TYPE_PRIORITY.get(x, DEFAULT_SEMANTIC_TYPE_PRIORITY) for x in semantic_types)


def rank_conso_row(row: RxnConsoRow) -> Tuple[int, int, int, int, str]:
    tty_priority = TTY_PRIORITY.get(row.tty, DEFAULT_TTY_PRIORITY)
    source_priority = SOURCE_PRIORITY.get(row.sab, DEFAULT_SOURCE_PRIORITY)
    suppress_priority = 0 if row.suppress != "O" else 1
    ispref_priority = 0 if row.ispref == "Y" else 1
    return tty_priority, source_priority, suppress_priority, ispref_priority, row.str_value.lower()


def rank_candidate(candidate: MatchCandidate) -> Tuple[float, int, int, int, int, str]:
    return (
        -candidate.score,
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
    if not candidates:
        return None

    grouped: Dict[str, List[MatchCandidate]] = defaultdict(list)
    for candidate in candidates:
        grouped[candidate.conso.rxcui].append(candidate)

    scored_groups: List[Tuple[Tuple[float, int, int, int, str], str, MatchCandidate, RxnConsoRow]] = []
    for rxcui, members in grouped.items():
        best_candidate = sorted(members, key=rank_candidate)[0]
        best_row = index.get_best_row_for_rxcui(rxcui)
        if best_row is None:
            continue
        score_tuple = (
            -best_candidate.score,
            TTY_PRIORITY.get(best_row.tty, DEFAULT_TTY_PRIORITY),
            semantic_type_priority(index.semantic_types_by_rxcui.get(rxcui, [])),
            SOURCE_PRIORITY.get(best_row.sab, DEFAULT_SOURCE_PRIORITY),
            best_row.str_value.lower(),
        )
        scored_groups.append((score_tuple, rxcui, best_candidate, best_row))

    if not scored_groups:
        return None

    _, winning_rxcui, winning_candidate, winning_row = sorted(scored_groups, key=lambda x: x[0])[0]
    return winning_rxcui, winning_candidate, winning_row


def summarize_candidates(candidates: Sequence[MatchCandidate], index: RxNormIndex, limit: int = 8) -> str:
    grouped: Dict[str, List[MatchCandidate]] = defaultdict(list)
    for candidate in candidates:
        grouped[candidate.conso.rxcui].append(candidate)

    summarized: List[Tuple[Tuple[float, int, int, int, str], str]] = []
    for rxcui, members in grouped.items():
        best_candidate = sorted(members, key=rank_candidate)[0]
        best_row = index.get_best_row_for_rxcui(rxcui)
        if best_row is None:
            continue
        score_tuple = (
            -best_candidate.score,
            TTY_PRIORITY.get(best_row.tty, DEFAULT_TTY_PRIORITY),
            semantic_type_priority(index.semantic_types_by_rxcui.get(rxcui, [])),
            SOURCE_PRIORITY.get(best_row.sab, DEFAULT_SOURCE_PRIORITY),
            best_row.str_value.lower(),
        )
        summarized.append(
            (
                score_tuple,
                f"{best_row.str_value} [RXCUI={rxcui}; TTY={best_row.tty}; via={best_candidate.alias}; score={best_candidate.score:.3f}]",
            )
        )

    return AGG_DELIMITER.join(text for _, text in sorted(summarized, key=lambda x: x[0])[:limit])


def unique_join(values: Sequence[str]) -> str:
    ordered: List[str] = []
    seen: Set[str] = set()
    for value in values:
        cleaned = str(value).strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        ordered.append(cleaned)
    return AGG_DELIMITER.join(ordered)


def aggregate_rxnconso_rows(rows: Sequence[RxnConsoRow]) -> Dict[str, str]:
    if not rows:
        return {f"rx_{field}": "" for field in RXNCONSO_OUTPUT_FIELDS}

    return {
        "rx_rxcui": unique_join([row.rxcui for row in rows]),
        "rx_lat": unique_join([row.lat for row in rows]),
        "rx_ts": unique_join([row.ts for row in rows]),
        "rx_lui": unique_join([row.lui for row in rows]),
        "rx_stt": unique_join([row.stt for row in rows]),
        "rx_sui": unique_join([row.sui for row in rows]),
        "rx_ispref": unique_join([row.ispref for row in rows]),
        "rx_rxaui": unique_join([row.rxaui for row in rows]),
        "rx_saui": unique_join([row.saui for row in rows]),
        "rx_scui": unique_join([row.scui for row in rows]),
        "rx_sdui": unique_join([row.sdui for row in rows]),
        "rx_sab": unique_join([row.sab for row in rows]),
        "rx_tty": unique_join([row.tty for row in rows]),
        "rx_code": unique_join([row.code for row in rows]),
        "rx_str": unique_join([row.str_value for row in rows]),
        "rx_srl": unique_join([row.srl for row in rows]),
        "rx_suppress": unique_join([row.suppress for row in rows]),
        "rx_cvf": unique_join([row.cvf for row in rows]),
    }


def aggregate_rxnsty_rows(rows: Sequence[RxnStyRow]) -> Dict[str, str]:
    if not rows:
        return {f"rx_{field}": "" for field in RXNSTY_OUTPUT_FIELDS}

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
        return

    for row in read_rrf_rows(path):
        if len(row) < 4:
            logger.warning("Skipping malformed RXNSTY row with %d columns", len(row))
            continue
        yield RxnStyRow(
            rxcui=row[0],
            tui=row[1],
            stn=row[2],
            sty=row[3],
            atui=row[4] if len(row) > 4 else "",
            cvf=row[5] if len(row) > 5 else "",
        )


def classify_no_match(alias_group: Sequence[str]) -> str:
    if not alias_group:
        return "NO_MATCH"

    joined = AGG_DELIMITER.join(alias_group)
    if CLASS_OR_REGIMEN_RE.search(joined):
        return "CLASS_OR_REGIMEN"
    if REVIEW_CODE_RE.search(joined):
        return "INVESTIGATIONAL_OR_CODE"
    return "NO_MATCH"


def map_alias_group(
    alias_group: Sequence[str],
    index: RxNormIndex,
    fuzzy_threshold: float,
    enable_fuzzy: bool,
) -> ResolvedMatch:
    all_candidates: List[MatchCandidate] = []

    for alias in alias_group:
        exact = index.lookup_exact(alias)
        if exact:
            all_candidates.extend(exact)
            continue

        variants = index.lookup_variants(alias)
        if variants:
            all_candidates.extend(variants)
            continue

    all_candidates = deduplicate_candidates(all_candidates)

    if not all_candidates and enable_fuzzy:
        fuzzy_candidates: List[MatchCandidate] = []
        for alias in alias_group:
            fuzzy_candidates.extend(index.lookup_fuzzy(alias, threshold=fuzzy_threshold))
        fuzzy_candidates = deduplicate_candidates(fuzzy_candidates)
        if fuzzy_candidates:
            fuzzy_winner = choose_best_rxcui(fuzzy_candidates, index)
            if fuzzy_winner is not None:
                winning_rxcui, winning_candidate, _ = fuzzy_winner
                return ResolvedMatch(
                    matched_alias=winning_candidate.alias,
                    match_status="FUZZY_CANDIDATE",
                    matched_rxcui=winning_rxcui,
                    candidate_count=len({candidate.conso.rxcui for candidate in fuzzy_candidates}),
                    candidate_summary=summarize_candidates(fuzzy_candidates, index=index),
                )

    if not all_candidates:
        status = classify_no_match(alias_group)
        return ResolvedMatch(
            matched_alias=None,
            match_status=status,
            matched_rxcui=None,
            candidate_count=0,
            candidate_summary="",
        )

    winner = choose_best_rxcui(all_candidates, index)
    if winner is None:
        raise RuntimeError("Expected a winning RXCUI after non-empty candidate list")

    winning_rxcui, winning_candidate, _ = winner
    candidate_rxcuis = {candidate.conso.rxcui for candidate in all_candidates}
    ambiguous = len(candidate_rxcuis) > 1

    return ResolvedMatch(
        matched_alias=winning_candidate.alias,
        match_status="MULTIPLE_CANDIDATES" if ambiguous else ("EXACT" if winning_candidate.score == 1.0 else "EXACT_VARIANT"),
        matched_rxcui=winning_rxcui,
        candidate_count=len(candidate_rxcuis),
        candidate_summary=summarize_candidates(all_candidates, index=index),
    )


def build_output_row(
    original_row: Dict[str, object],
    result: ResolvedMatch,
    index: RxNormIndex,
) -> Dict[str, object]:
    output_row = dict(original_row)
    output_row["rx_match_alias"] = result.matched_alias
    output_row["rx_match_status"] = result.match_status
    output_row["rx_candidate_count"] = result.candidate_count
    output_row["rx_candidate_summary"] = result.candidate_summary

    if not result.matched_rxcui:
        for field in RXNCONSO_OUTPUT_FIELDS:
            output_row[f"rx_{field}"] = ""
        for field in RXNSTY_OUTPUT_FIELDS:
            output_row[f"rx_{field}"] = ""
        output_row["rx_cvf_sty"] = ""
        return output_row

    output_row.update(index.get_rxnconso_aggregate(result.matched_rxcui))
    output_row.update(index.get_rxnsty_aggregate(result.matched_rxcui))
    return output_row


def map_interventions_dataframe(
    df_input: pd.DataFrame,
    index: RxNormIndex,
    fuzzy_threshold: float,
    enable_fuzzy: bool,
) -> pd.DataFrame:
    if COL_INPUT not in df_input.columns:
        raise KeyError(f"Column {COL_INPUT!r} not found in input sheet {SHEET_INPUT!r}")

    output_rows: List[Dict[str, object]] = []
    for row_number, record in enumerate(df_input.fillna("").to_dict(orient="records"), start=1):
        original = str(record.get(COL_INPUT, "")).strip()
        aliases = split_top_level_pipes(original)
        result = map_alias_group(
            aliases,
            index=index,
            fuzzy_threshold=fuzzy_threshold,
            enable_fuzzy=enable_fuzzy,
        )
        output_row = build_output_row(record, result, index)
        output_rows.append(output_row)

        if row_number % 1000 == 0:
            logger.info("Mapped %d intervention rows", row_number)

    return pd.DataFrame(output_rows)


def process_workbook(
    input_excel: Path,
    output_excel: Path,
    rxnorm_rrf_dir: Path,
    fuzzy_threshold: float,
    enable_fuzzy: bool,
) -> None:
    if not input_excel.exists():
        raise FileNotFoundError(f"Input workbook not found: {input_excel}")
    if not rxnorm_rrf_dir.exists():
        raise FileNotFoundError(f"RxNorm RRF directory not found: {rxnorm_rrf_dir}")

    xls = pd.ExcelFile(input_excel)
    if SHEET_INPUT not in xls.sheet_names:
        raise KeyError(f"Worksheet {SHEET_INPUT!r} not found. Available: {xls.sheet_names}")

    logger.info("Loading RxNorm lexical index from %s", rxnorm_rrf_dir)
    index = RxNormIndex.from_rrf_dir(rxnorm_rrf_dir)
    logger.info(
        "Loaded RxNorm lexical index: %d RXNCONSO rows, %d normalized keys, %d RXNSTY rows",
        len(index.conso_rows),
        len(index.by_normalized_string),
        len(index.sty_rows),
    )

    df_input = pd.read_excel(input_excel, sheet_name=SHEET_INPUT, dtype="string")
    logger.info("Read %d intervention rows from %s / %s", len(df_input), input_excel, SHEET_INPUT)

    df_output = map_interventions_dataframe(
        df_input=df_input,
        index=index,
        fuzzy_threshold=fuzzy_threshold,
        enable_fuzzy=enable_fuzzy,
    )

    output_excel.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output_excel, engine="openpyxl") as writer:
        df_output.to_excel(writer, sheet_name=SHEET_INPUT, index=False)

    logger.info("Wrote mapped workbook to %s", output_excel)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Map processed CT.gov intervention names to local RxNorm concepts")
    parser.add_argument("--input_excel", required=True, type=Path, help="ctgov_interventions_processed.xlsx")
    parser.add_argument("--rxnorm_rrf_dir", required=True, type=Path, help="Directory containing RXNCONSO.RRF and optional RXNSTY.RRF")
    parser.add_argument("--output_excel", required=True, type=Path, help="Output workbook path")
    parser.add_argument("--fuzzy_threshold", default=DEFAULT_FUZZY_THRESHOLD, type=float, help="Token Jaccard threshold for fuzzy review candidates")
    parser.add_argument(
        "--enable_fuzzy",
        action="store_true",
        help="Enable bounded fuzzy candidate generation. Off by default because exact and exact-variant matching are preferred.",
    )
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
        fuzzy_threshold=args.fuzzy_threshold,
        enable_fuzzy=args.enable_fuzzy,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())