"""THE STAGE MAP TABLES for every vocabulary column — one table per stage, so the OUTPUTS MIRROR THE PROCESS.

    <column>_map_initial.tsv      stage 1  · the LLM mapper+reviewer's answer
    <column>_map_reconciled.tsv   stage 2  · + deterministic canonicalisation
    <column>_map_finalised.tsv    stage 3  · + approved rulings.  THIS IS WHAT SHIPS.

ONE MODULE, THREE SPECS (user, 2026-08-06: *"factor out the common components across all eligibility criteria …
the idea is not to have duplicate code"*). The layout, the accreting columns, the writers, the readers and the
legacy fallback are identical for every column; only four things differ, and they are data, not code:

    column       the key column                        cancer_type | gene_alteration | molecular_signature
    value_stem   what the mapping produces             oncotree_code | finding_model
    stages       how many stages the column has        3, or 2 where there is no stage 2
    derived      a column computed FROM the value      oncotree_name (cancer_type only)

COLUMNS ACCRETE, so each file carries the full provenance chain up to its own stage and a reader can see what
every stage did to a value without joining anything. A difference between `_reconciled` and `_finalised` IS an
approved override, visibly — which is the whole reason the two-file layout was retired: it forced every mention
of "the finalised mapping" to be qualified with "…which also includes the hand-rulings".

⚠ A DERIVED COLUMN IS NEVER AUTHORED. `oncotree_name_*` is computed here, from the code beside it, at write time.
That is what makes name/code disagreement unreachable — 46 rows once disagreed when the two were independent LLM
outputs. Editing a code by hand in one of these files leaves its name stale; re-render it, and note that
`qa/invariants.py` C5 is the backstop.

Still 3NF: every table is a single-key lookup, so they belong in the store beside the content tables rather than
in `derived/joined/`.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

#: The stage sequence a full three-stage column uses. A column with no stage 2 omits `reconciled`.
ALL_STAGES = ("initial", "reconciled", "finalised")


@dataclass(frozen=True)
class StageTables:
    """The stage-table layout for ONE vocabulary column. Pure data plus the shared behaviour."""

    column: str
    value_stem: str
    stages: tuple[str, ...] = ALL_STAGES
    #: Name of a column DERIVED from the value (rendered at write time), or "" if the column has none.
    derived_stem: str = ""
    derive: Callable[[str], str] | None = None
    #: Retired filenames, `{stage: (filename, value column)}`. Read as a fallback so a pre-restructure ARCHIVE
    #: still loads; never written. A reader left on an old name would otherwise read nothing and ship blanks.
    legacy: dict[str, tuple[str, str]] = field(default_factory=dict)

    # -- names ------------------------------------------------------------- #
    def file(self, stage: str) -> str:
        self._check(stage)
        return f"{self.column}_map_{stage}.tsv"

    def value_col(self, stage: str) -> str:
        return f"{self.value_stem}_{stage}"

    def columns(self, stage: str) -> list[str]:
        """The accreting column list for one stage: the key, then every stage up to and including this one."""
        self._check(stage)
        out = [self.column]
        for s in self.stages[: self.stages.index(stage) + 1]:
            if self.derived_stem:
                out.append(f"{self.derived_stem}_{s}")
            out.append(self.value_col(s))
        return out

    def _check(self, stage: str) -> None:
        if stage not in self.stages:
            raise KeyError(f"{self.column} has no {stage!r} stage (it has {', '.join(self.stages)})")

    # -- write --------------------------------------------------------------- #
    def _row(self, value: str, by_stage: dict[str, str], upto: str) -> dict[str, str]:
        row = {self.column: value}
        for s in self.stages[: self.stages.index(upto) + 1]:
            expr = by_stage[s][value]
            if self.derived_stem:
                row[f"{self.derived_stem}_{s}"] = self.derive(expr) if self.derive else ""
            row[self.value_col(s)] = expr
        return row

    def _write(self, path: Path, columns: list[str], rows: list[dict]) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=columns, delimiter="\t", lineterminator="\n",
                               extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        return path

    def write_all(self, store_dir: Path, **by_stage: dict[str, str]) -> dict[str, Path]:
        """Write every stage table. Pass one `{value -> expression}` mapping per stage, by keyword.

        Value ORDER is the FIRST stage's dict order, so a diff between runs stays readable.
        """
        missing = set(self.stages) - set(by_stage)
        if missing:
            raise TypeError(f"{self.column}: missing stage mapping(s) {sorted(missing)}")
        d = Path(store_dir)
        keys = list(by_stage[self.stages[0]])
        return {s: self._write(d / self.file(s), self.columns(s),
                               [self._row(v, by_stage, s) for v in keys])
                for s in self.stages}

    def write_initial(self, store_dir: Path, initial: dict[str, str]) -> Path:
        """Write ONLY the stage-1 table — used by `EligStore.save`, which persists stage 1 as trials are curated.
        Later stages are written by the reconcile pass and must not be clobbered with empties here."""
        return self._write(Path(store_dir) / self.file("initial"), self.columns("initial"),
                           [self._row(v, {"initial": initial}, "initial") for v in initial])

    # -- read ---------------------------------------------------------------- #
    def load(self, store_dir: Path, stage: str) -> dict[str, str]:
        """`{value -> expression}` for one stage, falling back to the retired flat file for old archives."""
        d = Path(store_dir)
        got = _read(d / self.file(stage), self.column, self.value_col(stage))
        if got or stage not in self.legacy:
            return got
        name, col = self.legacy[stage]
        return _read(d / name, self.column, col)


def _read(path: Path, key: str, value_col: str) -> dict[str, str]:
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as fh:
        return {r[key]: r.get(value_col, "") for r in csv.DictReader(fh, delimiter="\t") if r.get(key)}


def _oncotree_name(code: str) -> str:
    from aus_trial_universe.tasks.eligibility.mapping.cancer_type.vocab import render_name_expression
    return render_name_expression(code)


#: cancer_type — three stages, and the only column with a DERIVED name beside its code.
CANCER_TYPE = StageTables(
    column="cancer_type", value_stem="oncotree_code",
    derived_stem="oncotree_name", derive=_oncotree_name,
    legacy={"initial": ("cancer_type_map.tsv", "oncotree_code"),
            "finalised": ("finalised_cancer_type_map.tsv", "oncotree_code_FINAL")},
)

#: gene_alteration — three stages. No derived column: a finding-model expression already names its genes, so
#: there is nothing a rendered name would add.
GENE_ALTERATION = StageTables(
    column="gene_alteration", value_stem="finding_model",
    legacy={"initial": ("gene_alteration_map.tsv", "finding_model"),
            "finalised": ("finalised_gene_alteration_map.tsv", "finding_model_FINAL")},
)

#: molecular_signature — TWO stages. It has no stage 2 and provably needs none: over the live corpus the
#: canonical form changes nothing, there are no divergent groups, and stage 1 == FINAL for all 171 values.
MOLECULAR_SIGNATURE = StageTables(
    column="molecular_signature", value_stem="finding_model", stages=("initial", "finalised"),
    legacy={"initial": ("molecular_signature_map.tsv", "finding_model"),
            "finalised": ("finalised_molecular_signature_map.tsv", "finding_model_FINAL")},
)

SPECS = {s.column: s for s in (CANCER_TYPE, GENE_ALTERATION, MOLECULAR_SIGNATURE)}
