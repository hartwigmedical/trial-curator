from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict

import pandas as pd

from aus_trial_universe.ctgov.utils.general.csv_mapping_file import (
    load_resource_csv,
)
from aus_trial_universe.ctgov.utils.general.text_normalisation import (
    norm_cell,
    clean_cell_str,
    is_effectively_empty,
)

logger = logging.getLogger(__name__)

MolecularSignatureKey = str
MolecularSignatureMap = Dict[MolecularSignatureKey, str]


def load_mapping_resource(mapping_path: Path) -> pd.DataFrame:
    suffix = mapping_path.suffix.lower()

    if suffix == ".csv":
        return load_resource_csv(mapping_path)

    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(mapping_path)

    raise ValueError(f"Unsupported mapping resource format: {mapping_path}")


def make_molecular_signature_key(signature: str) -> MolecularSignatureKey:
    return signature


def build_molecular_signature_map(mapping_path: Path) -> MolecularSignatureMap:
    df = load_mapping_resource(mapping_path)

    required = [
        "Signature_lookup",
        "Findings_curation",
    ]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            f"Molecular signature mapping resource missing required columns: {missing}"
        )

    out: MolecularSignatureMap = {}

    for _, row in df.iterrows():
        val = clean_cell_str(row.get("Findings_curation"))
        if is_effectively_empty(val):
            continue

        key = make_molecular_signature_key(
            norm_cell(row.get("Signature_lookup"))
        )

        if not key:
            continue

        if key in out and out[key] != val:
            logger.warning(
                "Duplicate MolecularSignature key %r with differing values; last-one-wins",
                key,
            )

        out[key] = val

    return out