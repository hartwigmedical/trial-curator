from __future__ import annotations

import argparse
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
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

DATE_OVERLAP_DAYS = 1  # a safety measure of <stored_date - 1 day> when downloading new/since updated trials


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


def _extract_last_update_post_date(study_obj: dict[str, Any]) -> Optional[str]:
    try:
        return study_obj["protocolSection"]["statusModule"]["lastUpdatePostDateStruct"]["date"]
    except KeyError:
        return None


def _extract_first_post_date(study_obj: dict[str, Any]) -> Optional[str]:
    try:
        return study_obj["protocolSection"]["statusModule"]["studyFirstPostDateStruct"]["date"]
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


def _parse_isodate(s: str) -> date:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError as e:
        raise ValueError(f"Invalid ISO date '{s}'. Expected YYYY-MM-DD.") from e


def _format_isodate(d: date) -> str:
    return d.isoformat()


def _apply_dual_date_filter(query_term: str, cutoff_iso: str) -> str:
    # Define new trial as (StudyFirstPostDate >= cutoff) OR (LastUpdatePostDate >= cutoff)
    # Area names are the Essie search areas used in query.term (not JSON field names). Wrap the original query to preserve OR precedence inside it.

    dual_date_clause = (
        f"(AREA[StudyFirstPostDate]RANGE[{cutoff_iso},MAX] "
        f"OR AREA[LastUpdatePostDate]RANGE[{cutoff_iso},MAX])"
    )
    return f"(\n{query_term.strip()}\n)\nAND\n{dual_date_clause}"


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


def _download_all_matching_trials(session: requests.Session, query_terms: list[tuple[str, str]]) -> list[dict[str, Any]]:
    all_trials_raw: list[dict[str, Any]] = []

    for stage_idx, (label, query_term) in enumerate(query_terms, start=1):
        logger.info("Starting query stage %d / %d (%s)", stage_idx, len(query_terms), label)

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

            logger.info("Stage %d (%s) | Page %d | Trials on page: %d | Raw accumulated (with duplicates): %d | Server reported total for stage: %s",
                stage_idx, label, on_page, len(page_trials), len(all_trials_raw), num_trials_for_stage,
            )

            page_token = data.get("nextPageToken")
            if not page_token:
                break

            on_page += 1
            time.sleep(PAUSE_BETWEEN_PAGES)

        logger.info("Finished query stage %d / %d (%s)", stage_idx, len(query_terms), label)

    logger.info("Finished all query stages. Raw trial count (with duplicates) = %d", len(all_trials_raw))
    all_trials_unique = _dedup_trials(all_trials_raw)

    logger.info("Unique trials = %d", len(all_trials_unique))
    return all_trials_unique


def _write_json_atomic(path: Path, obj: Any) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")

    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(obj=obj, fp=f, indent=2)
        f.write("\n")

    os.replace(tmp_path, path)


def _load_trials_json(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    with path.open("r", encoding="utf-8") as f:
        obj = json.load(f)

    if not isinstance(obj, list):
        raise ValueError(f"Expected a JSON list in {path}, got {type(obj).__name__}")

    return obj


def _merge_trials_by_nct_id(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}

    for study in existing:
        nct_id = _extract_nct_id(study)
        if nct_id:
            merged[nct_id] = study

    added = 0
    overwritten = 0
    for study in incoming:
        nct_id = _extract_nct_id(study)
        if not nct_id:
            continue
        if nct_id in merged:
            overwritten += 1
        else:
            added += 1
        merged[nct_id] = study

    logger.info("Merge complete | added=%d | overwritten=%d | final=%d", added, overwritten, len(merged))
    return list(merged.values())


def _max_date(values: list[Optional[date]]) -> Optional[date]:
    out: Optional[date] = None

    for v in values:
        if v is None:
            continue
        if out is None or v > out:
            out = v
    return out


def _max_first_post_date(trials: list[dict[str, Any]]) -> Optional[date]:
    max_d: Optional[date] = None

    for study in trials:
        s = _extract_first_post_date(study)
        if not s:
            continue
        d = _parse_isodate(s)
        if max_d is None or d > max_d:
            max_d = d

    return max_d


def _max_last_update_post_date(trials: list[dict[str, Any]]) -> Optional[date]:
    max_d: Optional[date] = None

    for study in trials:
        s = _extract_last_update_post_date(study)
        if not s:
            continue
        d = _parse_isodate(s)
        if max_d is None or d > max_d:
            max_d = d

    return max_d


# Meta file schema: { "last_successful_sync_date": "YYYY-MM-DD" }
@dataclass(frozen=True)
class CtgovMeta:
    last_successful_sync_date: date


def _load_meta(meta_path: Path) -> CtgovMeta:
    if not meta_path.exists():
        raise FileNotFoundError(
            f"Meta file not found: {meta_path}. Create it first: {'last_successful_sync_date': 'YYYY-MM-DD'}."
        )

    with meta_path.open("r", encoding="utf-8") as f:
        obj = json.load(f)

    if not isinstance(obj, dict):
        raise ValueError(f"Expected a JSON object in {meta_path}, got {type(obj).__name__}")

    raw = obj.get("last_successful_sync_date")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(
            f"Meta file {meta_path} missing required key 'last_successful_sync_date' (YYYY-MM-DD)."
        )

    return CtgovMeta(last_successful_sync_date=_parse_isodate(raw.strip()))


def _compute_cutoff_iso(meta: CtgovMeta) -> str:
    cutoff = meta.last_successful_sync_date - timedelta(days=DATE_OVERLAP_DAYS)
    return _format_isodate(cutoff)


def _write_meta(meta_path: Path, merged_trials: list[dict[str, Any]]) -> None:
    max_first = _max_first_post_date(merged_trials)
    max_update = _max_last_update_post_date(merged_trials)
    baseline = _max_date([max_first, max_update])

    if baseline is None:
        raise ValueError("Cannot update meta: neither FirstPostDate nor LastUpdatePostDate found in merged trials.")

    payload = {"last_successful_sync_date": _format_isodate(baseline)}

    _write_json_atomic(meta_path, payload)


def main():
    parser = argparse.ArgumentParser(description="Download Australian and New Zealand cancer trials from ClinicalTrials.gov (full coverage or incremental delta).")

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--all", action="store_true", help="Download all trials subject to the search criteria.")
    mode.add_argument("--incremental", action="store_true", help="Download only new/updated trials since last successful download.")

    parser.add_argument("--output_dir", required=True, help="Per-run output directory")
    parser.add_argument("--state_dir", required=True, help="Persistent state directory.")

    parser.add_argument("--log_level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], help="Logging level")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    state_dir = Path(args.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)

    # Per-run outputs
    delta_path = output_dir / "ctgov_trials_delta.json"
    merged_output_path = output_dir / "ctgov_trials_merged.json"

    # Persistent state files
    persistent_cache_path = state_dir / "ctgov_trials_latest.json"

    meta_glob = "ctgov_trials_meta_*.json"

    def _latest_meta_path() -> Path:
        candidates = sorted(state_dir.glob(meta_glob), key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            raise FileNotFoundError(f"No meta files found in {state_dir}. Run --all first.")
        return candidates[0]

    def _new_meta_path() -> Path:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return state_dir / f"ctgov_trials_meta_{ts}.json"

    session = create_session()

    base_query_terms = generate_query_terms()

    if args.all:
        all_trials_unique = _download_all_matching_trials(session=session, query_terms=base_query_terms)

        # Write full snapshot to this run
        _write_json_atomic(merged_output_path, all_trials_unique)

        # Also update persistent cache
        _write_json_atomic(persistent_cache_path, all_trials_unique)

        # Write new meta
        meta_path = _new_meta_path()
        _write_meta(meta_path, merged_trials=all_trials_unique)

        logger.info("Completed --all | Snapshot written to: %s", merged_output_path)
        logger.info("Persistent cache updated: %s", persistent_cache_path)
        logger.info("Meta written to: %s", meta_path)
        return

    # ---- INCREMENTAL ----
    latest_meta = _load_meta(_latest_meta_path())
    cutoff_iso = _compute_cutoff_iso(latest_meta)

    logger.info(
        "Incremental mode | meta_date=%s | cutoff=%s",
        latest_meta.last_successful_sync_date.isoformat(), cutoff_iso,
    )

    inc_query_terms = []
    for label, q in base_query_terms:
        inc_query_terms.append((label, _apply_dual_date_filter(q, cutoff_iso)))

    delta_trials = _download_all_matching_trials(session=session, query_terms=inc_query_terms)

    # Write delta (for Pydantic curator)
    _write_json_atomic(delta_path, delta_trials)
    logger.info("Delta written to: %s (count=%d)", delta_path, len(delta_trials))

    # Load previous full snapshot
    if not persistent_cache_path.exists():
        raise FileNotFoundError(
            f"Persistent cache not found at {persistent_cache_path}. "
            "Run --all first."
        )

    existing_full = _load_trials_json(persistent_cache_path)

    # Merge
    merged_trials = _merge_trials_by_nct_id(existing_full, delta_trials)

    # Write merged snapshot for this run
    _write_json_atomic(merged_output_path, merged_trials)

    # Update persistent cache
    _write_json_atomic(persistent_cache_path, merged_trials)

    # Write new meta
    new_meta = _new_meta_path()
    _write_meta(new_meta, merged_trials)

    logger.info("Merged snapshot written to: %s", merged_output_path)
    logger.info("Persistent cache updated: %s", persistent_cache_path)
    logger.info("Meta written to: %s", new_meta)


if __name__ == "__main__":
    main()
