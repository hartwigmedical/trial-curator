"""Persistence for the drug reference (spec §6.1).

An in-memory view of the three tables, loaded from the newest `version_<ddmmyyyy>` dir under
`data/agentic/resources/drug_ref/`. The build is **incremental**: an existing, non-stale canonical is a
pure lookup (no LLM). `save()` writes a fresh version dir holding the full current state — a self-contained,
datestamped snapshot (same-day rebuilds overwrite that day's version, matching the other resources).
"""
from __future__ import annotations

import csv
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from aus_trial_universe.agentic.core.pipeline_io import latest_version_dir
from aus_trial_universe.agentic.tasks.drug_ref.schema import (
    DRUG_ALIAS_COLUMNS,
    DRUG_INDICATION_COLUMNS,
    DRUG_REF_COLUMNS,
    DRUG_TARGET_COLUMNS,
    TABLE_FILES,
    DrugAlias,
    DrugIndication,
    DrugRef,
    DrugTarget,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
DRUG_REF_ROOT = REPO_ROOT / "data/agentic/resources/drug_ref"


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


def _parse_date(value: str) -> date | None:
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


class DrugRefStore:
    """In-memory drug reference; load the newest version, look up / upsert, then save a new version."""

    def __init__(self) -> None:
        self.aliases: dict[str, list[DrugAlias]] = {}            # raw_name -> [DrugAlias, ...] (a combination raw -> N)
        self.refs: dict[str, DrugRef] = {}                      # canonical_id -> DrugRef
        self.targets: dict[str, list[DrugTarget]] = {}         # canonical_id -> (target, action) rows
        self.indications: dict[str, list[DrugIndication]] = {}  # canonical_id -> rows

    # --- load -------------------------------------------------------------- #
    @classmethod
    def load(cls, root: Path = DRUG_REF_ROOT) -> "DrugRefStore":
        store = cls()
        try:
            vdir = latest_version_dir(root)
        except FileNotFoundError:
            return store  # first build — empty reference
        for row in _read_tsv(vdir / TABLE_FILES["drug_alias"]):
            a = DrugAlias(**{k: row.get(k, "") for k in DRUG_ALIAS_COLUMNS})
            if a.raw_name:
                store.aliases.setdefault(a.raw_name, []).append(a)
        for row in _read_tsv(vdir / TABLE_FILES["drug_ref"]):
            r = DrugRef(**{k: row.get(k, "") for k in DRUG_REF_COLUMNS})
            if r.canonical_id:
                store.refs[r.canonical_id] = r
        for row in _read_tsv(vdir / TABLE_FILES["drug_target"]):
            t = DrugTarget(**{k: row.get(k, "") for k in DRUG_TARGET_COLUMNS})
            if t.canonical_id:
                store.targets.setdefault(t.canonical_id, []).append(t)
        for row in _read_tsv(vdir / TABLE_FILES["drug_indication"]):
            i = DrugIndication(**{k: row.get(k, "") for k in DRUG_INDICATION_COLUMNS})
            if i.canonical_id:
                store.indications.setdefault(i.canonical_id, []).append(i)
        return store

    # --- lookups ----------------------------------------------------------- #
    def canonical_ids_for(self, raw_name: str) -> list[str]:
        """The canonical_id(s) a raw name resolves to — several for a combination/regimen raw, one for a single
        drug, none for a non-drug (a raw recorded with a single empty-id row)."""
        return [a.canonical_id for a in self.aliases.get(raw_name, []) if a.canonical_id]

    def has_alias(self, raw_name: str) -> bool:
        return raw_name in self.aliases

    def has_ref(self, canonical_id: str) -> bool:
        return canonical_id in self.refs

    def ref(self, canonical_id: str) -> DrugRef | None:
        return self.refs.get(canonical_id)

    def targets_for(self, canonical_id: str) -> list[DrugTarget]:
        return self.targets.get(canonical_id, [])

    def indications_for(self, canonical_id: str) -> list[DrugIndication]:
        return self.indications.get(canonical_id, [])

    def is_stale(self, canonical_id: str, max_age_days: int, *, today: date | None = None) -> bool:
        """True if the canonical is absent or its research is older than max_age_days."""
        r = self.refs.get(canonical_id)
        if r is None:
            return True
        d = _parse_date(r.researched_on)
        if d is None:
            return True
        return ((today or date.today()) - d).days > max_age_days

    # --- upserts ----------------------------------------------------------- #
    def set_alias(self, raw_name: str, canonical_ids: list[str]) -> None:
        """Map a raw name to its canonical drug(s): one row per distinct canonical_id (a combination raw ->
        several), or a single empty-id row marking the raw processed-but-not-a-drug (so it is not re-canonicalized)."""
        ids = list(dict.fromkeys(c for c in canonical_ids if c))
        self.aliases[raw_name] = ([DrugAlias(raw_name=raw_name, canonical_id=c) for c in ids]
                                  or [DrugAlias(raw_name=raw_name, canonical_id="")])

    def put_ref(self, ref: DrugRef) -> None:
        self.refs[ref.canonical_id] = ref

    def put_targets(self, canonical_id: str, rows: list[DrugTarget]) -> None:
        self.targets[canonical_id] = list(rows)

    def put_indications(self, canonical_id: str, rows: list[DrugIndication]) -> None:
        self.indications[canonical_id] = list(rows)

    # --- save -------------------------------------------------------------- #
    def save(self, root: Path = DRUG_REF_ROOT, *, on: date | None = None) -> Path:
        vdir = root / f"version_{(on or date.today()).strftime('%d%m%Y')}"
        vdir.mkdir(parents=True, exist_ok=True)
        _write_tsv(vdir / TABLE_FILES["drug_alias"], DRUG_ALIAS_COLUMNS,
                   [asdict(a) for rows in self.aliases.values() for a in rows])
        _write_tsv(vdir / TABLE_FILES["drug_ref"], DRUG_REF_COLUMNS,
                   [asdict(r) for r in self.refs.values()])
        _write_tsv(vdir / TABLE_FILES["drug_target"], DRUG_TARGET_COLUMNS,
                   [asdict(t) for rows in self.targets.values() for t in rows])
        _write_tsv(vdir / TABLE_FILES["drug_indication"], DRUG_INDICATION_COLUMNS,
                   [asdict(i) for rows in self.indications.values() for i in rows])
        return vdir
