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


# ⚠ The class docstring and every Field description below are hashed into the response-cache key (pydantic folds
# them into the JSON schema, and `client._fingerprint` hashes that). Editing this prose silently orphans every
# cached decision behind it — including the values signed off on 2026-08-04. Change it only with a re-run.
#
# Why `oncotree_name` is gone: the old schema asked the LLM for two independent renderings of the same expression
# and stored both with nothing cross-checking them, which is how 46 rows ended up with a name that did not match
# their code. The name is now derived by `tools.oncotree.render_name_expression`.
class OncotreeMapping(BaseModel):
    """Mapper output for one cancer-type expression — the CODE expression only.

    The matching `oncotree_name` is rendered from this deterministically; never author it here.
    """

    oncotree_code: str = Field(
        description="The cancer-type expression re-expressed with OncoTree CODES, its AND / OR / NOT(...) "
                    'structure preserved. "" if the value is not an oncological condition.'
    )


class FindingModelMapping(BaseModel):
    """Mapper output for one gene-alteration or molecular-signature expression."""

    finding_model: str = Field(
        description="The term in Hartwig finding-model syntax (Class[field=value & ...]; | / & / NOT()). "
                    '"" if the term has no finding-model representation.'
    )


class ReconciledMember(BaseModel):
    """The FINAL vocabulary value for one member of a reconciliation group (Step 2)."""

    input: str = Field(description="The member's input value, copied verbatim from the group.")
    final_value: str = Field(
        description="The reconciled FINAL mapping for this input (an OncoTree code expression, or a finding-model "
                    "expression). Equivalent members share ONE value; genuinely-distinct members keep their own."
    )


class ReviewVerdict(BaseModel):
    """A reviewer's verdict on a proposed mapping."""

    faithful: bool
    problems: list[str] = Field(
        default_factory=list,
        description="Concrete, actionable issues (wrong node/class, wrong granularity, invalid syntax, lost structure).",
    )
    suggested_fix: str = Field(
        default="",
        description=(
            "OPTIONAL. When not faithful, the concrete corrected mapping you would expect (the exact value). This is "
            "ADVICE handed to the mapper, which regenerates and is re-checked — NOT applied directly. Normally leave "
            "empty (reporting problems is enough); fill it only when the input is marked '[ESCALATION-MODE]'."
        ),
    )
