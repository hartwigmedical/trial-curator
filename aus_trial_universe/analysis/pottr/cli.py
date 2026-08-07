"""CLI for the POTTR workspace: `python -m aus_trial_universe.analysis.pottr <command>`.

Commands run independently and in order:

    infer       COMPONENT 2 — cancer_type -> derived alteration -> finding model   (LLM; writes the lookup)
    crosswalk   COMPONENT 1a — POTTR's terms -> our controlled vocabularies        (LLM; writes the crosswalk)
    compare     COMPONENT 1b — the two curations, over the shared trials           (LLM for the judgement calls)

Everything lands under `data/agentic/analysis/pottr/`. Nothing here writes to a production store.
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
from pathlib import Path

from aus_trial_universe.analysis.pottr import paths as P
from aus_trial_universe.core.client import DiskCache, LlmClient
from aus_trial_universe.core.paths import CACHE_DIR, EXPORT_FILE, EXPORT_ROOT
from aus_trial_universe.core.logfmt import stage

logger = logging.getLogger("agentic.pottr")

SET_A = EXPORT_ROOT / EXPORT_FILE


def _setup_logging(verbose: bool = True) -> None:
    P.ensure_workspace()
    fmt = logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO if verbose else logging.WARNING)
    for h in list(root.handlers):
        root.removeHandler(h)
    fh = logging.FileHandler(P.RUN_LOG, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(fh)
    root.addHandler(sh)


def _client(args) -> LlmClient:
    cache = None if args.no_cache else DiskCache(CACHE_DIR)
    kw = dict(cache=cache, max_concurrency=args.max_concurrency)
    return LlmClient(model=args.model, **kw) if args.model else LlmClient(**kw)


def load_export(path: Path = SET_A) -> list[dict]:
    """The Set-A export — the single source both components read, so the two sides cannot diverge."""
    csv.field_size_limit(10 ** 9)
    if not path.exists():
        raise SystemExit(f"export not found: {path}\nRun `make agentic-export` first.")
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


# --------------------------------------------------------------------------- #
# infer — COMPONENT 2
# --------------------------------------------------------------------------- #
def cmd_infer(args) -> int:
    from aus_trial_universe.analysis.pottr.disease_inference import (
        infer_all, map_derived_to_finding_model, read_map, write_map,
    )

    rows = load_export()
    values = [r["cancer_type_interpreted"] for r in rows]
    logger.info(stage("COMPONENT 2 · disease-derived gene alteration"))
    logger.info("export rows %d · distinct cancer types %d", len(rows), len(set(values)))

    if args.only:
        wanted = {v.strip() for v in args.only.split("||") if v.strip()}
        values = [v for v in values if v.strip() in wanted]
        logger.info("--only · restricted to %d value(s)", len(set(values)))

    done = {} if args.refresh else read_map(P.DISEASE_DERIVED_MAP)
    if done:
        logger.info("lookup-first · %d value(s) already inferred; reusing", len(done))
    todo = [v for v in values if v.strip() and v.strip() not in done]
    if args.limit:
        seen, capped = set(), []
        for v in todo:
            if v.strip() not in seen:
                seen.add(v.strip())
                capped.append(v)
            if len(seen) >= args.limit:
                break
        todo = capped
        logger.info("--limit · capped to %d distinct new value(s)", len(seen))

    fresh = infer_all(_client(args), todo, max_attempts=args.max_attempts,
                      use_reviewer=not args.no_review, workers=args.workers)
    fresh = map_derived_to_finding_model(_client(args), fresh, max_attempts=args.max_attempts,
                                         use_reviewer=not args.no_review, workers=args.workers)

    merged = dict(done)
    for r in fresh:
        merged[r.cancer_type] = r
    all_rows = list(merged.values())
    write_map(all_rows, P.DISEASE_DERIVED_MAP)
    write_map(all_rows, P.DISEASE_DERIVED_HITS, hits_only=True)
    hits = [r for r in all_rows if r.derived_alteration]
    logger.info("")
    logger.info("wrote %s  (%d rows)", P.DISEASE_DERIVED_MAP, len(all_rows))
    logger.info("wrote %s  (%d fired · %.1f%%)", P.DISEASE_DERIVED_HITS, len(hits),
                100.0 * len(hits) / max(1, len(all_rows)))
    return 0


# --------------------------------------------------------------------------- #
# crosswalk / compare — COMPONENT 1
# --------------------------------------------------------------------------- #
def cmd_crosswalk(args) -> int:
    from aus_trial_universe.analysis.pottr.crosswalk import build_crosswalk
    logger.info(stage("COMPONENT 1a · POTTR term crosswalk"))
    build_crosswalk(_client(args), workers=args.workers, max_attempts=args.max_attempts,
                    use_reviewer=not args.no_review, refresh=args.refresh)
    return 0


def cmd_compare(args) -> int:
    from aus_trial_universe.analysis.pottr.compare import run_comparison
    logger.info(stage("COMPONENT 1b · POTTR vs ours"))
    run_comparison(_client(args), load_export(), workers=args.workers,
                   adjudicate_diffs=not args.no_adjudicate)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m aus_trial_universe.analysis.pottr", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp):
        sp.add_argument("--workers", type=int, default=8, help="concurrent values (default 8)")
        sp.add_argument("--max-concurrency", type=int, default=None, help="global API cap (TPM-bound)")
        sp.add_argument("--max-attempts", type=int, default=3, help="refine cap per value (default 3)")
        sp.add_argument("--model", default=None)
        sp.add_argument("--no-cache", action="store_true")
        sp.add_argument("--no-review", action="store_true", help="doer only; skip the reviewer")
        sp.add_argument("--refresh", action="store_true", help="ignore existing output and redo every value")
        return sp

    sp = common(sub.add_parser("infer", help="COMPONENT 2 — disease-derived gene alteration"))
    sp.add_argument("--limit", type=int, default=0, help="cap the number of NEW distinct values (smoke test)")
    sp.add_argument("--only", default="", help="'||'-separated exact cancer-type values to run")
    sp.set_defaults(func=cmd_infer)

    common(sub.add_parser("crosswalk", help="COMPONENT 1a — POTTR terms -> our vocabularies")).set_defaults(
        func=cmd_crosswalk)

    sp = common(sub.add_parser("compare", help="COMPONENT 1b — the comparison"))
    sp.add_argument("--no-adjudicate", action="store_true", help="skip the who-is-wrong verdict (cheaper)")
    sp.set_defaults(func=cmd_compare)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging()
    return args.func(args)
