"""The specialist agents for slice-1 eligibility extraction (spec §6, §8).

- extractor: relevant trial text (title, description, conditions, eligibility, ...) -> DNF rows.
- oncotree:  a free-text cancer type -> OncoTree name + code.
- judge:     audits the extracted table against the SAME trial text (faithfulness).
"""
from __future__ import annotations

from aus_trial_universe.agentic.core.agent import Agent
from aus_trial_universe.agentic.core.client import LlmClient
from aus_trial_universe.agentic.tasks.eligibility_extraction.schema import (
    EligibilityExtraction,
    JudgeVerdict,
    OncotreeMapping,
)

EXTRACTOR_INSTRUCTIONS = """\
You are given the relevant sections of a clinical trial (title, official title, summary, \
detailed description, conditions, and eligibility criteria). From ALL of these sections, extract \
the trial's eligibility into a normalized table of DNF rows, covering ONLY cancer type and \
gene/molecular alteration.

Use every section, not just the eligibility criteria — the required cancer type and molecular \
selection are often stated in the title, conditions, or description.

Rules:
- One row = one satisfiable combination of requirements (a conjunction: all cells ANDed).
- If eligibility offers alternatives (OR), emit one row per alternative.
- Conditionals become co-occurrence: "if <cancer A> then <mutation X>; if <cancer B> then \
<mutation Y>" -> two rows: (cancer=A, gene=X) and (cancer=B, gene=Y).
- cancer_type: the required cancer/tumour type, in the trial's own words.
- gene_alteration: the required gene/molecular alteration as a concise normalized description \
(e.g. "EGFR exon 19 deletion", "BRAF V600E", "ALK fusion"). Use "" if the row has no molecular requirement.
- Consider inclusion requirements only. Ignore age, performance status, labs, prior therapy, and exclusions.
- Do NOT invent criteria. If a tumour type is required with no molecular criterion, emit a row with gene_alteration="".
"""

ONCOTREE_INSTRUCTIONS = """\
Map the given free-text cancer/tumour type to its single closest OncoTree entry.
Return the OncoTree name and its code (e.g. name="Lung Adenocarcinoma", code="LUAD").
For a broad mention (e.g. "advanced solid tumours") choose the closest broad OncoTree node.
Return only the two fields.
"""

JUDGE_INSTRUCTIONS = """\
You audit an extracted eligibility DNF table against the provided trial text — which includes all \
relevant sections (title, description, conditions, eligibility) — for cancer type and gene alteration ONLY.

Set faithful=true if every cancer-type and gene-alteration inclusion requirement stated ANYWHERE in \
the provided text is captured, none is invented, and combinations (conditionals/alternatives) are \
correctly represented as rows. Otherwise set faithful=false and list concrete, actionable problems \
(what is missing, invented, or mis-paired). Ignore age/labs/performance-status/prior-therapy/exclusion criteria.
"""


def build_extractor_agent(client: LlmClient, *, model: str | None = None) -> Agent[EligibilityExtraction]:
    return Agent(
        name="eligibility_extractor",
        instructions=EXTRACTOR_INSTRUCTIONS,
        output_schema=EligibilityExtraction,
        client=client,
        model=model,
    )


def build_oncotree_agent(client: LlmClient, *, model: str | None = None) -> Agent[OncotreeMapping]:
    return Agent(
        name="cancer_type_oncotree",
        instructions=ONCOTREE_INSTRUCTIONS,
        output_schema=OncotreeMapping,
        client=client,
        model=model,
    )


def build_judge_agent(client: LlmClient, *, model: str | None = None) -> Agent[JudgeVerdict]:
    return Agent(
        name="faithfulness_judge",
        instructions=JUDGE_INSTRUCTIONS,
        output_schema=JudgeVerdict,
        client=client,
        model=model,
    )
