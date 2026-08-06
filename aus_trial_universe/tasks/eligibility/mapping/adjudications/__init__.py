"""APPROVED ADJUDICATIONS — mapped values the user has ruled on by hand, one register per vocabulary column.

Sits beside `waivers.py` for the same reason: `data/` is gitignored, so a decision that lives in code is visible in
review and in `git log`. A hand-edited map table is overwritten by the next `--reconcile`; a ruling here is
reapplied on every run.

**An entry is an EXPECTATION FIRST, an override second.** The refinement runs normally; the review comparison then
reports, per adjudicated value, whether the pipeline reached the approved answer on its own:

    match     the prompts + logic produced the approved value unaided  -> the fix works
    override  they did not, so the approved value was substituted      -> the fix is INCOMPLETE

An `override` is not a success. It means the rule that should have produced it is missing or too weak, and the
correct response is to strengthen the prompt or the deterministic layer — not to accumulate overrides. The count of
overrides is therefore a quality metric for the correction itself, and an entry that flips to `match` after a
prompt fix should be RETIRED. (Handover to-do **B4** owns that work.)

LAYOUT — one module per column, named EXACTLY as the column is named everywhere else in the pipeline:

    cancer_type.py            -> oncotree_code_FINAL        applied at mapping/reconcile.py R7
    gene_alteration.py        -> finding_model_FINAL        applied at mapping/gene_alteration/reconcile.py R5
    molecular_signature.py    -> finding_model_FINAL        applied at mapping/reconcile.py R7 (empty today)

Each module exposes `APPROVED: dict[str, Adjudication]`, keyed by the INTERPRETED SOURCE VALUE — the map table's own
key — because the mapped value is what we are changing and so cannot be a stable key. Resolve a register with
`for_column(column)` rather than importing a submodule, so a caller that already carries the `column` discriminator
needs no branching. The module names are the column strings by construction, which keeps them in step without this
package importing anything from `tasks/` (that would invert the dependency and close an import cycle, since
`mapping/reconcile.py` imports this one).

TWO RULES FOR CALLERS, both learned the hard way:
  * A ruling is applied AFTER the deterministic convergence pass and is **not re-canonicalised**, so `final` must
    already be canonical and defect-free. `tests/agentic/qa/test_audit_20260805_checks.py` asserts exactly that.
  * `""` is a LEGITIMATE ruling — the faithful mapping of a criterion the target vocabulary cannot express (e.g.
    "AGA negative", where AGA is the trial's abbreviation for *actionable genomic alteration*). So test
    `is not None`, **never** truthiness.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Adjudication:
    """One approved ruling. Shared by every column — the columns differ in their vocabulary, not in what a
    ruling IS, so three near-identical dataclasses would only drift apart."""

    value: str          # the map table key: the interpreted SOURCE value
    final: str          # the approved *_FINAL ("" is meaningful — see the module docstring)
    rationale: str      # why the pipeline's answer was wrong, and why this one is right
    approved: str       # date + who


from aus_trial_universe.tasks.eligibility.mapping.adjudications import (  # noqa: E402  (needs Adjudication defined first)
    cancer_type,
    gene_alteration,
    molecular_signature,
)

#: column name -> that column's register. Keyed by the same strings the mapping stage uses.
REGISTERS: dict[str, dict[str, Adjudication]] = {
    "cancer_type": cancer_type.APPROVED,
    "gene_alteration": gene_alteration.APPROVED,
    "molecular_signature": molecular_signature.APPROVED,
}


def for_column(column: str) -> dict[str, Adjudication]:
    """The register for one vocabulary column; empty for a column with no rulings.

    Unknown columns return empty rather than raising: a caller passing a column this package has never heard of
    should get "no rulings", not a crash in the middle of a reconcile run.
    """
    return REGISTERS.get(column, {})


def all_rulings() -> dict[str, dict[str, Adjudication]]:
    """Every register, for reporting (the run report and the drift gate's exemption check)."""
    return dict(REGISTERS)
