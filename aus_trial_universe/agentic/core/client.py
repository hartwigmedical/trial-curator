"""Unified OpenAI client for the v2 agentic pipeline (spec §4).

The single door to the LLM: give it a pydantic output schema and a prompt, get
back a *validated* object. It also owns reliability (retries + backoff), a
response cache, and per-call tracing.

Structured outputs go through the Chat Completions parse helper
(`client.chat.completions.parse`, falling back to `client.beta.chat.completions.parse`
on older SDKs). Targets openai >= 2.x, where `chat.completions.parse` is stable. The
Responses API is also available at 2.x, but Chat Completions parse is proven and
sufficient here, so the client stays on it.

Design notes
------------
- Model choice is configuration, never hard-coded in a task/agent.
- `openai` is imported lazily, so this module — and unit tests that inject a fake
  client — import fine without the SDK installed.
- Determinism: the response **cache** is the primary deterministic layer
  (identical request -> identical output, even across process runs with a
  DiskCache). `temperature`/`seed` are optional and *omitted by default*, because
  current reasoning models (e.g. gpt-5.x) reject `temperature`; set them per-agent
  only for models that support them.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Generic, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

# Matches current repo usage; override per-agent. Kept here as the single default.
DEFAULT_MODEL = "gpt-5.5"
DEFAULT_MAX_RETRIES = 6
DEFAULT_INITIAL_DELAY = 2.0
DEFAULT_MAX_DELAY = 60.0

T = TypeVar("T", bound=BaseModel)

__all__ = [
    "LlmClient",
    "LlmResult",
    "LlmError",
    "LlmParseError",
    "ResponseCache",
    "InMemoryCache",
    "DiskCache",
    "DEFAULT_MODEL",
]


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class LlmError(RuntimeError):
    """Base error for LLM calls that could not be completed."""


class LlmParseError(LlmError):
    """The model returned output that could not be parsed into the schema."""


# --------------------------------------------------------------------------- #
# Response cache
# --------------------------------------------------------------------------- #
class ResponseCache(Protocol):
    """Maps a request fingerprint -> the raw JSON output text."""

    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str) -> None: ...


class InMemoryCache:
    """Process-local cache (fast; does not survive restarts)."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._store.get(key)

    def set(self, key: str, value: str) -> None:
        self._store[key] = value


class DiskCache:
    """JSON-file cache under `cache_dir`; gives run-to-run determinism."""

    def __init__(self, cache_dir: str | Path) -> None:
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self._dir / f"{key}.json"

    def get(self, key: str) -> str | None:
        path = self._path(key)
        return path.read_text(encoding="utf-8") if path.exists() else None

    def set(self, key: str, value: str) -> None:
        self._path(key).write_text(value, encoding="utf-8")


# --------------------------------------------------------------------------- #
# Result
# --------------------------------------------------------------------------- #
@dataclass
class LlmResult(Generic[T]):
    """A validated LLM response plus call metadata."""

    parsed: T
    model: str
    raw_text: str
    cache_hit: bool
    attempts: int
    usage: dict[str, Any] | None = None


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
class LlmClient:
    """Reliable, cached, schema-validated wrapper over OpenAI chat-completions parse."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        openai_client: Any | None = None,
        cache: ResponseCache | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        initial_delay: float = DEFAULT_INITIAL_DELAY,
        max_delay: float = DEFAULT_MAX_DELAY,
        trace: Callable[[dict[str, Any]], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.model = model
        self._client = openai_client
        self.cache: ResponseCache = cache if cache is not None else InMemoryCache()
        self.max_retries = max_retries
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self._trace = trace
        self._sleep = sleep

    def parse(
        self,
        output_schema: type[T],
        *,
        instructions: str,
        user_input: str,
        model: str | None = None,
        temperature: float | None = None,
        seed: int | None = None,
        max_completion_tokens: int | None = None,
    ) -> LlmResult[T]:
        """Call the model and return a validated `output_schema` instance.

        `instructions` is the system/role prompt; `user_input` is the content.
        Identical requests are served from the cache (the determinism guarantee).
        """
        model = model or self.model
        key = _fingerprint(
            {
                "model": model,
                "instructions": instructions,
                "input": user_input,
                "schema": output_schema.model_json_schema(),
                "temperature": temperature,
                "seed": seed,
                "max_completion_tokens": max_completion_tokens,
            }
        )

        cached = self.cache.get(key)
        if cached is not None:
            try:
                parsed = output_schema.model_validate_json(cached)
                result = LlmResult(parsed, model, cached, cache_hit=True, attempts=0)
                self._emit_trace(key, result, latency_ms=0.0)
                return result
            except ValidationError:
                logger.warning("Cached response %s failed validation; recomputing", key[:12])

        start = time.monotonic()
        parsed, raw_text, usage, attempts = self._call_with_retries(
            output_schema=output_schema,
            model=model,
            instructions=instructions,
            user_input=user_input,
            temperature=temperature,
            seed=seed,
            max_completion_tokens=max_completion_tokens,
        )
        latency_ms = (time.monotonic() - start) * 1000
        self.cache.set(key, raw_text)
        result = LlmResult(parsed, model, raw_text, cache_hit=False, attempts=attempts, usage=usage)
        self._emit_trace(key, result, latency_ms=latency_ms)
        return result

    def research(
        self,
        output_schema: type[T],
        *,
        instructions: str,
        user_input: str,
        model: str | None = None,
        max_completion_tokens: int | None = None,
    ) -> LlmResult[T]:
        """Like parse(), but answers via the Responses API with the web_search tool.

        For research tasks (e.g. TGA/PBS regulatory status) that need live web lookups.
        Cached on (mode, model, instructions, input, schema) for run-to-run reproducibility.
        """
        model = model or self.model
        key = _fingerprint(
            {
                "mode": "research:web_search",
                "model": model,
                "instructions": instructions,
                "input": user_input,
                "schema": output_schema.model_json_schema(),
                "max_completion_tokens": max_completion_tokens,
            }
        )
        cached = self.cache.get(key)
        if cached is not None:
            try:
                parsed = output_schema.model_validate_json(cached)
                result = LlmResult(parsed, model, cached, cache_hit=True, attempts=0)
                self._emit_trace(key, result, latency_ms=0.0)
                return result
            except ValidationError:
                logger.warning("Cached research %s failed validation; recomputing", key[:12])

        start = time.monotonic()
        parsed, raw_text, usage, attempts = self._research_with_retries(
            output_schema=output_schema, model=model, instructions=instructions,
            user_input=user_input, max_completion_tokens=max_completion_tokens,
        )
        latency_ms = (time.monotonic() - start) * 1000
        self.cache.set(key, raw_text)
        result = LlmResult(parsed, model, raw_text, cache_hit=False, attempts=attempts, usage=usage)
        self._emit_trace(key, result, latency_ms=latency_ms)
        return result

    def _research_with_retries(
        self, *, output_schema: type[T], model: str, instructions: str,
        user_input: str, max_completion_tokens: int | None,
    ) -> tuple[T, str, dict[str, Any] | None, int]:
        client = self._ensure_client()
        transient = _transient_errors()
        kwargs: dict[str, Any] = {
            "model": model,
            "instructions": instructions,
            "input": user_input,
            "tools": [{"type": "web_search"}],
            "text_format": output_schema,
        }
        if max_completion_tokens is not None:
            kwargs["max_output_tokens"] = max_completion_tokens

        delay = self.initial_delay
        last_err: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = client.responses.parse(**kwargs)
            except transient as err:  # rate limit / timeout / connection / 5xx
                last_err = err
                logger.warning("Transient research error (attempt %d/%d): %s", attempt, self.max_retries, err)
                if attempt < self.max_retries:
                    self._sleep(min(delay, self.max_delay))
                    delay *= 2
                continue
            parsed = getattr(response, "output_parsed", None)
            if parsed is None:
                raw = getattr(response, "output_text", "") or ""
                raise LlmParseError(f"No parsable research output for {output_schema.__name__}: {raw[:200]!r}")
            return parsed, parsed.model_dump_json(), _usage_dict(response), attempt
        raise LlmError(f"Research call failed after {self.max_retries} attempts") from last_err

    def _call_with_retries(
        self,
        *,
        output_schema: type[T],
        model: str,
        instructions: str,
        user_input: str,
        temperature: float | None,
        seed: int | None,
        max_completion_tokens: int | None,
    ) -> tuple[T, str, dict[str, Any] | None, int]:
        client = self._ensure_client()
        parse_fn = _resolve_parse(client)
        transient = _transient_errors()
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": _messages(instructions, user_input),
            "response_format": output_schema,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        if seed is not None:
            kwargs["seed"] = seed
        if max_completion_tokens is not None:
            kwargs["max_completion_tokens"] = max_completion_tokens

        delay = self.initial_delay
        last_err: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = parse_fn(**kwargs)
            except transient as err:  # rate limit / timeout / connection / 5xx
                last_err = err
                logger.warning(
                    "Transient LLM error (attempt %d/%d): %s", attempt, self.max_retries, err
                )
                if attempt < self.max_retries:
                    self._sleep(min(delay, self.max_delay))
                    delay *= 2
                continue

            message = response.choices[0].message
            refusal = getattr(message, "refusal", None)
            if refusal:
                raise LlmParseError(f"Model refused for {output_schema.__name__}: {refusal}")
            parsed = getattr(message, "parsed", None)
            if parsed is None:
                raw = getattr(message, "content", "") or ""
                raise LlmParseError(
                    f"No parsable output for {output_schema.__name__}: {raw[:200]!r}"
                )
            raw_text = getattr(message, "content", None) or parsed.model_dump_json()
            return parsed, raw_text, _usage_dict(response), attempt

        raise LlmError(f"LLM call failed after {self.max_retries} attempts") from last_err

    def _ensure_client(self) -> Any:
        if self._client is None:
            try:
                import openai
            except ImportError as err:  # pragma: no cover - env-dependent
                raise ImportError(
                    "The `openai` package is required to call the API (`pip install openai`)."
                ) from err
            self._client = openai.OpenAI()
        return self._client

    def _emit_trace(self, key: str, result: LlmResult[Any], *, latency_ms: float) -> None:
        # Deliberately logs sizes/metadata, not prompt content (leaner + data-conscious).
        logger.debug(
            "llm call | model=%s cache_hit=%s attempts=%d latency_ms=%.0f usage=%s",
            result.model,
            result.cache_hit,
            result.attempts,
            latency_ms,
            result.usage,
        )
        if self._trace is not None:
            self._trace(
                {
                    "cache_key": key[:12],
                    "model": result.model,
                    "cache_hit": result.cache_hit,
                    "attempts": result.attempts,
                    "latency_ms": round(latency_ms, 1),
                    "usage": result.usage,
                }
            )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _messages(instructions: str, user_input: str) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if instructions:
        messages.append({"role": "system", "content": instructions})
    messages.append({"role": "user", "content": user_input})
    return messages


def _resolve_parse(client: Any) -> Callable[..., Any]:
    """Return the chat-completions parse helper, preferring stable over beta."""
    chat = getattr(client, "chat", None)
    stable = getattr(getattr(chat, "completions", None), "parse", None)
    if callable(stable):
        return stable
    beta = getattr(client, "beta", None)
    beta_chat = getattr(beta, "chat", None)
    beta_parse = getattr(getattr(beta_chat, "completions", None), "parse", None)
    if callable(beta_parse):
        return beta_parse
    raise LlmError(
        "The installed openai SDK exposes no chat.completions.parse "
        "(need openai>=2.0 for structured outputs)."
    )


def _fingerprint(request: dict[str, Any]) -> str:
    blob = json.dumps(request, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _transient_errors() -> tuple[type[BaseException], ...]:
    try:
        import openai
    except ImportError:  # pragma: no cover - env-dependent
        return ()
    candidates = (
        getattr(openai, "RateLimitError", None),
        getattr(openai, "APITimeoutError", None),
        getattr(openai, "APIConnectionError", None),
        getattr(openai, "InternalServerError", None),
    )
    return tuple(c for c in candidates if isinstance(c, type))


def _usage_dict(response: Any) -> dict[str, Any] | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    if hasattr(usage, "model_dump"):
        return usage.model_dump()
    if isinstance(usage, dict):
        return usage
    return {
        field: getattr(usage, field)
        for field in ("prompt_tokens", "completion_tokens", "total_tokens")
        if hasattr(usage, field)
    }
