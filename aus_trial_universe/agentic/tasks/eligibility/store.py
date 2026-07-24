"""Persistence for the eligibility relational tables (spec §6.1).

An accumulating store (mirrors `DrugRefStore`): `load()` reads the newest snapshot under
`data/agentic/eligibility/`, the orchestrator upserts a trial's arms + raw text + interpreted conjunctions
(re-running a trial REPLACES its rows) and appends new value->vocabulary mappings (the lookup-first cache),
then `save()` writes a fresh full-state snapshot into that run's dir (`current_output/`).

The value->vocab map tables (`cancer_type_map` / `gene_alteration_map` / `molecular_signature_map`) accumulate
across every run — a distinct interpreted value is mapped ONCE and reused (the map-once/reuse efficiency win).
"""
from __future__ import annotations

import csv
from dataclasses import asdict
from pathlib import Path

from aus_trial_universe.agentic.core.paths import ELIG_CURRENT_OUTPUT, ELIGIBILITY_OUTPUT, latest_snapshot_dir
from aus_trial_universe.agentic.tasks.eligibility.schema import (
    ARM_ELIGIBILITY_RAW_COLUMNS,
    CANCER_TYPE_MAP_COLUMNS,
    GENE_ALTERATION_MAP_COLUMNS,
    INTERPRETED_ELIGIBILITY_COLUMNS,
    MOLECULAR_SIGNATURE_MAP_COLUMNS,
    TABLE_FILES,
    TRIAL_ARMS_COLUMNS,
    ArmEligibilityRaw,
    CancerTypeMap,
    GeneAlterationMap,
    InterpretedEligibility,
    MolecularSignatureMap,
    TrialArm,
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
    """In-memory view of the six eligibility tables; load newest snapshot, upsert, then save a new snapshot."""

    def __init__(self) -> None:
        self.arms: dict[str, list[TrialArm]] = {}                      # trialId -> its arms
        self.raw: dict[str, list[ArmEligibilityRaw]] = {}              # trialId -> its per-arm verbatim raw text
        self.interpreted: dict[str, list[InterpretedEligibility]] = {} # trialId -> its DNF conjunctions
        self.cancer_map: dict[str, CancerTypeMap] = {}                 # cancer_type value -> mapping
        self.gene_map: dict[str, GeneAlterationMap] = {}               # gene_alteration value -> mapping
        self.signature_map: dict[str, MolecularSignatureMap] = {}      # molecular_signature value -> mapping

    # --- load -------------------------------------------------------------- #
    @classmethod
    def load(cls, root: Path = ELIGIBILITY_OUTPUT) -> "EligStore":
        store = cls()
        cur = Path(root) / ELIG_CURRENT_OUTPUT
        vdir = cur if cur.exists() else latest_snapshot_dir(root)   # prefer current_output/; else newest snapshot
        if vdir is None:
            return store  # first run — empty store
        for row in _read_tsv(vdir / TABLE_FILES["trial_arms"]):
            a = TrialArm(**{k: row.get(k, "") for k in TRIAL_ARMS_COLUMNS})
            if a.trialId:
                store.arms.setdefault(a.trialId, []).append(a)
        for row in _read_tsv(vdir / TABLE_FILES["arm_eligibility_raw"]):
            r = ArmEligibilityRaw(**{k: row.get(k, "") for k in ARM_ELIGIBILITY_RAW_COLUMNS})
            if r.trialId:
                store.raw.setdefault(r.trialId, []).append(r)
        for row in _read_tsv(vdir / TABLE_FILES["interpreted_eligibility"]):
            e = InterpretedEligibility(**{k: row.get(k, "") for k in INTERPRETED_ELIGIBILITY_COLUMNS})
            e.conjunction_index = int(e.conjunction_index or 0)
            if e.trialId:
                store.interpreted.setdefault(e.trialId, []).append(e)
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
        return trial_id in self.interpreted or trial_id in self.arms

    # --- upserts ----------------------------------------------------------- #
    def set_trial(self, trial_id: str, arms: list[TrialArm], raw: list[ArmEligibilityRaw],
                  interpreted: list[InterpretedEligibility]) -> None:
        """Replace a trial's arms + raw text + interpreted conjunctions (re-running a trial updates its rows)."""
        self.arms[trial_id] = list(arms)
        self.raw[trial_id] = list(raw)
        self.interpreted[trial_id] = list(interpreted)

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
        _write_tsv(vdir / TABLE_FILES["trial_arms"], TRIAL_ARMS_COLUMNS,
                   [asdict(a) for rows in self.arms.values() for a in rows])
        _write_tsv(vdir / TABLE_FILES["arm_eligibility_raw"], ARM_ELIGIBILITY_RAW_COLUMNS,
                   [asdict(r) for rows in self.raw.values() for r in rows])
        _write_tsv(vdir / TABLE_FILES["interpreted_eligibility"], INTERPRETED_ELIGIBILITY_COLUMNS,
                   [asdict(e) for rows in self.interpreted.values() for e in rows])
        _write_tsv(vdir / TABLE_FILES["cancer_type_map"], CANCER_TYPE_MAP_COLUMNS,
                   [asdict(m) for m in self.cancer_map.values()])
        _write_tsv(vdir / TABLE_FILES["gene_alteration_map"], GENE_ALTERATION_MAP_COLUMNS,
                   [asdict(m) for m in self.gene_map.values()])
        _write_tsv(vdir / TABLE_FILES["molecular_signature_map"], MOLECULAR_SIGNATURE_MAP_COLUMNS,
                   [asdict(m) for m in self.signature_map.values()])
        return vdir
