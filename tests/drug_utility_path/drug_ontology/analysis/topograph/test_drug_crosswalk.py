from pathlib import Path

from aus_trial_universe.drug_utility_path.ctgov.drug_ontology.analysis.topograph.ctgov_drug_crosswalk import (
    build_ctgov_topograph_drug_crosswalk,
)


def write_annotated(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = [
        "input_drug_name",
        "rxnorm_match_status",
        "rxnorm_matched_term",
        "rxnorm_canonical_name",
        "rxnorm_ingredient_rxcui",
        "rxnorm_ingredient_name",
        "atc_match_status",
        "atc_code",
        "atc_l5_name",
    ]
    path.write_text(
        "\t".join(fieldnames)
        + "\n"
        + "\n".join("\t".join(row.get(field, "") for field in fieldnames) for row in rows)
        + "\n",
        encoding="utf-8",
    )


def test_build_ctgov_topograph_drug_crosswalk_links_by_rxnorm_ingredient(tmp_path: Path):
    ctgov_tsv = tmp_path / "ctgov.atc_annotated.tsv"
    topograph_tsv = tmp_path / "topograph.atc_annotated.tsv"
    output_tsv = tmp_path / "crosswalk.tsv"

    write_annotated(
        ctgov_tsv,
        [
            {
                "input_drug_name": "Imatinib",
                "rxnorm_match_status": "MATCHED",
                "rxnorm_matched_term": "Imatinib",
                "rxnorm_canonical_name": "Imatinib",
                "rxnorm_ingredient_rxcui": "282388",
                "rxnorm_ingredient_name": "Imatinib",
                "atc_match_status": "MATCHED_ATC",
                "atc_code": "L01EA01",
                "atc_l5_name": "imatinib",
            },
            {
                "input_drug_name": "Unknown",
                "rxnorm_match_status": "UNMATCHED",
            },
            {
                "input_drug_name": "Lonely Drug",
                "rxnorm_match_status": "MATCHED",
                "rxnorm_ingredient_rxcui": "999999",
                "rxnorm_ingredient_name": "Lonely Drug",
            },
        ],
    )
    write_annotated(
        topograph_tsv,
        [
            {
                "input_drug_name": "imatinib",
                "rxnorm_match_status": "MATCHED",
                "rxnorm_ingredient_rxcui": "282388",
                "rxnorm_ingredient_name": "Imatinib",
                "atc_match_status": "MATCHED_ATC",
                "atc_code": "L01EA01",
                "atc_l5_name": "imatinib",
            },
        ],
    )

    stats = build_ctgov_topograph_drug_crosswalk(ctgov_tsv, topograph_tsv, output_tsv)
    output = output_tsv.read_text(encoding="utf-8")

    assert stats["ctgov_terms"] == 3
    assert stats["topograph_terms"] == 1
    assert stats["ctgov_terms_with_topograph_ingredient_match"] == 1
    assert stats["ctgov_terms_without_topograph_ingredient_match"] == 1
    assert stats["ctgov_terms_without_confident_rxnorm_identity"] == 1
    assert stats["output_rows"] == 3
    assert "Imatinib\tTOPOGRAPH_INGREDIENT_MATCH" in output
    assert "Lonely Drug\tNO_TOPOGRAPH_TERM_FOR_INGREDIENT" in output
    assert "Unknown\tNO_CONFIDENT_CTGOV_RXNORM_MATCH" in output
    assert "\tTrue\n" in output
