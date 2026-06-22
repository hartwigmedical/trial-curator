"""Strict JSON-schema response formats for the navigator and reviewer.

These mirror the house pattern in
``aus_trial_universe/trial_drug_curation/llm/structured_outputs.py``: hand-built
JSON-schema dicts with ``"strict": True``. Constraining the ``code`` fields with
``enum`` to the exact codes shown at each step is what makes hallucinated codes
impossible at generation time, rather than something the checker has to catch.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, get_args

from aus_trial_universe.drug_utility_path.oncotree_agentic_workflow.models import (
    SCOPE_TO_FALLBACK_CODE,
    Confidence,
    NavAction,
    NavDecision,
    OncoTreeNode,
    ReviewFinding,
    Scope,
)

# Derived from the model Literals / scope map so the schema enums cannot drift.
_CONFIDENCE_VALUES = list(get_args(Confidence))
_NAV_ACTION_VALUES = list(get_args(NavAction))
_SCOPE_VALUES = list(get_args(Scope))
_FALLBACK_VALUES = ["none", *SCOPE_TO_FALLBACK_CODE]


def _response_format(name: str, description: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Build a Responses-API ``text.format`` object (strict json_schema)."""

    return {
        "type": "json_schema",
        "name": name,
        "description": description,
        "strict": True,
        "schema": schema,
    }


def _decision_item_schema(codes: Sequence[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["code", "action", "confidence", "rationale"],
        "properties": {
            "code": {"type": "string", "enum": list(codes)},
            "action": {"type": "string", "enum": _NAV_ACTION_VALUES},
            "confidence": {"type": "string", "enum": _CONFIDENCE_VALUES},
            "rationale": {"type": "string"},
        },
    }


def root_decision_format(roots: Sequence[OncoTreeNode]) -> dict[str, Any]:
    """Schema for the root step: classify scope and act on each root organ."""

    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["scope", "include_fallback", "roots"],
        "properties": {
            "scope": {"type": "string", "enum": _SCOPE_VALUES},
            # When scope is "specific", a broad term in the same input can still
            # require a fallback code to be emitted alongside the specific codes.
            "include_fallback": {"type": "string", "enum": _FALLBACK_VALUES},
            "roots": {
                "type": "array",
                "items": _decision_item_schema([node.code for node in roots]),
            },
        },
    }
    return _response_format(
        "oncotree_root_decision",
        "Scope classification, optional fallback, and per-organ navigation actions.",
        schema,
    )


def node_decision_format(children: Sequence[OncoTreeNode]) -> dict[str, Any]:
    """Schema for a non-root step: stop here, and/or act on each child."""

    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["stop_here", "children"],
        "properties": {
            "stop_here": {"type": "boolean"},
            "children": {
                "type": "array",
                "items": _decision_item_schema([node.code for node in children]),
            },
        },
    }
    return _response_format(
        "oncotree_node_decision",
        "Whether to stop at this node and how to act on each child.",
        schema,
    )


SEMANTIC_REVIEW_FORMAT: dict[str, Any] = _response_format(
    "oncotree_semantic_review",
    "Independent semantic review of proposed OncoTree mappings.",
    {
        "type": "object",
        "additionalProperties": False,
        "required": ["semantically_correct", "reasoning", "findings"],
        "properties": {
            "semantically_correct": {"type": "boolean"},
            "reasoning": {"type": "array", "items": {"type": "string"}},
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["code", "reason", "suggested_action"],
                    "properties": {
                        "code": {"type": "string"},
                        "reason": {"type": "string"},
                        "suggested_action": {"type": "string"},
                    },
                },
            },
        },
    },
)


def parse_decisions(
    raw_items: object,
    shown: Sequence[OncoTreeNode],
) -> tuple[NavDecision, ...]:
    """Turn the model's decision list into NavDecisions, ignoring junk rows.

    Only codes that were actually shown at this step are honoured; the strict
    enum should already guarantee this, but we re-check defensively.
    """

    names_by_code = {node.code: node.name for node in shown}
    if not isinstance(raw_items, list):
        return ()

    decisions: list[NavDecision] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code", "")).strip()
        if code not in names_by_code:
            continue
        action = str(item.get("action", "")).strip()
        if action not in _NAV_ACTION_VALUES:
            continue
        decisions.append(
            NavDecision(
                code=code,
                name=names_by_code[code],
                action=action,  # type: ignore[arg-type]
                confidence=_coerce_confidence(item.get("confidence")),
                rationale=str(item.get("rationale", "")).strip(),
            )
        )
    return tuple(decisions)


def parse_findings(raw_items: object) -> tuple[ReviewFinding, ...]:
    if not isinstance(raw_items, list):
        return ()

    findings: list[ReviewFinding] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code", "")).strip()
        reason = str(item.get("reason", "")).strip()
        if not code or not reason:
            continue
        findings.append(
            ReviewFinding(
                code=code,
                reason=reason,
                suggested_action=str(item.get("suggested_action", "")).strip(),
            )
        )
    return tuple(findings)


def coerce_scope(value: object) -> str:
    scope = str(value or "specific").strip().lower()
    return scope if scope in _SCOPE_VALUES else "specific"


def coerce_fallback(value: object) -> str:
    fallback = str(value or "none").strip().lower()
    return fallback if fallback in _FALLBACK_VALUES else "none"


def _coerce_confidence(value: object) -> Confidence:
    confidence = str(value or "medium").strip().lower()
    if confidence in _CONFIDENCE_VALUES:
        return confidence  # type: ignore[return-value]
    return "medium"
