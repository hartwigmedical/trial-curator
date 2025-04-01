from abc import ABC, abstractmethod


class LlmClient(ABC):
    """
    Abstract base class for a language model client.

    This class serves as a blueprint for creating clients that interact
    with language models. It defines an abstract method `llm_ask`, which
    must be implemented by any subclass to send prompts to the language
    model and retrieve corresponding responses.
    """

    @abstractmethod
    def llm_ask(self, prompt: str) -> str:
        pass
