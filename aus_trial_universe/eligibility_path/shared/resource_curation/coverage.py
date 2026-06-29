"""Flag and accrete the gap between a freshly-grown trial set and the
hand-curated resource files it relies on.

For each hand-curated resource (e.g. the cancer-type ``manual_overwrite`` file,
the gene-alteration / molecular-signature mapping resources) the pipeline can:

* **report coverage** (A): which generated rows/keys have no entry in the
  resource (``uncovered``). The uncovered rows are written as a *fill-ready
  template* — the resource's own columns with the value column(s) left blank —
  so a curator just fills the blanks. Templates live under a date-stamped
  ``resource_gaps/version_<ddmmyyyy>/`` folder in the exports tree; each run
  writes a fresh template into the run's version folder.

* **accrete fills** (B): on a later run, the filled rows in the latest gap
  template are merged into the resource and written as a NEW timestamped
  version. The original resource file is never overwritten; resource selection
  is latest-by-mtime, so the new version is picked up automatically.

Two key notions are kept separate so a resource can be gapped at a coarser grain
than it is keyed:

* ``coverage_key_cols`` decides covered vs. uncovered (cancer type: ``nct_id``).
* ``resource_key_cols`` is the resource's actual row key, used for the template
  schema and accretion de-duplication (cancer type: nct_id + tumour columns).

For value-mapping resources (gene alteration, molecular signature) the two are
the same: the value-pair columns.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable, Sequence

import pandas as pd

from aus_trial_universe.eligibility_path.shared.utils.pipeline_io import (
    VERSION_DIR_RE,
    version_dir_sort_key,
)
from aus_trial_universe.eligibility_path.shared.utils.text_normalisation import (
    blank_safe_str,
)

logger = logging.getLogger(__name__)

_KEY_SEP = "\x1f"


@dataclass(frozen=True)
class CoverageSpec:
    """Describes one hand-curated resource and how to gap-check it."""

    name: str
    resource_dir: Path
    resource_stem: str  # new versions are written as <stem>_<ddmmyyyy><resource_suffix>
    resource_suffix: str  # ".csv" | ".tsv" | ".xlsx"
    resource_tokens: tuple[tuple[str, ...], ...]  # token groups identifying the resource file
    coverage_key_cols: tuple[str, ...]
    value_cols: tuple[str, ...]
    gap_filename: str  # clean filename for the fill-ready gap template, e.g. "cancer_type_gaps.csv"
    resource_key_cols: tuple[str, ...] = ()  # defaults to coverage_key_cols
    context_cols: tuple[str, ...] = ()  # extra columns surfaced in the template for the curator

    def keys_for_resource(self) -> tuple[str, ...]:
        return self.resource_key_cols or self.coverage_key_cols


def gap_template_path(gaps_root: Path, spec: CoverageSpec, today_str: str) -> Path:
    """Write path for this run's fill-ready template: a dated version folder."""
    return gaps_root / f"version_{today_str}" / spec.gap_filename


def latest_gap_file(gaps_root: Path, spec: CoverageSpec) -> Path | None:
    """Newest ``version_<ddmmyyyy>`` gap file for ``spec``, or None if none exist."""
    if not gaps_root.exists():
        return None
    version_dirs = sorted(
        (
            path
            for path in gaps_root.iterdir()
            if path.is_dir()
            and VERSION_DIR_RE.match(path.name)
            and (path / spec.gap_filename).is_file()
        ),
        key=version_dir_sort_key,
    )
    if not version_dirs:
        return None
    return version_dirs[-1] / spec.gap_filename


def normalize_key_value(value: object) -> str:
    return re.sub(r"\s+", " ", blank_safe_str(value)).strip().casefold()


def coverage_key_series(frame: pd.DataFrame, key_cols: Sequence[str]) -> pd.Series:
    if len(frame) == 0:
        return pd.Series([], dtype="object")
    parts = [frame[col].map(normalize_key_value) for col in key_cols]
    key = parts[0].astype("object")
    for part in parts[1:]:
        key = key.str.cat(part, sep=_KEY_SEP)
    return key


def split_coverage(
    generated_df: pd.DataFrame,
    resource_df: pd.DataFrame,
    coverage_key_cols: Sequence[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (uncovered generated rows, stale resource rows) by coverage key."""
    generated_keys = coverage_key_series(generated_df, coverage_key_cols)
    resource_keys = (
        coverage_key_series(resource_df, coverage_key_cols)
        if not resource_df.empty
        else pd.Series([], dtype="object")
    )

    resource_key_set = set(resource_keys)
    generated_key_set = set(generated_keys)

    uncovered = generated_df.loc[~generated_keys.isin(resource_key_set)].reset_index(drop=True)
    stale = (
        resource_df.loc[~resource_keys.isin(generated_key_set)].reset_index(drop=True)
        if not resource_df.empty
        else resource_df.iloc[0:0].copy()
    )
    return uncovered, stale


def build_fill_ready_template(uncovered_df: pd.DataFrame, spec: CoverageSpec) -> pd.DataFrame:
    """One blank-value row per uncovered resource key, with curator context."""
    resource_keys = spec.keys_for_resource()
    columns = [
        *resource_keys,
        *[col for col in spec.context_cols if col not in resource_keys],
        *spec.value_cols,
    ]
    if uncovered_df.empty:
        return pd.DataFrame(columns=columns)

    available = [col for col in columns if col in uncovered_df.columns]
    template = uncovered_df[available].copy()
    # Back-fill any missing columns BEFORE the dedup, which keys on every
    # resource_key col (a missing one would otherwise raise KeyError).
    for col in columns:
        if col not in template.columns:
            template[col] = ""
    # Collapse to one row per resource key (uncovered generated data can repeat a key).
    template = template.loc[
        ~coverage_key_series(template, resource_keys).duplicated(keep="first")
    ].reset_index(drop=True)
    for col in columns:
        if col not in template.columns:
            template[col] = ""
    for col in spec.value_cols:
        template[col] = ""  # blank for the curator to fill
    return template[columns]


def filled_template_rows(template_df: pd.DataFrame, value_cols: Sequence[str]) -> pd.DataFrame:
    """Template rows where the curator has filled at least one value column."""
    if template_df.empty:
        return template_df.iloc[0:0].copy()
    present = [col for col in value_cols if col in template_df.columns]
    if not present:
        return template_df.iloc[0:0].copy()
    has_value = pd.Series(False, index=template_df.index)
    for col in present:
        has_value = has_value | (template_df[col].map(normalize_key_value) != "")
    return template_df.loc[has_value].reset_index(drop=True)


def accrete_resource(
    resource_df: pd.DataFrame,
    filled_rows: pd.DataFrame,
    spec: CoverageSpec,
) -> pd.DataFrame:
    """Concat filled curator rows onto the resource, newest value winning per key."""
    resource_keys = spec.keys_for_resource()
    aligned = filled_rows.copy()
    for col in resource_df.columns:
        if col not in aligned.columns:
            aligned[col] = ""
    aligned = aligned[resource_df.columns]

    combined = pd.concat([resource_df, aligned], ignore_index=True)
    keep = ~coverage_key_series(combined, resource_keys).duplicated(keep="last")
    return combined.loc[keep].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Tabular IO + resource version selection
# ---------------------------------------------------------------------------
def read_table(path: Path) -> pd.DataFrame:
    suffix = path.suffix.casefold()
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path, dtype=object).fillna("")
    sep = "\t" if suffix == ".tsv" else ","
    return pd.read_csv(path, sep=sep, dtype=str, keep_default_na=False)


def write_table(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.casefold()
    if suffix in {".xlsx", ".xls"}:
        df.to_excel(path, index=False)
    else:
        sep = "\t" if suffix == ".tsv" else ","
        df.to_csv(path, sep=sep, index=False)


def _tokens_match(filename: str, token_groups: Sequence[Sequence[str]]) -> bool:
    normalized = re.sub(r"[\s_-]+", "", filename.casefold())
    return all(
        any(re.sub(r"[\s_-]+", "", token.casefold()) in normalized for token in group)
        for group in token_groups
    )


def latest_resource_path(spec: CoverageSpec) -> Path | None:
    if not spec.resource_dir.exists():
        return None
    candidates = [
        path
        for path in spec.resource_dir.iterdir()
        if path.is_file()
        and path.suffix.casefold() == spec.resource_suffix.casefold()
        and _tokens_match(path.name, spec.resource_tokens)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def timestamped_version_path(spec: CoverageSpec, today_str: str) -> Path:
    return spec.resource_dir / f"{spec.resource_stem}_{today_str}{spec.resource_suffix}"


# ---------------------------------------------------------------------------
# High-level entry points
# ---------------------------------------------------------------------------
def accrete_filled_template(
    spec: CoverageSpec,
    gaps_root: Path,
    *,
    today_str: str | None = None,
    transform: "Callable[[pd.DataFrame], pd.DataFrame] | None" = None,
) -> Path | None:
    """Merge the latest gap template's filled rows into a NEW resource version.

    Reads the newest ``version_<ddmmyyyy>`` gap file for the spec under
    ``gaps_root``. Returns the new resource path, or ``None`` when there is
    nothing to accrete. Never overwrites a *prior-dated* resource version; a
    same-date re-run replaces today's version (idempotently, by key).
    ``transform`` runs on the merged resource before it is written — used to
    populate derived columns (e.g. the gene-alteration ``Mapping_args``,
    regenerated from the filled curation cols).
    """
    gap_path = latest_gap_file(gaps_root, spec)
    if gap_path is None:
        return None
    template_df = read_table(gap_path)
    filled = filled_template_rows(template_df, spec.value_cols)
    if filled.empty:
        logger.info("Coverage[%s]: no filled template rows to accrete.", spec.name)
        return None

    resource_path = latest_resource_path(spec)
    resource_df = read_table(resource_path) if resource_path is not None else pd.DataFrame()
    merged = accrete_resource(resource_df, filled, spec)
    if transform is not None:
        merged = transform(merged)

    today_str = today_str or date.today().strftime("%d%m%Y")
    new_path = timestamped_version_path(spec, today_str)
    write_table(merged, new_path)
    logger.info(
        "Coverage[%s]: accreted %d filled row(s) into new resource version %s (was %s).",
        spec.name,
        len(filled),
        new_path,
        resource_path,
    )
    return new_path


def refresh_coverage(
    spec: CoverageSpec,
    generated_df: pd.DataFrame,
    gaps_root: Path,
    *,
    today_str: str | None = None,
    resource_df: pd.DataFrame | None = None,
) -> dict[str, object]:
    """Recompute coverage and write the fill-ready gap template (no accretion).

    Run this AFTER processing (it needs the generated intermediates). The
    refreshed template is written into this run's ``version_<today_str>`` folder
    under ``gaps_root`` and reflects the latest resource version.
    """
    if resource_df is None:
        resource_path = latest_resource_path(spec)
        resource_df = read_table(resource_path) if resource_path is not None else pd.DataFrame()

    uncovered, _stale = split_coverage(generated_df, resource_df, spec.coverage_key_cols)
    uncovered_keys = (
        int(coverage_key_series(uncovered, spec.coverage_key_cols).nunique())
        if not uncovered.empty
        else 0
    )

    today_str = today_str or date.today().strftime("%d%m%Y")
    template = build_fill_ready_template(uncovered, spec)
    template_path = gap_template_path(gaps_root, spec, today_str)
    write_table(template, template_path)
    logger.info(
        "Coverage[%s]: %d generated rows, %d uncovered keys | gap template -> %s",
        spec.name,
        len(generated_df),
        uncovered_keys,
        template_path,
    )
    return {
        "spec": spec.name,
        "uncovered_keys": uncovered_keys,
        "remaining_gaps": len(template),
    }


def audit_resource(
    spec: CoverageSpec,
    generated_df: pd.DataFrame,
    gaps_root: Path,
    *,
    today_str: str | None = None,
    transform: "Callable[[pd.DataFrame], pd.DataFrame] | None" = None,
) -> dict[str, object]:
    """Full lifecycle for one resource: accrete the latest filled gap template
    into a new version, then recompute coverage + write a fresh gap template.
    Used by the standalone audit; the pipeline splits this into pre-processing
    accretion + post-processing reporting so curator fills take effect the same
    run.
    """
    new_resource_path = accrete_filled_template(
        spec, gaps_root, today_str=today_str, transform=transform
    )
    resource_path = new_resource_path or latest_resource_path(spec)
    resource_df = read_table(resource_path) if resource_path is not None else pd.DataFrame()

    summary = refresh_coverage(
        spec, generated_df, gaps_root, today_str=today_str, resource_df=resource_df
    )
    summary["resource"] = str(resource_path) if resource_path else ""
    summary["new_version"] = str(new_resource_path) if new_resource_path else ""
    return summary
