from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence

from aus_trial_universe.eligibility_path.anzctr.ii_process_eligibility_criteria.cancer_types import (
    cancer_type_pipeline as anzctr_cancer_type_pipeline,
)
from aus_trial_universe.eligibility_path.anzctr.ii_process_eligibility_criteria.cancer_types import (
    cohort_level_cancer_type as anzctr_cohort_level_cancer_type,
)
from aus_trial_universe.eligibility_path.anzctr.ii_process_eligibility_criteria.gene_alterations import (
    cohort_level_gene_alteration as anzctr_cohort_level_gene_alteration,
)
from aus_trial_universe.eligibility_path.anzctr.ii_process_eligibility_criteria.gene_alterations import (
    gene_alteration_pipeline as anzctr_gene_alteration_pipeline,
)
from aus_trial_universe.eligibility_path.anzctr.ii_process_eligibility_criteria.molecular_signature import (
    cohort_level_molecular_signature as anzctr_cohort_level_molecular_signature,
)
from aus_trial_universe.eligibility_path.anzctr.ii_process_eligibility_criteria.molecular_signature import (
    molecular_signature_pipeline as anzctr_molecular_signature_pipeline,
)
from aus_trial_universe.eligibility_path.ctgov.ii_process_eligibility_criteria.cancer_types import (
    cancer_type_pipeline as ctgov_cancer_type_pipeline,
)
from aus_trial_universe.eligibility_path.ctgov.ii_process_eligibility_criteria.cancer_types import (
    cohort_level_cancer_type as ctgov_cohort_level_cancer_type,
)
from aus_trial_universe.eligibility_path.ctgov.ii_process_eligibility_criteria.gene_alterations import (
    cohort_level_gene_alteration as ctgov_cohort_level_gene_alteration,
)
from aus_trial_universe.eligibility_path.ctgov.ii_process_eligibility_criteria.gene_alterations import (
    gene_alteration_pipeline as ctgov_gene_alteration_pipeline,
)
from aus_trial_universe.eligibility_path.ctgov.ii_process_eligibility_criteria.molecular_signature import (
    cohort_level_molecular_signature as ctgov_cohort_level_molecular_signature,
)
from aus_trial_universe.eligibility_path.ctgov.ii_process_eligibility_criteria.molecular_signature import (
    molecular_signature_pipeline as ctgov_molecular_signature_pipeline,
)
from aus_trial_universe.eligibility_path.qa import (
    cancer_type_output_diff,
    gene_alteration_output_diff,
)
from aus_trial_universe.eligibility_path.qa.tabular_output_diff import (
    create_snapshot_and_diffs,
)


PAIRED_LEVEL_STEMS: Sequence[tuple[str, str]] = (
    (
        ctgov_cancer_type_pipeline.TRIAL_LEVEL_STEM,
        ctgov_cohort_level_cancer_type.COHORT_LEVEL_STEM,
    ),
    (
        anzctr_cancer_type_pipeline.TRIAL_LEVEL_STEM,
        anzctr_cohort_level_cancer_type.COHORT_LEVEL_STEM,
    ),
    (
        ctgov_gene_alteration_pipeline.TRIAL_LEVEL_STEM,
        ctgov_cohort_level_gene_alteration.COHORT_LEVEL_STEM,
    ),
    (
        anzctr_gene_alteration_pipeline.TRIAL_LEVEL_STEM,
        anzctr_cohort_level_gene_alteration.COHORT_LEVEL_STEM,
    ),
    (
        ctgov_molecular_signature_pipeline.TRIAL_LEVEL_STEM,
        ctgov_cohort_level_molecular_signature.COHORT_LEVEL_STEM,
    ),
    (
        anzctr_molecular_signature_pipeline.TRIAL_LEVEL_STEM,
        anzctr_cohort_level_molecular_signature.COHORT_LEVEL_STEM,
    ),
)

DISTINCT_STAGE_STEMS: Sequence[str] = (
    ctgov_cancer_type_pipeline.CONDITIONS_MAPPING_STEM,
    ctgov_cancer_type_pipeline.PRIMARY_VS_CONDITIONS_STEM,
    ctgov_cancer_type_pipeline.ROW_LEVEL_STEM,
    anzctr_cancer_type_pipeline.HEALTH_CONDITION_MAPPING_STEM,
    anzctr_cancer_type_pipeline.PRIMARY_VS_HEALTH_CONDITION_STEM,
    anzctr_cancer_type_pipeline.ROW_LEVEL_STEM,
    ctgov_gene_alteration_pipeline.GENERATED_MAPPING_RESOURCE_STEM,
    ctgov_gene_alteration_pipeline.MAPPED_CRITERIA_STEM,
    anzctr_gene_alteration_pipeline.GENERATED_MAPPING_RESOURCE_STEM,
    anzctr_gene_alteration_pipeline.MAPPED_CRITERIA_STEM,
    ctgov_molecular_signature_pipeline.MAPPING_RESOURCE_STEM,
    ctgov_molecular_signature_pipeline.MAPPED_CRITERIA_STEM,
    anzctr_molecular_signature_pipeline.MAPPING_RESOURCE_STEM,
    anzctr_molecular_signature_pipeline.MAPPED_CRITERIA_STEM,
)

DIAGNOSTIC_REPORT_FILES: Sequence[str] = (
    "diagnostics/01a_gene_alteration_conflicts_trial_level.tsv",
    "diagnostics/01b_gene_alteration_conflicts_cohort_level.tsv",
)


def test_distinct_intermediate_stages_use_numbered_prefixes():
    for stem in DISTINCT_STAGE_STEMS:
        assert re.fullmatch(r"\d{2}_[a-z0-9_]+", stem)
        assert "_trial_level" not in stem
        assert "_cohort_level" not in stem


def test_trial_and_cohort_intermediate_stages_use_paired_ab_prefixes():
    for trial_stem, cohort_stem in PAIRED_LEVEL_STEMS:
        assert re.fullmatch(r"\d{2}a_[a-z0-9_]+_trial_level", trial_stem)
        assert re.fullmatch(r"\d{2}b_[a-z0-9_]+_cohort_level", cohort_stem)
        assert trial_stem[:2] == cohort_stem[:2]


def test_cancer_type_qa_diff_includes_trial_and_cohort_level_files():
    expected = {
        "04a_cancer_type_trial_level.tsv",
        "04b_cancer_type_cohort_level.tsv",
    }

    for files in cancer_type_output_diff.REGISTRY_FILES.values():
        assert expected.issubset(files)

    assert cancer_type_output_diff.KEY_COLUMNS_BY_FILE[
        "04b_cancer_type_cohort_level.tsv"
    ] == ("trial_id", "nct_id", "cohort")


def test_gene_alteration_diagnostics_use_subfolder_and_local_numbering():
    assert set(DIAGNOSTIC_REPORT_FILES).issubset(gene_alteration_output_diff.OUTPUT_FILES)

    trial_stem = ctgov_gene_alteration_pipeline.TRIAL_LEVEL_CONFLICTS_STEM
    cohort_stem = ctgov_cohort_level_gene_alteration.COHORT_LEVEL_CONFLICTS_STEM

    assert trial_stem == anzctr_gene_alteration_pipeline.TRIAL_LEVEL_CONFLICTS_STEM
    assert cohort_stem == anzctr_cohort_level_gene_alteration.COHORT_LEVEL_CONFLICTS_STEM
    assert trial_stem == DIAGNOSTIC_REPORT_FILES[0].removesuffix(".tsv")
    assert cohort_stem == DIAGNOSTIC_REPORT_FILES[1].removesuffix(".tsv")
    assert re.fullmatch(r"diagnostics/01a_[a-z0-9_]+_trial_level", trial_stem)
    assert re.fullmatch(r"diagnostics/01b_[a-z0-9_]+_cohort_level", cohort_stem)
    assert gene_alteration_output_diff.KEY_COLUMNS_BY_FILE[DIAGNOSTIC_REPORT_FILES[1]] == (
        "trial_id",
        "nct_id",
        "cohort",
        "positive_term",
        "negative_term",
    )


def test_qa_snapshot_supports_nested_diagnostic_files(tmp_path: Path):
    processed_dir = tmp_path / "processed"
    current_file = processed_dir / DIAGNOSTIC_REPORT_FILES[0]
    current_file.parent.mkdir(parents=True)
    current_file.write_text(
        "trial_id\tnct_id\tpositive_term\tnegative_term\n"
        "NCT00000001\tNCT00000001\tEGFR\tNOT(EGFR)\n",
        encoding="utf-8",
    )

    summary = create_snapshot_and_diffs(
        processed_dir=processed_dir,
        baseline_dir=None,
        snapshot_dir=processed_dir / "qa/test",
        files=[DIAGNOSTIC_REPORT_FILES[0]],
        key_columns_by_file=gene_alteration_output_diff.KEY_COLUMNS_BY_FILE,
    )

    assert (processed_dir / "qa/test" / DIAGNOSTIC_REPORT_FILES[0]).is_file()
    assert (
        processed_dir
        / "qa/test/diff_diagnostics/01a_gene_alteration_conflicts_trial_level.tsv"
    ).is_file()
    assert summary.loc[0, "file"] == DIAGNOSTIC_REPORT_FILES[0]


def test_tracked_docs_do_not_reference_old_intermediate_level_filenames():
    old_names = (
        "04_trial_level_cancer_type.tsv",
        "05_cohort_level_cancer_type.tsv",
        "04_trial_level_gene_alteration.tsv",
        "05_cohort_level_gene_alteration.tsv",
        "03_trial_level_molecular_signature.tsv",
        "04_cohort_level_molecular_signature.tsv",
        "99a_gene_alteration_conflicts_trial_level.tsv",
        "99b_gene_alteration_conflicts_cohort_level.tsv",
    )

    paths = [
        *Path("aus_trial_universe/eligibility_path").rglob("*.py"),
        *Path("docs/eligibility_path").rglob("*.md"),
        *Path("scripts/eligibility").rglob("*.sh"),
        Path("Makefile"),
    ]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for old_name in old_names:
            assert old_name not in text
