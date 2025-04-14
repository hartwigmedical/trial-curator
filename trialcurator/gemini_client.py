import logging
import os
from google import genai
from google.genai import types
from trialcurator.llm_client import LlmClient

logger = logging.getLogger(__name__)


class GeminiClient(LlmClient):

    MODEL = "gemini-2.0-flash-001"
    TOP_P = 1.0

    def __init__(self, temperature, top_p=TOP_P, model=MODEL):

        project_id = os.getenv("GEMINI_PROJECT_ID")
        location = os.getenv("GEMINI_LOCATION")

        self.wrapped_client = genai.Client(vertexai=True, project=project_id, location=location)
        self.temperature = temperature
        self.top_p = top_p
        self.model = model

    def llm_ask(self, user_prompt: str, system_prompt: str = None) -> str:

        if system_prompt:
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=self.temperature
            )
            for line in system_prompt.splitlines():
                logging.info(f"system prompt: {line}")
        else:
            config=types.GenerateContentConfig(
                temperature=self.temperature
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