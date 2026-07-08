"""Tests for trial-level drug curation (web-search curator -> validate + reviewer -> refine)."""
from __future__ import annotations

from aus_trial_universe.agentic.core.client import LlmResult
from aus_trial_universe.agentic.tasks.mapping.schema import DrugCuration, ReviewVerdict
from aus_trial_universe.agentic.tasks.mapping.workflow import curate_drugs


class _DrugClient:
    """Fake: research() -> DrugCuration (the web-search curator); parse() -> ReviewVerdict."""

    def __init__(self, curations, verdicts=None):
        self._curations = curations
        self._verdicts = verdicts or []
        self.research_calls = 0
        self.reviewer_calls = 0

    def research(self, output_schema, *, instructions, user_input, model=None, max_completion_tokens=None):
        i = min(self.research_calls, len(self._curations) - 1)
        self.research_calls += 1
        return LlmResult(self._curations[i], "fake", "{}", False, 1)

    def parse(self, output_schema, *, instructions, user_input, model=None,
              temperature=None, seed=None, max_completion_tokens=None):
        i = min(self.reviewer_calls, len(self._verdicts) - 1)
        self.reviewer_calls += 1
        v = self._verdicts[i] if self._verdicts else ReviewVerdict(faithful=True)
        return LlmResult(v, "fake", "{}", False, 1)


def test_curate_drugs_happy():
    client = _DrugClient(
        curations=[DrugCuration(main_drugs="olaparib", auxiliary_drugs="",
                                pottr_drug_class="cancer_therapy -> PARP_inhibitor",
                                drug_class="PARP inhibitor", tga_status="Approved (2016)",
                                pbs_status="PBS-listed for BRCA-mutated ovarian cancer")],
        verdicts=[ReviewVerdict(faithful=True)],
    )
    r = curate_drugs(client, "An olaparib maintenance trial ...", ["olaparib"])
    assert r.faithful and r.attempts == 1 and client.research_calls == 1
    assert r.main_drugs == "olaparib" and r.drug_class == "PARP inhibitor"
    assert r.tga_status == "Approved (2016)"


def test_curate_drugs_validator_refines_on_empty_main():
    client = _DrugClient(
        curations=[DrugCuration(main_drugs="", drug_class="", tga_status="", pbs_status=""),   # invalid
                   DrugCuration(main_drugs="pembrolizumab", drug_class="anti-PD-1 mAb",
                                tga_status="Approved (2015)", pbs_status="Unclear")],
        verdicts=[ReviewVerdict(faithful=True)],
    )
    r = curate_drugs(client, "A pembrolizumab trial ...", ["pembrolizumab", "placebo"], max_attempts=3)
    assert r.faithful and r.attempts == 2 and client.research_calls == 2
    assert r.main_drugs == "pembrolizumab" and r.tga_status == "Approved (2015)"


def test_curate_drugs_no_drugs_skips_research():
    client = _DrugClient(curations=[])
    r = curate_drugs(client, "some trial", [])
    assert client.research_calls == 0 and r.main_drugs == "" and r.faithful
