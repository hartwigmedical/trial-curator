import pytest

from aus_trial_universe.drug_utility_path.ctgov.drug_ontology.sources.fda import load as load_to_postgres
from aus_trial_universe.drug_utility_path.ctgov.drug_ontology.sources.fda.product_components import (
    FdaLinkAnchor,
)


def fda_anchor(rxcui: str, name: str = "anchor", tty: str = "IN") -> FdaLinkAnchor:
    return FdaLinkAnchor(
        link_anchor_rxcui=rxcui,
        link_anchor_name=name,
        link_anchor_term_type=tty,
        link_anchor_strategy="TEST",
        link_anchor_path=rxcui,
        link_anchor_manual_review_needed=False,
    )


def term(
    input_drug_name="Padcev",
    rxnorm_term_type="BN",
    rxnorm_rxcui="123",
    rxnorm_canonical_name="Padcev",
    rxnorm_ingredient_rxcui="999",
    rxnorm_ingredient_name="enfortumab",
    rxnorm_ingredient_term_type="IN",
):
    return load_to_postgres.RxNormDrugTermForFda(
        input_drug_name=input_drug_name,
        rxnorm_rxcui=rxnorm_rxcui,
        rxnorm_canonical_name=rxnorm_canonical_name,
        rxnorm_term_type=rxnorm_term_type,
        rxnorm_ingredient_rxcui=rxnorm_ingredient_rxcui,
        rxnorm_ingredient_name=rxnorm_ingredient_name,
        rxnorm_ingredient_term_type=rxnorm_ingredient_term_type,
    )


def test_brand_terms_can_use_fda_drug_name_anchor_override():
    t = term(input_drug_name="Padcev", rxnorm_term_type="BN")
    anchors = [fda_anchor("555", "enfortumab vedotin", "PIN")]

    assert load_to_postgres.should_use_fda_drug_name_anchors(t, anchors) is True


def test_non_brand_terms_do_not_use_fda_drug_name_anchor_override():
    t = term(input_drug_name="Enfortumab", rxnorm_term_type="IN")
    anchors = [fda_anchor("555", "enfortumab vedotin", "PIN")]

    assert load_to_postgres.should_use_fda_drug_name_anchors(t, anchors) is False


def test_unique_fda_anchors_from_input_records_deduplicates_by_anchor_rxcui():
    records = [
        {
            "input_drug_name": "A",
            "rxnorm_fda_anchor_rxcui": "1",
            "rxnorm_fda_anchor_name": "drug",
            "rxnorm_fda_anchor_term_type": "IN",
        },
        {
            "input_drug_name": "B",
            "rxnorm_fda_anchor_rxcui": "1",
            "rxnorm_fda_anchor_name": "drug",
            "rxnorm_fda_anchor_term_type": "IN",
        },
    ]

    out = load_to_postgres.unique_fda_anchors_from_input_records(records)

    assert out == [
        {
            "rxnorm_fda_anchor_rxcui": "1",
            "rxnorm_fda_anchor_name": "drug",
            "rxnorm_fda_anchor_term_type": "IN",
        }
    ]


def test_parser_does_not_accept_python_side_tsv_or_rxnorm_source_version_args():
    parser = load_to_postgres.build_arg_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "--fda_raw_dir",
                "data/fda",
                "--rxnorm_rrf_dir",
                "data/rxnorm",
                "--fda_source_version",
                "FDA_test",
                "--output_check_tsv",
                "bad.tsv",
            ]
        )

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "--fda_raw_dir",
                "data/fda",
                "--rxnorm_rrf_dir",
                "data/rxnorm",
                "--fda_source_version",
                "FDA_test",
                "--rxnorm_source_version",
                "RxNorm_test",
            ]
        )
