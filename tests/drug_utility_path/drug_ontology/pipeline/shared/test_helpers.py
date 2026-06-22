from pathlib import Path

from aus_trial_universe.drug_utility_path.ctgov.drug_ontology.shared.keys import stable_key
from aus_trial_universe.drug_utility_path.ctgov.drug_ontology.shared.text import (
    bool_text,
    clean_text,
    normalize_key,
    ordered_join,
    ordered_unique,
    split_display_values,
)
from aus_trial_universe.drug_utility_path.ctgov.drug_ontology.shared import (
    detect_delimiter,
    read_tsv,
    read_tsv_dicts,
    write_tsv,
)


def test_clean_text_strips_bom_collapses_whitespace_and_blanks_missing_values():
    assert clean_text("\ufeff  alpha\n\t beta  ") == "alpha beta"
    assert clean_text("nan") == ""
    assert clean_text(None) == ""


def test_ordered_text_helpers_preserve_first_spelling():
    values = [" Imatinib ", "imatinib", "Dasatinib", "", None, " dasatinib "]

    assert normalize_key("  IMATINIB\nMesylate ") == "imatinib mesylate"
    assert ordered_unique(values) == ["Imatinib", "Dasatinib"]
    assert ordered_join(values) == "Imatinib | Dasatinib"
    assert split_display_values("Imatinib| Dasatinib |imatinib", delimiter="|") == [
        "Imatinib",
        "Dasatinib",
    ]
    assert bool_text(True) == "true"
    assert bool_text(False) == "false"


def test_stable_key_is_deterministic_and_prefixable():
    assert stable_key("drug", "class") == stable_key("drug", "class")
    assert stable_key("drug", "class", prefix="pottr_").startswith("pottr_")
    assert stable_key("drug", "class") != stable_key("class", "drug")


def test_tsv_helpers_round_trip_clean_values_and_detect_delimiters(tmp_path: Path):
    tsv_path = tmp_path / "rows.tsv"
    write_tsv(
        tsv_path,
        [
            {"name": " Imatinib ", "rxcui": " 282388 ", "extra": "ignored"},
            {"name": "Dasatinib", "rxcui": "52175"},
        ],
        ["name", "rxcui"],
    )

    fieldnames, raw_rows = read_tsv(tsv_path)
    assert fieldnames == ["name", "rxcui"]
    assert raw_rows == [
        {"name": " Imatinib ", "rxcui": " 282388 "},
        {"name": "Dasatinib", "rxcui": "52175"},
    ]
    assert read_tsv_dicts(tsv_path) == [
        {"name": "Imatinib", "rxcui": "282388"},
        {"name": "Dasatinib", "rxcui": "52175"},
    ]
    assert detect_delimiter(tsv_path) == "\t"

    csv_path = tmp_path / "rows.csv"
    csv_path.write_text("name,rxcui\nImatinib,282388\n", encoding="utf-8")
    assert detect_delimiter(csv_path) == ","
