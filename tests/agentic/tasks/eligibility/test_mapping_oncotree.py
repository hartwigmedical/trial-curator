"""Tests for the OncoTree mapping procedure (mapper -> validate + reviewer -> refine).

Wiring is exercised with fake clients; the OncoTree vocabulary / validator run against
the real resource file.
"""
from __future__ import annotations

from aus_trial_universe.agentic.core.client import LlmResult
from aus_trial_universe.agentic.tasks.eligibility.mapping.schema import OncotreeMapping, ReviewVerdict
from aus_trial_universe.agentic.tasks.eligibility.mapping.workflow import (
    _oncotree_logic_problems,
    map_cancer_types,
    map_oncotree,
    strip_provenance,
)
from aus_trial_universe.agentic.tasks.eligibility.tools.oncotree import invalid_codes, is_subcode, oncotree_vocab, valid_codes


# --- tools/oncotree (real resource) ---------------------------------------- #
def test_vocab_loads_real_codes():
    vocab = oncotree_vocab()
    assert len(vocab) > 800
    assert vocab.get("NSCLC") and vocab.get("BREAST") and vocab.get("MEL")


def test_invalid_codes_flags_only_hallucinations():
    assert invalid_codes("NSCLC AND NOT(MEL)") == []
    assert invalid_codes("Solid tumour AND NOT(MEL)") == []   # sentinel + real code
    assert invalid_codes("Pan-cancer") == []
    assert invalid_codes("NSCLC | FOOBAR") == ["FOOBAR"]
    assert "MEL" in valid_codes() and "Pan-cancer" in valid_codes()
    assert "Haematological malignancy" in valid_codes()   # the third permitted sentinel
    assert "[None]" not in valid_codes()                   # [None] removed


def test_is_subcode_hierarchy():
    assert is_subcode("LUAD", "NSCLC")        # lung adenocarcinoma is under NSCLC
    assert not is_subcode("NSCLC", "NSCLC")   # not its own descendant
    assert not is_subcode("NSCLC", "LUAD")    # parent is not a subtype of its child


def test_strip_provenance():
    assert strip_provenance("metastatic NSCLC [TITLE; ELIGIBILITY CRITERIA]") == "metastatic NSCLC"
    assert strip_provenance("solid tumour AND NOT(melanoma) [TITLE]") == "solid tumour AND NOT(melanoma)"
    assert strip_provenance("") == ""


# --- mapper -> reviewer -> refine ------------------------------------------ #
class _SeqClient:
    """Fake dispatching canned outputs by schema, one per call (sequential)."""

    def __init__(self, mappings, verdicts=None):
        self._mappings = mappings
        self._verdicts = verdicts or []
        self.mapper_calls = 0
        self.reviewer_calls = 0

    def parse(self, output_schema, *, instructions, user_input, model=None,
              temperature=None, seed=None, max_completion_tokens=None, **_):
        if output_schema is OncotreeMapping:
            i = min(self.mapper_calls, len(self._mappings) - 1)
            self.mapper_calls += 1
            return LlmResult(self._mappings[i], "fake", "{}", False, 1)
        if output_schema is ReviewVerdict:
            i = min(self.reviewer_calls, len(self._verdicts) - 1)
            self.reviewer_calls += 1
            v = self._verdicts[i] if self._verdicts else ReviewVerdict(faithful=True)
            return LlmResult(v, "fake", "{}", False, 1)
        raise AssertionError(f"unexpected schema {output_schema}")


def test_map_oncotree_happy():
    client = _SeqClient(
        mappings=[OncotreeMapping(oncotree_name="Non-Small Cell Lung Cancer", oncotree_code="NSCLC")],
        verdicts=[ReviewVerdict(faithful=True)],
    )
    r = map_oncotree(client, "metastatic NSCLC")
    assert r.faithful and r.attempts == 1
    assert r.oncotree_code == "NSCLC" and r.oncotree_name == "Non-Small Cell Lung Cancer"


def test_map_oncotree_invalid_code_refines_before_reviewer():
    client = _SeqClient(
        mappings=[OncotreeMapping(oncotree_name="?", oncotree_code="FOOBAR"),
                  OncotreeMapping(oncotree_name="Melanoma", oncotree_code="MEL")],
        verdicts=[ReviewVerdict(faithful=True)],
    )
    r = map_oncotree(client, "melanoma", max_attempts=3)
    assert r.faithful and r.attempts == 2 and r.oncotree_code == "MEL"
    assert client.mapper_calls == 2
    assert client.reviewer_calls == 1  # reviewer only consulted once codes were valid


def test_oncotree_logic_problems_catches_logic_errors():
    assert _oncotree_logic_problems("NSCLC") == []
    assert _oncotree_logic_problems("Solid tumour AND NOT(MEL)") == []   # valid same-column carve-out
    assert _oncotree_logic_problems("Solid tumour OR MEL") == []          # OR of broad+subtype is fine
    assert any("[None]" in p for p in _oncotree_logic_problems("GCT AND NOT([None])"))
    assert any("included and excluded" in p for p in _oncotree_logic_problems("SCLC AND NOT(SCLC)"))
    assert any("duplicate" in p for p in _oncotree_logic_problems("NBL AND NBL"))            # X AND X
    assert any("broad" in p for p in _oncotree_logic_problems("Solid tumour AND MEL"))       # sentinel AND specific
    assert any("parent" in p for p in _oncotree_logic_problems("NSCLC AND LUAD"))            # subtype AND parent
    # an OR INSIDE a NOT() carve-out must not be split mid-NOT() and misread as a positive broad-ANDed-subtype
    assert _oncotree_logic_problems("Solid tumour AND NOT(NSCLC OR THYROID)") == []
    assert _oncotree_logic_problems("Pan-cancer AND NOT(MEL OR SCLC OR GCT)") == []


def test_map_oncotree_refines_on_contradiction():
    client = _SeqClient(
        mappings=[OncotreeMapping(oncotree_name="SCLC AND NOT(SCLC)", oncotree_code="SCLC AND NOT(SCLC)"),
                  OncotreeMapping(oncotree_name="Small Cell Lung Cancer", oncotree_code="SCLC")],
        verdicts=[ReviewVerdict(faithful=True)],
    )
    r = map_oncotree(client, "relapsed ES-SCLC excluding complex SCLC", max_attempts=3)
    assert r.faithful and r.attempts == 2 and r.oncotree_code == "SCLC"


def test_map_oncotree_reviewer_unfaithful_refines():
    client = _SeqClient(
        mappings=[OncotreeMapping(oncotree_name="Lung", oncotree_code="LUNG"),
                  OncotreeMapping(oncotree_name="Non-Small Cell Lung Cancer", oncotree_code="NSCLC")],
        verdicts=[ReviewVerdict(faithful=False, problems=["too broad — use NSCLC"]), ReviewVerdict(faithful=True)],
    )
    r = map_oncotree(client, "NSCLC", max_attempts=3)
    assert r.faithful and r.attempts == 2 and r.oncotree_code == "NSCLC"


# --- orchestrator: dedup distinct + apply ---------------------------------- #
class _SrcClient:
    """Deterministic under fan_out concurrency: maps by source substring."""

    def __init__(self, by_source):
        self._by_source = by_source

    def parse(self, output_schema, *, instructions, user_input, model=None,
              temperature=None, seed=None, max_completion_tokens=None, **_):
        if output_schema is OncotreeMapping:
            for key, m in self._by_source.items():
                if key in user_input:
                    return LlmResult(m, "fake", "{}", False, 1)
            return LlmResult(OncotreeMapping(oncotree_name="", oncotree_code=""), "fake", "{}", False, 1)
        if output_schema is ReviewVerdict:
            return LlmResult(ReviewVerdict(faithful=True), "fake", "{}", False, 1)
        raise AssertionError(f"unexpected schema {output_schema}")


def test_map_cancer_types_dedups_and_maps():
    client = _SrcClient({
        "NSCLC": OncotreeMapping(oncotree_name="Non-Small Cell Lung Cancer", oncotree_code="NSCLC"),
        "melanoma": OncotreeMapping(oncotree_name="Melanoma", oncotree_code="MEL"),
    })
    out = map_cancer_types(client, ["NSCLC [TITLE]", "NSCLC [CONDITIONS]", "melanoma [ELIGIBILITY CRITERIA]"])
    assert set(out) == {"NSCLC", "melanoma"}  # deduped on stripped value
    assert out["NSCLC"].oncotree_code == "NSCLC"
    assert out["melanoma"].oncotree_code == "MEL"
