"""Trial-level metadata master (`trial_info`) — one row per trialId, keyed the same way both paths key arms.

Derived DETERMINISTICALLY from the raw CTGov `protocolSection` / ANZCTR rows (no LLM, no judgement): the basic
trial info the matching-engine export (Set A) needs but the 3NF content stores don't hold (title / phase / status /
sponsor / dates / AU sites / demographics). Built by walking the same loaders the extraction path uses, then
persisted as a single-table master under `trial_info/current_version/` and joined into `trial_eligibility.tsv`.

Top-level module (not `tasks/shared`) on purpose: it depends on the eligibility loaders to read the raw source, so
it sits above the path-neutral shared layer alongside `run.py` / `export.py`.
"""
from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from aus_trial_universe.agentic.core.paths import CURRENT_VERSION, TRIAL_INFO_FILE, TRIAL_INFO_ROOT, current_version_dir


@dataclass
class TrialInfo:
    """One trial's basic metadata (deterministic; audit/display + hard demographic filters for the matcher)."""

    trialId: str = ""
    registry: str = ""                 # ctgov | anzctr
    official_title: str = ""
    phase: str = ""                    # e.g. "PHASE3" (ctgov) / "Phase 2 / Phase 3" (anzctr)
    overall_status: str = ""           # RECRUITING / ACTIVE_NOT_RECRUITING / ... (ctgov) / "Active, not recruiting" (anzctr)
    study_type: str = ""               # INTERVENTIONAL (ctgov); "" for anzctr (no clean field)
    lead_sponsor: str = ""
    start_date: str = ""
    primary_completion_date: str = ""
    completion_date: str = ""
    last_update_date: str = ""
    min_age: str = ""                  # e.g. "18 Years"; "" if none
    max_age: str = ""                  # e.g. "65 Years"; "" if none
    sex: str = ""                      # ALL | MALE | FEMALE
    countries: str = ""                # "; "-joined
    has_AU_site: str = ""              # "true" | "false"
    AU_site_status: str = ""           # "; "-joined recruiting statuses of the AU sites
    AU_site_cities: str = ""           # "; "-joined AU cities (anzctr: state — the finest available)
    trial_url: str = ""


TRIAL_INFO_COLUMNS = [f.name for f in fields(TrialInfo)]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _uniq(values) -> list[str]:
    out: list[str] = []
    for v in values:
        v = (v or "").strip()
        if v and v not in out:
            out.append(v)
    return out


def _date(struct) -> str:
    """CTGov `*DateStruct` -> its `date` (YYYY-MM or YYYY-MM-DD); "" if absent."""
    return (struct or {}).get("date", "") if isinstance(struct, dict) else ""


# --------------------------------------------------------------------------- #
# CTGov
# --------------------------------------------------------------------------- #
def ctgov_trial_info(rec: dict) -> TrialInfo | None:
    ps = rec.get("protocolSection", {}) or {}
    idm = ps.get("identificationModule", {}) or {}
    nct = (idm.get("nctId") or "").strip()
    if not nct:
        return None
    status = ps.get("statusModule", {}) or {}
    design = ps.get("designModule", {}) or {}
    sponsor = (ps.get("sponsorCollaboratorsModule", {}) or {}).get("leadSponsor", {}) or {}
    elig = ps.get("eligibilityModule", {}) or {}
    locs = (ps.get("contactsLocationsModule", {}) or {}).get("locations", []) or []
    au = [l for l in locs if isinstance(l, dict) and (l.get("country") or "").strip() == "Australia"]
    return TrialInfo(
        trialId=nct,
        registry="ctgov",
        official_title=(idm.get("officialTitle") or "").strip(),
        phase="; ".join(design.get("phases") or []),
        overall_status=(status.get("overallStatus") or "").strip(),
        study_type=(design.get("studyType") or "").strip(),
        lead_sponsor=(sponsor.get("name") or "").strip(),
        start_date=_date(status.get("startDateStruct")),
        primary_completion_date=_date(status.get("primaryCompletionDateStruct")),
        completion_date=_date(status.get("completionDateStruct")),
        last_update_date=_date(status.get("lastUpdatePostDateStruct")) or (status.get("lastUpdateSubmitDate") or "").strip(),
        min_age=(elig.get("minimumAge") or "").strip(),
        max_age=(elig.get("maximumAge") or "").strip(),
        sex=(elig.get("sex") or "").strip(),
        countries="; ".join(_uniq(l.get("country") for l in locs if isinstance(l, dict))),
        has_AU_site="true" if au else "false",
        AU_site_status="; ".join(_uniq(l.get("status") for l in au)),
        AU_site_cities="; ".join(_uniq(l.get("city") for l in au)),
        trial_url=f"https://clinicaltrials.gov/study/{nct}",
    )


# --------------------------------------------------------------------------- #
# ANZCTR
# --------------------------------------------------------------------------- #
_ANZCTR_NO_AGE = {"", "not stated", "no limit", "none"}


def _anzctr_age(value: str | None, unit: str | None) -> str:
    """ANZCTR age = number + unit; "0"/"Not stated" means unspecified -> ""."""
    unit = (unit or "").strip()
    try:
        n = float((value or "").strip())
    except ValueError:
        return ""
    if n <= 0 or unit.lower() in _ANZCTR_NO_AGE:
        return ""
    return f"{int(n)} {unit}"


def _anzctr_sex(value: str | None) -> str:
    v = (value or "").strip().lower()
    if not v:
        return ""
    has_f = "female" in v
    has_m = "male" in v.replace("female", "")   # avoid the "male" ⊂ "female" substring trap
    if has_f and has_m:
        return "ALL"
    if has_f:
        return "FEMALE"
    if has_m:
        return "MALE"
    return value.strip().upper()


def anzctr_trial_info(row: dict) -> TrialInfo | None:
    from aus_trial_universe.agentic.tasks.eligibility.extraction.loaders import _display_actrn

    actrn = _display_actrn(row.get("ACTRN"))
    if not actrn:
        return None
    country = (row.get("RECRUITMENT COUNTRY") or "").strip()
    status = (row.get("RECRUITMENT STATUS") or "").strip()
    return TrialInfo(
        trialId=actrn,
        registry="anzctr",
        official_title=(row.get("SCIENTIFIC TITLE") or "").strip(),
        phase=(row.get("PHASE") or "").strip(),
        overall_status=status,
        study_type="",                                          # ANZCTR has no clean study-type field
        lead_sponsor=(row.get("PRIMARY SPONSOR NAME") or "").strip(),
        start_date="",                                          # ANZCTR CSV carries no study start/completion dates
        primary_completion_date="",
        completion_date="",
        last_update_date=(row.get("APPROVAL DATE") or "").strip().split(" ")[0],   # registration date (closest proxy)
        min_age=_anzctr_age(row.get("MIN AGE"), row.get("MIN AGE TYPE")),
        max_age=_anzctr_age(row.get("MAX AGE"), row.get("MAX AGE TYPE")),
        sex=_anzctr_sex(row.get("INCLUSIVE GENDER")),
        countries="; ".join(_uniq((country or "").replace("\n", ";").split(";"))),
        has_AU_site="true" if "australia" in country.lower() else "false",
        AU_site_status=status if "australia" in country.lower() else "",   # ANZCTR is trial-level (no per-site status)
        AU_site_cities="; ".join(_uniq((row.get("RECRUITMENT STATE") or "").replace("\n", ";").split(";"))),
        trial_url=f"https://www.anzctr.org.au/Trial/Registration/TrialReview.aspx?ACTRN={actrn}",   # best-effort
    )


# --------------------------------------------------------------------------- #
# build + persist
# --------------------------------------------------------------------------- #
def build_all_trial_info() -> dict[str, TrialInfo]:
    """Walk every CTGov record + ANZCTR row -> {trialId: TrialInfo}. Deterministic, no API."""
    from aus_trial_universe.agentic.tasks.eligibility.extraction.loaders import _anzctr_rows, _ctgov_records

    infos: dict[str, TrialInfo] = {}
    for rec in _ctgov_records():
        ti = ctgov_trial_info(rec)
        if ti:
            infos[ti.trialId] = ti
    for row in _anzctr_rows():
        ti = anzctr_trial_info(row)
        if ti:
            infos[ti.trialId] = ti
    return infos


def save_trial_info(infos: dict[str, TrialInfo], root: Path = TRIAL_INFO_ROOT) -> Path:
    vdir = root / CURRENT_VERSION
    vdir.mkdir(parents=True, exist_ok=True)
    with open(vdir / TRIAL_INFO_FILE, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=TRIAL_INFO_COLUMNS, delimiter="\t", lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        for ti in infos.values():
            w.writerow(asdict(ti))
    return vdir / TRIAL_INFO_FILE


def load_trial_info(root: Path = TRIAL_INFO_ROOT) -> dict[str, TrialInfo]:
    try:
        vdir = current_version_dir(root)
    except FileNotFoundError:
        return {}
    path = vdir / TRIAL_INFO_FILE
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as fh:
        out: dict[str, TrialInfo] = {}
        for row in csv.DictReader(fh, delimiter="\t"):
            ti = TrialInfo(**{k: row.get(k, "") for k in TRIAL_INFO_COLUMNS})
            if ti.trialId:
                out[ti.trialId] = ti
        return out
