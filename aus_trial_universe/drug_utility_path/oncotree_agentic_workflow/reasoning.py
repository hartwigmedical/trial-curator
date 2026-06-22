from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from aus_trial_universe.drug_utility_path.oncotree_agentic_workflow.models import (
    MappingCandidate,
    NavigationTrace,
    ReviewResult,
)
from aus_trial_universe.drug_utility_path.oncotree_agentic_workflow.oncotree import OncoTree
from aus_trial_universe.drug_utility_path.oncotree_agentic_workflow.schemas import (
    SEMANTIC_REVIEW_FORMAT,
    parse_findings,
)

DEFAULT_REASONING_MODEL = "gpt-5.5"
DEFAULT_REASONING_EFFORT = "medium"

_JSON_OBJECT_FORMAT: dict[str, Any] = {"type": "json_object"}


class ReasoningClient(Protocol):
    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_format: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        ...


class OpenAIReasoningClient:
    """Thin OpenAI wrapper that returns parsed JSON, with strict-schema support.

    `response_format` is a Responses-API ``text.format`` object (e.g. a strict
    ``json_schema`` produced by :mod:`schemas`). It is translated to the
    chat-completions shape when the installed client only exposes that API.
    """

    def __init__(
        self,
        *,
        model: str = DEFAULT_REASONING_MODEL,
        reasoning_effort: str | None = DEFAULT_REASONING_EFFORT,
        max_output_tokens: int | None = None,
        store: bool | None = None,
        openai_client: Any | None = None,
    ) -> None:
        self.model = model
        self.reasoning_effort = _coerce_reasoning_effort(reasoning_effort)
        self.max_output_tokens = max_output_tokens
        self.store = store
        self.client = openai_client if openai_client is not None else self._make_client()

    @staticmethod
    def _make_client() -> Any:
        try:
            import openai
        except ImportError as error:
            raise ImportError(
                "The `openai` package is required for reasoning model mode."
            ) from error

        client_factory = getattr(openai, "OpenAI", None) or getattr(openai, "Client", None)
        if client_factory is None:
            raise RuntimeError("The installed `openai` package does not expose OpenAI().")
        return client_factory()

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_format: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        text_format = dict(response_format) if response_format is not None else _JSON_OBJECT_FORMAT

        if hasattr(self.client, "responses"):
            kwargs: dict[str, Any] = {
                "model": self.model,
                "input": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "text": {"format": text_format},
            }
            if self.reasoning_effort is not None:
                kwargs["reasoning"] = {"effort": self.reasoning_effort}
            if self.max_output_tokens is not None:
                kwargs["max_output_tokens"] = self.max_output_tokens
            if self.store is not None:
                kwargs["store"] = self.store

            response = self.client.responses.create(**kwargs)
            return _parse_json(_response_text(response))

        kwargs = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": _to_chat_response_format(text_format),
        }
        if self.reasoning_effort is not None:
            kwargs["reasoning_effort"] = self.reasoning_effort
        if self.max_output_tokens is not None:
            kwargs["max_completion_tokens"] = self.max_output_tokens

        response = self.client.chat.completions.create(**kwargs)
        return _parse_json(_response_text(response))


class ReasoningSemanticReviewer:
    """LLM reviewer that judges the navigator's stop points against the input.

    It receives the full navigation trace, so it can reason about whether the
    mapper stopped too deep (prefer an ancestor), too shallow (a skipped child
    was entailed), or descended the wrong branch entirely.
    """

    def __init__(self, client: ReasoningClient, tree: OncoTree) -> None:
        self.client = client
        self.tree = tree

    def review(
        self,
        *,
        input_text: str,
        mappings: Sequence[MappingCandidate],
        trace: NavigationTrace,
    ) -> ReviewResult:
        if not mappings:
            return ReviewResult(
                valid_codes=True,
                semantically_correct=False,
                reasoning=("No mapping was proposed, so there is nothing to accept.",),
            )

        payload = self.client.complete_json(
            system_prompt=REVIEWER_SYSTEM_PROMPT,
            user_prompt=self._user_prompt(input_text, mappings, trace),
            response_format=SEMANTIC_REVIEW_FORMAT,
        )
        reasoning = _string_list(payload.get("reasoning"))
        semantically_correct = bool(payload.get("semantically_correct", False))
        findings = parse_findings(payload.get("findings"))

        return ReviewResult(
            valid_codes=True,
            semantically_correct=semantically_correct and not findings,
            findings=findings,
            reasoning=tuple(reasoning),
        )

    def _user_prompt(
        self,
        input_text: str,
        mappings: Sequence[MappingCandidate],
        trace: NavigationTrace,
    ) -> str:
        lines = [
            f"Input text: {input_text}",
            f"Navigator scope decision: {trace.scope}",
            "",
            "Proposed mappings (with their OncoTree path):",
        ]
        for mapping in mappings:
            path = " > ".join(self.tree.path_names(mapping.code)) or "(fallback value)"
            lines.append(
                f"- {mapping.code}: path={path}; confidence={mapping.confidence}; "
                f"rationale={mapping.rationale}"
            )
        lines.extend(["", "Navigation trace (each step the mapper took):"])
        lines.extend(describe_trace_lines(trace))
        return "\n".join(lines)


REVIEWER_SYSTEM_PROMPT = """
You are an independent reviewer of OncoTree mappings produced by a tree-walking
mapper. Every proposed code provably exists in OncoTree, so do not check code
existence. Your job is purely semantic:

- Is each mapped code clinically entailed by the input text?
- Is it over-specific? The mapper may have descended too far. If the input only
  supports an ancestor on the same path, reject and point to that ancestor.
- Is it under-specific? The mapper may have stopped too early. If the input
  clearly names a subtype that was a skipped/available child, reject and say so.
- Was a "Not Otherwise Specified (NOS)" subtype chosen for a generic organ-level
  term (e.g. "soft tissue sarcoma" -> a "Soft Tissue Sarcoma, NOS" code)? That
  is over-specific: reject and point to the organ root.
- Did the mapper descend the wrong branch (e.g. confused primary cancer type
  with a metastatic site, or matched an unrelated organ)?
- Did it map a benign or non-cancer condition to a cancer code?

Use the navigation trace to justify your verdict. Return JSON with keys:
semantically_correct (bool), reasoning (list of strings), findings (list of
objects with code, reason, suggested_action). Only raise a finding when you are
confident the mapping is wrong; a finding sends the input back to the mapper.
""".strip()


def describe_trace_lines(trace: NavigationTrace) -> list[str]:
    """Render a navigation trace as human/LLM-readable lines."""

    if not trace.steps:
        return ["  (no navigation steps recorded)"]

    lines: list[str] = []
    for step in trace.steps:
        header = "roots" if step.parent_code is None else f"{step.parent_code} ({step.parent_name})"
        suffix = " [stop_here]" if step.stop_here else ""
        lines.append(f"  At {header}{suffix}:")
        if not step.decisions:
            lines.append("    (no decisions)")
        for decision in step.decisions:
            lines.append(
                f"    - {decision.action.upper()} {decision.code} ({decision.name}): "
                f"{decision.rationale or 'no rationale'}"
            )
    return lines


def _coerce_reasoning_effort(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"", "none", "off"}:
        return None
    return normalized


def _to_chat_response_format(text_format: Mapping[str, Any]) -> dict[str, Any]:
    if text_format.get("type") == "json_schema":
        return {
            "type": "json_schema",
            "json_schema": {
                "name": text_format["name"],
                "description": text_format.get("description", ""),
                "strict": text_format.get("strict", True),
                "schema": text_format["schema"],
            },
        }
    return {"type": text_format.get("type", "json_object")}


def _string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value:
        return [str(value).strip()]
    return []


def _parse_json(text: str) -> Mapping[str, Any]:
    payload = json.loads(text)
    if not isinstance(payload, Mapping):
        raise ValueError("Reasoning model response must be a JSON object.")
    return payload


def _response_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if output_text:
        return str(output_text)

    if isinstance(response, Mapping):
        if response.get("output_text"):
            return str(response["output_text"])
        choices = response.get("choices", [])
        output = response.get("output", [])
    else:
        choices = getattr(response, "choices", [])
        output = getattr(response, "output", [])

    chat_text = _chat_choices_text(choices)
    if chat_text:
        return chat_text

    for item in output:
        content = _get_field(item, "content", [])
        for content_item in content:
            text = _get_field(content_item, "text", None)
            if text is not None:
                return str(text)

    raise ValueError("Could not extract reasoning model response text.")


def _chat_choices_text(choices: Any) -> str | None:
    if not choices:
        return None

    first_choice = choices[0]
    message = _get_field(first_choice, "message", None)
    if message is None:
        return None

    content = _get_field(message, "content", None)
    if content is None:
        return None
    return str(content)


def _get_field(value: Any, field: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(field, default)
    return getattr(value, field, default)
