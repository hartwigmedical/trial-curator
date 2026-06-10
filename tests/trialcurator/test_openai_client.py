from __future__ import annotations

from types import SimpleNamespace

from trialcurator import openai_client as openai_client_module
from trialcurator.openai_client import OpenaiClient


def test_openai_client_uses_legacy_chatcompletion_when_client_class_absent(monkeypatch):
    calls = []

    class FakeChatCompletion:
        @staticmethod
        def create(**kwargs):
            calls.append(kwargs)
            return {"choices": [{"message": {"content": "legacy response"}}]}

    monkeypatch.setattr(openai_client_module.openai, "OpenAI", None, raising=False)
    monkeypatch.setattr(openai_client_module.openai, "Client", None, raising=False)
    monkeypatch.setattr(openai_client_module.openai, "ChatCompletion", FakeChatCompletion)

    client = OpenaiClient(model="test-model", temperature=0.2, top_p=0.9)

    assert client.wrapped_client is None
    assert client.llm_ask("User prompt", system_prompt="System prompt") == "legacy response"
    assert calls == [
        {
            "model": "test-model",
            "temperature": 0.2,
            "top_p": 0.9,
            "messages": [
                {"role": "system", "content": "System prompt"},
                {"role": "user", "content": "User prompt"},
            ],
        }
    ]


def test_openai_client_uses_modern_wrapped_client_when_available(monkeypatch):
    class FakeCompletions:
        def __init__(self):
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="modern response"),
                    )
                ]
            )

    class FakeOpenAI:
        instances = []

        def __init__(self):
            self.completions = FakeCompletions()
            self.chat = SimpleNamespace(completions=self.completions)
            self.__class__.instances.append(self)

    monkeypatch.setattr(openai_client_module.openai, "OpenAI", FakeOpenAI, raising=False)
    monkeypatch.setattr(openai_client_module.openai, "Client", None, raising=False)

    client = OpenaiClient(model="test-model", temperature=0.2, top_p=0.9)

    assert client.llm_ask("User prompt") == "modern response"
    assert FakeOpenAI.instances[0].completions.calls == [
        {
            "model": "test-model",
            "temperature": 0.2,
            "top_p": 0.9,
            "messages": [
                {"role": "user", "content": "User prompt"},
            ],
        }
    ]
