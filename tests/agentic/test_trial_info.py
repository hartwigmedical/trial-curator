"""trial_info master — deterministic extraction from raw CTGov protocolSection / ANZCTR rows + round-trip."""
from __future__ import annotations

from aus_trial_universe.agentic import trial_info as TI


def _ctgov_rec():
    return {"protocolSection": {
        "identificationModule": {"nctId": "NCT01", "briefTitle": "Brief", "officialTitle": "Official Title X"},
        "statusModule": {"overallStatus": "RECRUITING",
                         "startDateStruct": {"date": "2025-01-01"},
                         "primaryCompletionDateStruct": {"date": "2027-01-01"},
                         "completionDateStruct": {"date": "2028-01-01"},
                         "lastUpdatePostDateStruct": {"date": "2026-05-01"}},
        "designModule": {"studyType": "INTERVENTIONAL", "phases": ["PHASE2", "PHASE3"]},
        "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Acme Onc"}},
        "eligibilityModule": {"minimumAge": "18 Years", "maximumAge": "65 Years", "sex": "FEMALE"},
        "contactsLocationsModule": {"locations": [
            {"country": "United States", "city": "Boston", "status": "RECRUITING"},
            {"country": "Australia", "city": "Sydney", "status": "RECRUITING"},
            {"country": "Australia", "city": "Melbourne", "status": "NOT_YET_RECRUITING"},
        ]},
    }}


def test_ctgov_extraction():
    ti = TI.ctgov_trial_info(_ctgov_rec())
    assert ti.trialId == "NCT01" and ti.registry == "ctgov"
    assert ti.official_title == "Official Title X"
    assert ti.phase == "PHASE2; PHASE3" and ti.study_type == "INTERVENTIONAL"
    assert ti.overall_status == "RECRUITING" and ti.lead_sponsor == "Acme Onc"
    assert (ti.start_date, ti.primary_completion_date, ti.completion_date) == ("2025-01-01", "2027-01-01", "2028-01-01")
    assert ti.last_update_date == "2026-05-01"
    assert (ti.min_age, ti.max_age, ti.sex) == ("18 Years", "65 Years", "FEMALE")
    assert ti.countries == "United States; Australia"          # order-preserving distinct
    assert ti.has_AU_site == "true"
    assert ti.AU_site_cities == "Sydney; Melbourne"
    assert ti.AU_site_status == "RECRUITING; NOT_YET_RECRUITING"
    assert ti.trial_url == "https://clinicaltrials.gov/study/NCT01"


def test_ctgov_no_au_site():
    rec = _ctgov_rec()
    rec["protocolSection"]["contactsLocationsModule"]["locations"] = [{"country": "France", "city": "Paris"}]
    ti = TI.ctgov_trial_info(rec)
    assert ti.has_AU_site == "false" and ti.AU_site_cities == "" and ti.AU_site_status == ""


def _anzctr_row():
    return {"ACTRN": "12605000025639", "SCIENTIFIC TITLE": "The MAX Study",
            "PHASE": "Phase 2 / Phase 3", "RECRUITMENT STATUS": "Active, not recruiting",
            "PRIMARY SPONSOR NAME": "AGITG", "APPROVAL DATE": "2005-07-19 13:57:48.000",
            "MIN AGE": "18.0", "MIN AGE TYPE": "Years", "MAX AGE": "0.0", "MAX AGE TYPE": "Not stated",
            "INCLUSIVE GENDER": "Both males and females", "RECRUITMENT COUNTRY": "Australia",
            "RECRUITMENT STATE": "Victoria"}


def test_anzctr_extraction():
    ti = TI.anzctr_trial_info(_anzctr_row())
    assert ti.trialId == "ACTRN12605000025639" and ti.registry == "anzctr"
    assert ti.official_title == "The MAX Study" and ti.phase == "Phase 2 / Phase 3"
    assert ti.overall_status == "Active, not recruiting" and ti.study_type == ""       # no clean anzctr field
    assert ti.lead_sponsor == "AGITG"
    assert (ti.start_date, ti.completion_date) == ("", "")                             # not in the CSV
    assert ti.last_update_date == "2005-07-19"                                         # approval date, date-only
    assert ti.min_age == "18 Years" and ti.max_age == ""                              # "0"/"Not stated" -> ""
    assert ti.sex == "ALL"                                                             # "Both males and females"
    assert ti.has_AU_site == "true" and ti.AU_site_status == "Active, not recruiting"
    assert ti.AU_site_cities == "Victoria"


def test_anzctr_age_and_sex_helpers():
    assert TI._anzctr_age("18.0", "Years") == "18 Years"
    assert TI._anzctr_age("0.0", "Not stated") == "" and TI._anzctr_age("", "Years") == ""
    assert TI._anzctr_sex("Both males and females") == "ALL"
    assert TI._anzctr_sex("Males") == "MALE" and TI._anzctr_sex("Females") == "FEMALE"


def test_save_load_round_trip(tmp_path):
    infos = {"NCT01": TI.ctgov_trial_info(_ctgov_rec()), "ACTRN12605000025639": TI.anzctr_trial_info(_anzctr_row())}
    TI.save_trial_info(infos, root=tmp_path)
    loaded = TI.load_trial_info(root=tmp_path)
    assert set(loaded) == set(infos)
    assert loaded["NCT01"].phase == "PHASE2; PHASE3" and loaded["NCT01"].has_AU_site == "true"
    assert loaded["ACTRN12605000025639"].sex == "ALL"
