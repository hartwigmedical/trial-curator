"""Tests for Agent (aus_trial_universe/agentic/core/agent.py) using a fake client."""
from __future__ import annotations

from pydantic import BaseModel

from aus_trial_universe.core.agent import Agent
from aus_trial_universe.core.client import LlmResult


class _Out(BaseModel):
    value: str


class _FakeClient:
    """Duck-typed stand-in for LlmClient.parse; records each call's kwargs."""

    def __init__(self, parsed):
        self._parsed = parsed
        self.calls: list[dict] = []

    def parse(self, output_schema, *, instructions, user_input, model=None,
              temperature=None, seed=None, max_completion_tokens=None, **_):
        self.calls.append(
            {
                "mode": "parse",
                "schema": output_schema,
                "instructions": instructions,
                "user_input": user_input,
                "model": model,
                "temperature": temperature,
                "seed": seed,
            }
        )
        return LlmResult(self._parsed, model or "fake", "{}", cache_hit=False, attempts=1)

    def research(self, output_schema, *, instructions, user_input, model=None, max_completion_tokens=None, **_):
        self.calls.append({"mode": "research", "schema": output_schema, "user_input": user_input, "model": model})
        return LlmResult(self._parsed, model or "fake", "{}", cache_hit=False, attempts=1)


def _agent(client, **kw):
    return Agent(name="t", instructions="sys", output_schema=_Out, client=client, **kw)


def test_call_returns_parsed_object():
    out = _Out(value="x")
    client = _FakeClient(out)
    agent = _agent(client)
    assert agent("hello") == out
    assert client.calls[0]["user_input"] == "hello"
    assert client.calls[0]["instructions"] == "sys"
    assert client.calls[0]["schema"] is _Out


def test_run_returns_full_result():
    out = _Out(value="x")
    result = _agent(_FakeClient(out)).run("hi")
    assert result.parsed == out
    assert result.attempts == 1


def test_render_input_used_for_non_string():
    client = _FakeClient(_Out(value="x"))
    agent = _agent(client, render_input=lambda d: d["text"])
    agent({"text": "rendered!"})
    assert client.calls[0]["user_input"] == "rendered!"


def test_model_and_params_forwarded():
    client = _FakeClient(_Out(value="x"))
    _agent(client, model="gpt-x", temperature=0.0, seed=7)("hi")
    call = client.calls[0]
    assert call["mode"] == "parse" and call["model"] == "gpt-x" and call["temperature"] == 0.0 and call["seed"] == 7


def test_web_search_routes_to_research():
    client = _FakeClient(_Out(value="x"))
    _agent(client, web_search=True)("hi")
    assert client.calls[0]["mode"] == "research"  # web_search agents use client.research(), not parse()


def test_default_agent_uses_parse():
    client = _FakeClient(_Out(value="x"))
    _agent(client)("hi")
    assert client.calls[0]["mode"] == "parse"
