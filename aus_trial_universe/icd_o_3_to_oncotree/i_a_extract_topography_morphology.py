from __future__ import annotations

import argparse
import logging
import re
from pathlib import Path
from typing import List, Optional

import pandas as pd

logger = logging.getLogger(__name__)


def clean_str(x) -> str:
    if x is None or pd.isna(x):
        return ""
    return str(x).strip()


def normalize_text(s: str) -> str:
    s = clean_str(s).lower()
    s = s.replace("&", " and ")
    s = s.replace("/", " ")
    s = s.replace("-", " ")
    s = re.sub(r"[()]", " ", s)
    s = re.sub(r"\bnos\b", "", s)
    s = re.sub(r"\bnec\b", "", s)
    s = re.sub(r"\s*,\s*", ", ", s)
    s = s.replace(",", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def load_reference_terms(workbook_xlsx: Path):
    topog_df = pd.read_excel(workbook_xlsx, sheet_name="Unique Topog")
    morph_df = pd.read_excel(workbook_xlsx, sheet_name="Unique Morph")

    topogs = [clean_str(x) for x in topog_df.iloc[:, 0] if clean_str(x)]
    morphs = [clean_str(x) for x in morph_df.iloc[:, 0] if clean_str(x)]

    return topogs, morphs


def split_raw_terms(label: str, topogs: List[str], morphs: List[str]):
    raw = clean_str(label)
    if not raw:
        return "", ""

    morph_lookup = {normalize_text(x): x for x in morphs}
    topog_lookup = {normalize_text(x): x for x in topogs}

    raw_norm = normalize_text(raw)

    morph_candidates = sorted(morph_lookup.keys(), key=len, reverse=True)

    matched_morph_norm = None
    for morph_norm in morph_candidates:
        if raw_norm == morph_norm or raw_norm.endswith(" " + morph_norm):
            matched_morph_norm = morph_norm
            break

    if matched_morph_norm is None:
        return raw, ""

    morphology_raw = morph_lookup[matched_morph_norm]

    if raw_norm == matched_morph_norm:
        topography_raw = ""
    else:
        prefix_norm = raw_norm[: -len(matched_morph_norm)].strip()
        topography_raw = topog_lookup.get(prefix_norm, raw[: -len(morphology_raw)].rstrip(" ,"))

    return topography_raw, morphology_raw


def process_csv(input_csv: Path, workbook_xlsx: Path, output_csv: Path) -> None:
    df = pd.read_csv(input_csv)
    topogs, morphs = load_reference_terms(workbook_xlsx)

    parsed = df["Omico_cancertypes"].apply(
        lambda x: pd.Series(split_raw_terms(x, topogs, morphs))
    )
    parsed.columns = ["topography_raw", "morphology_raw"]

    out = pd.concat([df, parsed], axis=1)
    out["topography_norm"] = out["topography_raw"].map(normalize_text)
    out["morphology_norm"] = out["morphology_raw"].map(normalize_text)

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output_csv, index=False)

    logger.info("Wrote %d rows to %s", len(out), output_csv)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input_csv", required=True, type=Path)
    parser.add_argument("--workbook_xlsx", required=True, type=Path)
    parser.add_argument("--output_csv", required=True, type=Path)
    parser.add_argument("--log_level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    process_csv(
        input_csv=args.input_csv,
        workbook_xlsx=args.workbook_xlsx,
        output_csv=args.output_csv,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())