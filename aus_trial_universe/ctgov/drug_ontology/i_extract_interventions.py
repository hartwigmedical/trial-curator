from __future__ import annotations

import argparse
import ast
import logging
import os
import re
from pathlib import Path
from typing import Dict, List

import pandas as pd


logger = logging.getLogger(__name__)

SHEET_INPUT = "general"
SHEET_PROCESSED = "processed interventions"

COL_INPUT = "interventionName"
COL_PROCESSED = "unique_interventions_processed"

MAPPING_COL_UNIQUE = "unique_interventions"
MAPPING_COL_PROCESS_TO = "Process_to"

REMOVE_SENTINEL = "REMOVE"


# ---------- generic helpers ----------

def is_missing(value: object) -> bool:
    return value is None or pd.isna(value)


def simple_strip(text: object) -> str:
    return str(text).strip()


def parse_intervention_cell(value: object) -> List[str]:
    """
    Parse interventionName values.

    IMPORTANT:
    Do NOT split on commas here.
    Comma splitting applies only to Process_to in the mapping file.
    """
    if is_missing(value):
        return []

    if isinstance(value, (list, tuple)):
        return [simple_strip(x) for x in value if simple_strip(x)]

    text = simple_strip(value)
    if not text:
        return []

    try:
        parsed = ast.literal_eval(text)
        if isinstance(parsed, (list, tuple)):
            return [simple_strip(x) for x in parsed if simple_strip(x)]
        if isinstance(parsed, str):
            parsed_text = simple_strip(parsed)
            return [parsed_text] if parsed_text else []
    except Exception:
        pass

    return [text]


# ---------- first-pass canonicalization for unique-item dedup ----------

def preserve_token_in_first_pass(token: str) -> bool:
    return bool(token) and bool(re.fullmatch(r"[A-Z0-9-]+", token)) and any(ch.isalnum() for ch in token)


def canonicalize_for_first_dedup(text: str) -> str:
    parts = re.split(r"(\s+)", simple_strip(text))
    canonical_parts: List[str] = []
    for part in parts:
        if not part:
            continue
        if part.isspace():
            canonical_parts.append(part)
        elif preserve_token_in_first_pass(part):
            canonical_parts.append(part)
        else:
            canonical_parts.append(part.lower())
    return "".join(canonical_parts).strip()


def extract_raw_interventions(df_general: pd.DataFrame, input_col: str) -> List[str]:
    if input_col not in df_general.columns:
        raise KeyError(f"Column '{input_col}' not found in worksheet '{SHEET_INPUT}'")

    raw_items: List[str] = []
    for value in df_general[input_col].tolist():
        raw_items.extend(item for item in parse_intervention_cell(value) if item)
    return raw_items


def build_unique_interventions(raw_items: List[str]) -> List[str]:
    unique_items: List[str] = []
    seen_canonical = set()

    for raw_item in raw_items:
        stripped = simple_strip(raw_item)
        if not stripped:
            continue
        canonical = canonicalize_for_first_dedup(stripped)
        if not canonical:
            continue
        if canonical in seen_canonical:
            continue
        seen_canonical.add(canonical)
        unique_items.append(stripped)

    return unique_items


# ---------- mapping loading ----------

def load_intervention_mapping(mapping_csv_path: str | Path) -> Dict[str, str]:
    mapping_csv_path = Path(mapping_csv_path)
    if not mapping_csv_path.exists():
        raise FileNotFoundError(f"Mapping CSV not found: {mapping_csv_path}")

    df = pd.read_csv(mapping_csv_path, dtype="string")
    required = {MAPPING_COL_UNIQUE, MAPPING_COL_PROCESS_TO}
    missing = required - set(df.columns)
    if missing:
        raise KeyError(f"Mapping CSV missing required columns: {sorted(missing)}")

    mapping: Dict[str, str] = {}
    for _, row in df.iterrows():
        src_raw = row[MAPPING_COL_UNIQUE]
        if is_missing(src_raw):
            continue

        src_key = simple_strip(src_raw)
        if not src_key:
            continue

        dst_raw = row[MAPPING_COL_PROCESS_TO]
        dst_value = "" if is_missing(dst_raw) else str(dst_raw)

        if src_key in mapping and mapping[src_key] != dst_value:
            logger.warning(
                "Duplicate mapping for %r. Overwriting previous Process_to %r with %r",
                src_key,
                mapping[src_key],
                dst_value,
            )
        mapping[src_key] = dst_value

    return mapping


# ---------- mapping expansion ----------

def split_process_to_terms(process_to_value: str) -> List[str]:
    """
    Comma splitting applies ONLY to Process_to.
    """
    return [simple_strip(part) for part in str(process_to_value).split(",") if simple_strip(part)]


def map_unique_intervention(unique_term: str, mapping: Dict[str, str]) -> List[str]:
    lookup_key = simple_strip(unique_term)
    process_to_value = mapping.get(lookup_key)

    # REMOVE sentinel
    if process_to_value is not None and simple_strip(process_to_value) == REMOVE_SENTINEL:
        return []

    # If Process_to is blank → use first column terms but comma-split
    if process_to_value is None or simple_strip(process_to_value) == "":
        return [simple_strip(x) for x in lookup_key.split(",") if simple_strip(x)]

    # Otherwise use Process_to (comma split)
    return split_process_to_terms(process_to_value)


# ---------- final normalization ----------

def split_top_level_pipes(text: str) -> List[str]:
    return [simple_strip(part) for part in re.split(r"\s*\|\s*", text) if simple_strip(part)]


def should_uppercase_short_alpha(piece: str) -> bool:
    return piece.isalpha() and len(piece) < 4


def normalize_hyphenated_token(token: str) -> str:
    pieces = token.split("-")
    has_digit_piece = any(any(ch.isdigit() for ch in piece) for piece in pieces)

    normalized_pieces: List[str] = []
    for i, piece in enumerate(pieces):
        if piece == "":
            normalized_pieces.append(piece)
            continue

        if any(ch.isdigit() for ch in piece):
            if any(ch.isalpha() for ch in piece):
                normalized_pieces.append(re.sub(r"[A-Za-z]+", lambda m: m.group(0).upper(), piece))
            else:
                normalized_pieces.append(piece)
            continue

        if should_uppercase_short_alpha(piece):
            normalized_pieces.append(piece.upper())
            continue

        # Uppercase acronym-like alphabetic segments only when adjacent to digit-bearing
        # segments and the segment itself is short. Do not uppercase leading "anti".
        prev_has_digit = i > 0 and any(ch.isdigit() for ch in pieces[i - 1])
        next_has_digit = i < len(pieces) - 1 and any(ch.isdigit() for ch in pieces[i + 1])

        if (
            has_digit_piece
            and piece.isalpha()
            and len(piece) <= 4
            and (prev_has_digit or next_has_digit)
        ):
            normalized_pieces.append(piece.upper())
            continue

        if piece.isalpha():
            normalized_pieces.append(piece[:1].upper() + piece[1:].lower())
            continue

        normalized_pieces.append(piece)

    return "-".join(normalized_pieces)


_BRACKETED_PREFIX_RE = re.compile(r"^(\[[^\]]+\]-)(.*)$")


def normalize_token_case(token: str) -> str:
    token = simple_strip(token)
    if not token:
        return token

    bracket_match = _BRACKETED_PREFIX_RE.match(token)
    if bracket_match:
        prefix, remainder = bracket_match.groups()
        if not remainder:
            return prefix
        return prefix + normalize_token_case(remainder)

    if "-" in token:
        return normalize_hyphenated_token(token)

    if any(ch.isdigit() for ch in token):
        if any(ch.isalpha() for ch in token):
            return re.sub(r"[A-Za-z]+", lambda m: m.group(0).upper(), token)
        return token

    if re.fullmatch(r"[A-Za-z]+", token):
        if should_uppercase_short_alpha(token):
            return token.upper()
        return token[:1].upper() + token[1:].lower()

    return token


_TOKEN_RE = re.compile(r"\S+")


def normalize_phrase_case(text: str) -> str:
    text = simple_strip(text)
    if not text:
        return text

    return _TOKEN_RE.sub(lambda m: normalize_token_case(m.group(0)), text)


def normalize_processed_row(text: str) -> str:
    """
    Normalize each pipe-delimited equivalent on the same row.
    Dedup equivalent aliases within the row using exact string equality.
    """
    parts = split_top_level_pipes(text)
    normalized_parts: List[str] = []
    seen_parts = set()

    for part in parts:
        cleaned = normalize_phrase_case(part)
        cleaned = simple_strip(cleaned)
        if not cleaned:
            continue
        if cleaned in seen_parts:
            continue
        seen_parts.add(cleaned)
        normalized_parts.append(cleaned)

    return " | ".join(normalized_parts)


# ---------- final dedup + subsumption ----------

def dedup_processed_rows(rows: List[str]) -> List[str]:
    """
    Final dedup is exact-string only.
    Then apply subsumption logic:
    - remove singleton rows subsumed by a piped row
    - remove piped rows subsumed by a larger piped row
    """
    deduped: List[str] = []
    seen_rows = set()

    for row in rows:
        normalized_row = normalize_processed_row(row)
        if not normalized_row:
            continue
        if normalized_row in seen_rows:
            continue
        seen_rows.add(normalized_row)
        deduped.append(normalized_row)

    row_to_alias_set = {row: set(split_top_level_pipes(row)) for row in deduped}

    final_rows: List[str] = []
    seen_final = set()

    for row in deduped:
        current_set = row_to_alias_set[row]
        is_subsumed = False

        for other_row, other_set in row_to_alias_set.items():
            if other_row == row:
                continue
            if current_set < other_set:
                is_subsumed = True
                break

        if is_subsumed:
            continue

        if row in seen_final:
            continue
        seen_final.add(row)
        final_rows.append(row)

    return final_rows


def build_processed_interventions_sheet(
    unique_items: List[str],
    mapping_csv_path: str | Path,
) -> pd.DataFrame:
    mapping = load_intervention_mapping(mapping_csv_path)

    processed_rows: List[str] = []
    for unique_term in unique_items:
        mapped_rows = map_unique_intervention(unique_term, mapping)
        processed_rows.extend(mapped_rows)

    final_rows = dedup_processed_rows(processed_rows)
    return pd.DataFrame({COL_PROCESSED: final_rows})


# ---------- workbook orchestration ----------

def process_workbook(
    input_path: str | Path,
    output_path: str | Path,
    intervention_mapping_csv_path: str | Path,
) -> None:
    input_path = Path(input_path)
    output_path = Path(output_path)

    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    xls = pd.ExcelFile(input_path)
    if SHEET_INPUT not in xls.sheet_names:
        raise KeyError(f"Worksheet '{SHEET_INPUT}' not found. Available sheets: {xls.sheet_names}")

    df_general = pd.read_excel(input_path, sheet_name=SHEET_INPUT)
    raw_items = extract_raw_interventions(df_general, COL_INPUT)
    unique_items = build_unique_interventions(raw_items)
    df_processed = build_processed_interventions_sheet(unique_items, intervention_mapping_csv_path)

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        df_processed.to_excel(writer, sheet_name=SHEET_PROCESSED, index=False)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Process CT.gov intervention names and generate cleaned intervention sheet"
    )
    parser.add_argument(
        "--ctgov_filepath",
        help="Filepath to the input ctgov_field_extractions.xlsx workbook",
        required=True,
    )
    parser.add_argument(
        "--intervention_mapping_csv",
        help="Filepath to the intervention mapping CSV used to generate processed interventions",
        required=True,
    )
    parser.add_argument("--output_dir", help="Directory to store the output xlsx workbook", required=True)
    parser.add_argument(
        "--log_level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging level",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    input_path = Path(args.ctgov_filepath)
    if not input_path.exists():
        raise FileNotFoundError(f"Input workbook not found: {input_path}")

    mapping_path = Path(args.intervention_mapping_csv)
    if not mapping_path.exists():
        raise FileNotFoundError(f"Intervention mapping CSV not found: {mapping_path}")

    logger.info("Reading workbook: %s", input_path)
    logger.info("Reading intervention mapping CSV: %s", mapping_path)

    xls = pd.ExcelFile(input_path)
    if SHEET_INPUT not in xls.sheet_names:
        raise KeyError(f"Worksheet '{SHEET_INPUT}' not found. Available sheets: {xls.sheet_names}")

    df_general = pd.read_excel(input_path, sheet_name=SHEET_INPUT)
    raw_items = extract_raw_interventions(df_general, COL_INPUT)
    logger.info("Extracted %d raw intervention items", len(raw_items))

    unique_items = build_unique_interventions(raw_items)
    logger.info("Found %d unique interventions after first-pass dedup", len(unique_items))

    df_processed = build_processed_interventions_sheet(unique_items, mapping_path)
    logger.info("Generated %d unique processed interventions", len(df_processed))

    os.makedirs(args.output_dir, exist_ok=True)
    output_filepath = os.path.join(args.output_dir, "ctgov_interventions_processed.xlsx")

    with pd.ExcelWriter(output_filepath, engine="openpyxl") as writer:
        df_processed.to_excel(writer, sheet_name=SHEET_PROCESSED, index=False)

    logger.info("Wrote processed workbook to %s", output_filepath)


if __name__ == "__main__":
    main()