from pathlib import Path

from aus_trial_universe.drug_utility_path.ctgov.drug_ontology.identity.rxnorm import (
    ResolvedPottrSourceTerm,
    SelectedRxNormMatch,
    SourceDrugTerm,
    extract_pottr_terms_from_raw,
    extract_topograph_terms_from_raw,
    lookup_terms_for_source_term,
    row_for_pottr_resolved_group,
    split_topograph_drugs_cell,
)
from aus_trial_universe.drug_utility_path.ctgov.drug_ontology.identity.rxnorm.matcher import (
    IngredientResolution,
    TermResolution,
)


def term_resolution(
    *,
    input_term: str,
    rxcui: str,
    canonical_name: str,
    match_stage: str = "STAGE1_EXACT_STR",
    match_status: str = "MATCHED",
    manual_review_needed: bool = False,
) -> TermResolution:
    return TermResolution(
        input_term=input_term,
        matched_term=input_term,
        match_stage=match_stage,
        match_status=match_status,
        rxcui=rxcui,
        canonical_name=canonical_name,
        canonical_tty="IN",
        canonical_sab="RXNORM",
        canonical_code=rxcui,
        candidate_count=1 if rxcui else 0,
        candidate_summary="",
        manual_review_needed=manual_review_needed,
    )


def ingredient_resolution(rxcui: str, name: str) -> IngredientResolution:
    return IngredientResolution(
        ingredient_rxcui=rxcui,
        ingredient_name=name,
        ingredient_tty="IN" if rxcui else "",
        ingredient_resolution_stage="MATCHED_RXCUI_IS_INGREDIENT" if rxcui else "NO_MATCHED_RXCUI",
        ingredient_path="",
    )


def selected_match(lookup_term: str, rxcui: str, name: str) -> SelectedRxNormMatch:
    return SelectedRxNormMatch(
        lookup_term=lookup_term,
        term_resolution=term_resolution(
            input_term=lookup_term,
            rxcui=rxcui,
            canonical_name=name,
        ),
        ingredient_resolution=ingredient_resolution(rxcui, name),
    )


def unresolved_pottr_term(source_term: SourceDrugTerm) -> ResolvedPottrSourceTerm:
    return ResolvedPottrSourceTerm(
        source_term=source_term,
        lookup_terms=(source_term.input_name,),
        matches=(),
        first_term_resolution=term_resolution(
            input_term=source_term.input_name,
            rxcui="",
            canonical_name="",
            match_stage="NO_MATCH",
            match_status="UNMATCHED",
            manual_review_needed=True,
        ),
    )


def test_split_topograph_drugs_cell_splits_alternatives_and_combination_components():
    assert split_topograph_drugs_cell('" Afatinib + Bevacizumab ; Niraparib "') == [
        "Afatinib",
        "Bevacizumab",
        "Niraparib",
    ]


def test_split_topograph_drugs_cell_drops_missing_values():
    assert split_topograph_drugs_cell("N/A") == []
    assert split_topograph_drugs_cell(".") == []


def test_extract_topograph_terms_from_raw_preserves_first_seen_unique_terms(tmp_path: Path):
    input_tsv = tmp_path / "TOPOGRAPH-master.tsv"
    input_tsv.write_text(
        "Drugs\tOther\n"
        "Afatinib + Bevacizumab\tone\n"
        "afatinib; Niraparib\ttwo\n"
        "N/A\tthree\n",
        encoding="utf-8",
    )

    terms = extract_topograph_terms_from_raw(input_tsv)

    assert terms == [
        SourceDrugTerm(input_name="Afatinib", source="topograph"),
        SourceDrugTerm(input_name="Bevacizumab", source="topograph"),
        SourceDrugTerm(input_name="Niraparib", source="topograph"),
    ]


def test_extract_pottr_terms_from_raw_groups_canonical_rows_and_aliases(tmp_path: Path):
    raw_dir = tmp_path / "pottr"
    raw_dir.mkdir()
    (raw_dir / "drug_database.txt").write_text(
        "drug\tclass\n"
        "Drug A|Alias A\tClass 1\n"
        "drug a|Alias B\tClass 2\n"
        "Drug B\tClass 3\n",
        encoding="utf-8",
    )

    terms = extract_pottr_terms_from_raw(raw_dir)

    assert terms == [
        SourceDrugTerm(input_name="Drug A", source="pottr", pottr_aliases=("Alias A", "Alias B")),
        SourceDrugTerm(input_name="Drug B", source="pottr", pottr_aliases=()),
    ]


def test_lookup_terms_for_pottr_source_term_uses_canonical_then_unique_aliases():
    source_term = SourceDrugTerm(
        input_name="Drug A",
        source="pottr",
        pottr_aliases=("Alias A", "Drug A", "Alias A"),
    )

    assert lookup_terms_for_source_term(source_term) == ["Drug A", "Alias A"]


def test_row_for_pottr_resolved_group_preserves_merged_canonical_names_as_aliases():
    group = [
        ResolvedPottrSourceTerm(
            source_term=SourceDrugTerm("Drug A", "pottr", ("Alias A",)),
            lookup_terms=("Drug A", "Alias A"),
            matches=(selected_match("Alias A", "111", "Ingredient A"),),
            first_term_resolution=term_resolution(
                input_term="Drug A",
                rxcui="111",
                canonical_name="Ingredient A",
            ),
        ),
        ResolvedPottrSourceTerm(
            source_term=SourceDrugTerm("Drug B", "pottr", ("Alias B",)),
            lookup_terms=("Drug B", "Alias B"),
            matches=(selected_match("Alias B", "222", "Ingredient B"),),
            first_term_resolution=term_resolution(
                input_term="Drug B",
                rxcui="222",
                canonical_name="Ingredient B",
            ),
        ),
    ]

    row = row_for_pottr_resolved_group(group)

    assert row["input_name"] == "Drug A"
    assert row["pottr_aliases"] == "Drug B | Alias A | Alias B"
    assert row["pottr_match_term"] == "Alias A | Alias B"
    assert row["rxnorm_concept_id"] == "111 | 222"
    assert row["manual_review_needed"] == "true"


def test_row_for_pottr_resolved_group_keeps_unmatched_ids_blank():
    row = row_for_pottr_resolved_group(
        [
            unresolved_pottr_term(
                SourceDrugTerm(input_name="Unknown Drug", source="pottr", pottr_aliases=("Unknown Alias",))
            )
        ]
    )

    assert row["input_name"] == "Unknown Drug"
    assert row["rxnorm_concept_id"] == ""
    assert row["rxnorm_match_status"] == "UNMATCHED"
    assert row["manual_review_needed"] == "true"
