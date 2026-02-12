from __future__ import annotations

import argparse
import json
import logging
import os
import time
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

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
        raise_on_status=False
    )

    ses.mount("https://clinicaltrials.gov/", HTTPAdapter(max_retries=retries))

    ses.headers.update({
        "Accept": "application/json"
    })

    return ses


def download_one_page_from_ctgov(session: requests.Session, query_term: str, page_token: Optional[str] = None) -> dict[str, Any]:
    """
    This function downloads ONE PAGE of the Australian cancer trials from ClinicalTrials.gov
    according to a given Essie search query using ClinicalTrials' REST API

    More documentation on https://hartwigmedical.atlassian.net/wiki/spaces/AUS/pages/823263285/Australian+clinical+trials+universe
    """

    request_params = {
        "countTotal": "true",
        "format": "json",
        "pageSize": PAGE_SIZE,
        "query.term": query_term
    }

    if page_token:
        request_params["pageToken"] = page_token

    try:
        req = session.get(url=API_QUERY_BASE,
                          params=request_params,
                          timeout=TIMEOUT)
        req.raise_for_status()
        return req.json()

    except requests.exceptions.HTTPError as e:  # e.g. 404, 500, 503
        logger.error("HTTP error: %s | %s",
                     e.response.status_code if e.response else "[unknown]",
                     (e.response.text if e.response and e.response.text else "")
                     )
        raise

    except requests.exceptions.RequestException as e:
        logger.error("Request error: %s", e)
        raise


def _extract_nct_id(study_obj: dict[str, Any]) -> Optional[str]:
    try:
        return study_obj["protocolSection"]["identificationModule"]["nctId"]
    except KeyError:
        return None


def _dedup_trials(trials: list[dict[str, Any]]) -> list[dict[str, Any]]:  # Because the POTTR APPEND query may download the same trial as that covered by the MeSH term search
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

    query_terms.append(
        ("CORE_QUERY", CORE_QUERY)
    )

    for i in range(0, len(API_CONDITIONS_POTTR), chunk_size):
        chunk = API_CONDITIONS_POTTR[i : i + chunk_size]
        pottr_query = _build_essie_condition_query(chunk)

        label = f"POTTR_APPEND_{i // chunk_size + 1}"
        query_terms.append((label, pottr_query))

    return query_terms


def main():
    parser = argparse.ArgumentParser(description="Download ALL Australian cancer trials from ClinicalTrials.gov OR Download the UPDATED trials only")

    download_type = parser.add_mutually_exclusive_group(required=True)
    download_type.add_argument("--all", help="Download all trials", action="store_true")
    download_type.add_argument("--since", metavar="DD-MM-YYYY", help="Download only trials updated since DD-MM-YYYY")
    # Placeholder: Downloading only the updated trials has not been implemented

    parser.add_argument("--output_dir", help="Directory to store the trial JSON files", required=True)
    parser.add_argument("--log_level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], help="Logging level")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    os.makedirs(args.output_dir, exist_ok=True)
    session = create_session()

    if args.all:
        query_terms = generate_query_terms()
        all_trials_raw: list[dict[str, Any]] = []

        for stage_idx, (label, query_term) in enumerate(query_terms, start=1):
            logger.info("Starting query stage %d / %d (%s)", stage_idx, len(query_terms), label)

            num_trials_for_stage: Optional[int] = None
            page_token: Optional[str] = None
            on_page = 1

            while True:
                data = download_one_page_from_ctgov(session=session,
                                                    query_term=query_term,
                                                    page_token=page_token)

                if num_trials_for_stage is None:
                    num_trials_for_stage = data.get("totalCount")

                page_trials = data.get("studies", [])
                all_trials_raw.extend(page_trials)

                logger.info(
                    "Stage %d (%s) | Page %d | Trials on page: %d | Raw accumulated (with duplicates): %d | Server reported total for stage: %s",
                    stage_idx, label, on_page, len(page_trials), len(all_trials_raw), num_trials_for_stage,
                )

                page_token = data.get("nextPageToken")
                if not page_token:
                    break

                on_page += 1
                time.sleep(PAUSE_BETWEEN_PAGES)

            logger.info(
                "Finished query stage %d / %d (%s)",
                stage_idx, len(query_terms), label,
            )

        logger.info("Finished all query stages. Raw trial count (with duplicates) = %d", len(all_trials_raw))

        all_trials_unique = _dedup_trials(all_trials_raw)
        logger.info("Unique trials = %d", len(all_trials_unique))

        all_trials_json = os.path.join(args.output_dir, "ctgov_all_trials.json")
        with open(all_trials_json, "w") as f:
            json.dump(obj=all_trials_unique, fp=f, indent=2)

        logger.info("Completed | Final JSON written to: %s", all_trials_json)

    elif args.since:
        logger.error("The --since option is not implemented yet.")
        raise NotImplementedError("Downloading only updated trials (--since) is not implemented yet.")


if __name__ == "__main__":
    main()
