from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

from aus_trial_universe.eligibility_path.shared.resource_curation import coverage as cov
from aus_trial_universe.eligibility_path.shared.resource_curation.coverage import (
    CoverageSpec,
    accrete_filled_template,
    accrete_resource,
    audit_resource,
    build_fill_ready_template,
    filled_template_rows,
    report_coverage,
    split_coverage,
)


def _cancer_spec(resource_dir: Path) -> CoverageSpec:
    # Cancer-type style: gap at trial level (nct_id) but resource keyed per row.
    return CoverageSpec(
        name="cancer_manual_overwrite",
        resource_dir=resource_dir,
        resource_stem="manual_overwrite",
        resource_suffix=".csv",
        resource_tokens=(("manual",), ("overwrite",)),
        coverage_key_cols=("nct_id",),
        resource_key_cols=("nct_id", "primary_tumor_type", "conditions_original"),
        value_cols=("manual_overwrite",),
        context_cols=("auto_determination",),
    )


def _mapping_spec(resource_dir: Path) -> CoverageSpec:
    # Value-mapping style: coverage key == resource key == the value pair.
    return CoverageSpec(
        name="signature_mapping",
        resource_dir=resource_dir,
        resource_stem="MolecularSignatureCurationResource",
        resource_suffix=".csv",
        resource_tokens=(("molecularsignature",), ("curationresource",)),
        coverage_key_cols=("Signature_lookup",),
        value_cols=("Findings_curation",),
    )


def test_split_coverage_uses_coverage_key_not_resource_key(tmp_path: Path):
    spec = _cancer_spec(tmp_path)
    generated = pd.DataFrame(
        {
            "nct_id": ["NCT1", "NCT2", "NCT2", "NCT3"],
            "primary_tumor_type": ["lung", "breast", "colon", "skin"],
            "conditions_original": ["a", "b", "c", "d"],
            "auto_determination": ["lung ca", "breast ca", "colon ca", "skin ca"],
        }
    )
    # Resource covers NCT1 only (by nct_id).
    resource = pd.DataFrame(
        {
            "nct_id": ["NCT1", "NCT9"],
            "primary_tumor_type": ["lung", "x"],
            "conditions_original": ["a", "x"],
            "manual_overwrite": ["KEEP", "OLD"],
        }
    )

    uncovered, stale = split_coverage(generated, resource, spec.coverage_key_cols)

    # NCT2 (x2) + NCT3 uncovered; NCT1 covered at trial level
    assert sorted(uncovered["nct_id"]) == ["NCT2", "NCT2", "NCT3"]
    # NCT9 resource row no longer matches any generated trial
    assert stale["nct_id"].tolist() == ["NCT9"]


def test_build_fill_ready_template_one_blank_row_per_resource_key(tmp_path: Path):
    spec = _cancer_spec(tmp_path)
    uncovered = pd.DataFrame(
        {
            "nct_id": ["NCT2", "NCT2", "NCT3"],
            "primary_tumor_type": ["breast", "breast", "skin"],
            "conditions_original": ["b", "b", "d"],  # NCT2/breast/b duplicated
            "auto_determination": ["breast ca", "breast ca", "skin ca"],
        }
    )

    template = build_fill_ready_template(uncovered, spec)

    # de-duplicated to one row per resource key (NCT2 row collapses)
    assert len(template) == 2
    assert list(template.columns) == [
        "nct_id",
        "primary_tumor_type",
        "conditions_original",
        "auto_determination",
        "manual_overwrite",
    ]
    assert template["manual_overwrite"].tolist() == ["", ""]  # blank for curator
    assert template["auto_determination"].tolist() == ["breast ca", "skin ca"]


def test_filled_template_rows_detects_nonblank_value():
    template = pd.DataFrame(
        {
            "nct_id": ["NCT2", "NCT3"],
            "manual_overwrite": ["FILLED", "  "],  # whitespace counts as blank
        }
    )
    filled = filled_template_rows(template, ("manual_overwrite",))
    assert filled["nct_id"].tolist() == ["NCT2"]


def test_accrete_resource_appends_and_prefers_filled_value(tmp_path: Path):
    spec = _mapping_spec(tmp_path)
    resource = pd.DataFrame(
        {"Signature_lookup": ["msi-high"], "Findings_curation": ["OLD"]}
    )
    filled = pd.DataFrame(
        {
            "Signature_lookup": ["tmb-high", "msi-high"],  # new + override existing
            "Findings_curation": ["NEW_TMB", "OVERRIDE"],
        }
    )

    merged = accrete_resource(resource, filled, spec)

    by_key = dict(zip(merged["Signature_lookup"], merged["Findings_curation"]))
    assert by_key["tmb-high"] == "NEW_TMB"
    assert by_key["msi-high"] == "OVERRIDE"  # filled wins (keep last)
    assert len(merged) == 2  # no fan-out


def test_latest_resource_path_picks_newest_by_mtime(tmp_path: Path):
    spec = _mapping_spec(tmp_path)
    old = tmp_path / "MolecularSignatureCurationResource_13042026.csv"
    new = tmp_path / "MolecularSignatureCurationResource_27062026.csv"
    pd.DataFrame({"Signature_lookup": ["a"], "Findings_curation": ["x"]}).to_csv(old, index=False)
    pd.DataFrame({"Signature_lookup": ["b"], "Findings_curation": ["y"]}).to_csv(new, index=False)
    os.utime(old, (1.0, 1.0))
    os.utime(new, (2.0, 2.0))

    assert cov.latest_resource_path(spec) == new


def test_report_coverage_writes_gap_report_only(tmp_path: Path):
    spec = _cancer_spec(tmp_path)
    resource = pd.DataFrame(
        {
            "nct_id": ["NCT1"],
            "primary_tumor_type": ["lung"],
            "conditions_original": ["a"],
            "manual_overwrite": ["KEEP"],
        }
    )
    generated = pd.DataFrame(
        {
            "nct_id": ["NCT1", "NCT2"],
            "primary_tumor_type": ["lung", "breast"],
            "conditions_original": ["a", "b"],
            "auto_determination": ["lung ca", "breast ca"],
        }
    )
    diagnostics = tmp_path / "diagnostics"

    result = report_coverage(spec, generated, diagnostics_dir=diagnostics, resource_df=resource)

    assert result.uncovered_rows == 1
    assert result.uncovered_keys == 1
    assert result.gap_report_path.exists()
    # report_coverage is read-only: the template is owned by the audit step.
    assert result.template_path is None
    assert not spec.template_path.exists()
    report = pd.read_csv(result.gap_report_path, sep="\t", dtype=str, keep_default_na=False)
    uncovered = report[report["coverage_status"] == "uncovered"]
    assert uncovered["nct_id"].tolist() == ["NCT2"]


def test_audit_resource_accretes_fills_then_rebuilds_template(tmp_path: Path):
    spec = _cancer_spec(tmp_path)
    original = tmp_path / "manual_overwrite_01012026.csv"
    pd.DataFrame(
        {
            "nct_id": ["NCT1"],
            "primary_tumor_type": ["lung"],
            "conditions_original": ["a"],
            "manual_overwrite": ["KEEP"],
        }
    ).to_csv(original, index=False)
    original_bytes = original.read_bytes()

    generated = pd.DataFrame(
        {
            "nct_id": ["NCT1", "NCT2", "NCT3"],
            "primary_tumor_type": ["lung", "breast", "skin"],
            "conditions_original": ["a", "b", "d"],
            "auto_determination": ["lung ca", "breast ca", "skin ca"],
        }
    )
    diagnostics = tmp_path / "diagnostics"

    # First audit: no fills yet -> no new version; template lists both gaps.
    audit_resource(spec, generated, diagnostics_dir=diagnostics, today_str="27062026")
    assert not (tmp_path / "manual_overwrite_27062026.csv").exists()
    assert (diagnostics / "manual_overwrite_coverage_gaps.tsv").exists()
    template = pd.read_csv(spec.template_path, dtype=str, keep_default_na=False)
    assert sorted(template["nct_id"]) == ["NCT2", "NCT3"]

    # Curator fills NCT2.
    template.loc[template["nct_id"] == "NCT2", "manual_overwrite"] = "CURATED"
    template.to_csv(spec.template_path, index=False)

    # Second audit: NCT2 graduates into a NEW resource version; template keeps NCT3.
    summary = audit_resource(spec, generated, diagnostics_dir=diagnostics, today_str="27062026")

    new_version = tmp_path / "manual_overwrite_27062026.csv"
    assert new_version.exists()
    assert original.read_bytes() == original_bytes  # original never overwritten
    merged = pd.read_csv(new_version, dtype=str, keep_default_na=False)
    assert dict(zip(merged["nct_id"], merged["manual_overwrite"])) == {
        "NCT1": "KEEP",
        "NCT2": "CURATED",
    }
    template_after = pd.read_csv(spec.template_path, dtype=str, keep_default_na=False)
    assert template_after["nct_id"].tolist() == ["NCT3"]
    assert summary["remaining_gaps"] == 1


def test_accrete_filled_template_creates_new_version_without_overwriting(tmp_path: Path):
    spec = _cancer_spec(tmp_path)
    original = tmp_path / "manual_overwrite_01012026.csv"
    pd.DataFrame(
        {
            "nct_id": ["NCT1"],
            "primary_tumor_type": ["lung"],
            "conditions_original": ["a"],
            "manual_overwrite": ["KEEP"],
        }
    ).to_csv(original, index=False)
    original_bytes = original.read_bytes()

    # Curator filled a gap in the template.
    spec.template_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "nct_id": ["NCT2"],
            "primary_tumor_type": ["breast"],
            "conditions_original": ["b"],
            "auto_determination": ["breast ca"],
            "manual_overwrite": ["CURATED_VALUE"],
        }
    ).to_csv(spec.template_path, index=False)

    new_path = accrete_filled_template(spec, today_str="27062026")

    assert new_path == tmp_path / "manual_overwrite_27062026.csv"
    assert new_path.exists()
    assert original.read_bytes() == original_bytes  # never overwritten
    merged = pd.read_csv(new_path, dtype=str, keep_default_na=False)
    by_nct = dict(zip(merged["nct_id"], merged["manual_overwrite"]))
    assert by_nct == {"NCT1": "KEEP", "NCT2": "CURATED_VALUE"}


def test_accrete_filled_template_applies_transform_before_write(tmp_path: Path):
    spec = _mapping_spec(tmp_path)
    (tmp_path / "MolecularSignatureCurationResource_01012026.csv").write_text(
        "Signature_lookup,Findings_curation\nmsi,OLD\n", encoding="utf-8"
    )
    spec.template_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"Signature_lookup": ["tmb"], "Findings_curation": ["NEW"]}).to_csv(
        spec.template_path, index=False
    )

    new_path = accrete_filled_template(
        spec,
        today_str="27062026",
        transform=lambda df: df.assign(
            Findings_curation=df["Findings_curation"].astype(str) + "!"
        ),
    )

    merged = pd.read_csv(new_path, dtype=str, keep_default_na=False)
    assert set(merged["Findings_curation"]) == {"OLD!", "NEW!"}


def test_accrete_filled_template_noop_when_nothing_filled(tmp_path: Path):
    spec = _cancer_spec(tmp_path)
    spec.template_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "nct_id": ["NCT2"],
            "primary_tumor_type": ["breast"],
            "conditions_original": ["b"],
            "auto_determination": ["breast ca"],
            "manual_overwrite": [""],  # not filled
        }
    ).to_csv(spec.template_path, index=False)

    assert accrete_filled_template(spec, today_str="27062026") is None
    # no new resource version created
    assert list(tmp_path.glob("manual_overwrite_*.csv")) == []
