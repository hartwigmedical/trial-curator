"""Eligibility output schema — 3NF relational tables (spec §6.1; docs/agentic/drug_ref_schema.md pattern).

The eligibility path emits normalized relational tables (like the drug path's 5 tables); the column order of
each persisted TSV derives from its dataclass field order, so the schema is the single source of truth. Tables:

- `regime`                 (trialId, arm) -> arm_type          — the arm spine; JOIN key to the drug path
- `extracted_eligibility`  (trialId, arm, conj_id) -> 5 cells  — the DNF conjunctions (raw, [source] + NOT())
- `cancer_type_map`        cancer_type value -> OncoTree name/code   } the value->vocabulary mapping lookups,
- `gene_alteration_map`    gene value -> finding-model               } deduped across the whole universe
- `molecular_signature_map` signature value -> finding-model         } (map once, reuse — lookup-first cache)

The eligibility tables hold NO drug facts. Drugs join in via the (trialId, arm) key against the drug path's
`trial_to_intervention` -> `intervention_to_canonical` -> `drug_annotations_core` (+ targets/approvals).

DNF semantics: rows sharing (trialId, arm) are ORed; cells within a row are ANDed; exclusions inline as NOT(...).
"""
from __future__ import annotations

from dataclasses import dataclass, fields


# --- Table 1: the arm/regime spine (the join key to the drug utility path) --- #
@dataclass
class Regime:
    """One trial arm/regime. Grain: (trialId, arm). CTGov: one per drug-bearing armGroup (label + type);
    ANZCTR: `intervention` (EXPERIMENTAL) + optional `comparator` (ACTIVE_COMPARATOR); fallback `all`."""

    trialId: str = ""     # NCT... (ctgov) | ACTRN... (anzctr)
    arm: str = ""         # CTGov armGroups[].label | ANZCTR intervention|comparator | "all" — the join key
    arm_type: str = ""    # EXPERIMENTAL / ACTIVE_COMPARATOR / PLACEBO_COMPARATOR / ... (flags control arms)


# --- Table 2: the extracted DNF eligibility conjunctions --------------------- #
@dataclass
class ExtractedEligibility:
    """One DNF conjunction assigned to a (trial, arm). Grain: (trialId, arm, conj_id). Cells are raw normalized
    human descriptions with inline `[source]` provenance and `NOT(...)` exclusions; a cell may hold several ANDed
    terms. Rows sharing (trialId, arm) are OR-alternatives."""

    trialId: str = ""
    arm: str = ""
    conj_id: int = 0
    cancer_type: str = ""
    gene_alteration: str = ""
    molecular_signature: str = ""
    molecular_biomarker: str = ""
    prior_therapy: str = ""


# --- Tables 3-5: value -> vocabulary mapping lookups (deduped, reused) -------- #
@dataclass
class CancerTypeMap:
    """cancer_type value -> OncoTree. Keyed by the provenance-stripped cancer_type string; one row per distinct
    value across the whole universe (map once, reuse)."""

    cancer_type: str = ""       # the key (provenance-stripped value as it appears in extracted_eligibility)
    oncotree_name: str = ""
    oncotree_code: str = ""


@dataclass
class GeneAlterationMap:
    """gene_alteration value -> finding-model. Keyed by the provenance-stripped gene_alteration string."""

    gene_alteration: str = ""
    finding_model: str = ""


@dataclass
class MolecularSignatureMap:
    """molecular_signature value -> finding-model. Keyed by the provenance-stripped molecular_signature string."""

    molecular_signature: str = ""
    finding_model: str = ""


def _columns(dc) -> list[str]:
    return [f.name for f in fields(dc)]


REGIME_COLUMNS = _columns(Regime)
EXTRACTED_ELIGIBILITY_COLUMNS = _columns(ExtractedEligibility)
CANCER_TYPE_MAP_COLUMNS = _columns(CancerTypeMap)
GENE_ALTERATION_MAP_COLUMNS = _columns(GeneAlterationMap)
MOLECULAR_SIGNATURE_MAP_COLUMNS = _columns(MolecularSignatureMap)

TABLE_FILES = {
    "regime": "regime.tsv",
    "extracted_eligibility": "extracted_eligibility.tsv",
    "cancer_type_map": "cancer_type_map.tsv",
    "gene_alteration_map": "gene_alteration_map.tsv",
    "molecular_signature_map": "molecular_signature_map.tsv",
}
