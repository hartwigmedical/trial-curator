from __future__ import annotations

import argparse
import csv
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import DefaultDict, Dict, Iterator, List, Optional, Sequence, Set, Tuple
from collections import defaultdict

import pandas as pd

logger = logging.getLogger(__name__)

SHEET_INPUT = "interventions"
COL_ALIASES = "intervention_all_aliases"
AGG_DELIMITER = " | "
RRF_DELIMITER = "|"
RXNCONSO_FILENAME = "RXNCONSO.RRF"
TSV_BATCH_SIZE_DEFAULT = 200_000
LOG_SCAN_EVERY = 500_000
TOKEN_RE = re.compile(r"[a-z0-9]+")
MIN_ANCHOR_LEN = 3

RXNCONSO_OUTPUT_FIELDS = [
    "rxcui", "lat", "ts", "lui", "stt", "sui", "ispref", "rxaui", "saui", "scui",
    "sdui", "sab", "tty", "code", "str", "srl", "suppress", "cvf",
]

OUTPUT_COLUMNS = [
    "nct_id",
    "intervention_index",
    "intervention_type",
    "intervention_name",
    "intervention_description",
    "intervention_otherNames",
    "intervention_armGroupLabels",
    "intervention_all_aliases",
    "matched_term_in_RxNorm",
    "matched_term_in_RxNorm_normalized",
    "matched_term_alias_position",
    "match_method",
    "rxnconso_raw_row",
] + [f"rx_{field}" for field in RXNCONSO_OUTPUT_FIELDS]


@dataclass(frozen=True)
class RxnConsoMatch:
    raw_line: str
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
class AliasEntry:
    alias_norm: str
    alias_original: str
    alias_pos: int
    row_idx: int
    anchor_token: str


def normalize_alias(text: str) -> str:
    return str(text).strip().lower()


def split_aliases(value: object) -> List[str]:
    if value is None or pd.isna(value):
        return []
    aliases: List[str] = []
    seen: Set[str] = set()
    for part in str(value).split(AGG_DELIMITER):
        clean = str(part).strip()
        if not clean:
            continue
        norm = normalize_alias(clean)
        if norm in seen:
            continue
        seen.add(norm)
        aliases.append(clean)
    return aliases


def tokenize(text: str) -> List[str]:
    return TOKEN_RE.findall(text.lower())


def choose_anchor_token(alias_norm: str) -> str:
    tokens = tokenize(alias_norm)
    if not tokens:
        return alias_norm
    viable = [tok for tok in tokens if len(tok) >= MIN_ANCHOR_LEN]
    if viable:
        # longest token is a strong cheap filter; ties broken lexicographically for determinism
        viable.sort(key=lambda x: (-len(x), x))
        return viable[0]
    return max(tokens, key=len)


def parse_rxnconso_line(raw_line: str) -> RxnConsoMatch:
    row = raw_line.rstrip("\n").split(RRF_DELIMITER)
    if row and row[-1] == "":
        row = row[:-1]
    if len(row) < 17:
        raise ValueError(f"Malformed RXNCONSO row with {len(row)} columns: {raw_line[:200]}")
    return RxnConsoMatch(
        raw_line=raw_line.rstrip("\n"),
        rxcui=row[0], lat=row[1], ts=row[2], lui=row[3], stt=row[4], sui=row[5],
        ispref=row[6], rxaui=row[7], saui=row[8], scui=row[9], sdui=row[10],
        sab=row[11], tty=row[12], code=row[13], str_value=row[14],
        srl=row[15] if len(row) > 15 else "",
        suppress=row[16] if len(row) > 16 else "",
        cvf=row[17] if len(row) > 17 else "",
    )


def build_alias_registry(
    df_input: pd.DataFrame,
) -> Tuple[DefaultDict[str, List[AliasEntry]], Dict[int, List[str]], int]:
    anchor_to_entries: DefaultDict[str, List[AliasEntry]] = defaultdict(list)
    aliases_by_input_row: Dict[int, List[str]] = {}
    unique_aliases: Set[str] = set()

    for row_idx, value in enumerate(df_input[COL_ALIASES].tolist()):
        aliases = split_aliases(value)
        aliases_by_input_row[row_idx] = aliases
        for alias_pos, alias in enumerate(aliases, start=1):
            alias_norm = normalize_alias(alias)
            unique_aliases.add(alias_norm)
            anchor = choose_anchor_token(alias_norm)
            anchor_to_entries[anchor].append(
                AliasEntry(
                    alias_norm=alias_norm,
                    alias_original=alias,
                    alias_pos=alias_pos,
                    row_idx=row_idx,
                    anchor_token=anchor,
                )
            )
    return anchor_to_entries, aliases_by_input_row, len(unique_aliases)


def iter_rxnconso_matches(
    rxnconso_path: Path,
    anchor_to_entries: DefaultDict[str, List[AliasEntry]],
) -> Iterator[Tuple[int, int, str, str, RxnConsoMatch]]:
    if not rxnconso_path.exists():
        raise FileNotFoundError(f"RXNCONSO not found: {rxnconso_path}")

    logger.info(
        "Built alias anchor registry with %d unique anchor tokens", len(anchor_to_entries)
    )

    scanned = 0
    candidate_alias_checks = 0
    with rxnconso_path.open("r", encoding="utf-8", newline="") as handle:
        for raw_line in handle:
            scanned += 1
            if scanned % LOG_SCAN_EVERY == 0:
                logger.info(
                    "Scanned %d RXNCONSO rows (candidate alias checks=%d)",
                    scanned,
                    candidate_alias_checks,
                )

            line_lower = raw_line.lower()
            line_tokens = set(tokenize(line_lower))
            if not line_tokens:
                continue

            candidate_entries: List[AliasEntry] = []
            seen_candidates: Set[Tuple[int, str]] = set()
            for token in line_tokens:
                for entry in anchor_to_entries.get(token, []):
                    key = (entry.row_idx, entry.alias_norm)
                    if key in seen_candidates:
                        continue
                    seen_candidates.add(key)
                    candidate_entries.append(entry)

            if not candidate_entries:
                continue

            matched_entries: List[AliasEntry] = []
            for entry in candidate_entries:
                candidate_alias_checks += 1
                if entry.alias_norm in line_lower:
                    matched_entries.append(entry)

            if not matched_entries:
                continue

            parsed = parse_rxnconso_line(raw_line)
            for entry in matched_entries:
                yield entry.row_idx, entry.alias_pos, entry.alias_original, entry.alias_norm, parsed



def build_output_row(
    input_row: Dict[str, object],
    alias_pos: int,
    alias_original: str,
    alias_norm: str,
    match: RxnConsoMatch,
) -> Dict[str, object]:
    return {
        "nct_id": input_row.get("nct_id", ""),
        "intervention_index": input_row.get("intervention_index", ""),
        "intervention_type": input_row.get("intervention_type", ""),
        "intervention_name": input_row.get("intervention_name", ""),
        "intervention_description": input_row.get("intervention_description", ""),
        "intervention_otherNames": input_row.get("intervention_otherNames", ""),
        "intervention_armGroupLabels": input_row.get("intervention_armGroupLabels", ""),
        "intervention_all_aliases": input_row.get("intervention_all_aliases", ""),
        "matched_term_in_RxNorm": alias_original,
        "matched_term_in_RxNorm_normalized": alias_norm,
        "matched_term_alias_position": alias_pos,
        "match_method": "grep_substring_case_insensitive_anchor_filtered",
        "rxnconso_raw_row": match.raw_line,
        "rx_rxcui": match.rxcui,
        "rx_lat": match.lat,
        "rx_ts": match.ts,
        "rx_lui": match.lui,
        "rx_stt": match.stt,
        "rx_sui": match.sui,
        "rx_ispref": match.ispref,
        "rx_rxaui": match.rxaui,
        "rx_saui": match.saui,
        "rx_scui": match.scui,
        "rx_sdui": match.sdui,
        "rx_sab": match.sab,
        "rx_tty": match.tty,
        "rx_code": match.code,
        "rx_str": match.str_value,
        "rx_srl": match.srl,
        "rx_suppress": match.suppress,
        "rx_cvf": match.cvf,
    }


def build_no_match_row(input_row: Dict[str, object]) -> Dict[str, object]:
    row = {
        "nct_id": input_row.get("nct_id", ""),
        "intervention_index": input_row.get("intervention_index", ""),
        "intervention_type": input_row.get("intervention_type", ""),
        "intervention_name": input_row.get("intervention_name", ""),
        "intervention_description": input_row.get("intervention_description", ""),
        "intervention_otherNames": input_row.get("intervention_otherNames", ""),
        "intervention_armGroupLabels": input_row.get("intervention_armGroupLabels", ""),
        "intervention_all_aliases": input_row.get("intervention_all_aliases", ""),
        "matched_term_in_RxNorm": "",
        "matched_term_in_RxNorm_normalized": "",
        "matched_term_alias_position": "",
        "match_method": "NO_MATCH",
        "rxnconso_raw_row": "",
    }
    for field in RXNCONSO_OUTPUT_FIELDS:
        row[f"rx_{field}"] = ""
    return row


def tsv_batch_path(base_output_tsv: Path, batch_number: int) -> Path:
    suffix = base_output_tsv.suffix or ".tsv"
    stem = base_output_tsv.name[:-len(suffix)] if suffix and base_output_tsv.name.endswith(suffix) else base_output_tsv.name
    return base_output_tsv.with_name(f"{stem}.batch_{batch_number:04d}{suffix}")


def write_tsv_batch(path: Path, rows: Sequence[Dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_summary_tsv(path: Path, rows: Sequence[Dict[str, object]]) -> None:
    fieldnames = [
        "nct_id",
        "intervention_index",
        "intervention_type",
        "intervention_name",
        "intervention_all_aliases",
        "matched_output_rows",
        "matched_unique_rxcuis",
        "matched_unique_rxauis",
        "matched_aliases",
        "status",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def process_workbook(
    input_excel: Path,
    rxnorm_rrf_dir: Path,
    output_tsv: Path,
    summary_tsv: Optional[Path],
    sheet_name: str,
    tsv_batch_size: int,
) -> None:
    if not input_excel.exists():
        raise FileNotFoundError(f"Input workbook not found: {input_excel}")
    if not rxnorm_rrf_dir.exists():
        raise FileNotFoundError(f"RxNorm RRF directory not found: {rxnorm_rrf_dir}")

    xls = pd.ExcelFile(input_excel)
    if sheet_name not in xls.sheet_names:
        raise KeyError(f"Worksheet {sheet_name!r} not found. Available sheets: {xls.sheet_names}")

    df_input = pd.read_excel(input_excel, sheet_name=sheet_name, dtype="string").fillna("")
    if COL_ALIASES not in df_input.columns:
        raise KeyError(f"Required column missing: {COL_ALIASES}")

    logger.info("Read %d input rows from %s / %s", len(df_input), input_excel, sheet_name)

    records: List[Dict[str, object]] = df_input.to_dict(orient="records")
    anchor_to_entries, _aliases_by_input_row, unique_alias_count = build_alias_registry(df_input)
    logger.info("Built alias registry with %d unique aliases", unique_alias_count)

    matched_counts = [0] * len(records)
    matched_rxcuis = [set() for _ in records]
    matched_rxauis = [set() for _ in records]
    matched_aliases = [set() for _ in records]

    batch_rows: List[Dict[str, object]] = []
    batch_number = 1

    rxnconso_path = rxnorm_rrf_dir / RXNCONSO_FILENAME
    for row_idx, alias_pos, alias_original, alias_norm, parsed in iter_rxnconso_matches(rxnconso_path, anchor_to_entries):
        batch_rows.append(build_output_row(records[row_idx], alias_pos, alias_original, alias_norm, parsed))
        matched_counts[row_idx] += 1
        matched_rxcuis[row_idx].add(parsed.rxcui)
        matched_rxauis[row_idx].add(parsed.rxaui)
        matched_aliases[row_idx].add(alias_original)

        if len(batch_rows) >= tsv_batch_size:
            batch_path = tsv_batch_path(output_tsv, batch_number)
            write_tsv_batch(batch_path, batch_rows)
            logger.info("Wrote %d rows to %s", len(batch_rows), batch_path)
            batch_number += 1
            batch_rows = []

    no_match_rows = 0
    for row_idx, input_row in enumerate(records):
        if matched_counts[row_idx] == 0:
            batch_rows.append(build_no_match_row(input_row))
            no_match_rows += 1
            if len(batch_rows) >= tsv_batch_size:
                batch_path = tsv_batch_path(output_tsv, batch_number)
                write_tsv_batch(batch_path, batch_rows)
                logger.info("Wrote %d rows to %s", len(batch_rows), batch_path)
                batch_number += 1
                batch_rows = []

    if batch_rows:
        batch_path = tsv_batch_path(output_tsv, batch_number)
        write_tsv_batch(batch_path, batch_rows)
        logger.info("Wrote %d rows to %s", len(batch_rows), batch_path)

    logger.info("Found matches for %d input rows", sum(1 for count in matched_counts if count > 0))
    logger.info("Emitted explicit NO_MATCH rows for %d input rows", no_match_rows)

    if summary_tsv is not None:
        summary_rows: List[Dict[str, object]] = []
        for row_idx, input_row in enumerate(records):
            summary_rows.append(
                {
                    "nct_id": input_row.get("nct_id", ""),
                    "intervention_index": input_row.get("intervention_index", ""),
                    "intervention_type": input_row.get("intervention_type", ""),
                    "intervention_name": input_row.get("intervention_name", ""),
                    "intervention_all_aliases": input_row.get("intervention_all_aliases", ""),
                    "matched_output_rows": matched_counts[row_idx],
                    "matched_unique_rxcuis": len(matched_rxcuis[row_idx]),
                    "matched_unique_rxauis": len(matched_rxauis[row_idx]),
                    "matched_aliases": AGG_DELIMITER.join(sorted(matched_aliases[row_idx])) if matched_aliases[row_idx] else "",
                    "status": "MATCHED" if matched_counts[row_idx] > 0 else "NO_MATCH",
                }
            )
        write_summary_tsv(summary_tsv, summary_rows)
        logger.info("Wrote summary TSV to %s", summary_tsv)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Stage-1 CTGov intervention alias retrieval against RXNCONSO with batched TSV output"
    )
    parser.add_argument("--input_excel", required=True, type=Path, help="Path to CTGov intervention workbook")
    parser.add_argument("--rxnorm_rrf_dir", required=True, type=Path, help="Directory containing RXNCONSO.RRF")
    parser.add_argument("--output_tsv", required=True, type=Path, help="Base path for batched TSV output files")
    parser.add_argument("--summary_tsv", type=Path, default=None, help="Optional summary TSV path (one row per input intervention)")
    parser.add_argument("--sheet_name", default=SHEET_INPUT, help=f"Worksheet name to read. Defaults to {SHEET_INPUT!r}")
    parser.add_argument(
        "--tsv_batch_size",
        type=int,
        default=TSV_BATCH_SIZE_DEFAULT,
        help=f"Maximum output rows per TSV batch file. Default: {TSV_BATCH_SIZE_DEFAULT}",
    )
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
        rxnorm_rrf_dir=args.rxnorm_rrf_dir,
        output_tsv=args.output_tsv,
        summary_tsv=args.summary_tsv,
        sheet_name=args.sheet_name,
        tsv_batch_size=args.tsv_batch_size,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
