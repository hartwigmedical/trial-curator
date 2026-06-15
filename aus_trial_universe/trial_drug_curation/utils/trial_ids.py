from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from aus_trial_universe.trial_drug_curation.utils.constants import SUPPORTED_REGISTRIES

CTGOV_TRIAL_ID_RE = re.compile(r"^NCT\d+$")
ANZCTR_TRIAL_ID_RE = re.compile(r"^ACTRN\d+$")


@dataclass(frozen=True)
class SkippedTrial:
    trial_id: str
    registry: str
    reason: str


@dataclass(frozen=True)
class TrialIdSelection:
    unique_ids: tuple[str, ...]
    grouped_trial_ids: dict[str, list[str]]
    skipped_trials: tuple[SkippedTrial, ...]


def normalise_trial_id(trial_id: str) -> str:
    return trial_id.strip().upper()


def split_trial_ids(text: str) -> list[str]:
    return [trial_id for trial_id in re.split(r"[\s,]+", text) if trial_id]


def unique_trial_ids(trial_ids: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for trial_id in trial_ids:
        normalized = normalise_trial_id(trial_id)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique


def read_trial_ids_file(path: str | Path) -> list[str]:
    if str(path) == "-":
        return split_trial_ids(sys.stdin.read())
    return split_trial_ids(Path(path).read_text(encoding="utf-8"))


def infer_registry_for_trial_id(trial_id: str) -> str | None:
    normalized = normalise_trial_id(trial_id)
    if CTGOV_TRIAL_ID_RE.fullmatch(normalized):
        return "ctgov"
    if ANZCTR_TRIAL_ID_RE.fullmatch(normalized):
        return "anzctr"
    return None


def select_trial_ids(trial_ids: Sequence[str]) -> TrialIdSelection:
    unique_ids = unique_trial_ids(trial_ids)
    grouped: dict[str, list[str]] = {}
    skipped: list[SkippedTrial] = []

    for trial_id in unique_ids:
        registry = infer_registry_for_trial_id(trial_id)
        if registry is None:
            skipped.append(
                SkippedTrial(
                    trial_id=trial_id,
                    registry="unknown",
                    reason="unrecognised_trial_id",
                )
            )
            continue
        grouped.setdefault(registry, []).append(trial_id)

    return TrialIdSelection(
        unique_ids=tuple(unique_ids),
        grouped_trial_ids={
            registry: grouped[registry]
            for registry in SUPPORTED_REGISTRIES
            if registry in grouped
        },
        skipped_trials=tuple(skipped),
    )
