"""Path helpers for versioned drug ontology input directories."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence


VERSION_PREFIX = "version_"
DRUG_ONTOLOGY_DATA_DIR = Path("data/ctgov/drug_ontology")
RAW_INPUTS_DIR = DRUG_ONTOLOGY_DATA_DIR / "raw_inputs"
PROCESSED_INPUTS_DIR = DRUG_ONTOLOGY_DATA_DIR / "processed_inputs"
PIPELINE_PROCESSED_INPUTS_DIR = PROCESSED_INPUTS_DIR / "pipeline"
ANALYSIS_PROCESSED_INPUTS_DIR = PROCESSED_INPUTS_DIR / "analysis"
PIPELINE_OUTPUTS_DIR = DRUG_ONTOLOGY_DATA_DIR / "pipeline_outputs"
ANALYSIS_OUTPUTS_DIR = DRUG_ONTOLOGY_DATA_DIR / "analysis_outputs"
COMPARISON_RUNS_DIR = DRUG_ONTOLOGY_DATA_DIR / "comparison_runs"


@dataclass(frozen=True, order=True)
class VersionSortKey:
    """Comparable key for versioned source directories."""

    kind: int
    value: int
    name: str


def parse_version_sort_key(name: str) -> VersionSortKey | None:
    """Return a sortable key for a ``version_*`` directory name."""

    if not name.startswith(VERSION_PREFIX):
        return None

    suffix = name.removeprefix(VERSION_PREFIX)
    if len(suffix) == 8 and suffix.isdigit():
        try:
            parsed_date = datetime.strptime(suffix, "%d%m%Y").date()
        except ValueError:
            return None
        return VersionSortKey(2, int(parsed_date.strftime("%Y%m%d")), name)

    if suffix.isdigit():
        return VersionSortKey(1, int(suffix), name)

    return None


def iter_version_dirs(base_dir: Path) -> Iterable[Path]:
    """Yield immediate child directories named ``version_*``."""

    if not base_dir.exists():
        raise FileNotFoundError(f"Version root does not exist: {base_dir}")
    if not base_dir.is_dir():
        raise NotADirectoryError(f"Version root is not a directory: {base_dir}")

    yield from (path for path in base_dir.iterdir() if path.is_dir() and path.name.startswith(VERSION_PREFIX))


def latest_version_dir(base_dir: str | Path) -> Path:
    """
    Return the latest valid version directory under ``base_dir``.

    Date versions use ``version_DDMMYYYY`` and are ordered by calendar date.
    Numeric versions such as ``version_36`` are ordered numerically. Non-version
    labels such as ``version_legacy`` are ignored.
    """

    root = Path(base_dir)
    candidates: list[tuple[VersionSortKey, Path]] = []
    ignored: list[Path] = []

    for path in iter_version_dirs(root):
        sort_key = parse_version_sort_key(path.name)
        if sort_key is None:
            ignored.append(path)
            continue
        candidates.append((sort_key, path))

    if not candidates:
        ignored_hint = ""
        if ignored:
            ignored_names = ", ".join(path.name for path in sorted(ignored))
            ignored_hint = f" Ignored non-sortable version directories: {ignored_names}."
        raise FileNotFoundError(f"No sortable version_* directories found under {root}.{ignored_hint}")

    return max(candidates, key=lambda item: item[0])[1]


def latest_raw_input_dir(source_name: str) -> Path:
    """Return the latest raw input version directory for a source."""

    return latest_version_dir(RAW_INPUTS_DIR / source_name)


def latest_processed_input_dir(source_name: str, *, workflow: str = "pipeline") -> Path:
    """Return the latest processed input version directory for a source/workflow."""

    if workflow == "pipeline":
        root = PIPELINE_PROCESSED_INPUTS_DIR
    elif workflow == "analysis":
        root = ANALYSIS_PROCESSED_INPUTS_DIR
    else:
        raise ValueError(f"Unsupported processed-input workflow: {workflow!r}")

    return latest_version_dir(root / source_name)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Print the latest version_* directory under a source root.")
    parser.add_argument("base_dir", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    print(latest_version_dir(args.base_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
