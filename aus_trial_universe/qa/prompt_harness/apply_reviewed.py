"""APPLY a reviewed stage-1 artifact to the production store. Any vocabulary column.

    python -m aus_trial_universe.qa.prompt_harness.apply_reviewed --column molecular_signature [--apply]

THE RULE THIS OBEYS: apply the REVIEWED ARTIFACT; never re-derive it by re-running. The refine loop is not
reproducible from cache — a byte-identical prompt does not guarantee identical output, because one missed cache
entry mid-chain diverges every call after it (179 of 4,978 cancer_type values differed on a re-run of an unchanged
prompt). So `remap_raw_output.tsv` IS the approved stage-1 answer, and this writes exactly that.

Stages 2 and 3 ARE recomputed, and that is correct rather than inconsistent: they are pure deterministic functions
with no LLM in them, so recomputing cannot drift — and it MUST be recomputed, because register entries are
normally added AFTER the artifact is written, as the residue the prompt could not reach.

TWO GUARDS, both of which have already earned their place:
  · KEY-SET IDENTITY. The artifact was produced over the frozen store; if the key sets differ, the store moved
    underneath it and this is no longer applying what was reviewed.
  · EVERY CHANGED ROW EXPLAINED. A row whose shipped value changes must appear in the reviewed comparison as this
    run's change, or carry a register ruling. This caught a real gap on 2026-08-06: the review had been built by
    INTERSECTING version key-sets, silently dropping 4 values that postdate the oldest baseline — one of them
    changed. Nothing else noticed.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from aus_trial_universe.core.paths import ELIGIBILITY_OUTPUT, current_version_dir
from aus_trial_universe.qa import adjudications
from aus_trial_universe.qa.prompt_harness.columns import spec
from aus_trial_universe.tasks.eligibility.mapping import stage_tables as ST


def _reconcile(column: str, initial: dict[str, str]) -> dict[str, str]:
    """The column's STAGE 2. Pure and deterministic for every column that has one."""
    if column == "cancer_type":
        from aus_trial_universe.tasks.eligibility.mapping.cancer_type import stage2
        return stage2.run(initial)[0]
    if column == "gene_alteration":
        from aus_trial_universe.tasks.eligibility.mapping.gene_alteration.reconcile import run_stage2
        return {v: o.after_canon for v, o in run_stage2(initial).items()}
    # molecular_signature: the stage exists as a slot but does no work yet (user, 2026-08-06).
    return dict(initial)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--column", required=True)
    ap.add_argument("--apply", action="store_true", help="write; otherwise a dry run")
    args = ap.parse_args(argv)

    sp = spec(args.column)
    tables = ST.SPECS[sp.name]
    store = current_version_dir(ELIGIBILITY_OUTPUT)

    before_final = tables.load(store, "finalised")
    before_init = tables.load(store, "initial")
    with open(sp.out_dir / "remap_raw_output.tsv", newline="", encoding="utf-8") as fh:
        artifact = {r[sp.key]: r["new_stage1"] for r in csv.DictReader(fh, delimiter="\t")}

    missing, extra = set(before_init) - set(artifact), set(artifact) - set(before_init)
    if missing or extra:
        raise SystemExit(f"KEY SET MISMATCH — refusing. missing={len(missing)} extra={len(extra)}")

    reconciled = _reconcile(sp.name, artifact)
    rulings = {v: r.final for v, r in adjudications.for_column(sp.register).items()}
    finalised = {v: rulings.get(v, reconciled[v]) for v in artifact}

    with open(sp.out_dir / "three_way_review.tsv", newline="", encoding="utf-8") as fh:
        review = {r[sp.key]: r for r in csv.DictReader(fh, delimiter="\t")}
    changed = [v for v in artifact if finalised[v] != before_final.get(v, "")]
    unexplained = [v for v in changed
                   if review.get(v, {}).get("changed_by") != "this_run" and v not in rulings]

    print(f"column                    {sp.name}")
    print(f"values                    {len(artifact)}")
    print(f"stage-1 rows changed      {sum(1 for v in artifact if artifact[v] != before_init[v])}")
    print(f"FINAL rows changed        {len(changed)}")
    print(f"unexplained FINAL changes {len(unexplained)}")
    for v in unexplained[:10]:
        print(f"   ! {v[:100]}\n       was {before_final.get(v, '')[:110]!r}\n       now {finalised[v][:110]!r}")
    if unexplained:
        raise SystemExit("REFUSING — every changed row must be explained by the reviewed comparison.")

    if not args.apply:
        print("\nDRY RUN — pass --apply to write.")
        return 0
    tables.write_all(store, initial=artifact, reconciled=reconciled, finalised=finalised)
    print(f"\nAPPLIED — {sp.name}'s three stage tables rewritten.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
