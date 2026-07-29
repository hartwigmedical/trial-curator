"""ANZCTR trial download + workbook->CSV extraction (the ANZCTR half of the INPUT trial universe).

Self-contained port of the legacy eligibility-path ANZCTR steps
(``eligibility_path/anzctr/i_download_trials_and_extract_eligibility/{i_download_trials,ii_select_trials_and_fields}.py``),
DOWNLOAD + field extraction only. ANZCTR offers no "download my filtered results", only "download ALL trials", so
the flow is: replay the ASP.NET/DevExpress advanced search on ``TrialSearch.aspx`` (behind a Cloudflare managed
challenge that only ``curl_cffi``'s real-browser TLS fingerprint passes) -> pull the whole-registry ``all.xls`` zip
-> re-filter it LOCALLY to the advanced-search cohort (study_type ∩ status ∩ intervention ∩ condition ∩ country) ->
flatten the filtered TRIAL/HEALTH CONDITION/INTERVENTION CODE sheets to the 24-column per-trial CSV (drug-intervention
filter + POTTR exemption + manual removal list).

Deliberately dropped from the legacy tree (the tree is being retired):
  * ``iii_extract_drugs.py`` in full — NO RxNorm, NO drug columns, NO LLM columns. The output CSV is EXACTLY the 24
    base columns (``OUTPUT_COLUMNS``) the agentic loaders + trial_info read.
  * The POTTR-append / staged-input-workbook machinery (``download_trials_to_input_workbook``,
    ``append_trials_to_workbook``, the 01/02/03 workbook staging) and all argparse/``main()`` — the CLI lives
    elsewhere and POTTR handling is now the extraction-time exemption below.

Data lands under ``ANZCTR_ROOT/current_version/`` (superseded snapshots -> archive/).
"""
from __future__ import annotations

import io
import logging
import re
import time
import zipfile
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Mapping
from urllib.parse import urljoin

import pandas as pd

from aus_trial_universe.core.paths import (
    ANZCTR_ROOT,
    CURRENT_VERSION,
    archive_current_version,
)
from aus_trial_universe.tasks.ingestion.pottr_ids import should_remove_trial

logger = logging.getLogger(__name__)

ANZCTR_BASE_URL = "https://www.anzctr.org.au/"
ANZCTR_SEARCH_URL = urljoin(ANZCTR_BASE_URL, "TrialSearch.aspx")

DEFAULT_TIMEOUT_MS = 120_000
DEFAULT_SEARCH_RETRIES = 3
# The whole-registry export is ~90 MB and is generated on demand server-side, so
# it needs a far more generous timeout than the quick search postbacks.
DEFAULT_BULK_DOWNLOAD_TIMEOUT_S = 600

# curl_cffi impersonates a real Chrome TLS/JA3 fingerprint, which is what passes
# the Cloudflare "managed challenge" in front of anzctr.org.au. A plain
# requests/urllib client (or a headless browser) is served a 403 challenge page.
ANZCTR_BROWSER_IMPERSONATION = "chrome"
ANZCTR_ASYNC_HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "X-MicrosoftAjax": "Delta=true",
    "Cache-Control": "no-cache",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
}

# ASP.NET WebForms / DevExpress control names on TrialSearch.aspx.
SCRIPT_MANAGER_FIELD = "ctl00$body$tsmAJAXScriptManager"
SEARCH_BUTTON_FIELD = "ctl00$body$btnSearch"
DOWNLOAD_BUTTON_FIELD = "ctl00$body$btnDownload"
DOWNLOAD_MODE_FIELD = "ctl00$body$drpDwnLstDownload"
DOWNLOAD_MODE_ALL_TRIALS = "all.xls"
REGISTRY_FIELD = "ctl00$body$registryRdBtnLst"
STUDY_TYPE_FIELD = "ctl00$body$studyTypeDrpDwnLst"
CONDITION_CATEGORY_FIELD = "ctl00$body$conditionCategoryDrpDwnLst"
CONDITION_CODE_FIELD = "ctl00$body$conditionCodeDrpDwnLst"
INTERVENTION_OPERATOR_FIELD = "ctl00$body$interventionCodeOperatorDrpDwnLst"
COUNTRY_OPERATOR_FIELD = "ctl00$body$recruitmentCountryOperatorDrpDwnLst"
INTERVENTION_LIST_FIELD = "ctl00$body$interventionCodeChckListBx"
STATUS_LIST_FIELD = "ctl00$body$recruitmentStatusChckListBx"
COUNTRY_LIST_FIELD = "ctl00$body$recruitmentCountriesChckListBx"

# Sheets / columns in the official ANZCTR Excel export used by the filter.
TRIAL_SHEET = "TRIAL"
HEALTH_CONDITION_SHEET = "HEALTH CONDITION"
INTERVENTION_CODE_SHEET = "INTERVENTION CODE"
CONDITION_CODE_SHEET = "CONDITION CODE"
COUNTRY_OUTSIDE_AUSTRALIA_SHEET = "COUNTRY OUTSIDE AUSTRALIA"
POSTCODE_SHEET = "POSTCODE"
TRIAL_ID_COLUMN = "TRIAL ID"
ACTRN_COLUMN = "ACTRN"
HEALTH_CONDITION_COLUMN = "HEALTH CONDITION"
INTERVENTION_CODE_COLUMN = "INTERVENTION CODE"
CONDITION_CATEGORY_COLUMN = "CONDITION CATEGORY"
STUDY_TYPE_COLUMN = "STUDY TYPE"
RECRUITMENT_STATUS_COLUMN = "RECRUITMENT STATUS"
RECRUITMENT_COUNTRY_COLUMN = "RECRUITMENT COUNTRY"
COUNTRY_OUTSIDE_AUSTRALIA_COLUMN = "COUNTRY"

AUSTRALIA = "Australia"
NEW_ZEALAND = "New Zealand"
RECRUITS_OUTSIDE_AUSTRALIA = "Outside"

# Workbook -> CSV extraction.
DRUG_INTERVENTION_CODE = "Treatment: Drugs"
INTERVENTION_CODES_OUTPUT_COLUMN = "anzctr_intervention_codes"
DISPLAY_DELIMITER = " | "

# The EXACT 24-column output contract the agentic loaders + trial_info read (verbatim from the legacy
# ii_select_trials_and_fields.OUTPUT_TRIAL_COLUMNS / OUTPUT_COLUMNS). NO drug/LLM columns.
OUTPUT_TRIAL_COLUMNS = [
    "ACTRN",
    "SUBMIT DATE",
    "APPROVAL DATE",
    "STUDY TITLE",
    "SCIENTIFIC TITLE",
    HEALTH_CONDITION_COLUMN,
    "INTERVENTIONS",
    "COMPARATOR",
    "CONTROL",
    "INCLUSIVE CRITERIA",
    "MIN AGE",
    "MIN AGE TYPE",
    "MAX AGE",
    "MAX AGE TYPE",
    "INCLUSIVE GENDER",
    "EXCLUSIVE CRITERIA",
    "PHASE",
    "RECRUITMENT STATUS",
    "RECRUITMENT COUNTRY",
    "RECRUITMENT STATE",
    "PRIMARY SPONSOR TYPE",
    "PRIMARY SPONSOR NAME",
    "PRIMARY SPONSOR COUNTRY",
]
OUTPUT_COLUMNS = OUTPUT_TRIAL_COLUMNS + [INTERVENTION_CODES_OUTPUT_COLUMN]
TRIAL_SOURCE_COLUMNS = [
    column
    for column in OUTPUT_TRIAL_COLUMNS
    if column != HEALTH_CONDITION_COLUMN
]

# Output layout under ANZCTR_ROOT/current_version/.
EXTRACTED_TRIALS_SUBDIR = "extracted_trials"
FIELD_EXTRACTIONS_FILENAME = "anzctr_field_extractions.csv"
ALL_TRIALS_ZIP_FILENAME = "anzctr_all_trials.zip"

_WHITESPACE_RE = re.compile(r"\s+")
_ANZCTR_TRIAL_ID_RE = re.compile(r"ACTRN\d+", flags=re.IGNORECASE)
_TRAILING_FLOAT_ZERO_RE = re.compile(r"^(\d+)\.0+$")


@dataclass(frozen=True)
class AnzctrSearchParameters:
    registry: str = "ANZCTR"
    intervention_code_operator: str = "OR"
    intervention_codes: tuple[str, ...] = ("Treatment: Drugs", "Treatment: Other")
    study_type: str = "Interventional"
    recruitment_status: tuple[str, ...] = (
        "Not yet recruiting",
        "Recruiting",
        "Active, not recruiting",
    )
    condition_category: str = "Cancer"
    countries_operator: str = "OR"
    countries_of_recruitment: tuple[str, ...] = ("Australia", "New Zealand")


DEFAULT_SEARCH_PARAMETERS = AnzctrSearchParameters()


class AnzctrCloudflareChallengeError(RuntimeError):
    """Raised when ANZCTR serves an anti-bot challenge instead of the search form."""


# --------------------------------------------------------------------------- #
# Text / id helpers
# --------------------------------------------------------------------------- #
def clean_text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return _WHITESPACE_RE.sub(" ", str(value)).strip()


def normalise_code(value: object) -> str:
    return clean_text(value).casefold()


def normalise_workbook_trial_id(value: object) -> str:
    """Canonical internal ``TRIAL ID`` key (bare numeric string) from a workbook cell."""
    if value is None:
        return ""
    if isinstance(value, float):
        if pd.isna(value):
            return ""
        if value.is_integer():
            return str(int(value))
    return clean_text(value)


def normalize_anzctr_trial_id(value: object) -> str:
    """Return a stable uppercase ``ACTRN``-prefixed identifier, or blank.

    Accepts already-prefixed ids (``ACTRN12605000123456``), bare numeric ids (``12605000123456``), pandas float
    coercions (``12605000123456.0``) and ids embedded in surrounding text. This is the canonical ANZCTR join key,
    used only to compare workbook ACTRNs (stored bare) against the POTTR / removal sets (stored prefixed); the CSV
    itself keeps the bare ACTRN the export provides.
    """
    if isinstance(value, float) and value.is_integer():
        text = str(int(value))
    else:
        text = clean_text(value)

    if not text or text.casefold() == "nan":
        return ""

    text = text.upper()
    trailing_zero = _TRAILING_FLOAT_ZERO_RE.match(text)
    if trailing_zero:
        text = trailing_zero.group(1)

    embedded = _ANZCTR_TRIAL_ID_RE.findall(text)
    if embedded:
        distinct = list(dict.fromkeys(embedded))
        if len(distinct) > 1:
            # The normaliser must return a single canonical id; surface the
            # ambiguity instead of silently dropping the rest.
            logger.warning(
                "normalize_anzctr_trial_id: %d distinct ANZCTR ids in %r; using the first (%s).",
                len(distinct),
                value,
                distinct[0],
            )
        return distinct[0]
    if text.startswith("ACTRN"):
        return text
    return f"ACTRN{text}"


def ordered_unique(values: Iterable[object]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        cleaned = clean_text(value)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
    return out


# --------------------------------------------------------------------------- #
# curl_cffi HTTP layer (passes Cloudflare; replays the ASP.NET search/download).
# --------------------------------------------------------------------------- #
def _load_curl_cffi():
    try:
        from curl_cffi import requests as curl_requests
    except ImportError as exc:
        raise RuntimeError(
            "ANZCTR downloads require the 'curl_cffi' package: it impersonates a "
            "real browser's TLS fingerprint to pass the Cloudflare challenge in "
            "front of anzctr.org.au. Install it with `pip install curl_cffi`."
        ) from exc
    return curl_requests


def _new_anzctr_session():
    curl_requests = _load_curl_cffi()
    return curl_requests.Session(impersonate=ANZCTR_BROWSER_IMPERSONATION)


def is_cloudflare_challenge_page(html: str) -> bool:
    challenge_markers = (
        "Managed Challenge",
        "I'm Under Attack Mode",
        "Enable JavaScript and cookies to continue",
        "__cf_chl_",
        "challenge-platform",
    )
    return any(marker in html for marker in challenge_markers)


def _is_anzctr_error_page(html: str) -> bool:
    return "ErrorPage.aspx" in html or "you seem to have experienced an error" in html


class _FormFieldParser(HTMLParser):
    """Collects the submit-time name/value of every input and <select> in the form."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.fields: dict[str, str] = {}
        self._select_name: str | None = None
        self._options: list[tuple[str, bool]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: (value or "") for key, value in attrs}
        present = {key for key, _ in attrs}
        if tag == "input":
            name = attr.get("name")
            if not name:
                return
            input_type = (attr.get("type") or "text").lower()
            if input_type in {"submit", "image", "button", "reset"}:
                return
            if input_type in {"checkbox", "radio"} and "checked" not in present:
                return
            self.fields[name] = attr.get("value", "")
        elif tag == "select":
            self._select_name = attr.get("name")
            self._options = []
        elif tag == "option" and self._select_name is not None:
            self._options.append((attr.get("value", ""), "selected" in present))

    def handle_endtag(self, tag: str) -> None:
        if tag == "select" and self._select_name is not None:
            selected = [value for value, is_selected in self._options if is_selected]
            if selected:
                self.fields[self._select_name] = selected[0]
            elif self._options:
                self.fields[self._select_name] = self._options[0][0]
            else:
                self.fields[self._select_name] = ""
            self._select_name = None
            self._options = []


def parse_form_fields(html: str) -> dict[str, str]:
    parser = _FormFieldParser()
    parser.feed(html)
    parser.close()
    return parser.fields


def _async_delta_value(delta_text: str, field_name: str) -> str | None:
    """Pull a hidden field value out of an ASP.NET AJAX partial-postback response."""
    match = re.search(
        r"\|hiddenField\|" + re.escape(field_name) + r"\|(.*?)\|",
        delta_text,
        re.S,
    )
    return match.group(1) if match else None


def devexpress_listbox_state(values: Iterable[str]) -> str:
    """Serialize selected ASPxListBox items the way the DevExpress client does.

    The checkbox-list posts each selected item as ``<len>|<value>`` with no
    separator, e.g. ``16|Treatment: Drugs16|Treatment: Other``.
    """
    return "".join(f"{len(value)}|{value}" for value in values)


def _apply_search_dropdowns(fields: dict[str, str], params: AnzctrSearchParameters) -> None:
    fields[REGISTRY_FIELD] = params.registry
    fields[INTERVENTION_OPERATOR_FIELD] = params.intervention_code_operator
    fields[STUDY_TYPE_FIELD] = params.study_type
    fields[CONDITION_CATEGORY_FIELD] = params.condition_category
    fields[COUNTRY_OPERATOR_FIELD] = params.countries_operator


def _raise_if_challenge(response) -> None:
    """Raise when a response is a Cloudflare interstitial rather than the form.

    Every ANZCTR page embeds a ``challenge-platform`` beacon, so the marker alone
    is not sufficient: the real form and results pages always carry
    ``__VIEWSTATE`` and return 200, whereas the managed-challenge page returns
    403 without it.
    """
    text = getattr(response, "text", "") or ""
    if "__VIEWSTATE" in text:
        return
    status_code = getattr(response, "status_code", 200)
    if status_code == 403 or is_cloudflare_challenge_page(text):
        raise AnzctrCloudflareChallengeError(
            "ANZCTR served a Cloudflare challenge instead of the TrialSearch form. "
            "Ensure curl_cffi is installed and able to impersonate a current "
            "browser; the challenge is keyed on the TLS fingerprint."
        )


def execute_advanced_search(
    session,
    params: AnzctrSearchParameters,
    *,
    timeout_s: int,
) -> str:
    """Run the ANZCTR advanced search and return the results-page HTML.

    The page is ASP.NET WebForms + DevExpress: choosing the condition category
    fires an AJAX (UpdatePanel) postback that refreshes the page tokens, after
    which the full ``btnSearch`` postback carries the listbox selections.
    """
    response = session.get(ANZCTR_SEARCH_URL, timeout=timeout_s)
    _raise_if_challenge(response)
    fields = parse_form_fields(response.text)
    _apply_search_dropdowns(fields, params)

    async_fields = dict(fields)
    async_fields.pop(CONDITION_CODE_FIELD, None)
    async_fields.update(
        {
            "DXScript": "",
            "DXCss": "",
            "__ASYNCPOST": "true",
            "__EVENTTARGET": CONDITION_CATEGORY_FIELD,
            "__EVENTARGUMENT": "",
            SCRIPT_MANAGER_FIELD: f"{SCRIPT_MANAGER_FIELD}|{CONDITION_CATEGORY_FIELD}",
        }
    )
    delta = session.post(
        ANZCTR_SEARCH_URL,
        data=async_fields,
        headers=ANZCTR_ASYNC_HEADERS,
        timeout=timeout_s,
    )
    for token in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"):
        refreshed = _async_delta_value(delta.text, token)
        if refreshed is not None:
            fields[token] = refreshed

    search_fields = dict(fields)
    search_fields.update(
        {
            "__EVENTTARGET": "",
            "__EVENTARGUMENT": "",
            "DXScript": "",
            "DXCss": "",
            INTERVENTION_LIST_FIELD: devexpress_listbox_state(params.intervention_codes),
            STATUS_LIST_FIELD: devexpress_listbox_state(params.recruitment_status),
            COUNTRY_LIST_FIELD: devexpress_listbox_state(params.countries_of_recruitment),
            SEARCH_BUTTON_FIELD: "SEARCH",
        }
    )
    results = session.post(ANZCTR_SEARCH_URL, data=search_fields, timeout=timeout_s)
    _raise_if_challenge(results)
    if _is_anzctr_error_page(results.text):
        raise RuntimeError(
            "ANZCTR returned an error page for the advanced search; the form "
            "field set may have changed."
        )
    return results.text


def parse_result_count(results_html: str) -> int | None:
    match = re.search(r"records found:\s*([\d,]+)", results_html, re.IGNORECASE)
    return int(match.group(1).replace(",", "")) if match else None


def parse_result_actrns(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"ACTRN\d{11,}", text)))


def download_all_trials_zip(session, results_html: str, *, timeout_s: int) -> bytes:
    """Trigger the results-page DOWNLOAD control for the whole-registry export."""
    fields = parse_form_fields(results_html)
    fields.update(
        {
            "__EVENTTARGET": "",
            "__EVENTARGUMENT": "",
            "DXScript": "",
            "DXCss": "",
            DOWNLOAD_MODE_FIELD: DOWNLOAD_MODE_ALL_TRIALS,
            DOWNLOAD_BUTTON_FIELD: "DOWNLOAD",
        }
    )
    logger.info(
        "Downloading ANZCTR whole-registry export (~90 MB, generated on demand; "
        "this can take a minute)..."
    )
    response = session.post(ANZCTR_SEARCH_URL, data=fields, timeout=timeout_s)
    logger.info("ANZCTR export download complete (%.1f MB).", len(response.content) / 1e6)
    content = response.content
    if content[:2] != b"PK":
        raise RuntimeError(
            "ANZCTR DOWNLOAD did not return a zip archive "
            f"(content-type={response.headers.get('content-type')!r})."
        )
    return content


def fetch_all_trials_zip(
    session,
    params: AnzctrSearchParameters,
    *,
    timeout_s: int,
) -> tuple[bytes, int | None, set[str]]:
    """Run the search and download the whole-registry export in one session.

    Returns the zip bytes, the server-reported result count and the ACTRNs shown
    on the first results page (both used to sanity-check the local filter).
    """
    results_html = execute_advanced_search(session, params, timeout_s=timeout_s)
    reported_count = parse_result_count(results_html)
    first_page_actrns = {
        normalize_anzctr_trial_id(actrn) for actrn in parse_result_actrns(results_html)
    }
    zip_bytes = download_all_trials_zip(
        session,
        results_html,
        timeout_s=max(timeout_s, DEFAULT_BULK_DOWNLOAD_TIMEOUT_S),
    )
    return zip_bytes, reported_count, first_page_actrns


# --------------------------------------------------------------------------- #
# Whole-registry workbook -> local re-filter to the advanced-search cohort.
# ANZCTR only offers "download ALL trials" or "download the <=20 ticked rows on
# this page"; there is no native "download my filtered results". So we pull the
# full export once and filter it locally to match the advanced search.
# --------------------------------------------------------------------------- #
def _normalise_sheet_key(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().casefold()


def get_sheet(frames: Mapping[str, pd.DataFrame], target: str) -> pd.DataFrame | None:
    lookup = {_normalise_sheet_key(name): name for name in frames}
    name = lookup.get(_normalise_sheet_key(target))
    return frames[name] if name is not None else None


def _require_sheet(frames: Mapping[str, pd.DataFrame], target: str) -> pd.DataFrame:
    sheet = get_sheet(frames, target)
    if sheet is None:
        raise ValueError(f"ANZCTR export is missing the required {target!r} sheet.")
    return sheet


def _ids_with_value(
    frame: pd.DataFrame,
    value_column: str,
    accept: set[str],
) -> set[str]:
    accepted = {item.casefold() for item in accept}
    return {
        normalise_workbook_trial_id(trial_id)
        for trial_id, value in zip(frame[TRIAL_ID_COLUMN], frame[value_column])
        if clean_text(value).casefold() in accepted
        and normalise_workbook_trial_id(trial_id)
    }


def _split_recruitment_countries(value: object) -> set[str]:
    return {part.strip() for part in clean_text(value).split(",") if part.strip()}


def _country_matched_trial_ids(
    frames: Mapping[str, pd.DataFrame],
    params: AnzctrSearchParameters,
) -> set[str]:
    """Trial IDs recruiting in any requested country.

    The export encodes recruitment country as a ``RECRUITMENT COUNTRY`` flag set
    ({Australia, Outside}) on the TRIAL sheet, with the actual non-AU countries
    listed on COUNTRY OUTSIDE AUSTRALIA. This mirrors ANZCTR's own search:
      * Australia: an "Australia" flag, or a blank flag backed by an AU postcode.
      * New Zealand: an "Outside" flag plus a New Zealand row on COUNTRY OUTSIDE.
    """
    trial = _require_sheet(frames, TRIAL_SHEET)
    requested = {clean_text(country) for country in params.countries_of_recruitment}
    matched: set[str] = set()

    country_by_id = [
        (normalise_workbook_trial_id(trial_id), _split_recruitment_countries(country))
        for trial_id, country in zip(
            trial[TRIAL_ID_COLUMN], trial[RECRUITMENT_COUNTRY_COLUMN]
        )
    ]

    if AUSTRALIA in requested:
        matched |= {tid for tid, flags in country_by_id if tid and AUSTRALIA in flags}
        blank_country_ids = {tid for tid, flags in country_by_id if tid and not flags}
        postcode_sheet = get_sheet(frames, POSTCODE_SHEET)
        if postcode_sheet is not None and blank_country_ids:
            au_postcode_ids = {
                normalise_workbook_trial_id(trial_id)
                for trial_id in postcode_sheet[TRIAL_ID_COLUMN]
            }
            matched |= blank_country_ids & au_postcode_ids

    if NEW_ZEALAND in requested:
        outside_ids = {
            tid
            for tid, flags in country_by_id
            if tid and RECRUITS_OUTSIDE_AUSTRALIA in flags
        }
        outside_sheet = get_sheet(frames, COUNTRY_OUTSIDE_AUSTRALIA_SHEET)
        if outside_sheet is not None:
            nz_ids = _ids_with_value(
                outside_sheet, COUNTRY_OUTSIDE_AUSTRALIA_COLUMN, {NEW_ZEALAND}
            )
            matched |= outside_ids & nz_ids

    return matched


def matched_trial_ids(
    frames: Mapping[str, pd.DataFrame],
    params: AnzctrSearchParameters = DEFAULT_SEARCH_PARAMETERS,
) -> set[str]:
    """Trial IDs from the whole-registry export that match the advanced search."""
    trial = _require_sheet(frames, TRIAL_SHEET)
    condition = _require_sheet(frames, CONDITION_CODE_SHEET)
    intervention = _require_sheet(frames, INTERVENTION_CODE_SHEET)

    study_type_ids = {
        normalise_workbook_trial_id(trial_id)
        for trial_id, study_type in zip(trial[TRIAL_ID_COLUMN], trial[STUDY_TYPE_COLUMN])
        if clean_text(study_type) == params.study_type
        and normalise_workbook_trial_id(trial_id)
    }
    status_ids = {
        normalise_workbook_trial_id(trial_id)
        for trial_id, status in zip(
            trial[TRIAL_ID_COLUMN], trial[RECRUITMENT_STATUS_COLUMN]
        )
        if clean_text(status) in set(params.recruitment_status)
        and normalise_workbook_trial_id(trial_id)
    }
    intervention_ids = _ids_with_value(
        intervention, INTERVENTION_CODE_COLUMN, set(params.intervention_codes)
    )
    condition_ids = _ids_with_value(
        condition, CONDITION_CATEGORY_COLUMN, {params.condition_category}
    )
    country_ids = _country_matched_trial_ids(frames, params)

    return study_type_ids & status_ids & intervention_ids & condition_ids & country_ids


def pottr_present_trial_ids(frames: Mapping[str, pd.DataFrame], pottr_ids: set[str] | None) -> set[str]:
    """The workbook ``TRIAL ID``s (bare) of POTTR-listed trials present in the whole-registry export — regardless of
    the cohort filter. Unioned into the matched set so a POTTR-listed ANZCTR trial always reaches the output (the
    locked POTTR-exemption decision; symmetric with the CTGov id-append). ``pottr_ids`` is the ACTRN-prefixed set."""
    if not pottr_ids:
        return set()
    trial = _require_sheet(frames, TRIAL_SHEET)
    return {
        normalise_workbook_trial_id(trial_id)
        for trial_id, actrn in zip(trial[TRIAL_ID_COLUMN], trial[ACTRN_COLUMN])
        if normalize_anzctr_trial_id(actrn) in pottr_ids and normalise_workbook_trial_id(trial_id)
    }


def filter_workbook_to_trial_ids(
    frames: Mapping[str, pd.DataFrame],
    trial_ids: Iterable[str],
) -> dict[str, pd.DataFrame]:
    keep = {str(trial_id) for trial_id in trial_ids}
    filtered: dict[str, pd.DataFrame] = {}
    for name, frame in frames.items():
        if TRIAL_ID_COLUMN in frame.columns:
            mask = frame[TRIAL_ID_COLUMN].map(normalise_workbook_trial_id).isin(keep)
            filtered[name] = frame.loc[mask].reset_index(drop=True)
        else:
            filtered[name] = frame.copy()
    return filtered


def workbook_candidates_from_zip(zip_bytes: bytes) -> list[zipfile.ZipInfo]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        candidates = [
            member
            for member in archive.infolist()
            if not member.is_dir()
            and not member.filename.startswith("__MACOSX/")
            and Path(member.filename).suffix.casefold() in {".xlsx", ".xls"}
        ]
    return sorted(
        candidates,
        key=lambda member: (
            "trial" not in Path(member.filename).stem.casefold(),
            Path(member.filename).name.casefold(),
        ),
    )


def read_all_trials_workbook(zip_bytes: bytes) -> dict[str, pd.DataFrame]:
    """Read every sheet of the whole-registry workbook out of the ``all.xls`` zip bytes."""
    candidates = workbook_candidates_from_zip(zip_bytes)
    if not candidates:
        raise ValueError("No .xlsx/.xls workbook found inside the ANZCTR export zip.")
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        workbook_bytes = archive.read(candidates[0])
    excel = pd.ExcelFile(io.BytesIO(workbook_bytes))
    return {
        sheet: pd.read_excel(excel, sheet_name=sheet, dtype=object)
        for sheet in excel.sheet_names
    }


def _validate_filtered_count(
    frames: Mapping[str, pd.DataFrame],
    matched: set[str],
    reported_count: int | None,
    first_page_actrns: set[str],
) -> None:
    if reported_count is not None and len(matched) != reported_count:
        logger.warning(
            "ANZCTR local filter kept %d trials but the live search reported %d. "
            "ANZCTR may have changed its export; review the filter in %s.",
            len(matched),
            reported_count,
            __name__,
        )
    else:
        logger.info(
            "ANZCTR local filter kept %d trials (matches the live search count).",
            len(matched),
        )

    if not first_page_actrns:
        return
    trial = _require_sheet(frames, TRIAL_SHEET)
    matched_actrns = {
        normalize_anzctr_trial_id(actrn)
        for trial_id, actrn in zip(trial[TRIAL_ID_COLUMN], trial[ACTRN_COLUMN])
        if normalise_workbook_trial_id(trial_id) in matched
    }
    missing = first_page_actrns - matched_actrns
    if missing:
        logger.warning(
            "%d trials from the first ANZCTR results page are missing from the "
            "filtered set (e.g. %s).",
            len(missing),
            sorted(missing)[:5],
        )


# --------------------------------------------------------------------------- #
# Filtered workbook -> the 24-column per-trial CSV rows.
# (Drug-intervention filter + POTTR exemption + manual removal list.)
# --------------------------------------------------------------------------- #
def require_columns(
    frame: pd.DataFrame, columns: Iterable[str], *, sheet_name: str
) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(
            f"{sheet_name!r} sheet is missing required columns: {', '.join(missing)}"
        )


def build_health_condition_lookup(
    health_conditions: pd.DataFrame,
) -> dict[str, list[str]]:
    conditions_by_trial: dict[str, list[str]] = {}

    for trial_id_value, condition_value in health_conditions[
        [TRIAL_ID_COLUMN, HEALTH_CONDITION_COLUMN]
    ].itertuples(index=False, name=None):
        trial_id = normalise_workbook_trial_id(trial_id_value)
        if not trial_id:
            continue

        condition = clean_text(condition_value)
        if condition:
            conditions_by_trial.setdefault(trial_id, []).append(condition)

    return {
        trial_id: ordered_unique(conditions)
        for trial_id, conditions in conditions_by_trial.items()
    }


def build_intervention_code_lookup(
    intervention_codes: pd.DataFrame,
) -> tuple[dict[str, list[str]], set[str]]:
    codes_by_trial: dict[str, list[str]] = {}
    drug_trial_ids: set[str] = set()
    drug_code_key = normalise_code(DRUG_INTERVENTION_CODE)

    for trial_id_value, code_value in intervention_codes[
        [TRIAL_ID_COLUMN, INTERVENTION_CODE_COLUMN]
    ].itertuples(index=False, name=None):
        trial_id = normalise_workbook_trial_id(trial_id_value)
        if not trial_id:
            continue

        code = clean_text(code_value)
        if code:
            codes_by_trial.setdefault(trial_id, []).append(code)

        if normalise_code(code) == drug_code_key:
            drug_trial_ids.add(trial_id)

    return (
        {trial_id: ordered_unique(codes) for trial_id, codes in codes_by_trial.items()},
        drug_trial_ids,
    )


def extract_drug_intervention_trials(
    trials: pd.DataFrame,
    health_conditions: pd.DataFrame,
    intervention_codes: pd.DataFrame,
    pottr_trial_ids: set[str] | None = None,
) -> pd.DataFrame:
    codes_by_trial, drug_trial_ids = build_intervention_code_lookup(intervention_codes)
    conditions_by_trial = build_health_condition_lookup(health_conditions)
    pottr_ids = pottr_trial_ids or set()

    output_rows: list[dict[str, object]] = []
    pottr_exempt = 0
    trial_output_columns = [TRIAL_ID_COLUMN, *TRIAL_SOURCE_COLUMNS]
    for row_values in trials[trial_output_columns].itertuples(index=False, name=None):
        trial_id = normalise_workbook_trial_id(row_values[0])
        output_row = dict(zip(TRIAL_SOURCE_COLUMNS, row_values[1:]))

        # POTTR-listed trials must always reach the final output, so they are
        # exempt from the drug-intervention cohort filter. POTTR keys by ACTRN,
        # not the internal "TRIAL ID" used for drug-code matching. The workbook
        # ACTRN column is bare (e.g. "12624000110583") while POTTR (and the rest
        # of the pipeline) key on the ACTRN-prefixed id, so the comparison must
        # use the canonical prefixed key -- otherwise the exemption never matches
        # and non-drug ANZCTR POTTR trials are silently dropped here.
        is_pottr = normalize_anzctr_trial_id(output_row.get("ACTRN")) in pottr_ids
        if trial_id not in drug_trial_ids:
            if is_pottr:
                pottr_exempt += 1
            else:
                continue

        # POTTR-listed trials override the manual-removal list too, so they
        # always reach the final output (mirrors the drug-filter exemption).
        if (
            should_remove_trial(output_row.get("ACTRN"), registry="anzctr")
            and not is_pottr
        ):
            logger.info(
                "Skipping manually removed ANZCTR trial: %s",
                output_row.get("ACTRN"),
            )
            continue

        output_row[HEALTH_CONDITION_COLUMN] = DISPLAY_DELIMITER.join(
            conditions_by_trial.get(trial_id, [])
        )
        output_row[INTERVENTION_CODES_OUTPUT_COLUMN] = DISPLAY_DELIMITER.join(
            codes_by_trial.get(trial_id, [])
        )
        output_rows.append(output_row)

    if pottr_exempt:
        logger.info(
            "Retained %d non-drug trial(s) exempted as POTTR-listed.", pottr_exempt
        )
    return pd.DataFrame(output_rows, columns=OUTPUT_COLUMNS)


def _rows_from_frames(
    frames: Mapping[str, pd.DataFrame],
    *,
    pottr_trial_ids: set[str] | None,
) -> pd.DataFrame:
    """Flatten the filtered TRIAL/HEALTH CONDITION/INTERVENTION CODE sheets to the 24-column CSV rows."""
    trials = _require_sheet(frames, TRIAL_SHEET)
    health_conditions = _require_sheet(frames, HEALTH_CONDITION_SHEET)
    intervention_codes = _require_sheet(frames, INTERVENTION_CODE_SHEET)

    require_columns(trials, [TRIAL_ID_COLUMN, *TRIAL_SOURCE_COLUMNS], sheet_name=TRIAL_SHEET)
    require_columns(
        health_conditions,
        [TRIAL_ID_COLUMN, HEALTH_CONDITION_COLUMN],
        sheet_name=HEALTH_CONDITION_SHEET,
    )
    require_columns(
        intervention_codes,
        [TRIAL_ID_COLUMN, INTERVENTION_CODE_COLUMN],
        sheet_name=INTERVENTION_CODE_SHEET,
    )
    return extract_drug_intervention_trials(
        trials,
        health_conditions,
        intervention_codes,
        pottr_trial_ids=pottr_trial_ids,
    )


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def rows_from_zip(
    zip_bytes: bytes,
    *,
    pottr_ids: set[str] | None = None,
    reported_count: int | None = None,
    first_page_actrns: set[str] | None = None,
    log: logging.Logger | None = None,
) -> pd.DataFrame:
    """Whole-registry ``all.xls`` zip bytes -> the 24-column per-trial DataFrame (NO network).

    Reads the workbook, re-filters it to the advanced-search cohort (``matched_trial_ids``), then flattens the
    filtered sheets to rows (drug-intervention filter + POTTR exemption + removal list). Exposed so a re-parse of an
    already-downloaded / cached zip does not force a fresh download. ``reported_count`` / ``first_page_actrns`` (from
    :func:`fetch_all_trials_zip`) are used only to sanity-check the local filter and may be omitted.
    """
    log = log or logger
    log = log or logger
    frames = read_all_trials_workbook(zip_bytes)
    matched = matched_trial_ids(frames, DEFAULT_SEARCH_PARAMETERS)
    # Validate the COHORT match against the live search count BEFORE adding the POTTR force-includes (those
    # deliberately exceed the cohort, so they must not trip the "export changed?" sanity check).
    _validate_filtered_count(frames, matched, reported_count, first_page_actrns or set())
    bypass = pottr_present_trial_ids(frames, pottr_ids)   # POTTR-listed trials bypass the cohort filter
    if bypass - matched:
        log.info("ANZCTR: %d POTTR-listed trial(s) force-included past the cohort filter.", len(bypass - matched))
    matched |= bypass
    filtered = filter_workbook_to_trial_ids(frames, matched)
    rows = _rows_from_frames(filtered, pottr_trial_ids=pottr_ids)
    log.info(
        "ANZCTR workbook flattened to %d drug-intervention/POTTR-exempt trial row(s).",
        len(rows),
    )
    return rows


def download_anzctr(
    pottr_ids: set[str] | None = None, *, log: logging.Logger | None = None
) -> tuple[bytes, pd.DataFrame]:
    """Full ANZCTR ingest -> (raw ``all.xls`` zip bytes for audit, the 24-column per-trial DataFrame).

    Replays the advanced search + whole-registry export in one Cloudflare-passing ``curl_cffi`` session, then
    re-filters the export locally to the cohort and flattens it to rows (drug-intervention filter + POTTR exemption
    + manual removal list). ``pottr_ids`` should be the ANZCTR-registry POTTR set (``ACTRN``-prefixed, from
    ``pottr_ids.load_pottr_trial_ids_best_effort(registry="anzctr")``); those trials bypass the drug filter and the
    removal list so they always survive. The returned zip bytes are the audit copy (persist via
    :func:`write_anzctr_current_version`); re-feed them to :func:`rows_from_zip` to re-parse without re-downloading.

    Retries transient failures with backoff. Raises :class:`AnzctrCloudflareChallengeError` immediately if ANZCTR
    serves a Cloudflare challenge instead of the form, and ``RuntimeError`` if the search/export keeps failing.
    """
    log = log or logger
    _load_curl_cffi()  # Fail fast: a missing dependency must not be retried.
    timeout_s = max(1, DEFAULT_TIMEOUT_MS // 1000)

    attempts = max(1, DEFAULT_SEARCH_RETRIES)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            log.info(
                "ANZCTR advanced search + whole-registry export attempt %d/%d",
                attempt,
                attempts,
            )
            session = _new_anzctr_session()
            zip_bytes, reported_count, first_page_actrns = fetch_all_trials_zip(
                session, DEFAULT_SEARCH_PARAMETERS, timeout_s=timeout_s
            )
            rows = rows_from_zip(
                zip_bytes,
                pottr_ids=pottr_ids,
                reported_count=reported_count,
                first_page_actrns=first_page_actrns,
                log=log,
            )
            log.info("ANZCTR download complete | %d trial row(s).", len(rows))
            return zip_bytes, rows
        except AnzctrCloudflareChallengeError:
            raise
        except Exception as exc:  # noqa: BLE001 - retried below, re-raised if final.
            last_error = exc
            log.warning(
                "ANZCTR download attempt %d/%d failed: %s", attempt, attempts, exc
            )
            if attempt < attempts:
                time.sleep(min(2 ** (attempt - 1), 10))

    raise RuntimeError(
        f"ANZCTR download failed after {attempts} attempt(s)."
    ) from last_error


def write_anzctr_current_version(
    zip_bytes: bytes, rows: pd.DataFrame, *, on_date: date | None = None
) -> Path:
    """Archive the existing ``ANZCTR_ROOT/current_version`` then write a fresh one; returns the current_version dir.

    Moves any existing ``current_version`` to ``archive/<ddmmyyyy>/`` (via ``archive_current_version``, labelled by
    ``on_date`` or today), then writes the 24-column CSV to ``extracted_trials/anzctr_field_extractions.csv`` and the
    raw whole-registry export to ``anzctr_all_trials.zip`` (audit copy).
    """
    label = (on_date or date.today()).strftime("%d%m%Y")
    archive_current_version(ANZCTR_ROOT, label)

    current = ANZCTR_ROOT / CURRENT_VERSION
    extracted_dir = current / EXTRACTED_TRIALS_SUBDIR
    extracted_dir.mkdir(parents=True, exist_ok=True)

    csv_path = extracted_dir / FIELD_EXTRACTIONS_FILENAME
    rows.to_csv(csv_path, index=False)

    zip_path = current / ALL_TRIALS_ZIP_FILENAME
    zip_path.write_bytes(zip_bytes)

    logger.info(
        "Wrote ANZCTR current_version: %s | csv=%s (%d trials) | zip=%s (%.1f MB)",
        current,
        csv_path.relative_to(current),
        len(rows),
        ALL_TRIALS_ZIP_FILENAME,
        len(zip_bytes) / 1e6,
    )
    return current
