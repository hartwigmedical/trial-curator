from __future__ import annotations

from pathlib import Path

import pandas as pd

from aus_trial_universe.eligibility_path.anzctr.ii_process_eligibility_criteria.trial_resource.trial_level_resource_pipeline import (
    build_trial_resource_table,
)


def test_build_trial_resource_table_merges_numeric_base_ids_to_prefixed_components(
    tmp_path: Path,
):
    trials_file = tmp_path / "anzctr_field_extractions.csv"
    cancer_type_file = tmp_path / "04a_cancer_type_trial_level.tsv"
    gene_alteration_file = tmp_path / "04a_gene_alteration_trial_level.tsv"
    molecular_signature_file = tmp_path / "03a_molecular_signature_trial_level.tsv"

    pd.DataFrame(
        {
            "ACTRN": ["12605000003673"],
            "SCIENTIFIC TITLE": ["Trial title"],
        }
    ).to_csv(trials_file, index=False)
    pd.DataFrame(
        {
            "trial_id": ["ACTRN12605000003673"],
            "cancer_type_inclusive": ["Breast (BREAST)"],
            "cancer_type_exclusive": [""],
        }
    ).to_csv(cancer_type_file, sep="\t", index=False)
    pd.DataFrame(
        {
            "trial_id": ["ACTRN12605000003673"],
            "gene_alteration_inclusive": ["BRAF V600E"],
            "gene_alteration_exclusive": [""],
        }
    ).to_csv(gene_alteration_file, sep="\t", index=False)
    pd.DataFrame(
        {
            "trial_id": ["ACTRN12605000003673"],
            "molecular_signature_inclusive": ["MSI-H"],
            "molecular_signature_exclusive": [""],
        }
    ).to_csv(molecular_signature_file, sep="\t", index=False)

    out = build_trial_resource_table(
        trials_file=trials_file,
        cancer_type_file=cancer_type_file,
        gene_alteration_file=gene_alteration_file,
        molecular_signature_file=molecular_signature_file,
    )

    assert out.loc[0, "trialId"] == "ACTRN12605000003673"
    assert out.loc[0, "cancer_type_inclusive"] == "Breast (BREAST)"
    assert out.loc[0, "gene_alteration_inclusive"] == "BRAF V600E"
    assert out.loc[0, "molecular_signature_inclusive"] == "MSI-H"
