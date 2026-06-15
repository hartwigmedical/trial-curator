from __future__ import annotations

from pathlib import Path

import pytest

from aus_trial_universe.ctgov.eligibility.ii_process_eligibility_criteria.cancer_types.cancer_type_pipeline import (
    discover_pipeline_inputs as discover_cancer_type_inputs,
)
from aus_trial_universe.ctgov.eligibility.ii_process_eligibility_criteria.gene_alterations.gene_alteration_pipeline import (
    discover_pipeline_inputs as discover_gene_alteration_inputs,
)
from aus_trial_universe.ctgov.eligibility.ii_process_eligibility_criteria.molecular_signature.molecular_signature_pipeline import (
    discover_pipeline_inputs as discover_molecular_signature_inputs,
)
from aus_trial_universe.ctgov.eligibility.ii_process_eligibility_criteria.trial_resource.cohort_level_resource_pipeline import (
    discover_pipeline_inputs as discover_cohort_resource_inputs,
)
from aus_trial_universe.ctgov.eligibility.ii_process_eligibility_criteria.trial_resource.trial_level_resource_pipeline import (
    discover_pipeline_inputs as discover_trial_resource_inputs,
)


def write(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def seed_curated_file(data_dir: Path) -> Path:
    return write(data_dir / "trials/original_curations/NCT00000001.py", "rules = []\n")


def seed_base_trials(data_dir: Path) -> Path:
    return write(data_dir / "trials/ctgov_field_extractions.csv", "nct_id\nNCT00000001\n")


def test_cancer_type_discovery_uses_default_data_layout(tmp_path: Path):
    data_dir = tmp_path / "eligibility"
    shared_resources_dir = tmp_path / "data/eligibility/resources"
    seed_curated_file(data_dir)
    seed_base_trials(data_dir)
    write(shared_resources_dir / "oncotree.csv", "code,name,parent\n")
    write(shared_resources_dir / "cancer_type/ConditionsCurationResource_01012026.xlsx")
    write(shared_resources_dir / "cancer_type/PrimaryTumourCurationResource_01012026.xlsx")
    write(shared_resources_dir / "cancer_type/manual_overwrite_01012026.csv", "nct_id\n")

    inputs = discover_cancer_type_inputs(
        repo_root=tmp_path,
        eligibility_data_dir=data_dir,
        trials_dir=None,
        resources_dir=None,
        processed_dir=None,
        curated_dir=None,
        ctgov_extractions_csv=None,
        cancer_type_resource_dir=None,
        oncotree_csv=None,
        manual_overwrite_file=None,
        conditions_output_dir=None,
        output_dir=None,
        output_format="tsv",
        fail_on_error=False,
    )

    assert inputs.ctgov_extractions_csv == data_dir / "trials/ctgov_field_extractions.csv"
    assert inputs.curated_dir == data_dir / "trials/original_curations"
    assert inputs.cancer_type_resource_dir == shared_resources_dir / "cancer_type"
    assert inputs.oncotree_csv == shared_resources_dir / "oncotree.csv"
    assert inputs.output_dir == data_dir / "processed/cancer_type"
    assert inputs.conditions_output_dir == data_dir / "processed/cancer_type"


def test_gene_alteration_discovery_requires_mapping_corrections(tmp_path: Path):
    data_dir = tmp_path / "eligibility"
    seed_curated_file(data_dir)
    write(data_dir / "resources/gene_alteration/GeneAlterationCurationResource_01012026.xlsx")
    write(data_dir / "resources/gene_alteration/manual_overwrite_01012026.xlsx")

    with pytest.raises(FileNotFoundError, match="mapping corrections"):
        discover_gene_alteration_inputs(
            repo_root=tmp_path,
            eligibility_data_dir=data_dir,
            curated_dir=None,
            gene_alteration_resource_dir=None,
            mapping_resource_file=None,
            manual_overwrite_file=None,
            output_dir=None,
            output_format="tsv",
            fail_on_error=False,
        )


def test_gene_alteration_discovery_uses_default_data_layout(tmp_path: Path):
    data_dir = tmp_path / "eligibility"
    seed_curated_file(data_dir)
    write(data_dir / "resources/gene_alteration/GeneAlterationCurationResource_01012026.xlsx")
    write(data_dir / "resources/gene_alteration/manual_overwrite_01012026.xlsx")
    write(data_dir / "resources/gene_alteration/mapping_corrections.xlsx")

    inputs = discover_gene_alteration_inputs(
        repo_root=tmp_path,
        eligibility_data_dir=data_dir,
        curated_dir=None,
        gene_alteration_resource_dir=None,
        mapping_resource_file=None,
        manual_overwrite_file=None,
        output_dir=None,
        output_format="tsv",
        fail_on_error=False,
    )

    assert inputs.mapping_resource_file.name == "GeneAlterationCurationResource_01012026.xlsx"
    assert inputs.manual_overwrite_file.name == "manual_overwrite_01012026.xlsx"
    assert inputs.output_dir == data_dir / "processed/gene_alteration"


def test_molecular_signature_discovery_uses_default_data_layout(tmp_path: Path):
    data_dir = tmp_path / "eligibility"
    seed_curated_file(data_dir)
    write(data_dir / "resources/molecular_signature/MolecularSignatureCurationResource_01012026.xlsx")

    inputs = discover_molecular_signature_inputs(
        repo_root=tmp_path,
        eligibility_data_dir=data_dir,
        curated_dir=None,
        molecular_signature_resource_dir=None,
        mapping_resource_file=None,
        output_dir=None,
        output_format="tsv",
        fail_on_error=False,
    )

    assert inputs.mapping_resource_file.name == "MolecularSignatureCurationResource_01012026.xlsx"
    assert inputs.output_dir == data_dir / "processed/molecular_signature"


def test_trial_resource_discovery_uses_processed_trial_level_defaults(tmp_path: Path):
    data_dir = tmp_path / "eligibility"
    seed_base_trials(data_dir)
    write(data_dir / "processed/cancer_type/04_trial_level_cancer_type.tsv")
    write(data_dir / "processed/gene_alteration/04_trial_level_gene_alteration.tsv")
    write(data_dir / "processed/molecular_signature/03_trial_level_molecular_signature.tsv")

    inputs = discover_trial_resource_inputs(
        repo_root=tmp_path,
        eligibility_data_dir=data_dir,
        trials_file=None,
        cancer_type_file=None,
        gene_alteration_file=None,
        molecular_signature_file=None,
        output_file=None,
        export_date="05062026",
        nct_id_column=None,
    )

    assert inputs.output_file == data_dir / "exports/trial_resource_05062026.tsv"


def test_cohort_resource_discovery_uses_processed_cohort_level_defaults(tmp_path: Path):
    data_dir = tmp_path / "eligibility"
    seed_curated_file(data_dir)
    seed_base_trials(data_dir)
    write(data_dir / "processed/cancer_type/05_cohort_level_cancer_type.tsv")
    write(data_dir / "processed/gene_alteration/05_cohort_level_gene_alteration.tsv")
    write(data_dir / "processed/molecular_signature/04_cohort_level_molecular_signature.tsv")

    inputs = discover_cohort_resource_inputs(
        repo_root=tmp_path,
        eligibility_data_dir=data_dir,
        trials_file=None,
        curated_dir=None,
        cancer_type_file=None,
        gene_alteration_file=None,
        molecular_signature_file=None,
        cohort_base_file=None,
        output_file=None,
        export_date="05062026",
        nct_id_column=None,
        fail_on_error=False,
    )

    assert inputs.output_file == data_dir / "exports/cohort_resource_05062026.tsv"
