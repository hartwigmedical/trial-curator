"""Migrate `trial_to_intervention` to the `trial_arm_id` schema + report intervention-input additions/deletions.

Context: arm identity moved into the shared `trial_arms` registry, freshly derived for every trial by the full
eligibility run (ANZCTR arms via the shared LLM cohort module — so they may DRIFT from the frozen drug store).
This step re-keys the drug path's `trial_to_intervention` against that fresh registry:

  - a row whose (trialId, arm) -> trial_arm_id EXISTS in the registry is KEPT, re-keyed to (trial_arm_id, input);
  - a row whose arm no longer exists (drift) is a DELETION — reported, and dropped from the re-keyed table;
  - a registry arm with no intervention row is an ADDITION — reported (an arm that may need drug research later).

ONLY `trial_to_intervention.tsv` is rewritten (with --apply). The other four drug tables are LEFT UNTOUCHED — any
drug orphaned by a deletion is reconciled in a SEPARATE, user-reviewed step. Dry-run by default.

  python -m aus_trial_universe.agentic.tasks.drug_utility.migrate_trial_arms            # dry-run + report
  python -m aus_trial_universe.agentic.tasks.drug_utility.migrate_trial_arms --apply    # also rewrite the TSV
"""
from __future__ import annotations

import argparse
import csv
import logging
from dataclasses import dataclass
from pathlib import Path

from aus_trial_universe.agentic.core.paths import (
    ANALYSIS_DIR, CURRENT_VERSION, DRUG_ANNOTATIONS_ROOT, TRIAL_ARMS_ROOT,
)
from aus_trial_universe.agentic.tasks.drug_utility.schema import TRIAL_TO_INTERVENTION_COLUMNS
from aus_trial_universe.agentic.tasks.shared.cohorts import trial_arm_id, trial_id_of
from aus_trial_universe.agentic.tasks.shared.store import TrialArmStore

logger = logging.getLogger(__name__)


def _read_tsv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def _write_tsv(path: Path, columns: list[str], rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, delimiter="\t", lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


@dataclass
class MigrationReport:
    kept: list[dict]        # re-keyed rows: {trial_arm_id, input_intervention_name}
    deleted: list[dict]     # old rows whose arm no longer exists: {trialId, arm, input_intervention_name}
    added_arms: list[str]   # registry trial_arm_ids with no intervention row (may need drug research)
    old_row_count: int
    registry_arm_count: int


def build_report(t2i_path: Path, registry_ids: set[str]) -> MigrationReport:
    """Re-key the old-schema trial_to_intervention rows against the fresh registry; classify kept/deleted/added."""
    old_rows = _read_tsv(t2i_path)
    kept: list[dict] = []
    deleted: list[dict] = []
    referenced: set[str] = set()
    seen_kept: set[tuple] = set()
    for r in old_rows:
        # tolerate either schema: an already-migrated file carries trial_arm_id directly; the legacy file has
        # trialId + arm, from which the deterministic slug is recomputed.
        taid = (r.get("trial_arm_id") or "").strip() or trial_arm_id(r.get("trialId", ""), r.get("arm", ""))
        name = (r.get("input_intervention_name") or "").strip()
        if not taid or not name:
            continue
        if taid in registry_ids:
            key = (taid, name)
            if key not in seen_kept:
                seen_kept.add(key)
                kept.append({"trial_arm_id": taid, "input_intervention_name": name})
            referenced.add(taid)
        else:
            deleted.append({"trialId": r.get("trialId", "") or trial_id_of(taid),
                            "arm": r.get("arm", ""), "trial_arm_id": taid, "input_intervention_name": name})
    added_arms = sorted(registry_ids - referenced)
    return MigrationReport(kept=kept, deleted=deleted, added_arms=added_arms,
                           old_row_count=len(old_rows), registry_arm_count=len(registry_ids))


def _write_report_files(rep: MigrationReport, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_tsv(out_dir / "t2i_migration_deletions.tsv",
               ["trialId", "arm", "trial_arm_id", "input_intervention_name"], rep.deleted)
    _write_tsv(out_dir / "t2i_migration_additions.tsv", ["trial_arm_id", "trialId"],
               [{"trial_arm_id": a, "trialId": trial_id_of(a)} for a in rep.added_arms])
    return out_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Re-key trial_to_intervention to trial_arm_id + report the diff.")
    parser.add_argument("--t2i", default=None,
                        help="trial_to_intervention.tsv to migrate (default: the live drug current_version).")
    parser.add_argument("--registry", default=None,
                        help="trial_arms registry root (default: data/agentic/trial_arms).")
    parser.add_argument("--report-dir", default=None,
                        help="Where to write the deletions/additions TSVs (default: data/agentic/analysis).")
    parser.add_argument("--apply", action="store_true",
                        help="Rewrite trial_to_intervention.tsv in place (ONLY that drug file). Default: dry-run.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    t2i_path = Path(args.t2i) if args.t2i else DRUG_ANNOTATIONS_ROOT / CURRENT_VERSION / "trial_to_intervention.tsv"
    registry_root = Path(args.registry) if args.registry else TRIAL_ARMS_ROOT
    report_dir = Path(args.report_dir) if args.report_dir else ANALYSIS_DIR

    registry_ids = TrialArmStore.load(registry_root).ids()
    if not registry_ids:
        logger.info("registry at %s is EMPTY — build trial_arms first (run the eligibility extract).", registry_root)
        return 2
    rep = build_report(t2i_path, registry_ids)

    logger.info("trial_to_intervention migration · source=%s", t2i_path)
    logger.info("  old rows=%d · registry arms=%d", rep.old_row_count, rep.registry_arm_count)
    logger.info("  KEPT (re-keyed)      : %d intervention row(s)", len(rep.kept))
    logger.info("  DELETED (arm gone)   : %d intervention row(s) — %d trial(s)",
                len(rep.deleted), len({d["trialId"] for d in rep.deleted}))
    logger.info("  ADDED (new arm, no intervention yet): %d arm(s) — %d trial(s)",
                len(rep.added_arms), len({trial_id_of(a) for a in rep.added_arms}))
    out = _write_report_files(rep, report_dir)
    logger.info("  report → %s/{t2i_migration_deletions,t2i_migration_additions}.tsv", out)
    for d in rep.deleted[:15]:
        logger.info("    - DEL %s [%s] · %s", d["trialId"], d["arm"], d["input_intervention_name"])

    if args.apply:
        _write_tsv(t2i_path, TRIAL_TO_INTERVENTION_COLUMNS, rep.kept)
        logger.info("  APPLIED · rewrote %s with %d re-keyed row(s) (ONLY this drug file changed)",
                    t2i_path, len(rep.kept))
    else:
        logger.info("  DRY-RUN · no files rewritten (use --apply to rewrite trial_to_intervention.tsv)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
