"""Standalone drug-reference builder (spec §6.1).

Builds / incrementally tops up the drug_ref resource from a list of raw drug names. An existing, non-stale
canonical is a pure lookup; only new (or --refresh) drugs are researched.

  python -m aus_trial_universe.agentic.tasks.drug_ref.build --drugs "pembrolizumab; Keytruda; Ris-Rez"
  python -m aus_trial_universe.agentic.tasks.drug_ref.build --from-trials NCT07099898,NCT05009992 --limit 5
  options: --refresh-drugs  --refresh-days N  --no-review  --model <name>

Writes data/agentic/resources/drug_ref/version_<ddmmyyyy>/. Requires OPENAI_API_KEY. Run via `make drug-ref-build`.
"""
from __future__ import annotations

import argparse
import logging

from aus_trial_universe.agentic.run import _load_openai_key


def _add_tokens(names: list[str], tokens) -> None:
    for d in tokens:
        d = d.strip()
        if d and d not in names:
            names.append(d)


def _drugs_from_trials(ids: list[str]) -> list[str]:
    """Distinct regime drug names from CTGov trials (ANZCTR drugs are LLM-derived at run time -> skipped)."""
    from aus_trial_universe.agentic.tasks.extraction.loaders import load_trials

    log = logging.getLogger("agentic.drug_ref")
    names: list[str] = []
    for source, tid, _text, cohorts in load_trials(ids=ids):
        if cohorts is None:
            log.warning("skip %s: ANZCTR drugs are extracted during a run, not available for --from-trials", tid)
            continue
        for c in cohorts:
            _add_tokens(names, (c.drug or "").split(";"))
    return names


def _all_trial_drugs(client, *, use_reviewer: bool) -> list[str]:
    """Every distinct drug across ALL ctgov + anzctr trials: CTGov deterministic (armGroups);
    ANZCTR via the drug doer->reviewer agent (parallel)."""
    from aus_trial_universe.agentic.core.workflow import fan_out
    from aus_trial_universe.agentic.tasks.extraction.loaders import (
        load_all_anzctr_trials,
        load_all_ctgov_trials,
    )
    from aus_trial_universe.agentic.tasks.extraction.workflow import extract_anzctr_drugs

    log = logging.getLogger("agentic.drug_ref")
    names: list[str] = []
    ctgov = load_all_ctgov_trials()
    for _nct, _text, cohorts in ctgov:
        for c in (cohorts or []):
            _add_tokens(names, (c.drug or "").split(";"))
    log.info("collected %d distinct CTGov drug(s) from %d trials", len(names), len(ctgov))

    anz = load_all_anzctr_trials()
    log.info("identifying ANZCTR drugs (doer→reviewer) across %d trials …", len(anz))

    def _one(text):  # soft-fail: fan_out re-raises, so a bad trial must not kill collection
        try:
            return extract_anzctr_drugs(client, text, use_reviewer=use_reviewer)
        except Exception as exc:  # noqa: BLE001
            log.info("  ANZCTR drug extraction failed: %s: %s", type(exc).__name__, exc)
            return None

    results = fan_out([(lambda text=text: _one(text)) for _actrn, text, _ in anz])
    before, failed = len(names), 0
    for dr in results:
        if dr is None:
            failed += 1
            continue
        _add_tokens(names, list(dr.intervention_drugs) + list(dr.comparator_drugs))
    log.info("collected %d new distinct ANZCTR drug(s) (%d trials failed); %d distinct drugs total",
             len(names) - before, failed, len(names))
    return names


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build/refresh the drug_ref resource (spec §6.1).")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--drugs", help="';'-separated raw drug names to build.")
    src.add_argument("--from-trials", help="Comma-separated trial ids; pull distinct regime drugs (CTGov).")
    src.add_argument("--all-trials", action="store_true",
                     help="Build over EVERY distinct drug across ALL ctgov + anzctr trials.")
    parser.add_argument("--limit", type=int, default=None, help="Cap the number of drugs (after dedup).")
    parser.add_argument("--refresh-drugs", action="store_true", help="Re-research even drugs already in the resource.")
    parser.add_argument("--refresh-days", type=int, default=None, help="Re-research drugs older than N days.")
    parser.add_argument("--no-review", action="store_true", help="Skip the stage reviewers (cheaper).")
    parser.add_argument("--workers", type=int, default=8,
                        help="Parallel drugs + checkpoint batch size (default 8). Higher = faster, same output "
                             "(may hit API rate limits).")
    parser.add_argument("--model", default=None, help="Override the OpenAI model.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for _n in ("httpx", "openai", "urllib3", "numexpr"):
        logging.getLogger(_n).setLevel(logging.WARNING)
    _load_openai_key()

    from aus_trial_universe.agentic.core.client import LlmClient
    from aus_trial_universe.agentic.tasks.drug_ref.store import DrugRefStore
    from aus_trial_universe.agentic.tasks.drug_ref.workflow import build_drug_ref

    client = LlmClient(model=args.model) if args.model else LlmClient()

    if args.drugs:
        names = [d.strip() for d in args.drugs.split(";") if d.strip()]
    elif args.from_trials:
        names = _drugs_from_trials([x.strip() for x in args.from_trials.split(",") if x.strip()])
    else:  # --all-trials
        names = _all_trial_drugs(client, use_reviewer=not args.no_review)
    if args.limit:
        names = names[: args.limit]
    if not names:
        parser.error("no drug names to build")

    store = DrugRefStore.load()
    log = logging.getLogger("agentic.drug_ref")
    log.info("drug-ref build · %d drug(s) · refresh=%s · review=%s\n", len(names), args.refresh_drugs, not args.no_review)

    summary = build_drug_ref(
        client, names, store,
        refresh=args.refresh_drugs, refresh_days=args.refresh_days,
        use_reviewer=not args.no_review, workers=args.workers,
        checkpoint=lambda: store.save(),   # persist progress after each batch (resilient to interruption)
    )
    vdir = store.save()

    n_alias = sum(len(v) for v in store.aliases.values())
    n_tgt = sum(len(v) for v in store.targets.values())
    n_ind = sum(len(v) for v in store.indications.values())
    print(f"\n{'═' * 70}\ndrug-ref → {vdir}/\n"
          f"  this run:  canonicalized={summary.canonicalized} · reused_alias={summary.reused_alias} · "
          f"non_drug={summary.non_drug} · researched={summary.researched} · reused_ref={summary.reused_ref} · "
          f"failed={summary.failed}\n"
          f"  resource:  {n_alias} alias rows · {len(store.refs)} drugs · {n_tgt} target rows · "
          f"{n_ind} indication rows\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
