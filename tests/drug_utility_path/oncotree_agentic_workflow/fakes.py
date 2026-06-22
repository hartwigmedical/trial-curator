"""Test doubles for the OncoTree agentic workflow.

`ScriptedNavClient` fakes the reasoning client by dispatching on the strict
response-format name the production code sends:
- ``oncotree_root_decision``  -> scope + per-root actions
- ``oncotree_node_decision``  -> stop_here + per-child actions (keyed by node code)
- ``oncotree_semantic_review`` -> reviewer verdict (a queue)

Each bucket is a list whose last item repeats once exhausted, so a step that is
stable across repair rounds needs only one entry, while a step that changes
between rounds lists each round's payload in order.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


class ScriptedNavClient:
    def __init__(
        self,
        *,
        root: list[dict[str, Any]] | dict[str, Any] | None = None,
        nodes: Mapping[str, list[dict[str, Any]]] | None = None,
        reviews: list[dict[str, Any]] | None = None,
    ) -> None:
        if root is None:
            root_list: list[dict[str, Any]] = []
        elif isinstance(root, list):
            root_list = list(root)
        else:
            root_list = [root]
        self.root = root_list
        self.nodes = {code: list(payloads) for code, payloads in (nodes or {}).items()}
        self.reviews = list(reviews or [])
        self.calls: list[tuple[str, str]] = []

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_format: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        name = (response_format or {}).get("name", "")
        self.calls.append((name, user_prompt))

        if name == "oncotree_root_decision":
            return self._take(self.root, "root decision")
        if name == "oncotree_node_decision":
            code = self._node_code(user_prompt)
            bucket = self.nodes.get(code)
            if not bucket:
                raise AssertionError(f"No scripted node decision for node {code!r}.")
            return self._take(bucket, f"node decision for {code}")
        if name == "oncotree_semantic_review":
            return self._take(self.reviews, "semantic review")
        raise AssertionError(f"Unexpected response_format name {name!r}.")

    @staticmethod
    def _take(bucket: list[dict[str, Any]], label: str) -> dict[str, Any]:
        if not bucket:
            raise AssertionError(f"No scripted {label} queued.")
        return bucket.pop(0) if len(bucket) > 1 else bucket[0]

    @staticmethod
    def _node_code(prompt: str) -> str:
        match = re.search(r"Current node: (\S+) ", prompt)
        return match.group(1) if match else ""


# --- payload builders -------------------------------------------------------

def root_decision(scope: str, *, include_fallback: str = "none", **actions: str) -> dict[str, Any]:
    """root_decision('specific', LUNG='descend', BREAST='emit')."""
    return {
        "scope": scope,
        "include_fallback": include_fallback,
        "roots": [_decision(code, action) for code, action in actions.items()],
    }


def node_decision(stop_here: bool = False, **actions: str) -> dict[str, Any]:
    return {
        "stop_here": stop_here,
        "children": [_decision(code, action) for code, action in actions.items()],
    }


def review_ok(*reasoning: str) -> dict[str, Any]:
    return {
        "semantically_correct": True,
        "reasoning": list(reasoning) or ["Mapping is clinically entailed."],
        "findings": [],
    }


def review_reject(code: str, reason: str, suggested_action: str = "") -> dict[str, Any]:
    return {
        "semantically_correct": False,
        "reasoning": [reason],
        "findings": [
            {"code": code, "reason": reason, "suggested_action": suggested_action}
        ],
    }


def _decision(code: str, action: str) -> dict[str, Any]:
    return {
        "code": code,
        "action": action,
        "confidence": "high",
        "rationale": f"{action} {code}",
    }


# --- OpenAI-SDK-shaped fakes (for OpenAIReasoningClient wiring tests) --------

class FakeResponsesApi:
    """Stand-in for ``client.responses`` that records the create() kwargs."""

    def __init__(self, output_text: str = '{"ok": true}') -> None:
        self.output_text = output_text
        self.kwargs: dict[str, Any] | None = None

    def create(self, **kwargs: Any) -> dict[str, Any]:
        self.kwargs = kwargs
        return {"output_text": self.output_text}


class FakeOpenAIClient:
    def __init__(self) -> None:
        self.responses = FakeResponsesApi()
