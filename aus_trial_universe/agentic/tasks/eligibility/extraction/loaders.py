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
import re
from pathlib import Path

from aus_trial_universe.agentic.core.paths import ANZCTR_ROOT, CTGOV_ROOT, ELIGIBILITY_OUTPUT
from aus_trial_universe.agentic.core.pipeline_io import find_best_file, latest_version_dir
from aus_trial_universe.agentic.tasks.eligibility.extraction.workflow import Cohort

# ctgov input_trials keeps its own version_<ddmmyyyy>/ dirs (managed by the download pipeline, read via
# latest_version_dir); only the roots move under the consolidated trial_universe/ tree.
CTGOV_INPUT_ROOT = CTGOV_ROOT / "input_trials"
CTGOV_LATEST_JSON = CTGOV_ROOT / "download_state/ctgov_trials_latest.json"
ANZCTR_CSV = ANZCTR_ROOT / "extracted_trials/anzctr_field_extractions.csv"
DEFAULT_OUT_DIR = ELIGIBILITY_OUTPUT


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


# A regime is a DRUG regime: only pharmacological agents count (spec §6.1). A literal "Drug:" filter would
# drop the many investigational BIOLOGICALS (antibodies, ADCs, cell therapies) — e.g. NCT07099898's experimental
# arm "Ris-Rez" is typed "Biological:", so filtering to "Drug:" alone would keep only the comparator.
_PHARMACOLOGICAL_TYPES = {"drug", "biological"}


def _intervention_type(name: str) -> str:
    """Type prefix of an armGroup interventionName ('Drug: Topotecan' -> 'drug'); '' if unprefixed."""
    name = (name or "").strip()
    return name.split(":", 1)[0].strip().lower() if ":" in name else ""


def _is_placebo(name: str) -> bool:
    return "placebo" in (name or "").lower()


def _pharmacological_drugs(intervention_names: list) -> list[str]:
    """Cleaned names of the {Drug, Biological} interventions in an arm; placebo excluded, order-preserving dedup."""
    out: list[str] = []
    for n in intervention_names or []:
        if not isinstance(n, str):
            continue
        if _intervention_type(n) in _PHARMACOLOGICAL_TYPES and not _is_placebo(n):
            clean = _clean_intervention_name(n)
            if clean and clean not in out:
                out.append(clean)
    return out


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


# An armGroup whose label announces it is not recruiting is not a matchable regime — drop it (spec §6.1: closed /
# retired cohorts are dropped). Heuristic on the LABEL text: a few trials (e.g. NCT05009992) encode arm status
# there ("NOT CURRENTLY ENROLLING - ARM 2 ..."); the standard schema has no per-arm status field.
_CLOSED_ARM_RE = re.compile(
    r"not currently enrolling|no longer enrolling|closed to (?:accrual|enrol|recruit)|"
    r"\bwithdrawn\b|\bsuspended\b|\bterminated\b", re.I)


def _ctgov_cohorts(ai: dict) -> list[Cohort]:
    """One regime per DRUG-BEARING, OPEN armGroup (spec §6.1 — the regime axis).

    A regime = an armGroup with >=1 pharmacological agent ({Drug, Biological}); placebo-only / pure-radiation /
    procedure-only arms carry no drug and are dropped (not a drug regime), and arms whose label marks them
    closed/not-recruiting are dropped (not a matchable regime). Each regime carries arm_type (which flags control
    arms) and the armGroup `description` — the best signal for eligibility->regime assignment.
    """
    cohorts: list[Cohort] = []
    for arm in ai.get("armGroups") or []:
        label = (arm.get("label") or "").strip()
        if _CLOSED_ARM_RE.search(label):  # closed / not-recruiting arm -> not a matchable regime, drop it
            continue
        drugs = _pharmacological_drugs(arm.get("interventionNames"))
        if not drugs:  # no pharmacological agent -> not a drug regime
            continue
        cohorts.append(Cohort(
            label=label or "arm",
            drug="; ".join(drugs),
            drug_source="INTERVENTIONS MODULE",
            description=(arm.get("description") or "").strip(),
            arm_type=(arm.get("type") or "").strip(),
        ))
    if cohorts:
        return cohorts
    # Fallback (no drug-bearing armGroups): single regime from the pharmacological interventions[]. Type is the
    # canonical enum here (DRUG/BIOLOGICAL/RADIATION/…); keep untyped names (lenient) but drop typed non-drugs.
    drugs: list[str] = []
    for iv in ai.get("interventions") or []:
        name = (iv.get("name") or "").strip()
        itype = (iv.get("type") or "").strip().lower()
        if not name or _is_placebo(name) or (itype and itype not in _PHARMACOLOGICAL_TYPES):
            continue
        if name not in drugs:
            drugs.append(name)
    return [Cohort(label="all", drug="; ".join(drugs), drug_source="INTERVENTIONS MODULE")]


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
    ("COMPARATOR", "COMPARATOR"),  # comparator-arm drugs (regime axis, spec §6.1)
    ("CONTROL", "CONTROL"),        # Active/Placebo/Uncontrolled/… — signals whether a comparator drug exists
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
