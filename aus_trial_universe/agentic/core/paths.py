"""Central data-root configuration for the agentic pipeline (spec §6.1).

`DATA_ROOT` is a **TEMPORARY** designation (`data/agentic/`) — the v2 agentic pipeline is intended to replace and
retire the legacy `data/drug_utility_path/` and `data/eligibility_path/` trees. Every data path derives from the
single `DATA_ROOT` constant here, so promotion to the top-level `data/` later is a one-line change.

Layout (grouped by ROLE; see docs/agentic/drug_ref_schema.md):
    <DATA_ROOT>/
      inputs/                                        INPUT — external data we ingest (read-only to the pipeline)
        trial_universe/{ctgov,anzctr}/               trials
        resources/drug_utility/{pottr,rxnorm}/       reference data — current_version/ + archive/
        resources/eligibility/oncotree/              reference data — current_version/ + archive/
      masters/                                       OUTPUT — produced, VERSIONED stores (current_version/ + archive/)
        trial_arms/                                  the SHARED arm registry (FK target)
        trial_info/                                  trial-level metadata master
        drug_annotations/                            the 6 relational tables + 2 approval vocab maps
        eligibility/                                 the pure-3NF eligibility store
      derived/                                       OUTPUT — regenerable, NON-versioned flat views/deliverables
        joined/{eligibility,drug_annotations}/       denormalized review views
        export/                                      the matching-engine deliverable (Set A + MANIFEST)
      transient/                                     wipeable operational (safe to delete — make agentic-clean)
        cache/  log/
      analysis/                                      ad-hoc analysis workspace
      demo/                                          isolated live-demo sandbox (self-contained; re-roots here)

Versioned stores (everything under masters/ + the input resources) use a fixed `current_version/` folder for the
live data and `archive/` for superseded versions; the exact date lives in a metadata file inside the version dir.
(Trial inputs keep their own `version_<ddmmyyyy>/` dirs, managed by the download pipeline and read via
`latest_version_dir`.)
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

_TIMESTAMP_RE = re.compile(r"^\d{8}_\d{6}$")

# The single relocatable root. Promote to `REPO_ROOT / "data"` when the legacy trees are removed.
DATA_ROOT = REPO_ROOT / "data/agentic"

# --- role buckets (the top-level grouping; every leaf derives from one of these) --------------------------- #
INPUTS = DATA_ROOT / "inputs"          # external data we ingest (read-only to the pipeline)
MASTERS = DATA_ROOT / "masters"        # produced, VERSIONED stores (current_version/ + archive/)
DERIVED = DATA_ROOT / "derived"        # regenerable, NON-versioned flat views/deliverables
TRANSIENT = DATA_ROOT / "transient"    # wipeable operational (cache, log)

# --- INPUT: trials ---------------------------------------------------------- #
TRIAL_UNIVERSE = INPUTS / "trial_universe"
CTGOV_ROOT = TRIAL_UNIVERSE / "ctgov"
ANZCTR_ROOT = TRIAL_UNIVERSE / "anzctr"

# --- INPUT: external reference data (versioned; current_version/ + archive/) - #
RESOURCES = INPUTS / "resources"
POTTR_ROOT = RESOURCES / "drug_utility/pottr"
RXNORM_ROOT = RESOURCES / "drug_utility/rxnorm"
ONCOTREE_ROOT = RESOURCES / "eligibility/oncotree"

# --- MASTERS: the produced, versioned stores -------------------------------- #
DRUG_ANNOTATIONS_ROOT = MASTERS / "drug_annotations"
ELIGIBILITY_OUTPUT = MASTERS / "eligibility"
# The SHARED arm registry (`trial_arms`): the central table both paths link to by `trial_arm_id`. Versioned
# (current_version/ + archive/) like the other masters; written by whichever path processes a trial.
TRIAL_ARMS_ROOT = MASTERS / "trial_arms"

# Trial-level metadata master (`trial_info`), one row per trialId, derived deterministically from the raw
# CTGov protocolSection / ANZCTR rows. Feeds the matching-engine export (Set A). Versioned like the other masters.
TRIAL_INFO_ROOT = MASTERS / "trial_info"
TRIAL_INFO_FILE = "trial_info.tsv"

# --- DERIVED: regenerable flat outputs -------------------------------------- #
# The matching-engine EXPORT (deliverable): Set A = the wide flat `trial_eligibility.tsv`; Set B = the drug tables
# (referenced in place at masters/drug_annotations/current_version/). `export/snapshot_<ts>/` holds an immutable,
# self-contained bundle (Set A + a frozen copy of the drug tables) minted on demand for hand-off.
EXPORT_ROOT = DERIVED / "export"
EXPORT_FILE = "trial_eligibility.tsv"

# DENORMALIZED / JOINED views (mapped_eligibility, finalised_*, mapped_drug_regulatory_approval) are NOT 3NF
# masters, so they live under `derived/joined/` — keeping the masters/ stores strictly 3NF. `joined/` is split per
# producing subsystem into `eligibility/` and `drug_annotations/` subfolders. Single overwritten files, regenerable
# from the 3NF tables; not versioned.
JOINED_ROOT = DERIVED / "joined"
JOINED_ELIGIBILITY = "eligibility"          # joined/eligibility/  — the eligibility flat views
JOINED_DRUG = "drug_annotations"            # joined/drug_annotations/ — the drug-approval flat view
COMBINED = "combined"                       # legacy name kept for back-compat; combined.tsv now lands in JOINED_ROOT
COMBINED_OUTPUT = JOINED_ROOT
COMBINED_FILE = "combined.tsv"
MAPPED_ELIGIBILITY_FILE = "mapped_eligibility.tsv"          # Step-1 flat view (interpreted ⋈ maps) -> joined/eligibility/
FINALISED_MAPPED_ELIGIBILITY_FILE = "finalised_mapped_eligibility.tsv"   # Step-2 flat view (+ *_FINAL cols) -> joined/eligibility/
MAPPED_APPROVALS_FILE = "mapped_drug_regulatory_approval.tsv"  # drug approvals ⋈ vocab maps -> joined/drug_annotations/
# The Step-2 finalised map-table set (Step-1 cols + a `*_FINAL` col). These are still 3NF single-key LOOKUP tables
# (same kind as the Step-1 maps), so they live in the 3NF store (current_output/), NOT in joined/.
FINALISED_MAP_FILES = {
    "cancer_type_map": "finalised_cancer_type_map.tsv",
    "gene_alteration_map": "finalised_gene_alteration_map.tsv",
    "molecular_signature_map": "finalised_molecular_signature_map.tsv",
}

# --- TRANSIENT + operational ------------------------------------------------ #
LOG_DIR = TRANSIENT / "log"
CACHE_DIR = TRANSIENT / "cache"   # LLM response DiskCache (run-to-run reuse); safe to wipe (make agentic-clean)
ANALYSIS_DIR = DATA_ROOT / "analysis"   # ad-hoc analysis workspace (top-level, not a role bucket)

CURRENT_VERSION = "current_version"   # the live version folder for EVERY versioned store (masters/ + input resources)
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
