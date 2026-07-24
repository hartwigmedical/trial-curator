"""Agent: one well-scoped LLM job (spec §5).

An Agent binds a prompt + output schema + model config to a client. Calling it
runs a single schema-validated LLM call and returns the parsed object. Agents are
the *specialists* a Workflow dispatches; the orchestration *between* agents lives
in workflow.py / the task modules, never inside an agent.

Input-schema validation and tool/function-calling are planned extensions; slice-1
agents take a string, or a non-string rendered to a string via `render_input`.
"""
from __future__ import annotations

from typing import Any, Callable, Generic, TypeVar

from pydantic import BaseModel

from aus_trial_universe.agentic.core.client import LlmClient, LlmResult

TOut = TypeVar("TOut", bound=BaseModel)


class Agent(Generic[TOut]):
    """A prompt + output schema + model config, bound to a client."""

    def __init__(
        self,
        *,
        name: str,
        instructions: str,
        output_schema: type[TOut],
        client: LlmClient,
        model: str | None = None,
        temperature: float | None = None,
        seed: int | None = None,
        max_completion_tokens: int | None = None,
        render_input: Callable[[Any], str] = str,
        web_search: bool = False,
    ) -> None:
        self.name = name
        self.instructions = instructions
        self.output_schema = output_schema
        self.client = client
        self.model = model
        self.temperature = temperature
        self.seed = seed
        self.max_completion_tokens = max_completion_tokens
        self.render_input = render_input
        self.web_search = web_search  # research via the Responses API web_search tool

    def run(self, input_data: Any) -> LlmResult[TOut]:
        """Run the agent and return the full LlmResult (parsed object + metadata)."""
        user_input = input_data if isinstance(input_data, str) else self.render_input(input_data)
        if self.web_search:
            return self.client.research(
                self.output_schema,
                instructions=self.instructions,
                user_input=user_input,
                model=self.model,
                max_completion_tokens=self.max_completion_tokens,
                agent_name=self.name,
            )
        return self.client.parse(
            self.output_schema,
            instructions=self.instructions,
            user_input=user_input,
            model=self.model,
            temperature=self.temperature,
            seed=self.seed,
            max_completion_tokens=self.max_completion_tokens,
            agent_name=self.name,
        )

    def __call__(self, input_data: Any) -> TOut:
        """Ergonomic form: return just the validated object (see spec pseudo-code)."""
        return self.run(input_data).parsed

    def __repr__(self) -> str:
        return f"Agent(name={self.name!r}, output_schema={self.output_schema.__name__})"
