from __future__ import annotations

import logging
import random
import re
import time
from typing import Any, Mapping

import openai

from trialcurator.llm_client import LlmClient
from trialcurator.utils import llm_json_check_and_repair

logger = logging.getLogger(__name__)

DEFAULT_DRUG_MODEL = "gpt-5-search-api"
DEFAULT_DRUG_TEMPERATURE = None
DEFAULT_DRUG_TOP_P = None
DEFAULT_DRUG_WEB_SEARCH_OPTIONS: Mapping[str, Any] = {}
DEFAULT_DRUG_MAX_RETRIES = 6
DEFAULT_DRUG_RETRY_INITIAL_DELAY_SECONDS = 1.0
DEFAULT_DRUG_RETRY_MAX_DELAY_SECONDS = 30.0

RETRY_HINT_RE = re.compile(r"try again in ([0-9.]+)\s*(ms|milliseconds|s|seconds)")


class DrugOpenaiClient:
    """OpenAI helper for drug-domain prompts.

    This class is intentionally schema-neutral. Callers own their task-specific
    prompt, response schema, validation, and post-processing.
    """

    MODEL = DEFAULT_DRUG_MODEL
    TEMPERATURE = DEFAULT_DRUG_TEMPERATURE
    TOP_P = DEFAULT_DRUG_TOP_P
    MAX_RETRIES = DEFAULT_DRUG_MAX_RETRIES
    RETRY_INITIAL_DELAY_SECONDS = DEFAULT_DRUG_RETRY_INITIAL_DELAY_SECONDS
    RETRY_MAX_DELAY_SECONDS = DEFAULT_DRUG_RETRY_MAX_DELAY_SECONDS

    def __init__(
        self,
        *,
        model: str | None = None,
        temperature: float | None = TEMPERATURE,
        top_p: float | None = TOP_P,
        enable_web_search: bool = True,
        web_search_options: Mapping[str, Any] | None = None,
        max_retries: int = MAX_RETRIES,
        retry_initial_delay_seconds: float = RETRY_INITIAL_DELAY_SECONDS,
        retry_max_delay_seconds: float = RETRY_MAX_DELAY_SECONDS,
        openai_client: LlmClient | None = None,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if retry_initial_delay_seconds <= 0:
            raise ValueError("retry_initial_delay_seconds must be positive")
        if retry_max_delay_seconds <= 0:
            raise ValueError("retry_max_delay_seconds must be positive")

        self.client = openai_client
        self.wrapped_client = None if openai_client else self._make_wrapped_client()
        self.model = model or self.MODEL
        self.temperature = temperature
        self.top_p = top_p
        self.web_search_options = (
            dict(web_search_options or DEFAULT_DRUG_WEB_SEARCH_OPTIONS)
            if enable_web_search
            else None
        )
        self.max_retries = max_retries
        self.retry_initial_delay_seconds = retry_initial_delay_seconds
        self.retry_max_delay_seconds = retry_max_delay_seconds

    @staticmethod
    def _make_wrapped_client():
        client_factory = getattr(openai, "OpenAI", None) or getattr(openai, "Client", None)
        if client_factory is None:
            return None
        return client_factory()

    def ask(
        self,
        *,
        user_prompt: str,
        system_prompt: str | None = None,
    ) -> str:
        return self.llm_ask(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
        )

    def llm_ask(self, user_prompt: str, system_prompt: str | None = None) -> str:
        if self.client is not None:
            return self.client.llm_ask(
                user_prompt=user_prompt,
                system_prompt=system_prompt,
            )

        messages = []
        if system_prompt is not None:
            messages.append({"role": "system", "content": system_prompt})
            for line in system_prompt.splitlines():
                logger.info("drug system prompt: %s", line)

        messages.append({"role": "user", "content": user_prompt})
        for line in user_prompt.splitlines():
            logger.info("drug prompt: %s", line)

        completion = self._create_chat_completion_with_retries(messages)
        response = self._completion_text(completion)
        for line in response.splitlines():
            logger.info("drug response: %s", line)

        return response

    def _create_chat_completion_with_retries(self, messages: list[dict[str, str]]):
        attempt = 0
        while True:
            try:
                return self._create_chat_completion(messages)
            except Exception as error:
                if attempt >= self.max_retries or not is_retryable_openai_error(error):
                    raise
                delay_seconds = retry_delay_seconds(
                    error,
                    attempt=attempt,
                    initial_delay_seconds=self.retry_initial_delay_seconds,
                    max_delay_seconds=self.retry_max_delay_seconds,
                )
                logger.warning(
                    "OpenAI drug request failed with retryable error on attempt "
                    "%d/%d; sleeping %.2fs before retry: %s",
                    attempt + 1,
                    self.max_retries,
                    delay_seconds,
                    error,
                )
                time.sleep(delay_seconds)
                attempt += 1

    def _create_chat_completion(self, messages: list[dict[str, str]]):
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self.top_p is not None:
            kwargs["top_p"] = self.top_p
        if self.web_search_options is not None:
            kwargs["web_search_options"] = self.web_search_options

        if self.wrapped_client is not None:
            return self.wrapped_client.chat.completions.create(**kwargs)

        return openai.ChatCompletion.create(**kwargs)

    @staticmethod
    def _completion_text(completion):
        try:
            message = completion.choices[0].message
        except AttributeError:
            message = completion["choices"][0]["message"]

        if isinstance(message, dict):
            return message["content"]

        return message.content

    def ask_json(
        self,
        *,
        user_prompt: str,
        system_prompt: str | None = None,
    ) -> Mapping[str, Any]:
        response = self.ask(user_prompt=user_prompt, system_prompt=system_prompt)
        parsed = llm_json_check_and_repair(response, self.client or self)
        if not isinstance(parsed, Mapping):
            raise ValueError(f"Drug LLM did not return a JSON object: {parsed!r}")
        return parsed


def is_retryable_openai_error(error: Exception) -> bool:
    error_name = error.__class__.__name__.lower()
    message = str(error).lower()
    retryable_names = (
        "ratelimit",
        "timeout",
        "apiconnection",
        "serviceunavailable",
        "apierror",
    )
    return (
        any(name in error_name for name in retryable_names)
        or "rate limit" in message
        or "try again" in message
        or "temporarily unavailable" in message
    )


def retry_delay_seconds(
    error: Exception,
    *,
    attempt: int,
    initial_delay_seconds: float,
    max_delay_seconds: float,
) -> float:
    retry_hint_seconds = parse_retry_hint_seconds(str(error))
    if retry_hint_seconds is not None:
        return min(max_delay_seconds, retry_hint_seconds + random.uniform(0.0, 0.25))

    exponential_delay = min(max_delay_seconds, initial_delay_seconds * (2**attempt))
    return min(max_delay_seconds, exponential_delay + random.uniform(0.0, 0.5))


def parse_retry_hint_seconds(message: str) -> float | None:
    match = RETRY_HINT_RE.search(message)
    if not match:
        return None

    value = float(match.group(1))
    unit = match.group(2).lower()
    if unit in {"ms", "milliseconds"}:
        return value / 1000
    return value
