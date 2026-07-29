"""Periodic end-to-end refresh (the self-contained pipeline's one command).

  ingest (download + filter + POTTR + version, both registries)
    -> expire/restore (retire trials that fell out of the kept universe; restore any that returned)
    -> curate ONLY new trials  (eligibility: extract + Step-1 map)      — run.py --resume --skip-drug
    -> Step-2 reconcile         (finalised vocab maps)                    — run.py --reconcile
    -> drug utility for new/restored trials (canonical/annotate/approvals + occurrences + main/aux roles;
       existing drugs = pure lookup, NO web search)                      — drug_utility.build --from-trials
    -> approval symmetric-match vocab                                    — drug_utility.map_approvals
    -> matching-engine export (Set A + MANIFEST)                         — export

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
    from aus_trial_universe.tasks.drug_utility import build as drug_build
    from aus_trial_universe.tasks.drug_utility import map_approvals as approvals_mod
    from aus_trial_universe.tasks.eligibility.store import EligStore
    from aus_trial_universe.tasks.ingestion.expiry import compute_kept_ids, run_expiry

    wk = ["--workers", str(args.workers)]
    if args.max_concurrency is not None:
        wk += ["--max-concurrency", str(args.max_concurrency)]
    review = ["--no-review"] if args.no_review else []

    # 1) INGEST both registries (full download → current_version/; previous archived).
    if not args.skip_ingest:
        log.info("\n%s\n▶ REFRESH 1/7 · INGEST (ctgov + anzctr)\n%s", "═" * 70, "═" * 70)
        ingest_mod._ingest_ctgov(log)
        ingest_mod._ingest_anzctr(log)
    else:
        log.info("▶ REFRESH · skipping ingest (--skip-ingest); reusing current inputs")

    # 2) EXPIRE / RESTORE against the fresh kept universe (recoverable). Compute drug-pending BEFORE curation.
    log.info("\n%s\n▶ REFRESH 2/7 · EXPIRE / RESTORE\n%s", "═" * 70, "═" * 70)
    kept = compute_kept_ids(log=log)
    report = run_expiry(dry_run=args.dry_run_expiry, log=log)
    # Trials needing drug work = those not yet curated (new) + any just restored (their drug rows were dropped).
    _elig = EligStore.load()   # load once (post-expiry state)
    curated = {t for t in kept if _elig.has_trial(t)}
    drug_pending = sorted((kept - curated) | set(report.restored))
    log.info("refresh · new-or-restored trials to curate: %d", len(drug_pending))

    # 3) CURATE new trials — eligibility only (extract + Step-1 map). --resume curates just the not-yet-stored ones.
    log.info("\n%s\n▶ REFRESH 3/7 · ELIGIBILITY (extract + map, resume)\n%s", "═" * 70, "═" * 70)
    rc = run_mod.main(["--resume", "--skip-drug", *wk, *review])
    if rc not in (0,):
        log.warning("refresh · eligibility run returned rc=%s (incomplete); continuing with what completed", rc)

    # 4) Step-2 reconciliation (finalised vocab maps over the frozen extract store).
    log.info("\n%s\n▶ REFRESH 4/7 · MAP STEP-2 RECONCILE\n%s", "═" * 70, "═" * 70)
    run_mod.main(["--reconcile", *wk, *review])

    # 5) DRUG utility for new/restored trials (facts + occurrences + main/aux roles; existing drugs = lookup).
    if drug_pending:
        log.info("\n%s\n▶ REFRESH 5/7 · DRUG UTILITY (%d trial(s))\n%s", "═" * 70, len(drug_pending), "═" * 70)
        drug_build.main(["--from-trials", ",".join(drug_pending), *wk, *review])
    else:
        log.info("\n▶ REFRESH 5/7 · DRUG UTILITY · no new/restored trials — skipping")

    # 6) Approval symmetric-match vocab (seeded from the trial FINAL maps; cheap).
    log.info("\n%s\n▶ REFRESH 6/7 · APPROVAL VOCAB\n%s", "═" * 70, "═" * 70)
    approvals_mod.main([*wk, *review])

    # 7) Matching-engine export (Set A + MANIFEST; Set B in place).
    log.info("\n%s\n▶ REFRESH 7/7 · EXPORT\n%s", "═" * 70, "═" * 70)
    export_mod.run_export()

    print(f"\n{'█' * 70}\nREFRESH COMPLETE · expired={len(report.expired)} · restored={len(report.restored)} · "
          f"curated={len(drug_pending)}\n{'█' * 70}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
