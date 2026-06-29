"""Move curated trial `.py` files that are no longer in the latest download.

When the latest staged download (the merged ``03`` input) no longer contains a
trial that still has a curated ``.py`` file, that file is moved into an
``expired_trials/`` subfolder rather than deleted.  Because curated files are
discovered by a non-recursive top-level glob, files under that subfolder are
automatically excluded from all downstream processing.

POTTR-listed trials are never expired (they must always reach the final output).
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, List

from aus_trial_universe.eligibility_path.shared.utils.curated_files import (
    iter_curated_py_files,
)

LOGGER = logging.getLogger(__name__)

DEFAULT_EXPIRED_SUBDIR = "expired_trials"


@dataclass(frozen=True)
class ExpiryResult:
    moved: List[str]
    retained_pottr: List[str]
    expired_dir: Path
    restored: List[str] = field(default_factory=list)
    # True when the safety guard tripped and the expiry was skipped wholesale
    # (the latest download looked incomplete); nothing was moved.
    guarded: bool = False


def move_expired_curations(
    *,
    curated_dir: Path,
    current_trial_ids: Iterable[str],
    pottr_exempt_ids: Iterable[str],
    normalize_trial_id: Callable[[object], str],
    trial_id_prefix: str,
    expired_subdir_name: str = DEFAULT_EXPIRED_SUBDIR,
    max_expiry_fraction: float | None = None,
) -> ExpiryResult:
    """Reconcile curated ``{PREFIX}*.py`` files against the latest download.

    Three things happen, in order:

    * **Restore** — a previously-expired file whose trial re-appears in
      ``current_trial_ids`` is moved back out of ``expired_subdir_name`` so it is
      processed again instead of stranded (skipped if an active curation of the
      same name already exists).
    * **Safety guard** — when ``max_expiry_fraction`` is set and expiry would
      remove more than that fraction of the active curations, the expiry is
      skipped wholesale (``guarded=True``, nothing moved).  This protects against
      a network blip / partial download whose short merged set would otherwise
      expire real trials; it self-heals on the next complete download.
    * **Expire** — a curated file whose trial id is absent from
      ``current_trial_ids`` and not in ``pottr_exempt_ids`` is moved (not
      deleted) into ``curated_dir/expired_subdir_name``.
    """
    current = {normalize_trial_id(value) for value in current_trial_ids}
    pottr = {normalize_trial_id(value) for value in pottr_exempt_ids}

    expired_dir = curated_dir / expired_subdir_name

    # 1. Restore any expired curation whose trial is back in the latest download.
    restored: List[str] = []
    if expired_dir.exists():
        for py_path in iter_curated_py_files(expired_dir, trial_id_prefix=trial_id_prefix):
            trial_id = normalize_trial_id(py_path.stem)
            if trial_id not in current:
                continue
            destination = curated_dir / py_path.name
            if destination.exists():
                # An active curation already exists; leave the expired copy be.
                continue
            shutil.move(str(py_path), str(destination))
            restored.append(trial_id)

    # 2. Determine expiry candidates among the (post-restore) active curations.
    active_count = 0
    candidates: List[tuple[str, Path]] = []
    retained: List[str] = []
    for py_path in iter_curated_py_files(curated_dir, trial_id_prefix=trial_id_prefix):
        active_count += 1
        trial_id = normalize_trial_id(py_path.stem)
        if trial_id in current:
            continue
        if trial_id in pottr:
            retained.append(trial_id)
            continue
        candidates.append((trial_id, py_path))

    # 3. Safety guard against an incomplete download wiping out real curations.
    guarded = False
    if (
        max_expiry_fraction is not None
        and active_count > 0
        and len(candidates) > max_expiry_fraction * active_count
    ):
        LOGGER.warning(
            "Skipping expiry in %s: %d of %d curation(s) (%.0f%%) would expire, "
            "above the %.0f%% safety threshold — treating the latest download as "
            "incomplete.",
            curated_dir,
            len(candidates),
            active_count,
            100 * len(candidates) / active_count,
            100 * max_expiry_fraction,
        )
        candidates = []
        guarded = True

    moved: List[str] = []
    for trial_id, py_path in candidates:
        expired_dir.mkdir(parents=True, exist_ok=True)
        destination = expired_dir / py_path.name
        if destination.exists():
            destination.unlink()
        shutil.move(str(py_path), str(destination))
        moved.append(trial_id)

    if restored:
        LOGGER.info(
            "Restored %d re-appeared curation(s) from %s", len(restored), expired_dir
        )
    if moved:
        LOGGER.info("Moved %d expired curation(s) into %s", len(moved), expired_dir)

    return ExpiryResult(
        moved=sorted(moved),
        retained_pottr=sorted(set(retained)),
        expired_dir=expired_dir,
        restored=sorted(set(restored)),
        guarded=guarded,
    )
