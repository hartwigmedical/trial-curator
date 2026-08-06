"""THE REVIEW DELIVERABLE — one row per cancer_type value, the runs side by side (column set agreed 2026-08-05).

    python -m aus_trial_universe.qa.prompt_harness.export_comparison [<remap output tsv>]

COLUMNS
    cancer_type        the interpreted source value — the map key, and the thing every mapping must be faithful to
    code_28Jul         FINAL mapping as at 2026-07-28  — the OLDEST REVIEWED state, and the hard-gate baseline
    code_04Aug         FINAL mapping as at 2026-08-04  — post-B1
    code_new           the candidate prompt's answer, adjudication register DISABLED
    code_new_shipped   what would actually ship = `code_new` with the register applied
    verdict_vs_28Jul   MY judgement against 28 July: IDENTICAL / IMPROVEMENT / REGRESSION / NEUTRAL / UNCERTAIN
    fix_stage_vs_28Jul for a REGRESSION only — `stage2` if stage 2 can fix it, `stage1` if it is genuinely stage 1's
    verdict_vs_04Aug   the same judgement against 4 August
    fix_stage_vs_04Aug the same fix-ownership call on that axis
    my_reason          why — a verdict with no stated reason cannot be reviewed. Regressions append `FIX: …`
    ruling_status      retirable / still_needed, for values carrying a hand-ruling

The two verdict columns can legitimately differ, and that is the point: a value 28 July mapped correctly, B1 broke
on 4 August, and this run reproduces is a REGRESSION vs 28 July but IDENTICAL vs 4 August.

Intermediate Step-1 columns are deliberately absent: only each run's FINAL mapping matters for review. The current
5 Aug live output is omitted too, because it differs from 4 Aug on just 33 of 4,971 values — all of them the
adjudicated ones, which are already visible by comparing the two `code_new*` columns.

`my_verdict` is IDENTICAL only where the shipped mapping equals BOTH baselines. Everything else takes its verdict
from `judgements.py`, and a changed value with no recorded judgement shows as UNREVIEWED so review gaps are loud.
"""
from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path

from aus_trial_universe.core.paths import ANALYSIS_DIR, DATA_ROOT, ELIGIBILITY_OUTPUT, current_version_dir

OUT = ANALYSIS_DIR / "cancer_type_stage1_review"
JUL = DATA_ROOT.parent / "backups/pre_stage1_20260729_013636/masters/eligibility/current_version/finalised_cancer_type_map.tsv"
AUG = ELIGIBILITY_OUTPUT / "archive/20260804/finalised_cancer_type_map.tsv"
CUR = current_version_dir(ELIGIBILITY_OUTPUT) / "finalised_cancer_type_map.tsv"

COLUMNS = ["cancer_type", "code_28Jul", "code_04Aug", "code_new", "code_new_shipped",
           "verdict_vs_28Jul", "fix_stage_vs_28Jul", "verdict_vs_04Aug", "fix_stage_vs_04Aug",
           "my_reason", "ruling_status"]


def _final(path: Path) -> dict[str, str]:
    return {r["cancer_type"]: r["oncotree_code_FINAL"]
            for r in csv.DictReader(open(path, encoding="utf-8"), delimiter="\t")}


def main() -> int:
    from aus_trial_universe.qa.adjudications import cancer_type as reg
    from aus_trial_universe.qa.prompt_harness import judgements

    src = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT / "remap_raw_output.tsv"
    if not src.exists():
        print(f"missing {src} — run `python -m aus_trial_universe.qa.prompt_harness.remap --full` first")
        return 1
    new = {r["cancer_type"]: r["new_step1_canonical"]
           for r in csv.DictReader(open(src, encoding="utf-8"), delimiter="\t")}
    jul, aug, live = _final(JUL), _final(AUG), _final(CUR)
    # Every (old -> new) transition this run produced, so a regression can be tested for a disagreeing twin: if the
    # corpus also maps some equivalent value new -> old, the two disagree and stage 2's reconciliation owns it.
    seen_transitions = {(live.get(k, aug.get(k, "")), e) for k, e in new.items()}
    R = reg.APPROVED

    dest = OUT / "three_way_review.tsv"
    tally: Counter = Counter()
    tally_j: Counter = Counter()
    stages: Counter = Counter()
    with open(dest, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, delimiter="\t", lineterminator="\n")
        w.writeheader()
        for v in sorted(new):
            shipped = R[v].final if v in R else new[v]
            j, a = jul.get(v, ""), aug.get(v, "")
            # TWO verdicts, one per baseline (user, 2026-08-05). They can legitimately differ, and that is the
            # point: a value 28 July got right, B1 broke on 4 August, and this run reproduces is REGRESSION vs
            # 28 July but IDENTICAL vs 4 August. Collapsing them would hide precisely the case the older baseline
            # exists to catch.
            base_a = live.get(v, a)          # last approved state (5 Aug live == 4 Aug except the ruled values)
            # A value shipping its OWN adjudicated answer cannot be a regression: the register IS the approved
            # verdict for it. This has to be checked before any recorded judgement, because a judgement made before
            # the ruling existed was made against a different shipped value and is stale the moment it lands.
            ruled = v in R and shipped == R[v].final
            if judgements.equivalent(base_a, shipped):
                v_aug, reason = "IDENTICAL", ""
            elif ruled:
                v_aug, reason = "IMPROVEMENT", "ships the adjudicated answer from qa/adjudications/cancer_type.py"
            else:
                v_aug, reason = judgements.for_value_or_transition(v, base_a, shipped)
            if not j:
                v_jul = ""                    # the value did not exist on 28 July
            elif shipped == j:
                v_jul = "IDENTICAL"
            else:
                v_jul, r_jul = judgements.for_value_or_transition(v, j, shipped)
                hand = next(((vv, rr) for pre, (vv, rr) in judgements.VS_JUL.items() if v.startswith(pre)), None)
                # ⚠ ALGEBRAIC EQUIVALENCE IS CHECKED FIRST, and it has to be. A hand judgement recorded against an
                # earlier run persists by value prefix, so once a later run FIXES that value the stale verdict would
                # still fire — and because the fix often arrives in canonical form (`NOT(BREAST OR PROSTATE)` where
                # 28 July had `NOT(PROSTATE) AND NOT(BREAST)`), the plain string comparison above does not catch it.
                # That mislabelled two already-correct values as regressions and would have put them in the
                # adjudication register.
                if judgements.equivalent(j, shipped):
                    v_jul, reason = "IDENTICAL", ""
                elif ruled:
                    v_jul = "IMPROVEMENT"
                    reason = reason or "ships the adjudicated answer from qa/adjudications/cancer_type.py"
                elif hand is not None:
                    # the values where 28 July excluded a DESCENDANT of the new positive term, judged one by one
                    v_jul, reason = hand
                elif v_jul == "UNREVIEWED" and not judgements.substantive_lost_exclusions(j, shipped):
                    # 28 July differed only in FORM — unfactored `NOT(A) AND NOT(B)`, or exclusions that were
                    # vacuous anyway. Two expressions that differ only by algebra a stage-2 rewrite performs are the
                    # same mapping (user, 2026-08-05), so the substantive change is the one judged against 4 August.
                    v_jul = v_aug
                    if not reason:
                        reason = judgements.for_value_or_transition(v, base_a, shipped)[1]
                elif v_jul == "UNREVIEWED" and shipped == base_a:
                    # differs from 28 July ONLY because of B1's 4 August change, which was reviewed and approved
                    # then. Labelled rather than silently judged — it is not this run's change.
                    v_jul = "SAME_AS_04AUG"
                elif v_jul != "UNREVIEWED" and not reason:
                    reason = r_jul
            fs_jul = fs_aug = ""
            why_fix = ""
            if "REGRESSION" in (v_jul, v_aug):
                rev = (shipped, base_a) in seen_transitions or (shipped, j) in seen_transitions
                stage, why_fix = judgements.fix_stage(v, base_a, shipped, rev)
                fs_jul = stage if v_jul == "REGRESSION" else ""
                fs_aug = stage if v_aug == "REGRESSION" else ""
            if why_fix:
                reason = f"{reason} | FIX: {why_fix}" if reason else f"FIX: {why_fix}"
            status = ""
            if v in R:
                status = "retirable" if new[v] == R[v].final else "still_needed"
            if fs_jul or fs_aug:
                stages[fs_jul or fs_aug] += 1
            tally[v_aug] += 1
            tally_j[v_jul] += 1
            w.writerow({"cancer_type": v, "code_28Jul": j, "code_04Aug": a, "code_new": new[v],
                        "code_new_shipped": shipped, "verdict_vs_28Jul": v_jul, "fix_stage_vs_28Jul": fs_jul,
                        "verdict_vs_04Aug": v_aug, "fix_stage_vs_04Aug": fs_aug, "my_reason": reason,
                        "ruling_status": status})
    print(f"-> {dest}\n   {len(new)} values\n\n   vs 28 July (the HARD-gate baseline):")
    for k in judgements.VERDICTS + ("SAME_AS_04AUG",):
        if tally_j[k]:
            flag = ("   <-- must be zero before sign-off" if k == "REGRESSION"
                    else "   <-- review incomplete" if k == "UNREVIEWED" else "")
            print(f"     {k:14s} {tally_j[k]:5d}{flag}")
    print(f"\n   regressions by who can fix them:  "
          f"stage1={stages['stage1']}  stage2={stages['stage2']}")
    print("\n   vs 4 August (the last approved state):")
    for k in judgements.VERDICTS:
        if tally[k]:
            flag = ("   <-- must be zero before sign-off" if k == "REGRESSION"
                    else "   <-- review incomplete" if k == "UNREVIEWED" else "")
            print(f"     {k:14s} {tally[k]:5d}{flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
