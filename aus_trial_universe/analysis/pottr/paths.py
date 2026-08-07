"""Data paths for the POTTR workspace — everything under `data/agentic/analysis/pottr/`.

Derived from `core.paths.ANALYSIS_DIR`, so this workspace relocates with `DATA_ROOT` like everything else and
hard-codes nothing. Nothing here is production data: `analysis/` is a working directory that may be emptied
(see its README), so every file below is either regenerable or a DRAFT awaiting review before it migrates into
a real store.
"""
from __future__ import annotations

from pathlib import Path

from aus_trial_universe.core.paths import ANALYSIS_DIR

#: The workspace root. One subfolder for the whole task — both components write here.
POTTR_WORKSPACE = ANALYSIS_DIR / "pottr"

# --- INPUT: POTTR's own curation, snapshotted so a run is reproducible ------------------------------------- #
POTTR_SOURCE_URL = "https://raw.githubusercontent.com/fpylin/POTTR/master/data/trial_eligibility.AU.tsv"
POTTR_SNAPSHOT = POTTR_WORKSPACE / "pottr_trial_eligibility.AU.tsv"

# --- COMPONENT 2: the disease-derived alteration inference --------------------------------------------------- #
#: 3NF single-key lookup: cancer_type_interpreted -> the derived alteration, its basis, and its finding-model.
DISEASE_DERIVED_MAP = POTTR_WORKSPACE / "disease_derived_alteration.tsv"
#: Only the values that FIRED, for review — the signal is a rounding error in the full table.
DISEASE_DERIVED_HITS = POTTR_WORKSPACE / "disease_derived_alteration_hits.tsv"

# --- COMPONENT 1: the crosswalk + the comparison ------------------------------------------------------------- #
#: The intermediate file the brief asks for: every distinct POTTR term -> our column + our vocabulary value.
POTTR_TERM_CROSSWALK = POTTR_WORKSPACE / "pottr_term_crosswalk.tsv"
#: Trial-grain headline — one row per shared trial, the verdict columns.
COMPARISON_HEADLINE = POTTR_WORKSPACE / "pottr_comparison_by_trial.tsv"
#: Long per-criterion detail the headline aggregates from — what makes a verdict auditable.
COMPARISON_DETAIL = POTTR_WORKSPACE / "pottr_comparison_detail.tsv"
#: Our side, backed out to trial grain and deduplicated on the mapped values (the comparison's left-hand side).
OURS_TRIAL_GRAIN = POTTR_WORKSPACE / "ours_trial_grain.tsv"

RUN_LOG = POTTR_WORKSPACE / "run.log"


def ensure_workspace() -> Path:
    POTTR_WORKSPACE.mkdir(parents=True, exist_ok=True)
    return POTTR_WORKSPACE
