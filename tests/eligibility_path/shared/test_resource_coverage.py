from __future__ import annotations

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
    gap_template_path,
    latest_gap_file,
    refresh_coverage,
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
        gap_filename="cancer_type_gaps.csv",
        context_cols=("trial_id", "auto_determination"),
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
        gap_filename="molecular_signature_gaps.csv",
        context_cols=("trial_id",),
    )


def _write_gap(gaps_root: Path, spec: CoverageSpec, today_str: str, df: pd.DataFrame) -> Path:
    path = gap_template_path(gaps_root, spec, today_str)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return path


def test_build_fill_ready_template_tolerates_missing_resource_key_col(tmp_path: Path):
    spec = _cancer_spec(tmp_path / "cancer_type")
    # Generated rows lack a resource_key column (primary_tumor_type): the template
    # must still build (blank for the missing col) without raising KeyError in the
    # dedup, which keys on every resource_key col.
    uncovered = pd.DataFrame(
        [
            {"nct_id": "NCT00000001", "conditions_original": "melanoma"},
            {"nct_id": "NCT00000002", "conditions_original": "lung"},
        ]
    )
    template = build_fill_ready_template(uncovered, spec)
    assert list(template.columns) == [
        "nct_id",
        "primary_tumor_type",
        "conditions_original",
        "trial_id",
        "auto_determination",
        "manual_overwrite",
    ]
    assert (template["primary_tumor_type"] == "").all()
    assert (template["manual_overwrite"] == "").all()
    assert template["nct_id"].tolist() == ["NCT00000001", "NCT00000002"]


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
            "trial_id": ["NCT2", "NCT2", "NCT3"],
            "primary_tumor_type": ["breast", "breast", "skin"],
            "conditions_original": ["b", "b", "d"],  # NCT2/breast/b duplicated
            "auto_determination": ["breast ca", "breast ca", "skin ca"],
        }
    )

    template = build_fill_ready_template(uncovered, spec)

    # de-duplicated to one row per resource key (NCT2 row collapses)
    assert len(template) == 2
    # resource keys, then context cols (trial_id first), then blank value cols
    assert list(template.columns) == [
        "nct_id",
        "primary_tumor_type",
        "conditions_original",
        "trial_id",
        "auto_determination",
        "manual_overwrite",
    ]
    assert "coverage_status" not in template.columns
    assert template["manual_overwrite"].tolist() == ["", ""]  # blank for curator
    assert template["auto_determination"].tolist() == ["breast ca", "skin ca"]
    assert template["trial_id"].tolist() == ["NCT2", "NCT3"]


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


def test_latest_resource_path_picks_newest_by_date(tmp_path: Path):
    import os

    spec = _mapping_spec(tmp_path)
    earlier_date = tmp_path / "MolecularSignatureCurationResource_13042026.csv"
    later_date = tmp_path / "MolecularSignatureCurationResource_27062026.csv"
    pd.DataFrame({"Signature_lookup": ["a"], "Findings_curation": ["x"]}).to_csv(earlier_date, index=False)
    pd.DataFrame({"Signature_lookup": ["b"], "Findings_curation": ["y"]}).to_csv(later_date, index=False)
    # Give the EARLIER-dated file the NEWER mtime; date-based selection must still pick the later
    # date (mtime alone would be fooled into picking the earlier-dated file).
    os.utime(later_date, (1.0, 1.0))
    os.utime(earlier_date, (2.0, 2.0))

    assert cov.latest_resource_path(spec) == later_date


def test_latest_gap_file_picks_newest_version_dir(tmp_path: Path):
    spec = _cancer_spec(tmp_path)
    gaps_root = tmp_path / "resource_gaps"
    blank = pd.DataFrame(
        {
            "nct_id": ["NCT2"],
            "primary_tumor_type": ["breast"],
            "conditions_original": ["b"],
            "trial_id": ["NCT2"],
            "auto_determination": ["breast ca"],
            "manual_overwrite": [""],
        }
    )
    older = _write_gap(gaps_root, spec, "13042026", blank)
    newer = _write_gap(gaps_root, spec, "27062026", blank)

    assert older.exists() and newer.exists()
    assert latest_gap_file(gaps_root, spec) == newer
    # missing root / missing file -> None
    assert latest_gap_file(tmp_path / "missing", spec) is None


def test_refresh_coverage_writes_dated_gap_template_no_stale(tmp_path: Path):
    spec = _cancer_spec(tmp_path)
    gaps_root = tmp_path / "resource_gaps"
    resource = pd.DataFrame(
        {
            "nct_id": ["NCT1", "NCT9"],  # NCT9 no longer generated (would be stale)
            "primary_tumor_type": ["lung", "x"],
            "conditions_original": ["a", "x"],
            "manual_overwrite": ["KEEP", "OLD"],
        }
    )
    generated = pd.DataFrame(
        {
            "nct_id": ["NCT1", "NCT2"],
            "trial_id": ["NCT1", "NCT2"],
            "primary_tumor_type": ["lung", "breast"],
            "conditions_original": ["a", "b"],
            "auto_determination": ["lung ca", "breast ca"],
        }
    )

    summary = refresh_coverage(
        spec, generated, gaps_root, today_str="27062026", resource_df=resource
    )

    assert summary["remaining_gaps"] == 1
    assert "stale_rows" not in summary  # stale notion dropped

    gap_path = gaps_root / "version_27062026" / "cancer_type_gaps.csv"
    assert gap_path.exists()
    # NOT in resources/, NOT a _coverage_gaps.tsv, NOT a resource_audit folder
    assert list(tmp_path.glob("**/*_coverage_gaps.tsv")) == []
    assert not (tmp_path / "gaps").exists()
    assert not (gaps_root / "resource_audit").exists()

    gap = pd.read_csv(gap_path, dtype=str, keep_default_na=False)
    assert "coverage_status" not in gap.columns
    assert "trial_id" in gap.columns
    assert gap["nct_id"].tolist() == ["NCT2"]  # only uncovered, no stale NCT9
    assert gap["trial_id"].tolist() == ["NCT2"]
    assert gap["manual_overwrite"].tolist() == [""]  # blank value col


def test_audit_resource_accretes_fills_then_rebuilds_template(tmp_path: Path):
    spec = _cancer_spec(tmp_path)
    gaps_root = tmp_path / "resource_gaps"
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
            "trial_id": ["NCT1", "NCT2", "NCT3"],
            "primary_tumor_type": ["lung", "breast", "skin"],
            "conditions_original": ["a", "b", "d"],
            "auto_determination": ["lung ca", "breast ca", "skin ca"],
        }
    )

    # First audit: no fills yet -> no new version; gap template lists both gaps.
    audit_resource(spec, generated, gaps_root, today_str="27062026")
    assert not (tmp_path / "manual_overwrite_27062026.csv").exists()
    assert list(tmp_path.glob("**/*_coverage_gaps.tsv")) == []  # no verbose report
    gap_path = gaps_root / "version_27062026" / "cancer_type_gaps.csv"
    template = pd.read_csv(gap_path, dtype=str, keep_default_na=False)
    assert sorted(template["nct_id"]) == ["NCT2", "NCT3"]

    # Curator fills NCT2 in the latest gap file.
    template.loc[template["nct_id"] == "NCT2", "manual_overwrite"] = "CURATED"
    template.to_csv(gap_path, index=False)

    # Second audit: NCT2 graduates into a NEW resource version; template keeps NCT3.
    summary = audit_resource(spec, generated, gaps_root, today_str="27062026")

    new_version = tmp_path / "manual_overwrite_27062026.csv"
    assert new_version.exists()
    assert original.read_bytes() == original_bytes  # original never overwritten
    merged = pd.read_csv(new_version, dtype=str, keep_default_na=False)
    assert dict(zip(merged["nct_id"], merged["manual_overwrite"])) == {
        "NCT1": "KEEP",
        "NCT2": "CURATED",
    }
    template_after = pd.read_csv(gap_path, dtype=str, keep_default_na=False)
    assert template_after["nct_id"].tolist() == ["NCT3"]
    assert summary["remaining_gaps"] == 1


def test_accrete_filled_template_reads_latest_version_dir(tmp_path: Path):
    spec = _cancer_spec(tmp_path)
    gaps_root = tmp_path / "resource_gaps"
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

    # An OLD gap version has a stale (unfilled) row; the LATEST has the curator's fill.
    _write_gap(
        gaps_root,
        spec,
        "13042026",
        pd.DataFrame(
            {
                "nct_id": ["NCT2"],
                "primary_tumor_type": ["breast"],
                "conditions_original": ["b"],
                "trial_id": ["NCT2"],
                "auto_determination": ["breast ca"],
                "manual_overwrite": [""],  # not filled in the old version
            }
        ),
    )
    _write_gap(
        gaps_root,
        spec,
        "27062026",
        pd.DataFrame(
            {
                "nct_id": ["NCT2"],
                "primary_tumor_type": ["breast"],
                "conditions_original": ["b"],
                "trial_id": ["NCT2"],
                "auto_determination": ["breast ca"],
                "manual_overwrite": ["CURATED_VALUE"],  # filled in the latest version
            }
        ),
    )

    new_path = accrete_filled_template(spec, gaps_root, today_str="27062026")

    assert new_path == tmp_path / "manual_overwrite_27062026.csv"
    assert new_path.exists()
    assert original.read_bytes() == original_bytes  # never overwritten
    merged = pd.read_csv(new_path, dtype=str, keep_default_na=False)
    by_nct = dict(zip(merged["nct_id"], merged["manual_overwrite"]))
    assert by_nct == {"NCT1": "KEEP", "NCT2": "CURATED_VALUE"}


def test_accrete_filled_template_applies_transform_before_write(tmp_path: Path):
    spec = _mapping_spec(tmp_path)
    gaps_root = tmp_path / "resource_gaps"
    (tmp_path / "MolecularSignatureCurationResource_01012026.csv").write_text(
        "Signature_lookup,Findings_curation\nmsi,OLD\n", encoding="utf-8"
    )
    _write_gap(
        gaps_root,
        spec,
        "27062026",
        pd.DataFrame({"Signature_lookup": ["tmb"], "Findings_curation": ["NEW"]}),
    )

    new_path = accrete_filled_template(
        spec,
        gaps_root,
        today_str="27062026",
        transform=lambda df: df.assign(
            Findings_curation=df["Findings_curation"].astype(str) + "!"
        ),
    )

    merged = pd.read_csv(new_path, dtype=str, keep_default_na=False)
    assert set(merged["Findings_curation"]) == {"OLD!", "NEW!"}


def test_accrete_filled_template_noop_when_nothing_filled(tmp_path: Path):
    spec = _cancer_spec(tmp_path)
    gaps_root = tmp_path / "resource_gaps"
    _write_gap(
        gaps_root,
        spec,
        "27062026",
        pd.DataFrame(
            {
                "nct_id": ["NCT2"],
                "primary_tumor_type": ["breast"],
                "conditions_original": ["b"],
                "trial_id": ["NCT2"],
                "auto_determination": ["breast ca"],
                "manual_overwrite": [""],  # not filled
            }
        ),
    )

    assert accrete_filled_template(spec, gaps_root, today_str="27062026") is None
    # no new resource version created
    assert list(tmp_path.glob("manual_overwrite_*.csv")) == []


def test_accrete_filled_template_noop_when_no_gap_file(tmp_path: Path):
    spec = _cancer_spec(tmp_path)
    gaps_root = tmp_path / "resource_gaps"  # never created
    assert accrete_filled_template(spec, gaps_root, today_str="27062026") is None
