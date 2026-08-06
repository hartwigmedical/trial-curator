"""THE GATE + THE REVIEW DELIVERABLE, column-generic (added 2026-08-06 for gene_alteration).

    python -m aus_trial_universe.qa.prompt_harness.compare --column gene_alteration

Supersedes the pair `compare_runs.py` / `export_comparison.py` for new work. Those two are kept because they carry
the OncoTree review's 1,015 lines of recorded per-value hand judgements, which are column-specific data rather than
machinery; nothing here needs to re-derive them.

WHAT IT MEASURES, and the two measurements it refuses to conflate:

    NEW            the candidate prompt alone, register DISABLED. Answers "how far do the PRINCIPLES generalise?",
                   which is what decides whether a register entry can RETIRE.
    NEW+register   what would actually ship. The no-regression gate belongs here, because a value the prompt
                   cannot reach but the register rules on is not a shipped regression.

Per value, against each baseline:

    FIXED            baseline carried an error-severity defect, the candidate does not
    REGRESSED_DEFECT baseline was clean, the candidate carries a defect          <- must be ZERO vs the hard gate
    REGRESSED_*      baseline was clean, candidate is clean, but the change moved in a DANGEROUS direction
    STILL_BAD        both carry defects
    CLEAN_SAME       identical
    CHANGED_*        clean both sides, changed in a benign or neutral direction

THE DIRECTION BUCKETS ARE THE POINT. A defect count cannot see the worst regression class — a value that stays
deterministically clean while losing quality. For gene_alteration the dangerous directions are a BROADENED
EXCLUSION (an over-exclusion: the mapping now rejects patients the trial would accept) and a NARROWED INCLUSION
(patients lost). Broadening an inclusion is explicitly acceptable under the mapping rules, so it is not a
regression. See `columns.py` for the argument.
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

from aus_trial_universe.qa.prompt_harness.columns import spec

#: Buckets that block sign-off against the hard-gate baseline. Only the UNAMBIGUOUS ones: a change whose
#: correctness depends on the source belongs in REVIEW_REQUIRED, because a gate that fires on correct changes
#: trains the reviewer to ignore it.
REGRESSIONS = ("REGRESSED_DEFECT", "REGRESSED_BROADENED_EXCLUSION", "REGRESSED_NARROWED_INCLUSION",
               "REGRESSED_BROADENED")
#: Surfaced prominently and judged per value; never auto-failed.
REVIEW_REQUIRED = ("CHANGED_TO_EMPTY", "LOST_EXCLUSION")
ORDER = ("FIXED",) + REGRESSIONS + ("STILL_BAD", "CLEAN_SAME") + REVIEW_REQUIRED + (
    "GAINED_EXCLUSION", "CHANGED_FROM_EMPTY", "CHANGED_NARROWER", "CHANGED_ANCESTOR", "CHANGED_LATERAL",
    "PRIOR_APPROVED")


def _load(path: Path, key: str, col: str) -> dict[str, str]:
    with open(path, newline="", encoding="utf-8") as fh:
        return {r[key]: r[col] for r in csv.DictReader(fh, delimiter="\t")}


def _rulings(module: str) -> dict[str, str]:
    from aus_trial_universe.qa import adjudications
    return {v: r.final for v, r in adjudications.for_column(module).items()}


def classify(sp, before: str, after: str, errs_before: list[str], errs_after: list[str],
             prior_approved: str | None = None) -> str:
    """Classify one value against one baseline.

    `prior_approved` is the value the LAST APPROVED state ships. When the candidate reproduces it exactly, the
    difference from an OLDER baseline is not this run's change — it is a change that was already reviewed and
    signed off — so it is labelled `PRIOR_APPROVED` and excluded from the gate.

    Without this, the hard gate is unusable on its second outing: grading gene_alteration against the pre-B1b
    baseline counted all 9 of B1b's approved "activating EGFR mutation -> the classical sensitising set"
    expansions as regressions of a run that merely reproduced them. The cancer_type harness had the same label
    (`SAME_AS_04AUG`) for the same reason. The older baseline still earns its place — it is the only thing that
    can catch a defect the approved change INTRODUCED — but only where the candidate and the approved state
    actually differ.
    """
    if errs_before and not errs_after:
        return "FIXED"
    if errs_after and not errs_before:
        return "REGRESSED_DEFECT"
    if errs_before and errs_after:
        return "STILL_BAD"
    if before == after:
        return "CLEAN_SAME"
    if prior_approved is not None and after == prior_approved:
        return "PRIOR_APPROVED"
    return sp.direction(before, after)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--column", default="gene_alteration")
    ap.add_argument("--remap", default="", help="the remap output TSV (default: <out_dir>/remap_raw_output.tsv)")
    args = ap.parse_args(argv)

    sp = spec(args.column)
    src = Path(args.remap) if args.remap else sp.out_dir / "remap_raw_output.tsv"
    if not src.exists():
        print(f"missing {src} — run `python -m aus_trial_universe.qa.prompt_harness.remap --column {sp.name}` first")
        return 1

    new = _load(src, sp.key, "new_stage2")
    faithful = _load(src, sp.key, "faithful")
    rulings = _rulings(sp.register)
    new_plus = {v: rulings.get(v, e) for v, e in new.items()}
    # The live store uses the stage-table column name; older backups predate the rename.
    bases = {label: _load(p, sp.key, sp.final_col if label == 'live' else sp.final_col_baseline)
             for label, p in sp.baselines.items()}
    hard = next(iter(sp.baselines))

    shared = sorted(set(new) & set.intersection(*(set(b) for b in bases.values())))
    #: The last APPROVED state — the newest baseline. Used to tell "this run changed it" from "a previously
    #: signed-off change did", which is what keeps an older hard-gate baseline usable.
    approved = bases[list(sp.baselines)[-1]]
    versions = {**bases, "NEW": new, "NEW+register": new_plus}
    print(f"column: {sp.name}   shared value set: {len(shared)}   hard-gate baseline: {hard}\n")

    cache = {n: {v: sp.errs(v, t.get(v, "")) for v in shared} for n, t in versions.items()}
    print(f"{'version':16s} {'values w/ error':>16s} {'total errors':>13s} {'distinct exprs':>15s}")
    for n, t in versions.items():
        bad = sum(1 for v in shared if cache[n][v])
        tot = sum(len(cache[n][v]) for v in shared)
        print(f"{n:16s} {bad:16d} {tot:13d} {len({t[v] for v in shared}):15d}")

    rows: list[dict] = []
    gate_ok = True
    for cand in ("NEW", "NEW+register"):
        for base in bases:
            tally: Counter = Counter()
            for v in shared:
                k = classify(sp, versions[base][v], versions[cand][v], cache[base][v], cache[cand][v],
                             prior_approved=approved.get(v) if base != list(sp.baselines)[-1] else None)
                tally[k] += 1
                if cand == "NEW+register" and k != "CLEAN_SAME":
                    rows.append({
                        sp.key: v, "baseline": base, "verdict": k,
                        "base_expr": versions[base][v], "new": new[v], "new_shipped": versions[cand][v],
                        "base_errors": ";".join(cache[base][v]), "new_errors": ";".join(cache[cand][v]),
                        "reviewer_ok": faithful.get(v, ""),
                        "adjudicated": "yes" if v in rulings else "",
                        "ruling_status": ("retirable" if new.get(v) == rulings.get(v) else "still_needed")
                                         if v in rulings else "",
                    })
            n_reg = sum(tally[k] for k in REGRESSIONS)
            tag = "  [HARD GATE]" if (base == hard and cand == "NEW+register") else ""
            print(f"\n{cand} vs {base}:{tag}")
            for k in ORDER:
                if tally[k]:
                    flag = ""
                    if k in REGRESSIONS and base == hard and cand == "NEW+register":
                        flag = "   <-- MUST BE ZERO"
                    elif k in REVIEW_REQUIRED and cand == "NEW+register":
                        flag = "   <-- read every one of these"
                    print(f"   {k:32s} {tally[k]:5d}{flag}")
            if base == hard and cand == "NEW+register":
                gate_ok = n_reg == 0
                print(f"   {'>> regressions':32s} {n_reg:5d}  " + ("PASS" if gate_ok else "FAIL — hard gate"))

    dest = sp.out_dir / "three_way_review.tsv"
    sp.out_dir.mkdir(parents=True, exist_ok=True)
    if rows:
        with open(dest, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]), delimiter="\t", lineterminator="\n")
            w.writeheader()
            w.writerows(sorted(rows, key=lambda r: (r["verdict"] not in REGRESSIONS, r["baseline"],
                                                    r["verdict"], r[sp.key])))
        print(f"\n-> {dest}   ({len(rows)} rows for review; regressions first)")
    return 0 if gate_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
