from __future__ import annotations

import argparse
import ast
import csv
import logging
from pathlib import Path
from typing import Dict, List

import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------
# Helpers
# ---------------------------

def _normalize(s: str) -> str:
    if s is None:
        return ""
    return str(s).strip()


def _normalize_key(s: str) -> str:
    return _normalize(s).lower()


def _parse_conditions_list(raw: str) -> List[str]:
    """
    Expect conditions column to be a Python list string.
    Example: '["A", "B"]'
    """
    if not raw:
        return []

    try:
        parsed = ast.literal_eval(raw)
    except Exception:
        return []

    if isinstance(parsed, list):
        return [_normalize(x) for x in parsed if _normalize(x)]

    return []


def _find_mapping_file(mapping_dir: Path) -> Path:
    for p in mapping_dir.iterdir():
        if "conditions" in p.name.lower() and p.suffix.lower() in {".xlsx", ".xls"}:
            return p
    raise FileNotFoundError("Could not find Conditions mapping file in mapping_dir")


# ---------------------------
# Mapping loader
# ---------------------------

def load_mapping(mapping_file: Path) -> Dict[str, str]:
    df = pd.read_excel(mapping_file, dtype=str).fillna("")

    df.columns = [str(c).strip() for c in df.columns]

    lookup_col = None
    value_col = None

    for c in df.columns:
        if c.lower() == "conditions_lookup":
            lookup_col = c
        if c.lower() == "oncotree_curation":
            value_col = c

    if not lookup_col or not value_col:
        raise ValueError("Mapping file must contain 'Conditions_lookup' and 'Oncotree_curation'")

    mapping = {}
    mapping_ci = {}

    for _, row in df.iterrows():
        key = _normalize(row[lookup_col])
        val = _normalize(row[value_col])

        if not key:
            continue

        mapping[key] = val
        mapping_ci[_normalize_key(key)] = val

    return mapping, mapping_ci


# ---------------------------
# Core logic
# ---------------------------

def process(
    ctgov_csv: Path,
    mapping_dir: Path,
    output_csv: Path,
) -> None:
    mapping_file = _find_mapping_file(mapping_dir)
    logger.info(f"Using mapping file: {mapping_file}")

    mapping, mapping_ci = load_mapping(mapping_file)

    df = pd.read_csv(ctgov_csv, dtype=str, keep_default_na=False)

    # find columns
    nct_col = None
    cond_col = None

    for c in df.columns:
        if c.lower() == "nctid":
            nct_col = c
        if c.lower() == "conditions":
            cond_col = c

    if not nct_col or not cond_col:
        raise ValueError("CSV must contain nctId and conditions columns")

    rows = []

    for _, row in df.iterrows():
        nct_id = _normalize(row[nct_col]).upper()
        raw_conditions = row[cond_col]  # ← PRESERVE EXACTLY

        terms = _parse_conditions_list(raw_conditions)

        mapped_terms = []
        for term in terms:
            mapped = mapping.get(term) or mapping_ci.get(_normalize_key(term)) or ""
            mapped = _normalize(mapped)

            if mapped:
                mapped_terms.append(mapped)

        curated = " | ".join(dict.fromkeys(mapped_terms))

        rows.append({
            "trial_id": nct_id,
            "conditions_original": raw_conditions,  # ← unchanged
            "conditions_oncotree_curation": curated,
        })

    output_csv.parent.mkdir(parents=True, exist_ok=True)

    with output_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "trial_id",
                "conditions_original",
                "conditions_oncotree_curation",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    logger.info(f"Wrote {len(rows)} rows to {output_csv}")


# ---------------------------
# CLI
# ---------------------------

def main():
    parser = argparse.ArgumentParser(description="Simple conditions → OncoTree mapping")

    parser.add_argument("--ctgov_extractions_csv", required=True, type=Path)
    parser.add_argument("--mapping_dir", required=True, type=Path)
    parser.add_argument("--output_csv", required=True, type=Path)
    parser.add_argument("--log_level", default="INFO")

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    process(
        ctgov_csv=args.ctgov_extractions_csv,
        mapping_dir=args.mapping_dir,
        output_csv=args.output_csv,
    )


if __name__ == "__main__":
    main()