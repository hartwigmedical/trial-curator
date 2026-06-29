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


def test_default_entries_have_clean_gap_filenames_and_trial_id_context(tmp_path: Path):
    by_name = {e.spec.name: e.spec for e in audit.default_entries(tmp_path / "resources")}
    assert by_name["cancer_type_manual_overwrite"].gap_filename == "cancer_type_gaps.csv"
    assert by_name["gene_alteration_curation"].gap_filename == "gene_alteration_gaps.csv"
    assert (
        by_name["molecular_signature_curation"].gap_filename
        == "molecular_signature_gaps.csv"
    )
    # trial_id is surfaced first in every gap file.
    for spec in by_name.values():
        assert spec.context_cols[0] == "trial_id"


def test_load_generated_union_filters_status_and_renames_keys(tmp_path: Path):
    inter = tmp_path / "ctgov" / "gene_alteration"
    inter.mkdir(parents=True)
    pd.DataFrame(
        {
            "nct_id": ["NCT1", "NCT2"],
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
    # CTGov rows get a coalesced trial_id from nct_id.
    assert df["trial_id"].tolist() == ["NCT1"]


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


def test_load_generated_union_unions_registries_and_coalesces_trial_id(tmp_path: Path):
    # CTGov uses nct_id, ANZCTR uses trial_id; both coalesce into a trial_id col.
    for registry, signature, id_col, id_val in (
        ("ctgov", "msi-high", "nct_id", "NCT1"),
        ("anzctr", "tmb-high", "trial_id", "ACTRN9"),
    ):
        inter = tmp_path / registry / "molecular_signature"
        inter.mkdir(parents=True)
        pd.DataFrame(
            {
                id_col: [id_val],
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
    assert sorted(df["trial_id"]) == ["ACTRN9", "NCT1"]


def test_cancer_type_union_reads_anzctr_table_and_backfills_nct_id(tmp_path: Path):
    # CTGov writes 02_primary_vs_conditions (nct_id); ANZCTR writes
    # 02_primary_vs_health_condition (trial_id, no nct_id). Both must load, and
    # the ANZCTR rows must get nct_id back-filled so coverage keys line up.
    ctgov = tmp_path / "ctgov" / "cancer_type"
    ctgov.mkdir(parents=True)
    pd.DataFrame(
        {
            "nct_id": ["NCT1"],
            "primary_tumor_type": ["carcinoma"],
            "primary_tumor_location": ["lung"],
            "conditions_original": ["NSCLC"],
            "primary_vs_conditions_relation": ["match"],
        }
    ).to_csv(ctgov / "02_primary_vs_conditions.tsv", sep="\t", index=False)

    anzctr = tmp_path / "anzctr" / "cancer_type"
    anzctr.mkdir(parents=True)
    pd.DataFrame(
        {
            "trial_id": ["ACTRN9"],
            "primary_tumor_type": ["melanoma"],
            "primary_tumor_location": ["skin"],
            "conditions_original": ["melanoma"],
            "primary_vs_conditions_relation": ["match"],
        }
    ).to_csv(anzctr / "02_primary_vs_health_condition.tsv", sep="\t", index=False)

    entry = audit.cancer_type_entry(tmp_path / "resources")
    df = audit.load_generated_union(
        entry, intermediates_root=tmp_path, registries=("ctgov", "anzctr")
    )

    # Both registries loaded despite the differing table names.
    assert sorted(df["trial_id"]) == ["ACTRN9", "NCT1"]
    # nct_id (the coverage/resource key) is present for both, back-filled for ANZCTR.
    by_trial = dict(zip(df["trial_id"], df["nct_id"]))
    assert by_trial == {"ACTRN9": "ACTRN9", "NCT1": "NCT1"}


def test_cancer_type_run_report_surfaces_anzctr_gap(tmp_path: Path):
    # An ANZCTR cancer-type trial with no manual_overwrite resource must surface
    # as a fill-ready gap keyed on its trial id in the nct_id column.
    inter = tmp_path / "intermediates"
    anzctr = inter / "anzctr" / "cancer_type"
    anzctr.mkdir(parents=True)
    pd.DataFrame(
        {
            "trial_id": ["ACTRN9"],
            "primary_tumor_type": ["melanoma"],
            "primary_tumor_location": ["skin"],
            "conditions_original": ["melanoma"],
            "primary_vs_conditions_relation": ["match"],
        }
    ).to_csv(anzctr / "02_primary_vs_health_condition.tsv", sep="\t", index=False)

    resources_root = tmp_path / "resources"  # no resource file -> everything uncovered
    gaps_root = inter / "resource_gaps"
    entry = audit.cancer_type_entry(resources_root)

    summaries = audit.run_report(
        [entry],
        intermediates_root=inter,
        gaps_root=gaps_root,
        registries=("anzctr",),
        today_str="29062026",
    )

    assert summaries and summaries[0]["remaining_gaps"] == 1
    gap_path = gaps_root / "version_29062026" / "cancer_type_gaps.csv"
    gap = pd.read_csv(gap_path, dtype=str, keep_default_na=False)
    assert gap["nct_id"].tolist() == ["ACTRN9"]
    assert gap["trial_id"].tolist() == ["ACTRN9"]
    assert gap["manual_overwrite"].tolist() == [""]  # blank value col for the curator


def test_run_report_writes_dated_gap_file_only(tmp_path: Path):
    # One generated intermediate with an uncovered signature.
    inter = tmp_path / "intermediates" / "ctgov" / "molecular_signature"
    inter.mkdir(parents=True)
    pd.DataFrame(
        {
            "nct_id": ["NCT1"],
            "signature_input": ["msi-high"],
            "molecular_signature_curation": [""],
            "mapping_status": ["no_mapping_found"],
            "description_input": ["msi-high"],
        }
    ).to_csv(inter / "02_molecular_signature_mapped_criteria.tsv", sep="\t", index=False)

    resources_root = tmp_path / "resources"
    intermediates_root = tmp_path / "intermediates"
    gaps_root = intermediates_root / "resource_gaps"
    entry = audit.molecular_signature_entry(resources_root)

    summaries = audit.run_report(
        [entry],
        intermediates_root=intermediates_root,
        gaps_root=gaps_root,
        registries=("ctgov",),
        today_str="27062026",
    )

    assert summaries and summaries[0]["remaining_gaps"] == 1
    gap_path = gaps_root / "version_27062026" / "molecular_signature_gaps.csv"
    assert gap_path.exists()
    # No verbose report and no resource_audit diagnostics folder.
    assert list(tmp_path.glob("**/*_coverage_gaps.tsv")) == []
    assert not (intermediates_root / "resource_audit").exists()

    gap = pd.read_csv(gap_path, dtype=str, keep_default_na=False)
    assert "coverage_status" not in gap.columns
    assert gap["Signature_lookup"].tolist() == ["msi-high"]
    assert gap["trial_id"].tolist() == ["NCT1"]
    assert gap["Findings_curation"].tolist() == [""]  # blank value col
