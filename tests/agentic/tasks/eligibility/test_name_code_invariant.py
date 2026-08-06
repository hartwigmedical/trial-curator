"""`oncotree_name` must never disagree with `oncotree_code`.

The old schema asked the LLM for two independent renderings of the same expression and persisted both with
nothing cross-checking them, which is how 46 store rows ended up with a name that did not match their code. The
name is now DERIVED, and the derivation happens at the persistence boundary so it cannot be bypassed.
"""
from __future__ import annotations

from aus_trial_universe.qa import adjudications
from aus_trial_universe.tasks.eligibility.schema import CancerTypeMap
from aus_trial_universe.tasks.eligibility.mapping.cancer_type.vocab import render_name_expression


def test_cancer_type_map_derives_the_name_and_ignores_what_it_is_given():
    m = CancerTypeMap(cancer_type="x", oncotree_name="DELIBERATELY WRONG",
                      oncotree_code="BREAST AND NOT(BRAIN)")
    assert m.oncotree_name == "Breast AND NOT(CNS/Brain)"


def test_name_derivation_round_trips_and_handles_sentinels_and_empty():
    for code in ["NSCLC", "Solid tumour AND NOT(MEL)", "GB OR (Solid tumour AND NOT(BRAIN))", ""]:
        assert CancerTypeMap(cancer_type="k", oncotree_code=code).oncotree_name == render_name_expression(code)


def test_approved_adjudications_are_wellformed():
    """Each entry is an EXPECTATION first: the comparison reports whether the pipeline reached it unaided. An
    entry with no rationale or no approver is not reviewable, which defeats the point of keeping it in code."""
    assert adjudications.for_column("cancer_type"), "the register should not be silently empty"
    for key, ruling in adjudications.for_column("cancer_type").items():
        assert ruling.value == key
        # `final` is checked for being a STRING, not for being truthy: `''` is a legitimate ruling — it is the
        # correct mapping for a value that states no current tumour type (a prior-malignancy or screening cohort),
        # and the register's own docstring says so. Asserting truthiness here forbade a valid verdict, which is the
        # same truthiness trap `reconcile.py` documents at its ruling lookup.
        assert isinstance(ruling.final, str)
        assert ruling.rationale.strip() and ruling.approved.strip()
