"""Persistence for the eligibility relational tables (spec §6.1).

An accumulating store (mirrors `DrugRefStore`): `load()` reads the newest snapshot under
`data/agentic/eligibility/`, the orchestrator upserts a trial's per-arm raw text + interpreted conjunctions
(re-running a trial REPLACES its rows) and appends new value->vocabulary mappings (the lookup-first cache),
then `save()` writes a fresh full-state snapshot into that run's dir (`current_output/`).

The arm spine (`trial_arms`) is the SHARED central table (`tasks/shared`); the two content tables here link to
it by `trial_arm_id` (a deterministic (trialId, arm) slug). Rows are grouped internally by trialId — derived
from the slug — so a trial's rows are replaced atomically on re-run.

The value->vocab map tables (`cancer_type_map` / `gene_alteration_map` / `molecular_signature_map`) accumulate
across every run — a distinct interpreted value is mapped ONCE and reused (the map-once/reuse efficiency win).
"""
from __future__ import annotations

import csv
from dataclasses import asdict
from pathlib import Path

from aus_trial_universe.core.paths import CURRENT_VERSION, ELIGIBILITY_OUTPUT, latest_snapshot_dir
from aus_trial_universe.tasks.eligibility.schema import (
    ARM_ELIGIBILITY_RAW_COLUMNS,
    ARM_SCOPE_COLUMNS,
    CANCER_TYPE_MAP_COLUMNS,
    GENE_ALTERATION_MAP_COLUMNS,
    INTERPRETED_ELIGIBILITY_COLUMNS,
    MOLECULAR_SIGNATURE_MAP_COLUMNS,
    TABLE_FILES,
    ArmEligibilityRaw,
    ArmScope,
    CancerTypeMap,
    GeneAlterationMap,
    InterpretedEligibility,
    MolecularSignatureMap,
)
from aus_trial_universe.tasks.shared.cohorts import trial_id_of


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
    """In-memory view of the eligibility tables; load newest snapshot, upsert, then save a new snapshot."""

    def __init__(self) -> None:
        self.raw: dict[str, list[ArmEligibilityRaw]] = {}              # trialId -> its per-arm verbatim raw text
        self.interpreted: dict[str, list[InterpretedEligibility]] = {} # trialId -> its DNF conjunctions
        self.cancer_map: dict[str, CancerTypeMap] = {}                 # cancer_type value -> mapping
        self.gene_map: dict[str, GeneAlterationMap] = {}               # gene_alteration value -> mapping
        self.signature_map: dict[str, MolecularSignatureMap] = {}      # molecular_signature value -> mapping
        self.scope: dict[str, ArmScope] = {}                           # trial_arm_id -> why it has NO interpreted rows

    # --- load -------------------------------------------------------------- #
    @classmethod
    def load(cls, root: Path = ELIGIBILITY_OUTPUT) -> "EligStore":
        store = cls()
        cur = Path(root) / CURRENT_VERSION
        vdir = cur if cur.exists() else latest_snapshot_dir(root)   # prefer current_version/; else newest snapshot
        if vdir is None:
            return store  # first run — empty store
        for row in _read_tsv(vdir / TABLE_FILES["arm_eligibility_raw"]):
            r = ArmEligibilityRaw(**{k: row.get(k, "") for k in ARM_ELIGIBILITY_RAW_COLUMNS})
            if r.trial_arm_id:
                store.raw.setdefault(trial_id_of(r.trial_arm_id), []).append(r)
        for row in _read_tsv(vdir / TABLE_FILES["interpreted_eligibility"]):
            e = InterpretedEligibility(**{k: row.get(k, "") for k in INTERPRETED_ELIGIBILITY_COLUMNS})
            e.conjunction_index = int(e.conjunction_index or 0)
            if e.trial_arm_id:
                store.interpreted.setdefault(trial_id_of(e.trial_arm_id), []).append(e)
        # STAGE 1 of OncoTree mapping now lives in `cancer_type_map_initial.tsv` with stage-suffixed columns, so
        # the store's files mirror the three-stage process (user, 2026-08-06). The in-memory `CancerTypeMap` keeps
        # its plain field names — only the on-disk column names carry the stage — so nothing downstream had to move.
        # The legacy `cancer_type_map.tsv` is still read as a fallback, which is what lets an ARCHIVED snapshot
        # written before the restructure still load.
        # Every column loads its STAGE-1 table through the one shared spec, which also carries the retired
        # filename as a read-only fallback — that is what lets a pre-restructure ARCHIVE still load.
        from aus_trial_universe.tasks.eligibility.mapping import stage_tables as ST
        for value, code in ST.CANCER_TYPE.load(vdir, "initial").items():
            store.cancer_map[value] = CancerTypeMap(cancer_type=value, oncotree_code=code)
        for value, fm in ST.GENE_ALTERATION.load(vdir, "initial").items():
            store.gene_map[value] = GeneAlterationMap(gene_alteration=value, finding_model=fm)
        for value, fm in ST.MOLECULAR_SIGNATURE.load(vdir, "initial").items():
            store.signature_map[value] = MolecularSignatureMap(molecular_signature=value, finding_model=fm)
        for row in _read_tsv(vdir / TABLE_FILES["arm_scope"]):
            s = ArmScope(**{k: row.get(k, "") for k in ARM_SCOPE_COLUMNS})
            if s.trial_arm_id:
                store.scope[s.trial_arm_id] = s
        return store

    @classmethod
    def load_dir(cls, vdir: Path) -> "EligStore":
        """Load an eligibility store from an EXPLICIT dir holding the content tables (used for the expired/ area,
        which stashes only arm_eligibility_raw + interpreted_eligibility — no value->vocab maps)."""
        store = cls()
        vdir = Path(vdir)
        for row in _read_tsv(vdir / TABLE_FILES["arm_eligibility_raw"]):
            r = ArmEligibilityRaw(**{k: row.get(k, "") for k in ARM_ELIGIBILITY_RAW_COLUMNS})
            if r.trial_arm_id:
                store.raw.setdefault(trial_id_of(r.trial_arm_id), []).append(r)
        for row in _read_tsv(vdir / TABLE_FILES["interpreted_eligibility"]):
            e = InterpretedEligibility(**{k: row.get(k, "") for k in INTERPRETED_ELIGIBILITY_COLUMNS})
            e.conjunction_index = int(e.conjunction_index or 0)
            if e.trial_arm_id:
                store.interpreted.setdefault(trial_id_of(e.trial_arm_id), []).append(e)
        return store

    def save_content(self, vdir: Path) -> Path:
        """Write ONLY the two content tables (raw + interpreted) to ``vdir`` — no maps. Used for the expired/ area."""
        vdir = Path(vdir)
        vdir.mkdir(parents=True, exist_ok=True)
        _write_tsv(vdir / TABLE_FILES["arm_eligibility_raw"], ARM_ELIGIBILITY_RAW_COLUMNS,
                   [asdict(r) for rows in self.raw.values() for r in rows])
        _write_tsv(vdir / TABLE_FILES["interpreted_eligibility"], INTERPRETED_ELIGIBILITY_COLUMNS,
                   [asdict(e) for rows in self.interpreted.values() for e in rows])
        return vdir

    # --- lookups (the lookup-first cache) ---------------------------------- #
    def lookup_cancer_type(self, value: str) -> CancerTypeMap | None:
        return self.cancer_map.get(value)

    def lookup_gene_alteration(self, value: str) -> GeneAlterationMap | None:
        return self.gene_map.get(value)

    def lookup_molecular_signature(self, value: str) -> MolecularSignatureMap | None:
        return self.signature_map.get(value)

    def has_trial(self, trial_id: str) -> bool:
        return trial_id in self.interpreted or trial_id in self.raw

    # --- upserts ----------------------------------------------------------- #
    def set_trial(self, trial_id: str, raw: list[ArmEligibilityRaw],
                  interpreted: list[InterpretedEligibility]) -> None:
        """Replace a trial's per-arm raw text + interpreted conjunctions (re-running a trial updates its rows)."""
        self.raw[trial_id] = list(raw)
        self.interpreted[trial_id] = list(interpreted)

    def pop_trial(self, trial_id: str) -> tuple[list[ArmEligibilityRaw], list[InterpretedEligibility]]:
        """Remove and RETURN a trial's content rows (raw + interpreted). Used by expiry to move a trial out of the
        live store into the recoverable expired/ area. The shared value->vocab maps are left untouched."""
        return self.raw.pop(trial_id, []), self.interpreted.pop(trial_id, [])

    def put_cancer_type(self, m: CancerTypeMap) -> None:
        self.cancer_map[m.cancer_type] = m

    def put_gene_alteration(self, m: GeneAlterationMap) -> None:
        self.gene_map[m.gene_alteration] = m

    def put_molecular_signature(self, m: MolecularSignatureMap) -> None:
        self.signature_map[m.molecular_signature] = m

    def put_scope(self, s: ArmScope) -> None:
        self.scope[s.trial_arm_id] = s

    def empty_arms(self, arm_ids: set[str]) -> list[str]:
        """Of `arm_ids` (the registry's arms), those with NO interpreted conjunctions — the arms that contribute
        no export row and therefore need a scope verdict."""
        with_rows = {e.trial_arm_id for rows in self.interpreted.values() for e in rows}
        return sorted(a for a in arm_ids if a not in with_rows)

    # --- save -------------------------------------------------------------- #
    def _write_maps(self, vdir: Path) -> None:
        """Write the 3 value->vocab map tables (ONLY when populated — an extract-only run leaves just the 2 core
        tables rather than creating empty map placeholders)."""
        # Every column writes its STAGE-1 table through the one shared spec, so the stage-suffixed columns (and
        # cancer_type's derived `oncotree_name_initial`) have exactly one definition. Stages 2 and 3 are written
        # later by the reconcile pass and must not be clobbered with empties here.
        from aus_trial_universe.tasks.eligibility.mapping import stage_tables as ST
        for spec, mapping in (
            (ST.CANCER_TYPE, {v: m.oncotree_code for v, m in self.cancer_map.items()}),
            (ST.GENE_ALTERATION, {v: m.finding_model for v, m in self.gene_map.items()}),
            (ST.MOLECULAR_SIGNATURE, {v: m.finding_model for v, m in self.signature_map.items()}),
        ):
            if mapping:
                spec.write_initial(vdir, mapping)

    def save(self, run_dir: Path) -> Path:
        """Write the full current state as a snapshot into ``run_dir`` (the run's timestamp dir)."""
        vdir = Path(run_dir)
        vdir.mkdir(parents=True, exist_ok=True)
        _write_tsv(vdir / TABLE_FILES["arm_eligibility_raw"], ARM_ELIGIBILITY_RAW_COLUMNS,
                   [asdict(r) for rows in self.raw.values() for r in rows])
        _write_tsv(vdir / TABLE_FILES["interpreted_eligibility"], INTERPRETED_ELIGIBILITY_COLUMNS,
                   [asdict(e) for rows in self.interpreted.values() for e in rows])
        self._write_maps(vdir)
        return vdir

    def save_scope(self, run_dir: Path) -> Path:
        """Write ONLY the arm_scope table — the content tables and the maps are NOT touched (same additive
        discipline as `save_maps`: the frozen source of truth is never re-persisted)."""
        vdir = Path(run_dir)
        vdir.mkdir(parents=True, exist_ok=True)
        _write_tsv(vdir / TABLE_FILES["arm_scope"], ARM_SCOPE_COLUMNS,
                   [asdict(s) for s in self.scope.values()])
        return vdir

    def save_maps(self, run_dir: Path) -> Path:
        """Write ONLY the 3 value->vocab map tables into ``run_dir`` — the two content tables
        (arm_eligibility_raw / interpreted_eligibility) are NOT written/touched. Used by the map-only pass so the
        frozen source of truth is never re-persisted."""
        vdir = Path(run_dir)
        vdir.mkdir(parents=True, exist_ok=True)
        self._write_maps(vdir)
        return vdir

    def save_mapped_eligibility(self, run_dir: Path) -> Path:
        """Write the denormalized per-row MAPPED view: a copy of interpreted_eligibility with each cell's vocabulary
        mapping joined in (by the provenance-stripped value, the same key the map tables use). 1:1 with
        interpreted_eligibility; does NOT touch the content tables. This is the mapping Step-1 flat output."""
        from aus_trial_universe.tasks.eligibility.schema import MAPPED_ELIGIBILITY_COLUMNS
        from aus_trial_universe.tasks.eligibility.mapping.workflow import strip_provenance
        vdir = Path(run_dir)
        vdir.mkdir(parents=True, exist_ok=True)
        out: list[dict] = []
        for rows in self.interpreted.values():
            for e in rows:
                ct = self.cancer_map.get(strip_provenance(e.cancer_type_interpreted))
                ga = self.gene_map.get(strip_provenance(e.gene_alteration_interpreted))
                sig = self.signature_map.get(strip_provenance(e.molecular_signature_interpreted))
                out.append({
                    "trial_arm_id": e.trial_arm_id,
                    "conjunction_index": e.conjunction_index,
                    "cancer_type_interpreted": e.cancer_type_interpreted,
                    "oncotree_name": ct.oncotree_name if ct else "",
                    "oncotree_code": ct.oncotree_code if ct else "",
                    "gene_alteration_interpreted": e.gene_alteration_interpreted,
                    "gene_alteration_findingmodel": ga.finding_model if ga else "",
                    "molecular_signature_interpreted": e.molecular_signature_interpreted,
                    "molecular_signature_findingmodel": sig.finding_model if sig else "",
                    "molecular_biomarker_interpreted": e.molecular_biomarker_interpreted,
                    "prior_therapy_interpreted": e.prior_therapy_interpreted,
                })
        _write_tsv(vdir / TABLE_FILES["mapped_eligibility"], MAPPED_ELIGIBILITY_COLUMNS, out)
        return vdir
