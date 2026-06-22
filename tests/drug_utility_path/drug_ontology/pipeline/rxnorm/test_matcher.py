from aus_trial_universe.drug_utility_path.ctgov.drug_ontology.identity.rxnorm.matcher import (
    IngredientResolution,
    RxnConsoIndex,
    RxnConsoRow,
    RxnRelIndex,
    ingredient_names_are_compatible,
    normalize_lookup_text,
    resolve_term,
)


def conso_row(rxcui: str, name: str, tty: str = "IN", sab: str = "RXNORM") -> RxnConsoRow:
    return RxnConsoRow(
        rxcui=rxcui,
        lat="ENG",
        ts="",
        lui="",
        stt="",
        sui="",
        ispref="Y",
        rxaui=f"A{rxcui}",
        saui="",
        scui="",
        sdui="",
        sab=sab,
        tty=tty,
        code=rxcui,
        str_value=name,
        srl="",
        suppress="N",
        cvf="",
    )


def test_normalize_lookup_text_strips_trademark_and_normalizes_dash():
    assert normalize_lookup_text("Keytruda™") == "keytruda"
    assert normalize_lookup_text("alfa–2b") == "alfa-2b"


def test_resolve_exact_rxnorm_term():
    index = RxnConsoIndex([conso_row("282388", "Imatinib")])

    result = resolve_term("Imatinib", index)

    assert result.match_status == "MATCHED"
    assert result.match_stage == "STAGE1_EXACT_STR"
    assert result.rxcui == "282388"
    assert result.canonical_name == "Imatinib"
    assert result.canonical_tty == "IN"


def test_resolve_gleevec_by_curated_expansion():
    index = RxnConsoIndex([conso_row("282388", "Imatinib")])

    result = resolve_term("Gleevec", index)

    assert result.match_status == "MATCHED"
    assert result.match_stage == "STAGE3_EXPANSION_EXACT_STR"
    assert result.matched_term == "Imatinib"
    assert result.rxcui == "282388"


def test_resolve_sti571_by_curated_expansion():
    index = RxnConsoIndex([conso_row("282388", "Imatinib")])

    result = resolve_term("STI571", index)

    assert result.match_status == "MATCHED"
    assert result.match_stage == "STAGE3_EXPANSION_EXACT_STR"
    assert result.matched_term == "Imatinib"
    assert result.rxcui == "282388"


def test_short_ambiguous_term_is_skipped():
    index = RxnConsoIndex([conso_row("999", "Aspartate Aminotransferase")])

    result = resolve_term("AST", index)

    assert result.match_status == "UNMATCHED"
    assert result.match_stage == "SKIPPED_AMBIGUOUS_SHORT_TERM"
    assert result.manual_review_needed is True


def test_resolve_ingredient_when_matched_rxcui_is_ingredient():
    index = RxnConsoIndex([conso_row("282388", "Imatinib")])
    rel_index = RxnRelIndex([])

    result = rel_index.resolve_ingredient(
        matched_rxcui="282388",
        conso_index=index,
        source_term="Imatinib",
    )

    assert isinstance(result, IngredientResolution)
    assert result.ingredient_rxcui == "282388"
    assert result.ingredient_name == "Imatinib"
    assert result.ingredient_tty == "IN"
    assert result.ingredient_resolution_stage == "MATCHED_RXCUI_IS_INGREDIENT"


def test_ingredient_name_compatibility_accepts_salt_variant_but_rejects_unrelated():
    assert ingredient_names_are_compatible("Clavulanate Potassium", "Clavulanic Acid")
    assert not ingredient_names_are_compatible("Prednisolone Acetate", "Gentamicin")


def test_regimen_folfox_does_not_expand_to_component_drug():
    index = RxnConsoIndex(
        [
            conso_row("481", "Leucovorin"),
            conso_row("4492", "Fluorouracil"),
            conso_row("32592", "Oxaliplatin"),
        ]
    )

    result = resolve_term("FOLFOX", index)

    assert result.match_status == "UNMATCHED"
    assert result.rxcui == ""


def test_regimen_r_chop_does_not_expand_to_rituximab():
    index = RxnConsoIndex([conso_row("121191", "Rituximab")])

    result = resolve_term("R-CHOP", index)

    assert result.match_status == "UNMATCHED"
    assert result.rxcui == ""


def test_numeric_only_term_is_skipped():
    index = RxnConsoIndex([conso_row("999", "ZSTK-474")])

    result = resolve_term("474", index)

    assert result.match_status == "UNMATCHED"
    assert result.match_stage == "SKIPPED_AMBIGUOUS_SHORT_TERM"
    assert result.rxcui == ""


def test_obvious_fragment_term_is_skipped():
    index = RxnConsoIndex([conso_row("999", "2-amino-1,3-propanediol")])

    result = resolve_term("2-amino-1", index)

    assert result.match_status == "UNMATCHED"
    assert result.match_stage == "SKIPPED_AMBIGUOUS_SHORT_TERM"
    assert result.rxcui == ""


def test_broad_class_terms_are_skipped():
    index = RxnConsoIndex(
        [
            conso_row("111", "Taxane derivative"),
            conso_row("222", "Statin"),
            conso_row("333", "calcium channel blocker"),
        ]
    )

    for term in ["Taxane", "Statin", "Calcium channel blocker"]:
        result = resolve_term(term, index)
        assert result.match_status == "UNMATCHED"
        assert result.match_stage == "SKIPPED_AMBIGUOUS_SHORT_TERM"
        assert result.rxcui == ""


def test_explicitly_curated_short_single_agent_alias_still_matches():
    index = RxnConsoIndex([conso_row("4492", "Fluorouracil")])

    result = resolve_term("5-FU", index)

    assert result.match_status == "MATCHED"
    assert result.rxcui == "4492"