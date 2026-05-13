from __future__ import annotations

from pathlib import Path

import pytest

from aus_trial_universe.ctgov.drug_ontology.knowledgebase.topograph.topograph import (
    evidence_direction_for_tier,
    normalize_topograph_text,
    read_topograph_master_tsv,
    split_plus_components,
    split_semicolon_values,
)


def write_sample_topograph(path: Path) -> None:
    path.write_text(
        "\t".join(["Tier", "Biomarker", "Alteration", "Tumour Type", "Drugs", "Comments", "Evidence"])
        + "\n"
        + "\t".join(
            [
                "4",
                "BAP1",
                "Oncogenic mutations",
                "Solid tumours",
                "PARP inhibitor; Niraparib",
                "Example comment",
                "12345678",
            ]
        )
        + "\n"
        + "\t".join(
            [
                "1",
                "EGFR",
                "Exon 19 deletion, L858R",
                "Non-small cell lung cancer",
                "Afatinib + Bevacizumab",
                "Combination example",
                "23456789, 34567890",
            ]
        )
        + "\n"
        + "\t".join(
            [
                "R1",
                "KRAS",
                "G12C",
                "Colorectal cancer",
                "Cetuximab",
                "Resistance example",
                "45678901",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_split_semicolon_values_expands_alternative_therapy_options():
    assert split_semicolon_values("PARP inhibitor; Niraparib") == ["PARP inhibitor", "Niraparib"]


def test_split_plus_components_preserves_combination_as_components():
    assert split_plus_components("Afatinib + Bevacizumab") == ["Afatinib", "Bevacizumab"]


def test_evidence_direction_for_resistance_tiers():
    assert evidence_direction_for_tier("R1") == "resistance_or_lack_of_activity"
    assert evidence_direction_for_tier("R2") == "resistance_or_lack_of_activity"
    assert evidence_direction_for_tier("1B") == "sensitivity_or_activity"
    assert evidence_direction_for_tier("4") == "sensitivity_or_activity"


def test_read_topograph_master_tsv_builds_three_grains(tmp_path: Path):
    input_tsv = tmp_path / "TOPOGRAPH-master.tsv"
    write_sample_topograph(input_tsv)

    records = read_topograph_master_tsv(input_tsv, source_version="Topograph_test")

    assert len(records.raw_assertions) == 3
    assert len(records.therapy_options) == 4
    assert len(records.therapy_components) == 5

    option_by_raw = {o.therapy_option_raw: o for o in records.therapy_options}
    assert option_by_raw["PARP inhibitor"].therapy_option_type == "monotherapy"
    assert option_by_raw["Niraparib"].therapy_option_type == "monotherapy"
    assert option_by_raw["Afatinib + Bevacizumab"].therapy_option_type == "combination"
    assert option_by_raw["Afatinib + Bevacizumab"].component_count == 2

    combo_components = [
        c.component_raw
        for c in records.therapy_components
        if c.therapy_option_key == option_by_raw["Afatinib + Bevacizumab"].therapy_option_key
    ]
    assert combo_components == ["Afatinib", "Bevacizumab"]

    resistance = next(r for r in records.raw_assertions if r.tier == "R1")
    assert resistance.evidence_direction == "resistance_or_lack_of_activity"


def test_read_topograph_master_tsv_rejects_unexpected_columns(tmp_path: Path):
    bad_tsv = tmp_path / "bad.tsv"
    bad_tsv.write_text("Tier\tBiomarker\tDrugs\n1\tEGFR\tAfatinib\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Unexpected TOPOGRAPH columns"):
        read_topograph_master_tsv(bad_tsv, source_version="bad")


def test_normalize_topograph_text_is_exact_match_oriented():
    assert normalize_topograph_text("  Afatinib\n") == normalize_topograph_text("Afatinib")
