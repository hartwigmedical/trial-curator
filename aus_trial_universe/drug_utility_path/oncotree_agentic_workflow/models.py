from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Scope decided once at the root of the navigation. Anything other than
# "specific" short-circuits the tree walk. The values are the project fallback
# codes: valid mapper outputs that are not OncoTree codes in the YAML, spelled
# to match the curation truth set ("Solid-Tumor").
SCOPE_TO_FALLBACK_CODE: dict[str, str] = {
    "solid_tumour": "Solid-Tumor",
    "pan_cancer": "Pan-cancer",
}
ALLOWED_FALLBACK_CODES = frozenset(SCOPE_TO_FALLBACK_CODE.values())

MappingStatus = Literal["accepted", "failed", "needs_human_review"]
Confidence = Literal["high", "medium", "low"]
Scope = Literal["specific", "solid_tumour", "pan_cancer", "non_cancer"]

# How the navigator treats one node it is shown:
# - descend: the input entails this branch, keep walking into its children
# - emit: the input is entailed exactly by this node, stop and return it
# - skip: the input does not relate to this node, prune it
NavAction = Literal["descend", "emit", "skip"]


@dataclass(frozen=True)
class OncoTreeNode:
    code: str
    name: str
    level: int
    parent_code: str | None = None
    children_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class MappingCandidate:
    """A code the mapper proposes as a final answer."""

    code: str
    confidence: Confidence = "medium"
    rationale: str = ""


@dataclass(frozen=True)
class MappingFeedback:
    """A reviewer rejection handed back to the mapper for the next round."""

    code: str
    note: str


@dataclass(frozen=True)
class NavDecision:
    """The navigator's verdict on one node it was shown during a step."""

    code: str
    name: str
    action: NavAction
    confidence: Confidence = "medium"
    rationale: str = ""


@dataclass(frozen=True)
class NavigationStep:
    """One LLM call: the children of `parent_code` and the chosen actions.

    `parent_code` is None for the root step, where the frontier is the set of
    OncoTree root organs rather than the children of a single node.
    """

    parent_code: str | None
    parent_name: str
    stop_here: bool
    decisions: tuple[NavDecision, ...]


@dataclass(frozen=True)
class NavigationTrace:
    """The full record of one mapper pass, shared with the reviewer and log."""

    scope: Scope
    steps: tuple[NavigationStep, ...] = ()
    truncated: bool = False


@dataclass(frozen=True)
class MappingOutcome:
    """What a mapper returns: the proposed codes plus how it got there."""

    proposed_mappings: tuple[MappingCandidate, ...]
    trace: NavigationTrace


@dataclass(frozen=True)
class ReviewFinding:
    """A blocking reason the reviewer rejects a mapping; becomes mapper feedback."""

    code: str
    reason: str
    suggested_action: str = ""


@dataclass(frozen=True)
class ReviewResult:
    valid_codes: bool
    semantically_correct: bool
    findings: tuple[ReviewFinding, ...] = ()
    reasoning: tuple[str, ...] = ()

    @property
    def accepted(self) -> bool:
        return self.valid_codes and self.semantically_correct


@dataclass(frozen=True)
class MappingIteration:
    iteration: int
    mapper_feedback: tuple[MappingFeedback, ...]
    trace: NavigationTrace
    proposed_mappings: tuple[MappingCandidate, ...]
    review: ReviewResult


@dataclass(frozen=True)
class WorkflowResult:
    input_text: str
    status: MappingStatus
    codes: tuple[str, ...]
    iterations: tuple[MappingIteration, ...] = ()
