"""Persistence for the shared `trial_arms` registry (spec §6.1).

The central arm table both paths reference. An accumulating store (like `DrugRefStore`): `load()` reads
`data/agentic/trial_arms/current_version/trial_arms.tsv`, callers upsert a trial's arms (re-running a trial
REPLACES its rows), then `save()` overwrites `current_version/` in place. Whichever path processes a trial
writes its arms here; the other path links to them by `trial_arm_id`.
"""
from __future__ import annotations

import csv
from dataclasses import asdict
from datetime import date
from pathlib import Path

from aus_trial_universe.agentic.core.paths import CURRENT_VERSION, TRIAL_ARMS_ROOT, current_version_dir
from aus_trial_universe.agentic.tasks.shared.schema import TABLE_FILES, TRIAL_ARMS_COLUMNS, TrialArm


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


class TrialArmStore:
    """In-memory view of the `trial_arms` table; load newest, upsert per trial, then save."""

    def __init__(self) -> None:
        self.arms: dict[str, list[TrialArm]] = {}   # trialId -> its arms (in first-seen order)

    # --- load -------------------------------------------------------------- #
    @classmethod
    def load(cls, root: Path = TRIAL_ARMS_ROOT) -> "TrialArmStore":
        store = cls()
        try:
            vdir = current_version_dir(root)
        except FileNotFoundError:
            return store  # first build — empty registry
        for row in _read_tsv(vdir / TABLE_FILES["trial_arms"]):
            a = TrialArm(**{k: row.get(k, "") for k in TRIAL_ARMS_COLUMNS})
            if a.trial_arm_id and a.trialId:
                store.arms.setdefault(a.trialId, []).append(a)
        return store

    # --- lookups ----------------------------------------------------------- #
    def has_trial(self, trial_id: str) -> bool:
        return trial_id in self.arms

    def arms_for(self, trial_id: str) -> list[TrialArm]:
        return self.arms.get(trial_id, [])

    def ids(self) -> set[str]:
        """Every trial_arm_id in the registry (the valid FK set)."""
        return {a.trial_arm_id for rows in self.arms.values() for a in rows}

    # --- upsert ------------------------------------------------------------ #
    def set_trial_arms(self, trial_id: str, arms: list[TrialArm]) -> None:
        """Replace a trial's arm rows (re-running a trial updates its arms)."""
        self.arms[trial_id] = list(arms)

    # --- save -------------------------------------------------------------- #
    def save(self, root: Path = TRIAL_ARMS_ROOT, *, on: date | None = None) -> Path:
        """Write the full registry to ``root/current_version/`` (overwrites; a `.version` file records the date)."""
        vdir = Path(root) / CURRENT_VERSION
        vdir.mkdir(parents=True, exist_ok=True)
        (vdir / ".version").write_text((on or date.today()).isoformat() + "\n", encoding="utf-8")
        _write_tsv(vdir / TABLE_FILES["trial_arms"], TRIAL_ARMS_COLUMNS,
                   [asdict(a) for rows in self.arms.values() for a in rows])
        return vdir
