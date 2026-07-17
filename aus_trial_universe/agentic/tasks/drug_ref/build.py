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


def _collect(names: list[str], occ: list[tuple[str, str, str]], trial_id: str, registry: str, tokens) -> list[str]:
    """Record each drug token as a global distinct name AND as a (trial, registry, name) occurrence (provenance).
    Returns this trial's distinct tokens (for the per-trial log line — the traceability the aggregate count lost)."""
    seen: list[str] = []
    for d in tokens:
        d = d.strip()
        if not d:
            continue
        if d not in names:
            names.append(d)
        occ.append((trial_id, registry, d))
        if d not in seen:
            seen.append(d)
    return seen


def _drugs_from_trials(ids: list[str]) -> tuple[list[str], list[tuple[str, str, str]]]:
    """(distinct names, occurrences) from CTGov trials (ANZCTR drugs are LLM-derived at run time -> skipped)."""
    from aus_trial_universe.agentic.tasks.extraction.loaders import load_trials

    log = logging.getLogger("agentic.drug_ref")
    names: list[str] = []
    occ: list[tuple[str, str, str]] = []
    for source, tid, _text, cohorts in load_trials(ids=ids):
        if cohorts is None:
            log.warning("skip %s: ANZCTR drugs are extracted during a run, not available for --from-trials", tid)
            continue
        toks = _collect(names, occ, tid, source, [d for c in cohorts for d in (c.drug or "").split(";")])
        log.info("  [%s] %s → %s", source, tid, "; ".join(toks) or "(none)")
    return names, occ


def _all_trial_drugs(client, *, use_reviewer: bool) -> tuple[list[str], list[tuple[str, str, str]]]:
    """(distinct names, occurrences) across ALL ctgov + anzctr trials: CTGov deterministic (armGroups);
    ANZCTR via the drug doer->reviewer agent (parallel). Every drug is attributed to its trial + registry in
    `occurrences` (the trial_to_intervention provenance) and logged per trial (traceability)."""
    from aus_trial_universe.agentic.core.workflow import fan_out
    from aus_trial_universe.agentic.tasks.extraction.loaders import (
        load_all_anzctr_trials,
        load_all_ctgov_trials,
    )
    from aus_trial_universe.agentic.tasks.extraction.workflow import extract_anzctr_drugs

    log = logging.getLogger("agentic.drug_ref")
    names: list[str] = []
    occ: list[tuple[str, str, str]] = []
    ctgov = load_all_ctgov_trials()
    for nct, _text, cohorts in ctgov:
        toks = _collect(names, occ, nct, "ctgov", [d for c in (cohorts or []) for d in (c.drug or "").split(";")])
        log.info("  [ctgov] %s → %s", nct, "; ".join(toks) or "(none)")
    log.info("collected %d distinct CTGov drug(s) from %d trials", len(names), len(ctgov))

    anz = load_all_anzctr_trials()
    log.info("identifying ANZCTR drugs (doer→reviewer) across %d trials …", len(anz))

    def _one(text):  # soft-fail: fan_out re-raises, so a bad trial must not kill collection
        try:
            return extract_anzctr_drugs(client, text, use_reviewer=use_reviewer)
        except Exception as exc:  # noqa: BLE001
            return exc

    results = fan_out([(lambda text=text: _one(text)) for _actrn, text, _ in anz])
    before, failed = len(names), 0
    for (actrn, _text, _), dr in zip(anz, results):          # fan_out preserves order -> align to trials
        if isinstance(dr, Exception) or dr is None:
            failed += 1
            log.info("  [anzctr] %s → (extraction failed: %s)", actrn,
                     type(dr).__name__ if isinstance(dr, Exception) else "none")
            continue
        toks = _collect(names, occ, actrn, "anzctr", list(dr.intervention_drugs) + list(dr.comparator_drugs))
        log.info("  [anzctr] %s → %s", actrn, "; ".join(toks) or "(none)")
    log.info("collected %d new distinct ANZCTR drug(s) (%d trials failed); %d distinct drugs total",
             len(names) - before, failed, len(names))
    return names, occ


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

    occurrences: list[tuple[str, str, str]] = []
    if args.drugs:
        names = [d.strip() for d in args.drugs.split(";") if d.strip()]
    elif args.from_trials:
        names, occurrences = _drugs_from_trials([x.strip() for x in args.from_trials.split(",") if x.strip()])
    else:  # --all-trials
        names, occurrences = _all_trial_drugs(client, use_reviewer=not args.no_review)
    if args.limit:
        names = names[: args.limit]
        keep = set(names)
        occurrences = [o for o in occurrences if o[2] in keep]
    if not names:
        parser.error("no drug names to build")

    store = DrugRefStore.load()
    log = logging.getLogger("agentic.drug_ref")
    log.info("drug-ref build · %d drug(s) · refresh=%s · review=%s\n", len(names), args.refresh_drugs, not args.no_review)

    summary = build_drug_ref(
        client, names, store, occurrences=occurrences,
        refresh=args.refresh_drugs, refresh_days=args.refresh_days,
        use_reviewer=not args.no_review, workers=args.workers,
        checkpoint=lambda: store.save(),   # persist progress after each batch (resilient to interruption)
    )
    vdir = store.save()

    n_map = sum(len(v) for v in store.mappings.values())
    n_tgt = sum(len(v) for v in store.targets.values())
    n_ind = sum(len(v) for v in store.indications.values())
    print(f"\n{'═' * 70}\ndrug-ref → {vdir}/\n"
          f"  this run:  canonicalized={summary.canonicalized} · reused_alias={summary.reused_alias} · "
          f"non_drug={summary.non_drug} · researched={summary.researched} · reused_ref={summary.reused_ref} · "
          f"failed={summary.failed}\n"
          f"  resource:  {n_map} mapping rows · {len(store.occurrences)} trial-links · {len(store.refs)} drugs · "
          f"{n_tgt} target rows · {n_ind} indication rows\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
