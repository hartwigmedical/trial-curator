import argparse

import pytest

from aus_trial_universe.ctgov.drug_ontology.sources.pottr import load as load_to_postgres
from aus_trial_universe.ctgov.drug_ontology.sources.pottr.drug_classes import (
    PottrDrugConcept,
)


def term(
    input_drug_name="FOLFOX",
    match_status="UNMATCHED",
    rxnorm_rxcui="",
    rxnorm_canonical_name="",
    rxnorm_term_type="",
    rxnorm_ingredient_rxcui="",
    rxnorm_ingredient_name="",
    rxnorm_ingredient_term_type="",
):
    return load_to_postgres.RxNormDrugTermForPottr(
        input_drug_name=input_drug_name,
        match_status=match_status,
        rxnorm_rxcui=rxnorm_rxcui,
        rxnorm_canonical_name=rxnorm_canonical_name,
        rxnorm_term_type=rxnorm_term_type,
        rxnorm_ingredient_rxcui=rxnorm_ingredient_rxcui,
        rxnorm_ingredient_name=rxnorm_ingredient_name,
        rxnorm_ingredient_term_type=rxnorm_ingredient_term_type,
    )


def concept(key="folfox", name="FOLFOX", text="FOLFOX regimen"):
    return PottrDrugConcept(
        concept_key=key,
        concept_index=1,
        drug_raw=text,
        canonical_drug_name=name,
        aliases_raw=name,
        direct_classes_raw="chemotherapy_regimen",
    )


def test_direct_alias_anchor_for_unmatched_regimen_uses_alias_key():
    t = term("FOLFOX", match_status="UNMATCHED")
    candidate = load_to_postgres.direct_alias_anchor(t, {"concept_folfox"})

    assert candidate.pottr_anchor_key == "ALIAS:folfox"
    assert candidate.pottr_anchor_type == "DIRECT_ALIAS"
    assert candidate.concept_keys == {"concept_folfox"}


def test_concept_is_combo_or_regimen_detects_regimen_text():
    assert load_to_postgres.concept_is_combo_or_regimen(concept()) is True


def test_exact_alias_override_can_preserve_specific_product_or_regimen():
    t = term(
        input_drug_name="FOLFOX",
        match_status="MATCHED",
        rxnorm_ingredient_name="fluorouracil",
    )

    rx_anchor = type(
        "FakeAnchor",
        (),
        {
            "link_anchor_name": "fluorouracil",
        },
    )()

    should_override = load_to_postgres.exact_alias_should_override_rxnorm(
        term=t,
        rx_anchor=rx_anchor,
        rx_concepts={"component_concept"},
        alias_concepts={"folfox_concept"},
        concept_by_key={"folfox_concept": concept("folfox_concept", "FOLFOX")},
    )

    assert should_override is True


def test_parser_does_not_accept_python_side_tsv_or_rxnorm_source_version_args():
    parser = load_to_postgres.build_arg_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "--pottr_raw_dir",
                "data/pottr",
                "--rxnorm_rrf_dir",
                "data/rxnorm",
                "--pottr_source_version",
                "POTTR_test",
                "--output_check_tsv",
                "bad.tsv",
            ]
        )

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "--pottr_raw_dir",
                "data/pottr",
                "--rxnorm_rrf_dir",
                "data/rxnorm",
                "--pottr_source_version",
                "POTTR_test",
                "--rxnorm_source_version",
                "RxNorm_test",
            ]
        )


def test_fetch_query_uses_simplified_rxnorm_mapping_table_only():
    sql = str(load_to_postgres.FETCH_RXNORM_DRUG_TERMS_SQL)

    assert "drug_identity.ctgov_drug_term_rxnorm_mapping" in sql
    assert "ctgov_drug_term_id" not in sql
    assert "source_field" not in sql
    assert "term_kind" not in sql



def test_truncate_statement_truncates_fk_related_tables_together():
    sql = str(load_to_postgres.TRUNCATE_POTTR_TABLES_SQL)

    assert "TRUNCATE TABLE" in sql
    assert "pottr_drug_concept" in sql
    assert "pottr_drug_class_assignment" in sql
    assert "pottr_anchor_class_mapping" in sql
    assert "input_drug_name_pottr_anchor" in sql
    assert not isinstance(load_to_postgres.TRUNCATE_POTTR_TABLES_SQL, list)



def test_curated_capeox_alias_equivalence_maps_to_capox_key():
    keys = load_to_postgres.alias_equivalence_keys("CAPEOX")

    assert "capeox" in keys
    assert "capox" in keys
