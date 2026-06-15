from __future__ import annotations

import json
import logging
from typing import Any, Mapping

from aus_trial_universe.trial_drug_curation.llm.structured_outputs import (
    TRIAL_DRUG_CURATION_CHAT_RESPONSE_FORMAT,
    TRIAL_DRUG_CURATION_RESPONSE_FORMAT,
    TrialDrugCurationTable,
    table_from_payload,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-5.5"


class TrialDrugCurationClient:
    """Small wrapper around OpenAI APIs for trial drug curation."""

    MODEL = DEFAULT_MODEL

    def __init__(
        self,
        *,
        model: str = MODEL,
        openai_client: Any | None = None,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
        store: bool | None = None,
    ) -> None:
        self.model = model
        self.wrapped_client = openai_client if openai_client is not None else self._make_client()
        self.max_output_tokens = max_output_tokens
        self.temperature = temperature
        self.store = store

    @staticmethod
    def _make_client() -> Any:
        try:
            import openai
        except ImportError as error:
            raise ImportError(
                "The `openai` package is required to call the OpenAI API. "
                "Install it in the active environment before running trial drug "
                "curation."
            ) from error

        client_factory = getattr(openai, "OpenAI", None) or getattr(openai, "Client", None)
        if client_factory is None:
            raise RuntimeError("The installed `openai` package does not expose OpenAI().")
        return client_factory()

    def curate(
        self,
        *,
        user_prompt: str,
        system_prompt: str | None = None,
    ) -> TrialDrugCurationTable:
        """Run curation and return a structured tabular result."""
        response = self.create_response(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
        )
        return self.parse_response(response)

    def curate_tsv(
        self,
        *,
        user_prompt: str,
        system_prompt: str | None = None,
    ) -> str:
        """Run curation and return the result as TSV text."""
        return self.curate(user_prompt=user_prompt, system_prompt=system_prompt).to_tsv()

    def create_response(
        self,
        *,
        user_prompt: str,
        system_prompt: str | None = None,
    ) -> Any:
        if hasattr(self.wrapped_client, "responses"):
            return self._create_responses_api_response(
                user_prompt=user_prompt,
                system_prompt=system_prompt,
            )

        chat = getattr(self.wrapped_client, "chat", None)
        completions = getattr(chat, "completions", None)
        if completions is not None:
            return self._create_chat_completion(
                user_prompt=user_prompt,
                system_prompt=system_prompt,
            )

        raise RuntimeError(
            "The installed OpenAI client exposes neither `responses` nor "
            "`chat.completions`. Upgrade the `openai` package or install a "
            "compatible version."
        )

    def _create_responses_api_response(
        self,
        *,
        user_prompt: str,
        system_prompt: str | None = None,
    ) -> Any:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "input": self._build_input(user_prompt, system_prompt),
            "text": {"format": TRIAL_DRUG_CURATION_RESPONSE_FORMAT},
        }
        if self.max_output_tokens is not None:
            kwargs["max_output_tokens"] = self.max_output_tokens
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self.store is not None:
            kwargs["store"] = self.store

        logger.info("Creating trial drug curation response with model %s", self.model)
        return self.wrapped_client.responses.create(**kwargs)

    def _create_chat_completion(
        self,
        *,
        user_prompt: str,
        system_prompt: str | None = None,
    ) -> Any:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": self._build_messages(user_prompt, system_prompt),
            "response_format": TRIAL_DRUG_CURATION_CHAT_RESPONSE_FORMAT,
        }
        if self.max_output_tokens is not None:
            kwargs["max_completion_tokens"] = self.max_output_tokens
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature

        logger.info(
            "Creating trial drug curation chat completion with model %s", self.model
        )
        return self.wrapped_client.chat.completions.create(**kwargs)

    @staticmethod
    def parse_response(response: Any) -> TrialDrugCurationTable:
        return table_from_payload(json.loads(response_text(response)))

    @staticmethod
    def _build_input(
        user_prompt: str,
        system_prompt: str | None,
    ) -> str | list[dict[str, str]]:
        if system_prompt is None:
            return user_prompt
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    @staticmethod
    def _build_messages(
        user_prompt: str,
        system_prompt: str | None,
    ) -> list[dict[str, str]]:
        messages = []
        if system_prompt is not None:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})
        return messages


def response_text(response: Any) -> str:
    """Extract output text from SDK object or dict-shaped OpenAI payloads."""
    output_text = getattr(response, "output_text", None)
    if output_text:
        return output_text

    if isinstance(response, Mapping):
        output_text = response.get("output_text")
        if output_text:
            return str(output_text)
        choices = response.get("choices", [])
        chat_text = _chat_choices_text(choices)
        if chat_text:
            return chat_text
        output = response.get("output", [])
    else:
        choices = getattr(response, "choices", [])
        chat_text = _chat_choices_text(choices)
        if chat_text:
            return chat_text
        output = getattr(response, "output", [])

    for item in output:
        content = _get_field(item, "content", [])
        for content_item in content:
            text = _get_field(content_item, "text", None)
            if text is not None:
                return str(text)

    raise ValueError("Could not extract output text from OpenAI API payload.")


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
