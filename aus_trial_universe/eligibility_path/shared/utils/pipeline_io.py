from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Sequence, Set

import pandas as pd

LOGGER = logging.getLogger(__name__)

SUPPORTED_TABULAR_SUFFIXES: Set[str] = {".csv", ".tsv", ".xlsx", ".xls"}
SUPPORTED_CSV_SUFFIXES: Set[str] = {".csv"}
SUPPORTED_OUTPUT_FORMATS: Set[str] = {"tsv", "csv", "xlsx", "xls"}


def normalize_token(value: str) -> str:
    return value.casefold().replace("-", "_").replace(" ", "_")


def iter_candidate_files(
    directory: Path,
    *,
    allowed_suffixes: Set[str],
    recursive: bool = False,
) -> Iterable[Path]:
    if not directory.exists():
        raise FileNotFoundError(f"Directory does not exist: {directory}")

    iterator = directory.rglob("*") if recursive else directory.iterdir()

    for path in iterator:
        if not path.is_file():
            continue
        if path.name.startswith("~$"):
            continue
        if path.suffix.casefold() not in allowed_suffixes:
            continue
        yield path


def matches_token_groups(path: Path, token_groups: Sequence[Sequence[str]]) -> bool:
    """Return whether every token group has at least one token in the file stem."""
    normalized_stem = normalize_token(path.stem)

    for group in token_groups:
        normalized_group = [normalize_token(token) for token in group]
        if not any(token in normalized_stem for token in normalized_group):
            return False

    return True


def find_best_file(
    directory: Path,
    *,
    token_groups: Sequence[Sequence[str]],
    allowed_suffixes: Set[str] = SUPPORTED_TABULAR_SUFFIXES,
    recursive: bool = False,
    label: str,
    warn_on_multiple: bool = True,
) -> Path:
    matches = [
        path
        for path in iter_candidate_files(
            directory,
            allowed_suffixes=allowed_suffixes,
            recursive=recursive,
        )
        if matches_token_groups(path, token_groups)
    ]

    if not matches:
        raise FileNotFoundError(
            f"Could not find {label} in {directory}. "
            f"Token groups: {token_groups}. "
            f"Allowed suffixes: {sorted(allowed_suffixes)}"
        )

    matches = sorted(
        matches,
        key=lambda path: (
            path.stat().st_mtime,
            -len(path.name),
        ),
        reverse=True,
    )
    selected = matches[0]

    if warn_on_multiple and len(matches) > 1:
        LOGGER.warning(
            "Multiple candidate files found for %s in %s; using most recent: %s. "
            "Other candidates: %s",
            label,
            directory,
            selected,
            ", ".join(str(path) for path in matches[1:5]),
        )

    return selected


def resolve_path(path: Path, repo_root: Path) -> Path:
    if path.is_absolute():
        return path
    return repo_root / path


def read_tabular_file(path: Path) -> pd.DataFrame:
    suffix = path.suffix.casefold()

    if suffix == ".csv":
        return pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[])
    if suffix == ".tsv":
        return pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False, na_values=[])
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path, dtype=str, keep_default_na=False, na_values=[])

    raise ValueError(f"Unsupported input file type: {path.suffix}")


def write_tabular_file(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.casefold()

    if suffix == ".csv":
        df.to_csv(path, index=False)
        return
    if suffix == ".tsv":
        df.to_csv(path, sep="\t", index=False)
        return
    if suffix in {".xlsx", ".xls"}:
        df.to_excel(path, index=False)
        return

    raise ValueError(f"Unsupported output file type: {path.suffix}")


def output_path(output_dir: Path, stem: str, output_format: str) -> Path:
    normalized_format = output_format.casefold().lstrip(".")

    if normalized_format not in SUPPORTED_OUTPUT_FORMATS:
        raise ValueError(
            f"Unsupported output_format: {output_format!r}. "
            "Expected tsv, csv, xlsx, or xls."
        )

    return output_dir / f"{stem}.{normalized_format}"
