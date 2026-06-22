from __future__ import annotations

from pathlib import Path
from typing import Iterable


def has_curated_py_files(path: Path, *, trial_id_prefix: str) -> bool:
    """Return whether a file/directory contains curated trial `.py` files.

    `trial_id_prefix` is the registry-specific trial id prefix, for example
    `NCT` for CTGov or `ACTRN` for ANZCTR.
    """

    prefix = trial_id_prefix.upper()
    if path.is_file():
        return path.name.upper().startswith(prefix) and path.suffix == ".py"
    if not path.is_dir():
        return False
    return any(path.glob(f"{prefix}*.py"))


def iter_curated_py_files(path: Path, *, trial_id_prefix: str) -> Iterable[Path]:
    """Yield curated trial `.py` files in stable order."""

    prefix = trial_id_prefix.upper()
    if path.is_file():
        yield path
        return
    yield from sorted(candidate for candidate in path.glob(f"{prefix}*.py") if candidate.is_file())
