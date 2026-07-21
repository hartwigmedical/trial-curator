"""Output schemas for the mapping task (spec §6.2, §7).

The mapping stage runs LLM mapper→reviewer procedures on the extraction output, mapping each distinct extracted
value to the eligibility vocabulary (lookup-first — a value is mapped once and reused, see EligStore):
- cancer_type -> OncoTree name + code,
- gene_alteration / molecular_signature -> finding-model syntax.

Cells preserve the extraction's inline AND / NOT() structure; the mapped code expression is validated against the
real OncoTree vocabulary. (Drug facts are NOT here — they live in the separate drug utility path, joined on
(trialId, arm).)
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


class ReviewVerdict(BaseModel):
    """A reviewer's verdict on a proposed mapping."""

    faithful: bool
    problems: list[str] = Field(
        default_factory=list,
        description="Concrete, actionable issues (wrong node/class, wrong granularity, invalid syntax, lost structure).",
    )
