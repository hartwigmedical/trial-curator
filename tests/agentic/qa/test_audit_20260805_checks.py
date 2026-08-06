"""The checks added by the 2026-08-05 mapping audit, each pinned to the real defect that motivated it.

Every case here is a value that actually shipped. The point of the fixtures is that if someone later relaxes a
check, the test names say exactly which real-world defect would ship again.
"""
from __future__ import annotations

from aus_trial_universe.qa import adjudications
from aus_trial_universe.tasks.eligibility.mapping.cancer_type.checks import (
    CATCHALL_NODES,
    check_against_source,
    check_expression,
)
from aus_trial_universe.tasks.eligibility.mapping.gene_alteration.checks import semantic_problems


def _defects(source: str, expr: str) -> list[str]:
    return [f.defect for f in check_against_source(source, expr).findings]


# --------------------------------------------------------------------------- log_unjustified_sentinel
def test_sentinel_flagged_when_the_source_names_a_specific_entity():
    """`recurrent/metastatic adenoid cystic carcinoma` -> `Solid tumour`: ACYC thrown away."""
    src = "recurrent and/or metastatic adenoid cystic carcinoma with disease progression within 12 months"
    assert "log_unjustified_sentinel" in _defects(src, "Solid tumour")


def test_sentinel_flagged_for_the_teratoma_values():
    assert "log_unjustified_sentinel" in _defects("mature teratoma", "Solid tumour")
    assert "log_unjustified_sentinel" in _defects("immature teratoma", "Solid tumour")


def test_sentinel_allowed_when_the_named_type_is_one_example_of_a_basket():
    """A basket trial NAMES types as examples; the sentinel is then correct. Without this carve-out the check
    fires on 11 values of which only 4 are defects."""
    for src in ("hematologic malignancies (including but not limited to acute myeloid leukemia (AML))",
                "Tumor types such as colorectal cancer, renal cell carcinoma, etc.",
                "solid tumors, including non-small cell lung cancer",
                "advanced or metastatic solid tumors other than non-small cell lung cancer"):
        assert "log_unjustified_sentinel" not in _defects(src, "Solid tumour"), src


def test_sentinel_allowed_when_the_type_is_named_only_in_an_exclusion():
    """`solid tumour AND NOT(hepatocellular carcinoma)` names HCC in the NOT() — that is not a positive type."""
    src = "advanced malignant solid tumor AND NOT(hepatocellular carcinoma)"
    assert "log_unjustified_sentinel" not in _defects(src, "Solid tumour AND NOT(HCC)")


def test_no_finding_once_the_mapping_is_specific():
    src = "recurrent and/or metastatic adenoid cystic carcinoma with disease progression within 12 months"
    assert _defects(src, "ACYC") == []


def test_generic_source_wording_is_not_treated_as_naming_a_type():
    """'malignant tumour' matches OncoTree's MT node by name; that must not count as naming a specific type."""
    assert _defects("advanced malignant solid tumour", "Solid tumour") == []


# --------------------------------------------------------------------------- lex_catchall_node / MT
def test_mt_is_a_catchall_bucket():
    """A paediatric BRAIN tumour was mapped to MT ('Malignant Tumor'); MT was missing from the ban list, so the
    error-severity gate never fired."""
    assert "MT" in CATCHALL_NODES
    assert [f.defect for f in check_expression("MT").findings] == ["lex_catchall_node"]
    assert all(f.severity == "error" for f in check_expression("MT").findings)


# --------------------------------------------------------------------------- acronym_as_gene
def test_aga_is_an_acronym_not_a_gene():
    """`AGA negative` in an NSCLC trial means ACTIONABLE GENOMIC ALTERATION. AGA is also a real HGNC symbol
    (aspartylglucosaminidase), so no vocabulary check could catch it — hence the explicit ban."""
    found = semantic_problems("AGA negative", "Wildtype[gene=AGA]")
    assert [f.check for f in found] == ["acronym_as_gene"]
    assert found[0].severity == "error"


def test_a_merely_unattested_symbol_still_only_warns():
    """A genuinely new gene must be able to appear, so PTPRZ1 (real, unattested here) may not gate."""
    found = semantic_problems("MET fusion, including PTPRZ1-MET fusion", "Fusion[geneStart=PTPRZ1 & geneEnd=MET]")
    assert [f.check for f in found] == ["unknown_gene_symbol"]
    assert found[0].severity == "warn"


# --------------------------------------------------------------------------- the registers
def test_both_registers_are_populated_and_wellformed():
    # ⚠ These are FLOORS on the entry count, which will break when the register is CLEANED: every entry is an
    # admission that a prompt or rule is too weak, so the register is meant to shrink. Retiring the 21 cancer_type
    # entries the refined stage-1 prompt now reaches unaided drops this below 28 — i.e. it fails on success. Relax
    # to a non-empty check at that point.
    assert len(adjudications.for_column("cancer_type")) >= 28
    assert len(adjudications.for_column("gene_alteration")) >= 2
    # molecular_signature is deliberately empty, but the register must EXIST so for_column needs no special case
    assert adjudications.for_column("molecular_signature") == {}
    assert adjudications.for_column("nonsense") == {}
    assert set(adjudications.all_rulings()) == {"cancer_type", "gene_alteration", "molecular_signature"}
    for column, register in adjudications.all_rulings().items():
        for key, ruling in register.items():
            assert ruling.value == key, column
            assert ruling.rationale.strip() and ruling.approved.strip(), column


def test_an_empty_string_is_a_legitimate_gene_ruling():
    """`AGA negative` -> '' is the FIX, so callers must test `is not None`, never truthiness."""
    ruling = adjudications.for_column("gene_alteration")["AGA negative"]
    assert ruling.final == ""
    assert ruling is not None


def test_every_approved_cancer_type_final_is_clean_and_canonical():
    """A ruling is applied AFTER the deterministic pass and is never re-canonicalised, so it must already be
    correct — a ruling that carries an error-severity defect would ship one."""
    from aus_trial_universe.tasks.eligibility.mapping.cancer_type.vocab import canonical_form
    for key, ruling in adjudications.for_column("cancer_type").items():
        final = ruling.final
        if not final:
            continue
        errors = [f.defect for f in check_expression(final).findings if f.severity == "error"]
        assert not errors, f"{key[:60]!r} -> {final!r}: {errors}"
        assert canonical_form(final) == final, f"{key[:60]!r} -> {final!r} is not canonical"


def test_every_approved_gene_final_passes_the_grammar():
    from aus_trial_universe.tasks.eligibility.mapping.finding_model import finding_model_problems
    for key, ruling in adjudications.for_column("gene_alteration").items():
        if ruling.final:
            assert not finding_model_problems(ruling.final), key
