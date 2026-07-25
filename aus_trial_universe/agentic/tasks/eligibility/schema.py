"""Eligibility output schema — 3NF relational tables (spec §6.1; docs/agentic/drug_ref_schema.md pattern).

The eligibility path emits normalized relational tables (like the drug path's 5 tables); the column order of
each persisted TSV derives from its dataclass field order, so the schema is the single source of truth.

The arm spine (`trial_arms`) is now the SHARED central table (`tasks/shared`); the eligibility path holds only
its two content tables, each linking to it by `trial_arm_id` (the FK — a deterministic slug of (trialId, arm)):

- `arm_eligibility_raw`    trial_arm_id -> 5 *_raw cells     — VERBATIM source fragments, each with its own
                           inline `[source]`, `|`-delimited; trial-wide criteria replicated onto each arm
- `interpreted_eligibility`(trial_arm_id, conjunction_index) -> 5 *_interpreted cells — the DNF logical
                           interpretation (ANDed terms, `NOT()` exclusions; rows sharing trial_arm_id are ORed)
- `cancer_type_map`        cancer_type value -> OncoTree name/code   } the value->vocabulary mapping lookups,
- `gene_alteration_map`    gene value -> finding-model               } deduped across the whole universe
- `molecular_signature_map` signature value -> finding-model         } (map once, reuse — lookup-first cache)

The raw text (grain: arm) and the interpreted conjunctions (grain: arm × conjunction) are different entities, so
they live in separate tables (3NF; the raw text is not duplicated across a conjunction's OR-branches).

The eligibility tables hold NO drug facts. Drugs join in via `trial_arm_id` against the drug path's
`trial_to_intervention` -> `intervention_to_canonical` -> `drug_annotations_core` (+ targets/approvals); both
paths resolve `trial_arm_id` from the shared `trial_arms` registry.
"""
from __future__ import annotations

from dataclasses import dataclass, fields


# The arm spine (`trial_arms`) is the SHARED central table — see `tasks/shared/schema.py`. The eligibility tables
# below link to it by `trial_arm_id` (the deterministic (trialId, arm) slug).


# --- Table 1: the verbatim raw eligibility text (per arm) -------------------- #
@dataclass
class ArmEligibilityRaw:
    """The VERBATIM source text for each criterion of an arm. Grain: (trial_arm_id). Each cell holds the
    relevant source fragment(s) copied verbatim, each with its own inline `[SECTION]` tag, `|`-delimited
    (e.g. `ovarian, fallopian tube or primary peritoneal cancer [ELIGIBILITY CRITERIA] | ...`). Trial-wide
    criteria are replicated onto every arm's row. This is the auditable layer: the reviewer checks it against
    the source (nothing missing, nothing extraneous, not truncated). No logic, no paraphrase."""

    trial_arm_id: str = ""   # FK -> shared trial_arms.trial_arm_id
    cancer_type_raw: str = ""
    gene_alteration_raw: str = ""
    molecular_signature_raw: str = ""
    molecular_biomarker_raw: str = ""
    prior_therapy_raw: str = ""


# --- Table 2: the interpreted DNF eligibility conjunctions ------------------- #
@dataclass
class InterpretedEligibility:
    """One DNF conjunction assigned to an arm. Grain: (trial_arm_id, conjunction_index). Cells are the
    logical INTERPRETATION of the raw text: normalized human descriptions with inline `NOT(...)` exclusions;
    a cell may hold several ANDed terms. Rows sharing trial_arm_id are OR-alternatives. NO source tags —
    provenance lives in `arm_eligibility_raw` (join on trial_arm_id)."""

    trial_arm_id: str = ""   # FK -> shared trial_arms.trial_arm_id
    conjunction_index: int = 0
    cancer_type_interpreted: str = ""
    gene_alteration_interpreted: str = ""
    molecular_signature_interpreted: str = ""
    molecular_biomarker_interpreted: str = ""
    prior_therapy_interpreted: str = ""


# --- Tables 3-5: value -> vocabulary mapping lookups (deduped, reused) -------- #
@dataclass
class CancerTypeMap:
    """cancer_type value -> OncoTree. Keyed by the interpreted cancer_type string; one row per distinct
    value across the whole universe (map once, reuse)."""

    cancer_type: str = ""       # the key (the value as it appears in interpreted_eligibility)
    oncotree_name: str = ""
    oncotree_code: str = ""


@dataclass
class GeneAlterationMap:
    """gene_alteration value -> finding-model. Keyed by the interpreted gene_alteration string."""

    gene_alteration: str = ""
    finding_model: str = ""


@dataclass
class MolecularSignatureMap:
    """molecular_signature value -> finding-model. Keyed by the interpreted molecular_signature string."""

    molecular_signature: str = ""
    finding_model: str = ""


def _columns(dc) -> list[str]:
    return [f.name for f in fields(dc)]


ARM_ELIGIBILITY_RAW_COLUMNS = _columns(ArmEligibilityRaw)
INTERPRETED_ELIGIBILITY_COLUMNS = _columns(InterpretedEligibility)
CANCER_TYPE_MAP_COLUMNS = _columns(CancerTypeMap)
GENE_ALTERATION_MAP_COLUMNS = _columns(GeneAlterationMap)
MOLECULAR_SIGNATURE_MAP_COLUMNS = _columns(MolecularSignatureMap)

# The five eligibility criterion stems (column base names shared by the raw + interpreted tables).
CRITERION_STEMS = [
    "cancer_type", "gene_alteration", "molecular_signature", "molecular_biomarker", "prior_therapy",
]

TABLE_FILES = {
    "arm_eligibility_raw": "arm_eligibility_raw.tsv",
    "interpreted_eligibility": "interpreted_eligibility.tsv",
    "cancer_type_map": "cancer_type_map.tsv",
    "gene_alteration_map": "gene_alteration_map.tsv",
    "molecular_signature_map": "molecular_signature_map.tsv",
}
