from __future__ import annotations

import time

import pandas as pd

from aus_trial_universe.anzctr.eligibility.i_download_trials_and_extract_eligibility.ii_extract_drugs import (
    LLM_DRUGS_TO_ADD_COLUMN,
    LLM_DRUGS_TO_CORRECT_COLUMN,
    LLM_DRUG_TO_REMOVE_COLUMN,
    LLM_REASONING_COLUMN,
    RXNORM_MATCHED_DRUGS_COLUMN,
    RXNORM_MATCHED_INTERVENTIONS_COLUMN,
    RXNORM_MATCHED_TITLE_COLUMN,
    DrugExtractionReview,
    DrugMentionLexicon,
    append_drug_columns,
    build_llm_review_prompts,
    llm_review_columns,
)
from aus_trial_universe.ctgov.drug_ontology.identity.rxnorm.matcher import (
    RxnConsoIndex,
    RxnConsoRow,
)


def row(rxcui: str, tty: str, value: str, sab: str = "RXNORM") -> RxnConsoRow:
    return RxnConsoRow(
        rxcui=rxcui,
        lat="ENG",
        ts="",
        lui="",
        stt="",
        sui="",
        ispref="Y",
        rxaui="",
        saui="",
        scui="",
        sdui="",
        sab=sab,
        tty=tty,
        code="",
        str_value=value,
        srl="",
        suppress="N",
        cvf="",
    )


def test_append_drug_columns_extracts_rxnorm_matches_and_keeps_empty_llm_review():
    conso_index = RxnConsoIndex(
        [
            row("1", "IN", "Capecitabine"),
            row("1", "BN", "Xeloda"),
            row("2", "IN", "Bevacizumab"),
            row("2", "BN", "Avastin"),
            row("3", "IN", "Mitomycin C"),
            row("4", "IN", "Drug"),
            row("5", "BN", "Therapeutic"),
            row("6", "BN", "Active", sab="MMSL"),
            row("7", "BN", "Perform"),
            row("8", "IN", "cyclohexane"),
            row("9", "IN", "water"),
            row("10", "IN", "sodium chloride"),
            row("11", "IN", "creatinine"),
        ]
    )
    lexicon = DrugMentionLexicon.from_rxnorm_index(conso_index)
    frame = pd.DataFrame(
        {
            "ACTRN": ["ACTRN1"],
            "SCIENTIFIC TITLE": ["Capecitabine trial"],
            "INTERVENTIONS": [
                "Capecitabine plus Avastin and Mitomycin C. "
                "Study drug is continued as active therapeutic treatment. "
                "Staff perform dosing checks for a cyclohexane-labelled compound. "
                "Water and sodium chloride are used as diluents, and creatinine is monitored."
            ],
        }
    )

    output = append_drug_columns(
        frame,
        lexicon=lexicon,
        conso_index=conso_index,
        rel_index=None,
    )

    assert output[RXNORM_MATCHED_INTERVENTIONS_COLUMN].tolist() == [
        "Capecitabine | Bevacizumab | Mitomycin C"
    ]
    assert output[RXNORM_MATCHED_TITLE_COLUMN].tolist() == ["Capecitabine"]
    assert output[RXNORM_MATCHED_DRUGS_COLUMN].tolist() == [
        "Capecitabine | Bevacizumab | Mitomycin C"
    ]
    assert output[LLM_DRUG_TO_REMOVE_COLUMN].tolist() == [""]
    assert output[LLM_DRUGS_TO_ADD_COLUMN].tolist() == [""]
    assert output[LLM_DRUGS_TO_CORRECT_COLUMN].tolist() == [""]
    assert output[LLM_REASONING_COLUMN].tolist() == [""]


def test_append_drug_columns_uses_curated_anzctr_drug_rescues():
    conso_index = RxnConsoIndex([])
    lexicon = DrugMentionLexicon.from_rxnorm_index(conso_index)
    frame = pd.DataFrame(
        {
            "ACTRN": ["ACTRN1"],
            "SCIENTIFIC TITLE": ["Phenoxodiol study"],
            "INTERVENTIONS": [
                "Oral Phenoxodiol with lignocaine, adrenaline, and oestriol."
            ],
        }
    )

    output = append_drug_columns(
        frame,
        lexicon=lexicon,
        conso_index=conso_index,
        rel_index=None,
    )

    assert output[RXNORM_MATCHED_DRUGS_COLUMN].tolist() == [
        "Phenoxodiol | lidocaine | epinephrine | estriol"
    ]


def test_append_drug_columns_extracts_title_matches_and_combines_with_deduping():
    conso_index = RxnConsoIndex(
        [
            row("1", "IN", "Capecitabine"),
            row("2", "IN", "Bevacizumab"),
        ]
    )
    lexicon = DrugMentionLexicon.from_rxnorm_index(conso_index)
    frame = pd.DataFrame(
        {
            "ACTRN": ["ACTRN1"],
            "SCIENTIFIC TITLE": ["Capecitabine and Bevacizumab title"],
            "INTERVENTIONS": ["Capecitabine tablets."],
        }
    )

    output = append_drug_columns(
        frame,
        lexicon=lexicon,
        conso_index=conso_index,
        rel_index=None,
    )

    assert output[RXNORM_MATCHED_INTERVENTIONS_COLUMN].tolist() == ["Capecitabine"]
    assert output[RXNORM_MATCHED_TITLE_COLUMN].tolist() == [
        "Capecitabine | Bevacizumab"
    ]
    assert output[RXNORM_MATCHED_DRUGS_COLUMN].tolist() == [
        "Capecitabine | Bevacizumab"
    ]


def test_append_drug_columns_filters_prior_treatment_contexts():
    conso_index = RxnConsoIndex(
        [
            row("1", "IN", "Docetaxel"),
            row("2", "IN", "Paclitaxel"),
            row("3", "IN", "Irinotecan"),
            row("4", "IN", "Fluorouracil"),
        ]
    )
    lexicon = DrugMentionLexicon.from_rxnorm_index(conso_index)
    frame = pd.DataFrame(
        {
            "ACTRN": ["ACTRN1", "ACTRN2"],
            "SCIENTIFIC TITLE": [
                "Weekly docetaxel for patients who have previously received paclitaxel",
                "Irinotecan for patients who failed 5-FU based chemotherapy",
            ],
            "INTERVENTIONS": [
                "4 cycles of docetaxel.",
                "Irinotecan treatment after previously received 5-FU chemotherapy.",
            ],
        }
    )

    output = append_drug_columns(
        frame,
        lexicon=lexicon,
        conso_index=conso_index,
        rel_index=None,
    )

    assert output[RXNORM_MATCHED_DRUGS_COLUMN].tolist() == [
        "Docetaxel",
        "Irinotecan",
    ]


def test_prior_treatment_filter_keeps_new_intervention_after_reset_phrase():
    conso_index = RxnConsoIndex(
        [
            row("1", "IN", "Letrozole"),
            row("2", "IN", "Everolimus"),
            row("3", "IN", "Cisplatin"),
        ]
    )
    lexicon = DrugMentionLexicon.from_rxnorm_index(conso_index)
    frame = pd.DataFrame(
        {
            "ACTRN": ["ACTRN1"],
            "SCIENTIFIC TITLE": ["Everolimus with letrozole"],
            "INTERVENTIONS": [
                "Patients who have previously failed on cisplatin chemotherapy "
                "will be treated with letrozole and everolimus."
            ],
        }
    )

    output = append_drug_columns(
        frame,
        lexicon=lexicon,
        conso_index=conso_index,
        rel_index=None,
    )

    assert output[RXNORM_MATCHED_INTERVENTIONS_COLUMN].tolist() == [
        "Letrozole | Everolimus"
    ]
    assert output[RXNORM_MATCHED_DRUGS_COLUMN].tolist() == [
        "Letrozole | Everolimus"
    ]


def test_append_drug_columns_blocks_g_csf_expansion_and_non_drug_artifacts():
    conso_index = RxnConsoIndex(
        [
            row("1", "IN", "Filgrastim"),
            row("2", "IN", "Pegfilgrastim"),
            row("3", "IN", "Cytosine"),
            row("4", "IN", "Cytarabine"),
            row("5", "IN", "Hyaluronate"),
            row("6", "IN", "Hyaluronic Acid"),
        ]
    )
    lexicon = DrugMentionLexicon.from_rxnorm_index(conso_index)
    frame = pd.DataFrame(
        {
            "ACTRN": ["ACTRN1"],
            "SCIENTIFIC TITLE": ["Cytosine Arabinoside and G-CSF study"],
            "INTERVENTIONS": [
                "G-CSF mobilised PBSC. Cytosine Arabinoside (AraC) and "
                "hyaluronic acid support are described."
            ],
        }
    )

    output = append_drug_columns(
        frame,
        lexicon=lexicon,
        conso_index=conso_index,
        rel_index=None,
    )

    assert output[RXNORM_MATCHED_DRUGS_COLUMN].tolist() == ["Cytarabine"]


def test_llm_review_columns_flattens_remove_add_and_corrections():
    assert llm_review_columns(
        {
            "drugs_to_remove": ["water", "water"],
            "drugs_to_add": ["Phenoxodiol"],
            "drugs_to_correct": [
                {"from": "VMCL vaccine", "to": "VMCL"},
                {"from": "old name", "to": "new name"},
            ],
        }
    ) == (
        "water",
        "Phenoxodiol",
        "VMCL vaccine => VMCL | old name => new name",
    )


def test_llm_review_prompt_prefers_canonical_names_and_requests_summary():
    _, user_prompt = build_llm_review_prompts(
        "A trial of docetaxel (Taxotere)",
        "Docetaxel administered weekly.",
        ["docetaxel"],
    )
    compact_prompt = " ".join(user_prompt.split())

    assert "Do not add brand names" in compact_prompt
    assert "do not add Taxotere when docetaxel is present" in compact_prompt
    assert '"summary_text": "brief reasoning summary"' in compact_prompt


def test_append_drug_columns_writes_llm_reasoning_without_api_call():
    conso_index = RxnConsoIndex([row("1", "IN", "Docetaxel")])
    lexicon = DrugMentionLexicon.from_rxnorm_index(conso_index)

    def fake_reviewer(*args, **kwargs):
        return DrugExtractionReview(
            drugs_to_add=("Phenoxodiol",),
            summary_text="Phenoxodiol is named in the intervention text.",
        )

    frame = pd.DataFrame(
        {
            "ACTRN": ["ACTRN1"],
            "SCIENTIFIC TITLE": ["Docetaxel study"],
            "INTERVENTIONS": ["Docetaxel administered weekly."],
        }
    )

    output = append_drug_columns(
        frame,
        lexicon=lexicon,
        conso_index=conso_index,
        rel_index=None,
        llm_review=True,
        llm_reviewer=fake_reviewer,
    )

    assert output[LLM_DRUGS_TO_ADD_COLUMN].tolist() == ["Phenoxodiol"]
    assert output[LLM_REASONING_COLUMN].tolist() == [
        "Phenoxodiol is named in the intervention text."
    ]


def test_append_drug_columns_forwards_llm_retry_settings():
    conso_index = RxnConsoIndex([row("1", "IN", "Docetaxel")])
    lexicon = DrugMentionLexicon.from_rxnorm_index(conso_index)
    calls = []

    def fake_reviewer(*args, **kwargs):
        calls.append(kwargs)
        return DrugExtractionReview(summary_text="reviewed")

    frame = pd.DataFrame(
        {
            "ACTRN": ["ACTRN1"],
            "SCIENTIFIC TITLE": ["Docetaxel study"],
            "INTERVENTIONS": ["Docetaxel administered weekly."],
        }
    )

    append_drug_columns(
        frame,
        lexicon=lexicon,
        conso_index=conso_index,
        rel_index=None,
        llm_review=True,
        llm_max_retries=9,
        llm_retry_initial_delay_seconds=2.5,
        llm_retry_max_delay_seconds=45.0,
        llm_reviewer=fake_reviewer,
    )

    assert calls == [
        {
            "model": None,
            "max_retries": 9,
            "retry_initial_delay_seconds": 2.5,
            "retry_max_delay_seconds": 45.0,
        }
    ]


def test_append_drug_columns_parallel_llm_review_preserves_row_order():
    conso_index = RxnConsoIndex(
        [
            row("1", "IN", "Docetaxel"),
            row("2", "IN", "Capecitabine"),
            row("3", "IN", "Imatinib"),
        ]
    )
    lexicon = DrugMentionLexicon.from_rxnorm_index(conso_index)

    def fake_reviewer(scientific_title, interventions, rxnorm_matched_drugs, **kwargs):
        if scientific_title == "first":
            time.sleep(0.03)
        elif scientific_title == "second":
            time.sleep(0.01)
        else:
            time.sleep(0.02)
        return DrugExtractionReview(
            drugs_to_add=(f"add-{scientific_title}",),
            summary_text=f"reason-{scientific_title}",
        )

    frame = pd.DataFrame(
        {
            "ACTRN": ["ACTRN1", "ACTRN2", "ACTRN3"],
            "SCIENTIFIC TITLE": ["first", "second", "third"],
            "INTERVENTIONS": [
                "Docetaxel administered weekly.",
                "Capecitabine administered orally.",
                "Imatinib administered orally.",
            ],
        }
    )

    output = append_drug_columns(
        frame,
        lexicon=lexicon,
        conso_index=conso_index,
        rel_index=None,
        llm_review=True,
        llm_workers=3,
        llm_reviewer=fake_reviewer,
    )

    assert output[LLM_DRUGS_TO_ADD_COLUMN].tolist() == [
        "add-first",
        "add-second",
        "add-third",
    ]
    assert output[LLM_REASONING_COLUMN].tolist() == [
        "reason-first",
        "reason-second",
        "reason-third",
    ]


def test_append_drug_columns_rejects_invalid_llm_worker_count():
    conso_index = RxnConsoIndex([])
    lexicon = DrugMentionLexicon([])

    try:
        append_drug_columns(
            pd.DataFrame(
                {
                    "ACTRN": ["ACTRN1"],
                    "SCIENTIFIC TITLE": ["Title"],
                    "INTERVENTIONS": ["Intervention"],
                }
            ),
            lexicon=lexicon,
            conso_index=conso_index,
            rel_index=None,
            llm_workers=0,
        )
    except ValueError as error:
        assert "llm_workers" in str(error)
    else:
        raise AssertionError("Expected invalid llm_workers to raise")


def test_append_drug_columns_requires_interventions_column():
    conso_index = RxnConsoIndex([])
    lexicon = DrugMentionLexicon([])

    try:
        append_drug_columns(
            pd.DataFrame({"ACTRN": ["ACTRN1"]}),
            lexicon=lexicon,
            conso_index=conso_index,
            rel_index=None,
        )
    except ValueError as error:
        assert "INTERVENTIONS" in str(error)
    else:
        raise AssertionError("Expected missing INTERVENTIONS column to raise")
