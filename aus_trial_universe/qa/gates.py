"""Production gates — the deterministic checks that decide whether a refresh cycle is TRUSTWORTHY.

In production this pipeline runs unattended: no one reads the log, and nothing downstream can tell a good cycle
from a bad one by looking at the output (a partially-curated store still produces a plausible-looking export).
These gates close that hole. Every check is deterministic, reads only what the run left on disk, and returns
PASS / WARN / FAIL; any FAIL makes `refresh` exit non-zero and marks the run failed in STATUS.json, so the
scheduler sees it.

Design rules:
  - **Deterministic only.** No LLM, no network. A gate must give the same verdict on the same bytes.
  - **FAIL means "do not trust this cycle".** WARN means "look, but the data is usable".
  - **Exact arithmetic where possible.** Identities (`after == before - expired + curated`) beat thresholds.
  - Reference data may never shrink; trial-scoped data may shrink ONLY as expiry explains.

    python -m aus_trial_universe.qa.gates      # standalone: gate the CURRENT on-disk state (exit 1 on FAIL)
"""
from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"

# Countables that can NEVER legitimately shrink during a refresh: value->vocab maps are keyed by VALUE (expiry
# doesn't prune them) and the drug reference accumulates (pruning is an explicit, separate migration).
REFERENCE_COUNTS = (
    "cancer_type_map", "gene_alteration_map", "molecular_signature_map",
    "drugs (canonical)", "intervention_to_canonical", "drug_target_actions",
    "drug_regulatory_approvals", "approval_cancer_type_map", "approval_biomarker_map",
)
# Trial-scoped countables: these DO shrink when trials expire, so a decrease is only suspicious with 0 expiries.
TRIAL_SCOPED_COUNTS = (
    "trials curated", "arms (trial_arms registry)", "arm_eligibility_raw",
    "interpreted_eligibility", "trial_to_intervention", "trial_arm_drug_role",
)

UNIVERSE_SWING_WARN = 0.10     # kept-universe change vs the previous run beyond this is worth a human's attention


@dataclass
class Gate:
    name: str
    status: str
    detail: str

    @property
    def failed(self) -> bool:
        return self.status == FAIL


@dataclass
class GateReport:
    gates: list[Gate] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str) -> None:
        self.gates.append(Gate(name, status, detail))

    @property
    def failed(self) -> list[Gate]:
        return [g for g in self.gates if g.failed]

    @property
    def ok(self) -> bool:
        return not self.failed

    @property
    def verdict(self) -> str:
        if self.failed:
            return FAIL
        return WARN if any(g.status == WARN for g in self.gates) else PASS


def _export_trial_ids(path: Path) -> tuple[set[str], int, int]:
    """(trialIds, row count, column count) of the Set-A export."""
    if not Path(path).exists():
        return set(), 0, 0
    csv.field_size_limit(10 ** 9)
    ids: set[str] = set()
    rows = 0
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        cols = len(reader.fieldnames or [])
        for row in reader:
            rows += 1
            ids.add(row.get("trialId", ""))
    return ids, rows, cols


def _oncotree_expression_gate(rep: "GateReport", export_path: Path) -> None:
    """Parse every OncoTree code expression IN THE EXPORT and fail on any error-severity defect.

    Graded on the export rather than the map table because the export is what actually ships — and because it is
    the artifact every other gate here is already scoped to, so the gate cannot end up silently grading the live
    store while the rest of the report grades a fixture.

    This is the backstop that would have caught the whole 2026-08-03 defect set (leaked names, nested negation,
    negation-only cells, unsatisfiable conjunctions) the day it appeared. Catalogue: tools/oncotree_checks.py.
    """
    from collections import Counter
    from aus_trial_universe.tasks.eligibility.tools.oncotree import expression_problems

    if not Path(export_path).exists():
        rep.add("oncotree_expressions", PASS, "no export on disk (nothing to grade)")
        return
    csv.field_size_limit(10 ** 9)
    errors: Counter = Counter()
    warns: Counter = Counter()
    seen: set[str] = set()
    with open(export_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            code = (row.get("oncotree_code") or "").strip()
            if not code or code in seen:
                continue
            seen.add(code)
            for problem in expression_problems(code):
                (errors if problem.severity == "error" else warns)[problem.defect] += 1
    if errors:
        rep.add("oncotree_expressions", FAIL,
                f"{sum(errors.values())} error-severity defect(s) across {len(seen):,} distinct expressions: "
                + ", ".join(f"{k}×{v}" for k, v in errors.most_common(5)))
    elif warns:
        rep.add("oncotree_expressions", WARN,
                f"{len(seen):,} distinct expressions · 0 errors · "
                + ", ".join(f"{k}×{v}" for k, v in warns.most_common(5)))
    else:
        rep.add("oncotree_expressions", PASS, f"{len(seen):,} distinct expressions · 0 defects")


def run_gates(
    *,
    before: dict[str, int] | None = None,
    after: dict[str, int] | None = None,
    kept: set[str] | None = None,
    expiry=None,
    curated_ids: list[str] | None = None,
    export_path: Path | None = None,
    elig_rc: int = 0,
    previous_kept: int | None = None,
) -> GateReport:
    """Gate the on-disk state. Run-context arguments are optional so the module also works standalone: with no
    `before`/`expiry` the deltas and churn gates are skipped rather than guessed."""
    from aus_trial_universe.core.paths import EXPORT_FILE, EXPORT_ROOT
    from aus_trial_universe.export import EXPORT_COLUMNS
    from aus_trial_universe.tasks.eligibility.qa.arm_consistency import check as fk_check
    from aus_trial_universe.tasks.eligibility.scope import unexplained_arms
    from aus_trial_universe.qa.waivers import WAIVED_EMPTY_ARMS
    from aus_trial_universe.tasks.eligibility.store import EligStore
    from aus_trial_universe.tasks.shared.store import TrialArmStore

    rep = GateReport()
    export_path = Path(export_path or (EXPORT_ROOT / EXPORT_FILE))

    # 1) FK referential integrity — a dangling trial_arm_id silently drops a row at join time.
    r = fk_check()
    dangling = len(r["elig_dangling"]) + len(r["drug_dangling"]) + len(r["role_dangling"])
    rep.add("fk_integrity", PASS if dangling == 0 else FAIL,
            f"registry {r['registry']:,} · elig {r['elig_refs']:,} · drug {r['drug_refs']:,} · "
            f"role {r['role_refs']:,} · dangling {dangling}")

    elig = EligStore.load()
    arms = TrialArmStore.load()

    _oncotree_expression_gate(rep, export_path)
    registry_arms = arms.ids()
    registry_trials = set(arms.arms)

    # 2) Expiry completeness — an expired trial must be gone from EVERY live master, and trial_info must track
    #    the registry exactly. Leftovers here are how a "deleted" trial keeps appearing downstream.
    problems = []
    if expiry is not None and getattr(expiry, "expired", None):
        still = sorted(set(expiry.expired) & registry_trials)
        if still:
            problems.append(f"{len(still)} expired trial(s) still in the registry: {still[:5]}")
    try:
        from aus_trial_universe.trial_info import load_trial_info
        info_ids = set(load_trial_info())          # {trialId: TrialInfo} -> its keys
        extra = info_ids - registry_trials
        missing = registry_trials - info_ids
        if extra:
            problems.append(f"{len(extra)} trial_info row(s) for trials not in the registry")
        if missing:
            problems.append(f"{len(missing)} registry trial(s) missing from trial_info")
    except Exception as exc:                      # trial_info is rebuilt by export; absence is a real problem
        problems.append(f"trial_info unreadable: {exc}")
    rep.add("expiry_completeness", PASS if not problems else FAIL, "; ".join(problems) or "no orphans in any master")

    # 3) Additive safety — reference data must never shrink; trial data only as expiry explains.
    if before and after:
        shrunk_ref = [k for k in REFERENCE_COUNTS if after.get(k, 0) < before.get(k, 0)]
        n_expired = len(getattr(expiry, "expired", []) or [])
        shrunk_trial = [k for k in TRIAL_SCOPED_COUNTS if after.get(k, 0) < before.get(k, 0)]
        msgs = []
        if shrunk_ref:
            msgs.append("reference data SHRANK: " + ", ".join(
                f"{k} {before[k]:,}→{after[k]:,}" for k in shrunk_ref))
        if shrunk_trial and n_expired == 0:
            msgs.append("trial data shrank with 0 expiries: " + ", ".join(
                f"{k} {before[k]:,}→{after[k]:,}" for k in shrunk_trial))
        # Exact identity on the trial count: after == before - expired + newly curated.
        exp_trials = before.get("trials curated", 0) - n_expired + len(curated_ids or [])
        got = after.get("trials curated", 0)
        if got != exp_trials:
            msgs.append(f"trial count identity broken: expected {exp_trials:,} "
                        f"({before.get('trials curated', 0):,} − {n_expired} expired + {len(curated_ids or [])} "
                        f"curated), got {got:,}")
        rep.add("additive_safety", PASS if not msgs else FAIL,
                "; ".join(msgs) or f"nothing lost; trial count identity holds ({got:,})")

    # 4) Curation completeness — every kept trial must be in the store, and the eligibility run must have
    #    succeeded. rc=3 means trials are still missing (failed / offline) — the export would ship incomplete.
    if kept is not None:
        absent = sorted(kept - registry_trials)
        status = FAIL if (absent or elig_rc != 0) else PASS
        detail = f"kept {len(kept):,} · in store {len(registry_trials & kept):,} · rc={elig_rc}"
        if absent:
            detail += f" · MISSING {len(absent)}: {absent[:5]}"
        rep.add("curation_completeness", status, detail)

    # 5) Empty-output reasons — every arm with no DNF must say WHY. An unexplained empty is a possible
    #    extraction miss, and is the one signal that would otherwise vanish into a row-count.
    empty = elig.empty_arms(registry_arms)
    unexplained = unexplained_arms(elig, empty)   # covers "verdict says unexplained" AND "no verdict at all"
    waived = [a for a in unexplained if a in WAIVED_EMPTY_ARMS]
    bad = [a for a in unexplained if a not in WAIVED_EMPTY_ARMS]
    rep.add("empty_output_reasons", PASS if not bad else FAIL,
            f"{len(empty)} empty arm(s), all reason-coded" + (f" ({len(waived)} waived)" if waived else "")
            if not bad
            else f"{len(bad)} empty arm(s) UNEXPLAINED (possible extraction miss): {bad[:5]}")
    # Waived findings are never silently absolved — they stay on the report as a WARN with the follow-up named.
    if waived:
        rep.add("waived_findings", WARN,
                f"{len(waived)} known extraction miss(es) accepted pending a prompt fix (see qa/waivers.py "
                f"+ the handover to-do): {waived[:5]}")

    # 6) Export integrity — shape, and no kept+curated trial missing without a reason.
    export_ids, rows, cols = _export_trial_ids(export_path)
    msgs = []
    if rows == 0:
        msgs.append("export is empty or missing")
    if cols != len(EXPORT_COLUMNS):
        msgs.append(f"column count {cols} != expected {len(EXPORT_COLUMNS)}")
    if kept is not None:
        explained = {a.split("::", 1)[0] for a in elig.scope}
        unexp_missing = sorted((kept & registry_trials) - export_ids - explained)
        if unexp_missing:
            msgs.append(f"{len(unexp_missing)} curated trial(s) absent from the export with no scope verdict: "
                        f"{unexp_missing[:5]}")
    rep.add("export_integrity", PASS if not msgs else FAIL,
            "; ".join(msgs) or f"{rows:,} rows · {len(export_ids):,} trials · {cols} cols")

    # 6b) The independent output validator ("review of the reviewers") over the finished export. WARN-only by
    #     design: its checks are deliberately blunt and some flags are validator crudeness (e.g. a same-type
    #     histology+stage AND is satisfiable and faithful), so a cycle must not fail on them — but the count has to
    #     be VISIBLE every run, which is what it never was while it pointed at a retired file nobody generated.
    if rows:
        try:
            from aus_trial_universe.tasks.eligibility.qa.validate_output import load_output_rows, validate_rows
            reports = validate_rows(load_output_rows(export_path))
            n_problems = sum(len(r.problems) for r in reports)
            flagged = [r.trial_id for r in reports if r.problems]
            rep.add("output_validator", WARN if n_problems else PASS,
                    f"{len(reports) - len(flagged):,}/{len(reports):,} trials clean · {n_problems:,} flag(s)"
                    + (f" · e.g. {flagged[:3]}" if flagged else ""))
        except Exception as exc:
            rep.add("output_validator", WARN, f"validator unavailable: {exc}")

    # 7) Universe swing — a big change in the kept set vs the previous cycle is legitimate (a registry surge) or a
    #    filter/query regression. Deliberately a WARN: the expiry fraction guard already blocks the catastrophic case.
    if kept is not None and previous_kept:
        delta = len(kept) - previous_kept
        frac = abs(delta) / previous_kept
        rep.add("universe_swing", WARN if frac > UNIVERSE_SWING_WARN else PASS,
                f"kept {previous_kept:,} → {len(kept):,} ({delta:+,}, {frac:.1%})")

    # 8) The expiry guard itself — if it tripped, this cycle deliberately did NOT expire; that is a FAIL because
    #    the download was judged untrustworthy.
    if expiry is not None:
        if getattr(expiry, "guarded", False):
            rep.add("expiry_guard", FAIL, f"guard tripped — {len(expiry.expired)} trial(s) would have expired "
                                          f"(>{50}% of the store); treated as a partial/failed download")
        elif getattr(expiry, "pottr_skipped", False):
            rep.add("expiry_guard", WARN, "POTTR list unavailable — expiry was skipped this cycle")

    return rep


def log_report(rep: GateReport, log: logging.Logger | None = None) -> None:
    lg = log or logger
    mark = {PASS: "✓", WARN: "⚠", FAIL: "✗"}
    lg.info("\n%s\nGATES · %s\n%s", "═" * 70, rep.verdict, "═" * 70)
    for g in rep.gates:
        lg.info("  %s %-24s %s", mark.get(g.status, "?"), g.name, g.detail)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    rep = run_gates()
    log_report(rep)
    return 1 if rep.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
