"""Schemas for the POTTR workspace.

⚠ A pydantic docstring and every `Field(description=...)` below is hashed into the response-cache key
(`core/client._fingerprint` hashes `model_json_schema()`). Editing this prose re-rolls cached answers.
"""
from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field


class DiseaseDerivedAlteration(BaseModel):
    """The gene alteration a cancer-type wording DEFINITIONALLY entails, or none at all."""

    derived_alteration: str = Field(
        default="",
        description=(
            "The entailed gene alteration in plain clinical English, phrased as a trial would phrase a "
            "gene-alteration criterion (e.g. 'BCR::ABL1 fusion', 'NOT(KIT mutation) AND NOT(PDGFRA mutation)'). "
            "Empty when the disease entails no specific alteration — which is the normal, expected answer."
        ),
    )
    basis: str = Field(
        default="",
        description=(
            "One short clause naming why the alteration is DEFINITIONAL — the diagnostic criterion, the defining "
            "fusion, or the qualifier that states it. Empty when derived_alteration is empty. A vague basis "
            "('well-known association') is itself evidence the answer should have been empty."
        ),
    )


class AlignedPair(BaseModel):
    """One POTTR term judged to mean the same criterion as one of our values."""

    pottr_term: str = Field(description="The POTTR term, copied verbatim from the list you were given.")
    our_value: str = Field(description="Our value, copied verbatim from the list you were given.")
    reason: str = Field(default="", description="One short clause on why they are the same criterion.")


class FreeTextAlignment(BaseModel):
    """The alignment of two free-text criterion lists that share no controlled vocabulary.

    Used for `prior_therapy` and `molecular_biomarker`, where neither side maps to a target vocabulary, so
    equivalence is a judgement rather than a set operation.
    """

    aligned: list[AlignedPair] = Field(
        default_factory=list,
        description="Pairs that state the SAME criterion, even if worded very differently.",
    )
    pottr_only: list[str] = Field(
        default_factory=list,
        description="POTTR terms with no counterpart in our list, verbatim.",
    )
    ours_only: list[str] = Field(
        default_factory=list,
        description="Our values with no counterpart in POTTR's list, verbatim.",
    )


class MistakeVerdict(BaseModel):
    """Which curation is wrong about a difference, judged against the trial's own registry text."""

    verdict: str = Field(
        description=(
            "Exactly one of: 'ours_wrong' · 'pottr_wrong' · 'both_wrong' · 'neither_wrong' · 'pottr_omission' · "
            "'ours_omission' · 'undecidable'. An OMISSION is not an error: use 'pottr_omission' when the source "
            "supports our value and POTTR simply did not record that criterion, and 'ours_omission' for the "
            "mirror case. Reserve '*_wrong' for a side that records something the source CONTRADICTS. "
            "'neither_wrong' is right when both readings are defensible from the source, or when the two sides "
            "record the same fact at different granularity or in different columns."
        )
    )
    reason: str = Field(
        description=(
            "The justification, quoting or closely paraphrasing the SOURCE TEXT that decides it. A verdict that "
            "cites neither side's source wording is not usable — say 'undecidable' instead."
        )
    )
    correct_value: str = Field(
        default="",
        description="What the criterion should be, when one side is wrong and the source supports a definite answer.",
    )


@dataclass
class DiseaseDerivedRow:
    """One row of the component-2 lookup: a cancer-type value and everything derived from it.

    3NF single-key — the inference is a pure function of `cancer_type`, which is what lets it be cached, reviewed
    once, and joined onto any row carrying that value.
    """

    cancer_type: str
    derived_alteration: str
    basis: str
    #: The `derived_alteration` put through the PRODUCTION gene mapper, so it lands in the same grammar as
    #: `gene_alteration_findingmodel` and is directly comparable to it. Empty whenever the alteration is empty.
    finding_model: str = ""
    #: False when the doer→reviewer loop ended still flagged; the row is written either way, as elsewhere.
    faithful: bool = True
    attempts: int = 0
    problems: str = ""

    COLUMNS = ("cancer_type", "derived_alteration", "basis", "finding_model", "faithful", "attempts", "problems")

    def as_row(self) -> dict[str, str]:
        return {
            "cancer_type": self.cancer_type,
            "derived_alteration": self.derived_alteration,
            "basis": self.basis,
            "finding_model": self.finding_model,
            "faithful": "true" if self.faithful else "false",
            "attempts": str(self.attempts),
            "problems": self.problems,
        }
