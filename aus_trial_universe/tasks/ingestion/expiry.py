"""Trial expiry + restore (the incremental-refresh store-retire capability).

After a fresh ingestion, a curated trial that has fallen out of the kept universe (its recruitment status is no
longer one we keep, so it is absent from the fresh download) should leave the LIVE stores — but recoverably, in
case it re-appears. This module:

  - computes `kept` = the trial ids the loaders now see (fresh ctgov + anzctr current_version) ∪ POTTR-listed ids;
  - EXPIRES `stored − kept` by MOVING each trial's eligibility (raw + interpreted) + trial_arms rows from the live
    stores into a recoverable `expired/` area, and REMOVING its drug provenance (trial_to_intervention +
    trial_arm_drug_role) — the drug rows are cheap and re-derived (lookup-first, no web search) on the next drug
    build if the trial is restored;
  - RESTORES any expired trial that is back in `kept` (moves its rows from `expired/` back to live);
  - POTTR-listed trials NEVER expire (the download re-fetches them, so they're in `kept`; a belt-and-suspenders
    exemption also protects them). Expiry is SKIPPED when the POTTR list could not be fetched (so a POTTR network
    blip can't wrongly expire trials), and when more than `max_fraction` of the store would expire (a guard against
    a partial/failed download). A `dry_run` reports the sets without mutating.

The value->vocab maps + shared drug facts (canonical drugs) are trial-independent and never moved.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from aus_trial_universe.core.paths import (
    CURRENT_VERSION,
    DRUG_ANNOTATIONS_ROOT,
    ELIGIBILITY_OUTPUT,
    TRIAL_ARMS_ROOT,
)

logger = logging.getLogger(__name__)

# Recoverable expired areas (siblings of each master's live data).
EXPIRED_ELIG_DIR = ELIGIBILITY_OUTPUT / "expired"
EXPIRED_ARMS_DIR = TRIAL_ARMS_ROOT / "expired"

DEFAULT_MAX_FRACTION = 0.5   # skip wholesale if >this fraction of the store would expire (partial-download guard)


@dataclass
class ExpiryReport:
    kept: int = 0
    stored: int = 0
    expired: list[str] = field(default_factory=list)
    restored: list[str] = field(default_factory=list)
    guarded: bool = False        # expiry skipped: too large a fraction would expire
    pottr_skipped: bool = False  # expiry skipped: POTTR list unavailable
    applied: bool = False        # rows actually moved (False for dry-run / skipped)


def _norm(tid: str) -> str:
    return (tid or "").strip().upper()


def decide_expiry(
    stored: set[str], kept: set[str], pottr: set[str], previously_expired: set[str], *, max_fraction: float,
) -> tuple[list[str], list[str], bool]:
    """Pure decision: given the curated `stored` trialIds, the `kept` universe, the `pottr` exempt set, and the
    `previously_expired` set, return (to_expire, to_restore, guarded). POTTR trials never expire; the guard trips
    when more than `max_fraction` of the store would expire."""
    expired = sorted((stored - kept) - pottr)
    restored = sorted(previously_expired & kept)
    guarded = bool(stored) and len(expired) > max_fraction * len(stored)
    return expired, restored, guarded


def compute_kept_ids(*, log: logging.Logger) -> set[str]:
    """The current kept universe = every trial id the loaders now see (fresh ctgov + anzctr current_version)."""
    from aus_trial_universe.tasks.eligibility.extraction.loaders import (
        load_all_anzctr_trials,
        load_all_ctgov_trials,
    )
    kept: set[str] = set()
    try:
        kept |= {_norm(nct) for nct, *_ in load_all_ctgov_trials()}
    except FileNotFoundError:
        log.warning("no ctgov input found while computing kept set")
    try:
        kept |= {_norm(actrn) for actrn, *_ in load_all_anzctr_trials()}
    except FileNotFoundError:
        log.warning("no anzctr input found while computing kept set")
    return kept


def run_expiry(*, dry_run: bool = False, max_fraction: float = DEFAULT_MAX_FRACTION,
               require_pottr: bool = True, log: logging.Logger | None = None) -> ExpiryReport:
    """Expire trials that fell out of the kept universe (recoverably) and restore any that re-appeared."""
    from aus_trial_universe.tasks.drug_utility.store import DrugRefStore
    from aus_trial_universe.tasks.eligibility.store import EligStore
    from aus_trial_universe.tasks.ingestion.pottr_ids import load_pottr_trial_ids_best_effort
    from aus_trial_universe.tasks.shared.store import TrialArmStore

    log = log or logger
    kept = compute_kept_ids(log=log)
    pottr = {_norm(t) for t in load_pottr_trial_ids_best_effort()}
    kept |= pottr  # POTTR trials are always kept

    live_elig = EligStore.load()
    stored = set(live_elig.raw) | set(live_elig.interpreted)   # curated trialIds (display form)
    report = ExpiryReport(kept=len(kept), stored=len(stored))

    if require_pottr and not pottr:
        report.pottr_skipped = True
        log.warning("expiry SKIPPED — POTTR trial list unavailable (would risk expiring POTTR trials). "
                    "Re-run when the POTTR source is reachable, or pass require_pottr=False.")
        return report

    exp_elig = EligStore.load_dir(EXPIRED_ELIG_DIR)
    exp_arms = TrialArmStore.load_dir(EXPIRED_ARMS_DIR)
    previously_expired = set(exp_elig.raw) | set(exp_elig.interpreted)

    expired, restored, guarded = decide_expiry(stored, kept, pottr, previously_expired, max_fraction=max_fraction)
    report.expired, report.restored, report.guarded = expired, restored, guarded
    if guarded:
        log.warning("expiry SKIPPED (guard) — %d of %d stored trials would expire (>%.0f%%); treating as a "
                    "partial/failed download. Restores also deferred.", len(expired), len(stored), max_fraction * 100)
        return report

    log.info("expiry · kept=%d · stored=%d · to-expire=%d · to-restore=%d%s",
             len(kept), len(stored), len(expired), len(report.restored), "  (dry-run)" if dry_run else "")
    if dry_run or (not expired and not report.restored):
        return report

    live_arms = TrialArmStore.load()
    live_drug = DrugRefStore.load()

    for tid in expired:                                      # live -> expired (recoverable)
        raw, interp = live_elig.pop_trial(tid)
        exp_elig.set_trial(tid, raw, interp)
        exp_arms.set_trial_arms(tid, live_arms.pop_trial(tid))
        live_drug.remove_trial_occurrences(tid)              # drug provenance re-derived on restore's next build
        live_drug.remove_trial_roles(tid)
    for tid in report.restored:                              # expired -> live
        raw, interp = exp_elig.pop_trial(tid)
        live_elig.set_trial(tid, raw, interp)
        live_arms.set_trial_arms(tid, exp_arms.pop_trial(tid))

    live_elig.save(ELIGIBILITY_OUTPUT / CURRENT_VERSION)
    live_arms.save(TRIAL_ARMS_ROOT)
    live_drug.save(DRUG_ANNOTATIONS_ROOT)
    exp_elig.save_content(EXPIRED_ELIG_DIR)
    exp_arms.save_dir(EXPIRED_ARMS_DIR)
    report.applied = True
    log.info("expiry APPLIED · expired %d → %s · restored %d", len(expired), EXPIRED_ELIG_DIR, len(report.restored))
    return report


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Expire trials that fell out of the kept universe (recoverable).")
    parser.add_argument("--apply", action="store_true", help="Apply the moves (default: dry-run report only).")
    parser.add_argument("--no-require-pottr", action="store_true",
                        help="Proceed even if the POTTR list could not be fetched (default: skip expiry then).")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for _n in ("httpx", "urllib3", "requests", "numexpr"):
        logging.getLogger(_n).setLevel(logging.WARNING)
    r = run_expiry(dry_run=not args.apply, require_pottr=not args.no_require_pottr)
    print(f"\n{'═' * 70}\nexpiry {'APPLIED' if r.applied else '(dry-run)'} · kept={r.kept} · stored={r.stored} · "
          f"expired={len(r.expired)} · restored={len(r.restored)} · guarded={r.guarded} · pottr_skipped={r.pottr_skipped}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
