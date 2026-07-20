"""Deterministic POTTR drug-class lookup (spec §6.1) — offline reuse of the POTTR ontology, no judgement.

``drug_database.txt``:        ``drug|alias|alias<TAB>class;class`` — a drug's POTTR leaf class(es).
``drug_class_hierarchy.txt``: one FULL PATH per line, ``root<TAB>...<TAB>leaf`` (variable length). POTTR is a DAG,
                              so a class can be the leaf of SEVERAL paths (several valid classifications).

For a drug we match its name/alias in the database to its class(es); then for each class we TRACE OUT EVERY
INSTANCE of that class in the hierarchy file — every line that ENDS at the class — as ``root -> ... -> class``.
(Each line is a self-contained path; we do NOT chain edges across lines, which would be ambiguous in a DAG.)
Read in place from the (legacy) drug_ontology raw inputs; cached.

Delimiters (a drug can have several classes; a class name can contain a comma):
- DB: multiple classes are ``;``-separated; comma is PART of a class name (``ALK_inhibitor,first_generation``).
- Output: multiple class-hierarchies / instances are joined by `` | `` (pipe) — deliberately distinct from comma.
"""
from __future__ import annotations

import re
import urllib.request
from collections import defaultdict
from datetime import date
from functools import lru_cache
from pathlib import Path

from aus_trial_universe.agentic.core.paths import (
    CURRENT_VERSION, POTTR_ROOT, archive_current_version, current_version_dir)

_WS_RE = re.compile(r"\s+")

# POTTR is public on GitHub (the only reference dataset we refresh on demand — RxNorm is UMLS-licensed).
POTTR_FILES = ("drug_database.txt", "drug_class_hierarchy.txt")
POTTR_SOURCE_URL = "https://raw.githubusercontent.com/fpylin/POTTR/master/data"
_CLASS_SPLIT_RE = re.compile(r"\s*;\s*")   # multiple classes are ';'-separated; comma stays INSIDE a class name


def _norm(name: str) -> str:
    return _WS_RE.sub(" ", (name or "").strip().lower())


def _parse_dir(vdir: Path):
    """Parse a POTTR version dir -> (alias -> [leaf classes], leaf class -> [full paths], node -> [full paths])."""
    alias_to_classes: dict[str, list[str]] = {}
    for line in (vdir / "drug_database.txt").read_text(encoding="utf-8").splitlines():
        if "\t" not in line:
            continue
        names, classes = line.split("\t", 1)
        cls = [c.strip() for c in _CLASS_SPLIT_RE.split(classes) if c.strip()]
        for alias in names.split("|"):
            key = _norm(alias)
            if key and cls:
                alias_to_classes.setdefault(key, cls)

    leaf_to_paths: dict[str, list[tuple]] = defaultdict(list)
    node_to_paths: dict[str, list[tuple]] = defaultdict(list)
    for line in (vdir / "drug_class_hierarchy.txt").read_text(encoding="utf-8").splitlines():
        nodes = tuple(n.strip() for n in line.split("\t") if n.strip())
        if not nodes:
            continue
        leaf_to_paths[nodes[-1]].append(nodes)
        for n in nodes:
            node_to_paths[n].append(nodes)
    return alias_to_classes, dict(leaf_to_paths), dict(node_to_paths)


@lru_cache(maxsize=1)
def _load():
    return _parse_dir(current_version_dir(POTTR_ROOT))


def _class_paths(cls: str, leaf_to_paths: dict, node_to_paths: dict) -> list[str]:
    """Every hierarchy path for a class: the line(s) that END at it (``root -> ... -> cls``). If it never appears
    as a leaf, the prefix up to it in each containing line; if absent from the hierarchy, the bare class name.
    A degenerate single-node instance is dropped when a fuller path for the same class exists."""
    if cls in leaf_to_paths:
        outs = list(dict.fromkeys(leaf_to_paths[cls]))
    elif cls in node_to_paths:
        outs = list(dict.fromkeys(tuple(p[: p.index(cls) + 1]) for p in node_to_paths[cls]))
    else:
        outs = [(cls,)]
    if any(len(p) > 1 for p in outs):
        outs = [p for p in outs if len(p) > 1]
    return [" -> ".join(p) for p in outs]


def _hierarchy(name: str, alias_to_classes: dict, leaf_to_paths: dict, node_to_paths: dict) -> str:
    classes = alias_to_classes.get(_norm(name))
    if not classes:
        return ""
    paths: list[str] = []
    for cls in classes:
        paths.extend(_class_paths(cls, leaf_to_paths, node_to_paths))
    return " | ".join(dict.fromkeys(paths))


def pottr_class_for(name: str) -> str:
    """POTTR class hierarchy(ies) for a drug/alias; "" if not in POTTR. Classes / instances joined by ' | '."""
    return _hierarchy(name, *_load())


def pottr_class_for_any(names) -> str:
    """Try the canonical name then each alias IN ORDER; return the first non-empty match ("" if none in POTTR)."""
    a2c, l2p, n2p = _load()
    for name in names:
        hit = _hierarchy(name, a2c, l2p, n2p)
        if hit:
            return hit
    return ""


# --------------------------------------------------------------------------- #
# Refresh — download the current POTTR files from GitHub (archive-on-refresh)
# --------------------------------------------------------------------------- #
def _http_get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as resp:   # noqa: S310 — public POTTR data over https
        return resp.read()


def _archive_label(version_dir: Path) -> str:
    """A label for archiving a superseded version — its recorded download/snapshot date (from SOURCE.txt), else
    a generic marker."""
    src = version_dir / "SOURCE.txt"
    if src.exists():
        for line in src.read_text(encoding="utf-8").splitlines():
            for key in ("downloaded:", "snapshot_version:"):
                if line.strip().startswith(key):
                    return line.split(":", 1)[1].strip().replace("-", "") or "previous"
    return "previous"


def refresh_pottr(*, root: Path = POTTR_ROOT, on: date | None = None, fetch=None) -> Path:
    """Download the current POTTR files from GitHub into ``root/current_version/``, moving the previous version to
    ``root/archive/<its-date>/`` first. `fetch(url)->bytes` is injectable for testing (defaults to an https GET).
    Records the download date + source in SOURCE.txt and clears the in-process cache. Returns the new version dir."""
    fetch = fetch or _http_get
    if (root / CURRENT_VERSION).exists():
        archive_current_version(root, _archive_label(root / CURRENT_VERSION))
    cur = root / CURRENT_VERSION
    cur.mkdir(parents=True, exist_ok=True)
    for fname in POTTR_FILES:
        (cur / fname).write_bytes(fetch(f"{POTTR_SOURCE_URL}/{fname}"))
    stamp = (on or date.today()).isoformat()
    (cur / "SOURCE.txt").write_text(
        f"source: {POTTR_SOURCE_URL}\ndownloaded: {stamp}\nfiles: {', '.join(POTTR_FILES)}\n", encoding="utf-8")
    _load.cache_clear()
    return cur


if __name__ == "__main__":   # `make drug-ref-refresh-pottr`
    import logging
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    vdir = refresh_pottr()
    logging.getLogger("agentic.drug_ref").info("POTTR refreshed → %s (%d classes)", vdir, len(_load()[1]))
