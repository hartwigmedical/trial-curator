"""Tests for the finding-model conversion procedures (mapper -> validate + reviewer -> refine)."""
from __future__ import annotations

from aus_trial_universe.agentic.core.client import LlmResult
from aus_trial_universe.agentic.tasks.mapping.schema import FindingModelMapping, ReviewVerdict
from aus_trial_universe.agentic.tasks.mapping.workflow import (
    map_gene_alterations,
    map_molecular_signatures,
)
from aus_trial_universe.agentic.tools.finding_model import finding_model_problems


# --- validator (tools/finding_model) --------------------------------------- #
def test_finding_model_validator():
    assert finding_model_problems("SmallVariant[gene=BRAF & transcriptImpact.hgvsProteinImpact=p.V600E]") == []
    assert finding_model_problems("Fusion[geneStart=ALK | geneEnd=ALK]") == []
    assert finding_model_problems("") == []
    assert any("unbalanced" in p for p in finding_model_problems("SmallVariant[gene=EGFR"))
    assert any("unknown finding-model class" in p for p in finding_model_problems("Bogus[gene=X]"))
    assert any("gene=" in p for p in finding_model_problems("SmallVariant[inSpliceRegion]"))  # unscoped


# --- mapper -> validate + reviewer -> refine ------------------------------- #
class _FMClient:
    """Fake dispatching FindingModelMapping (mapper) + ReviewVerdict (reviewer), sequential."""

    def __init__(self, mappings, verdicts=None):
        self._mappings = mappings
        self._verdicts = verdicts or []
        self.mapper_calls = 0
        self.reviewer_calls = 0

    def parse(self, output_schema, *, instructions, user_input, model=None,
              temperature=None, seed=None, max_completion_tokens=None):
        if output_schema is FindingModelMapping:
            i = min(self.mapper_calls, len(self._mappings) - 1)
            self.mapper_calls += 1
            return LlmResult(self._mappings[i], "fake", "{}", False, 1)
        if output_schema is ReviewVerdict:
            i = min(self.reviewer_calls, len(self._verdicts) - 1)
            self.reviewer_calls += 1
            v = self._verdicts[i] if self._verdicts else ReviewVerdict(faithful=True)
            return LlmResult(v, "fake", "{}", False, 1)
        raise AssertionError(f"unexpected schema {output_schema}")


def test_gene_mapping_happy():
    client = _FMClient(
        mappings=[FindingModelMapping(finding_model="SmallVariant[gene=BRAF & transcriptImpact.hgvsProteinImpact=p.V600E]")],
        verdicts=[ReviewVerdict(faithful=True)],
    )
    out = map_gene_alterations(client, ["BRAF V600E [ELIGIBILITY CRITERIA]"])
    r = out["BRAF V600E"]
    assert r.faithful and r.attempts == 1
    assert r.finding_model == "SmallVariant[gene=BRAF & transcriptImpact.hgvsProteinImpact=p.V600E]"


def test_gene_mapping_invalid_syntax_refines_before_reviewer():
    client = _FMClient(
        mappings=[FindingModelMapping(finding_model="SmallVariant[inSpliceRegion]"),        # missing gene= -> invalid
                  FindingModelMapping(finding_model="SmallVariant[gene=MET & inSpliceRegion]")],
        verdicts=[ReviewVerdict(faithful=True)],
    )
    out = map_gene_alterations(client, ["MET splice"])
    r = out["MET splice"]
    assert r.faithful and r.attempts == 2
    assert client.mapper_calls == 2 and client.reviewer_calls == 1  # reviewer only after syntax valid


def test_signature_mapping_and_dedup():
    class _SrcClient:
        def parse(self, output_schema, *, instructions, user_input, model=None,
                  temperature=None, seed=None, max_completion_tokens=None):
            if output_schema is FindingModelMapping:
                fm = "MicrosatelliteStability[PurpleMicrosatelliteStatus=MSI]" if "MSI" in user_input \
                    else "homologousRecombination[ChordStatus=HR_DEFICIENT]"
                return LlmResult(FindingModelMapping(finding_model=fm), "fake", "{}", False, 1)
            return LlmResult(ReviewVerdict(faithful=True), "fake", "{}", False, 1)

    out = map_molecular_signatures(_SrcClient(), ["MSI-high [ELIGIBILITY CRITERIA]", "MSI-high [X]", "HRD [Y]"])
    assert set(out) == {"MSI-high", "HRD"}  # deduped on stripped value
    assert out["MSI-high"].finding_model == "MicrosatelliteStability[PurpleMicrosatelliteStatus=MSI]"
    assert out["HRD"].finding_model == "homologousRecombination[ChordStatus=HR_DEFICIENT]"
