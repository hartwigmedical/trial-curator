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


def simplify_topography(s: str) -> str:
    s = clean_str(s).lower()
    if not s:
        return ""

    exact_map = {
        "ascending colon": "colon",
        "descending colon": "colon",
        "sigmoid colon": "colon",
        "transverse colon": "colon",
        "splenic flexure of colon": "colon",
        "hepatic flexure of colon": "colon",
        "cecum": "cecum",
        "appendix": "appendix",
        "rectum": "rectum",
        "head of pancreas": "pancreas",
        "body of pancreas": "pancreas",
        "tail of pancreas": "pancreas",
        "neck of pancreas": "pancreas",
        "fundus of stomach": "stomach",
        "body of stomach": "stomach",
        "pylorus": "stomach",
        "antrum": "stomach",
        "gastric antrum": "stomach",
        "bones of skull and face and associated joints": "bone",
        "bone marrow": "bone marrow",
        "long bones of lower limb and associated joints": "bone",
        "long bones of upper limb scapula and associated joints": "bone",
        "short bones of lower limb and associated joints": "bone",
        "short bones of upper limb and associated joints": "bone",
        "pelvic bones sacrum coccyx and associated joints": "bone",
        "vertebral column": "bone",
        "ribs sternum clavicle and associated joints": "bone",
        "bones of skull face and associated joints": "bone",
        "connective subcutaneous and other soft tissues of abdomen": "soft tissue",
        "connective subcutaneous and other soft tissues of head face and neck": "soft tissue",
        "connective subcutaneous and other soft tissues of lower limb and hip": "soft tissue",
        "connective subcutaneous and other soft tissues of pelvis": "soft tissue",
        "connective subcutaneous and other soft tissues of thorax": "soft tissue",
        "connective subcutaneous and other soft tissues of trunk": "soft tissue",
        "connective subcutaneous and other soft tissues of upper limb and shoulder": "soft tissue",
        "connective subcutaneous and other soft tissues": "soft tissue",
        "cortex of adrenal gland": "adrenal gland",
        "lower lobe lung": "lung",
        "lower third of esophagus": "esophagus",
        "lower inner quadrant of breast": "breast",
        "lower outer quadrant of breast": "breast",
        "main bronchus": "bronchus",
        "major salivary gland": "salivary gland",
        "male genital organs": "male genital",
        "other specified parts of male genital organs": "male genital",
        "peripheral nerves and autonomic nervous system of pelvis": "peripheral nerve",
        "prostate gland": "prostate",
        "overlapping lesion of rectum anus and anal canal": "overlapping rectum anus anal canal",
    }
    if s in exact_map:
        return exact_map[s]

    if s.endswith(" colon") or " flexure of colon" in s:
        return "colon"
    if s.endswith(" of pancreas"):
        return "pancreas"
    if s.endswith(" of stomach") or s.startswith("gastric "):
        return "stomach"
    if "bone" in s or "bones of" in s or "bones " in s:
        return "bone"

    return s


def simplify_morphology(s: str) -> str:
    s = clean_str(s).lower()
    if not s:
        return ""

    s = s.replace("tumour", "tumor")
    s = re.sub(r"\bgrade\s*[1234ivx]+\b", "", s)
    s = re.sub(r"\bwell differentiated\b", "", s)
    s = re.sub(r"\bmoderately differentiated\b", "", s)
    s = re.sub(r"\bpoorly differentiated\b", "", s)
    s = re.sub(r"\bmalignant\b", "", s)
    s = re.sub(r"\bbenign\b", "", s)
    s = re.sub(r"\bmetastatic\b", "", s)
    s = re.sub(r"\bmetastasizing\b", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\bminimally invasive\b", "", s)

    exact_map = {
        "neuroendocrine tumor": "neuroendocrine",
        "atypical meningioma": "meningioma",
        "anaplastic meningioma": "meningioma",
    }
    if s in exact_map:
        return exact_map[s]

    if s.startswith("neuroendocrine tumor"):
        return "neuroendocrine"

    s = re.sub(r"\s+", " ", s).strip()
    return s


def process_csv(input_csv: Path, output_csv: Path) -> None:
    df = pd.read_csv(input_csv)

    required_cols = {"topography_norm", "morphology_norm"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"Input CSV missing required columns: {sorted(missing)}")

    out = df.copy()
    out["topography_simplified"] = out["topography_norm"].map(simplify_topography)
    out["morphology_simplified"] = out["morphology_norm"].map(simplify_morphology)

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output_csv, index=False)

    logger.info("Wrote %d rows to %s", len(out), output_csv)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input_csv", required=True, type=Path)
    parser.add_argument("--output_csv", required=True, type=Path)
    parser.add_argument("--log_level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    process_csv(
        input_csv=args.input_csv,
        output_csv=args.output_csv,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
