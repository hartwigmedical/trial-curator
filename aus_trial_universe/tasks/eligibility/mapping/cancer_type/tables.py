"""The three OncoTree map tables — one per stage, so the OUTPUTS MIRROR THE PROCESS (user, 2026-08-06).

    cancer_type_map_initial.tsv      stage 1  · the LLM mapper+reviewer's answer
    cancer_type_map_reconciled.tsv   stage 2  · + deterministic canonicalisation
    cancer_type_map_finalised.tsv    stage 3  · + approved rulings.  THIS IS WHAT SHIPS.

COLUMNS ACCRETE, so each file carries the full provenance chain up to its own stage and a reader can see what every
stage did to a value without joining anything:

    initial      cancer_type · oncotree_name_initial · oncotree_code_initial
    reconciled   …           · + oncotree_name_reconciled · oncotree_code_reconciled
    finalised    …           · + oncotree_name_finalised  · oncotree_code_finalised

This replaces `cancer_type_map.tsv` + `finalised_cancer_type_map.tsv`, whose two-file shape encoded a two-stage
model and forced every discussion of "the finalised mapping" to be qualified with "…which also includes the
hand-rulings applied at R7". A difference between `_reconciled` and `_finalised` now IS the override, visibly.

Still 3NF: every table is a single-key lookup on `cancer_type`, so they belong in the store next to the content
tables rather than in `derived/joined/`.

⚠ EVERY `oncotree_name_*` IS DERIVED from the code beside it, via `vocab.render_name_expression`. A name is never
authored, here or by an LLM — that is what makes name/code disagreement unreachable (46 rows once disagreed when the
two were independent LLM outputs). Editing a code in one of these files by hand leaves its name stale; re-render it,
and note that `qa/invariants.py` C5 is the backstop.

gene_alteration and molecular_signature deliberately keep their existing two-file layout: neither has a three-stage
pipeline, and the user scoped this change to OncoTree only (2026-08-06). Revisit if either grows a stage 2.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, fields
from pathlib import Path

INITIAL_FILE = "cancer_type_map_initial.tsv"
RECONCILED_FILE = "cancer_type_map_reconciled.tsv"
FINALISED_FILE = "cancer_type_map_finalised.tsv"


def _derive(code: str) -> str:
    from aus_trial_universe.tasks.eligibility.mapping.cancer_type.vocab import render_name_expression
    return render_name_expression(code)


@dataclass
class InitialRow:
    """Stage 1's answer for one distinct cancer_type value."""

    cancer_type: str = ""
    oncotree_name_initial: str = ""     # DERIVED — never author this
    oncotree_code_initial: str = ""

    def __post_init__(self) -> None:
        self.oncotree_name_initial = _derive(self.oncotree_code_initial)


@dataclass
class ReconciledRow(InitialRow):
    """Stage 2's output: stage 1's columns plus the canonicalised form."""

    oncotree_name_reconciled: str = ""  # DERIVED
    oncotree_code_reconciled: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        self.oncotree_name_reconciled = _derive(self.oncotree_code_reconciled)


@dataclass
class FinalisedRow(ReconciledRow):
    """Stage 3's output, and the row that ships. `_finalised` differs from `_reconciled` iff a ruling fired."""

    oncotree_name_finalised: str = ""   # DERIVED
    oncotree_code_finalised: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        self.oncotree_name_finalised = _derive(self.oncotree_code_finalised)


def _columns(dc) -> list[str]:
    return [f.name for f in fields(dc)]


INITIAL_COLUMNS = _columns(InitialRow)
RECONCILED_COLUMNS = _columns(ReconciledRow)
FINALISED_COLUMNS = _columns(FinalisedRow)


def _write(path: Path, columns: list[str], rows: list) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, delimiter="\t", lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows([{c: getattr(r, c) for c in columns} for r in rows])
    return path


def write_all(store_dir: Path, initial: dict[str, str], reconciled: dict[str, str],
              finalised: dict[str, str]) -> dict[str, Path]:
    """Write all three tables. Value ORDER is the stage-1 dict's, so a diff between runs stays readable."""
    d = Path(store_dir)
    keys = list(initial)
    return {
        "initial": _write(d / INITIAL_FILE, INITIAL_COLUMNS,
                          [InitialRow(cancer_type=v, oncotree_code_initial=initial[v]) for v in keys]),
        "reconciled": _write(d / RECONCILED_FILE, RECONCILED_COLUMNS,
                             [ReconciledRow(cancer_type=v, oncotree_code_initial=initial[v],
                                            oncotree_code_reconciled=reconciled[v]) for v in keys]),
        "finalised": _write(d / FINALISED_FILE, FINALISED_COLUMNS,
                            [FinalisedRow(cancer_type=v, oncotree_code_initial=initial[v],
                                          oncotree_code_reconciled=reconciled[v],
                                          oncotree_code_finalised=finalised[v]) for v in keys]),
    }


def write_initial(store_dir: Path, initial: dict[str, str]) -> Path:
    """Write ONLY the stage-1 table. Used by `EligStore.save`, which persists stage 1 as trials are curated; stages
    2 and 3 are written later by the reconcile pass, so they must not be clobbered with empties here."""
    return _write(Path(store_dir) / INITIAL_FILE, INITIAL_COLUMNS,
                  [InitialRow(cancer_type=v, oncotree_code_initial=c) for v, c in initial.items()])


def load_finalised(store_dir: Path) -> dict[str, str]:
    """`{cancer_type -> oncotree_code_finalised}` — the shipping mapping, for the export and the gates."""
    p = Path(store_dir) / FINALISED_FILE
    if not p.exists():
        return {}
    with open(p, newline="", encoding="utf-8") as fh:
        return {r["cancer_type"]: r["oncotree_code_finalised"] for r in csv.DictReader(fh, delimiter="\t")}
