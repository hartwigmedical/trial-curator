"""Output schemas for the mapping task (spec §6.2, §7).

The mapping stage runs LLM mapper→reviewer procedures on the extraction output:
- cancer_type -> OncoTree name + code (this module),
- (later) gene_alteration / molecular_signature -> finding-model syntax,
- (later) drug -> POTTR class / TGA / PBS.

Cells preserve the extraction's inline AND / NOT() structure; the mapped code
expression is validated against the real OncoTree vocabulary.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class OncotreeMapping(BaseModel):
    """Mapper output for one cancer-type expression."""

    oncotree_name: str = Field(
        description="The cancer-type expression re-expressed with OncoTree NAMES, AND/NOT preserved."
    )
    oncotree_code: str = Field(
        description="The same expression re-expressed with OncoTree CODES, AND/NOT preserved."
    )


class FindingModelMapping(BaseModel):
    """Mapper output for one gene-alteration or molecular-signature expression."""

    finding_model: str = Field(
        description="The term in Hartwig finding-model syntax (Class[field=value & ...]; | / & / NOT()). "
                    '"" if the term has no finding-model representation.'
    )


class DrugCuration(BaseModel):
    """Trial-level drug enrichment for the main investigational drug(s)/regimen."""

    main_drugs: str = Field(
        description="The trial's MAIN investigational drug(s) or regimen under study, listed first "
                    "('; '-joined; a regimen as one element, e.g. 'FOLFOX')."
    )
    auxiliary_drugs: str = Field(
        default="",
        description="Comparators / chemo backbone / supportive / placebo drugs (not the investigational focus).",
    )
    pottr_drug_class: str = Field(
        default="",
        description="POTTR drug-class hierarchy of the main drug(s)/regimen, ' -> '-joined per drug. "
                    '"" if not in POTTR.',
    )
    drug_class: str = Field(
        description="General (non-POTTR) drug class / mechanism of the main drug(s)/regimen (web search if needed)."
    )
    tga_status: str = Field(
        description="Per main drug, TGA/ARTG approval as 'drug: Approved' or 'drug: Not approved' "
                    "('; '-joined across the main drugs). Do NOT count SAS / Authorised Prescriber / trial supply."
    )
    pbs_status: str = Field(
        description="Per main drug, PBS listing as 'drug: Approved' or 'drug: Not approved' ('; '-joined)."
    )
    tga_detail: str = Field(
        default="",
        description="Evidence for tga_status, per main drug: year of ARTG approval + brief rationale + official "
                    "source link (tga.gov.au / ARTG). 'Unclear' where not determinable.",
    )
    pbs_detail: str = Field(
        default="",
        description="Evidence for pbs_status, per main drug: year/indication of PBS listing + brief rationale + "
                    "official source link (pbs.gov.au). 'Unclear' where not determinable.",
    )


class ReviewVerdict(BaseModel):
    """A reviewer's verdict on a proposed mapping."""

    faithful: bool
    problems: list[str] = Field(
        default_factory=list,
        description="Concrete, actionable issues (wrong node/class, wrong granularity, invalid syntax, lost structure).",
    )
