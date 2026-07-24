"""Eligibility output schema — 3NF relational tables (spec §6.1; docs/agentic/drug_ref_schema.md pattern).

The eligibility path emits normalized relational tables (like the drug path's 5 tables); the column order of
each persisted TSV derives from its dataclass field order, so the schema is the single source of truth.

Extraction is split into two grains (the raw text vs. its logical interpretation are different entities — 3NF):

- `trial_arms`             (trialId, arm) -> arm_type          — the arm spine; JOIN key to the drug path
- `arm_eligibility_raw`    (trialId, arm) -> 5 *_raw cells     — VERBATIM source fragments, each with its own
                           inline `[source]`, `|`-delimited; trial-wide criteria replicated onto each arm
- `interpreted_eligibility`(trialId, arm, conjunction_index) -> 5 *_interpreted cells — the DNF logical
                           interpretation (ANDed terms, `NOT()` exclusions; rows sharing (trialId, arm) are ORed)
- `cancer_type_map`        cancer_type value -> OncoTree name/code   } the value->vocabulary mapping lookups,
- `gene_alteration_map`    gene value -> finding-model               } deduped across the whole universe
- `molecular_signature_map` signature value -> finding-model         } (map once, reuse — lookup-first cache)

The `*_raw` cells depend only on (trialId, arm); the `*_interpreted` cells depend on the full
(trialId, arm, conjunction_index) key (the OR-branch structure is decided during interpretation) — so they
live in separate tables (the raw text is not duplicated across a conjunction's OR-branches).

The eligibility tables hold NO drug facts. Drugs join in via the (trialId, arm) key against the drug path's
`trial_to_intervention` -> `intervention_to_canonical` -> `drug_annotations_core` (+ targets/approvals).
"""
from __future__ import annotations

from dataclasses import dataclass, fields


# --- Table 1: the arm spine (the join key to the drug utility path) ---------- #
@dataclass
class TrialArm:
    """One trial arm/regime. Grain: (trialId, arm). CTGov: one per drug-bearing armGroup (label + type);
    ANZCTR: `intervention` (EXPERIMENTAL) + optional `comparator` (ACTIVE_COMPARATOR); fallback `all`."""

    trialId: str = ""     # NCT... (ctgov) | ACTRN... (anzctr)
    arm: str = ""         # CTGov armGroups[].label | ANZCTR intervention|comparator | "all" — the join key
    arm_type: str = ""    # EXPERIMENTAL / ACTIVE_COMPARATOR / PLACEBO_COMPARATOR / ... (flags control arms)


# --- Table 2: the verbatim raw eligibility text (per arm) -------------------- #
@dataclass
class ArmEligibilityRaw:
    """The VERBATIM source text for each criterion of an arm. Grain: (trialId, arm). Each cell holds the
    relevant source fragment(s) copied verbatim, each with its own inline `[SECTION]` tag, `|`-delimited
    (e.g. `ovarian, fallopian tube or primary peritoneal cancer [ELIGIBILITY CRITERIA] | ...`). Trial-wide
    criteria are replicated onto every arm's row. This is the auditable layer: the reviewer checks it against
    the source (nothing missing, nothing extraneous, not truncated). No logic, no paraphrase."""

    trialId: str = ""
    arm: str = ""
    cancer_type_raw: str = ""
    gene_alteration_raw: str = ""
    molecular_signature_raw: str = ""
    molecular_biomarker_raw: str = ""
    prior_therapy_raw: str = ""


# --- Table 3: the interpreted DNF eligibility conjunctions ------------------- #
@dataclass
class InterpretedEligibility:
    """One DNF conjunction assigned to a (trial, arm). Grain: (trialId, arm, conjunction_index). Cells are the
    logical INTERPRETATION of the raw text: normalized human descriptions with inline `NOT(...)` exclusions;
    a cell may hold several ANDed terms. Rows sharing (trialId, arm) are OR-alternatives. NO source tags —
    provenance lives in `arm_eligibility_raw` (join on (trialId, arm))."""

    trialId: str = ""
    arm: str = ""
    conjunction_index: int = 0
    cancer_type_interpreted: str = ""
    gene_alteration_interpreted: str = ""
    molecular_signature_interpreted: str = ""
    molecular_biomarker_interpreted: str = ""
    prior_therapy_interpreted: str = ""


# --- Tables 4-6: value -> vocabulary mapping lookups (deduped, reused) -------- #
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


TRIAL_ARMS_COLUMNS = _columns(TrialArm)
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
    "trial_arms": "trial_arms.tsv",
    "arm_eligibility_raw": "arm_eligibility_raw.tsv",
    "interpreted_eligibility": "interpreted_eligibility.tsv",
    "cancer_type_map": "cancer_type_map.tsv",
    "gene_alteration_map": "gene_alteration_map.tsv",
    "molecular_signature_map": "molecular_signature_map.tsv",
}
