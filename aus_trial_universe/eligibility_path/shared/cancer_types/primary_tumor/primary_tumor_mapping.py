from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Tuple

import pandas as pd

from aus_trial_universe.eligibility_path.shared.utils.csv_mapping_file import (
    load_resource_csv,
)
from aus_trial_universe.eligibility_path.shared.utils.text_normalisation import (
    clean_cell_str,
    norm_cell,
)

logger = logging.getLogger(__name__)

PrimaryTumorKey = Tuple[str, str]
PrimaryTumorMap = Dict[PrimaryTumorKey, str]


def load_mapping_resource(mapping_path: Path) -> pd.DataFrame:
    suffix = mapping_path.suffix.lower()

    if suffix == ".csv":
        return load_resource_csv(mapping_path)

    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(mapping_path)

    raise ValueError(f"Unsupported mapping resource format: {mapping_path}")


def make_primary_tumor_key(
    primary_tumor_type: str,
    primary_tumor_location: str,
) -> PrimaryTumorKey:
    return (primary_tumor_type, primary_tumor_location)


def build_primary_tumor_map(mapping_path: Path) -> PrimaryTumorMap:
    df = load_mapping_resource(mapping_path)

    required = [
        "PrimaryTumorType_lookup",
        "PrimaryTumorLocation_lookup",
        "Oncotree_curation",
    ]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            f"Primary tumour mapping resource missing required columns: {missing}"
        )

    out: PrimaryTumorMap = {}

    for _, row in df.iterrows():
        tumor_type = norm_cell(row.get("PrimaryTumorType_lookup"))
        tumor_location = norm_cell(row.get("PrimaryTumorLocation_lookup"))

        if tumor_type == "" and tumor_location == "":
            continue

        key = make_primary_tumor_key(tumor_type, tumor_location)
        val = clean_cell_str(row.get("Oncotree_curation"))

        if key in out and out[key] != val:
            logger.warning(
                "Duplicate primary tumour mapping key %r with differing values; last-one-wins",
                key,
            )

        out[key] = val

    return out
