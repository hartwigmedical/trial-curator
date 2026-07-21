"""Persistence for the eligibility relational tables (spec §6.1).

An accumulating versioned store (mirrors `DrugRefStore`): `load()` reads the newest date-stamped snapshot under
`data/agentic/eligibility/`, the orchestrator upserts a trial's regimes + eligibility conjunctions (re-running a
trial REPLACES its rows) and appends new value->vocabulary mappings (the lookup-first cache), then `save()` writes
a fresh full-state snapshot into that run's timestamp dir. Date-stamped snapshots for now; the `current_version/`
+ `archive/` pattern comes at finalization (see `core/paths.latest_snapshot_dir`).

The value->vocab map tables (`cancer_type_map` / `gene_alteration_map` / `molecular_signature_map`) accumulate
across every run — a distinct extracted value is mapped ONCE and reused (the map-once/reuse efficiency win).
"""
from __future__ import annotations

import csv
from dataclasses import asdict
from pathlib import Path

from aus_trial_universe.agentic.core.paths import ELIGIBILITY_OUTPUT, latest_snapshot_dir
from aus_trial_universe.agentic.tasks.eligibility.schema import (
    CANCER_TYPE_MAP_COLUMNS,
    EXTRACTED_ELIGIBILITY_COLUMNS,
    GENE_ALTERATION_MAP_COLUMNS,
    MOLECULAR_SIGNATURE_MAP_COLUMNS,
    REGIME_COLUMNS,
    TABLE_FILES,
    CancerTypeMap,
    ExtractedEligibility,
    GeneAlterationMap,
    MolecularSignatureMap,
    Regime,
)


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


class EligStore:
    """In-memory view of the five eligibility tables; load newest snapshot, upsert, then save a new snapshot."""

    def __init__(self) -> None:
        self.regimes: dict[str, list[Regime]] = {}                     # trialId -> its arms
        self.eligibility: dict[str, list[ExtractedEligibility]] = {}   # trialId -> its DNF conjunctions
        self.cancer_map: dict[str, CancerTypeMap] = {}                 # cancer_type value -> mapping
        self.gene_map: dict[str, GeneAlterationMap] = {}               # gene_alteration value -> mapping
        self.signature_map: dict[str, MolecularSignatureMap] = {}      # molecular_signature value -> mapping

    # --- load -------------------------------------------------------------- #
    @classmethod
    def load(cls, root: Path = ELIGIBILITY_OUTPUT) -> "EligStore":
        store = cls()
        vdir = latest_snapshot_dir(root)
        if vdir is None:
            return store  # first run — empty store
        for row in _read_tsv(vdir / TABLE_FILES["regime"]):
            r = Regime(**{k: row.get(k, "") for k in REGIME_COLUMNS})
            if r.trialId:
                store.regimes.setdefault(r.trialId, []).append(r)
        for row in _read_tsv(vdir / TABLE_FILES["extracted_eligibility"]):
            e = ExtractedEligibility(**{k: row.get(k, "") for k in EXTRACTED_ELIGIBILITY_COLUMNS})
            e.conj_id = int(e.conj_id or 0)
            if e.trialId:
                store.eligibility.setdefault(e.trialId, []).append(e)
        for row in _read_tsv(vdir / TABLE_FILES["cancer_type_map"]):
            m = CancerTypeMap(**{k: row.get(k, "") for k in CANCER_TYPE_MAP_COLUMNS})
            if m.cancer_type:
                store.cancer_map[m.cancer_type] = m
        for row in _read_tsv(vdir / TABLE_FILES["gene_alteration_map"]):
            m = GeneAlterationMap(**{k: row.get(k, "") for k in GENE_ALTERATION_MAP_COLUMNS})
            if m.gene_alteration:
                store.gene_map[m.gene_alteration] = m
        for row in _read_tsv(vdir / TABLE_FILES["molecular_signature_map"]):
            m = MolecularSignatureMap(**{k: row.get(k, "") for k in MOLECULAR_SIGNATURE_MAP_COLUMNS})
            if m.molecular_signature:
                store.signature_map[m.molecular_signature] = m
        return store

    # --- lookups (the lookup-first cache) ---------------------------------- #
    def lookup_cancer_type(self, value: str) -> CancerTypeMap | None:
        return self.cancer_map.get(value)

    def lookup_gene_alteration(self, value: str) -> GeneAlterationMap | None:
        return self.gene_map.get(value)

    def lookup_molecular_signature(self, value: str) -> MolecularSignatureMap | None:
        return self.signature_map.get(value)

    def has_trial(self, trial_id: str) -> bool:
        return trial_id in self.eligibility or trial_id in self.regimes

    # --- upserts ----------------------------------------------------------- #
    def set_trial(self, trial_id: str, regimes: list[Regime], rows: list[ExtractedEligibility]) -> None:
        """Replace a trial's regimes + eligibility conjunctions (re-running a trial updates its rows in place)."""
        self.regimes[trial_id] = list(regimes)
        self.eligibility[trial_id] = list(rows)

    def put_cancer_type(self, m: CancerTypeMap) -> None:
        self.cancer_map[m.cancer_type] = m

    def put_gene_alteration(self, m: GeneAlterationMap) -> None:
        self.gene_map[m.gene_alteration] = m

    def put_molecular_signature(self, m: MolecularSignatureMap) -> None:
        self.signature_map[m.molecular_signature] = m

    # --- save -------------------------------------------------------------- #
    def save(self, run_dir: Path) -> Path:
        """Write the full current state as a snapshot into ``run_dir`` (the run's timestamp dir)."""
        vdir = Path(run_dir)
        vdir.mkdir(parents=True, exist_ok=True)
        _write_tsv(vdir / TABLE_FILES["regime"], REGIME_COLUMNS,
                   [asdict(r) for rows in self.regimes.values() for r in rows])
        _write_tsv(vdir / TABLE_FILES["extracted_eligibility"], EXTRACTED_ELIGIBILITY_COLUMNS,
                   [asdict(e) for rows in self.eligibility.values() for e in rows])
        _write_tsv(vdir / TABLE_FILES["cancer_type_map"], CANCER_TYPE_MAP_COLUMNS,
                   [asdict(m) for m in self.cancer_map.values()])
        _write_tsv(vdir / TABLE_FILES["gene_alteration_map"], GENE_ALTERATION_MAP_COLUMNS,
                   [asdict(m) for m in self.gene_map.values()])
        _write_tsv(vdir / TABLE_FILES["molecular_signature_map"], MOLECULAR_SIGNATURE_MAP_COLUMNS,
                   [asdict(m) for m in self.signature_map.values()])
        return vdir
