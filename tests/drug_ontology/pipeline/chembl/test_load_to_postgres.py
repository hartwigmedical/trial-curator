import pytest

from aus_trial_universe.ctgov.drug_ontology.sources.chembl import load as load_to_postgres
from aus_trial_universe.ctgov.drug_ontology.sources.chembl.molecules import (
    ChemblNameMatch,
)


def term(
    input_drug_name="Opdualag",
    match_status="MATCHED",
    rxnorm_rxcui="1",
    rxnorm_canonical_name="Opdualag",
    rxnorm_term_type="BN",
    rxnorm_ingredient_rxcui="2",
    rxnorm_ingredient_name="nivolumab",
    rxnorm_ingredient_term_type="IN",
):
    return load_to_postgres.RxNormDrugTermForChembl(
        input_drug_name=input_drug_name,
        match_status=match_status,
        rxnorm_rxcui=rxnorm_rxcui,
        rxnorm_canonical_name=rxnorm_canonical_name,
        rxnorm_term_type=rxnorm_term_type,
        rxnorm_ingredient_rxcui=rxnorm_ingredient_rxcui,
        rxnorm_ingredient_name=rxnorm_ingredient_name,
        rxnorm_ingredient_term_type=rxnorm_ingredient_term_type,
    )


def match(molregno: int, pref_name: str, matched_name: str = "") -> ChemblNameMatch:
    return ChemblNameMatch(
        molregno=molregno,
        chembl_id=f"CHEMBL{molregno}",
        pref_name=pref_name,
        matched_name=matched_name or pref_name,
        matched_name_type="SYNONYM",
        match_strategy="EXACT_NORMALIZED_NAME",
        match_key=load_to_postgres.normalize_match_key(matched_name or pref_name),
    )


def test_parser_does_not_accept_python_side_tsv_or_rxnorm_source_version_args():
    parser = load_to_postgres.build_arg_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "--chembl_sqlite_path",
                "chembl.db",
                "--rxnorm_rrf_dir",
                "data/rxnorm",
                "--chembl_source_version",
                "ChEMBL_36",
                "--output_check_tsv",
                "bad.tsv",
            ]
        )

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "--chembl_sqlite_path",
                "chembl.db",
                "--rxnorm_rrf_dir",
                "data/rxnorm",
                "--chembl_source_version",
                "ChEMBL_36",
                "--rxnorm_source_version",
                "RxNorm_test",
            ]
        )


def test_fetch_query_uses_simplified_rxnorm_mapping_table_only():
    sql = str(load_to_postgres.FETCH_RXNORM_DRUG_TERMS_SQL)

    assert "drug_identity.ctgov_drug_term_rxnorm_mapping" in sql
    assert "input_drug_name" in sql
    assert "ctgov_drug_term_id" not in sql
    assert "source_field" not in sql
    assert "term_kind" not in sql
    assert "rxnorm_source_version" not in sql


def test_known_combination_brand_is_blocked_from_single_chembl_molecule():
    assert load_to_postgres.is_blocked_combination_brand("Opdualag")


def test_specific_direct_matches_prefer_adc_payload_molecule():
    broad = match(1, "TRASTUZUMAB", "Kadcyla")
    specific = match(2, "TRASTUZUMAB EMTANSINE", "Kadcyla")

    filtered = load_to_postgres.prefer_specific_direct_matches([broad, specific])

    assert [m.pref_name for m in filtered] == ["TRASTUZUMAB EMTANSINE"]
