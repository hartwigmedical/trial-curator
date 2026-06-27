from __future__ import annotations

import zipfile
from pathlib import Path

import pandas as pd

from aus_trial_universe.eligibility_path.anzctr.i_download_trials_and_extract_eligibility import (
    i_download_trials as download_module,
)
from aus_trial_universe.eligibility_path.anzctr.i_download_trials_and_extract_eligibility.i_download_trials import (
    DEFAULT_INITIAL_SEARCH_PARAMETERS,
    POTTR_APPEND_INPUT_FILENAME,
    ParsedAnzctrTrial,
    append_trials_to_workbook,
    devexpress_listbox_state,
    download_trials_to_input_workbook,
    filter_workbook_to_trial_ids,
    matched_trial_ids,
    parse_form_fields,
    parsed_trials_from_workbook,
    read_trial_ids,
)


def write_base_workbook(path: Path) -> None:
    trial_rows = pd.DataFrame(
        {
            "TRIAL ID": [1],
            "ACTRN": ["12623000000000"],
            "SUBMIT DATE": ["1/01/2023"],
            "APPROVAL DATE": ["2/01/2023"],
            "STUDY TITLE": ["Existing trial"],
            "SCIENTIFIC TITLE": ["Existing scientific trial"],
            "UTN": [""],
            "TRIAL ACRONYM": [""],
            "RELATED TRIAL RECORDS": [""],
            "INTERVENTIONS": ["Existing drug"],
            "COMPARATOR": [""],
            "CONTROL": [""],
            "INCLUSIVE CRITERIA": ["Existing include"],
            "MIN AGE": [18],
            "MIN AGE TYPE": ["Years"],
            "MAX AGE": [80],
            "MAX AGE TYPE": ["Years"],
            "INCLUSIVE GENDER": ["All"],
            "HEALTHY VOLUNTEERS?": ["No"],
            "EXCLUSIVE CRITERIA": ["Existing exclude"],
            "STUDY TYPE": ["Interventional"],
            "PURPOSE": ["Treatment"],
            "ALLOCATION": [""],
            "CONCEALMENT": [""],
            "SEQUENCE": [""],
            "MASKING": [""],
            "ASSIGNMENT": [""],
            "OTHER DESIGN FEATURES": [""],
            "PHASE": ["Phase 1"],
            "STATISTICAL METHODS": [""],
            "ANTICIPATED START DATE": [""],
            "ACTUAL START DATE": ["1/03/2023"],
            "ANTICIPATED END DATE": [""],
            "ACTUAL END DATE": [""],
            "TARGET SAMPLE SIZE": [50],
            "FINAL SAMPLE SIZE": [""],
            "CURRENT SAMPLE SIZE": [""],
            "ANTICIPATED LAST VISIT DATE": [""],
            "ACTUAL LAST VISIT DATE": [""],
            "RECRUITMENT STATUS": ["Recruiting"],
            "RECRUITMENT COUNTRY": ["Australia"],
            "RECRUITMENT STATE": ["NSW"],
            "PRIMARY SPONSOR TYPE": ["Hospital"],
            "PRIMARY SPONSOR NAME": ["Existing Sponsor"],
            "PRIMARY SPONSOR COUNTRY": ["Australia"],
            "ETHICS STATUS": ["Approved"],
            "BRIEF SUMMARY": [""],
            "TRIAL WEBSITE": [""],
            "PUBLICATION": [""],
            "PUBLIC NOTES": [""],
        }
    )
    health_rows = pd.DataFrame(
        {"TRIAL ID": [1], "HEALTH CONDITION": ["Existing cancer"]}
    )
    intervention_code_rows = pd.DataFrame(
        {"TRIAL ID": [1], "INTERVENTION CODE": ["Treatment: Drugs"]}
    )
    other_rows = pd.DataFrame({"VALUE": ["preserved"]})

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        trial_rows.to_excel(writer, sheet_name="TRIAL", index=False)
        health_rows.to_excel(writer, sheet_name="HEALTH CONDITION", index=False)
        intervention_code_rows.to_excel(
            writer,
            sheet_name="INTERVENTION CODE",
            index=False,
        )
        other_rows.to_excel(writer, sheet_name="OTHER SHEET", index=False)


# ---------------------------------------------------------------------------
# Whole-registry export fixtures (the bulk download ANZCTR actually offers).
# Eight trials exercise every advanced-search filter; exactly {1, 2, 6} match
# the default cancer/drug cohort.
# ---------------------------------------------------------------------------
def build_all_trials_frames() -> dict[str, pd.DataFrame]:
    trial = pd.DataFrame(
        {
            "TRIAL ID": [1, 2, 3, 4, 5, 6, 7, 8],
            "ACTRN": [
                "12600000000001",
                "12600000000002",
                "12600000000003",
                "12600000000004",
                "12600000000005",
                "12600000000006",
                "12600000000007",
                "12600000000008",
            ],
            "STUDY TITLE": [f"Trial {n}" for n in range(1, 9)],
            "STUDY TYPE": [
                "Interventional",  # 1 match
                "Interventional",  # 2 match (NZ)
                "Interventional",  # 3 excluded: status
                "Observational",   # 4 excluded: study type
                "Interventional",  # 5 excluded: outside, not NZ
                "Interventional",  # 6 match (blank country + AU postcode)
                "Interventional",  # 7 excluded: condition category
                "Interventional",  # 8 excluded: intervention code
            ],
            "RECRUITMENT STATUS": [
                "Recruiting",
                "Not yet recruiting",
                "Completed",
                "Recruiting",
                "Recruiting",
                "Active, not recruiting",
                "Recruiting",
                "Recruiting",
            ],
            "RECRUITMENT COUNTRY": [
                "Australia",
                ",Outside",
                "Australia",
                "Australia",
                ",Outside",
                "",
                "Australia",
                "Australia",
            ],
        }
    )
    condition = pd.DataFrame(
        {
            "TRIAL ID": [1, 2, 3, 4, 5, 6, 7, 8],
            "CONDITION CATEGORY": [
                "Cancer",
                "Cancer",
                "Cancer",
                "Cancer",
                "Cancer",
                "Cancer",
                "Cardiovascular",  # 7 not cancer
                "Cancer",
            ],
            "CONDITION CODE": ["code"] * 8,
        }
    )
    intervention = pd.DataFrame(
        {
            "TRIAL ID": [1, 2, 3, 4, 5, 6, 7, 8],
            "INTERVENTION CODE": [
                "Treatment: Drugs",
                "Treatment: Other",
                "Treatment: Drugs",
                "Treatment: Drugs",
                "Treatment: Drugs",
                "Treatment: Drugs",
                "Treatment: Drugs",
                "Treatment: Surgery",  # 8 not drugs/other
            ],
        }
    )
    health = pd.DataFrame(
        {
            "TRIAL ID": [1, 2, 6],
            "HEALTH CONDITION": ["Breast cancer", "Lung cancer", "Bowel cancer"],
        }
    )
    country_outside = pd.DataFrame(
        {
            "TRIAL ID": [2, 5],
            "COUNTRY": ["New Zealand", "United States"],
            "STATE": ["", ""],
        }
    )
    postcode = pd.DataFrame({"TRIAL ID": [6], "POSTCODE": ["2000"]})
    return {
        "TRIAL": trial,
        "HEALTH CONDITION": health,
        "INTERVENTION CODE": intervention,
        "CONDITION  CODE": condition,  # double space mirrors the real export
        "COUNTRY OUTSIDE AUSTRALIA": country_outside,
        "POSTCODE": postcode,
    }


def build_all_trials_zip(path: Path) -> None:
    frames = build_all_trials_frames()
    workbook_path = path.parent / "TrialDetails.xlsx"
    with pd.ExcelWriter(workbook_path, engine="openpyxl") as writer:
        for sheet, frame in frames.items():
            frame.to_excel(writer, sheet_name=sheet, index=False)
    with zipfile.ZipFile(path, "w") as archive:
        archive.write(workbook_path, "TrialDetails.xlsx")
        archive.writestr("ANZCTR_data_dictionary.pdf", b"%PDF-1.4 stub")


class FakeResponse:
    def __init__(self, *, text: str = "", content: bytes = b"", headers=None):
        self.text = text
        self.content = content
        self.headers = headers or {}


class FakeAnzctrSession:
    """Stands in for a curl_cffi session driving TrialSearch.aspx."""

    def __init__(self, *, form_html: str, delta_text: str, results_html: str, zip_bytes: bytes):
        self._form_html = form_html
        self._delta_text = delta_text
        self._results_html = results_html
        self._zip_bytes = zip_bytes
        self.calls: list[str] = []

    def get(self, url, timeout=None):  # noqa: ARG002 - signature parity
        self.calls.append("get")
        return FakeResponse(text=self._form_html)

    def post(self, url, data=None, headers=None, timeout=None):  # noqa: ARG002
        data = data or {}
        if data.get("__ASYNCPOST") == "true":
            self.calls.append("async_condition_category")
            return FakeResponse(text=self._delta_text)
        if download_module.DOWNLOAD_BUTTON_FIELD in data:
            self.calls.append("download")
            return FakeResponse(
                content=self._zip_bytes,
                headers={"content-type": "application/x-compressed"},
            )
        if download_module.SEARCH_BUTTON_FIELD in data:
            self.calls.append("search")
            return FakeResponse(text=self._results_html)
        self.calls.append("other")
        return FakeResponse(text="")


def test_curl_cffi_dependency_is_importable():
    # The unit tests mock the HTTP session, so this is the only check that the
    # runtime dependency the live download needs is actually installed in the
    # environment that runs the suite (and therefore the pipeline).
    import curl_cffi  # noqa: F401


def test_cloudflare_challenge_page_is_detected():
    html = """
    <title>Managed Challenge / I'm Under Attack Mode</title>
    <noscript>Enable JavaScript and cookies to continue</noscript>
    <script src="/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1"></script>
    """

    assert download_module.is_cloudflare_challenge_page(html)


def test_raise_if_challenge_ignores_beacon_on_real_form():
    # The real form embeds the challenge-platform beacon but carries __VIEWSTATE.
    real_form = FakeResponse(
        text='<input name="__VIEWSTATE" value="x"/>'
        '<script src="/cdn-cgi/challenge-platform/..."></script>'
    )
    download_module._raise_if_challenge(real_form)  # must not raise

    challenge = FakeResponse(
        text="<title>Managed Challenge / I'm Under Attack Mode</title>"
    )
    try:
        download_module._raise_if_challenge(challenge)
    except download_module.AnzctrCloudflareChallengeError:
        pass
    else:
        raise AssertionError("Expected a challenge page to be flagged")


# ---------------------------------------------------------------------------
# curl_cffi request/response plumbing.
# ---------------------------------------------------------------------------
def test_parse_form_fields_reads_inputs_and_selected_options():
    html = """
    <form>
      <input type="hidden" name="__VIEWSTATE" value="vs0"/>
      <input type="text" name="ctl00$body$searchTxtBx" value=""/>
      <input type="submit" name="ctl00$body$btnSearch" value="SEARCH"/>
      <input type="checkbox" name="cb_off"/>
      <input type="checkbox" name="cb_on" value="1" checked/>
      <select name="ctl00$body$registryRdBtnLst">
        <option value="All">All</option>
        <option value="ANZCTR" selected="selected">ANZCTR</option>
      </select>
    </form>
    """
    fields = parse_form_fields(html)

    assert fields["__VIEWSTATE"] == "vs0"
    assert fields["ctl00$body$searchTxtBx"] == ""
    assert fields["ctl00$body$registryRdBtnLst"] == "ANZCTR"
    assert fields["cb_on"] == "1"
    assert "cb_off" not in fields  # unchecked checkbox is not submitted
    assert "ctl00$body$btnSearch" not in fields  # submit buttons added explicitly


def test_async_delta_value_extracts_hidden_field():
    delta = "1|#||4|hiddenField|__VIEWSTATE|vs-new|hiddenField|__EVENTVALIDATION|ev-new|"
    assert download_module._async_delta_value(delta, "__VIEWSTATE") == "vs-new"
    assert download_module._async_delta_value(delta, "__EVENTVALIDATION") == "ev-new"
    assert download_module._async_delta_value(delta, "__MISSING") is None


def test_devexpress_listbox_state_is_length_prefixed():
    assert (
        devexpress_listbox_state(["Treatment: Drugs", "Treatment: Other"])
        == "16|Treatment: Drugs16|Treatment: Other"
    )
    assert devexpress_listbox_state(["Australia", "New Zealand"]) == "9|Australia11|New Zealand"
    assert devexpress_listbox_state([]) == ""


# ---------------------------------------------------------------------------
# Whole-registry filter (replaces ANZCTR's missing "download my results").
# ---------------------------------------------------------------------------
def test_matched_trial_ids_applies_every_advanced_search_filter():
    frames = build_all_trials_frames()

    matched = matched_trial_ids(frames, DEFAULT_INITIAL_SEARCH_PARAMETERS)

    # 1: AU + drugs + cancer + interventional + recruiting
    # 2: NZ (Outside + COUNTRY OUTSIDE row) + other + cancer + interventional
    # 6: blank country backed by an AU postcode
    assert matched == {"1", "2", "6"}


def test_filter_workbook_to_trial_ids_keeps_linked_rows():
    frames = build_all_trials_frames()

    filtered = filter_workbook_to_trial_ids(frames, {"1", "2", "6"})

    assert filtered["TRIAL"]["TRIAL ID"].astype(str).tolist() == ["1", "2", "6"]
    assert filtered["COUNTRY OUTSIDE AUSTRALIA"]["TRIAL ID"].astype(str).tolist() == ["2"]
    assert filtered["POSTCODE"]["TRIAL ID"].astype(str).tolist() == ["6"]


def test_parsed_trials_from_workbook_builds_records_for_requested_actrns():
    frames = build_all_trials_frames()

    parsed = parsed_trials_from_workbook(frames, ["ACTRN12600000000002"])

    assert len(parsed) == 1
    assert parsed[0].actrn == "ACTRN12600000000002"
    assert parsed[0].trial_row["ACTRN"] == "12600000000002"
    assert parsed[0].health_conditions == ["Lung cancer"]
    assert parsed[0].intervention_codes == ["Treatment: Other"]


# ---------------------------------------------------------------------------
# End-to-end initial search via the fake curl_cffi session.
# ---------------------------------------------------------------------------
def test_download_initial_search_workbook_filters_whole_registry_export(
    tmp_path: Path,
    monkeypatch,
):
    zip_path = tmp_path / "all_trials.zip"
    build_all_trials_zip(zip_path)
    zip_bytes = zip_path.read_bytes()

    form_html = """
    <form>
      <input type="hidden" name="__VIEWSTATE" value="vs0"/>
      <input type="hidden" name="__VIEWSTATEGENERATOR" value="gen0"/>
      <input type="hidden" name="__EVENTVALIDATION" value="ev0"/>
      <input type="hidden" name="ctl00$body$SessionID" value="sess0"/>
    </form>
    """
    delta_text = (
        "1|#||4|hiddenField|__VIEWSTATE|vs1|hiddenField|__EVENTVALIDATION|ev1|"
        "hiddenField|__VIEWSTATEGENERATOR|gen1|"
    )
    results_html = """
    <html><body>
      <span id="resultCountLbl">Number of records found: 3</span>
      <a href="TrialReview.aspx?id=1">ACTRN12600000000001</a>
      <a href="TrialReview.aspx?id=2">ACTRN12600000000002</a>
      <a href="TrialReview.aspx?id=6">ACTRN12600000000006</a>
      <form><input type="hidden" name="__VIEWSTATE" value="vs2"/>
      <input type="hidden" name="__EVENTVALIDATION" value="ev2"/></form>
    </body></html>
    """
    session = FakeAnzctrSession(
        form_html=form_html,
        delta_text=delta_text,
        results_html=results_html,
        zip_bytes=zip_bytes,
    )
    monkeypatch.setattr(download_module, "_new_anzctr_session", lambda: session)

    output = tmp_path / "version_26062026" / "01_initial_search_anzctr_input.xlsx"
    raw_dir = tmp_path / "raw_trials" / "version_26062026"

    output_path, manifest = download_module.download_initial_search_workbook(
        output_xlsx=output,
        raw_dir=raw_dir,
        timeout_ms=1234,
        retries=1,
    )

    assert output_path == output
    trial_frame = pd.read_excel(output_path, sheet_name="TRIAL", dtype=str).fillna("")
    assert trial_frame["ACTRN"].tolist() == [
        "12600000000001",
        "12600000000002",
        "12600000000006",
    ]
    assert manifest.loc[0, "status"] == "downloaded_initial_search_export"
    assert manifest.loc[0, "matched_trials"] == 3
    assert manifest.loc[0, "reported_count"] == 3
    assert (raw_dir / download_module.ALL_TRIALS_CACHE_FILENAME).exists()
    assert session.calls == ["get", "async_condition_category", "search", "download"]


def test_download_initial_search_workbook_raises_on_cloudflare(tmp_path: Path, monkeypatch):
    challenge_html = """
    <title>Managed Challenge / I'm Under Attack Mode</title>
    <script src="/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1"></script>
    """

    class ChallengeSession:
        def get(self, url, timeout=None):  # noqa: ARG002
            return FakeResponse(text=challenge_html)

        def post(self, *args, **kwargs):  # noqa: ARG002 - never reached
            raise AssertionError("search must not run after a challenge")

    monkeypatch.setattr(download_module, "_new_anzctr_session", lambda: ChallengeSession())

    try:
        download_module.download_initial_search_workbook(
            output_xlsx=tmp_path / "out.xlsx",
            raw_dir=tmp_path / "raw",
            timeout_ms=1234,
            retries=1,
        )
    except download_module.AnzctrCloudflareChallengeError:
        pass
    else:
        raise AssertionError("Expected a Cloudflare challenge to be raised")


# ---------------------------------------------------------------------------
# Workbook assembly (retained behaviour).
# ---------------------------------------------------------------------------
def test_default_initial_search_parameters_match_reviewed_anzctr_filters():
    params = DEFAULT_INITIAL_SEARCH_PARAMETERS

    assert params.registry == "ANZCTR"
    assert params.intervention_code_operator == "OR"
    assert params.intervention_codes == ("Treatment: Drugs", "Treatment: Other")
    assert params.study_type == "Interventional"
    assert params.recruitment_status == (
        "Not yet recruiting",
        "Recruiting",
        "Active, not recruiting",
    )
    assert params.condition_category == "Cancer"
    assert params.countries_operator == "OR"
    assert params.countries_of_recruitment == ("Australia", "New Zealand")


def test_read_trial_ids_accepts_missing_pottr_trial_id_column(tmp_path: Path):
    input_tsv = tmp_path / "missing_pottr_trials_26062026.tsv"
    pd.DataFrame(
        {
            "trialId": ["ACTRN12624000000000", "12624000000000", "NCT00000001"],
            "registry": ["anzctr", "anzctr", "ctgov"],
        }
    ).to_csv(input_tsv, sep="\t", index=False)

    assert read_trial_ids(input_tsv) == ["ACTRN12624000000000"]


def test_append_trials_to_workbook_preserves_sheets_and_appends_required_rows(
    tmp_path: Path,
):
    base_input = tmp_path / "base.xlsx"
    output = tmp_path / "version_26062026" / "anzctr_input.xlsx"
    write_base_workbook(base_input)
    parsed = ParsedAnzctrTrial(
        actrn="ACTRN12624000000000",
        trial_row={
            "ACTRN": "12624000000000",
            "STUDY TITLE": "Public cancer trial",
            "SCIENTIFIC TITLE": "Scientific cancer trial",
            "INTERVENTIONS": "Drug A daily.",
            "RECRUITMENT STATUS": "Recruiting",
        },
        health_conditions=["Breast cancer", "Lung cancer"],
        intervention_codes=["Treatment: Drugs", "Treatment: Radiotherapy"],
    )

    append_trials_to_workbook(
        base_input_xlsx=base_input,
        output_xlsx=output,
        parsed_trials=[parsed],
    )

    trial_frame = pd.read_excel(output, sheet_name="TRIAL", dtype=str).fillna("")
    health_frame = pd.read_excel(output, sheet_name="HEALTH CONDITION", dtype=str).fillna("")
    code_frame = pd.read_excel(output, sheet_name="INTERVENTION CODE", dtype=str).fillna("")
    other_frame = pd.read_excel(output, sheet_name="OTHER SHEET", dtype=str)

    assert trial_frame["ACTRN"].tolist() == ["12623000000000", "12624000000000"]
    assert trial_frame["TRIAL ID"].tolist() == ["1", "2"]
    new_health_conditions = health_frame.loc[
        health_frame["TRIAL ID"] == "2", "HEALTH CONDITION"
    ].tolist()
    new_intervention_codes = code_frame.loc[
        code_frame["TRIAL ID"] == "2", "INTERVENTION CODE"
    ].tolist()

    assert new_health_conditions == ["Breast cancer", "Lung cancer"]
    assert new_intervention_codes == ["Treatment: Drugs", "Treatment: Radiotherapy"]
    assert other_frame["VALUE"].tolist() == ["preserved"]


def test_append_trials_to_workbook_replaces_existing_actrn(tmp_path: Path):
    base_input = tmp_path / "base.xlsx"
    output = tmp_path / "anzctr_input.xlsx"
    write_base_workbook(base_input)
    parsed = ParsedAnzctrTrial(
        actrn="ACTRN12623000000000",
        trial_row={
            "ACTRN": "12623000000000",
            "STUDY TITLE": "Updated existing trial",
            "SCIENTIFIC TITLE": "Updated scientific trial",
        },
        health_conditions=["Updated cancer"],
        intervention_codes=["Treatment: Drugs"],
    )

    append_trials_to_workbook(
        base_input_xlsx=base_input,
        output_xlsx=output,
        parsed_trials=[parsed],
    )

    trial_frame = pd.read_excel(output, sheet_name="TRIAL", dtype=str).fillna("")
    health_frame = pd.read_excel(output, sheet_name="HEALTH CONDITION", dtype=str).fillna("")

    assert trial_frame["TRIAL ID"].tolist() == ["1"]
    assert trial_frame["STUDY TITLE"].tolist() == ["Updated existing trial"]
    assert health_frame["HEALTH CONDITION"].tolist() == ["Updated cancer"]


def test_download_trials_to_input_workbook_appends_from_export(tmp_path: Path):
    base_input = tmp_path / "base.xlsx"
    output = tmp_path / "input_trials" / "version_26062026" / "anzctr_input.xlsx"
    raw_dir = tmp_path / "raw_trials" / "version_26062026"
    write_base_workbook(base_input)

    output_path, manifest = download_trials_to_input_workbook(
        trial_ids=["ACTRN12600000000002", "ACTRN12699999999999"],
        base_input_xlsx=base_input,
        output_xlsx=output,
        raw_dir=raw_dir,
        all_trials_frames=build_all_trials_frames(),
    )

    trial_frame = pd.read_excel(output_path, sheet_name="TRIAL", dtype=str).fillna("")
    assert output_path == output
    assert trial_frame["ACTRN"].tolist() == ["12623000000000", "12600000000002"]
    assert manifest[["trial_id", "status"]].to_dict("records") == [
        {"trial_id": "ACTRN12600000000002", "status": "downloaded"},
        {"trial_id": "ACTRN12699999999999", "status": "missing_from_export"},
    ]


def test_main_accepts_explicit_output_dir_for_pottr_append(
    tmp_path: Path,
    monkeypatch,
):
    trial_ids = tmp_path / "missing.tsv"
    base_input = tmp_path / "base.xlsx"
    output_dir = tmp_path / "input_trials" / "version_25062026"
    raw_dir = tmp_path / "raw_trials" / "version_25062026"
    pd.DataFrame(
        {
            "trial_id": ["ACTRN12624000000000"],
            "registry": ["anzctr"],
        }
    ).to_csv(trial_ids, sep="\t", index=False)
    base_input.write_text("stub", encoding="utf-8")
    calls = {}

    def fake_download_trials_to_input_workbook(**kwargs):
        calls.update(kwargs)
        kwargs["output_xlsx"].parent.mkdir(parents=True, exist_ok=True)
        kwargs["output_xlsx"].write_text("workbook", encoding="utf-8")
        return kwargs["output_xlsx"], pd.DataFrame(
            [{"trial_id": "ACTRN12624000000000", "status": "downloaded"}]
        )

    monkeypatch.setattr(
        download_module,
        "download_trials_to_input_workbook",
        fake_download_trials_to_input_workbook,
    )

    assert download_module.main(
        [
            "--trial_ids",
            str(trial_ids),
            "--base_input_xlsx",
            str(base_input),
            "--output_dir",
            str(output_dir),
            "--raw_dir",
            str(raw_dir),
        ]
    ) == 0

    assert calls["output_xlsx"] == output_dir / POTTR_APPEND_INPUT_FILENAME
    assert calls["raw_dir"] == raw_dir
    assert (output_dir / "anzctr_download_manifest_25062026.tsv").exists()
