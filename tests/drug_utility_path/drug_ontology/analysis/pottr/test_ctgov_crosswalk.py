from aus_trial_universe.drug_utility_path.ctgov.drug_ontology.analysis.pottr import (
    build_crosswalk_rows,
    build_pottr_indexes,
    determine_presence_status,
)


def test_determine_presence_status_variants():
    assert determine_presence_status(
        ctgov_has_annotation=False,
        ctgov_has_confident_rxnorm_match=False,
        ctgov_link_rxcuis=[],
        present_link_rxcuis=[],
        absent_link_rxcuis=[],
    ) == ("NO_CTGOV_RXNORM_ATC_ANNOTATION_ROW", "")

    assert determine_presence_status(
        ctgov_has_annotation=True,
        ctgov_has_confident_rxnorm_match=True,
        ctgov_link_rxcuis=[],
        present_link_rxcuis=[],
        absent_link_rxcuis=[],
    ) == ("UNCOMPARABLE_NO_CTGOV_RXNORM_ID", "")

    assert determine_presence_status(
        ctgov_has_annotation=True,
        ctgov_has_confident_rxnorm_match=False,
        ctgov_link_rxcuis=["1"],
        present_link_rxcuis=[],
        absent_link_rxcuis=["1"],
    ) == ("UNCOMPARABLE_NO_CONFIDENT_CTGOV_RXNORM_MATCH", "")

    assert determine_presence_status(
        ctgov_has_annotation=True,
        ctgov_has_confident_rxnorm_match=True,
        ctgov_link_rxcuis=["1", "2"],
        present_link_rxcuis=["1"],
        absent_link_rxcuis=["2"],
    ) == ("PARTIAL_POTTR_RXNORM_ID_MATCH", "partial")

    assert determine_presence_status(
        ctgov_has_annotation=True,
        ctgov_has_confident_rxnorm_match=True,
        ctgov_link_rxcuis=["1"],
        present_link_rxcuis=["1"],
        absent_link_rxcuis=[],
    ) == ("POTTR_RXNORM_ID_MATCH", "false")

    assert determine_presence_status(
        ctgov_has_annotation=True,
        ctgov_has_confident_rxnorm_match=True,
        ctgov_link_rxcuis=["1"],
        present_link_rxcuis=[],
        absent_link_rxcuis=["1"],
    ) == ("MISSING_FROM_POTTR_BY_RXNORM_ID", "true")


def test_build_crosswalk_rows_links_by_rxnorm_id_and_exact_name():
    pottr_rows = [
        {
            "__row_index": "0",
            "pottr_canonical_drug_name": "Imatinib",
            "pottr_drug_aliases": "Gleevec",
            "pottr_direct_class_names": "BCR-ABL inhibitor",
            "pottr_class_hierarchy_paths": "Targeted therapy > BCR-ABL inhibitor",
            "rxnorm_match_statuses": "MATCHED",
            "rxnorm_rxcuis": "282388",
            "rxnorm_ingredient_rxcuis": "282388",
            "rxnorm_ingredient_names": "Imatinib",
            "pottr_link_anchor_rxcuis": "282388",
            "atc_codes": "L01EA01",
        }
    ]
    pottr_by_rxnorm_id, pottr_by_exact_name = build_pottr_indexes(pottr_rows)

    rows = build_crosswalk_rows(
        ctgov_terms=["Imatinib", "Missing Drug", "Unmatched Drug"],
        ctgov_summaries_by_key={
            "imatinib": {
                "has_annotation_row": "true",
                "rxnorm_match_statuses": "MATCHED",
                "rxnorm_rxcuis": "282388",
                "rxnorm_ingredient_rxcuis": "282388",
            },
            "missing drug": {
                "has_annotation_row": "true",
                "rxnorm_match_statuses": "MATCHED",
                "rxnorm_rxcuis": "999999",
                "rxnorm_ingredient_rxcuis": "999999",
            },
            "unmatched drug": {
                "has_annotation_row": "true",
                "rxnorm_match_statuses": "UNMATCHED",
            },
        },
        pottr_by_rxnorm_id=pottr_by_rxnorm_id,
        pottr_by_exact_name=pottr_by_exact_name,
    )

    by_term = {row["ctgov_input_drug_name"]: row for row in rows}

    assert by_term["Imatinib"]["pottr_presence_status"] == "POTTR_RXNORM_ID_MATCH"
    assert by_term["Imatinib"]["pottr_exact_casefold_term_match"] == "true"
    assert by_term["Imatinib"]["pottr_canonical_drug_name"] == "Imatinib"
    assert by_term["Missing Drug"]["pottr_presence_status"] == "MISSING_FROM_POTTR_BY_RXNORM_ID"
    assert by_term["Missing Drug"]["is_missing_from_pottr_by_rxnorm_id"] == "true"
    assert by_term["Unmatched Drug"]["pottr_presence_status"] == "UNCOMPARABLE_NO_CTGOV_RXNORM_ID"
