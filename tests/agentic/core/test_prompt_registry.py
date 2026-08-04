"""The response cache is only safe if EVERY live agent is registered.

`cache_prune` classifies any (agent name, prompt sha) it cannot find in the registry as STALE and deletes it. An
unregistered agent therefore has its cached responses silently GC'd and re-billed on the next run — and, worse,
a run that depended on those cached values stops being reproducible.

This bit twice on 2026-08-04: the new OncoTree refinement agents were unregistered, and so (pre-existing) were the
two `approval_biomarker_split*` agents, whose 497 entries a prune would have binned. Hence this test: it walks the
agent-builder functions in every stage module and asserts each one's agent name appears in the registry, so a new
builder cannot be added without registering it.
"""
from __future__ import annotations

import inspect

import pytest

from aus_trial_universe.core.client import LlmClient
from aus_trial_universe.core.prompt_registry import live_prompt_shas

BUILDER_MODULES = [
    "aus_trial_universe.tasks.eligibility.mapping.cancer_type.agents",
    "aus_trial_universe.tasks.eligibility.mapping.gene_alteration.agents",
    "aus_trial_universe.tasks.eligibility.mapping.molecular_signature.agents",
    "aus_trial_universe.tasks.eligibility.extraction.agents",
    "aus_trial_universe.tasks.drug_utility.agents",
    "aus_trial_universe.tasks.shared.agents",
]


def _live_agent_names() -> set[str]:
    shas = live_prompt_shas()
    first = next(iter(shas))
    return {k[0] for k in shas} if isinstance(first, tuple) else set(shas)


@pytest.mark.parametrize("module_path", BUILDER_MODULES)
def test_every_agent_builder_is_registered(module_path):
    module = __import__(module_path, fromlist=["*"])
    client = LlmClient()
    registered = _live_agent_names()
    missing = []
    for name, fn in vars(module).items():
        if not (name.startswith("build_") and callable(fn)):
            continue
        params = inspect.signature(fn).parameters
        if "client" not in params:
            continue
        agent = fn(client)
        if getattr(agent, "name", None) and agent.name not in registered:
            missing.append(f"{name}() -> {agent.name!r}")
    assert not missing, (
        f"{module_path}: agent(s) not in prompt_registry — cache_prune would delete their cached responses "
        f"as stale: {missing}"
    )
