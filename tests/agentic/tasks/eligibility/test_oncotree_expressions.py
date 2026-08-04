"""Tests for the OncoTree expression layer (parser, canonical form, defect catalogue).

These protect the semantics that the correction rests on. Two of them exist because a real bug got through:
`test_names_with_parentheses_parse_as_one_atom` (a naive splitter shreds `Primary Mediastinal (Thymic) …`) and
`test_or_branch_subsumed_is_reported_not_rewritten` (absorbing it is a judgement, not a rewrite).
"""
from __future__ import annotations

import pytest

from aus_trial_universe.tasks.eligibility.tools.oncotree_checks import ALLOWED_NESTED_DIFFERENCES
from aus_trial_universe.tasks.eligibility.tools.oncotree import (
    canonical_form,
    expression_problems,
    has_error,
    normalise_code_expression,
    render_name_expression,
)


def defects(expr: str) -> set[str]:
    return {p.defect for p in expression_problems(expr)}


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def test_names_with_parentheses_parse_as_one_atom():
    """`Primary Mediastinal (Thymic) Large B-Cell Lymphoma` contains parens; a splitter that runs before name
    masking tears it apart and reports nonsense."""
    expr = "NOT(Primary Mediastinal (Thymic) Large B-Cell Lymphoma)"
    assert defects(expr) & {"lex_leaked_name"}
    assert "lex_unknown_operand" not in defects(expr)
    assert normalise_code_expression(expr) == "NOT(PMBL)"


def test_name_with_lowercase_and_inside_it():
    """`Oligodendroglioma, IDH-mutant, and 1p/19q-Codeleted` has a lowercase 'and' — must not split on it."""
    assert normalise_code_expression("DIFG AND NOT(Oligodendroglioma, IDH-mutant, and 1p/19q-Codeleted)") \
        == "DIFG AND NOT(ODG)"


# --------------------------------------------------------------------------- #
# The name/code invariant
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raw,code", [
    ("Breast AND NOT(BRAIN)", "BREAST AND NOT(BRAIN)"),          # leaked name
    ("breast", "BREAST"),                                        # case variant
    ("PLASMA CELL MYELOMA", "PCM"),                              # case variant of a name
    ("Solid tumour AND NOT(MEL)", "Solid tumour AND NOT(MEL)"),  # sentinel untouched
])
def test_normalise_resolves_every_operand_to_a_code(raw, code):
    assert normalise_code_expression(raw) == code


def test_render_name_is_derived_and_round_trips():
    code = "BREAST AND NOT(BRAIN)"
    name = render_name_expression(code)
    assert name == "Breast AND NOT(CNS/Brain)"
    assert normalise_code_expression(name) == code       # names resolve straight back to codes


def test_sentinel_passes_through_name_rendering():
    assert render_name_expression("Solid tumour AND NOT(MEL)") == "Solid tumour AND NOT(Melanoma)"


# --------------------------------------------------------------------------- #
# Canonical form — every rewrite must be meaning-preserving
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raw,expected", [
    ("DIFG AND NOT(DMG) AND NOT(HGGNOS)", "DIFG AND NOT(DMG OR HGGNOS)"),   # factor
    ("NSCLC AND NOT(LUAS OR LUSC)", "NSCLC AND NOT(LUAS OR LUSC)"),         # genuine subtypes kept
    ("MDS OR AML", "AML OR MDS"),                                            # OR branches sorted
    ("(BLCA)", "BLCA"),                                                      # redundant parens
    ("NOT(NOT(BLCA))", "BLCA"),                                              # direct double negation
])
def test_canonical_rewrites(raw, expected):
    assert canonical_form(raw) == expected


def test_canonical_form_is_idempotent():
    for expr in ["DIFG AND NOT(DMG) AND NOT(HGGNOS)", "SKCM AND NOT(UM OR OM)", "AML OR MDS"]:
        once = canonical_form(expr)
        assert canonical_form(once) == once


def test_vacuous_exclusion_dropped_whole_clause():
    assert canonical_form("PAAD AND NOT(PANET)") == "PAAD"


def test_vacuous_exclusion_pruned_branch_by_branch():
    """A real exclusion list is mixed; an all-or-nothing rule keeps the whole thing because of the genuine few."""
    assert canonical_form("MBN AND NOT(PCM OR MDS OR BLL)") == "MBN AND NOT(PCM)"


def test_sentinel_exclusion_survives():
    """A sentinel covers everything, so nothing under it is ever a no-op exclusion."""
    assert canonical_form("Solid tumour AND NOT(MEL)") == "Solid tumour AND NOT(MEL)"


def test_or_branch_subsumed_is_reported_not_rewritten():
    """`MBN OR BL` reduces to `MBN` logically, but WHICH term survives depends on the source wording — the parent
    is often the mapper's error. Report it; let R4 decide."""
    expr = "MBN OR BL"
    assert "log_or_redundant_ancestor" in defects(expr)
    assert canonical_form(expr) == "BL OR MBN"          # sorted, but BL is NOT absorbed into MBN


# --------------------------------------------------------------------------- #
# Detectors — one per error-severity class
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("expr,defect", [
    ("Pancreatic Adenocarcinoma AND NOT(PANET)", "lex_leaked_name"),
    ("NOT(BREAST)", "log_negation_only"),
    ("NOT(Pan-cancer)", "log_negated_sentinel"),
    ("DLBCLNOS AND CLLSLL", "log_disjoint_and"),
    ("PCM AND NOT(DLBCLNOS AND CLLSLL)", "log_disjoint_and_inside_not"),
    ("HGSOC AND NOT(UCEC AND NOT(UEC))", "syn_nested_not"),
    ("GB AND NOT(BRAIN)", "log_exclude_ancestor"),
    ("Solid tumour AND BLCA", "log_sentinel_and_specific"),
    ("LUAD AND NSCLC", "log_subtype_and_parent"),
])
def test_error_detectors_fire(expr, defect):
    assert defect in defects(expr), f"{defect} not detected in {expr!r}"
    assert has_error(expression_problems(expr))


def test_clean_expression_has_no_defects():
    assert defects("LXSC OR OCSC OR OPHSC") == set()
    assert defects("NSCLC AND NOT(LUAS OR LUSC)") == set()


def test_the_prompts_flagship_example_is_faithful_but_reducible():
    """`(OCSC OR OPHSC OR LXSC) AND NOT(NPC)` is the mapper prompt's own reference mapping, and it is CORRECT as a
    faithful translation — NPC is genuinely excluded by the source. But NPC is a sibling of all three, so a patient
    matching any of them is not NPC: the exclusion is a no-op and reconciliation strips it. This separation is the
    point — the mapper translates, the refinement reduces."""
    assert defects("(OCSC OR OPHSC OR LXSC) AND NOT(NPC)") == {
        "syn_or_order_noncanonical", "log_vacuous_exclusion"}
    assert canonical_form("(OCSC OR OPHSC OR LXSC) AND NOT(NPC)") == "LXSC OR OCSC OR OPHSC"


# --------------------------------------------------------------------------- #
# Nested-NOT whitelist
# --------------------------------------------------------------------------- #
def test_whitelisted_nested_differences_are_legal():
    assert "syn_nested_not" not in defects("Pan-cancer AND NOT(SKIN AND NOT(MEL))")
    assert "syn_nested_not" not in defects("Solid tumour AND NOT((NSCLC AND NOT(LUSC)) OR COADREAD)")


def test_non_whitelisted_nesting_still_fails():
    assert "syn_nested_not" in defects("HGSOC AND NOT(UCEC AND NOT(UEC))")


def test_whitelist_is_closed():
    """A new nested difference must FAIL loudly rather than ship into an engine with no carve-out for it."""
    assert ALLOWED_NESTED_DIFFERENCES == frozenset({("SKIN", "MEL"), ("NSCLC", "LUSC")})
    assert "syn_nested_not" in defects("Solid tumour AND NOT(BREAST AND NOT(IBC))")


def test_nested_not_flattens_to_the_engine_form():
    """The mapper writes the FAITHFUL transcription; turning it into the engine's form is mechanical:
        Solid tumour AND NOT(BRAIN AND NOT(GB))              direct mapping
        Solid tumour AND (NOT(BRAIN) OR GB)                  De Morgan
        (Solid tumour AND NOT(BRAIN)) OR (Solid tumour AND GB)   distribute
        (Solid tumour AND NOT(BRAIN)) OR GB                  absorb (GB is inside the excluded scope)
    """
    out = canonical_form("Solid tumour AND NOT(BRAIN AND NOT(GB))")
    assert out == "GB OR (Solid tumour AND NOT(BRAIN))"
    assert defects(out) == set()
    assert canonical_form(out) == out


def test_flattening_drops_unsatisfiable_branches():
    """`HGSOC AND NOT(UCEC AND NOT(UEC))` -> branch 1 `HGSOC AND NOT(UCEC)` (UCEC disjoint -> vacuous -> HGSOC);
    branch 2 `HGSOC AND UEC` (disjoint -> unsatisfiable -> dropped)."""
    assert canonical_form("HGSOC AND NOT(UCEC AND NOT(UEC))") == "HGSOC"
    assert canonical_form("EOV AND NOT(UCEC AND NOT(UEC)) AND NOT(USC OR UCCC OR UCS)") == "EOV"


def test_whitelisted_idioms_are_not_flattened():
    assert canonical_form("Pan-cancer AND NOT(SKIN AND NOT(MEL))") == "Pan-cancer AND NOT(SKIN AND NOT(MEL))"


def test_boolean_absorption_of_subsumed_disjuncts():
    """`X OR (X AND Y)` == `X` — term containment, independent of the hierarchy."""
    assert canonical_form("PCM AND NOT(MDS OR BLL OR (MBN AND NOT(PCM)))") == "PCM"


def test_composite_who_entity_uses_its_own_node():
    """SM-AHN is DEFINED by co-occurrence and has its own code, so ANDing the parts is both unsatisfiable and
    unnecessary."""
    assert "log_composite_entity_split" in defects("SMAHN AND MDSEB")
    assert defects("SMAHN") == set()


def test_double_negated_whitelisted_idiom_is_still_flagged():
    """`NOT(Pan-cancer AND NOT(SKIN AND NOT(MEL)))` embeds a legal idiom inside an illegal outer negation."""
    d = defects("NOT(Pan-cancer AND NOT(SKIN AND NOT(MEL)))")
    assert "syn_nested_not" in d and "log_negated_sentinel" in d
