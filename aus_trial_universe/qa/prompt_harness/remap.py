"""THE ISOLATED CANDIDATE RE-MAP — run a candidate stage-1 prompt over the frozen corpus, touching nothing.

    python -m aus_trial_universe.qa.prompt_harness.remap --column gene_alteration [--limit N] [--workers N]

WHAT "ISOLATED" MEANS HERE, precisely — these are the properties that make it safe to run against the live API
while the production pipeline stays exactly as it is:

  · the candidate prompt is MONKEYPATCHED IN MEMORY. `mapping/<column>/agents.py` is never edited, so the
    production agents keep their current fingerprint and their cached answers stay valid.
  · it READS the store's frozen stage-1 keys and WRITES only under `analysis/<column>_stage1_review/`. It never
    opens the store for writing, never touches `derived/`, and `make agentic-clean` does not reach `analysis/`.
  · it NEVER PRUNES THE CACHE. A candidate prompt has a different fingerprint, so `cache_prune` would classify
    every PRODUCTION entry for that agent as outdated and delete it — throwing away answers that cost hours and
    that we may still need to migrate. Pruning lives in `run.py` behind `--no-cache-prune`; this module simply
    never calls it. Do not add it.
  · candidate responses DO get written to the shared cache. That is deliberate and harmless: they are keyed by the
    candidate fingerprint, so they cannot collide with production, and they make a re-run of the same candidate
    free.

⚠ THE RESULT IS NOT REPRODUCIBLE BY RE-RUNNING. A byte-identical prompt does not guarantee identical output — one
missed cache entry mid-chain diverges every call after it (179 of 4,978 cancer_type values differed on a re-run of
an unchanged prompt, 2026-08-06). So the artifact this writes is the thing to review AND the thing to migrate;
never re-derive it later and assume it matches.
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
from pathlib import Path

from aus_trial_universe.core.client import DiskCache, LlmClient
from aus_trial_universe.core.paths import CACHE_DIR, ELIGIBILITY_OUTPUT, current_version_dir
from aus_trial_universe.qa.prompt_harness.columns import spec

logger = logging.getLogger("prompt_harness.remap")


def _frozen_values(sp) -> list[str]:
    """The DISTINCT stage-1 keys currently in the store — the corpus the candidate must answer."""
    path = current_version_dir(ELIGIBILITY_OUTPUT) / sp.table
    with open(path, newline="", encoding="utf-8") as fh:
        return [r[sp.key] for r in csv.DictReader(fh, delimiter="\t")]


def _install_candidate(column: str) -> list[str]:
    """Monkeypatch a candidate prompt in memory, if one is staged for this column.

    A column with no staged candidate re-maps with its PRODUCTION prompt, which is what you want for a fresh
    measurement. All three columns' candidates were folded into production on 2026-08-06, so nothing is staged.

    To stage the next one: write a module that expresses the change as ANCHORED EDITS to the production prompt —
    `(anchor, replacement, why)` tuples, asserting each anchor appears EXACTLY ONCE — then import it here and
    assign onto the agents module. The anchored form is what makes the diff reviewable and makes a candidate fail
    loud instead of silently no-opping when production moves underneath it.

    Never edit `agents.py` to test a candidate — that changes the production fingerprint and orphans every cached
    answer behind it.
    """
    return []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--column", default="gene_alteration")
    ap.add_argument("--limit", type=int, default=0, help="map only the first N values (a smoke run)")
    ap.add_argument("--workers", type=int, default=60)
    ap.add_argument("--max-concurrency", type=int, default=300)
    ap.add_argument("--max-attempts", type=int, default=3)
    ap.add_argument("--out", default="", help="override the output TSV path")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    sp = spec(args.column)
    sp.out_dir.mkdir(parents=True, exist_ok=True)

    values = _frozen_values(sp)
    if args.limit:
        values = values[:args.limit]
    swapped = _install_candidate(args.column)
    logger.info("remap · column=%s · %d value(s) · candidate prompts swapped: %s",
                sp.name, len(values), ", ".join(swapped) or "NONE (running the production prompt)")

    # Deliberately NOT pruned — see the module docstring.
    client = LlmClient(cache=DiskCache(CACHE_DIR), max_concurrency=args.max_concurrency)

    from aus_trial_universe.tasks.eligibility.mapping.gene_alteration.reconcile import run_stage2
    from aus_trial_universe.tasks.eligibility.mapping.workflow import (map_gene_alterations,
                                                                       map_molecular_signatures)

    mapper = {"gene_alteration": map_gene_alterations,
              "molecular_signature": map_molecular_signatures}[sp.name]
    results = mapper(client, values, max_attempts=args.max_attempts, use_reviewer=True, workers=args.workers)
    stage1 = {v: (results[v].finding_model if v in results else "") for v in values}
    # molecular_signature's stage 2 is a PASS-THROUGH (the slot exists but does no work yet), so its `new_stage2`
    # is its stage 1. gene_alteration runs the real canonical-form pass.
    outcomes = run_stage2(stage1) if sp.name == "gene_alteration" else None

    dest = Path(args.out) if args.out else sp.out_dir / "remap_raw_output.tsv"
    cols = [sp.key, "new_stage1", "new_stage2", "faithful", "attempts", "problems"]
    with open(dest, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, delimiter="\t", lineterminator="\n")
        w.writeheader()
        for v in values:
            r = results.get(v)
            w.writerow({
                sp.key: v,
                "new_stage1": stage1[v],
                # stage 2 with the REGISTER DISABLED — the review needs to see how far the PROMPT gets on its own,
                # because that is what decides whether a ruling can retire. `run_stage2` applies rulings at R5, so
                # take `after_canon`, which is the canonical form before any override.
                "new_stage2": outcomes[v].after_canon if outcomes else stage1[v],
                "faithful": "yes" if (r and r.faithful) else "no",
                "attempts": r.attempts if r else 0,
                "problems": "; ".join(r.problems)[:400] if r else "",
            })
    unfaithful = sum(1 for v in values if not (results.get(v) and results[v].faithful))
    logger.info("-> %s\n   %d value(s); %d did not satisfy the reviewer", dest, len(values), unfaithful)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
