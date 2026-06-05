from pathlib import Path

from aus_trial_universe.ctgov.drug_ontology.sources.atc.ingredient_to_atc import (
    AtcTree,
    RxnConsoAtcAtom,
    load_rxnconso_atc_index,
    resolve_ingredient_to_atc,
)


def write_atc_tree(tmp_path: Path) -> Path:
    path = tmp_path / "atc_tree.tsv"
    path.write_text(
        "\n".join(
            [
                "ATC code\tATC level name",
                "L\tAntineoplastic and immunomodulating agents",
                "L01\tAntineoplastic agents",
                "L01E\tProtein kinase inhibitors",
                "L01EA\tBCR-ABL tyrosine kinase inhibitors",
                "L01EA01\timatinib",
            ]
        ),
        encoding="utf-8",
    )
    return path


def test_atc_tree_loads_hierarchy(tmp_path: Path):
    tree = AtcTree.from_tsv(write_atc_tree(tmp_path))

    assert tree.get("L01EA01").level_name == "imatinib"
    assert tree.node_at_level("L01EA01", 1).code == "L"
    assert tree.node_at_level("L01EA01", 5).code == "L01EA01"


def test_resolve_ingredient_to_direct_atc_match(tmp_path: Path):
    tree = AtcTree.from_tsv(write_atc_tree(tmp_path))
    atc_by_rxcui = {
        "282388": [
            RxnConsoAtcAtom(
                rxcui="282388",
                code="L01EA01",
                name="imatinib",
                tty="ATC",
            )
        ]
    }

    rows = resolve_ingredient_to_atc("282388", atc_by_rxcui, tree)

    assert len(rows) == 1
    row = rows[0]
    assert row.atc_match_status == "MATCHED_ATC"
    assert row.atc_code == "L01EA01"
    assert row.atc_name == "imatinib"
    assert row.atc_level == 5
    assert row.atc_l1_code == "L"
    assert row.atc_l5_code == "L01EA01"


def test_resolve_ingredient_without_atc_returns_no_match(tmp_path: Path):
    tree = AtcTree.from_tsv(write_atc_tree(tmp_path))

    rows = resolve_ingredient_to_atc("999999", {}, tree)

    assert len(rows) == 1
    assert rows[0].atc_match_status == "NO_ATC_MATCH"
    assert rows[0].atc_code == ""


def test_atc_code_not_in_tree_is_explicit(tmp_path: Path):
    tree = AtcTree.from_tsv(write_atc_tree(tmp_path))
    atc_by_rxcui = {
        "1": [RxnConsoAtcAtom(rxcui="1", code="Z99ZZ99", name="unknown", tty="ATC")]
    }

    rows = resolve_ingredient_to_atc("1", atc_by_rxcui, tree)

    assert rows[0].atc_match_status == "ATC_CODE_NOT_IN_TREE"
    assert rows[0].atc_code == "Z99ZZ99"


def test_load_rxnconso_atc_index_keeps_only_target_rxcuis_and_atc_rows(tmp_path: Path):
    rxnconso = tmp_path / "RXNCONSO.RRF"
    rxnconso.write_text(
        "\n".join(
            [
                "282388|ENG|||||Y|A1||||ATC|ATC|L01EA01|imatinib||N|",
                "282388|ENG|||||Y|A2||||RXNORM|IN|282388|imatinib||N|",
                "999|ENG|||||Y|A3||||ATC|ATC|L01XX00|other||N|",
            ]
        ),
        encoding="utf-8",
    )

    index = load_rxnconso_atc_index(rxnconso, {"282388"})

    assert set(index) == {"282388"}
    assert len(index["282388"]) == 1
    assert index["282388"][0].code == "L01EA01"
