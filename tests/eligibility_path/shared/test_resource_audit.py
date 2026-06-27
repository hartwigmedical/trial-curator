from __future__ import annotations

from pathlib import Path

import pandas as pd

from aus_trial_universe.eligibility_path.shared.resource_curation import audit


def test_default_entries_cover_all_three_domains(tmp_path: Path):
    names = {entry.spec.name for entry in audit.default_entries(tmp_path / "resources")}
    assert names == {
        "cancer_type_manual_overwrite",
        "gene_alteration_curation",
        "molecular_signature_curation",
    }


def test_load_generated_union_filters_status_and_renames_keys(tmp_path: Path):
    inter = tmp_path / "ctgov" / "gene_alteration"
    inter.mkdir(parents=True)
    pd.DataFrame(
        {
            "gene_input": ["EGFR", "KRAS"],
            "alteration_input": ["mutation", "mutation"],
            "variant_input": ["T790M", "G12C"],
            "mapping_status": ["no_mapping_found", "mapped_exact"],
            "input_text": ["a", "b"],
            "description_input": ["da", "db"],
        }
    ).to_csv(inter / "03_gene_alteration_mapped_criteria.tsv", sep="\t", index=False)

    entry = audit.gene_alteration_entry(tmp_path / "resources")
    df = audit.load_generated_union(entry, intermediates_root=tmp_path, registries=("ctgov",))

    # Only the no_mapping_found gap is kept, with *_input renamed to *_lookup.
    assert df["Gene_lookup"].tolist() == ["EGFR"]
    assert "Alteration_lookup" in df.columns and "Variant_lookup" in df.columns
    assert "gene_input" not in df.columns


def test_gene_accretion_transform_fills_only_blank_mapping_args(monkeypatch):
    from aus_trial_universe.eligibility_path.shared.gene_alterations.mapping import (
        generate_gene_alteration_mapping as gen,
    )

    monkeypatch.setattr(gen, "map_row_to_args", lambda row: f"ARGS[{row['Gene_lookup']}]")
    monkeypatch.setattr(gen, "postprocess_args_mapping", lambda s: s)

    df = pd.DataFrame(
        {
            "Gene_lookup": ["EGFR", "KRAS"],
            "Alteration_lookup": ["mutation", "mutation"],
            "Variant_lookup": ["T790M", "G12C"],
            "Gene_curation": ["EGFR", "KRAS"],
            "Mapping_args": ["ALREADY", ""],  # first already curated, second blank
        }
    )

    out = audit._populate_gene_alteration_mapping_args(df)

    assert out.loc[0, "Mapping_args"] == "ALREADY"  # existing left untouched
    assert out.loc[1, "Mapping_args"] == "ARGS[KRAS]"  # regenerated for the blank row


def test_load_generated_union_unions_registries(tmp_path: Path):
    for registry, signature in (("ctgov", "msi-high"), ("anzctr", "tmb-high")):
        inter = tmp_path / registry / "molecular_signature"
        inter.mkdir(parents=True)
        pd.DataFrame(
            {
                "signature_input": [signature],
                "molecular_signature_curation": [""],
                "mapping_status": ["no_mapping_found"],
                "description_input": [signature],
            }
        ).to_csv(inter / "02_molecular_signature_mapped_criteria.tsv", sep="\t", index=False)

    entry = audit.molecular_signature_entry(tmp_path / "resources")
    df = audit.load_generated_union(
        entry, intermediates_root=tmp_path, registries=("ctgov", "anzctr")
    )

    assert sorted(df["Signature_lookup"]) == ["msi-high", "tmb-high"]
