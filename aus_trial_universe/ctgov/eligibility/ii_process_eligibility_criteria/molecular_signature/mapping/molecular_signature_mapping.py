from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict

import pandas as pd

from aus_trial_universe.eligibility_utils.general import (
    load_resource_csv,
)
from aus_trial_universe.eligibility_utils.general import (
    clean_cell_str,
    is_effectively_empty,
    norm_cell,
)

logger = logging.getLogger(__name__)

MolecularSignatureKey = str
MolecularSignatureMap = Dict[MolecularSignatureKey, str]


def load_mapping_resource(mapping_path: Path) -> pd.DataFrame:
    suffix = mapping_path.suffix.casefold()

    if suffix == ".csv":
        return load_resource_csv(mapping_path)

    if suffix == ".tsv":
        return pd.read_csv(
            mapping_path,
            sep="\t",
            dtype=str,
            keep_default_na=False,
            na_values=[],
        )

    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(
            mapping_path,
            dtype=str,
            keep_default_na=False,
            na_values=[],
        )

    raise ValueError(f"Unsupported mapping resource format: {mapping_path}")


def make_molecular_signature_key(signature: str) -> MolecularSignatureKey:
    return signature


def build_molecular_signature_map(mapping_path: Path) -> MolecularSignatureMap:
    df = load_mapping_resource(mapping_path)
    df.columns = [str(column).strip() for column in df.columns]

    required = [
        "Signature_lookup",
        "Findings_curation",
    ]
    missing = [column for column in required if column not in df.columns]
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