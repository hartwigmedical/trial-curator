"""Enumerate the pipeline's live agent prompts -> {agent_name: prompt_sha}.

Used by `core/cache_prune` to garbage-collect cache entries produced by prompts that
are no longer in the code (an *outdated prompt*). Every agent is a `build_*(client)`
factory with static `instructions`, so the full live set is finite and enumerable
OFFLINE: constructing an agent only wires config — it never calls the LLM — so no API
key or network is needed. Covers BOTH the eligibility and drug-utility paths, which
share one physical response cache.
"""
from __future__ import annotations

from aus_trial_universe.agentic.core.client import LlmClient, prompt_sha


def live_agents(client: LlmClient | None = None):
    """Construct one instance of every current pipeline agent (offline)."""
    client = client or LlmClient()  # bare: no openai client, no cache — construction is offline
    from aus_trial_universe.agentic.tasks.drug_utility import agents as dr
    from aus_trial_universe.agentic.tasks.eligibility.extraction import agents as ex
    from aus_trial_universe.agentic.tasks.eligibility.mapping import agents as mp

    agents = [
        # eligibility · extraction
        ex.build_extractor_agent(client),
        ex.build_drug_agent(client),
        ex.build_drug_reviewer_agent(client),
        ex.build_enumeration_reviewer(client),
        # eligibility · mapping
        mp.build_oncotree_mapper(client),
        mp.build_oncotree_reviewer(client),
        mp.build_gene_alteration_mapper(client),
        mp.build_gene_alteration_reviewer(client),
        mp.build_molecular_signature_mapper(client),
        mp.build_molecular_signature_reviewer(client),
        # drug utility
        dr.build_canonicalizer(client),
        dr.build_canonicalizer_reviewer(client),
        dr.build_annotator(client),
        dr.build_annotator_reviewer(client),
        dr.build_approval_agent(client),
        dr.build_approval_reviewer(client),
    ]
    agents += [a for _spec, a in ex.build_reviewer_agents(client)]  # the extraction reviewer panel
    return agents


def live_prompt_shas(client: LlmClient | None = None) -> dict[str, str]:
    """Map each current agent's `name` -> the sha256 of its current `instructions`."""
    return {a.name: prompt_sha(a.instructions) for a in live_agents(client)}
