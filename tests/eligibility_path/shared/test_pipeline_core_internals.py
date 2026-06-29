"""Coverage for previously-untested internals of the eligibility cores.

- term tokeniser bracket/paren protection + NOT() idempotency (exercised via the
  public ``collapse_to_trial_level`` so the tests survive helper relocation);
- the molecular-signature node walker that produces the ``under_not`` flag the
  polarity logic consumes;
- ``blank_safe_str`` (the newly-extracted None/NA guard);
- the ``fail_on_error`` swallow-vs-raise fork in ``build_mapped_criteria_table``.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from aus_trial_universe.eligibility_path.shared.gene_alterations import (
    gene_alteration_pipeline_core as ga,
)
from aus_trial_universe.eligibility_path.shared.molecular_signature import (
    molecular_signature_pipeline_core as ms,
)
from aus_trial_universe.eligibility_path.shared.utils.text_normalisation import (
    blank_safe_str,
)


# --- term tokeniser: top-level OR must not split inside [] or () -------------

def test_ga_collapse_keeps_or_inside_brackets_intact():
    df = pd.DataFrame([{
        "nct_id": "NCT1", "polarity": "inclusive",
        "gene_alteration_curation": "Fusion[geneA|geneB] | SmallVariant[gene=KRAS]",
    }])
    out = ga.collapse_to_trial_level(df, trial_id_column="nct_id")
    # If the inner '|' were split, rejoining would add spaces / extra terms.
    assert out.loc[0, "gene_alteration_inclusive"] == "Fusion[geneA|geneB] | SmallVariant[gene=KRAS]"


def test_ms_collapse_keeps_or_inside_parens_intact():
    df = pd.DataFrame([{
        "nct_id": "NCT1", "polarity": "inclusive",
        "molecular_signature_curation": "Signature(a|b) | MSI[status=HIGH]",
    }])
    out = ms.collapse_to_trial_level(df, trial_id_column="nct_id")
    assert out.loc[0, "molecular_signature_inclusive"] == "Signature(a|b) | MSI[status=HIGH]"


def test_collapse_wrap_not_is_idempotent_for_already_negated_terms():
    df = pd.DataFrame([{
        "nct_id": "NCT1", "polarity": "exclusive",
        "gene_alteration_curation": "NOT(SmallVariant[gene=TP53])",
    }])
    out = ga.collapse_to_trial_level(df, trial_id_column="nct_id")
    assert out.loc[0, "gene_alteration_exclusive"] == "NOT(SmallVariant[gene=TP53])"


# --- molecular-signature node walker -----------------------------------------

# Class names must match what the walker keys on: it yields nodes whose type is
# named "MolecularSignatureCriterion" and flags under_not when crossing a node
# whose name is "NotCriterion".
class MolecularSignatureCriterion:
    def __init__(self, signature):
        self.signature = signature


class NotCriterion:
    def __init__(self, criterion):
        self.criterion = criterion


class AndCriterion:
    def __init__(self, criteria):
        self.criteria = criteria


def test_node_walker_propagates_under_not_and_finds_nested_nodes():
    msi = MolecularSignatureCriterion("MSI")
    tmb = MolecularSignatureCriterion("TMB")
    rule = AndCriterion([msi, NotCriterion(tmb)])

    found = list(ms.iter_molecular_signature_nodes(rule, rule_index=1))
    under_by_signature = {node.signature: under_not for (node, under_not, _path) in found}
    assert under_by_signature == {"MSI": False, "TMB": True}


def test_node_walker_is_cycle_safe():
    node = MolecularSignatureCriterion("X")
    node.self_ref = node  # introduce a cycle
    found = list(ms.iter_molecular_signature_nodes(node, rule_index=1))
    assert len(found) == 1


# --- blank_safe_str: None/NA guard with NO stripping/casefold ----------------

def test_blank_safe_str_contract():
    assert blank_safe_str(None) == ""
    assert blank_safe_str(float("nan")) == ""
    assert blank_safe_str("  Keep Spaces  ") == "  Keep Spaces  "  # no strip
    assert blank_safe_str("MixedCase") == "MixedCase"             # no casefold
    assert blank_safe_str(42) == "42"
    # a value whose pd.isna() is non-scalar must not raise
    assert blank_safe_str(["a", "b"]) == str(["a", "b"])


# --- build_mapped_criteria_table fail_on_error fork --------------------------

def _ms_spec():
    return ms.MolecularSignatureRegistrySpec(
        registry_label="Test",
        trial_id_column="nct_id",
        trial_id_prefix="NCT",
        default_eligibility_data_dir=Path("."),
        default_curated_dir=Path("."),
        normalize_trial_id_from_stem=lambda stem: str(stem).upper(),
    )


def test_build_mapped_criteria_table_swallows_or_raises_per_fail_on_error(monkeypatch):
    monkeypatch.setattr(ms, "build_molecular_signature_map", lambda _f: {})
    monkeypatch.setattr(ms, "iter_curated_py_files", lambda _d, trial_id_prefix=None: [Path("NCT1.py")])

    def _boom(_path):
        raise RuntimeError("corrupt curated file")

    monkeypatch.setattr(ms, "load_curated_rules", _boom)
    spec = _ms_spec()

    # fail_on_error=False: the bad file is skipped, result is an empty but well-formed frame.
    df = ms.build_mapped_criteria_table(
        spec=spec, curated_dir=Path("x"), mapping_resource_file=Path("m"), fail_on_error=False,
    )
    assert df.empty
    assert "nct_id" in df.columns
    assert "molecular_signature_curation" in df.columns

    # fail_on_error=True: the loader error propagates.
    with pytest.raises(RuntimeError):
        ms.build_mapped_criteria_table(
            spec=spec, curated_dir=Path("x"), mapping_resource_file=Path("m"), fail_on_error=True,
        )
