import logging
import os
import warnings

from google import genai
from google.genai import types
from trialcurator.llm_client import LlmClient

logger = logging.getLogger(__name__)


class GeminiClient(LlmClient):
    MODEL = "gemini-2.5-pro"  # Use 2.5-pro as the default. But each function / unit test should have the model that it called specified.

    # Gemini models: https://ai.google.dev/gemini-api/docs/models
    # As of June 2025, latest model is Gemini 2.5 with three stable releases:
    # 1. gemini-2.5-flash-lite
    # 2. gemini-2.5-flash
    # 3. gemini-2.5-pro

    def __init__(self, temperature=0.0, top_p=1.0, top_k=1, model=MODEL):

        project_id = os.getenv("GEMINI_PROJECT_ID")
        location = os.getenv("GEMINI_LOCATION")

        self.wrapped_client = genai.Client(vertexai=True, project=project_id, location=location)
        self.model = model
        self.temperature = temperature
        self.top_p = top_p
        self.top_k = top_k

    def llm_ask(self, user_prompt: str, system_prompt: str = None) -> str:

        warnings.filterwarnings("ignore", category=DeprecationWarning, module="pydantic")

        if system_prompt:
            config = types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=self.temperature,
                top_p = self.top_p,
                top_k=self.top_k
            )
            for line in system_prompt.splitlines():
                logging.info(f"system prompt: {line}")
        else:
            config = types.GenerateContentConfig(
                temperature=self.temperature,
                top_p = self.top_p,
                top_k=self.top_k
            )
        for line in user_prompt.splitlines():
            logging.info(f"user prompt: {line}")

        completion = self.wrapped_client.models.generate_content(
            model=self.model,
            contents=user_prompt,
            config=config
        )

        response = completion.text
        for line in response.splitlines():
            logging.info(f"response: {line}")

        return response
