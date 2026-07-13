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
            for d in (c.drug or "").split(";"):
                d = d.strip()
                if d and d not in names:
                    names.append(d)
    return names


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build/refresh the drug_ref resource (spec §6.1).")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--drugs", help="';'-separated raw drug names to build.")
    src.add_argument("--from-trials", help="Comma-separated trial ids; pull distinct regime drugs (CTGov).")
    parser.add_argument("--limit", type=int, default=None, help="Cap the number of drugs (after dedup).")
    parser.add_argument("--refresh-drugs", action="store_true", help="Re-research even drugs already in the resource.")
    parser.add_argument("--refresh-days", type=int, default=None, help="Re-research drugs older than N days.")
    parser.add_argument("--no-review", action="store_true", help="Skip the stage reviewers (cheaper).")
    parser.add_argument("--model", default=None, help="Override the OpenAI model.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for _n in ("httpx", "openai", "urllib3", "numexpr"):
        logging.getLogger(_n).setLevel(logging.WARNING)
    _load_openai_key()

    from aus_trial_universe.agentic.core.client import LlmClient
    from aus_trial_universe.agentic.tasks.drug_ref.store import DrugRefStore
    from aus_trial_universe.agentic.tasks.drug_ref.workflow import build_drug_ref

    if args.drugs:
        names = [d.strip() for d in args.drugs.split(";") if d.strip()]
    else:
        names = _drugs_from_trials([x.strip() for x in args.from_trials.split(",") if x.strip()])
    if args.limit:
        names = names[: args.limit]
    if not names:
        parser.error("no drug names to build")

    client = LlmClient(model=args.model) if args.model else LlmClient()
    store = DrugRefStore.load()
    log = logging.getLogger("agentic.drug_ref")
    log.info("drug-ref build · %d drug(s) · refresh=%s · review=%s\n", len(names), args.refresh_drugs, not args.no_review)

    summary = build_drug_ref(
        client, names, store,
        refresh=args.refresh_drugs, refresh_days=args.refresh_days,
        use_reviewer=not args.no_review,
    )
    vdir = store.save()

    n_ind = sum(len(v) for v in store.indications.values())
    print(f"\n{'═' * 70}\ndrug-ref → {vdir}/\n"
          f"  this run:  canonicalized={summary.canonicalized} · reused_alias={summary.reused_alias} · "
          f"non_drug={summary.non_drug} · researched={summary.researched} · reused_ref={summary.reused_ref}\n"
          f"  resource:  {len(store.aliases)} aliases · {len(store.refs)} drugs · {n_ind} indication rows\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
