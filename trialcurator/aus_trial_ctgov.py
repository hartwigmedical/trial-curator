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

API_QUERY_PARAMS = """
(
    AREA[ConditionMeshTerm]Neoplasms
    OR
    (
    -- Broad solid-tumor basket phrasing
    AREA[Condition]"advanced cancer"
    OR AREA[Condition]"advanced malignancy"
    OR AREA[Condition]"advanced malignancies"
    OR AREA[Condition]"advanced solid tumor"
    OR AREA[Condition]"advanced solid tumors"
    OR AREA[Condition]"solid tumor"
    OR AREA[Condition]"solid tumors"
    OR AREA[Condition]"solid tumour"
    OR AREA[Condition]"solid tumours"
    OR AREA[Condition]"solid malignancy"
    OR AREA[Condition]"solid malignancies"
    OR AREA[Condition]"advanced carcinoma"

    -- Common histologic or site descriptors
    OR AREA[Condition]"endometrial cancer"
    OR AREA[Condition]"endometrial carcinoma"
    OR AREA[Condition]"ovarian cancer"
    OR AREA[Condition]"ovarian carcinoma"
    OR AREA[Condition]"cervical cancer"
    OR AREA[Condition]"cervical carcinoma"
    OR AREA[Condition]"vulvar cancer"
    OR AREA[Condition]"vulvar carcinoma"
    OR AREA[Condition]"bile duct cancer"
    OR AREA[Condition]"cholangiocarcinoma"
    OR AREA[Condition]"gallbladder cancer"
    OR AREA[Condition]"biliary cancer"
    OR AREA[Condition]"biliary tract cancer"
    OR AREA[Condition]"salivary gland cancer"
    OR AREA[Condition]"salivary gland carcinoma"
    OR AREA[Condition]"parotid gland cancer"
    OR AREA[Condition]"thyroid cancer"
    OR AREA[Condition]"thyroid carcinoma"
    OR AREA[Condition]"mesothelioma"
    OR AREA[Condition]"neuroendocrine tumor"
    OR AREA[Condition]"neuroendocrine tumors"
    OR AREA[Condition]"carcinoid tumor"
    OR AREA[Condition]"merkel cell carcinoma"
    OR AREA[Condition]"glioblastoma"
    OR AREA[Condition]"glioblastoma multiforme"
    OR AREA[Condition]"glioma"
    OR AREA[Condition]"astrocytoma"
    OR AREA[Condition]"rhabdomyosarcoma"
    OR AREA[Condition]"osteosarcoma"
    OR AREA[Condition]"chondrosarcoma"
    OR AREA[Condition]"retinoblastoma"

    -- Lung and thoracic subtypes
    OR AREA[Condition]"non small cell lung cancer"
    OR AREA[Condition]"non-small cell lung cancer"
    OR AREA[Condition]"nsclc"
    OR AREA[Condition]"small cell lung cancer"
    OR AREA[Condition]"sclc"
    OR AREA[Condition]"lung cancer"
    OR AREA[Condition]"lung carcinoma"

    -- Prostate and genitourinary
    OR AREA[Condition]"prostate cancer"
    OR AREA[Condition]"prostate carcinoma"
    OR AREA[Condition]"castration-resistant prostate cancer"
    OR AREA[Condition]"metastatic castration-resistant prostate cancer"
    OR AREA[Condition]"mcrpc"
    OR AREA[Condition]"renal cell carcinoma"
    OR AREA[Condition]"kidney cancer"
    OR AREA[Condition]"urothelial carcinoma"
    OR AREA[Condition]"bladder cancer"

    -- Breast and gynecologic
    OR AREA[Condition]"breast cancer"
    OR AREA[Condition]"breast carcinoma"
    OR AREA[Condition]"triple negative breast cancer"

    -- Gastrointestinal
    OR AREA[Condition]"colorectal cancer"
    OR AREA[Condition]"colon cancer"
    OR AREA[Condition]"rectal cancer"
    OR AREA[Condition]"pancreatic cancer"
    OR AREA[Condition]"pancreatic ductal adenocarcinoma"
    OR AREA[Condition]"gastric cancer"
    OR AREA[Condition]"stomach cancer"
    OR AREA[Condition]"gastroesophageal junction cancer"
    OR AREA[Condition]"esophageal cancer"
    OR AREA[Condition]"esophageal carcinoma"
    OR AREA[Condition]"hepatocellular carcinoma"
    OR AREA[Condition]"liver cancer"

    -- Head and neck
    OR AREA[Condition]"head and neck cancer"
    OR AREA[Condition]"head and neck squamous cell carcinoma"
    OR AREA[Condition]"nasopharyngeal carcinoma"

    -- Hematologic / marrow
    OR AREA[Condition]"leukemia"
    OR AREA[Condition]"acute myeloid leukemia"
    OR AREA[Condition]"aml"
    OR AREA[Condition]"myelodysplastic syndrome"
    OR AREA[Condition]"myelodysplastic syndromes"
    OR AREA[Condition]"myelodysplastic neoplasm"
    OR AREA[Condition]"myelofibrosis"
    OR AREA[Condition]"lymphoma"
    OR AREA[Condition]"b-cell malignancy"
    OR AREA[Condition]"multiple myeloma"

    -- Melanoma and skin
    OR AREA[Condition]"melanoma"
    OR AREA[Condition]"uveal melanoma"
    OR AREA[Condition]"cutaneous squamous cell carcinoma"

    -- Miscellaneous basket descriptors
    OR AREA[Condition]"advanced malignancies"
    OR AREA[Condition]"advanced tumors"
    OR AREA[Condition]"advanced or metastatic solid tumors"
    )
)
AND 
(
    AREA[LocationCountry]Australia
    OR AREA[LocationCountry]"New Zealand"
)
AND
(
    AREA[OverallStatus]RECRUITING
    OR AREA[OverallStatus]NOT_YET_RECRUITING
    OR AREA[OverallStatus]ACTIVE_NOT_RECRUITING
    OR AREA[OverallStatus]ENROLLING_BY_INVITATION
)
AND
(
    AREA[InterventionType]DRUG
    OR AREA[InterventionType]BIOLOGICAL
    OR AREA[InterventionType]DIAGNOSTIC_TEST
    OR AREA[InterventionType]RADIATION
    OR AREA[InterventionType]DEVICE
    OR AREA[InterventionType]COMBINATION_PRODUCT
    OR AREA[InterventionType]OTHER
)
""".strip()

PAGE_SIZE = 1000  # Max allowed on CT.gov
TIMEOUT = 30  # unit is seconds
PAUSE_BETWEEN_PAGES = 0.5  # unit is seconds


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


def download_one_page_from_ctgov(session: requests.Session, page_token: Optional[str] = None) -> dict[str, Any]:
    """
    This function downloads ONE PAGE of the Australian cancer trials from ClinicalTrials.gov
    according to a pre-set search criteria using ClinicalTrials' REST API

    More documentation on https://hartwigmedical.atlassian.net/wiki/spaces/AUS/pages/823263285/Australian+clinical+trials+universe
    """

    request_params = {
        "countTotal": "true",
        "format": "json",
        "pageSize": PAGE_SIZE,
        "query.term": API_QUERY_PARAMS
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
        all_trials: list[dict[str, Any]] = []
        num_trials = None
        page_token = None
        on_page = 1

        while True:

            data = download_one_page_from_ctgov(session, page_token=page_token)

            if num_trials is None:
                num_trials = data.get("totalCount")

            page_trials = data.get("studies", [])
            all_trials.extend(page_trials)

            # per-page json outputs
            with open(os.path.join(args.output_dir, f"page_num_{on_page}.json"), "w") as f:
                json.dump(obj=data, fp=f, indent=2)

            logger.info("Downloaded page: %d | Trials on page: %d | Running total: %d | total trials from server: %s",
                        on_page, len(page_trials), len(all_trials), num_trials)

            page_token = data.get("nextPageToken")
            if not page_token:
                break

            on_page += 1

            time.sleep(PAUSE_BETWEEN_PAGES)

        # aggregate json outputs
        all_trials_json = os.path.join(args.output_dir, "all_trials_ctgov.json")
        with open(all_trials_json, "w") as f:
            json.dump(obj=all_trials, fp=f, indent=2)

        logger.info("Completed | Trials downloaded=%d | Trials from server=%s",
                    len(all_trials), num_trials)


if __name__ == "__main__":
    main()
