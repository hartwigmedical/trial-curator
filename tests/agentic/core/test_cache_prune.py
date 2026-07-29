"""Tests for the prompt-aware cache prune (aus_trial_universe/agentic/core/cache_prune.py).

No API/network: the cache is a DiskCache in tmp_path and `live` is a hand-built
{name: prompt_sha} map, so classification is deterministic.
"""
from __future__ import annotations

from aus_trial_universe.core.cache_prune import LIVE, STALE, UNKNOWN, classify, prune_cache
from aus_trial_universe.core.client import DiskCache
from aus_trial_universe.core.prompt_registry import live_prompt_shas


def _seed(tmp_path):
    """A cache holding one live, one stale (prompt changed), one removed-agent, one legacy entry."""
    cache = DiskCache(tmp_path)
    cache.set("live", '{"ok":1}', {"name": "extractor", "prompt_sha": "SHA_A", "mode": "parse"})
    cache.set("changed", '{"ok":1}', {"name": "extractor", "prompt_sha": "OLD_SHA", "mode": "parse"})
    cache.set("gone", '{"ok":1}', {"name": "deleted_agent", "prompt_sha": "SHA_X", "mode": "parse"})
    (tmp_path / "legacy.json").write_text('{"raw":"response"}', encoding="utf-8")  # pre-envelope
    return cache


LIVE_MAP = {"extractor": "SHA_A"}


def test_classify():
    assert classify({"name": "extractor", "prompt_sha": "SHA_A"}, LIVE_MAP) == LIVE
    assert classify({"name": "extractor", "prompt_sha": "OLD_SHA"}, LIVE_MAP) == STALE  # prompt changed
    assert classify({"name": "deleted_agent", "prompt_sha": "SHA_X"}, LIVE_MAP) == STALE  # agent removed
    assert classify(None, LIVE_MAP) == UNKNOWN                                            # legacy/untagged


def test_dry_run_counts_but_keeps_files(tmp_path):
    _seed(tmp_path)
    rep = prune_cache(tmp_path, LIVE_MAP, apply=False)
    assert (rep.scanned, rep.live, rep.stale, rep.unknown) == (4, 1, 2, 1)
    assert rep.removed == 2 and not rep.applied
    assert len(list(tmp_path.glob("*.json"))) == 4  # nothing deleted in a dry run


def test_apply_removes_stale_only(tmp_path):
    _seed(tmp_path)
    rep = prune_cache(tmp_path, LIVE_MAP, apply=True)
    assert rep.removed == 2 and rep.applied
    remaining = {p.stem for p in tmp_path.glob("*.json")}
    assert remaining == {"live", "legacy"}  # stale gone; live + legacy kept


def test_purge_unknown_also_drops_legacy(tmp_path):
    _seed(tmp_path)
    rep = prune_cache(tmp_path, LIVE_MAP, apply=True, purge_unknown=True)
    assert rep.removed == 3
    assert {p.stem for p in tmp_path.glob("*.json")} == {"live"}


def test_missing_cache_dir_is_safe(tmp_path):
    rep = prune_cache(tmp_path / "nope", LIVE_MAP, apply=True)
    assert rep.scanned == 0 and rep.removed == 0


def test_live_prompt_shas_covers_both_paths():
    """Smoke: the registry enumerates agents from eligibility AND drug-utility, offline."""
    live = live_prompt_shas()
    assert "eligibility_interpreter" in live and "raw_extractor" in live   # eligibility path (two sub-stages)
    assert "drug_annotator" in live                 # drug-utility path
    assert all(isinstance(v, str) and len(v) == 64 for v in live.values())  # sha256 hex
