"""Prune the LLM response cache of entries produced by OUTDATED prompts.

Each cache entry records provenance: the agent `name` and a `prompt_sha` (sha256 of
that agent's `instructions`). This tool compares every entry against the CURRENT live
prompts (`prompt_registry.live_prompt_shas`) and classifies it:

  live     - (name, prompt_sha) matches a current agent                 -> keep
  stale    - the agent still exists but its prompt changed, OR the        -> REMOVE
             agent no longer exists (both mean the entry is an outdated prompt)
  unknown  - legacy/untagged entry (written before the envelope format)   -> keep,
             unless --purge-unknown

Dry-run by default; `--apply` (or `apply=True`) actually deletes. One physical cache
serves BOTH the eligibility and drug-utility paths, so this covers both at once.

Usage:
    python -m aus_trial_universe.core.cache_prune            # dry-run report
    python -m aus_trial_universe.core.cache_prune --apply    # delete stale
    python -m aus_trial_universe.core.cache_prune --apply --purge-unknown
"""
from __future__ import annotations

import argparse
import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from aus_trial_universe.core.client import read_cache_meta
from aus_trial_universe.core.paths import CACHE_DIR
from aus_trial_universe.core.prompt_registry import live_prompt_shas

logger = logging.getLogger(__name__)

LIVE, STALE, UNKNOWN = "live", "stale", "unknown"


@dataclass
class PruneReport:
    scanned: int = 0
    live: int = 0
    stale: int = 0
    unknown: int = 0
    removed: int = 0
    bytes_freed: int = 0
    applied: bool = False
    purge_unknown: bool = False
    stale_by_agent: Counter = field(default_factory=Counter)  # label -> count


def classify(meta: dict | None, live: dict[str, str]) -> str:
    """live | stale | unknown for one entry's provenance meta."""
    if not meta or "prompt_sha" not in meta:
        return UNKNOWN
    current = live.get(meta.get("name"))
    if current is None:
        return STALE  # agent removed / renamed
    return LIVE if current == meta.get("prompt_sha") else STALE


def _stale_label(meta: dict, live: dict[str, str]) -> str:
    name = meta.get("name") or "<unnamed>"
    return name if name in live else f"{name} (removed)"


def prune_cache(
    cache_dir: str | Path = CACHE_DIR,
    live: dict[str, str] | None = None,
    *,
    apply: bool = False,
    purge_unknown: bool = False,
) -> PruneReport:
    """Scan `cache_dir`, classify every `*.json` entry, and (if apply) delete outdated ones."""
    cache_dir = Path(cache_dir)
    live = live if live is not None else live_prompt_shas()
    rep = PruneReport(applied=apply, purge_unknown=purge_unknown)
    if not cache_dir.exists():
        return rep

    for path in cache_dir.glob("*.json"):
        rep.scanned += 1
        meta = read_cache_meta(path)
        verdict = classify(meta, live)
        setattr(rep, verdict, getattr(rep, verdict) + 1)
        remove = verdict == STALE or (verdict == UNKNOWN and purge_unknown)
        if not remove:
            continue
        rep.stale_by_agent[_stale_label(meta, live) if verdict == STALE else "<unknown/legacy>"] += 1
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        if apply:
            try:
                path.unlink()
            except OSError:
                continue
        rep.removed += 1
        rep.bytes_freed += size
    return rep


def summary_line(rep: PruneReport) -> str:
    verb = "removed" if rep.applied else "would remove"
    kb = rep.bytes_freed / 1024
    return (f"cache-prune · scanned={rep.scanned} live={rep.live} stale={rep.stale} "
            f"unknown={rep.unknown} · {verb} {rep.removed} ({kb:.0f} KB)")


def _print_report(rep: PruneReport) -> None:
    logger.info(summary_line(rep))
    for label, n in rep.stale_by_agent.most_common():
        logger.info("  - %-40s %d", label, n)
    if not rep.applied and rep.removed:
        logger.info("(dry-run — re-run with --apply to delete)")
    if rep.unknown and not rep.purge_unknown:
        logger.info("(%d legacy/untagged entries kept; --purge-unknown to drop them)", rep.unknown)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prune the LLM response cache of outdated-prompt entries.")
    parser.add_argument("--cache-dir", default=str(CACHE_DIR), help="Cache directory (default: data/agentic/cache/).")
    parser.add_argument("--apply", action="store_true", help="Actually delete (default: dry-run report).")
    parser.add_argument("--purge-unknown", action="store_true",
                        help="Also drop legacy/untagged entries (pre-provenance format).")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    rep = prune_cache(args.cache_dir, apply=args.apply, purge_unknown=args.purge_unknown)
    _print_report(rep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
