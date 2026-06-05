from pathlib import Path

from aus_trial_universe.ctgov.drug_ontology.sources.atc.ingredient_to_atc import (
    AtcTree,
    RxnConsoAtcAtom,
)
from aus_trial_universe.ctgov.drug_ontology.identity.rxnorm.matcher import (
    IngredientResolution,
    TermResolution,
)
from aus_trial_universe.ctgov.drug_ontology.analysis.topograph.annotate_unique_drugs_with_atc import (
    DrugTermRxNormResolution,
    atc_resolutions_for_drug,
    default_output_path,
    read_unique_drug_terms,
    resolve_drug_column,
    resolve_ingredient_to_atc_with_audit,
)


def term_resolution(match_status: str, rxcui: str = "282388") -> TermResolution:
    return TermResolution(
        input_term="Imatinib",
        matched_term="Imatinib" if rxcui else "",
        match_stage="STAGE1_EXACT_STR" if rxcui else "NO_MATCH",
        match_status=match_status,
        rxcui=rxcui,
        canonical_name="Imatinib" if rxcui else "",
        canonical_tty="IN" if rxcui else "",
        canonical_sab="RXNORM" if rxcui else "",
        canonical_code=rxcui,
        candidate_count=1 if rxcui else 0,
        candidate_summary="",
        manual_review_needed=match_status != "MATCHED",
    )


def ingredient_resolution(rxcui: str = "282388") -> IngredientResolution:
    return IngredientResolution(
        ingredient_rxcui=rxcui,
        ingredient_name="Imatinib" if rxcui else "",
        ingredient_tty="IN" if rxcui else "",
        ingredient_resolution_stage="MATCHED_RXCUI_IS_INGREDIENT" if rxcui else "NO_MATCHED_RXCUI",
        ingredient_path="",
    )


def write_atc_tree(tmp_path: Path) -> Path:
    path = tmp_path / "atc_tree.tsv"
    path.write_text(
        "ATC code\tATC level name\n"
        "L\tAntineoplastic and immunomodulating agents\n"
        "L01\tAntineoplastic agents\n"
        "L01E\tProtein kinase inhibitors\n"
        "L01EA\tBCR-ABL tyrosine kinase inhibitors\n"
        "L01EA01\timatinib\n",
        encoding="utf-8",
    )
    return path


def test_read_unique_drug_terms_detects_comma_delimiter_and_deduplicates(tmp_path: Path):
    input_csv = tmp_path / "drugs.csv"
    input_csv.write_text(
        "drug_name,comment\n"
        "Imatinib,first\n"
        " imatinib ,duplicate\n"
        "Pembrolizumab,second\n",
        encoding="utf-8",
    )

    assert read_unique_drug_terms(input_csv) == ["Imatinib", "Pembrolizumab"]


def test_resolve_drug_column_uses_single_column_file_when_unambiguous():
    assert resolve_drug_column(["only_column"], requested_column=None) == "only_column"


def test_atc_resolutions_for_drug_requires_confident_rxnorm_match(tmp_path: Path):
    resolution = DrugTermRxNormResolution(
        input_drug_name="Unknown",
        term_resolution=term_resolution("UNMATCHED", rxcui=""),
        ingredient_resolution=ingredient_resolution(rxcui=""),
    )

    rows = atc_resolutions_for_drug(
        resolution,
        atc_by_rxcui={},
        tree=AtcTree.from_tsv(write_atc_tree(tmp_path)),
        obsolete_atc_code_bridge={},
    )

    assert rows[0][0].atc_match_status == "NO_CONFIDENT_RXNORM_MATCH"
    assert rows[0][1] == ""
    assert rows[0][2] is False


def test_atc_resolutions_for_drug_requires_rxnorm_ingredient(tmp_path: Path):
    resolution = DrugTermRxNormResolution(
        input_drug_name="Imatinib",
        term_resolution=term_resolution("MATCHED"),
        ingredient_resolution=ingredient_resolution(rxcui=""),
    )

    rows = atc_resolutions_for_drug(
        resolution,
        atc_by_rxcui={},
        tree=AtcTree.from_tsv(write_atc_tree(tmp_path)),
        obsolete_atc_code_bridge={},
    )

    assert rows[0][0].atc_match_status == "NO_RXNORM_INGREDIENT"


def test_resolve_ingredient_to_atc_with_audit_records_obsolete_code_bridge(tmp_path: Path):
    tree = AtcTree.from_tsv(write_atc_tree(tmp_path))
    atc_by_rxcui = {
        "282388": [
            RxnConsoAtcAtom(
                rxcui="282388",
                code="OLD01",
                name="imatinib old",
                tty="ATC",
            )
        ]
    }

    rows = resolve_ingredient_to_atc_with_audit(
        "282388",
        atc_by_rxcui,
        tree,
        obsolete_atc_code_bridge={"OLD01": "L01EA01"},
    )

    resolution, original_code, bridge_applied = rows[0]
    assert resolution.atc_match_status == "MATCHED_ATC"
    assert resolution.atc_code == "L01EA01"
    assert original_code == "OLD01"
    assert bridge_applied is True


def test_default_output_path_appends_annotation_suffix():
    assert default_output_path(Path("drugs.tsv")) == Path("drugs.atc_annotated.tsv")
