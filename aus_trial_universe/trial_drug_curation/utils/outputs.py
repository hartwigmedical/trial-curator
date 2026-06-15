from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from aus_trial_universe.trial_drug_curation.utils.constants import (
    DEFAULT_OUTPUT_DIR,
    DEFAULT_OUTPUT_PREFIX,
    SKIPPED_OUTPUT_PREFIX,
)
from aus_trial_universe.trial_drug_curation.utils.trial_ids import SkippedTrial
from aus_trial_universe.trial_drug_curation.utils.schema import TSV_COLUMNS


def dated_output_path(
    *,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    run_date: datetime | None = None,
    prefix: str = DEFAULT_OUTPUT_PREFIX,
) -> Path:
    date_suffix = (run_date or datetime.now()).strftime("%Y%m%d")
    return Path(output_dir) / f"{prefix}_{date_suffix}.tsv"


def skipped_trials_output_path(
    *,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    run_date: datetime | None = None,
) -> Path:
    return dated_output_path(
        output_dir=output_dir,
        run_date=run_date,
        prefix=SKIPPED_OUTPUT_PREFIX,
    )


def next_available_output_path(path: str | Path) -> Path:
    candidate = Path(path)
    if not candidate.exists():
        return candidate

    for counter in range(2, 1000):
        numbered_candidate = candidate.with_name(
            f"{candidate.stem}_{counter}{candidate.suffix}"
        )
        if not numbered_candidate.exists():
            return numbered_candidate

    raise FileExistsError(f"Could not find an available output path for {candidate}")


def count_tsv_data_rows(tsv: str) -> int:
    lines = [line for line in tsv.splitlines() if line.strip()]
    return max(0, len(lines) - 1)


def merge_tsv_tables(tsvs: Sequence[str]) -> str:
    output = io.StringIO()
    writer = csv.writer(output, delimiter="\t", lineterminator="\n")
    writer.writerow(TSV_COLUMNS)
    for tsv in tsvs:
        reader = csv.reader(io.StringIO(tsv), delimiter="\t")
        header = next(reader, None)
        if header is None:
            continue
        if tuple(header) != TSV_COLUMNS:
            raise ValueError("Cannot merge TSV with unexpected header.")
        for row in reader:
            if row:
                writer.writerow(row)
    return output.getvalue()


def unique_trial_ids_in_tsv(tsv: str) -> set[str]:
    reader = csv.DictReader(io.StringIO(tsv), delimiter="\t")
    return {row["trialId"] for row in reader if row.get("trialId")}


def write_skipped_trials_tsv(
    skipped_trials: Sequence[SkippedTrial],
    *,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
) -> Path | None:
    if not skipped_trials:
        return None

    path = next_available_output_path(skipped_trials_output_path(output_dir=output_dir))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, delimiter="\t", lineterminator="\n")
        writer.writerow(("trialId", "registry", "reason"))
        for skipped in skipped_trials:
            writer.writerow((skipped.trial_id, skipped.registry, skipped.reason))
    return path
