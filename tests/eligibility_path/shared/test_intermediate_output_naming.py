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
from aus_trial_universe.eligibility_path.qa import cancer_type_output_diff


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


def test_tracked_docs_do_not_reference_old_intermediate_level_filenames():
    old_names = (
        "04_trial_level_cancer_type.tsv",
        "05_cohort_level_cancer_type.tsv",
        "04_trial_level_gene_alteration.tsv",
        "05_cohort_level_gene_alteration.tsv",
        "03_trial_level_molecular_signature.tsv",
        "04_cohort_level_molecular_signature.tsv",
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
