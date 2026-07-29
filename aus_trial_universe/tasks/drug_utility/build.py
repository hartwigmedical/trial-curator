"""Standalone drug-reference builder (spec §6.1).

Builds / incrementally tops up the drug_ref resource from a list of raw drug names. An existing, non-stale
canonical is a pure lookup; only new (or --refresh) drugs are researched.

  python -m aus_trial_universe.tasks.drug_utility.build --drugs "pembrolizumab; Keytruda; Ris-Rez"
  python -m aus_trial_universe.tasks.drug_utility.build --from-trials NCT07099898,NCT05009992 --limit 5
  options: --refresh-drugs  --refresh-days N  --no-review  --model <name>

Writes data/agentic/drug_annotations/current_version/. Requires OPENAI_API_KEY. Run via `make drug-ref-build`.
"""
from __future__ import annotations

import argparse
import logging

from aus_trial_universe.run import _load_openai_key
from aus_trial_universe.tasks.shared.cohorts import trial_arm_id
from aus_trial_universe.tasks.shared.schema import TrialArm


def _collect(names, occ, arms, trial_id, registry, arm, arm_type, tokens) -> list[str]:
    """Register this arm in the shared `trial_arms` registry (`arms`, even if it has no drug) AND record each drug
    token as a global distinct name + a `(trial_arm_id, name)` occurrence (provenance). Returns this arm's distinct
    tokens (for the per-trial log line — the traceability lost otherwise)."""
    taid = trial_arm_id(trial_id, arm)
    if taid and not any(a.trial_arm_id == taid for a in arms):
        arms.append(TrialArm(trial_arm_id=taid, trialId=trial_id, registry=registry, arm=arm, arm_type=arm_type))
    seen: list[str] = []
    for d in tokens:
        d = d.strip()
        if not d:
            continue
        if d not in names:
            names.append(d)
        occ.append((taid, d))
        if d not in seen:
            seen.append(d)
    return seen


def _drugs_from_trials(client, ids: list[str]) -> tuple[list[str], list[tuple], list, list[tuple], list[str]]:
    """(distinct names, occurrences, trial_arms, arm_contexts, processed trial-ids) for specific trials — CTGov via
    armGroups (deterministic), ANZCTR via the SHARED `anzctr_regimes` (identical arms to the eligibility path).
    `arm_contexts` are (trial_arm_id, label, arm_type, description) tuples for the Phase-2 role classifier.
    `processed` lists EVERY trial derived (incl. no-drug ones), so the caller can overwrite their stale arm rows."""
    from aus_trial_universe.tasks.eligibility.extraction.loaders import load_trials
    from aus_trial_universe.tasks.shared.cohorts import anzctr_regimes

    log = logging.getLogger("agentic.drug_ref")
    names: list[str] = []
    occ: list[tuple] = []
    arms: list = []
    arm_ctxs: list[tuple] = []
    processed: list[str] = []
    for source, tid, text, cohorts in load_trials(ids=ids):
        regimes = cohorts if cohorts is not None else anzctr_regimes(client, text)   # ANZCTR: shared derivation
        processed.append(tid)
        toks: list[str] = []
        for c in regimes:                                    # one arm = one regime (label + type)
            toks += _collect(names, occ, arms, tid, source, c.label, c.arm_type, (c.drug or "").split(";"))
            arm_ctxs.append((trial_arm_id(tid, c.label), c.label, c.arm_type, c.description))
        log.info("  [%s] %s → %s", source, tid, "; ".join(dict.fromkeys(toks)) or "(none)")
    return names, occ, arms, arm_ctxs, processed


def _all_trial_drugs(client, *, use_reviewer: bool) -> tuple[list[str], list[tuple], list, list[tuple], list[str]]:
    """(distinct names, occurrences, trial_arms, arm_contexts, processed trial-ids) across ALL ctgov + anzctr
    trials: CTGov deterministic (armGroups); ANZCTR via the SHARED `anzctr_regimes` (parallel). Every drug is
    attributed to its trial ARM in `occurrences` (the trial_to_intervention provenance, keyed by trial_arm_id);
    every arm is registered in `trial_arms`; `arm_contexts` are (trial_arm_id, label, arm_type, description) tuples
    for the Phase-2 role classifier. `processed` lists every trial derived (incl. no-drug ones)."""
    from aus_trial_universe.core.workflow import fan_out
    from aus_trial_universe.tasks.eligibility.extraction.loaders import (
        load_all_anzctr_trials,
        load_all_ctgov_trials,
    )
    # Use the SHARED, flag-independent ANZCTR arm derivation so the (trialId, arm) split is IDENTICAL to the
    # eligibility path (the drug<->eligibility join key must match; see shared.cohorts.anzctr_regimes).
    from aus_trial_universe.tasks.shared.cohorts import anzctr_regimes

    log = logging.getLogger("agentic.drug_ref")
    names: list[str] = []
    occ: list[tuple] = []
    arms: list = []
    arm_ctxs: list[tuple] = []
    processed: list[str] = []
    ctgov = load_all_ctgov_trials()
    for nct, _text, cohorts in ctgov:
        processed.append(nct)
        toks: list[str] = []
        for c in (cohorts or []):                            # per armGroup: label + arm_type (deterministic)
            toks += _collect(names, occ, arms, nct, "ctgov", c.label, c.arm_type, (c.drug or "").split(";"))
            arm_ctxs.append((trial_arm_id(nct, c.label), c.label, c.arm_type, c.description))
        log.info("  [ctgov] %s → %s", nct, "; ".join(dict.fromkeys(toks)) or "(none)")
    log.info("collected %d distinct CTGov drug(s) from %d trials", len(names), len(ctgov))

    anz = load_all_anzctr_trials()
    log.info("identifying ANZCTR drugs (doer→reviewer) across %d trials …", len(anz))

    def _one(text):  # soft-fail: fan_out re-raises, so a bad trial must not kill collection
        try:
            return anzctr_regimes(client, text)   # shared derivation -> arms identical to the eligibility path
        except Exception as exc:  # noqa: BLE001
            return exc

    results = fan_out([(lambda text=text: _one(text)) for _actrn, text, _ in anz])
    before, failed = len(names), 0
    for (actrn, _text, _), regimes in zip(anz, results):     # fan_out preserves order -> align to trials
        if isinstance(regimes, Exception) or regimes is None:
            failed += 1
            log.info("  [anzctr] %s → (extraction failed: %s)", actrn,
                     type(regimes).__name__ if isinstance(regimes, Exception) else "none")
            continue
        # ANZCTR regimes come from the shared derivation: intervention / comparator (or a single `all`) — one
        # occurrence per (arm, arm_type), byte-identical to eligibility's trial_arms.
        processed.append(actrn)
        toks: list[str] = []
        for c in regimes:
            toks += _collect(names, occ, arms, actrn, "anzctr", c.label, c.arm_type, (c.drug or "").split(";"))
            arm_ctxs.append((trial_arm_id(actrn, c.label), c.label, c.arm_type, c.description))
        log.info("  [anzctr] %s → %s", actrn, "; ".join(dict.fromkeys(toks)) or "(none)")
    log.info("collected %d new distinct ANZCTR drug(s) (%d trials failed); %d distinct drugs total",
             len(names) - before, failed, len(names))
    return names, occ, arms, arm_ctxs, processed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build/refresh the drug_ref resource (spec §6.1).")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--drugs", help="';'-separated raw drug names to build.")
    src.add_argument("--from-trials", help="Comma-separated trial ids (CTGov or ANZCTR); (re)build those trials' "
                                           "regime drugs — overwrites their existing arm rows.")
    src.add_argument("--all-trials", action="store_true",
                     help="Build over EVERY distinct drug across ALL ctgov + anzctr trials.")
    parser.add_argument("--limit", type=int, default=None, help="Cap the number of drugs (after dedup).")
    parser.add_argument("--refresh-drugs", action="store_true", help="Re-research even drugs already in the resource.")
    parser.add_argument("--refresh-days", type=int, default=None, help="Re-research drugs older than N days.")
    parser.add_argument("--no-review", action="store_true", help="Skip the stage reviewers (cheaper).")
    parser.add_argument("--workers", type=int, default=8,
                        help="Parallel drugs + checkpoint batch size (default 8). Higher = faster, same output "
                             "(may hit API rate limits — cap with --max-concurrency).")
    parser.add_argument("--max-concurrency", type=int, default=None,
                        help="GLOBAL cap on concurrent LLM API calls (set to the account's empirical rate-limit "
                             "ceiling and raise --workers freely). Default: uncapped.")
    parser.add_argument("--model", default=None, help="Override the OpenAI model.")
    parser.add_argument("--no-cache", action="store_true",
                        help="Disable the on-disk LLM response cache (default: cache under data/agentic/cache/, so "
                             "a re-run after an interruption resumes near-instantly on the drugs already researched).")
    parser.add_argument("--no-cache-prune", action="store_true",
                        help="Skip the automatic prune of cache entries from OUTDATED prompts at build start "
                             "(default: on when the cache is enabled; removes only stale entries, never live/legacy).")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for _n in ("httpx", "openai", "urllib3", "numexpr"):
        logging.getLogger(_n).setLevel(logging.WARNING)
    _load_openai_key()

    from aus_trial_universe.core.client import DiskCache, LlmClient
    from aus_trial_universe.core.paths import CACHE_DIR
    from aus_trial_universe.tasks.drug_utility.store import DrugRefStore
    from aus_trial_universe.tasks.drug_utility.workflow import ArmContext, build_drug_ref, classify_arm_roles
    from aus_trial_universe.tasks.shared.store import TrialArmStore

    cache = None if args.no_cache else DiskCache(CACHE_DIR)
    _client_kw = dict(cache=cache, max_concurrency=args.max_concurrency)
    client = LlmClient(model=args.model, **_client_kw) if args.model else LlmClient(**_client_kw)

    # GC cache entries from outdated prompts before we start (only stale entries; never live/legacy).
    if cache is not None and not args.no_cache_prune:
        from aus_trial_universe.core.cache_prune import prune_cache, summary_line
        _rep = prune_cache(CACHE_DIR, apply=True)
        if _rep.removed:
            logging.getLogger("agentic.drug_ref").info(summary_line(_rep))

    occurrences: list[tuple] = []
    arms: list = []
    arm_ctxs: list[tuple] = []
    processed: list[str] = []
    if args.drugs:
        names = [d.strip() for d in args.drugs.split(";") if d.strip()]
    elif args.from_trials:
        names, occurrences, arms, arm_ctxs, processed = _drugs_from_trials(
            client, [x.strip() for x in args.from_trials.split(",") if x.strip()])
    else:  # --all-trials
        names, occurrences, arms, arm_ctxs, processed = _all_trial_drugs(client, use_reviewer=not args.no_review)
    if args.limit:
        names = names[: args.limit]
        keep = set(names)
        occurrences = [o for o in occurrences if o[-1] in keep]   # o = (trial_arm_id, name)
    if not names:
        parser.error("no drug names to build")

    # Populate the SHARED trial_arms registry from the arms just identified (overwrite each processed trial's arms).
    # Both paths write this registry; trial_to_intervention below links to it by trial_arm_id.
    if arms:
        arm_store = TrialArmStore.load()
        by_trial: dict[str, list] = {}
        for a in arms:
            by_trial.setdefault(a.trialId, []).append(a)
        for _tid, _rows in by_trial.items():
            arm_store.set_trial_arms(_tid, _rows)
        arm_store.save()

    store = DrugRefStore.load()
    # OVERWRITE — SURGICAL, --from-trials ONLY. Drop the stale trial_to_intervention rows for ONLY the explicitly
    # requested trials before re-deriving them (so an ANZCTR trial whose arms changed — even to a no-drug `all` —
    # replaces its old rows instead of keeping both). We deliberately do NOT do this under --all-trials: mass-
    # clearing the whole universe's occurrences before a rebuild would risk destroying a lot of legit output on a
    # mid-run crash. Drug facts (canonical/annotation tables) are shared across trials and never touched here.
    if args.from_trials:
        for _tid in dict.fromkeys(processed):
            removed = store.remove_trial_occurrences(_tid)
            removed_roles = store.remove_trial_roles(_tid)
            if removed or removed_roles:
                logging.getLogger("agentic.drug_ref").info(
                    "  overwrite · %s · dropped %d stale arm row(s), %d stale role row(s)", _tid, removed, removed_roles)
    log = logging.getLogger("agentic.drug_ref")
    log.info("drug-ref build · %d drug(s) · refresh=%s · review=%s\n", len(names), args.refresh_drugs, not args.no_review)

    summary = build_drug_ref(
        client, names, store, occurrences=occurrences,
        refresh=args.refresh_drugs, refresh_days=args.refresh_days,
        use_reviewer=not args.no_review, workers=args.workers,
        checkpoint=lambda: store.save(),   # persist progress after each batch (resilient to interruption)
    )

    # Phase 2 — per-arm main/auxiliary role (needs the drugs' canonical identities, so it runs AFTER the build).
    # Trial-independent `--drugs` builds have no arms, so nothing to classify.
    role_summary = None
    if arm_ctxs:
        role_summary = classify_arm_roles(
            client, [ArmContext(*t) for t in arm_ctxs], store,
            refresh=args.refresh_drugs, use_reviewer=not args.no_review, workers=args.workers,
            checkpoint=lambda: store.save(),
        )
    vdir = store.save()

    n_map = sum(len(v) for v in store.mappings.values())
    n_tgt = sum(len(v) for v in store.targets.values())
    n_ind = sum(len(v) for v in store.indications.values())
    n_role = sum(len(v) for v in store.roles.values())
    role_line = (f" · roles classified={role_summary.classified} · reused={role_summary.reused} · "
                 f"no_drug={role_summary.no_drugs} · failed={role_summary.failed}") if role_summary else ""
    print(f"\n{'═' * 70}\ndrug-ref → {vdir}/\n"
          f"  this run:  canonicalized={summary.canonicalized} · reused_alias={summary.reused_alias} · "
          f"non_drug={summary.non_drug} · researched={summary.researched} · reused_ref={summary.reused_ref} · "
          f"failed={summary.failed}{role_line}\n"
          f"  resource:  {n_map} mapping rows · {len(store.occurrences)} trial-links · {len(store.refs)} drugs · "
          f"{n_tgt} target rows · {n_ind} indication rows · {n_role} role rows\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
