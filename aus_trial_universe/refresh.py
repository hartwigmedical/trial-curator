"""Periodic end-to-end refresh (the self-contained pipeline's one command).

  ingest (download + filter + POTTR + version, both registries)
    -> expire/restore (retire trials that fell out of the kept universe; restore any that returned)
    -> curate ONLY new trials  (eligibility: extract + Step-1 map)      — run.py --resume --skip-drug
    -> Step-2 reconcile         (finalised vocab maps)                    — run.py --reconcile
    -> drug utility for new/restored trials (canonical/annotate/approvals + occurrences + main/aux roles;
       existing drugs = pure lookup, NO web search)                      — drug_utility.build --from-trials
    -> approval symmetric-match vocab                                    — drug_utility.map_approvals
    -> matching-engine export (Set A + MANIFEST)                         — export
    -> run report (the durable per-cycle record)                          — run_report

Incremental by construction: an unchanged trial re-hits the DiskCache (near-instant, no LLM); an already-researched
drug is skipped by the drug store's lookup-first gate. Only genuinely-new trials/drugs cost LLM calls. Full download
each run, so a trial whose recruitment status is no longer kept simply drops out of the fresh universe and is expired.

  python -m aus_trial_universe.refresh
  options: --workers N --max-concurrency N --no-review --dry-run-expiry --skip-ingest (reuse current inputs)
Run via `make agentic-refresh`. Wrap in `caffeinate -i`.
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime

from aus_trial_universe.core.paths import ELIGIBILITY_OUTPUT, snapshot_current_version


def _scope_pass(log: logging.Logger, *, workers: int, max_concurrency: int | None) -> dict[str, int]:
    """Give every empty arm a scope verdict (`arm_scope`), so the gate can tell "legitimately out of scope" from
    "extraction missed it". Deterministic rules first; the LLM sees only what no rule explains. Writes ONLY the
    arm_scope table — content tables and maps are never re-persisted."""
    from aus_trial_universe.core.client import DiskCache, LlmClient
    from aus_trial_universe.core.paths import CACHE_DIR, CURRENT_VERSION, ELIGIBILITY_OUTPUT
    from aus_trial_universe.tasks.eligibility import scope as scope_mod
    from aus_trial_universe.tasks.eligibility.extraction import loaders
    from aus_trial_universe.tasks.eligibility.store import EligStore
    from aus_trial_universe.tasks.shared.store import TrialArmStore

    elig, arms = EligStore.load(), TrialArmStore.load()
    arm_ids = arms.ids()
    pending = [a for a in elig.empty_arms(arm_ids) if a not in elig.scope]
    if not pending:
        # Still call through: an arm that was REPAIRED (or expired) since the last pass leaves a stale verdict
        # behind, and pruning it lives inside classify_empty_arms. Returning early here would skip that.
        stale = len(elig.scope) - len(set(elig.empty_arms(arm_ids)) & set(elig.scope))
        scope_mod.classify_empty_arms(None, elig, arm_ids)
        if stale:
            elig.save_scope(ELIGIBILITY_OUTPUT / CURRENT_VERSION)
            log.info("▶ SCOPE · no new empty arms · pruned %d stale verdict(s) for repaired/expired arms", stale)
        else:
            log.info("▶ SCOPE · all empty arms already have a verdict — nothing to do")
        return {}

    log.info("▶ SCOPE · %d empty arm(s) without a verdict", len(pending))
    source_text: dict[str, str] = {}
    for tid, text, _ in loaders.load_all_ctgov_trials():
        source_text[tid] = text
    for tid, text, _ in loaders.load_all_anzctr_trials():
        source_text[tid] = text
    client = LlmClient(cache=DiskCache(CACHE_DIR), max_concurrency=max_concurrency)
    counts = scope_mod.classify_empty_arms(
        client, elig, arm_ids, source_text_for=source_text,
        ctgov_healthy=loaders.ctgov_healthy_volunteer_flags(), workers=workers,
        checkpoint=lambda: elig.save_scope(ELIGIBILITY_OUTPUT / CURRENT_VERSION))
    elig.save_scope(ELIGIBILITY_OUTPUT / CURRENT_VERSION)
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="End-to-end periodic refresh (ingest → expire → curate new → export).")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--max-concurrency", type=int, default=None)
    parser.add_argument("--no-review", action="store_true")
    parser.add_argument("--skip-ingest", action="store_true", help="Reuse the current inputs (skip the download).")
    parser.add_argument("--dry-run-expiry", action="store_true", help="Report expiry without mutating the stores.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for _n in ("httpx", "openai", "urllib3", "requests", "numexpr"):
        logging.getLogger(_n).setLevel(logging.WARNING)
    log = logging.getLogger("agentic.refresh")

    from aus_trial_universe import export as export_mod
    from aus_trial_universe import ingest as ingest_mod
    from aus_trial_universe import run as run_mod
    from aus_trial_universe import run_report
    from aus_trial_universe.qa import gates
    from aus_trial_universe.tasks.drug_utility import build as drug_build
    from aus_trial_universe.tasks.drug_utility import map_approvals as approvals_mod
    from aus_trial_universe.tasks.eligibility.store import EligStore
    from aus_trial_universe.tasks.ingestion.expiry import compute_kept_ids, run_expiry

    wk = ["--workers", str(args.workers)]
    if args.max_concurrency is not None:
        wk += ["--max-concurrency", str(args.max_concurrency)]
    review = ["--no-review"] if args.no_review else []

    started = datetime.now()
    notes: list[str] = []
    before = run_report.snapshot()   # countable master state BEFORE anything moves (for the report's deltas)

    # 0) BASELINE the eligibility store. A copy (not a move) — the store accumulates, so it must stay in place.
    #    This is what makes `mapping_drift` a genuine run-to-run diff instead of a comparison against an
    #    ever-staler snapshot, and it gives the LLM-curated masters a per-cycle rollback point.
    baseline = snapshot_current_version(ELIGIBILITY_OUTPUT, datetime.now().strftime("%d%m%Y"))
    if baseline is not None:
        log.info("refresh · eligibility baseline snapshot → %s", baseline)
    else:
        log.info("refresh · no eligibility current_version to baseline (first build)")

    # 1) INGEST both registries (full download → current_version/; previous archived).
    if not args.skip_ingest:
        log.info("\n%s\n▶ REFRESH 1/9 · INGEST (ctgov + anzctr)\n%s", "═" * 70, "═" * 70)
        ingest_mod._ingest_ctgov(log)
        ingest_mod._ingest_anzctr(log)
    else:
        log.info("▶ REFRESH · skipping ingest (--skip-ingest); reusing current inputs")

    # 2) EXPIRE / RESTORE against the fresh kept universe (recoverable). Compute drug-pending BEFORE curation.
    log.info("\n%s\n▶ REFRESH 2/9 · EXPIRE / RESTORE\n%s", "═" * 70, "═" * 70)
    kept = compute_kept_ids(log=log)
    report = run_expiry(dry_run=args.dry_run_expiry, log=log)
    # Trials needing drug work = those not yet curated (new) + any just restored (their drug rows were dropped).
    _elig = EligStore.load()   # load once (post-expiry state)
    curated = {t for t in kept if _elig.has_trial(t)}
    drug_pending = sorted((kept - curated) | set(report.restored))
    log.info("refresh · new-or-restored trials to curate: %d", len(drug_pending))

    # 3) CURATE new trials — eligibility only (extract + Step-1 map). --resume curates just the not-yet-stored ones.
    log.info("\n%s\n▶ REFRESH 3/9 · ELIGIBILITY (extract + map, resume)\n%s", "═" * 70, "═" * 70)
    rc = run_mod.main(["--resume", "--skip-drug", *wk, *review])
    if rc not in (0,):
        log.warning("refresh · eligibility run returned rc=%s (incomplete); continuing with what completed", rc)
        notes.append(f"eligibility run returned rc={rc} (incomplete) — curation may be partial")

    # 4) Step-2 reconciliation (finalised vocab maps over the frozen extract store).
    log.info("\n%s\n▶ REFRESH 4/9 · MAP STEP-2 RECONCILE\n%s", "═" * 70, "═" * 70)
    run_mod.main(["--reconcile", *wk, *review])

    # 5) DRUG utility for new/restored trials (facts + occurrences + main/aux roles; existing drugs = lookup).
    if drug_pending:
        log.info("\n%s\n▶ REFRESH 5/9 · DRUG UTILITY (%d trial(s))\n%s", "═" * 70, len(drug_pending), "═" * 70)
        drug_build.main(["--from-trials", ",".join(drug_pending), *wk, *review])
    else:
        log.info("\n▶ REFRESH 5/9 · DRUG UTILITY · no new/restored trials — skipping")

    # 6) Approval symmetric-match vocab (seeded from the trial FINAL maps; cheap).
    log.info("\n%s\n▶ REFRESH 6/9 · APPROVAL VOCAB\n%s", "═" * 70, "═" * 70)
    approvals_mod.main([*wk, *review])

    # 6b) SCOPE VERDICTS — why any arm has no interpreted eligibility. Deterministic first, LLM only for the
    #     residue; idempotent (already-verdicted arms are skipped), so this also back-fills the existing store.
    log.info("\n%s\n▶ REFRESH 7/9 · SCOPE VERDICTS (empty-output reasons)\n%s", "═" * 70, "═" * 70)
    _scope_pass(log, workers=args.workers, max_concurrency=args.max_concurrency)

    # 7) Matching-engine export (Set A + MANIFEST; Set B in place).
    log.info("\n%s\n▶ REFRESH 8/9 · EXPORT\n%s", "═" * 70, "═" * 70)
    export_path = export_mod.run_export()

    # The durable per-cycle record: the masters are mutated in place and the export overwritten, so without this
    # the only trace of a completed cycle is the log.
    settings = (f"refresh --workers {args.workers}"
                + (f" --max-concurrency {args.max_concurrency}" if args.max_concurrency is not None else "")
                + (" --no-review" if args.no_review else "")
                + (" --skip-ingest" if args.skip_ingest else "")
                + (" --dry-run-expiry" if args.dry_run_expiry else ""))
    # 9) GATES — the deterministic trust decision. Any FAIL makes this run exit non-zero so an unattended
    #    scheduler sees a bad cycle instead of a silent success.
    log.info("\n%s\n▶ REFRESH 9/9 · GATES\n%s", "═" * 70, "═" * 70)
    gate_report = gates.run_gates(
        before=before, after=run_report.snapshot(), kept=kept, expiry=report, curated_ids=drug_pending,
        export_path=export_path, elig_rc=rc, previous_kept=run_report.previous_kept_count())
    gates.log_report(gate_report, log)

    report_path = run_report.write_report(
        before=before, after=run_report.snapshot(), kept=kept, expiry=report, curated_ids=drug_pending,
        export_path=export_path, settings=settings, started=started, finished=datetime.now(),
        dry_run=args.dry_run_expiry, gates=gate_report, notes=notes)

    ok = gate_report.ok
    print(f"\n{'█' * 70}\nREFRESH {'COMPLETE' if ok else 'FAILED (gates)'} · gates={gate_report.verdict} · "
          f"expired={len(report.expired)} · restored={len(report.restored)} · curated={len(drug_pending)}\n"
          f"run report · {report_path}\n{'█' * 70}\n")
    if not ok:
        for g in gate_report.failed:
            log.error("GATE FAILED · %s · %s", g.name, g.detail)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
