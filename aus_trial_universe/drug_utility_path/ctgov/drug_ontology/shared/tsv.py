from __future__ import annotations

import csv
from pathlib import Path
from typing import Mapping, Sequence

from aus_trial_universe.drug_utility_path.ctgov.drug_ontology.shared.text import clean_text


def read_tsv_dicts(path: Path) -> list[dict[str, str]]:
    """Read a UTF-8-sig TSV into cleaned dictionaries."""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames is None:
            raise ValueError(f"Input TSV has no header row: {path}")
        return [
            {clean_text(key): clean_text(value) for key, value in row.items() if key is not None}
            for row in reader
        ]


def read_tsv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Read a UTF-8-sig TSV and return fieldnames plus raw row dictionaries."""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames is None:
            raise ValueError(f"Input TSV has no header row: {path}")
        return list(reader.fieldnames), [dict(row) for row in reader]


def write_tsv(
    path: Path,
    rows: Sequence[Mapping[str, object]],
    fieldnames: Sequence[str],
) -> None:
    """Write dictionaries as a UTF-8 TSV using a stable header."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(fieldnames),
            delimiter="\t",
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def detect_delimiter(path: Path) -> str:
    """Detect tab vs comma using the header line."""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        header_line = handle.readline()

    if not header_line:
        raise ValueError(f"Input file is empty: {path}")

    tab_cols = next(csv.reader([header_line], delimiter="\t"))
    comma_cols = next(csv.reader([header_line], delimiter=","))
    return "\t" if len(tab_cols) >= len(comma_cols) else ","
