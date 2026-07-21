"""Central data-root configuration for the agentic pipeline (spec §6.1).

`DATA_ROOT` is a **TEMPORARY** designation (`data/agentic/`) — the v2 agentic pipeline is intended to replace and
retire the legacy `data/drug_utility_path/` and `data/eligibility_path/` trees. Every data path derives from the
single `DATA_ROOT` constant here, so promotion to the top-level `data/` later is a one-line change.

Layout (see docs/agentic/drug_ref_schema.md):
    <DATA_ROOT>/
      trial_universe/{ctgov,anzctr}/                 INPUT  trials
      resources/drug_utility/{pottr,rxnorm}/         INPUT  reference data — current_version/ + archive/
      resources/eligibility/oncotree/                INPUT  reference data — current_version/ + archive/
      drug_annotations/                              OUTPUT the 5 relational tables — current_version/ + archive/
      eligibility/                                   OUTPUT per-run regime/eligibility/combined
      log/  analysis/                                operational

Versioned datasets (resources + drug_annotations) use a fixed `current_version/` folder for the live data and
`archive/` for superseded versions; the exact date lives in a metadata file inside the version dir. (Trial inputs
keep their own `version_<ddmmyyyy>/` dirs, managed by the download pipeline and read via `latest_version_dir`.)
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

_TIMESTAMP_RE = re.compile(r"^\d{8}_\d{6}$")

# The single relocatable root. Promote to `REPO_ROOT / "data"` when the legacy trees are removed.
DATA_ROOT = REPO_ROOT / "data/agentic"

# --- INPUT: trials ---------------------------------------------------------- #
TRIAL_UNIVERSE = DATA_ROOT / "trial_universe"
CTGOV_ROOT = TRIAL_UNIVERSE / "ctgov"
ANZCTR_ROOT = TRIAL_UNIVERSE / "anzctr"

# --- INPUT: external reference data (versioned; current_version/ + archive/) - #
RESOURCES = DATA_ROOT / "resources"
POTTR_ROOT = RESOURCES / "drug_utility/pottr"
RXNORM_ROOT = RESOURCES / "drug_utility/rxnorm"
ONCOTREE_ROOT = RESOURCES / "eligibility/oncotree"

# --- OUTPUT ----------------------------------------------------------------- #
DRUG_ANNOTATIONS_ROOT = DATA_ROOT / "drug_annotations"
ELIGIBILITY_OUTPUT = DATA_ROOT / "eligibility"

# --- operational ------------------------------------------------------------ #
LOG_DIR = DATA_ROOT / "log"
ANALYSIS_DIR = DATA_ROOT / "analysis"
CACHE_DIR = DATA_ROOT / "cache"   # LLM response DiskCache (run-to-run reuse); safe to wipe (make agentic-clean)

CURRENT_VERSION = "current_version"
ELIG_CURRENT_OUTPUT = "current_output"   # eligibility live store (current/archive pattern; parallels current_version)
ARCHIVE = "archive"


def current_version_dir(root: Path) -> Path:
    """The live version dir under a versioned resource/output root (``root/current_version``).

    Raises FileNotFoundError if absent (mirrors `latest_version_dir` so first-build handling is unchanged)."""
    d = Path(root) / CURRENT_VERSION
    if not d.exists():
        raise FileNotFoundError(f"no {CURRENT_VERSION}/ under {root}")
    return d


def latest_snapshot_dir(root: Path) -> Path | None:
    """Newest timestamped snapshot subdir (``<YYYYMMDD_HHMMSS>``) under a per-run output root, or None.

    Interim accumulating-store convention (eligibility): each run writes a fresh full-state snapshot into a new
    timestamp dir, and the newest one is the current state. Lexicographic max works — the timestamps sort by time.
    (Promotable to the ``current_version/`` + ``archive/`` pattern at finalization, like the versioned resources.)"""
    root = Path(root)
    if not root.exists():
        return None
    subs = [d for d in root.iterdir() if d.is_dir() and _TIMESTAMP_RE.match(d.name)]
    return max(subs, key=lambda d: d.name) if subs else None


def archive_current_version(root: Path, label: str) -> Path | None:
    """Move ``root/current_version`` → ``root/archive/<label>/`` (archive-on-refresh). Returns the archive path,
    or None if there was no current version to archive."""
    cur = Path(root) / CURRENT_VERSION
    if not cur.exists():
        return None
    dest = Path(root) / ARCHIVE / label
    dest.parent.mkdir(parents=True, exist_ok=True)
    cur.rename(dest)
    return dest
