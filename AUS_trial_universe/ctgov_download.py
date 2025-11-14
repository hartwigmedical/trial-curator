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

API_QUERY_PARAMS_CORE = """
(
    AREA[ConditionMeshTerm]Neoplasms
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
)
""".strip()

API_QUERY_PARAMS_POTTR_APPEND_1 = """
(
    AREA[Condition]"acute myeloid leukemia"
    OR AREA[Condition]"advanced cancer"
    OR AREA[Condition]"advanced carcinoma"
    OR AREA[Condition]"advanced malignancies"
    OR AREA[Condition]"advanced malignant tumors"
    OR AREA[Condition]"advanced or metastatic nsclc"
    OR AREA[Condition]"advanced or metastatic solid tumors"
    OR AREA[Condition]"advanced solid tumor"
    OR AREA[Condition]"advanced solid tumors"
    OR AREA[Condition]"advanced tumors"

    OR AREA[Condition]"alk-positive non-small cell lung cancer"
    OR AREA[Condition]"aml"
    OR AREA[Condition]"anal cancer"
    OR AREA[Condition]"anal carcinoma"
    OR AREA[Condition]"astrocytoma"

    OR AREA[Condition]"b-cell malignancies"
    OR AREA[Condition]"b-cell malignancy"
    OR AREA[Condition]"biliary cancer"
    OR AREA[Condition]"biliary tract cancer"
    OR AREA[Condition]"bile duct cancer"
    OR AREA[Condition]"bilateral retinoblastoma"
    OR AREA[Condition]"bladder cancer"
    OR AREA[Condition]"breast cancer"
    OR AREA[Condition]"breast carcinoma"

    OR AREA[Condition]"carcinoid tumor"
    OR AREA[Condition]"carcinoma, non-small cell lung"
    OR AREA[Condition]"carcinoma, non-small-cell lung"
    OR AREA[Condition]"castration-resistant prostate cancer"
    OR AREA[Condition]"cervical cancer"
    OR AREA[Condition]"cervical carcinoma"
    OR AREA[Condition]"chondrosarcoma"
    OR AREA[Condition]"cholangiocarcinoma"
    OR AREA[Condition]"cutaneous squamous cell carcinoma"

    OR AREA[Condition]"endometrial adenocarcinoma"
    OR AREA[Condition]"endometrial cancer"
    OR AREA[Condition]"endometrial carcinoma"
    OR AREA[Condition]"endometrial clear cell adenocarcinoma"
    OR AREA[Condition]"endometrial endometrioid adenocarcinoma"
    OR AREA[Condition]"endometrial serous adenocarcinoma"

    OR AREA[Condition]"esophageal cancer"
    OR AREA[Condition]"esophageal carcinoma"
    OR AREA[Condition]"extrahepatic cholangiocarcinoma"

    OR AREA[Condition]"fallopian tube cancer"
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

API_QUERY_PARAMS_POTTR_APPEND_2 = """
(
    AREA[Condition]"gall bladder cancer"
    OR AREA[Condition]"gall bladder carcinoma"
    OR AREA[Condition]"gallbladder cancer"
    OR AREA[Condition]"gallbladder carcinoma"
    OR AREA[Condition]"gastric cancer"
    OR AREA[Condition]"gastroesophageal junction cancer"
    OR AREA[Condition]"gastrointestinal malignancy"
    OR AREA[Condition]"gene alteration"
    OR AREA[Condition]"glioblastoma"
    OR AREA[Condition]"glioblastoma multiforme"
    OR AREA[Condition]"glioma"
    OR AREA[Condition]"group d retinoblastoma"

    OR AREA[Condition]"head and neck cancer"
    OR AREA[Condition]"head and neck squamous cell carcinoma"
    OR AREA[Condition]"hematological malignancy"
    OR AREA[Condition]"hepatocellular carcinoma"
    OR AREA[Condition]"her2-positive breast cancer"
    OR AREA[Condition]"high-risk neuroblastoma"

    OR AREA[Condition]"intrahepatic cholangiocarcinoma"

    OR AREA[Condition]"kidney cancer"
    OR AREA[Condition]"kras p.g12c"

    OR AREA[Condition]"leukemia"
    OR AREA[Condition]"liver cancer"
    OR AREA[Condition]"locally advanced or metastatic her2-expressing cancers"
    OR AREA[Condition]"lung adenocarcinoma"
    OR AREA[Condition]"lung cancer"
    OR AREA[Condition]"lung cancers"
    OR AREA[Condition]"lung carcinoma"
    OR AREA[Condition]"lymphoma"

    OR AREA[Condition]"malignant pleural mesothelioma"
    OR AREA[Condition]"medullary thyroid cancer"
    OR AREA[Condition]"melanoma"
    OR AREA[Condition]"merkel cell carcinoma"
    OR AREA[Condition]"mesothelioma"
    OR AREA[Condition]"metastatic non small cell lung cancer"
    OR AREA[Condition]"metastatic non-small cell lung cancer"
    OR AREA[Condition]"metastatic; her2-positive breast cancer"
    OR AREA[Condition]"metastatic nasopharyngeal carcinoma"
    OR AREA[Condition]"metastatic castration-resistant prostate cancer"
    OR AREA[Condition]"mcrpc"
    OR AREA[Condition]"multiple myeloma"
    OR AREA[Condition]"myelodysplastic neoplasm"
    OR AREA[Condition]"myelodysplastic syndrome"
    OR AREA[Condition]"myelodysplastic syndromes"
    OR AREA[Condition]"myelofibrosis"
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

API_QUERY_PARAMS_POTTR_APPEND_3 = """
(
    AREA[Condition]"nasopharyngeal carcinoma"
    OR AREA[Condition]"neoplastic disease"
    OR AREA[Condition]"neoplasms"
    OR AREA[Condition]"neuroblastoma"
    OR AREA[Condition]"neuroendocrine tumor"
    OR AREA[Condition]"non small cell lung cancer"
    OR AREA[Condition]"non-small cell lung cancer"
    OR AREA[Condition]"nsclc"

    OR AREA[Condition]"other solid tumors"
    OR AREA[Condition]"ovarian cancer"
    OR AREA[Condition]"ovarian carcinoma"
    OR AREA[Condition]"ovarian clear cell adenocarcinoma"
    OR AREA[Condition]"ovarian clear cell carcinoma"
    OR AREA[Condition]"ovarian epithelial cancer"
    OR AREA[Condition]"ovarian endometrioid adenocarcinoma"
    OR AREA[Condition]"ovarian mucinous adenocarcinoma"
    OR AREA[Condition]"ovarian carcinosarcoma"
    OR AREA[Condition]"ovarian cancer"
    OR AREA[Condition]"ovarian epithelial cancer"
    OR AREA[Condition]"ovarian endometrioid adenocarcinoma"
    OR AREA[Condition]"ovarian mucinous adenocarcinoma"

    OR AREA[Condition]"pancreas cancer"
    OR AREA[Condition]"pancreatic cancer"
    OR AREA[Condition]"pancreatic ductal adenocarcinoma"
    OR AREA[Condition]"papillary thyroid cancer"
    OR AREA[Condition]"parotid gland cancer"
    OR AREA[Condition]"patient with insufficient response chemoimmunotherapy"
    OR AREA[Condition]"platinum resistant ovarian cancer"
    OR AREA[Condition]"platinum-resistant ovarian cancer"
    OR AREA[Condition]"primary peritoneal carcinoma"
    OR AREA[Condition]"prostate cancer"
    OR AREA[Condition]"prostate cancers"
    OR AREA[Condition]"prostate carcinoma"
    OR AREA[Condition]"ptcl"

    OR AREA[Condition]"rectal cancer"
    OR AREA[Condition]"relapsed or refractory multiple myeloma"
    OR AREA[Condition]"renal cell carcinoma"
    OR AREA[Condition]"retinoblastoma"
    OR AREA[Condition]"rhabdomyosarcoma"
    OR AREA[Condition]"ros1-positive non-small cell lung cancer"

    OR AREA[Condition]"salivary cancer"
    OR AREA[Condition]"salivary gland cancer"
    OR AREA[Condition]"salivary gland carcinoma"
    OR AREA[Condition]"sclc"
    OR AREA[Condition]"skin cancer"
    OR AREA[Condition]"small cell lung cancer"
    OR AREA[Condition]"small cell lung carcinoma"
    OR AREA[Condition]"solid cancer"
    OR AREA[Condition]"solid malignancies"
    OR AREA[Condition]"solid tumor"
    OR AREA[Condition]"solid tumors"
    OR AREA[Condition]"solid tumour"
    OR AREA[Condition]"solid tumours"
    OR AREA[Condition]"stage i retinoblastoma"
    OR AREA[Condition]"stage ii nasopharyngeal carcinoma"
    OR AREA[Condition]"stage iii nasopharyngeal carcinoma"
    OR AREA[Condition]"stage iv nasopharyngeal carcinoma"
    OR AREA[Condition]"stomach cancer"

    OR AREA[Condition]"thyroid cancer"
    OR AREA[Condition]"thyroid carcinoma"
    OR AREA[Condition]"triple negative breast cancer"

    OR AREA[Condition]"unilateral retinoblastoma"
    OR AREA[Condition]"unresectable solid tumors"

    OR AREA[Condition]"urothelial carcinoma"

    OR AREA[Condition]"uveal melanoma"

    OR AREA[Condition]"vulvar cancer"
    OR AREA[Condition]"vulvar carcinoma"
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

API_QUERY_TERMS = [
    API_QUERY_PARAMS_CORE,
    API_QUERY_PARAMS_POTTR_APPEND_1, API_QUERY_PARAMS_POTTR_APPEND_2, API_QUERY_PARAMS_POTTR_APPEND_3
]

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


def download_one_page_from_ctgov(session: requests.Session, query_term: str, page_token: Optional[str] = None) -> dict[
    str, Any]:
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


def main():
    parser = argparse.ArgumentParser(
        description="Download ALL Australian cancer trials from ClinicalTrials.gov OR Download the UPDATED trials only")

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
        all_trials_raw: list[dict[str, Any]] = []
        download_stage = 0

        for query_term in API_QUERY_TERMS:
            download_stage += 1
            logger.info("Starting query stage %d / %d", download_stage, len(API_QUERY_TERMS))

            num_trials_for_stage = None
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
                    "Stage %d | Page %d | Trials on page: %d | Stage reported total: %s | Raw accumulated (with duplicates): %d",
                    download_stage, on_page,
                    len(page_trials), num_trials_for_stage, len(all_trials_raw)
                )

                page_token = data.get("nextPageToken")
                if not page_token:
                    break

                on_page += 1
                time.sleep(PAUSE_BETWEEN_PAGES)

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
