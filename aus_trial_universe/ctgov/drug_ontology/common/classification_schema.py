"""Shared constants/helpers for the CTGov drug ontology pipeline.

Scope
-----
This module should only contain stable, cross-stage constants used by multiple
pipeline components.

It should NOT define ATC/FDA/POTTR/ChEMBL output schemas. Those belong in:
  - SQL schema files
  - source-specific classification modules
  - SQL-derived export views/dumps

Canonical rule
--------------
Check/review TSVs must be SQL-derived dumps/views, not Python-side parallel
representations of what was loaded.
"""

from __future__ import annotations

from typing import Iterable, List


# =============================================================================
# Shared delimiters
# =============================================================================

DISPLAY_DELIMITER = " | "
TERM_DELIMITER = DISPLAY_DELIMITER


# =============================================================================
# Shared CTGov intervention type filters
# =============================================================================

TARGET_DRUG_INTERVENTION_TYPES = {
    "DRUG",
    "BIOLOGICAL",
    "OTHER",
    "COMBINATION_PRODUCT",
}


# =============================================================================
# Core CTGov intervention columns
# =============================================================================

COL_NCT_ID = "nct_id"
COL_INTERVENTION_INDEX = "intervention_index"
COL_INTERVENTION_TYPE = "intervention_type"
COL_INTERVENTION_NAME = "intervention_name"
COL_INTERVENTION_DESCRIPTION = "intervention_description"
COL_INTERVENTION_OTHER_NAMES = "intervention_otherNames"
COL_INTERVENTION_ARM_GROUP_LABELS = "intervention_armGroupLabels"
COL_INTERVENTION_ALL_ALIASES = "intervention_all_aliases"
COL_INTERVENTION_ALL_ALIASES_NORMALISED = "intervention_all_aliases_normalised"
COL_INTERVENTION_ALL_ALIASES_NORMALISED_FULL = "intervention_all_aliases_normalised_full"

CORE_INTERVENTION_COLUMNS = [
    COL_NCT_ID,
    COL_INTERVENTION_INDEX,
    COL_INTERVENTION_TYPE,
    COL_INTERVENTION_NAME,
    COL_INTERVENTION_DESCRIPTION,
    COL_INTERVENTION_OTHER_NAMES,
    COL_INTERVENTION_ARM_GROUP_LABELS,
    COL_INTERVENTION_ALL_ALIASES,
    COL_INTERVENTION_ALL_ALIASES_NORMALISED,
    COL_INTERVENTION_ALL_ALIASES_NORMALISED_FULL,
]

REQUIRED_STABLE_INTERVENTION_COLUMNS = [
    COL_NCT_ID,
    COL_INTERVENTION_INDEX,
    COL_INTERVENTION_TYPE,
    COL_INTERVENTION_NAME,
    COL_INTERVENTION_DESCRIPTION,
]


# =============================================================================
# Lightweight display helpers
# =============================================================================

def clean_text(value: object) -> str:
    """Return a stripped string, treating None/NaN-like objects as blank."""
    if value is None:
        return ""

    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "<na>"}:
        return ""

    return text


def ordered_unique(values: Iterable[object]) -> List[str]:
    """Return stable unique cleaned strings."""
    out: List[str] = []
    seen: set[str] = set()

    for value in values:
        clean = clean_text(value)
        if not clean:
            continue

        if clean in seen:
            continue

        seen.add(clean)
        out.append(clean)

    return out


def join_display_values(values: Iterable[object]) -> str:
    """Join flat display values using the project-wide delimiter."""
    return DISPLAY_DELIMITER.join(ordered_unique(values))


def split_display_values(value: object) -> List[str]:
    """Split a flat display column created with join_display_values."""
    text = clean_text(value)
    if not text:
        return []

    return ordered_unique(part.strip() for part in text.split(DISPLAY_DELIMITER))