from __future__ import annotations

from pathlib import Path

import pytest

from aus_trial_universe.eligibility_path.anzctr.ii_process_eligibility_criteria.gene_alterations.gene_alteration_pipeline import (
    discover_pipeline_inputs as discover_gene_alteration_inputs,
)
from aus_trial_universe.eligibility_path.anzctr.ii_process_eligibility_criteria.molecular_signature.molecular_signature_pipeline import (
    discover_pipeline_inputs as discover_molecular_signature_inputs,
)
from aus_trial_universe.eligibility_path.anzctr.ii_process_eligibility_criteria.trial_resource.cohort_level_resource_pipeline import (
    discover_pipeline_inputs as discover_cohort_resource_inputs,
)
from aus_trial_universe.eligibility_path.anzctr.ii_process_eligibility_criteria.trial_resource.trial_level_resource_pipeline import (
    discover_pipeline_inputs as discover_trial_resource_inputs,
)


def write(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def seed_curated_file(data_dir: Path) -> Path:
    return write(
        data_dir / "trial_inputs/anzctr/eligibility_curations/ACTRN12605000003673.py",
        "rules = []\n",
    )


def seed_base_trials(data_dir: Path) -> Path:
    return write(
        data_dir / "trial_inputs/anzctr/extracted_trials/anzctr_field_extractions.csv",
        "ACTRN,HEALTH CONDITION\nACTRN12605000003673,Breast cancer\n",
    )


def test_gene_alteration_discovery_requires_mapping_corrections(tmp_path: Path):
    data_dir = tmp_path / "data/eligibility_path/exports/intermediates/anzctr"
    seed_curated_file(tmp_path / "data")
    resources_dir = tmp_path / "data/eligibility_path/resources/gene_alteration"
    write(resources_dir / "GeneAlterationCurationResource_01012026.xlsx")
    write(resources_dir / "manual_overwrite_01012026.xlsx")

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
    data_dir = tmp_path / "data/eligibility_path/exports/intermediates/anzctr"
    seed_curated_file(tmp_path / "data")
    resources_dir = tmp_path / "data/eligibility_path/resources/gene_alteration"
    write(resources_dir / "GeneAlterationCurationResource_01012026.xlsx")
    write(resources_dir / "manual_overwrite_01012026.xlsx")
    write(resources_dir / "mapping_corrections.xlsx")

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

    assert inputs.curated_dir == tmp_path / "data/trial_inputs/anzctr/eligibility_curations"
    assert inputs.gene_alteration_resource_dir == resources_dir
    assert inputs.mapping_resource_file.name == "GeneAlterationCurationResource_01012026.xlsx"
    assert inputs.manual_overwrite_file.name == "manual_overwrite_01012026.xlsx"
    assert inputs.output_dir == data_dir / "gene_alteration"


def test_molecular_signature_discovery_uses_default_data_layout(tmp_path: Path):
    data_dir = tmp_path / "data/eligibility_path/exports/intermediates/anzctr"
    seed_curated_file(tmp_path / "data")
    resource_file = write(
        tmp_path / "data/eligibility_path/resources/molecular_signature/"
        "MolecularSignatureCurationResource_01012026.xlsx"
    )

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

    assert inputs.curated_dir == tmp_path / "data/trial_inputs/anzctr/eligibility_curations"
    assert inputs.molecular_signature_resource_dir == resource_file.parent
    assert inputs.mapping_resource_file == resource_file
    assert inputs.output_dir == data_dir / "molecular_signature"


def test_trial_resource_discovery_uses_anzctr_final_export_defaults(tmp_path: Path):
    data_dir = tmp_path / "data/eligibility_path"
    seed_base_trials(tmp_path / "data")
    write(data_dir / "exports/intermediates/anzctr/cancer_type/04a_cancer_type_trial_level.tsv")
    write(data_dir / "exports/intermediates/anzctr/gene_alteration/04a_gene_alteration_trial_level.tsv")
    write(data_dir / "exports/intermediates/anzctr/molecular_signature/03a_molecular_signature_trial_level.tsv")

    inputs = discover_trial_resource_inputs(
        repo_root=tmp_path,
        eligibility_data_dir=data_dir,
        trials_file=None,
        cancer_type_file=None,
        gene_alteration_file=None,
        molecular_signature_file=None,
        output_file=None,
        export_date="05062026",
        trial_id_column=None,
    )

    assert inputs.trials_file == tmp_path / "data/trial_inputs/anzctr/extracted_trials/anzctr_field_extractions.csv"
    assert inputs.output_file == data_dir / "exports/final/anzctr/trial_resource_05062026.tsv"


def test_cohort_resource_discovery_uses_anzctr_final_export_defaults(tmp_path: Path):
    data_dir = tmp_path / "data/eligibility_path"
    seed_curated_file(tmp_path / "data")
    seed_base_trials(tmp_path / "data")
    write(data_dir / "exports/intermediates/anzctr/cancer_type/04b_cancer_type_cohort_level.tsv")
    write(data_dir / "exports/intermediates/anzctr/gene_alteration/04b_gene_alteration_cohort_level.tsv")
    write(data_dir / "exports/intermediates/anzctr/molecular_signature/03b_molecular_signature_cohort_level.tsv")

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
        trial_id_column=None,
        fail_on_error=False,
    )

    assert inputs.curated_dir == tmp_path / "data/trial_inputs/anzctr/eligibility_curations"
    assert inputs.output_file == data_dir / "exports/final/anzctr/cohort_resource_05062026.tsv"
