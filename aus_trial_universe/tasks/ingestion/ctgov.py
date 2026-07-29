"""ClinicalTrials.gov API v2 full download (spec §6.1 — the CTGov half of the INPUT trial universe).

Self-contained port of the legacy eligibility-path downloader
(``eligibility_path/ctgov/i_download_trials_and_extract_eligibility/i_api_download.py``), FULL download only.
Preserves the exact Essie query semantics: recruiting-ish status ∩ Australia/New Zealand ∩ (drug ∪ POTTR-broad
intervention) ∩ (Neoplasms MeSH core ∪ the hardcoded POTTR cancer-condition net), plus a POTTR id-append for
any listed trials the query missed. Paged v2 download (pageSize 1000, nextPageToken), retry/backoff via urllib3
Retry, dedup by nctId. Output lands under ``CTGOV_ROOT/current_version/`` (superseded snapshots -> archive/).

Deliberately dropped from the legacy file: the ``--incremental`` dual-date delta mode, the persistent
download_state / meta-watermark, the CSV/field extraction, and all argparse/``main()`` (the CLI lives elsewhere).
"""
from __future__ import annotations

import csv
import json
import logging
import os
import time
from datetime import date
from pathlib import Path
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from aus_trial_universe.core.paths import (
    CTGOV_ROOT,
    CURRENT_VERSION,
    archive_current_version,
)

logger = logging.getLogger(__name__)


API_QUERY_BASE = "https://clinicaltrials.gov/api/v2/studies"

API_LOCATION_QUERY = """
(
    AREA[LocationCountry]Australia
    OR AREA[LocationCountry]"New Zealand"
)
"""

API_STATUS_QUERY = """
(
    AREA[OverallStatus]RECRUITING
    OR AREA[OverallStatus]NOT_YET_RECRUITING
    OR AREA[OverallStatus]ACTIVE_NOT_RECRUITING
    OR AREA[OverallStatus]ENROLLING_BY_INVITATION
)
"""

API_INTERVENTION_QUERY_CORE = """
(
    AREA[InterventionType]DRUG
)
"""

API_INTERVENTION_QUERY_POTTR = """
(
    AREA[InterventionType]BIOLOGICAL
    OR AREA[InterventionType]DIAGNOSTIC_TEST
    OR AREA[InterventionType]RADIATION
    OR AREA[InterventionType]DEVICE
    OR AREA[InterventionType]COMBINATION_PRODUCT
    OR AREA[InterventionType]OTHER
)
"""

API_CONDITION_QUERY_CORE = """
(
    AREA[ConditionMeshTerm]Neoplasms
)
"""

API_CONDITIONS_POTTR = [
    'acute myeloid leukemia',
    'advanced cancer',
    'advanced carcinoma',
    'advanced malignancies',
    'advanced malignant tumors',
    'advanced or metastatic nsclc',
    'advanced or metastatic solid tumors',
    'advanced solid tumor',
    'advanced solid tumors',
    'advanced tumors',
    'alk-positive non-small cell lung cancer',
    'aml',
    'anal cancer',
    'anal carcinoma',
    'astrocytoma',
    'b-cell malignancies',
    'b-cell malignancy',
    'biliary cancer',
    'biliary tract cancer',
    'bile duct cancer',
    'bilateral retinoblastoma',
    'bladder cancer',
    'breast cancer',
    'breast carcinoma',
    'carcinoid tumor',
    'carcinoma, non-small cell lung',
    'carcinoma, non-small-cell lung',
    'castration-resistant prostate cancer',
    'cervical cancer',
    'cervical carcinoma',
    'chondrosarcoma',
    'cholangiocarcinoma',
    'cutaneous squamous cell carcinoma',
    'endometrial adenocarcinoma',
    'endometrial cancer',
    'endometrial carcinoma',
    'endometrial clear cell adenocarcinoma',
    'endometrial endometrioid adenocarcinoma',
    'endometrial serous adenocarcinoma',
    'esophageal cancer',
    'esophageal carcinoma',
    'extrahepatic cholangiocarcinoma',
    'fallopian tube cancer',
    'gall bladder cancer',
    'gall bladder carcinoma',
    'gallbladder cancer',
    'gallbladder carcinoma',
    'gastric cancer',
    'gastroesophageal junction cancer',
    'gastrointestinal malignancy',
    'gene alteration',
    'glioblastoma',
    'glioblastoma multiforme',
    'glioma',
    'group d retinoblastoma',
    'head and neck cancer',
    'head and neck squamous cell carcinoma',
    'hematological malignancy',
    'hepatocellular carcinoma',
    'her2-positive breast cancer',
    'high-risk neuroblastoma',
    'intrahepatic cholangiocarcinoma',
    'kidney cancer',
    'kras p.g12c',
    'leukemia',
    'liver cancer',
    'locally advanced or metastatic her2-expressing cancers',
    'lung adenocarcinoma',
    'lung cancer',
    'lung cancers',
    'lung carcinoma',
    'lymphoma',
    'malignant pleural mesothelioma',
    'medullary thyroid cancer',
    'melanoma',
    'merkel cell carcinoma',
    'mesothelioma',
    'metastatic non small cell lung cancer',
    'metastatic non-small cell lung cancer',
    'metastatic; her2-positive breast cancer',
    'metastatic nasopharyngeal carcinoma',
    'metastatic castration-resistant prostate cancer',
    'mcrpc',
    'multiple myeloma',
    'myelodysplastic neoplasm',
    'myelodysplastic syndrome',
    'myelodysplastic syndromes',
    'myelofibrosis',
    'nasopharyngeal carcinoma',
    'neoplastic disease',
    'neoplasms',
    'neuroblastoma',
    'neuroendocrine tumor',
    'non small cell lung cancer',
    'non-small cell lung cancer',
    'nsclc',
    'other solid tumors',
    'ovarian cancer',
    'ovarian carcinoma',
    'ovarian clear cell adenocarcinoma',
    'ovarian clear cell carcinoma',
    'ovarian epithelial cancer',
    'ovarian endometrioid adenocarcinoma',
    'ovarian mucinous adenocarcinoma',
    'ovarian carcinosarcoma',
    'ovarian cancer',
    'ovarian epithelial cancer',
    'ovarian endometrioid adenocarcinoma',
    'ovarian mucinous adenocarcinoma',
    'pancreas cancer',
    'pancreatic cancer',
    'pancreatic ductal adenocarcinoma',
    'papillary thyroid cancer',
    'parotid gland cancer',
    'patient with insufficient response chemoimmunotherapy',
    'platinum resistant ovarian cancer',
    'platinum-resistant ovarian cancer',
    'primary peritoneal carcinoma',
    'prostate cancer',
    'prostate cancers',
    'prostate carcinoma',
    'ptcl',
    'rectal cancer',
    'relapsed or refractory multiple myeloma',
    'renal cell carcinoma',
    'retinoblastoma',
    'rhabdomyosarcoma',
    'ros1-positive non-small cell lung cancer',
    'salivary cancer',
    'salivary gland cancer',
    'salivary gland carcinoma',
    'sclc',
    'skin cancer',
    'small cell lung cancer',
    'small cell lung carcinoma',
    'solid cancer',
    'solid malignancies',
    'solid tumor',
    'solid tumors',
    'solid tumour',
    'solid tumours',
    'stage i retinoblastoma',
    'stage ii nasopharyngeal carcinoma',
    'stage iii nasopharyngeal carcinoma',
    'stage iv nasopharyngeal carcinoma',
    'stomach cancer',
    'thyroid cancer',
    'thyroid carcinoma',
    'triple negative breast cancer',
    'unilateral retinoblastoma',
    'unresectable solid tumors',
    'urothelial carcinoma',
    'uveal melanoma',
    'vulvar cancer',
    'vulvar carcinoma',
]

QUERY_CHUNK_SIZE = 20
PAGE_SIZE = 1000  # Max allowed on CT.gov
TIMEOUT = 30  # unit is seconds
PAUSE_BETWEEN_PAGES = 0.5  # unit is seconds

# Output filenames under CTGOV_ROOT/current_version/ (the merged filename keeps the legacy `merged`/`.json` token).
MERGED_CTGOV_FILENAME = "03_merged_ctgov_input.json"
POTTR_ID_ALIASES_FILENAME = "02b_pottr_id_aliases_ctgov.tsv"


def _build_essie_condition_query(query_list: list[str]) -> str:
    conditions: list[str] = []

    first = query_list[0]
    conditions.append(f'AREA[Condition]"{first}"')

    for term in query_list[1:]:
        conditions.append(f'OR AREA[Condition]"{term}"')

    conditions_block = "(\n    " + "\n    ".join(conditions) + "\n)\n"

    query = f"""
{conditions_block}
AND
{API_LOCATION_QUERY}
AND
{API_STATUS_QUERY}
AND
(
    {API_INTERVENTION_QUERY_CORE}
    OR
    {API_INTERVENTION_QUERY_POTTR}
)
""".strip()

    return query


def create_session() -> requests.Session:
    ses = requests.session()

    retries = Retry(
        total=8,  # max no. retries
        connect=5,
        read=5,
        redirect=2,
        status=5,  # retries for status code beneath
        status_forcelist=(429, 500, 502, 503, 504),
        other=1,  # retry once on rare cases
        allowed_methods=("GET",),
        backoff_factor=1.0,  # urllib3 will sleep for = backoff_factor * (2 ** no. previous retries)
        raise_on_status=False,
    )

    ses.mount("https://clinicaltrials.gov/", HTTPAdapter(max_retries=retries))

    ses.headers.update({"Accept": "application/json"})

    return ses


def download_one_page_from_ctgov(
    session: requests.Session, query_term: str, page_token: Optional[str] = None
) -> dict[str, Any]:
    """Download ONE PAGE of Australian/New Zealand cancer trials for a given Essie query via the v2 REST API."""
    request_params = {
        "countTotal": "true",
        "format": "json",
        "pageSize": PAGE_SIZE,
        "query.term": query_term,
    }

    if page_token:
        request_params["pageToken"] = page_token

    try:
        req = session.get(url=API_QUERY_BASE, params=request_params, timeout=TIMEOUT)
        req.raise_for_status()
        return req.json()

    except requests.exceptions.HTTPError as e:  # e.g. 404, 500, 503
        logger.error(
            "HTTP error: %s | %s",
            e.response.status_code if e.response else "[unknown]",
            (e.response.text if e.response and e.response.text else ""),
        )
        raise

    except requests.exceptions.RequestException as e:
        logger.error("Request error: %s", e)
        raise


def download_one_trial_from_ctgov(session: requests.Session, nct_id: str) -> dict[str, Any]:
    nct_id = nct_id.strip().upper()
    if not nct_id:
        raise ValueError("nct_id is required")

    try:
        req = session.get(
            url=f"{API_QUERY_BASE}/{nct_id}",
            params={"format": "json"},
            timeout=TIMEOUT,
        )
        req.raise_for_status()
        return req.json()

    except requests.exceptions.HTTPError as e:
        logger.error(
            "HTTP error downloading %s: %s | %s",
            nct_id,
            e.response.status_code if e.response else "[unknown]",
            (e.response.text if e.response and e.response.text else ""),
        )
        raise

    except requests.exceptions.RequestException as e:
        logger.error("Request error downloading %s: %s", nct_id, e)
        raise


def download_trials_by_nct_ids(
    session: requests.Session,
    nct_ids: list[str],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Download each requested trial, returning ``(trials, aliases)``.

    ClinicalTrials.gov resolves a requested NCT id to its canonical record, which for a merged/duplicate
    registration can carry a *different* nctId. ``aliases`` maps each requested id to the canonical id of the study
    it returned, but only where they differ — so callers can record that, e.g., a POTTR-listed ``NCT03816254`` is
    really served as ``NCT03783403``.
    """
    trials: list[dict[str, Any]] = []
    aliases: dict[str, str] = {}
    seen: set[str] = set()
    for nct_id in nct_ids:
        normalized_nct_id = nct_id.strip().upper()
        if not normalized_nct_id or normalized_nct_id in seen:
            continue
        seen.add(normalized_nct_id)
        study = download_one_trial_from_ctgov(session, normalized_nct_id)
        trials.append(study)
        canonical = (_extract_nct_id(study) or "").strip().upper()
        if canonical and canonical != normalized_nct_id:
            aliases[normalized_nct_id] = canonical
    return trials, aliases


def _extract_nct_id(study_obj: dict[str, Any]) -> Optional[str]:
    try:
        return study_obj["protocolSection"]["identificationModule"]["nctId"]
    except KeyError:
        return None


def _dedup_trials(trials: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # The POTTR APPEND / id-append queries may re-download a trial already covered by the MeSH-term search.
    unique_trials: dict[str, dict[str, Any]] = {}

    for study in trials:
        nct_id = _extract_nct_id(study)
        if not nct_id:
            logger.debug("Study without NCTID encountered and skipped.")
            continue

        unique_trials[nct_id] = study

    return list(unique_trials.values())


def generate_query_terms(chunk_size: int = QUERY_CHUNK_SIZE) -> list[tuple[str, str]]:
    query_terms: list[tuple[str, str]] = []

    CORE_QUERY = f"""
{API_CONDITION_QUERY_CORE}
AND
{API_LOCATION_QUERY}
AND
{API_STATUS_QUERY}
AND
{API_INTERVENTION_QUERY_CORE}
""".strip()

    query_terms.append(("CORE_QUERY", CORE_QUERY))

    for i in range(0, len(API_CONDITIONS_POTTR), chunk_size):
        chunk = API_CONDITIONS_POTTR[i : i + chunk_size]
        pottr_query = _build_essie_condition_query(chunk)

        label = f"POTTR_APPEND_{i // chunk_size + 1}"
        query_terms.append((label, pottr_query))

    return query_terms


def _download_all_matching_trials(
    session: requests.Session, query_terms: list[tuple[str, str]], *, log: logging.Logger
) -> list[dict[str, Any]]:
    all_trials_raw: list[dict[str, Any]] = []

    for stage_idx, (label, query_term) in enumerate(query_terms, start=1):
        log.info("Starting query stage %d / %d (%s)", stage_idx, len(query_terms), label)

        num_trials_for_stage: Optional[int] = None
        page_token: Optional[str] = None
        on_page = 1

        while True:
            data = download_one_page_from_ctgov(
                session=session,
                query_term=query_term,
                page_token=page_token,
            )

            if num_trials_for_stage is None:
                num_trials_for_stage = data.get("totalCount")

            page_trials = data.get("studies", [])
            all_trials_raw.extend(page_trials)

            log.info(
                "Stage %d (%s) | Page %d | Trials on page: %d | Raw accumulated (with duplicates): %d | "
                "Server reported total for stage: %s",
                stage_idx, label, on_page, len(page_trials), len(all_trials_raw), num_trials_for_stage,
            )

            page_token = data.get("nextPageToken")
            if not page_token:
                break

            on_page += 1
            time.sleep(PAUSE_BETWEEN_PAGES)

        log.info("Finished query stage %d / %d (%s)", stage_idx, len(query_terms), label)

    log.info("Finished all query stages. Raw trial count (with duplicates) = %d", len(all_trials_raw))
    all_trials_unique = _dedup_trials(all_trials_raw)

    log.info("Unique trials = %d", len(all_trials_unique))
    return all_trials_unique


def _write_json_atomic(path: Path, obj: Any) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")

    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(obj=obj, fp=f, indent=2)
        f.write("\n")

    os.replace(tmp_path, path)


def _write_pottr_alias_table(path: Path, aliases: dict[str, str]) -> None:
    """Write the requested->canonical POTTR id-alias table (overwriting).

    Always written (even when empty, header only) so the file reflects the current append's aliases rather than a
    stale earlier set.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter="\t", lineterminator="\n")
        writer.writerow(["requested_id", "canonical_id"])
        for requested, canonical in sorted(aliases.items()):
            writer.writerow([requested, canonical])


def download_ctgov(
    pottr_ids: set[str] | None = None, *, log: logging.Logger | None = None
) -> tuple[list[dict], dict[str, str]]:
    """Full CTGov download -> (merged deduped list of raw v2 study records, {requested_id: canonical_id} aliases).

    Runs the core status/country/drug/condition search plus the POTTR broad-net stages, then a POTTR id-append that
    fetches only the ``pottr_ids`` not already present in the search results; dedups the union by nctId. ``aliases``
    records POTTR ids that CT.gov served under a different canonical nctId (empty when no id-append ran).
    """
    log = log or logger
    session = create_session()

    records = _download_all_matching_trials(session, generate_query_terms(), log=log)

    aliases: dict[str, str] = {}
    if pottr_ids:
        present = {(_extract_nct_id(s) or "").strip().upper() for s in records}
        present.discard("")
        wanted = {tid.strip().upper() for tid in pottr_ids if tid and tid.strip().upper().startswith("NCT")}
        to_fetch = sorted(wanted - present)
        if to_fetch:
            log.info("POTTR id-append: fetching %d listed trial(s) not covered by the search.", len(to_fetch))
            appended, aliases = download_trials_by_nct_ids(session, to_fetch)
            records.extend(appended)
            if aliases:
                log.info(
                    "POTTR id aliases (requested -> canonical): %s",
                    ", ".join(f"{req}->{can}" for req, can in sorted(aliases.items())),
                )

    records = _dedup_trials(records)
    log.info("CTGov download complete | merged unique trials = %d | pottr aliases = %d", len(records), len(aliases))
    return records, aliases


def write_ctgov_current_version(
    records: list[dict], aliases: dict[str, str], *, on_date: date | None = None
) -> Path:
    """Archive the existing ``CTGOV_ROOT/current_version`` then write a fresh one; returns the current_version dir.

    Moves any existing ``current_version`` to ``archive/<ddmmyyyy>/`` (via ``archive_current_version``, labelled by
    ``on_date`` or today), then writes the merged records to ``03_merged_ctgov_input.json`` (a JSON list) and the
    POTTR aliases to ``02b_pottr_id_aliases_ctgov.tsv``.
    """
    label = (on_date or date.today()).strftime("%d%m%Y")
    archive_current_version(CTGOV_ROOT, label)

    current = CTGOV_ROOT / CURRENT_VERSION
    current.mkdir(parents=True, exist_ok=True)

    _write_json_atomic(current / MERGED_CTGOV_FILENAME, records)
    _write_pottr_alias_table(current / POTTR_ID_ALIASES_FILENAME, aliases)

    logger.info(
        "Wrote CTGov current_version: %s | merged=%s (%d trials) | aliases=%s (%d)",
        current, MERGED_CTGOV_FILENAME, len(records), POTTR_ID_ALIASES_FILENAME, len(aliases),
    )
    return current
