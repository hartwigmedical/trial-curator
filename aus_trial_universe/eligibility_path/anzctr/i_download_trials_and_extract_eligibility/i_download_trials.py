from __future__ import annotations

import argparse
import io
import logging
import re
import time
import zipfile
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Mapping, Sequence
from urllib.parse import urljoin

import pandas as pd
from aus_trial_universe.eligibility_path.shared.utils.pipeline_io import (
    existing_files,
    latest_version_dir,
)
from aus_trial_universe.eligibility_path.shared.cohorts import (
    normalize_anzctr_trial_id,
)

logger = logging.getLogger(__name__)

ANZCTR_BASE_URL = "https://www.anzctr.org.au/"
ANZCTR_SEARCH_URL = urljoin(ANZCTR_BASE_URL, "TrialSearch.aspx")
DEFAULT_BASE_INPUT_XLSX = Path(
    "data/trial_inputs/anzctr/input_trials/version_10062026/anzctr_input.xlsx"
)
DEFAULT_INPUT_ROOT = Path("data/trial_inputs/anzctr/input_trials")
DEFAULT_RAW_ROOT = Path("data/trial_inputs/anzctr/raw_trials")
DEFAULT_MANIFEST_STEM = "anzctr_download_manifest"
DEFAULT_EXPORT_SUFFIX_FORMAT = "%d%m%Y"
DEFAULT_TRIAL_ID_COLUMNS = ("trial_id", "trialId", "ACTRN", "actrn")
DEFAULT_TIMEOUT_MS = 120_000
DEFAULT_SEARCH_RETRIES = 3
# The whole-registry export is ~90 MB and is generated on demand server-side, so
# it needs a far more generous timeout than the quick search postbacks.
DEFAULT_BULK_DOWNLOAD_TIMEOUT_S = 600
INITIAL_SEARCH_INPUT_FILENAME = "01_initial_search_anzctr_input.xlsx"
POTTR_APPEND_INPUT_FILENAME = "02_pottr_append_anzctr_input.xlsx"
# Whole-registry export cached in the raw dir so the POTTR-append run can reuse
# the initial-search download instead of pulling ~90 MB again.
ALL_TRIALS_CACHE_FILENAME = "anzctr_all_trials.zip"

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

REQUIRED_SHEETS = (TRIAL_SHEET, HEALTH_CONDITION_SHEET, INTERVENTION_CODE_SHEET)


@dataclass(frozen=True)
class ParsedAnzctrTrial:
    actrn: str
    trial_row: dict[str, object]
    health_conditions: list[str]
    intervention_codes: list[str]


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


DEFAULT_INITIAL_SEARCH_PARAMETERS = AnzctrSearchParameters()


class AnzctrCloudflareChallengeError(RuntimeError):
    """Raised when ANZCTR serves an anti-bot challenge instead of the search form."""


def default_base_input_xlsx(input_root: Path = DEFAULT_INPUT_ROOT) -> Path:
    try:
        version_dir = latest_version_dir(input_root)
    except FileNotFoundError:
        return DEFAULT_BASE_INPUT_XLSX

    candidates = existing_files(
        [
            version_dir / "anzctr_input.xlsx",
            version_dir / INITIAL_SEARCH_INPUT_FILENAME,
            version_dir / POTTR_APPEND_INPUT_FILENAME,
        ]
    )
    return candidates[0] if candidates else DEFAULT_BASE_INPUT_XLSX


def clean_text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return re.sub(r"\s+", " ", str(value)).strip()


def bare_anzctr_trial_id(value: object) -> str:
    return normalize_anzctr_trial_id(value).removeprefix("ACTRN")


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


# ---------------------------------------------------------------------------
# curl_cffi HTTP layer (passes Cloudflare; replays the ASP.NET search/download).
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Whole-registry workbook -> filtered workbook.
# ANZCTR only offers "download ALL trials" or "download the <=20 ticked rows on
# this page"; there is no native "download my filtered results". So we pull the
# full export once and filter it locally to match the advanced search.
# ---------------------------------------------------------------------------
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


def normalise_workbook_trial_id(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if pd.isna(value):
            return ""
        if value.is_integer():
            return str(int(value))
    return clean_text(value)


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
    params: AnzctrSearchParameters = DEFAULT_INITIAL_SEARCH_PARAMETERS,
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


def write_workbook(frames: Mapping[str, pd.DataFrame], output_xlsx: Path) -> Path:
    output_xlsx.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output_xlsx, engine="openpyxl") as writer:
        for name, frame in frames.items():
            frame.to_excel(writer, sheet_name=name, index=False)
    return output_xlsx


def workbook_candidates_from_zip(zip_path: Path) -> list[zipfile.ZipInfo]:
    with zipfile.ZipFile(zip_path) as archive:
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


def read_all_trials_workbook(zip_path: Path) -> dict[str, pd.DataFrame]:
    candidates = workbook_candidates_from_zip(zip_path)
    if not candidates:
        raise ValueError(f"No .xlsx/.xls workbook found inside {zip_path}")
    with zipfile.ZipFile(zip_path) as archive:
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


def download_initial_search_workbook(
    search_parameters: AnzctrSearchParameters = DEFAULT_INITIAL_SEARCH_PARAMETERS,
    *,
    output_xlsx: Path,
    raw_dir: Path,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    retries: int = DEFAULT_SEARCH_RETRIES,
) -> tuple[Path, pd.DataFrame]:
    """Acquire the cancer/drug ANZCTR cohort and write the input workbook.

    Downloads the whole-registry Excel export (the only bulk export ANZCTR
    offers) once, caches it in ``raw_dir`` for the POTTR-append run, and writes a
    copy filtered to the advanced-search cohort.
    """
    _load_curl_cffi()  # Fail fast: a missing dependency must not be retried.
    timeout_s = max(1, timeout_ms // 1000)
    raw_dir.mkdir(parents=True, exist_ok=True)
    cache_path = raw_dir / ALL_TRIALS_CACHE_FILENAME

    attempts = max(1, retries)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            logger.info("ANZCTR advanced search + export attempt %d/%d", attempt, attempts)
            session = _new_anzctr_session()
            zip_bytes, reported_count, first_page_actrns = fetch_all_trials_zip(
                session, search_parameters, timeout_s=timeout_s
            )
            cache_path.write_bytes(zip_bytes)
            logger.info("Cached ANZCTR whole-registry export at %s", cache_path)

            frames = read_all_trials_workbook(cache_path)
            matched = matched_trial_ids(frames, search_parameters)
            _validate_filtered_count(frames, matched, reported_count, first_page_actrns)
            filtered = filter_workbook_to_trial_ids(frames, matched)
            write_workbook(filtered, output_xlsx)

            manifest = pd.DataFrame(
                [
                    {
                        "trial_id": "",
                        "status": "downloaded_initial_search_export",
                        "trial_review_url": "",
                        "raw_html_file": str(cache_path),
                        "error": "",
                        "source_file": str(cache_path),
                        "matched_trials": len(matched),
                        "reported_count": "" if reported_count is None else reported_count,
                    }
                ]
            )
            logger.info("Wrote filtered ANZCTR initial-search workbook: %s", output_xlsx)
            return output_xlsx, manifest
        except AnzctrCloudflareChallengeError:
            raise
        except Exception as exc:  # noqa: BLE001 - retried below, re-raised if final.
            last_error = exc
            logger.warning(
                "ANZCTR initial search attempt %d/%d failed: %s", attempt, attempts, exc
            )
            if attempt < attempts:
                time.sleep(min(2 ** (attempt - 1), 10))

    raise RuntimeError(
        f"ANZCTR initial search failed after {attempts} attempt(s)."
    ) from last_error


def read_trial_ids(input_file: Path, *, trial_id_column: str | None = None) -> list[str]:
    if input_file.suffix.lower() in {".tsv", ".csv"}:
        sep = "\t" if input_file.suffix.lower() == ".tsv" else ","
        frame = pd.read_csv(input_file, sep=sep, dtype=str, keep_default_na=False)
        if "registry" in frame.columns:
            frame = frame.loc[
                frame["registry"].map(clean_text).str.casefold() == "anzctr"
            ]
        candidate_columns = (
            [trial_id_column] if trial_id_column else list(DEFAULT_TRIAL_ID_COLUMNS)
        )
        selected_column = next(
            (column for column in candidate_columns if column in frame.columns),
            "",
        )
        if not selected_column:
            raise ValueError(
                f"{input_file} must contain one of {candidate_columns}. "
                f"Found columns: {list(frame.columns)}"
            )
        values = frame[selected_column]
    else:
        values = input_file.read_text(encoding="utf-8").splitlines()
    return ordered_unique(normalize_anzctr_trial_id(value) for value in values)


def ordered_unique(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = clean_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def load_workbook_frames(path: Path) -> dict[str, pd.DataFrame]:
    workbook = pd.ExcelFile(path)
    frames = {
        sheet: pd.read_excel(path, sheet_name=sheet, dtype=object)
        for sheet in workbook.sheet_names
    }
    missing = [sheet for sheet in REQUIRED_SHEETS if sheet not in frames]
    if missing:
        raise ValueError(f"{path} is missing required ANZCTR sheets: {missing}")
    return frames


def _next_workbook_trial_id(trial_frame: pd.DataFrame) -> int:
    existing = pd.to_numeric(trial_frame[TRIAL_ID_COLUMN], errors="coerce")
    max_existing = existing.max()
    if pd.isna(max_existing):
        return 1
    return int(max_existing) + 1


def _group_values_by_trial(
    frame: pd.DataFrame | None,
    value_column: str,
) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    if frame is None or value_column not in frame.columns:
        return grouped
    for trial_id, value in zip(frame[TRIAL_ID_COLUMN], frame[value_column]):
        workbook_id = normalise_workbook_trial_id(trial_id)
        text = clean_text(value)
        if workbook_id and text:
            bucket = grouped.setdefault(workbook_id, [])
            if text not in bucket:
                bucket.append(text)
    return grouped


def parsed_trials_from_workbook(
    frames: Mapping[str, pd.DataFrame],
    actrns: Iterable[str],
) -> list[ParsedAnzctrTrial]:
    """Build ParsedAnzctrTrial records from the whole-registry export."""
    wanted = {normalize_anzctr_trial_id(actrn) for actrn in actrns}
    wanted.discard("")
    if not wanted:
        return []

    trial = _require_sheet(frames, TRIAL_SHEET)
    health = get_sheet(frames, HEALTH_CONDITION_SHEET)
    intervention = get_sheet(frames, INTERVENTION_CODE_SHEET)

    id_to_actrn: dict[str, str] = {}
    for trial_id, actrn in zip(trial[TRIAL_ID_COLUMN], trial[ACTRN_COLUMN]):
        normalized = normalize_anzctr_trial_id(actrn)
        workbook_id = normalise_workbook_trial_id(trial_id)
        if normalized in wanted and workbook_id:
            id_to_actrn[workbook_id] = normalized

    health_by_id = _group_values_by_trial(health, HEALTH_CONDITION_COLUMN)
    codes_by_id = _group_values_by_trial(intervention, INTERVENTION_CODE_COLUMN)

    parsed: list[ParsedAnzctrTrial] = []
    for record in trial.to_dict("records"):
        workbook_id = normalise_workbook_trial_id(record.get(TRIAL_ID_COLUMN))
        actrn = id_to_actrn.get(workbook_id)
        if actrn is None:
            continue
        trial_row = {
            column: value
            for column, value in record.items()
            if column != TRIAL_ID_COLUMN
        }
        trial_row[ACTRN_COLUMN] = bare_anzctr_trial_id(actrn)
        parsed.append(
            ParsedAnzctrTrial(
                actrn=actrn,
                trial_row=trial_row,
                health_conditions=health_by_id.get(workbook_id, []),
                intervention_codes=codes_by_id.get(workbook_id, []),
            )
        )
    return parsed


def append_trials_to_workbook(
    *,
    base_input_xlsx: Path,
    output_xlsx: Path,
    parsed_trials: Sequence[ParsedAnzctrTrial],
    replace_existing: bool = True,
) -> Path:
    frames = load_workbook_frames(base_input_xlsx)
    trial_frame = frames[TRIAL_SHEET].copy()
    existing_by_actrn = {
        normalize_anzctr_trial_id(actrn): trial_id
        for actrn, trial_id in zip(
            trial_frame[ACTRN_COLUMN],
            trial_frame[TRIAL_ID_COLUMN],
        )
        if normalize_anzctr_trial_id(actrn)
    }

    next_trial_id = _next_workbook_trial_id(trial_frame)
    trial_ids_to_replace: set[object] = set()
    trial_rows: list[dict[str, object]] = []
    health_rows: list[dict[str, object]] = []
    intervention_code_rows: list[dict[str, object]] = []

    for parsed in parsed_trials:
        actrn = normalize_anzctr_trial_id(parsed.actrn)
        if actrn in existing_by_actrn:
            trial_id = existing_by_actrn[actrn]
            if replace_existing:
                trial_ids_to_replace.add(trial_id)
            else:
                continue
        else:
            trial_id = next_trial_id
            next_trial_id += 1

        row = dict(parsed.trial_row)
        row[TRIAL_ID_COLUMN] = trial_id
        row[ACTRN_COLUMN] = bare_anzctr_trial_id(actrn)
        trial_rows.append(row)
        health_rows.extend(
            {TRIAL_ID_COLUMN: trial_id, "HEALTH CONDITION": condition}
            for condition in parsed.health_conditions
        )
        intervention_code_rows.extend(
            {TRIAL_ID_COLUMN: trial_id, "INTERVENTION CODE": code}
            for code in parsed.intervention_codes
        )

    if trial_ids_to_replace:
        for sheet, frame in list(frames.items()):
            if TRIAL_ID_COLUMN in frame.columns:
                frames[sheet] = frame.loc[
                    ~frame[TRIAL_ID_COLUMN].isin(trial_ids_to_replace)
                ].reset_index(drop=True)

    frames[TRIAL_SHEET] = append_rows(frames[TRIAL_SHEET], trial_rows)
    frames[HEALTH_CONDITION_SHEET] = append_rows(
        frames[HEALTH_CONDITION_SHEET],
        health_rows,
    )
    frames[INTERVENTION_CODE_SHEET] = append_rows(
        frames[INTERVENTION_CODE_SHEET],
        intervention_code_rows,
    )

    output_xlsx.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output_xlsx, engine="openpyxl") as writer:
        for sheet, frame in frames.items():
            frame.to_excel(writer, sheet_name=sheet, index=False)
    return output_xlsx


def append_rows(frame: pd.DataFrame, rows: Sequence[dict[str, object]]) -> pd.DataFrame:
    if not rows:
        return frame
    incoming = pd.DataFrame(rows)
    for column in frame.columns:
        if column not in incoming.columns:
            incoming[column] = ""
    incoming = incoming.loc[:, frame.columns]
    return pd.concat([frame, incoming], ignore_index=True)


def default_version_dir(root: Path, *, export_date: str | None = None) -> Path:
    suffix = export_date or date.today().strftime(DEFAULT_EXPORT_SUFFIX_FORMAT)
    return root / f"version_{suffix}"


def _all_trials_frames_for_append(
    raw_dir: Path,
    *,
    timeout_s: int,
) -> dict[str, pd.DataFrame]:
    """Reuse the cached whole-registry export if present, else fetch it."""
    cache_path = raw_dir / ALL_TRIALS_CACHE_FILENAME
    if cache_path.exists() and cache_path.stat().st_size > 0:
        logger.info("Reusing cached ANZCTR whole-registry export at %s", cache_path)
        return read_all_trials_workbook(cache_path)

    logger.info("No cached ANZCTR export found; downloading the whole registry.")
    session = _new_anzctr_session()
    zip_bytes, _count, _actrns = fetch_all_trials_zip(
        session, DEFAULT_INITIAL_SEARCH_PARAMETERS, timeout_s=timeout_s
    )
    raw_dir.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(zip_bytes)
    return read_all_trials_workbook(cache_path)


def download_trials_to_input_workbook(
    *,
    trial_ids: Sequence[str],
    base_input_xlsx: Path,
    output_xlsx: Path,
    raw_dir: Path,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    replace_existing: bool = True,
    all_trials_frames: Mapping[str, pd.DataFrame] | None = None,
) -> tuple[Path, pd.DataFrame]:
    """Append specific ANZCTR trials (POTTR convergence) to the input workbook.

    Trials are taken from the cached whole-registry export rather than scraped
    one review page at a time.
    """
    timeout_s = max(1, timeout_ms // 1000)
    raw_dir.mkdir(parents=True, exist_ok=True)
    requested = ordered_unique(normalize_anzctr_trial_id(value) for value in trial_ids)

    if all_trials_frames is None:
        all_trials_frames = _all_trials_frames_for_append(raw_dir, timeout_s=timeout_s)

    parsed_trials = parsed_trials_from_workbook(all_trials_frames, requested)
    found = {parsed.actrn for parsed in parsed_trials}

    manifest_rows: list[dict[str, object]] = []
    for trial_id in requested:
        if trial_id in found:
            status, error = "downloaded", ""
        else:
            status, error = "missing_from_export", "ACTRN not present in ANZCTR export"
            logger.warning("ANZCTR trial %s not found in the whole-registry export", trial_id)
        manifest_rows.append(
            {
                "trial_id": trial_id,
                "status": status,
                "trial_review_url": "",
                "raw_html_file": "",
                "error": error,
            }
        )

    append_trials_to_workbook(
        base_input_xlsx=base_input_xlsx,
        output_xlsx=output_xlsx,
        parsed_trials=parsed_trials,
        replace_existing=replace_existing,
    )
    return output_xlsx, pd.DataFrame(manifest_rows)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Download ANZCTR cancer/drug trials by replaying the advanced search "
            "and the whole-registry Excel export, then filter to the cohort."
        )
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--trial_ids",
        type=Path,
        help="Text/CSV/TSV file containing ACTRN trial IDs (POTTR append).",
    )
    mode.add_argument(
        "--initial_search",
        action="store_true",
        help="Run the standard ANZCTR advanced search for initial acquisition.",
    )
    parser.add_argument(
        "--trial_id_column",
        default=None,
        help=(
            "Column to read from --trial_ids when it is CSV/TSV. Defaults to the "
            "first available of trial_id, trialId, ACTRN, actrn."
        ),
    )
    parser.add_argument(
        "--base_input_xlsx",
        type=Path,
        default=None,
        help=(
            "Existing ANZCTR workbook to append to. Defaults to the newest "
            "data/trial_inputs/anzctr/input_trials/version_<ddmmyyyy> workbook."
        ),
    )
    parser.add_argument(
        "--output_xlsx",
        type=Path,
        default=None,
        help=(
            "Output workbook. Defaults to "
            "data/trial_inputs/anzctr/input_trials/version_<ddmmyyyy>/anzctr_input.xlsx."
        ),
    )
    parser.add_argument(
        "--output_dir",
        type=Path,
        default=None,
        help=(
            "Version directory for generated ANZCTR input workbooks and manifest. "
            "Defaults to data/trial_inputs/anzctr/input_trials/version_<ddmmyyyy>."
        ),
    )
    parser.add_argument(
        "--raw_dir",
        type=Path,
        default=None,
        help=(
            "Directory for the cached whole-registry export. Defaults to "
            "data/trial_inputs/anzctr/raw_trials/version_<ddmmyyyy>."
        ),
    )
    parser.add_argument("--export_date", default=None, help="Date suffix in ddmmyyyy format.")
    parser.add_argument("--timeout_ms", type=int, default=DEFAULT_TIMEOUT_MS)
    parser.add_argument(
        "--search_retries",
        type=int,
        default=DEFAULT_SEARCH_RETRIES,
        help="Number of attempts for the ANZCTR advanced search + export.",
    )
    parser.add_argument(
        "--keep_existing",
        action="store_true",
        help="Do not replace existing ACTRN rows in the output workbook.",
    )
    parser.add_argument("--log_level", default="INFO")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    output_dir = args.output_dir or default_version_dir(
        DEFAULT_INPUT_ROOT,
        export_date=args.export_date,
    )
    raw_dir = args.raw_dir or default_version_dir(DEFAULT_RAW_ROOT, export_date=args.export_date)
    default_output_filename = (
        INITIAL_SEARCH_INPUT_FILENAME
        if args.initial_search
        else POTTR_APPEND_INPUT_FILENAME
    )
    output_xlsx = args.output_xlsx or output_dir / default_output_filename
    manifest_path = (
        output_dir
        / f"{DEFAULT_MANIFEST_STEM}_{output_dir.name.removeprefix('version_')}.tsv"
    )

    if args.initial_search:
        output_xlsx, manifest = download_initial_search_workbook(
            DEFAULT_INITIAL_SEARCH_PARAMETERS,
            output_xlsx=output_xlsx,
            raw_dir=raw_dir,
            timeout_ms=args.timeout_ms,
            retries=args.search_retries,
        )
    else:
        trial_ids = read_trial_ids(args.trial_ids, trial_id_column=args.trial_id_column)
        output_xlsx, manifest = download_trials_to_input_workbook(
            trial_ids=trial_ids,
            base_input_xlsx=args.base_input_xlsx or default_base_input_xlsx(),
            output_xlsx=output_xlsx,
            raw_dir=raw_dir,
            timeout_ms=args.timeout_ms,
            replace_existing=not args.keep_existing,
        )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest.to_csv(manifest_path, sep="\t", index=False)
    logger.info("Wrote ANZCTR appended input workbook: %s", output_xlsx)
    logger.info("Wrote ANZCTR download manifest: %s", manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
