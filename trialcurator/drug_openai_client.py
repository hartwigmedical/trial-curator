from __future__ import annotations

import logging
from typing import Any, Mapping

import openai

from trialcurator.llm_client import LlmClient
from trialcurator.utils import llm_json_check_and_repair

logger = logging.getLogger(__name__)

DEFAULT_DRUG_MODEL = "gpt-5-search-api"
DEFAULT_DRUG_TEMPERATURE = None
DEFAULT_DRUG_TOP_P = None
DEFAULT_DRUG_WEB_SEARCH_OPTIONS: Mapping[str, Any] = {}


class DrugOpenaiClient:
    """OpenAI helper for drug-domain prompts.

    This class is intentionally schema-neutral. Callers own their task-specific
    prompt, response schema, validation, and post-processing.
    """

    MODEL = DEFAULT_DRUG_MODEL
    TEMPERATURE = DEFAULT_DRUG_TEMPERATURE
    TOP_P = DEFAULT_DRUG_TOP_P

    def __init__(
        self,
        *,
        model: str | None = None,
        temperature: float | None = TEMPERATURE,
        top_p: float | None = TOP_P,
        enable_web_search: bool = True,
        web_search_options: Mapping[str, Any] | None = None,
        openai_client: LlmClient | None = None,
    ) -> None:
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

        completion = self._create_chat_completion(messages)
        response = self._completion_text(completion)
        for line in response.splitlines():
            logger.info("drug response: %s", line)

        return response

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
