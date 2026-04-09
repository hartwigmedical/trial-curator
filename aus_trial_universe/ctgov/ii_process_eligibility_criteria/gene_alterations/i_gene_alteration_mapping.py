from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Tuple

import pandas as pd

from aus_trial_universe.ctgov.utils.general.csv_mapping_file import (
    load_resource_csv,
)
from aus_trial_universe.ctgov.utils.general.text_normalisation import (
    norm_cell,
    clean_cell_str,
)

logger = logging.getLogger(__name__)

GeneAlterationKey = Tuple[str, str, str]
GeneAlterationMap = Dict[GeneAlterationKey, str]


def load_mapping_resource(mapping_path: Path) -> pd.DataFrame:
    suffix = mapping_path.suffix.lower()

    if suffix == ".csv":
        return load_resource_csv(mapping_path)

    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(mapping_path)

    raise ValueError(f"Unsupported mapping resource format: {mapping_path}")


def make_gene_alteration_key(
    gene: str,
    alteration: str,
    variant: str,
) -> GeneAlterationKey:
    return (gene, alteration, variant)


def build_gene_alteration_map(mapping_path: Path) -> GeneAlterationMap:
    df = load_mapping_resource(mapping_path)

    required = [
        "Gene_lookup",
        "Alteration_lookup",
        "Variant_lookup",
        "Mapping_args",
    ]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            f"Gene alteration mapping resource missing required columns: {missing}"
        )

    out: GeneAlterationMap = {}

    for _, row in df.iterrows():
        key = make_gene_alteration_key(
            norm_cell(row.get("Gene_lookup")),
            norm_cell(row.get("Alteration_lookup")),
            norm_cell(row.get("Variant_lookup")),
        )

        val = clean_cell_str(row.get("Mapping_args"))

        if key in out and out[key] != val:
            logger.warning(
                "Duplicate GeneAlteration key %r with differing values; last-one-wins",
                key,
            )

        out[key] = val

    return out
