"""Trial loaders + input assembly for the extraction task (CTGov + ANZCTR).

Per trial, assembles ALL relevant sections into one labeled document and enumerates cohorts:
  ctgov  -> title, official title, conditions, keywords, summary, detailed description,
            eligibility criteria, interventions module; cohorts from armGroups (arm_type + drug).
  anzctr -> study title, scientific title, health condition, interventions, inclusion + exclusion
            criteria; cohorts (+drug) are LLM-derived in the workflow, so cohorts=None here.

Consumed by the pipeline orchestrator (aus_trial_universe/agentic/run.py).
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from aus_trial_universe.agentic.core.pipeline_io import find_best_file, latest_version_dir
from aus_trial_universe.agentic.tasks.extraction.workflow import Cohort

REPO_ROOT = Path(__file__).resolve().parents[4]
CTGOV_INPUT_ROOT = REPO_ROOT / "data/trial_inputs/ctgov/input_trials"
CTGOV_LATEST_JSON = REPO_ROOT / "data/trial_inputs/ctgov/download_state/ctgov_trials_latest.json"
ANZCTR_CSV = REPO_ROOT / "data/trial_inputs/anzctr/extracted_trials/anzctr_field_extractions.csv"
DEFAULT_OUT_DIR = REPO_ROOT / "data/agentic/output"


def _assemble(sections: list[tuple[str, str | None]]) -> str:
    """Join non-empty (label, text) sections into one labeled document."""
    return "\n\n".join(
        f"## {label}\n{str(text).strip()}" for label, text in sections if text and str(text).strip()
    )


# --- CTGov (versioned merged JSON input; falls back to the download cache) --- #
def _ctgov_records() -> list[dict]:
    """Raw ctgov API records for the current working set (newest versioned merged file)."""
    try:
        version_dir = latest_version_dir(CTGOV_INPUT_ROOT)
        path = find_best_file(
            version_dir,
            token_groups=[["merged"]],
            allowed_suffixes={".json"},
            label="merged ctgov input",
            warn_on_multiple=False,
        )
    except FileNotFoundError:
        path = CTGOV_LATEST_JSON
    return json.loads(Path(path).read_text())


def _ctgov_nct(rec: dict) -> str | None:
    ps = rec.get("protocolSection", {}) or {}
    return (ps.get("identificationModule", {}) or {}).get("nctId")


def _clean_intervention_name(name: str) -> str:
    """'Biological: Ris-Rez' -> 'Ris-Rez' (armGroup.interventionNames are 'Type: Name')."""
    name = (name or "").strip()
    return name.split(":", 1)[1].strip() if ":" in name else name


def _ctgov_interventions_text(ai: dict) -> str:
    """Human-readable render of armsInterventionsModule (interventions + arm->drug mapping)."""
    lines: list[str] = []
    for iv in ai.get("interventions") or []:
        itype = (iv.get("type") or "").strip()
        name = (iv.get("name") or "").strip()
        others = [o.strip() for o in (iv.get("otherNames") or []) if isinstance(o, str) and o.strip()]
        desc = (iv.get("description") or "").strip()
        seg = f"- {itype}: {name}" if itype else f"- {name}"
        if others:
            seg += f" (aka {', '.join(others)})"
        if desc:
            seg += f" — {desc}"
        lines.append(seg)
    for arm in ai.get("armGroups") or []:
        label = (arm.get("label") or "").strip()
        atype = (arm.get("type") or "").strip()
        names = [n for n in (arm.get("interventionNames") or []) if isinstance(n, str)]
        lines.append(f"- Arm '{label}' ({atype}): {', '.join(names)}")
    return "\n".join(lines)


def _assemble_ctgov_text(protocol_section: dict) -> str:
    idm = protocol_section.get("identificationModule", {}) or {}
    desc = protocol_section.get("descriptionModule", {}) or {}
    cond = protocol_section.get("conditionsModule", {}) or {}
    elig = protocol_section.get("eligibilityModule", {}) or {}
    ai = protocol_section.get("armsInterventionsModule", {}) or {}
    return _assemble(
        [
            ("TITLE", idm.get("briefTitle")),
            ("OFFICIAL TITLE", idm.get("officialTitle")),
            ("CONDITIONS", ", ".join(cond.get("conditions", []) or [])),
            ("KEYWORDS", ", ".join(cond.get("keywords", []) or [])),
            ("BRIEF SUMMARY", desc.get("briefSummary")),
            ("DETAILED DESCRIPTION", desc.get("detailedDescription")),
            ("ELIGIBILITY CRITERIA", elig.get("eligibilityCriteria")),
            ("INTERVENTIONS MODULE", _ctgov_interventions_text(ai)),
        ]
    )


def _ctgov_cohorts(ai: dict) -> list[Cohort]:
    """Option A: one cohort per armGroup; drug = that arm's (cleaned) interventionNames."""
    cohorts: list[Cohort] = []
    for arm in ai.get("armGroups") or []:
        label = (arm.get("label") or "").strip() or "arm"
        names = [_clean_intervention_name(n) for n in (arm.get("interventionNames") or []) if isinstance(n, str)]
        drug = "; ".join(dict.fromkeys(n for n in names if n))
        arm_type = (arm.get("type") or "").strip()
        cohorts.append(Cohort(label=label, drug=drug, drug_source="INTERVENTIONS MODULE", arm_type=arm_type))
    if not cohorts:  # no arm structure -> single cohort with all intervention names
        names = [(iv.get("name") or "").strip() for iv in (ai.get("interventions") or [])]
        drug = "; ".join(dict.fromkeys(n for n in names if n))
        cohorts = [Cohort(label="all", drug=drug, drug_source="INTERVENTIONS MODULE")]
    return cohorts


def load_ctgov_trial(trial_id: str) -> tuple[str, list[Cohort]]:
    for rec in _ctgov_records():
        if _ctgov_nct(rec) == trial_id:
            ps = rec.get("protocolSection", {}) or {}
            ai = ps.get("armsInterventionsModule", {}) or {}
            return _assemble_ctgov_text(ps), _ctgov_cohorts(ai)
    raise KeyError(f"{trial_id} not found in ctgov input")


def load_all_ctgov_trials() -> list[tuple[str, str, list[Cohort]]]:
    out: list[tuple[str, str, list[Cohort]]] = []
    for rec in _ctgov_records():
        nct = _ctgov_nct(rec)
        if not nct:
            continue
        ps = rec.get("protocolSection", {}) or {}
        text = _assemble_ctgov_text(ps)
        if text.strip():
            out.append((nct, text, _ctgov_cohorts(ps.get("armsInterventionsModule", {}) or {})))
    return out


# --- ANZCTR (CSV, one row per trial; no arm structure -> single cohort) ------ #
ANZCTR_SECTIONS = [
    ("STUDY TITLE", "STUDY TITLE"),
    ("SCIENTIFIC TITLE", "SCIENTIFIC TITLE"),
    ("HEALTH CONDITION", "HEALTH CONDITION"),
    ("INTERVENTIONS", "INTERVENTIONS"),
    ("INCLUSION CRITERIA", "INCLUSIVE CRITERIA"),
    ("EXCLUSION CRITERIA", "EXCLUSIVE CRITERIA"),
]


def _assemble_anzctr_text(row: dict) -> str:
    return _assemble([(label, row.get(col)) for label, col in ANZCTR_SECTIONS])


def _anzctr_rows() -> list[dict]:
    with open(ANZCTR_CSV, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _norm_actrn(value: str | None) -> str:
    """Normalize ANZCTR ids so 'ACTRN12605000108617' and '12605000108617' match."""
    return (value or "").strip().upper().removeprefix("ACTRN")


def _display_actrn(value: str | None) -> str:
    """Display form of an ANZCTR id: always the 'ACTRN' prefix (CSV stores bare digits)."""
    bare = _norm_actrn(value)
    return f"ACTRN{bare}" if bare else ""


def _infer_source(trial_id: str) -> str | None:
    """Guess the registry from an id: NCT... -> ctgov; ACTRN.../bare digits -> anzctr."""
    tid = (trial_id or "").strip().upper()
    if tid.startswith("NCT"):
        return "ctgov"
    if tid.startswith("ACTRN") or tid.isdigit():
        return "anzctr"
    return None


def load_anzctr_trial(trial_id: str) -> tuple[str, None]:
    """ANZCTR: cohorts + drug are LLM-derived inside the workflow -> cohorts=None."""
    target = _norm_actrn(trial_id)
    for row in _anzctr_rows():
        if _norm_actrn(row.get("ACTRN")) == target:
            return _assemble_anzctr_text(row), None
    raise KeyError(f"{trial_id} not found in {ANZCTR_CSV}")


def load_all_anzctr_trials() -> list[tuple[str, str, None]]:
    out: list[tuple[str, str, None]] = []
    for row in _anzctr_rows():
        actrn = _display_actrn(row.get("ACTRN"))
        if not actrn:
            continue
        text = _assemble_anzctr_text(row)
        if text.strip():
            out.append((actrn, text, None))
    return out


LOADERS = {
    "ctgov": load_ctgov_trial,
    "anzctr": load_anzctr_trial,
}


def _load_one(trial_id: str) -> tuple[str, str, str, list[Cohort] | None]:
    """(source, display_id, base_text, cohorts) for one id; source inferred from the id."""
    source = _infer_source(trial_id)
    if source is None:
        raise ValueError(f"cannot infer source from id {trial_id!r} (expected NCT... or ACTRN.../digits)")
    display_id = _display_actrn(trial_id) if source == "anzctr" else trial_id
    base_text, cohorts = LOADERS[source](trial_id)
    return source, display_id, base_text, cohorts


def load_trials(*, id: str | None = None, ids: list[str] | None = None) \
        -> list[tuple[str, str, str, list[Cohort] | None]]:
    """Load trials for a run: one `id`, a list of `ids`, or (default) ALL trials.

    Returns (source, display_id, base_text, cohorts) tuples.
    """
    if id:
        return [_load_one(id)]
    if ids:
        return [_load_one(x.strip()) for x in ids if x.strip()]
    trials = [("ctgov", tid, text, coh) for tid, text, coh in load_all_ctgov_trials()]
    trials += [("anzctr", tid, text, coh) for tid, text, coh in load_all_anzctr_trials()]
    return trials
