"""Shared schema constants and lightweight helpers for the CT.gov drug ontology pipeline.

Excel-facing columns are intentionally flat and human-readable. Nested/aligned
structure belongs in JSON/debug sheets, not in delimiter-encoded strings.

Use one display delimiter everywhere in summary columns to avoid fragile two-level
parsing such as ``" | "`` vs ``" || "``.
"""

from __future__ import annotations

from typing import Iterable, List

# -----------------------------------------------------------------------------
# Sheets / delimiters
# -----------------------------------------------------------------------------

SHEET_INTERVENTIONS = "interventions"
SHEET_RXNORM_DEBUG = "rxnorm_debug"
SHEET_ATC_DEBUG = "atc_debug"
SHEET_FDA_DEBUG = "fda_debug"
SHEET_POTTR_DEBUG = "pottr_debug"
SHEET_CHEMBL_DEBUG = "chembl_debug"

DISPLAY_DELIMITER = " | "
TERM_DELIMITER = DISPLAY_DELIMITER


# -----------------------------------------------------------------------------
# Shared helpers
# -----------------------------------------------------------------------------

def clean_text(value: object) -> str:
    """Return a stripped string, treating None/NaN-like objects as blank."""
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "<na>"}:
        return ""
    return text


def ordered_unique(values: Iterable[object]) -> List[str]:
    """Stable unique list after simple string cleaning."""
    out: List[str] = []
    seen = set()
    for value in values:
        clean = clean_text(value)
        if not clean or clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
    return out


def join_display_values(values: Iterable[object]) -> str:
    """Join display values with the single project-wide display delimiter."""
    return DISPLAY_DELIMITER.join(ordered_unique(values))


def split_display_values(value: object) -> List[str]:
    """Split a flat display column produced by join_display_values."""
    text = clean_text(value)
    if not text:
        return []
    return ordered_unique(part.strip() for part in text.split(DISPLAY_DELIMITER))


# -----------------------------------------------------------------------------
# Core CT.gov intervention columns
# -----------------------------------------------------------------------------

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


# -----------------------------------------------------------------------------
# RxNorm identity-layer columns
# -----------------------------------------------------------------------------

COL_DRUG_LOOKUP_TERMS = "drug_lookup_terms"
COL_DRUG_ENTITY_COUNT = "drug_entity_count"
COL_DRUG_MATCH_STATUS = "drug_match_status"
COL_DRUG_MANUAL_REVIEW_NEEDED = "drug_manual_review_needed"
COL_RESOLVED_INPUT_TERMS = "resolved_input_terms"
COL_UNRESOLVED_INPUT_TERMS = "unresolved_input_terms"
COL_RESOLVED_DRUG_NAMES = "resolved_drug_names"
COL_RXNORM_RXCUIS = "rxnorm_rxcuis"
COL_RXNORM_TERM_TYPES = "rxnorm_term_types"
COL_RXNORM_CANONICAL_SOURCES = "rxnorm_canonical_sources"
COL_RXNORM_CANONICAL_SOURCE_CODES = "rxnorm_canonical_source_codes"

RXNORM_MAIN_OUTPUT_COLUMNS = CORE_INTERVENTION_COLUMNS + [
    COL_DRUG_LOOKUP_TERMS,
    COL_DRUG_ENTITY_COUNT,
    COL_DRUG_MATCH_STATUS,
    COL_DRUG_MANUAL_REVIEW_NEEDED,
    COL_RESOLVED_INPUT_TERMS,
    COL_UNRESOLVED_INPUT_TERMS,
    COL_RESOLVED_DRUG_NAMES,
    COL_RXNORM_RXCUIS,
    COL_RXNORM_TERM_TYPES,
    COL_RXNORM_CANONICAL_SOURCES,
    COL_RXNORM_CANONICAL_SOURCE_CODES,
]


# -----------------------------------------------------------------------------
# ATC columns
# -----------------------------------------------------------------------------

COL_ATC_LINK_STATUS = "atc_link_status"
COL_ATC_CODES = "atc_codes"
COL_ATC_NAMES = "atc_names"
COL_ATC_LEVELS = "atc_levels"
COL_ATC_CODE_NAME_PAIRS = "atc_code_name_pairs"
COL_ATC_PATHS = "atc_paths"
COL_ATC_L1_CODES = "atc_l1_codes"
COL_ATC_L1_NAMES = "atc_l1_names"
COL_ATC_L2_CODES = "atc_l2_codes"
COL_ATC_L2_NAMES = "atc_l2_names"
COL_ATC_L3_CODES = "atc_l3_codes"
COL_ATC_L3_NAMES = "atc_l3_names"
COL_ATC_L4_CODES = "atc_l4_codes"
COL_ATC_L4_NAMES = "atc_l4_names"
COL_ATC_L5_CODES = "atc_l5_codes"
COL_ATC_L5_NAMES = "atc_l5_names"

ATC_MAIN_OUTPUT_COLUMNS = [
    COL_ATC_LINK_STATUS,
    COL_ATC_CODES,
    COL_ATC_NAMES,
    COL_ATC_LEVELS,
    COL_ATC_CODE_NAME_PAIRS,
    COL_ATC_PATHS,
    COL_ATC_L1_CODES,
    COL_ATC_L1_NAMES,
    COL_ATC_L2_CODES,
    COL_ATC_L2_NAMES,
    COL_ATC_L3_CODES,
    COL_ATC_L3_NAMES,
    COL_ATC_L4_CODES,
    COL_ATC_L4_NAMES,
    COL_ATC_L5_CODES,
    COL_ATC_L5_NAMES,
]


# -----------------------------------------------------------------------------
# FDA columns
# ------------------------------------------------- ----------------------------

COL_FDA_LINK_STATUS = "fda_link_status"
COL_FDA_LOOKUP_TERMS = "fda_lookup_terms"
COL_FDA_MATCHED_TERMS = "fda_matched_terms"
COL_FDA_APPLICATION_NUMBERS = "fda_application_numbers"
COL_FDA_APPLICATION_TYPES = "fda_application_types"
COL_FDA_PRODUCT_NUMBERS = "fda_product_numbers"
COL_FDA_DRUG_NAMES = "fda_drug_names"
COL_FDA_ACTIVE_INGREDIENTS = "fda_active_ingredients"
COL_FDA_FORMS = "fda_forms"
COL_FDA_STRENGTHS = "fda_strengths"
COL_FDA_SPONSOR_NAMES = "fda_sponsor_names"
COL_FDA_MARKETING_STATUSES = "fda_marketing_statuses"
COL_FDA_REFERENCE_DRUGS = "fda_reference_drugs"
COL_FDA_REFERENCE_STANDARDS = "fda_reference_standards"

FDA_MAIN_OUTPUT_COLUMNS = [
    COL_FDA_LINK_STATUS,
    COL_FDA_LOOKUP_TERMS,
    COL_FDA_MATCHED_TERMS,
    COL_FDA_APPLICATION_NUMBERS,
    COL_FDA_APPLICATION_TYPES,
    COL_FDA_PRODUCT_NUMBERS,
    COL_FDA_DRUG_NAMES,
    COL_FDA_ACTIVE_INGREDIENTS,
    COL_FDA_FORMS,
    COL_FDA_STRENGTHS,
    COL_FDA_SPONSOR_NAMES,
    COL_FDA_MARKETING_STATUSES,
    COL_FDA_REFERENCE_DRUGS,
    COL_FDA_REFERENCE_STANDARDS,
]


# -----------------------------------------------------------------------------
# POTTR columns
# -----------------------------------------------------------------------------

COL_POTTR_MATCH_STATUS = "pottr_match_status"
COL_POTTR_MANUAL_REVIEW_NEEDED = "pottr_manual_review_needed"
COL_POTTR_LOOKUP_TERMS = "pottr_lookup_terms"
COL_POTTR_MATCHED_TERMS = "pottr_matched_terms"
COL_POTTR_DRUG_ENTRIES = "pottr_drug_entries"
COL_POTTR_DRUG_CLASSES = "pottr_drug_classes"
COL_POTTR_PRIMARY_CLASS = "pottr_primary_class"
COL_POTTR_CLASS_ANCESTORS = "pottr_class_ancestors"
COL_POTTR_CLASS_DESCENDANTS = "pottr_class_descendants"
COL_POTTR_CLASS_PATHS = "pottr_class_paths"
COL_POTTR_CLASS_LINK_STATUS = "pottr_class_link_status"

POTTR_MAIN_OUTPUT_COLUMNS = [
    COL_POTTR_MATCH_STATUS,
    COL_POTTR_MANUAL_REVIEW_NEEDED,
    COL_POTTR_LOOKUP_TERMS,
    COL_POTTR_MATCHED_TERMS,
    COL_POTTR_DRUG_ENTRIES,
    COL_POTTR_DRUG_CLASSES,
    COL_POTTR_PRIMARY_CLASS,
    COL_POTTR_CLASS_ANCESTORS,
    COL_POTTR_CLASS_DESCENDANTS,
    COL_POTTR_CLASS_PATHS,
    COL_POTTR_CLASS_LINK_STATUS,
]


# -----------------------------------------------------------------------------
# ChEMBL columns
# -----------------------------------------------------------------------------

COL_CHEMBL_LINK_STATUS = "chembl_link_status"
COL_CHEMBL_MANUAL_REVIEW_NEEDED = "chembl_manual_review_needed"
COL_CHEMBL_LOOKUP_TERMS = "chembl_lookup_terms"
COL_CHEMBL_MATCHED_TERMS = "chembl_matched_terms"
COL_CHEMBL_IDS = "chembl_ids"
COL_CHEMBL_PREF_NAMES = "chembl_pref_names"
COL_CHEMBL_MOLREGNOS = "chembl_molregnos"
COL_CHEMBL_MAX_PHASES = "chembl_max_phases"
COL_CHEMBL_MOLECULE_TYPES = "chembl_molecule_types"
COL_CHEMBL_THERAPEUTIC_FLAGS = "chembl_therapeutic_flags"
COL_CHEMBL_WITHDRAWN_FLAGS = "chembl_withdrawn_flags"
COL_CHEMBL_MECHANISMS = "chembl_mechanisms"
COL_CHEMBL_ACTION_TYPES = "chembl_action_types"
COL_CHEMBL_TARGET_IDS = "chembl_target_ids"
COL_CHEMBL_TARGET_NAMES = "chembl_target_names"
COL_CHEMBL_TARGET_TYPES = "chembl_target_types"
COL_CHEMBL_TARGET_ORGANISMS = "chembl_target_organisms"
COL_CHEMBL_MECHANISM_TARGET_PAIRS = "chembl_mechanism_target_pairs"
COL_CHEMBL_INDICATIONS = "chembl_indications"
COL_CHEMBL_INDICATION_PHASE_PAIRS = "chembl_indication_phase_pairs"
COL_CHEMBL_INDICATION_MAX_PHASES = "chembl_indication_max_phases"

CHEMBL_MAIN_OUTPUT_COLUMNS = [
    COL_CHEMBL_LINK_STATUS,
    COL_CHEMBL_MANUAL_REVIEW_NEEDED,
    COL_CHEMBL_LOOKUP_TERMS,
    COL_CHEMBL_MATCHED_TERMS,
    COL_CHEMBL_IDS,
    COL_CHEMBL_PREF_NAMES,
    COL_CHEMBL_MOLREGNOS,
    COL_CHEMBL_MAX_PHASES,
    COL_CHEMBL_MOLECULE_TYPES,
    COL_CHEMBL_THERAPEUTIC_FLAGS,
    COL_CHEMBL_WITHDRAWN_FLAGS,
    COL_CHEMBL_MECHANISMS,
    COL_CHEMBL_ACTION_TYPES,
    COL_CHEMBL_TARGET_IDS,
    COL_CHEMBL_TARGET_NAMES,
    COL_CHEMBL_TARGET_TYPES,
    COL_CHEMBL_TARGET_ORGANISMS,
    COL_CHEMBL_MECHANISM_TARGET_PAIRS,
    COL_CHEMBL_INDICATIONS,
    COL_CHEMBL_INDICATION_PHASE_PAIRS,
    COL_CHEMBL_INDICATION_MAX_PHASES,
]
