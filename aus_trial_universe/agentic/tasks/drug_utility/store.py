"""Persistence for the drug reference (spec §6.1).

An in-memory view of the five tables, loaded from `current_version/` under
`data/agentic/drug_annotations/` (`paths.DRUG_ANNOTATIONS_ROOT`). The build is **incremental**: an existing,
non-stale canonical is a pure lookup (no LLM). `save()` writes the full current state to `current_version/`
(overwriting), recording the build date in a `.version` file; a superseded set is moved to `archive/<label>/`
via `paths.archive_current_version` — matching the other versioned resources.

Table 1 is split (3NF): `intervention_to_canonical` (input string -> canonical drug(s), deduped by string) and
`trial_to_intervention` (which trials used each input string — the provenance/traceability record). A legacy
`drug_alias.tsv` (raw_name -> canonical_id) still loads for backward compatibility with pre-split version dirs.
"""
from __future__ import annotations

import csv
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from aus_trial_universe.agentic.core.paths import CURRENT_VERSION, DRUG_ANNOTATIONS_ROOT, current_version_dir
from aus_trial_universe.agentic.tasks.shared.cohorts import trial_id_of
from aus_trial_universe.agentic.tasks.drug_utility.schema import (
    APPROVAL_BIOMARKER_MAP_COLUMNS,
    APPROVAL_CANCER_TYPE_MAP_COLUMNS,
    DRUG_REGULATORY_APPROVALS_COLUMNS,
    DRUG_ANNOTATIONS_CORE_COLUMNS,
    DRUG_TARGET_ACTIONS_COLUMNS,
    INTERVENTION_TO_CANONICAL_COLUMNS,
    TABLE_FILES,
    TRIAL_ARM_DRUG_ROLE_COLUMNS,
    TRIAL_TO_INTERVENTION_COLUMNS,
    ApprovalBiomarkerMap,
    ApprovalCancerTypeMap,
    DrugRegulatoryApproval,
    DrugAnnotationsCore,
    DrugTargetAction,
    InterventionToCanonical,
    TrialArmDrugRole,
    TrialToIntervention,
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


def _parse_date(value: str) -> date | None:
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


class DrugRefStore:
    """In-memory drug reference; load the newest version, look up / upsert, then save a new version."""

    def __init__(self) -> None:
        # input_intervention_name -> [InterventionToCanonical, ...] (a combination input -> N; deduped by string)
        self.mappings: dict[str, list[InterventionToCanonical]] = {}
        # (trial_arm_id, input_intervention_name) -> row (deduped provenance / traceability; links to trial_arms)
        self.occurrences: dict[tuple[str, str], TrialToIntervention] = {}
        self.refs: dict[str, DrugAnnotationsCore] = {}                      # canonical_id -> DrugAnnotationsCore
        self.targets: dict[str, list[DrugTargetAction]] = {}         # canonical_id -> (target, action) rows
        self.indications: dict[str, list[DrugRegulatoryApproval]] = {}  # canonical_id -> rows
        self.roles: dict[str, list[TrialArmDrugRole]] = {}          # trial_arm_id -> (canonical_id, role) rows
        # symmetric-match vocab for the approval free-text (value -> mapping; single-key 3NF lookups)
        self.approval_cancer_type_map: dict[str, ApprovalCancerTypeMap] = {}   # cancer_type -> OncoTree
        self.approval_biomarker_map: dict[str, ApprovalBiomarkerMap] = {}      # biomarker -> split + finding-model

    # --- load -------------------------------------------------------------- #
    @classmethod
    def load(cls, root: Path = DRUG_ANNOTATIONS_ROOT) -> "DrugRefStore":
        store = cls()
        try:
            vdir = current_version_dir(root)
        except FileNotFoundError:
            return store  # first build — empty reference
        for row in _read_tsv(vdir / TABLE_FILES["intervention_to_canonical"]):
            m = InterventionToCanonical(**{k: row.get(k, "") for k in INTERVENTION_TO_CANONICAL_COLUMNS})
            if m.input_intervention_name:
                store.mappings.setdefault(m.input_intervention_name, []).append(m)
        if not store.mappings:  # backward compat: pre-split version dir with a legacy drug_alias.tsv
            for row in _read_tsv(vdir / "drug_alias.tsv"):
                name, cid = (row.get("raw_name") or "").strip(), (row.get("canonical_id") or "").strip()
                if name:
                    store.mappings.setdefault(name, []).append(
                        InterventionToCanonical(input_intervention_name=name, raw_name_to_map=name, canonical_id=cid))
        for row in _read_tsv(vdir / TABLE_FILES["trial_to_intervention"]):
            o = TrialToIntervention(**{k: row.get(k, "") for k in TRIAL_TO_INTERVENTION_COLUMNS})
            if o.trial_arm_id and o.input_intervention_name:
                store.occurrences[(o.trial_arm_id, o.input_intervention_name)] = o
        for row in _read_tsv(vdir / TABLE_FILES["drug_annotations_core"]):
            r = DrugAnnotationsCore(**{k: row.get(k, "") for k in DRUG_ANNOTATIONS_CORE_COLUMNS})
            if r.canonical_id:
                store.refs[r.canonical_id] = r
        for row in _read_tsv(vdir / TABLE_FILES["drug_target_actions"]):
            t = DrugTargetAction(**{k: row.get(k, "") for k in DRUG_TARGET_ACTIONS_COLUMNS})
            if t.canonical_id:
                store.targets.setdefault(t.canonical_id, []).append(t)
        for row in _read_tsv(vdir / TABLE_FILES["drug_regulatory_approvals"]):
            i = DrugRegulatoryApproval(**{k: row.get(k, "") for k in DRUG_REGULATORY_APPROVALS_COLUMNS})
            if i.canonical_id:
                store.indications.setdefault(i.canonical_id, []).append(i)
        for row in _read_tsv(vdir / TABLE_FILES["trial_arm_drug_role"]):
            r = TrialArmDrugRole(**{k: row.get(k, "") for k in TRIAL_ARM_DRUG_ROLE_COLUMNS})
            if r.trial_arm_id and r.canonical_id:
                store.roles.setdefault(r.trial_arm_id, []).append(r)
        for row in _read_tsv(vdir / TABLE_FILES["approval_cancer_type_map"]):  # absent on pre-symmetric-match dirs
            m = ApprovalCancerTypeMap(**{k: row.get(k, "") for k in APPROVAL_CANCER_TYPE_MAP_COLUMNS})
            if m.cancer_type:
                store.approval_cancer_type_map[m.cancer_type] = m
        for row in _read_tsv(vdir / TABLE_FILES["approval_biomarker_map"]):
            m = ApprovalBiomarkerMap(**{k: row.get(k, "") for k in APPROVAL_BIOMARKER_MAP_COLUMNS})
            if m.biomarker:
                store.approval_biomarker_map[m.biomarker] = m
        return store

    # --- lookups ----------------------------------------------------------- #
    def canonical_ids_for(self, input_name: str) -> list[str]:
        """The canonical_id(s) an input intervention name resolves to — several for a combination/regimen input,
        one for a single drug, none for a non-drug (an input recorded with a single empty-id row)."""
        return [m.canonical_id for m in self.mappings.get(input_name, []) if m.canonical_id]

    def has_mapping(self, input_name: str) -> bool:
        return input_name in self.mappings

    def has_ref(self, canonical_id: str) -> bool:
        return canonical_id in self.refs

    def ref(self, canonical_id: str) -> DrugAnnotationsCore | None:
        return self.refs.get(canonical_id)

    def targets_for(self, canonical_id: str) -> list[DrugTargetAction]:
        return self.targets.get(canonical_id, [])

    def indications_for(self, canonical_id: str) -> list[DrugRegulatoryApproval]:
        return self.indications.get(canonical_id, [])

    def roles_for(self, trial_arm_id: str) -> list[TrialArmDrugRole]:
        """The (canonical_id -> role) rows for a trial arm (Phase 2 main/auxiliary)."""
        return self.roles.get(trial_arm_id, [])

    def trial_arm_ids(self) -> set[str]:
        """Every trial_arm_id referenced by `trial_to_intervention` (its FKs into the shared trial_arms registry)."""
        return {taid for (taid, _name) in self.occurrences}

    def role_trial_arm_ids(self) -> set[str]:
        """Every trial_arm_id referenced by `trial_arm_drug_role` (its FK into the shared trial_arms registry)."""
        return {taid for taid in self.roles}

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
    def set_mapping(self, input_name: str, pairs: list[tuple[str, str]]) -> None:
        """Map an input intervention name to its canonical drug(s). `pairs` is (raw_name_to_map, canonical_id)
        per component: one row per distinct canonical_id (a combination input -> several), or a single empty-id
        row marking the input processed-but-not-a-drug (so it is not re-canonicalized). For a single-drug or
        non-drug input the fragment defaults to the whole input; for a combination component an empty fragment is
        kept empty (an undecomposable regimen acronym)."""
        multi = len({c for _, c in pairs if c}) > 1
        rows, seen = [], set()
        for frag, cid in pairs:
            if cid and cid not in seen:
                seen.add(cid)
                rmap = frag.strip() if frag and frag.strip() else ("" if multi else input_name)
                rows.append(InterventionToCanonical(input_intervention_name=input_name,
                                                    raw_name_to_map=rmap, canonical_id=cid))
        self.mappings[input_name] = rows or [
            InterventionToCanonical(input_intervention_name=input_name, raw_name_to_map=input_name, canonical_id="")]

    def add_occurrence(self, trial_arm_id: str, input_name: str) -> None:
        """Record that a trial ARM used an input intervention name (deduped provenance / traceability). Links to the
        shared trial_arms registry by `trial_arm_id`."""
        if trial_arm_id and input_name:
            self.occurrences[(trial_arm_id, input_name)] = TrialToIntervention(
                trial_arm_id=trial_arm_id, input_intervention_name=input_name)

    def remove_trial_occurrences(self, trial_id: str) -> int:
        """Drop ALL trial_to_intervention rows for a trial (every arm) — overwrite semantics before re-deriving its
        arms (so a re-build cannot leave a trial with both its old and new arm rows). Returns the count removed.
        Drug facts (canonical/annotation tables) are keyed by drug, shared across trials, and left untouched."""
        keys = [k for k in self.occurrences if trial_id_of(k[0]) == trial_id]
        for k in keys:
            del self.occurrences[k]
        return len(keys)

    def remove_trial_roles(self, trial_id: str) -> int:
        """Drop ALL trial_arm_drug_role rows for a trial (every arm) — overwrite semantics before re-deriving its
        arms (mirrors `remove_trial_occurrences`, so a re-build cannot leave stale roles). Returns the count of
        (arm, drug) role rows removed. Drug facts (canonical/annotation tables) are left untouched."""
        taids = [taid for taid in self.roles if trial_id_of(taid) == trial_id]
        removed = sum(len(self.roles[taid]) for taid in taids)
        for taid in taids:
            del self.roles[taid]
        return removed

    def put_ref(self, ref: DrugAnnotationsCore) -> None:
        self.refs[ref.canonical_id] = ref

    def put_targets(self, canonical_id: str, rows: list[DrugTargetAction]) -> None:
        self.targets[canonical_id] = list(rows)

    def put_indications(self, canonical_id: str, rows: list[DrugRegulatoryApproval]) -> None:
        self.indications[canonical_id] = list(rows)

    def put_roles(self, trial_arm_id: str, rows: list[TrialArmDrugRole]) -> None:
        """Set an arm's (canonical_id -> role) rows (Phase 2). Empty list removes the arm's roles."""
        if rows:
            self.roles[trial_arm_id] = list(rows)
        else:
            self.roles.pop(trial_arm_id, None)

    def put_approval_cancer_type(self, m: ApprovalCancerTypeMap) -> None:
        """Upsert one approval cancer_type -> OncoTree mapping (keyed by the free-text value)."""
        if m.cancer_type:
            self.approval_cancer_type_map[m.cancer_type] = m

    def put_approval_biomarker(self, m: ApprovalBiomarkerMap) -> None:
        """Upsert one approval biomarker -> split + finding-model mapping (keyed by the free-text value)."""
        if m.biomarker:
            self.approval_biomarker_map[m.biomarker] = m

    # --- save -------------------------------------------------------------- #
    def save(self, root: Path = DRUG_ANNOTATIONS_ROOT, *, on: date | None = None) -> Path:
        """Write the full current state to ``root/current_version/`` (overwrites — incremental checkpoints re-save
        the same dir). A `.version` file records the build date; archiving a superseded set is a separate step
        (`paths.archive_current_version`), so per-batch checkpoints do not spawn archive entries."""
        vdir = root / CURRENT_VERSION
        vdir.mkdir(parents=True, exist_ok=True)
        (vdir / ".version").write_text((on or date.today()).isoformat() + "\n", encoding="utf-8")
        _write_tsv(vdir / TABLE_FILES["intervention_to_canonical"], INTERVENTION_TO_CANONICAL_COLUMNS,
                   [asdict(m) for rows in self.mappings.values() for m in rows])
        _write_tsv(vdir / TABLE_FILES["trial_to_intervention"], TRIAL_TO_INTERVENTION_COLUMNS,
                   [asdict(o) for o in self.occurrences.values()])
        _write_tsv(vdir / TABLE_FILES["drug_annotations_core"], DRUG_ANNOTATIONS_CORE_COLUMNS,
                   [asdict(r) for r in self.refs.values()])
        _write_tsv(vdir / TABLE_FILES["drug_target_actions"], DRUG_TARGET_ACTIONS_COLUMNS,
                   [asdict(t) for rows in self.targets.values() for t in rows])
        _write_tsv(vdir / TABLE_FILES["drug_regulatory_approvals"], DRUG_REGULATORY_APPROVALS_COLUMNS,
                   [asdict(i) for rows in self.indications.values() for i in rows])
        _write_tsv(vdir / TABLE_FILES["trial_arm_drug_role"], TRIAL_ARM_DRUG_ROLE_COLUMNS,
                   [asdict(r) for rows in self.roles.values() for r in rows])
        # symmetric-match vocab maps — written only once populated, so a plain drug build never emits empty files
        if self.approval_cancer_type_map:
            _write_tsv(vdir / TABLE_FILES["approval_cancer_type_map"], APPROVAL_CANCER_TYPE_MAP_COLUMNS,
                       [asdict(m) for m in self.approval_cancer_type_map.values()])
        if self.approval_biomarker_map:
            _write_tsv(vdir / TABLE_FILES["approval_biomarker_map"], APPROVAL_BIOMARKER_MAP_COLUMNS,
                       [asdict(m) for m in self.approval_biomarker_map.values()])
        return vdir

    def save_approval_maps(self, root: Path = DRUG_ANNOTATIONS_ROOT) -> Path:
        """Write ONLY the two symmetric-match vocab tables into ``root/current_version/`` — the 5 core drug tables +
        `trial_arm_drug_role` are left byte-untouched (additive; mirrors the eligibility store's `save_maps`). Used by
        the map-approvals build so it never rewrites the reviewed drug facts."""
        vdir = root / CURRENT_VERSION
        vdir.mkdir(parents=True, exist_ok=True)
        _write_tsv(vdir / TABLE_FILES["approval_cancer_type_map"], APPROVAL_CANCER_TYPE_MAP_COLUMNS,
                   [asdict(m) for m in self.approval_cancer_type_map.values()])
        _write_tsv(vdir / TABLE_FILES["approval_biomarker_map"], APPROVAL_BIOMARKER_MAP_COLUMNS,
                   [asdict(m) for m in self.approval_biomarker_map.values()])
        return vdir
