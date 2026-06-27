from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import pytest

from aus_trial_universe.eligibility_path.anzctr.i_download_trials_and_extract_eligibility import (
    iii_extract_drugs as drug_module,
)
from aus_trial_universe.eligibility_path.anzctr.i_download_trials_and_extract_eligibility.iii_extract_drugs import (
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
    ensure_input_csv,
    extract_drugs_to_csv,
    llm_review_columns,
    resolve_rxnorm_rrf_dir,
)
from aus_trial_universe.drug_utility_path.ctgov.drug_ontology.identity.rxnorm.matcher import (
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


def test_ensure_input_csv_missing_without_create_raises_helpful_error(tmp_path: Path):
    input_csv = tmp_path / "missing.csv"

    with pytest.raises(FileNotFoundError, match="--create_input_csv"):
        ensure_input_csv(input_csv, input_xlsx=tmp_path / "raw.xlsx")


def test_ensure_input_csv_creates_from_workbook_when_requested(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    input_csv = tmp_path / "nested" / "anzctr_field_extractions.csv"
    input_xlsx = tmp_path / "anzctr_input.xlsx"
    calls: list[tuple[Path, Path]] = []

    def fake_extract_fields_to_csv(
        xlsx: str | Path, output: str | Path, pottr_trial_ids=None
    ) -> Path:
        xlsx_path = Path(xlsx)
        output_path = Path(output)
        calls.append((xlsx_path, output_path))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("ACTRN,SCIENTIFIC TITLE,INTERVENTIONS\n", encoding="utf-8")
        return output_path

    monkeypatch.setattr(
        "aus_trial_universe.eligibility_path.anzctr."
        "i_download_trials_and_extract_eligibility.iii_extract_drugs."
        "extract_fields_to_csv",
        fake_extract_fields_to_csv,
    )

    result = ensure_input_csv(
        input_csv,
        input_xlsx=input_xlsx,
        create_input_csv=True,
    )

    assert result == input_csv
    assert input_csv.exists()
    assert calls == [(input_xlsx, input_csv)]


def test_extract_drugs_refreshes_canonical_csv_without_losing_llm_reviews(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    canonical_csv = tmp_path / "anzctr_field_extractions.csv"
    input_xlsx = tmp_path / "anzctr_input.xlsx"
    rxnorm_dir = tmp_path / "rxnorm"
    rxnorm_dir.mkdir()

    pd.DataFrame(
        {
            "ACTRN": ["ACTRN1"],
            "SCIENTIFIC TITLE": ["Old title"],
            "INTERVENTIONS": ["Old intervention"],
            LLM_DRUG_TO_REMOVE_COLUMN: ["old removal"],
            LLM_DRUGS_TO_ADD_COLUMN: ["old add"],
            LLM_DRUGS_TO_CORRECT_COLUMN: ["old correction"],
            LLM_REASONING_COLUMN: ["old reasoning"],
        }
    ).to_csv(canonical_csv, index=False)

    created_base_paths: list[Path] = []

    def fake_extract_fields_to_csv(
        xlsx: str | Path, output: str | Path, pottr_trial_ids=None
    ) -> Path:
        output_path = Path(output)
        created_base_paths.append(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            {
                "ACTRN": ["ACTRN1"],
                "SCIENTIFIC TITLE": ["Fresh title"],
                "INTERVENTIONS": ["Fresh intervention"],
            }
        ).to_csv(output_path, index=False)
        return output_path

    def fake_append_drug_columns(frame: pd.DataFrame, **_: object) -> pd.DataFrame:
        out = frame.copy()
        out[RXNORM_MATCHED_INTERVENTIONS_COLUMN] = ["FreshDrug"]
        out[RXNORM_MATCHED_TITLE_COLUMN] = [""]
        out[RXNORM_MATCHED_DRUGS_COLUMN] = ["FreshDrug"]
        out[LLM_DRUG_TO_REMOVE_COLUMN] = [""]
        out[LLM_DRUGS_TO_ADD_COLUMN] = [""]
        out[LLM_DRUGS_TO_CORRECT_COLUMN] = [""]
        out[LLM_REASONING_COLUMN] = [""]
        return out

    monkeypatch.setattr(drug_module, "extract_fields_to_csv", fake_extract_fields_to_csv)
    monkeypatch.setattr(drug_module, "resolve_rxnorm_rrf_dir", lambda *args, **kwargs: rxnorm_dir)
    monkeypatch.setattr(drug_module.RxnConsoIndex, "from_rrf_dir", lambda *_: object())
    monkeypatch.setattr(drug_module.DrugMentionLexicon, "from_rxnorm_index", lambda *_: object())
    monkeypatch.setattr(drug_module.RxnRelIndex, "from_rrf_dir", lambda *_: object())
    monkeypatch.setattr(drug_module, "append_drug_columns", fake_append_drug_columns)

    result = extract_drugs_to_csv(
        canonical_csv,
        canonical_csv,
        input_xlsx=input_xlsx,
        refresh_input_csv=True,
        rxnorm_rrf_dir=rxnorm_dir,
    )

    output = pd.read_csv(result, dtype=str, keep_default_na=False)

    assert result == canonical_csv
    assert created_base_paths
    assert canonical_csv not in created_base_paths
    assert output["SCIENTIFIC TITLE"].tolist() == ["Fresh title"]
    assert output[RXNORM_MATCHED_DRUGS_COLUMN].tolist() == ["FreshDrug"]
    assert output[LLM_DRUG_TO_REMOVE_COLUMN].tolist() == ["old removal"]
    assert output[LLM_DRUGS_TO_ADD_COLUMN].tolist() == ["old add"]
    assert output[LLM_DRUGS_TO_CORRECT_COLUMN].tolist() == ["old correction"]
    assert output[LLM_REASONING_COLUMN].tolist() == ["old reasoning"]


def write_rxnorm_rrf_files(path: Path, *, include_rel: bool = True) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "RXNCONSO.RRF").write_text("", encoding="utf-8")
    if include_rel:
        (path / "RXNREL.RRF").write_text("", encoding="utf-8")


def test_resolve_rxnorm_rrf_dir_accepts_concrete_rrf_dir(tmp_path: Path):
    rrf_dir = tmp_path / "version_01012026"
    write_rxnorm_rrf_files(rrf_dir)

    assert resolve_rxnorm_rrf_dir(rrf_dir) == rrf_dir


def test_resolve_rxnorm_rrf_dir_selects_latest_valid_version(tmp_path: Path):
    older_dir = tmp_path / "version_31122025"
    newer_dir = tmp_path / "version_01012026"
    invalid_newer_dir = tmp_path / "version_02012026"
    write_rxnorm_rrf_files(older_dir)
    write_rxnorm_rrf_files(newer_dir)
    invalid_newer_dir.mkdir()
    (invalid_newer_dir / "RXNCONSO.RRF").write_text("", encoding="utf-8")

    assert resolve_rxnorm_rrf_dir(tmp_path) == newer_dir


def test_resolve_rxnorm_rrf_dir_can_skip_rxnrel_when_ingredient_resolution_disabled(
    tmp_path: Path,
):
    rrf_dir = tmp_path / "version_01012026"
    write_rxnorm_rrf_files(rrf_dir, include_rel=False)

    assert (
        resolve_rxnorm_rrf_dir(
            tmp_path,
            ingredient_resolution=False,
        )
        == rrf_dir
    )


def test_resolve_rxnorm_rrf_dir_missing_files_raises_helpful_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "aus_trial_universe.eligibility_path.anzctr."
        "i_download_trials_and_extract_eligibility.iii_extract_drugs."
        "RXNORM_RRF_ROOT_CANDIDATES",
        (),
    )

    with pytest.raises(FileNotFoundError, match="RXNCONSO.RRF"):
        resolve_rxnorm_rrf_dir(tmp_path)
