"""Per-run refresh report — the durable record of one end-to-end cycle.

A refresh mutates the masters IN PLACE and OVERWRITES the export, so a completed cycle used to leave no trace
beyond its (very large) log. This writes one small Markdown report per run to ``DATA_ROOT/run_report/``:
the fresh universe, the churn (which trial ids were expired / restored / newly curated), the store deltas,
the export shape, and the integrity + export-coverage verdicts.

Counts are read back from the stores and the export file on disk — NOT collected from the sub-CLIs — so the
report describes the state a run actually left behind.

    data/agentic/run_report/refresh_<YYYYmmdd_HHMMSS>.md
"""
from __future__ import annotations

import csv
import json
import logging
from datetime import datetime
from pathlib import Path

from aus_trial_universe.core.paths import ARCHIVE_KEEP, RUN_REPORT_DIR

STATUS_FILE = "STATUS.json"   # machine-readable last-run status for an external monitor (overwritten each run)

logger = logging.getLogger(__name__)

_ID_LIST_CAP = 200          # a first build would otherwise dump thousands of ids into the report


def snapshot() -> dict[str, int]:
    """Countable state of every master, read from disk. Cheap enough to call before and after a run."""
    from aus_trial_universe.tasks.drug_utility.store import DrugRefStore
    from aus_trial_universe.tasks.eligibility.store import EligStore
    from aus_trial_universe.tasks.shared.store import TrialArmStore

    elig, arms, drug = EligStore.load(), TrialArmStore.load(), DrugRefStore.load()
    return {
        "trials curated": len(elig.raw),
        "arms (trial_arms registry)": sum(len(v) for v in arms.arms.values()),
        "arm_eligibility_raw": sum(len(v) for v in elig.raw.values()),
        "interpreted_eligibility": sum(len(v) for v in elig.interpreted.values()),
        "cancer_type_map": len(elig.cancer_map),
        "gene_alteration_map": len(elig.gene_map),
        "molecular_signature_map": len(elig.signature_map),
        "drugs (canonical)": len(drug.refs),
        "intervention_to_canonical": sum(len(v) for v in drug.mappings.values()),
        "trial_to_intervention": len(drug.occurrences),
        "drug_target_actions": sum(len(v) for v in drug.targets.values()),
        "drug_regulatory_approvals": sum(len(v) for v in drug.indications.values()),
        "trial_arm_drug_role": sum(len(v) for v in drug.roles.values()),
        "approval_cancer_type_map": len(drug.approval_cancer_type_map),
        "approval_biomarker_map": len(drug.approval_biomarker_map),
    }


def _export_shape(path: Path) -> dict:
    """Rows / trials / arms / columns of the Set-A export, parsed back from the written file."""
    if not path.exists():
        return {}
    csv.field_size_limit(10 ** 9)
    trials: set[str] = set()
    arms: set[str] = set()
    rows = 0
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        cols = reader.fieldnames or []
        for row in reader:
            rows += 1
            trials.add(row.get("trialId", ""))
            arms.add(row.get("trial_arm_id", ""))
    return {"rows": rows, "trials": len(trials), "arms": len(arms), "columns": len(cols), "trial_ids": trials}


def _coverage() -> dict:
    """Curated-but-not-exported gap: an arm whose interpreted DNF is empty contributes no export row, so its
    trial can be curated and still be absent from the deliverable. Silent unless reported."""
    from aus_trial_universe.tasks.eligibility.store import EligStore
    from aus_trial_universe.tasks.shared.store import TrialArmStore

    elig, arms = EligStore.load(), TrialArmStore.load()
    interpreted = {r.trial_arm_id for rows in elig.interpreted.values() for r in rows}
    empty_arms = [a.trial_arm_id for rows in arms.arms.values() for a in rows if a.trial_arm_id not in interpreted]
    empty_trials = sorted(t for t, rows in arms.arms.items()
                          if all(a.trial_arm_id not in interpreted for a in rows))
    return {"empty_arms": len(empty_arms), "empty_trials": empty_trials}


# A trial first registered within this window of the run is a genuinely NEW registration; an older one entered the
# kept universe because its RECORD changed (AU/NZ sites added, recruitment status flipped into scope). Both look
# identical in a "newly curated" count, and "why did this appear?" is the first question when reviewing a cycle.
NEW_REGISTRATION_DAYS = 30


def _registry_dates(ids: set[str]) -> dict[str, tuple[str, str]]:
    """`trialId -> (first_posted, last_update)` read from the RAW registry inputs. Empty dict on any failure —
    this is report garnish and must never break a completed run."""
    if not ids:
        return {}
    out: dict[str, tuple[str, str]] = {}
    try:
        import json

        from aus_trial_universe.core.paths import ANZCTR_ROOT, CTGOV_ROOT, current_version_dir
        ct = current_version_dir(CTGOV_ROOT) / "03_merged_ctgov_input.json"
        if any(i.upper().startswith("NCT") for i in ids) and ct.exists():
            for rec in json.loads(ct.read_text()):
                ps = rec.get("protocolSection", {}) or {}
                nct = (ps.get("identificationModule", {}) or {}).get("nctId")
                if nct in ids:
                    sm = ps.get("statusModule", {}) or {}
                    out[nct] = ((sm.get("studyFirstPostDateStruct", {}) or {}).get("date", ""),
                                (sm.get("lastUpdatePostDateStruct", {}) or {}).get("date", ""))
        anz = current_version_dir(ANZCTR_ROOT) / "extracted_trials" / "anzctr_field_extractions.csv"
        if any(not i.upper().startswith("NCT") for i in ids) and anz.exists():
            with open(anz, newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    tid = f"ACTRN{(row.get('ACTRN') or '').strip()}"
                    if tid in ids:
                        out[tid] = ((row.get("SUBMIT DATE") or "").strip(),
                                    (row.get("APPROVAL DATE") or "").strip())
    except Exception as exc:
        logger.warning("run report · registry dates unavailable: %s", exc)
    return out


def _curated_lines(ids: list[str], started: datetime) -> list[str]:
    """One bullet per newly-curated trial, labelled NEW REGISTRATION vs NEWLY MATCHING so a reviewer can see at a
    glance why it entered the universe rather than having to go and look it up."""
    if not ids:
        return ["  - _none_"]
    if len(ids) > _ID_LIST_CAP:                      # a first build: keep the flat list
        return [f"  - {_ids(ids)}"]
    dates = _registry_dates(set(ids))
    lines = []
    for tid in sorted(ids):
        first, last = dates.get(tid, ("", ""))
        note = ""
        if first:
            try:
                age = (started.date() - datetime.fromisoformat(first).date()).days
                note = (" — **new registration**" if age <= NEW_REGISTRATION_DAYS
                        else " — **newly matching** (existing trial; its record changed to match our filters)")
            except ValueError:
                note = ""
            note += f" · first posted {first}" + (f" · last update {last}" if last else "")
        lines.append(f"  - `{tid}`{note}")
    return lines


def _ids(values) -> str:
    vals = sorted(values)
    if not vals:
        return "_none_"
    shown = ", ".join(f"`{v}`" for v in vals[:_ID_LIST_CAP])
    return shown if len(vals) <= _ID_LIST_CAP else f"{shown} … and {len(vals) - _ID_LIST_CAP} more"


def _delta_table(before: dict[str, int], after: dict[str, int]) -> list[str]:
    lines = ["| table / entity | before | after | Δ |", "|---|---:|---:|---:|"]
    for key, now in after.items():
        was = before.get(key, 0)
        d = now - was
        lines.append(f"| {key} | {was:,} | {now:,} | {d:+,} |" if d else f"| {key} | {was:,} | {now:,} | — |")
    return lines


def write_report(
    *,
    before: dict[str, int],
    after: dict[str, int],
    kept: set[str],
    expiry,
    curated_ids: list[str],
    export_path: Path,
    settings: str,
    started: datetime,
    finished: datetime,
    dry_run: bool = False,
    gates=None,
    notes: list[str] | None = None,
    report_dir: Path = RUN_REPORT_DIR,
) -> Path:
    """Write one Markdown report for a completed refresh; returns its path."""
    ctgov = sorted(t for t in kept if t.upper().startswith("NCT"))
    anzctr = sorted(t for t in kept if not t.upper().startswith("NCT"))
    shape = _export_shape(export_path)
    cov = _coverage()
    # A curated trial with no export row is the gap; intersect with the kept universe to ignore expired leftovers.
    gap = sorted(set(cov["empty_trials"]) & kept)

    integrity_lines = []
    try:
        from aus_trial_universe.tasks.eligibility.qa.arm_consistency import check
        r = check()
        dangling = len(r["elig_dangling"]) + len(r["drug_dangling"]) + len(r["role_dangling"])
        verdict = "CONSISTENT" if dangling == 0 else f"**DANGLING ({dangling})**"
        integrity_lines = [
            f"- `trial_arm_id` referential integrity: **{verdict}** — registry {r['registry']:,} · "
            f"eligibility refs {r['elig_refs']:,} · drug refs {r['drug_refs']:,} · role refs {r['role_refs']:,}",
            f"- registry arms referenced by neither path: {len(r['unused']):,}",
        ]
        if dangling:
            integrity_lines.append(f"- dangling: elig {_ids(r['elig_dangling'])} · drug {_ids(r['drug_dangling'])} "
                                   f"· role {_ids(r['role_dangling'])}")
    except Exception as exc:                                   # a report must never fail a completed run
        integrity_lines = [f"- integrity check unavailable: `{exc}`"]

    mins = (finished - started).total_seconds() / 60
    # NB: `expiry.applied` is False both for a real dry-run AND when there was simply nothing to move, so the
    # dry-run note must come from the caller's intent — never inferred from the report object.
    guard = " · **EXPIRY GUARD TRIPPED (skipped)**" if getattr(expiry, "guarded", False) else ""
    if getattr(expiry, "pottr_skipped", False):
        guard += " · **POTTR list unavailable (expiry skipped)**"
    if dry_run:
        guard += " · _dry-run (stores not mutated)_"

    verdict = gates.verdict if gates is not None else "not run"
    out = [
        f"# Refresh report · {started:%Y-%m-%d %H:%M}",
        "",
        f"**GATES: {verdict}** · `{settings}` · duration **{mins:.0f} min** · finished {finished:%H:%M}",
        "",
        "## Universe (fresh download)",
        "",
        "| registry | trials |",
        "|---|---:|",
        f"| ctgov | {len(ctgov):,} |",
        f"| anzctr | {len(anzctr):,} |",
        f"| **kept total** | **{len(kept):,}** |",
        "",
        "## Churn",
        "",
        f"- **expired {len(expiry.expired):,}**{guard} → recoverable in `masters/*/expired/`",
        f"  - {_ids(expiry.expired)}",
        f"- **restored {len(expiry.restored):,}**",
        f"  - {_ids(expiry.restored)}",
        f"- **newly curated {len(curated_ids):,}** (new + restored)",
        *_curated_lines(curated_ids, started),
        "",
        "## Store deltas",
        "",
        *_delta_table(before, after),
        "",
        "## Export (Set A)",
        "",
        f"`{export_path}`" + (f" — **{shape['rows']:,} rows · {shape['trials']:,} trials · "
                              f"{shape['arms']:,} arms · {shape['columns']} cols**" if shape else " — **MISSING**"),
        "",
        "## Integrity",
        "",
        *integrity_lines,
        f"- arms with an empty interpreted DNF (contribute no export row): {cov['empty_arms']:,}",
        f"- **kept trials curated but ABSENT from the export: {len(gap):,}**",
        *([f"  - {_ids(gap)}"] if gap else []),
    ]
    if gates is not None:
        out += ["", f"## Gates — {gates.verdict}", "", "| gate | status | detail |", "|---|---|---|",
                *[f"| {g.name} | **{g.status}** | {g.detail} |" for g in gates.gates]]
    if notes:
        out += ["", "## Notes", "", *[f"- {n}" for n in notes]]

    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"refresh_{started:%Y%m%d_%H%M%S}.md"
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    logger.info("run report → %s", path)

    # STATUS.json — the machine-readable last-run status a monitor polls (overwritten every run; the per-run
    # Markdown above is the history). Written LAST so its presence implies the report exists.
    status = {
        "status": "ok" if (gates is None or gates.ok) else "fail",
        "gates_verdict": verdict,
        "started": started.isoformat(timespec="seconds"),
        "finished": finished.isoformat(timespec="seconds"),
        "duration_min": round(mins, 1),
        "settings": settings,
        "dry_run": dry_run,
        "universe": {"ctgov": len(ctgov), "anzctr": len(anzctr), "kept": len(kept)},
        "churn": {"expired": len(expiry.expired), "restored": len(expiry.restored),
                  "curated": len(curated_ids)},
        "export": {k: v for k, v in shape.items() if k != "trial_ids"},
        "curated_not_exported": len(gap),
        "gates": [{"name": g.name, "status": g.status, "detail": g.detail} for g in gates.gates]
                 if gates is not None else [],
        "notes": list(notes or []),
        "report": str(path),
    }
    (report_dir / STATUS_FILE).write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    for gone in prune_reports(report_dir):
        logger.info("pruned stale run report: %s", gone.name)
    return path


def prune_reports(report_dir: Path = RUN_REPORT_DIR, *, keep: int = ARCHIVE_KEEP) -> list[Path]:
    """Keep only the `keep` newest per-run reports; delete the rest. Same retention rule (and count) as the input
    archives, by mtime. Reports are timestamped so they ACCUMULATE rather than overwrite — this is what stops them
    growing without bound. `STATUS.json` is not a report and is never pruned: it is the single current-status file
    an external monitor polls, and is overwritten by design."""
    d = Path(report_dir)
    if keep < 1 or not d.exists():
        return []
    reports = sorted((p for p in d.glob("refresh_*.md") if p.is_file()),
                     key=lambda p: p.stat().st_mtime, reverse=True)
    removed = []
    for p in reports[keep:]:
        p.unlink(missing_ok=True)
        removed.append(p)
    return removed


def previous_kept_count(report_dir: Path = RUN_REPORT_DIR) -> int | None:
    """The previous run's kept-universe size, from STATUS.json — lets the universe-swing gate compare cycles
    without a separate state file. None on the first run or an unreadable status."""
    try:
        return int(json.loads((Path(report_dir) / STATUS_FILE).read_text())["universe"]["kept"]) or None
    except Exception:
        return None
