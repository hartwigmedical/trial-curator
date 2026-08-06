"""Enumerate the pipeline's live agent prompts -> {agent_name: prompt_sha}.

Used by `core/cache_prune` to garbage-collect cache entries produced by prompts that
are no longer in the code (an *outdated prompt*). Every agent is a `build_*(client)`
factory with static `instructions`, so the full live set is finite and enumerable
OFFLINE: constructing an agent only wires config — it never calls the LLM — so no API
key or network is needed. Covers BOTH the eligibility and drug-utility paths, which
share one physical response cache.
"""
from __future__ import annotations

from aus_trial_universe.core.client import LlmClient, prompt_sha


def live_agents(client: LlmClient | None = None):
    """Construct one instance of every current pipeline agent (offline)."""
    client = client or LlmClient()  # bare: no openai client, no cache — construction is offline
    from aus_trial_universe.tasks.drug_utility import agents as dr
    from aus_trial_universe.tasks.eligibility.extraction import agents as ex
    from aus_trial_universe.tasks.eligibility.mapping.cancer_type import agents as mp_ct
    from aus_trial_universe.tasks.eligibility.mapping.gene_alteration import agents as mp_ga
    from aus_trial_universe.tasks.eligibility.mapping.molecular_signature import agents as mp_sig
    from aus_trial_universe.tasks.shared import agents as sh

    agents = [
        # shared · ANZCTR arm identification (used by both paths)
        sh.build_drug_agent(client),
        sh.build_drug_reviewer_agent(client),
        # eligibility · extraction (two sub-stages: raw + interpret)
        ex.build_raw_extractor_agent(client),
        ex.build_raw_reviewer_agent(client),
        ex.build_interpreter_agent(client),
        ex.build_enumeration_reviewer(client),
        # eligibility · mapping — one module per vocabulary column
        mp_ct.build_oncotree_mapper(client),
        mp_ct.build_oncotree_reviewer(client),
        # cancer_type is stage 1 ONLY. Its repairer and group reconciler (and their reviewers) were DELETED
        # 2026-08-06 with the legacy branch's last caller: stages 2 and 3 are deterministic, so the column makes
        # no API call after mapping. Registering LIVE agents is not cosmetic — `cache_prune` deletes any cached
        # (agent, prompt) it cannot find here — but a deleted agent must NOT be listed.
        # gene_alteration is stage 1 ONLY. Its stage-2 repairer and group reconciler (and their reviewers) were
        # DELETED 2026-08-06: stage 2 is now deterministic, so the column makes no API calls after mapping.
        mp_ga.build_gene_alteration_mapper(client),
        mp_ga.build_gene_alteration_reviewer(client),
        mp_sig.build_molecular_signature_mapper(client),
        mp_sig.build_molecular_signature_reviewer(client),
        # drug utility
        dr.build_canonicalizer(client),
        dr.build_canonicalizer_reviewer(client),
        dr.build_annotator(client),
        dr.build_annotator_reviewer(client),
        # the symmetric-match splitter pair — live agents that were never registered, so a prune
        # classified their 497 cached responses as stale and would have re-billed them
        dr.build_biomarker_splitter(client),
        dr.build_biomarker_split_reviewer(client),
        dr.build_role_classifier(client),
        dr.build_role_reviewer(client),
        dr.build_approval_agent(client),
        dr.build_approval_reviewer(client),
    ]
    agents += [a for _spec, a in ex.build_reviewer_agents(client)]  # the extraction reviewer panel
    return agents


def live_prompt_shas(client: LlmClient | None = None) -> dict[str, str]:
    """Map each current agent's `name` -> the sha256 of its current `instructions`."""
    return {a.name: prompt_sha(a.instructions) for a in live_agents(client)}
