"""Tests for the unified LLM client (aus_trial_universe/agentic/core/client.py).

These inject a fake OpenAI client (dependency injection) so they run without an
API key or network. They cover the parse/extract path, caching, and errors.
Retry/backoff behaviour is exercised separately once error construction helpers land.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from aus_trial_universe.agentic.core.client import (
    DiskCache,
    LlmClient,
    LlmParseError,
)


class _Out(BaseModel):
    label: str
    score: int


def _fake_response(parsed, *, content=None, refusal=None, usage=None):
    message = SimpleNamespace(parsed=parsed, content=content, refusal=refusal)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


class _FakeOpenAI:
    """Minimal stand-in exposing chat.completions.parse and counting calls."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(parse=self._parse))

    def _parse(self, **kwargs):
        self.calls += 1
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_parse_returns_validated_object():
    out = _Out(label="EGFR", score=3)
    fake = _FakeOpenAI([_fake_response(out, content=out.model_dump_json())])
    result = LlmClient(openai_client=fake).parse(_Out, instructions="sys", user_input="hi")
    assert result.parsed == out
    assert result.cache_hit is False
    assert result.attempts == 1
    assert fake.calls == 1


def test_identical_request_is_cached():
    out = _Out(label="EGFR", score=3)
    fake = _FakeOpenAI([_fake_response(out, content=out.model_dump_json())])
    client = LlmClient(openai_client=fake)
    first = client.parse(_Out, instructions="sys", user_input="hi")
    second = client.parse(_Out, instructions="sys", user_input="hi")
    assert first.cache_hit is False and second.cache_hit is True
    assert second.parsed == out
    assert fake.calls == 1  # second served from cache, no extra API call


def test_disk_cache_gives_cross_instance_hits(tmp_path):
    out = _Out(label="MSI", score=1)
    cache = DiskCache(tmp_path)
    fake = _FakeOpenAI([_fake_response(out, content=out.model_dump_json())])
    LlmClient(openai_client=fake, cache=cache).parse(_Out, instructions="s", user_input="u")
    # A brand-new client sharing the disk cache must not call the API again.
    fake2 = _FakeOpenAI([])  # empty -> would IndexError if the API were called
    result = LlmClient(openai_client=fake2, cache=cache).parse(_Out, instructions="s", user_input="u")
    assert result.cache_hit is True and result.parsed == out
    assert fake2.calls == 0


class _FakeResearchOpenAI:
    """Stand-in exposing responses.parse (Responses API + web_search path)."""

    def __init__(self, outputs):
        self._outputs = list(outputs)
        self.calls = 0
        self.responses = SimpleNamespace(parse=self._parse)

    def _parse(self, **kwargs):
        self.calls += 1
        item = self._outputs.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _fake_research_response(parsed, *, usage=None):
    return SimpleNamespace(output_parsed=parsed, output_text=parsed.model_dump_json(), usage=usage)


def test_research_returns_validated_object():
    out = _Out(label="Approved (2015)", score=1)
    fake = _FakeResearchOpenAI([_fake_research_response(out)])
    result = LlmClient(openai_client=fake).research(_Out, instructions="sys", user_input="is X TGA-approved?")
    assert result.parsed == out and result.cache_hit is False and result.attempts == 1
    assert fake.calls == 1


def test_research_is_cached():
    out = _Out(label="Approved (2015)", score=1)
    fake = _FakeResearchOpenAI([_fake_research_response(out)])
    client = LlmClient(openai_client=fake)
    first = client.research(_Out, instructions="s", user_input="u")
    second = client.research(_Out, instructions="s", user_input="u")
    assert first.cache_hit is False and second.cache_hit is True and fake.calls == 1


def test_research_missing_parsed_raises():
    fake = _FakeResearchOpenAI([SimpleNamespace(output_parsed=None, output_text="", usage=None)])
    with pytest.raises(LlmParseError):
        LlmClient(openai_client=fake).research(_Out, instructions="s", user_input="u")


def test_refusal_raises():
    fake = _FakeOpenAI([_fake_response(None, refusal="cannot help")])
    with pytest.raises(LlmParseError):
        LlmClient(openai_client=fake).parse(_Out, instructions="s", user_input="u")


def test_missing_parsed_raises():
    fake = _FakeOpenAI([_fake_response(None, content="{}")])
    with pytest.raises(LlmParseError):
        LlmClient(openai_client=fake).parse(_Out, instructions="s", user_input="u")
