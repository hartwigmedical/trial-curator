from __future__ import annotations

from trialcurator import drug_openai_client as drug_openai_client_module
from trialcurator.drug_openai_client import (
    DrugOpenaiClient,
    is_retryable_openai_error,
    parse_retry_hint_seconds,
)


class FakeOpenaiClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[tuple[str, str | None]] = []

    def llm_ask(self, user_prompt: str, system_prompt: str | None = None) -> str:
        self.calls.append((user_prompt, system_prompt))
        return self.response


def test_drug_openai_client_ask_uses_injected_client_without_api_call():
    fake_client = FakeOpenaiClient("drug-domain answer")
    client = DrugOpenaiClient(openai_client=fake_client)

    assert client.ask(
        user_prompt="What class is imatinib?",
        system_prompt="Answer as a drug curator.",
    ) == "drug-domain answer"
    assert fake_client.calls == [
        ("What class is imatinib?", "Answer as a drug curator.")
    ]


def test_drug_openai_client_defaults_to_search_model_and_web_search(monkeypatch):
    calls = []

    class FakeChatCompletion:
        @staticmethod
        def create(**kwargs):
            calls.append(kwargs)
            return {"choices": [{"message": {"content": "answer"}}]}

    monkeypatch.setattr(drug_openai_client_module.openai, "OpenAI", None, raising=False)
    monkeypatch.setattr(drug_openai_client_module.openai, "Client", None, raising=False)
    monkeypatch.setattr(drug_openai_client_module.openai, "ChatCompletion", FakeChatCompletion)

    client = DrugOpenaiClient()

    assert client.ask(user_prompt="What is the mechanism of action of imatinib?") == "answer"
    assert calls == [
        {
            "model": "gpt-5-search-api",
            "messages": [
                {
                    "role": "user",
                    "content": "What is the mechanism of action of imatinib?",
                }
            ],
            "web_search_options": {},
        }
    ]


def test_drug_openai_client_can_disable_web_search(monkeypatch):
    calls = []

    class FakeChatCompletion:
        @staticmethod
        def create(**kwargs):
            calls.append(kwargs)
            return {"choices": [{"message": {"content": "answer"}}]}

    monkeypatch.setattr(drug_openai_client_module.openai, "OpenAI", None, raising=False)
    monkeypatch.setattr(drug_openai_client_module.openai, "Client", None, raising=False)
    monkeypatch.setattr(drug_openai_client_module.openai, "ChatCompletion", FakeChatCompletion)

    client = DrugOpenaiClient(enable_web_search=False)

    assert client.ask(user_prompt="What is imatinib?") == "answer"
    assert calls == [
        {
            "model": "gpt-5-search-api",
            "messages": [{"role": "user", "content": "What is imatinib?"}],
        }
    ]


def test_drug_openai_client_preserves_custom_web_search_options(monkeypatch):
    calls = []

    class FakeChatCompletion:
        @staticmethod
        def create(**kwargs):
            calls.append(kwargs)
            return {"choices": [{"message": {"content": "answer"}}]}

    monkeypatch.setattr(drug_openai_client_module.openai, "OpenAI", None, raising=False)
    monkeypatch.setattr(drug_openai_client_module.openai, "Client", None, raising=False)
    monkeypatch.setattr(drug_openai_client_module.openai, "ChatCompletion", FakeChatCompletion)

    client = DrugOpenaiClient(
        web_search_options={"user_location": {"type": "approximate"}}
    )

    assert client.ask(user_prompt="What is imatinib?") == "answer"
    assert calls[0]["web_search_options"] == {
        "user_location": {"type": "approximate"}
    }


def test_drug_openai_client_retries_retryable_openai_errors(monkeypatch):
    calls = []
    sleeps = []

    class FakeRateLimitError(Exception):
        pass

    class FakeChatCompletion:
        @staticmethod
        def create(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise FakeRateLimitError("Rate limit reached. Please try again in 250ms.")
            return {"choices": [{"message": {"content": "retried answer"}}]}

    monkeypatch.setattr(drug_openai_client_module.openai, "OpenAI", None, raising=False)
    monkeypatch.setattr(drug_openai_client_module.openai, "Client", None, raising=False)
    monkeypatch.setattr(drug_openai_client_module.openai, "ChatCompletion", FakeChatCompletion)
    monkeypatch.setattr(drug_openai_client_module.random, "uniform", lambda a, b: 0)
    monkeypatch.setattr(drug_openai_client_module.time, "sleep", sleeps.append)

    client = DrugOpenaiClient(max_retries=2)

    assert client.ask(user_prompt="What is imatinib?") == "retried answer"
    assert len(calls) == 2
    assert sleeps == [0.25]


def test_drug_openai_client_does_not_retry_non_retryable_errors(monkeypatch):
    calls = []

    class FakeChatCompletion:
        @staticmethod
        def create(**kwargs):
            calls.append(kwargs)
            raise ValueError("bad request")

    monkeypatch.setattr(drug_openai_client_module.openai, "OpenAI", None, raising=False)
    monkeypatch.setattr(drug_openai_client_module.openai, "Client", None, raising=False)
    monkeypatch.setattr(drug_openai_client_module.openai, "ChatCompletion", FakeChatCompletion)

    client = DrugOpenaiClient(max_retries=2)

    try:
        client.ask(user_prompt="What is imatinib?")
    except ValueError as error:
        assert "bad request" in str(error)
    else:
        raise AssertionError("Expected non-retryable error to raise")
    assert len(calls) == 1


def test_drug_openai_client_retry_helpers():
    assert parse_retry_hint_seconds("Please try again in 250ms.") == 0.25
    assert parse_retry_hint_seconds("Please try again in 2 seconds.") == 2
    assert parse_retry_hint_seconds("No retry hint") is None
    assert is_retryable_openai_error(Exception("rate limit reached"))
    assert not is_retryable_openai_error(ValueError("bad request"))


def test_drug_openai_client_ask_json_returns_generic_mapping_without_api_call():
    fake_client = FakeOpenaiClient(
        """
        {
          "drug": "imatinib",
          "drug_class": "tyrosine kinase inhibitor",
          "mechanism_of_action": "BCR-ABL inhibition"
        }
        """
    )
    client = DrugOpenaiClient(openai_client=fake_client)

    assert client.ask_json(
        user_prompt="Classify imatinib.",
        system_prompt="Return JSON.",
    ) == {
        "drug": "imatinib",
        "drug_class": "tyrosine kinase inhibitor",
        "mechanism_of_action": "BCR-ABL inhibition",
    }
    assert fake_client.calls == [("Classify imatinib.", "Return JSON.")]
