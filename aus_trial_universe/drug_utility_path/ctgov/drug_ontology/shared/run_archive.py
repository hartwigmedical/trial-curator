"""Archive and compare generated drug ontology files."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

from aus_trial_universe.drug_utility_path.ctgov.drug_ontology.shared.paths import COMPARISON_RUNS_DIR, DRUG_ONTOLOGY_DATA_DIR


DEFAULT_ARCHIVE_DIRNAME = "comparison_runs"
LATEST_RUN_FILENAME = "latest_run.json"
MANIFEST_FILENAME = "manifest.json"
EXACT_MODE = "exact"
UNORDERED_TSV_MODE = "tsv_unordered_rows"
SUPPORTED_COMPARISON_MODES = {EXACT_MODE, UNORDERED_TSV_MODE}


@dataclass(frozen=True)
class GeneratedFile:
    """A generated file tracked in a comparison run."""

    path: Path
    comparison_mode: str = EXACT_MODE


@dataclass(frozen=True)
class ComparisonResult:
    """Comparison status for one generated file."""

    path: str
    matches: bool
    reason: str = ""


def default_archive_dir(data_dir: Path = DRUG_ONTOLOGY_DATA_DIR) -> Path:
    if data_dir == DRUG_ONTOLOGY_DATA_DIR:
        return COMPARISON_RUNS_DIR
    return data_dir / DEFAULT_ARCHIVE_DIRNAME


def normalize_run_at(run_at: datetime | None = None) -> datetime:
    if run_at is None:
        return datetime.now().astimezone()
    if run_at.tzinfo is None:
        return run_at.astimezone()
    return run_at


def run_id_for_datetime(run_at: datetime) -> str:
    return run_at.strftime("run_%Y%m%d_%H%M%S")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative_to_data_dir(path: Path, data_dir: Path) -> Path:
    try:
        return path.relative_to(data_dir)
    except ValueError as exc:
        raise ValueError(f"Generated file must live under {data_dir}: {path}") from exc


def validate_generated_files(files: Iterable[GeneratedFile], data_dir: Path) -> list[tuple[GeneratedFile, Path]]:
    validated: list[tuple[GeneratedFile, Path]] = []
    seen: set[Path] = set()

    for generated_file in files:
        comparison_mode = generated_file.comparison_mode
        if comparison_mode not in SUPPORTED_COMPARISON_MODES:
            raise ValueError(f"Unsupported comparison mode: {comparison_mode!r}")

        path = generated_file.path.resolve()
        if not path.exists():
            raise FileNotFoundError(f"Generated file does not exist: {path}")
        if not path.is_file():
            raise IsADirectoryError(f"Generated path is not a file: {path}")

        relative_path = relative_to_data_dir(path, data_dir)
        if relative_path in seen:
            raise ValueError(f"Generated file listed more than once: {relative_path}")
        seen.add(relative_path)
        validated.append((GeneratedFile(path=path, comparison_mode=comparison_mode), relative_path))

    if not validated:
        raise ValueError("At least one generated file is required")
    return validated


def allocate_run_dir(archive_dir: Path, requested_run_id: str) -> tuple[str, Path]:
    run_id = requested_run_id
    run_dir = archive_dir / run_id
    suffix = 2
    while run_dir.exists():
        run_id = f"{requested_run_id}_{suffix}"
        run_dir = archive_dir / run_id
        suffix += 1
    return run_id, run_dir


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def create_run_snapshot(
    files: Sequence[GeneratedFile],
    *,
    data_dir: Path = DRUG_ONTOLOGY_DATA_DIR,
    archive_dir: Path | None = None,
    run_at: datetime | None = None,
    run_id: str | None = None,
) -> dict[str, object]:
    """Copy generated files into a timestamped comparison run directory."""

    data_dir = data_dir.resolve()
    archive_dir = (archive_dir or default_archive_dir(data_dir)).resolve()
    run_at = normalize_run_at(run_at)
    requested_run_id = run_id or run_id_for_datetime(run_at)
    run_id, run_dir = allocate_run_dir(archive_dir, requested_run_id)

    validated = validate_generated_files(files, data_dir)
    file_entries: list[dict[str, object]] = []

    for generated_file, relative_path in validated:
        source = generated_file.path
        destination = run_dir / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

        file_entries.append(
            {
                "path": relative_path.as_posix(),
                "comparison_mode": generated_file.comparison_mode,
                "size_bytes": source.stat().st_size,
                "sha256": sha256_file(source),
            }
        )

    manifest: dict[str, object] = {
        "run_id": run_id,
        "generated_at": run_at.isoformat(),
        "data_dir": str(data_dir),
        "files": file_entries,
    }
    write_json(run_dir / MANIFEST_FILENAME, manifest)
    write_json(
        archive_dir / LATEST_RUN_FILENAME,
        {
            "run_id": run_id,
            "generated_at": run_at.isoformat(),
            "manifest_path": f"{run_id}/{MANIFEST_FILENAME}",
        },
    )
    return manifest


def latest_manifest_path(archive_dir: Path) -> Path:
    latest_path = archive_dir / LATEST_RUN_FILENAME
    if not latest_path.exists():
        raise FileNotFoundError(f"Latest run pointer does not exist: {latest_path}")

    latest = read_json(latest_path)
    manifest_path = latest.get("manifest_path")
    if not isinstance(manifest_path, str) or not manifest_path:
        raise ValueError(f"Latest run pointer is missing manifest_path: {latest_path}")
    return archive_dir / manifest_path


def tsv_rows_for_comparison(path: Path) -> tuple[tuple[str, ...], list[tuple[str, ...]]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle, delimiter="\t")
        try:
            header = tuple(next(reader))
        except StopIteration:
            return (), []
        rows = sorted(tuple(row) for row in reader)
    return header, rows


def files_match(current_path: Path, archived_path: Path, comparison_mode: str) -> tuple[bool, str]:
    if comparison_mode == EXACT_MODE:
        current_hash = sha256_file(current_path)
        archived_hash = sha256_file(archived_path)
        if current_hash == archived_hash:
            return True, ""
        return False, f"sha256 differs: current={current_hash} archived={archived_hash}"

    if comparison_mode == UNORDERED_TSV_MODE:
        current_header, current_rows = tsv_rows_for_comparison(current_path)
        archived_header, archived_rows = tsv_rows_for_comparison(archived_path)
        if current_header != archived_header:
            return False, "TSV header differs"
        if current_rows != archived_rows:
            return False, "TSV rows differ after stable sorting"
        return True, ""

    raise ValueError(f"Unsupported comparison mode: {comparison_mode!r}")


def compare_current_to_manifest(
    manifest_path: Path,
    *,
    data_dir: Path = DRUG_ONTOLOGY_DATA_DIR,
) -> list[ComparisonResult]:
    """Compare current generated files against a comparison-run manifest."""

    data_dir = data_dir.resolve()
    manifest = read_json(manifest_path)
    files = manifest.get("files")
    if not isinstance(files, list):
        raise ValueError(f"Manifest is missing files list: {manifest_path}")

    results: list[ComparisonResult] = []
    run_dir = manifest_path.parent
    for entry in files:
        if not isinstance(entry, dict):
            raise ValueError(f"Invalid manifest file entry in {manifest_path}: {entry!r}")
        relative_path = entry.get("path")
        comparison_mode = entry.get("comparison_mode", EXACT_MODE)
        if not isinstance(relative_path, str) or not relative_path:
            raise ValueError(f"Manifest file entry is missing path: {entry!r}")
        if not isinstance(comparison_mode, str):
            raise ValueError(f"Manifest file entry has invalid comparison_mode: {entry!r}")

        current_path = data_dir / relative_path
        archived_path = run_dir / relative_path
        if not current_path.exists():
            results.append(ComparisonResult(relative_path, False, f"current file missing: {current_path}"))
            continue
        if not archived_path.exists():
            results.append(ComparisonResult(relative_path, False, f"archived file missing: {archived_path}"))
            continue

        matches, reason = files_match(current_path, archived_path, comparison_mode)
        results.append(ComparisonResult(relative_path, matches, reason))

    return results


def parse_generated_files(paths: Sequence[Path], unordered_tsv_paths: Sequence[Path]) -> list[GeneratedFile]:
    unordered = {path.resolve() for path in unordered_tsv_paths}
    return [
        GeneratedFile(path=path, comparison_mode=UNORDERED_TSV_MODE if path.resolve() in unordered else EXACT_MODE)
        for path in paths
    ]


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Archive or compare generated drug ontology files.")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DRUG_ONTOLOGY_DATA_DIR,
        help=f"Drug ontology data root. Default: {DRUG_ONTOLOGY_DATA_DIR}",
    )
    parser.add_argument(
        "--archive-dir",
        type=Path,
        default=None,
        help="Comparison-run archive directory. Defaults to <data-dir>/comparison_runs.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    snapshot = subparsers.add_parser("snapshot", help="Archive generated files as a timestamped run.")
    snapshot.add_argument("--file", dest="files", type=Path, action="append", required=True)
    snapshot.add_argument("--unordered-tsv", dest="unordered_tsvs", type=Path, action="append", default=[])
    snapshot.add_argument("--run-id", default=None)

    compare = subparsers.add_parser("compare-latest", help="Compare current generated files to latest run.")
    compare.add_argument("--manifest", type=Path, default=None, help="Manifest path. Defaults to latest_run.json.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    archive_dir = args.archive_dir or default_archive_dir(args.data_dir)

    if args.command == "snapshot":
        generated_files = parse_generated_files(args.files, args.unordered_tsvs)
        manifest = create_run_snapshot(
            generated_files,
            data_dir=args.data_dir,
            archive_dir=archive_dir,
            run_id=args.run_id,
        )
        print(archive_dir / str(manifest["run_id"]) / MANIFEST_FILENAME)
        return 0

    if args.command == "compare-latest":
        manifest_path = args.manifest or latest_manifest_path(archive_dir)
        results = compare_current_to_manifest(manifest_path, data_dir=args.data_dir)
        failed = [result for result in results if not result.matches]
        if failed:
            for result in failed:
                print(f"{result.path}: {result.reason}")
            return 1
        print(f"{len(results)} generated files match {manifest_path}")
        return 0

    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
