"""THE REVIEW DELIVERABLE — one row per value, the runs side by side. Any vocabulary column.

    python -m aus_trial_universe.qa.prompt_harness.export_review --column molecular_signature

Column set specified by the user (2026-08-06): *"the input text, 28 july mapping, current mapping, your verdict —
treat any difference due to the programmatic manipulation in stage 2 as identical still, if it is an improvement,
explain why in a separate col, same for regressions."*

    gene_alteration      the interpreted source value — the map key, and what every mapping must be faithful to
    mapping_28Jul        FINAL mapping as at 2026-07-28 (== the 4 Aug pre-B1b archive; byte-identical)
    mapping_current      FINAL mapping as it ships TODAY
    mapping_new          what WOULD ship = the candidate prompt's answer with the register applied
    my_verdict           IDENTICAL / IMPROVEMENT / REGRESSION / NEUTRAL / UNCERTAIN
    improvement_reason   why it is better  (IMPROVEMENT / NEUTRAL)
    regression_reason    why it is worse, or the unresolved tension  (REGRESSION / UNCERTAIN)
    changed_by           this_run | prior_approved | ''  — the one extra column, and it earns its place: without
                         it there is no way to tell THIS run's changes from the ones where the candidate merely
                         reproduces what B1b already shipped, and only the former are being approved here

TWO LAYERS, deliberately separated (see `gene_judgements.py` for why the first version got this wrong):
  · DETERMINISTIC — which values differ at all, and which population they belong to. Decided by canonicalising
    both expressions and comparing parsed ASTs, so stage-2 algebra (De Morgan, ordering, dedup, absorption) is
    exactly what the user asked it to be: not a difference.
  · LLM — the quality verdict and its reason, one call per genuinely-changed value. No string matching, no
    pattern heuristics: the question is semantic, so it is answered semantically.
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys

from aus_trial_universe.core.client import DiskCache, LlmClient
from aus_trial_universe.core.paths import CACHE_DIR
from aus_trial_universe.core.workflow import fan_out
from aus_trial_universe.tasks.eligibility.mapping import adjudications
from aus_trial_universe.qa.prompt_harness import review_judge as J
from aus_trial_universe.qa.prompt_harness.columns import spec

logger = logging.getLogger("prompt_harness.export")

BASE_COLUMNS = ["mapping_28Jul", "mapping_current", "mapping_new", "my_verdict",
           "improvement_reason", "regression_reason", "changed_by", "ruling_status"]


def _load(path, key: str, col: str) -> dict[str, str]:
    with open(path, newline="", encoding="utf-8") as fh:
        return {r[key]: r[col] for r in csv.DictReader(fh, delimiter="\t")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--column", default="gene_alteration")
    ap.add_argument("--workers", type=int, default=40)
    ap.add_argument("--max-concurrency", type=int, default=200)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    SP = spec(args.column)
    KEY = SP.key
    COLUMNS = [KEY] + BASE_COLUMNS

    first = list(SP.baselines)[0]
    jul = _load(SP.baselines[first], KEY, SP.final_col_baseline)
    cur = _load(SP.baselines["live"], KEY, SP.final_col)
    raw = _load(SP.out_dir / "remap_raw_output.tsv", KEY, "new_stage2")
    rulings = {v: r.final for v, r in adjudications.for_column(SP.register).items()}
    new = {v: rulings.get(v, e) for v, e in raw.items()}

    # THE UNIVERSE IS THE CURRENT CORPUS, not the intersection of the three versions. Intersecting silently
    # dropped the 4 values that postdate the 28 July baseline — one of which this run changes — so the review
    # covered 909 of 913 and the migration guard was the only thing that noticed. A value with no July mapping is
    # still perfectly reviewable: it is judged against CURRENT, which is the comparison that matters anyway.
    values = sorted(set(cur) & set(new))
    NOT_IN_JUL = "(did not exist on 28 July)"

    # ---- deterministic: what actually differs, and who changed it -------------------------------------------
    population: dict[str, str] = {}
    for v in values:
        # A value absent from July counts as 'same as July' for population purposes: there is no older
        # mapping for it to differ from, so only the comparison against CURRENT can classify it.
        same_jul = J.equivalent(jul[v], new[v], SP.name) if v in jul else True
        same_cur = J.equivalent(cur[v], new[v], SP.name)
        population[v] = "" if (same_jul and same_cur) else ("prior_approved" if same_cur else "this_run")
    todo = [v for v in values if population[v]]
    logger.info("review · %d value(s) total · %d need a verdict (%d this run, %d previously approved)",
                len(values), len(todo),
                sum(1 for v in todo if population[v] == "this_run"),
                sum(1 for v in todo if population[v] == "prior_approved"))

    # ---- LLM: the quality verdict, one call per changed value -----------------------------------------------
    client = LlmClient(cache=DiskCache(CACHE_DIR), max_concurrency=args.max_concurrency)   # never prunes
    judge = J.build_mapping_judge(client, SP.name)

    def _one(v: str):
        try:
            return judge(J.judge_input(v, jul.get(v, NOT_IN_JUL), cur[v], new[v]))
        except Exception as exc:                      # noqa: BLE001 — isolate one item, keep the batch
            logger.warning("judge failed for %r: %s", v[:70], exc)
            return None

    verdicts = fan_out([(lambda v=v: _one(v)) for v in todo], max_workers=args.workers)
    got = dict(zip(todo, verdicts))

    rows, tally = [], {}
    for v in values:
        pop = population[v]
        if not pop:
            verdict, imp, reg = "IDENTICAL", "", ""
        else:
            r = got.get(v)
            if r is None or (r.verdict or "").strip().upper() not in J.VERDICTS:
                verdict, imp, reg = "UNCERTAIN", "", "the review judge did not return a usable verdict"
            else:
                verdict = r.verdict.strip().upper()
                imp, reg = (r.improvement_reason or "").strip(), (r.regression_reason or "").strip()
        tally[verdict] = tally.get(verdict, 0) + 1
        rows.append({KEY: v, "mapping_28Jul": jul.get(v, NOT_IN_JUL),
                     "mapping_current": cur[v],
                     "mapping_new": new[v], "my_verdict": verdict, "improvement_reason": imp,
                     "regression_reason": reg, "changed_by": pop,
                     "ruling_status": ("retirable" if raw.get(v) == rulings.get(v) else "still_needed")
                                      if v in rulings else ""})

    # This run's own changes first — they are what is being approved — then B1b's, then the unchanged remainder.
    order = {"this_run": 0, "prior_approved": 1, "": 2}
    rank = {k: i for i, k in enumerate(("REGRESSION", "UNCERTAIN", "NEUTRAL", "IMPROVEMENT", "IDENTICAL"))}
    rows.sort(key=lambda r: (order[r["changed_by"]], rank.get(r["my_verdict"], 9), r[KEY]))

    dest = SP.out_dir / "three_way_review.tsv"
    with open(dest, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, delimiter="\t", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)

    print(f"\n-> {dest}\n   {len(rows)} values\n")
    print(f"   {'verdict':12s} {'all':>6s} {'this run':>10s} {'prior approved':>16s}")
    for k in J.VERDICTS:
        if tally.get(k):
            a = sum(1 for r in rows if r["my_verdict"] == k and r["changed_by"] == "this_run")
            b = sum(1 for r in rows if r["my_verdict"] == k and r["changed_by"] == "prior_approved")
            print(f"   {k:12s} {tally[k]:6d} {a:10d} {b:16d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
