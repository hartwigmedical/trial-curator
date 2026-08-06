"""THREE-WAY comparison: the new principles-only run vs 4 Aug vs 28 July (user requirement, 2026-08-05).

    "They need to be meaningfully better than both, especially 28 July AND no regression wrt 28 July"

METHOD. The one objective, reproducible quality signal available without an LLM is the deterministic defect
catalogue — `expression_problems` (expression-only) + `source_problems` (mapping vs its source value). Every
version is graded with TODAY's checks, which is the fair yardstick but worth stating plainly: it measures defects
visible to the checks we have NOW, so July's count is partly higher because we can see more than we could in July.
It is not a reconstruction of what July's gates would have said.

Per value, versus each baseline:
    FIXED       baseline had >=1 error-severity defect, new has none
    REGRESSED   baseline was clean, new has >=1 error          <- must be ZERO vs 28 July
    STILL_BAD   both have errors
    CLEAN_SAME  both clean, same expression
    CLEAN_DIFF  both clean but the expression changed -> no defect signal either way, needs human eyes
Values carrying an approved ruling are reported separately, since there the register IS the answer.
"""
from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path


from aus_trial_universe.core.paths import ANALYSIS_DIR, DATA_ROOT, ELIGIBILITY_OUTPUT, current_version_dir

#: Every artifact this package writes. Under `analysis/`, which `make agentic-clean` never touches and which is
#: neither a master nor a derived output — so a review run cannot be mistaken for, or overwrite, the deliverable.
OUT = ANALYSIS_DIR / "cancer_type_stage1_review"

#: The two baselines the change must beat (user, 2026-08-05). 28 July is the OLDEST REVIEWED state and the hard
#: gate; 4 August is yesterday's, post-B1. `data/backups/` is outside DATA_ROOT by design (it survives a wipe).
JUL = DATA_ROOT.parent / "backups/pre_stage1_20260729_013636/masters/eligibility/current_version/finalised_cancer_type_map.tsv"
AUG = ELIGIBILITY_OUTPUT / "archive/20260804/finalised_cancer_type_map.tsv"
CUR = current_version_dir(ELIGIBILITY_OUTPUT) / "finalised_cancer_type_map.tsv"
NEW = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT / "remap_raw_output.tsv"


# --------------------------------------------------------------------------- #
# WHICH STAGE OWNS A RESIDUAL DEFECT (user, 2026-08-05): stage-1 sign-off is only blocked by defects STAGE 1 owns.
# Stage 1 = per-value translation decidable from the value alone. Stage 2 = anything needing a comparison ACROSS
# values, plus boolean / canonical-form manipulation. The catalogue's layer prefixes already encode most of it,
# which is why `syn_nested_not` is deliberately non-blocking at stage 1 today.
# --------------------------------------------------------------------------- #
STAGE2_DEFECTS = {
    "syn_nested_not", "syn_multi_not_clauses", "syn_redundant_outer_parens", "syn_or_order_noncanonical",
    "syn_ambiguous_precedence", "syn_unparseable", "syn_empty_not",
    "log_vacuous_exclusion", "log_or_redundant_ancestor", "log_duplicate_operand", "lex_whitespace_noise",
}


def owner(defects: list[str]) -> str:
    """`stage1` if ANY residual defect is stage-1-owned (that is what blocks sign-off), else `stage2`."""
    if not defects:
        return ""
    return "stage2" if all(d in STAGE2_DEFECTS for d in defects) else "stage1"


def _load_final(path: Path) -> dict[str, str]:
    return {r["cancer_type"]: r["oncotree_code_FINAL"]
            for r in csv.DictReader(open(path, encoding="utf-8"), delimiter="\t")}


def main() -> int:
    from aus_trial_universe.qa.adjudications import cancer_type as reg
    from aus_trial_universe.tasks.eligibility.mapping.cancer_type.expr import broadening, narrows
    from aus_trial_universe.tasks.eligibility.mapping.cancer_type.vocab import (
        expression_problems, source_problems)

    if not NEW.exists():
        print(f"missing {NEW} — run run_staged.py --full first")
        return 1
    new = {r["cancer_type"]: r["new_step1_canonical"]
           for r in csv.DictReader(open(NEW, encoding="utf-8"), delimiter="\t")}
    jul, aug, cur = _load_final(JUL), _load_final(AUG), _load_final(CUR)
    rulings = set(reg.APPROVED)

    def errs(src: str, expr: str) -> list[str]:
        return [f"{p.defect}" for p in expression_problems(expr) + source_problems(src, expr)
                if p.severity == "error"]

    # TWO DISTINCT MEASUREMENTS, and conflating them would answer the wrong question:
    #   NEW          = prompt only, register DISABLED. Measures how far the PRINCIPLES generalise, which is what
    #                  decides whether a register entry can retire.
    #   NEW+register = what would actually SHIP. The "no regression vs 28 July" gate belongs here, because a value
    #                  the prompt cannot reach but the register rules on is not a shipped regression.
    new_plus = {v: (reg.APPROVED[v].final if v in reg.APPROVED else e) for v, e in new.items()}
    versions = {"28Jul": jul, "04Aug": aug, "05Aug_current": cur, "NEW": new, "NEW+register": new_plus}
    shared = sorted(set(jul) & set(aug) & set(cur) & set(new))
    print(f"shared value set: {len(shared)}\n")

    # ---- population-level defect counts ------------------------------------
    print(f"{'version':16s} {'values w/ error':>16s} {'total errors':>13s} {'distinct exprs':>15s}")
    cache: dict[str, dict[str, list[str]]] = {}
    for name, table in versions.items():
        cache[name] = {v: errs(v, table[v]) for v in shared}
        bad = sum(1 for v in shared if cache[name][v])
        tot = sum(len(cache[name][v]) for v in shared)
        print(f"{name:16s} {bad:16d} {tot:13d} {len({table[v] for v in shared}):15d}")

    # ---- residual defects, split by which stage owns them -------------------
    print()
    for name in ("NEW", "NEW+register"):
        by = Counter(owner(cache[name][v]) for v in shared if cache[name][v])
        tot = sum(by.values())
        print(f"{name:14s} residual defective values: {tot:4d}"
              + (f"   stage1={by['stage1']}  stage2={by['stage2']}" if tot else "   (none)")
              + ("   <-- stage-1 sign-off blocker" if by["stage1"] else ""))

    # ---- per-value verdicts vs each baseline -------------------------------
    # A defect count alone CANNOT see the regression class that caused the nine B1 problems: a value that stays
    # deterministically clean while losing quality (grade collapsed, specific codes replaced by a sentinel). So a
    # clean-but-changed value is additionally classified by DIRECTION, and broadening to a sentinel — never a
    # legitimate outcome, per expr.broadening — counts as a regression in its own right.
    ORDER = ("FIXED", "REGRESSED_DEFECT", "REGRESSED_BROADENED", "STILL_BAD",
             "CLEAN_SAME", "CHANGED_NARROWER", "CHANGED_ANCESTOR", "CHANGED_LATERAL")
    REGRESSIONS = ("REGRESSED_DEFECT", "REGRESSED_BROADENED")
    rows: list[dict] = []
    verdicts: dict[str, dict[str, str]] = {}

    for cand in ("NEW", "NEW+register"):
      for base in ("28Jul", "04Aug", "05Aug_current"):
        tally: Counter = Counter()
        verdicts[base] = {}
        for v in shared:
            be, ne = cache[base][v], cache[cand][v]
            old_expr, cand_expr = versions[base][v], versions[cand][v]
            b = broadening(old_expr, cand_expr)
            if be and not ne:
                k = "FIXED"
            elif ne and not be:
                k = "REGRESSED_DEFECT"
            elif be and ne:
                k = "STILL_BAD"
            elif old_expr == cand_expr:
                k = "CLEAN_SAME"
            elif b and b[0] == "sentinel":
                k = "REGRESSED_BROADENED"
            elif b:
                k = "CHANGED_ANCESTOR"
            elif narrows(old_expr, cand_expr):
                k = "CHANGED_NARROWER"
            else:
                k = "CHANGED_LATERAL"
            tally[k] += 1
            verdicts[base][v] = k
            if cand == "NEW+register" and base in ("28Jul", "04Aug") and k not in ("CLEAN_SAME", "FIXED"):
                rows.append({"baseline": base, "verdict": k, "cancer_type": v,
                             "base_expr": old_expr, "new": cand_expr,
                             "jul_28": jul[v], "aug_04": aug[v], "current_05Aug": cur[v],
                             "base_errors": ";".join(be), "new_errors": ";".join(ne),
                             "adjudicated": "yes" if v in rulings else "",
                             "owner": owner(ne) or owner(be),
                             "direction": (b[1] if b else (narrows(old_expr, cand_expr) or ""))})
        gate = ("HARD GATE" if (base == "28Jul" and cand == "NEW+register")
                else "soft" if base == "04Aug" else "")
        print(f"\n{cand} vs {base}:" + (f"   [{gate}]" if gate else ""))
        for k in ORDER:
            if tally[k]:
                flag = ""
                if k in REGRESSIONS and base == "28Jul" and cand == "NEW+register":
                    flag = "   <-- MUST BE ZERO"
                elif k in REGRESSIONS and base == "04Aug":
                    flag = "   <-- ideally zero"
                print(f"   {k:20s} {tally[k]:5d}{flag}")
        n_reg = sum(tally[k] for k in REGRESSIONS)
        if base == "28Jul":
            print(f"   {'>> regressions':20s} {n_reg:5d}  " + ("PASS" if n_reg == 0 else "FAIL — hard gate"))
        elif base == "04Aug":
            print(f"   {'>> regressions':20s} {n_reg:5d}  " + ("PASS" if n_reg == 0 else "review required"))

    out = OUT / "three_way_review.tsv"
    if rows:
        cols = list(rows[0])
        with open(out, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, delimiter="\t", lineterminator="\n")
            w.writeheader()
            w.writerows(sorted(rows, key=lambda r: (r["verdict"] not in REGRESSIONS, r["baseline"],
                                                    r["verdict"], r["cancer_type"])))
        print(f"\n-> {out}  ({len(rows)} rows for review; regressions first)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
