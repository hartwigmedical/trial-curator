"""Unified LLM client for the v2 agentic pipeline (spec §4).

The single door to the LLM: give it a pydantic output schema and a prompt, get
back a *validated* object. It also owns reliability (retries + backoff), a
response cache, and per-call tracing.

LLM calls go through the Fireworks AI Python SDK (``fireworks-ai``). The
`research()` path (web_search agents) uses Parallel AI for live web search
and then feeds the results to the LLM.

Design notes
------------
- Model choice is configuration, never hard-coded in a task/agent.
- ``fireworks.client`` is imported lazily, so this module — and unit tests that
  inject a fake client — import fine without the SDK installed.
- Determinism: the response **cache** is the primary deterministic layer
  (identical request -> identical output, even across process runs with a
  DiskCache). `temperature`/`seed` are optional and *omitted by default*, because
  many models reject `temperature`; set them per-agent only for models that support them.
- API key: FIREWORKS_API_KEY (LLM) + PARALLEL_API_KEY (web search), auto-loaded
  from .env / .env.local by the entry points.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Generic, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

# Fireworks AI API config
DEFAULT_MODEL = "accounts/fireworks/models/deepseek-v4-flash-0731"

# Parallel AI web search config
PARALLEL_BASE_URL = "https://api.parallel.ai/v1/search"

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
    "prompt_sha",
    "read_cache_meta",
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
# Each cache entry records not just the response but the PROVENANCE of the prompt
# that produced it — the agent name and a `prompt_sha` (sha256 of the agent's
# instructions). This is what lets `core/cache_prune` garbage-collect entries from
# OUTDATED prompts (the prompt changed, so the old response is dead weight). The on-
# disk format is a self-describing envelope; legacy bare-JSON files (pre-envelope)
# still read fine and are treated as unknown-provenance (kept unless purged).
_ENVELOPE_MARKER = "__agentic_cache_v1__"


def prompt_sha(instructions: str) -> str:
    """Stable identity of a prompt: sha256 of the agent's `instructions` text."""
    return hashlib.sha256(instructions.encode("utf-8")).hexdigest()


def _wrap_entry(response: str, meta: dict[str, Any] | None) -> str:
    return json.dumps({_ENVELOPE_MARKER: 1, "meta": meta or {}, "response": response})


def _unwrap_entry(text: str) -> tuple[str, dict[str, Any] | None]:
    """Return (response_text, meta). A legacy bare-JSON file -> (text, None)."""
    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        return text, None
    if isinstance(obj, dict) and _ENVELOPE_MARKER in obj:
        return obj.get("response", ""), (obj.get("meta") or {})
    return text, None


def read_cache_meta(path: str | Path) -> dict[str, Any] | None:
    """Read just the provenance meta of a cache file (None if legacy/untagged/unreadable)."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    return _unwrap_entry(text)[1]


class ResponseCache(Protocol):
    """Maps a request fingerprint -> the raw JSON output text (+ optional provenance meta)."""

    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str, meta: dict[str, Any] | None = None) -> None: ...


class InMemoryCache:
    """Process-local cache (fast; does not survive restarts). Meta is accepted but not used."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._store.get(key)

    def set(self, key: str, value: str, meta: dict[str, Any] | None = None) -> None:
        self._store[key] = value


class DiskCache:
    """JSON-file cache under `cache_dir`; gives run-to-run determinism.

    Entries are stored as a provenance envelope `{marker, meta, response}`; `get`
    transparently reads both the envelope and legacy bare-JSON files, so upgrading
    the format never orphans the (costly) responses already on disk.
    """

    def __init__(self, cache_dir: str | Path) -> None:
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self._dir / f"{key}.json"

    def get(self, key: str) -> str | None:
        path = self._path(key)
        if not path.exists():
            return None
        return _unwrap_entry(path.read_text(encoding="utf-8"))[0]

    def set(self, key: str, value: str, meta: dict[str, Any] | None = None) -> None:
        self._path(key).write_text(_wrap_entry(value, meta), encoding="utf-8")


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
    """Reliable, cached, schema-validated wrapper over Fireworks AI chat completions."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        fireworks_client: Any | None = None,
        cache: ResponseCache | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        initial_delay: float = DEFAULT_INITIAL_DELAY,
        max_delay: float = DEFAULT_MAX_DELAY,
        max_concurrency: int | None = None,
        trace: Callable[[dict[str, Any]], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.model = model
        self._client = fireworks_client
        self.cache: ResponseCache = cache if cache is not None else InMemoryCache()
        self.max_retries = max_retries
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self._trace = trace
        self._sleep = sleep
        # A GLOBAL cap on concurrent LLM API calls across ALL callers (trials × their reviewer fan-outs share this
        # one client). Trial-level `--workers` × the per-trial fan_out would otherwise multiply into an
        # unpredictable request burst with no ceiling; this semaphore governs the TRUE concurrency deterministically
        # at the account's rate-limit ceiling. Cache HITS never take a slot (no network); only live calls do.
        self._api_sema = threading.Semaphore(max_concurrency) if (max_concurrency and max_concurrency > 0) else None

    def _api_slot(self):
        """Context manager: acquire a global API slot (no-op when no cap is configured)."""
        return self._api_sema if self._api_sema is not None else nullcontext()

    def _entry_meta(self, *, agent_name: str | None, instructions: str, model: str, mode: str) -> dict[str, Any]:
        """Provenance stored alongside a cached response (see cache_prune)."""
        return {
            "name": agent_name,
            "prompt_sha": prompt_sha(instructions),
            "model": model,
            "mode": mode,
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

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
        agent_name: str | None = None,
    ) -> LlmResult[T]:
        """Call the model and return a validated `output_schema` instance.

        `instructions` is the system/role prompt; `user_input` is the content.
        Identical requests are served from the cache (the determinism guarantee).
        `agent_name` is recorded as cache provenance (for prompt-aware pruning).
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
        with self._api_slot():   # global concurrency cap (cache hits above never reach here)
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
        self.cache.set(key, raw_text, self._entry_meta(
            agent_name=agent_name, instructions=instructions, model=model, mode="parse"))
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
        agent_name: str | None = None,
    ) -> LlmResult[T]:
        """Like parse(), but augments the prompt with live web search results.

        Uses Parallel AI for web search, then feeds the results into the LLM
        (an ordinary chat-completions call with JSON-schema response_format).
        For research tasks (e.g. TGA/PBS regulatory status) that need live web lookups.
        Cached on (mode, model, instructions, input, schema) for run-to-run reproducibility.
        `agent_name` is recorded as cache provenance (for prompt-aware pruning).
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
        with self._api_slot():   # global concurrency cap (cache hits above never reach here)
            parsed, raw_text, usage, attempts = self._research_with_retries(
                output_schema=output_schema, model=model, instructions=instructions,
                user_input=user_input, max_completion_tokens=max_completion_tokens,
            )
        latency_ms = (time.monotonic() - start) * 1000
        self.cache.set(key, raw_text, self._entry_meta(
            agent_name=agent_name, instructions=instructions, model=model, mode="research:web_search"))
        result = LlmResult(parsed, model, raw_text, cache_hit=False, attempts=attempts, usage=usage)
        self._emit_trace(key, result, latency_ms=latency_ms)
        return result

    def _research_with_retries(
        self, *, output_schema: type[T], model: str, instructions: str,
        user_input: str, max_completion_tokens: int | None,
    ) -> tuple[T, str, dict[str, Any] | None, int]:
        """Do web search via Parallel AI, then call the LLM with search results as context.

        Search failures are non-fatal: the LLM is called without search results,
        falling back to its training knowledge.
        """
        try:
            search_results = self._parallel_search(user_input)
            augmented_instructions = f"{instructions}\n\n**Web Search Results:**\n{search_results}"
        except Exception as exc:
            logger.warning("Web search failed (proceeding without search): %s", exc)
            augmented_instructions = instructions
        return self._call_with_retries(
            output_schema=output_schema, model=model,
            instructions=augmented_instructions, user_input=user_input,
            temperature=None, seed=None,
            max_completion_tokens=max_completion_tokens,
        )

    def _parallel_search(self, query: str) -> str:
        """Perform web search via Parallel AI API. Returns formatted search results text."""
        api_key = os.environ.get("PARALLEL_API_KEY")
        if not api_key:
            raise LlmError(
                "PARALLEL_API_KEY not set for web search (research path). "
                "Set it in your environment or .env / .env.local."
            )
        base_url = os.environ.get("PARALLEL_BASE_URL", PARALLEL_BASE_URL)

        try:
            import httpx
        except ImportError as err:
            raise ImportError(
                "The `httpx` package is required for web search (`pip install httpx`)."
            ) from err

        payload: dict[str, Any] = {"search_queries": [query]}
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        logger.debug("parallel search query=%s", query[:120])
        response = httpx.post(base_url, headers=headers, json=payload, timeout=30.0)
        response.raise_for_status()
        data = response.json()

        results = data.get("results", [])
        if not results:
            return "(no search results found)"

        formatted: list[str] = []
        for i, r in enumerate(results[:8], 1):
            url = r.get("url", "")
            title = r.get("title", "")
            excerpts = r.get("excerpts", [])
            snippet = excerpts[0] if isinstance(excerpts, list) and excerpts else ""
            parts = [f"[{i}] {title}"] if title else [f"[{i}]"]
            if snippet:
                parts.append(f"   {snippet[:500]}")
            if url:
                parts.append(f"   Source: {url}")
            formatted.append("\n".join(parts))
        return "\n\n".join(formatted) if formatted else "(no search results found)"

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
        """Call the LLM with JSON-schema response_format and parse the response manually.

        Uses ``chat.completions.create()`` (NOT ``.parse()``) so it works with any
        OpenAI-compatible API (e.g. Fireworks AI). The response content is validated
        against the Pydantic schema after the call.
        """
        client = self._ensure_client()
        transient = _transient_errors()
        json_schema = output_schema.model_json_schema()
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": _messages(instructions, user_input),
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": output_schema.__name__,
                    "schema": json_schema,
                    "strict": True,
                },
            },
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
                response = client.chat.completions.create(**kwargs)
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
            content = message.content
            if not content:
                raise LlmParseError(
                    f"No parsable output for {output_schema.__name__}: empty response"
                )
            try:
                parsed = output_schema.model_validate_json(content)
            except ValidationError as err:
                raise LlmParseError(
                    f"Response failed Pydantic validation for {output_schema.__name__}: {err}"
                ) from err
            return parsed, content, _usage_dict(response), attempt

        raise LlmError(f"LLM call failed after {self.max_retries} attempts") from last_err

    def _ensure_client(self) -> Any:
        if self._client is None:
            try:
                from fireworks.client import Fireworks
            except ImportError as err:  # pragma: no cover - env-dependent
                raise ImportError(
                    "The `fireworks-ai` package is required to call the API (`pip install fireworks-ai`)."
                ) from err
            api_key = os.environ.get("FIREWORKS_API_KEY")
            if not api_key:
                raise LlmError(
                    "FIREWORKS_API_KEY not set. Set it in your environment or .env / .env.local "
                    "(or pass a pre-configured `fireworks_client` to the constructor)."
                )
            base_url = os.environ.get(
                "FIREWORKS_BASE_URL", "https://api.fireworks.ai/inference/v1"
            )
            self._client = Fireworks(api_key=api_key, base_url=base_url)
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


def _fingerprint(request: dict[str, Any]) -> str:
    blob = json.dumps(request, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _transient_errors() -> tuple[type[BaseException], ...]:
    try:
        from fireworks.client import error as fwe
    except ImportError:  # pragma: no cover - env-dependent
        return ()
    candidates = (
        getattr(fwe, "RateLimitError", None),
        getattr(fwe, "APITimeoutError", None),
        getattr(fwe, "BadGatewayError", None),
        getattr(fwe, "InternalServerError", None),
        getattr(fwe, "ServiceUnavailableError", None),
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
