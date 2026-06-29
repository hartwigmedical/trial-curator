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
from dataclasses import dataclass
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


def move_expired_curations(
    *,
    curated_dir: Path,
    current_trial_ids: Iterable[str],
    pottr_exempt_ids: Iterable[str],
    normalize_trial_id: Callable[[object], str],
    trial_id_prefix: str,
    expired_subdir_name: str = DEFAULT_EXPIRED_SUBDIR,
) -> ExpiryResult:
    """Move curated ``{PREFIX}*.py`` files whose trial is not in the latest download.

    A curated file is *expired* when its trial id is absent from
    ``current_trial_ids`` and not in ``pottr_exempt_ids``.  Expired files are
    moved (not deleted) into ``curated_dir/expired_subdir_name``.
    """
    current = {normalize_trial_id(value) for value in current_trial_ids}
    pottr = {normalize_trial_id(value) for value in pottr_exempt_ids}

    expired_dir = curated_dir / expired_subdir_name
    moved: List[str] = []
    retained: List[str] = []

    for py_path in iter_curated_py_files(curated_dir, trial_id_prefix=trial_id_prefix):
        trial_id = normalize_trial_id(py_path.stem)
        if trial_id in current:
            continue
        if trial_id in pottr:
            retained.append(trial_id)
            continue

        expired_dir.mkdir(parents=True, exist_ok=True)
        destination = expired_dir / py_path.name
        if destination.exists():
            destination.unlink()
        shutil.move(str(py_path), str(destination))
        moved.append(trial_id)

    if moved:
        LOGGER.info(
            "Moved %d expired curation(s) into %s", len(moved), expired_dir
        )

    return ExpiryResult(
        moved=sorted(moved),
        retained_pottr=sorted(set(retained)),
        expired_dir=expired_dir,
    )
