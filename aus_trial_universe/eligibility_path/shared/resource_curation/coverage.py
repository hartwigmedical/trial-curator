"""Flag and accrete the gap between a freshly-grown trial set and the
hand-curated resource files it relies on.

For each hand-curated resource (e.g. the cancer-type ``manual_overwrite`` file,
the gene-alteration / molecular-signature mapping resources) the pipeline can:

* **report coverage** (A): which generated rows/keys have no entry in the
  resource (``uncovered``) and which resource entries no longer match anything
  generated (``stale``). The uncovered rows are written as a *fill-ready
  template* — the resource's own columns with the value column(s) left blank —
  so a curator just fills the blanks. The template is persistent and preserves
  in-progress fills across runs.

* **accrete fills** (B): on a later run, the filled rows in the template are
  merged into the resource and written as a NEW timestamped version. The
  original resource file is never overwritten; resource selection is
  latest-by-mtime, so the new version is picked up automatically.

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
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable, Sequence

import pandas as pd

from aus_trial_universe.eligibility_path.shared.utils.text_normalisation import (
    blank_safe_str,
)

logger = logging.getLogger(__name__)

COVERAGE_STATUS_COL = "coverage_status"
UNCOVERED = "uncovered"
STALE = "stale"
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
    resource_key_cols: tuple[str, ...] = ()  # defaults to coverage_key_cols
    context_cols: tuple[str, ...] = ()  # extra columns surfaced in the template for the curator

    def keys_for_resource(self) -> tuple[str, ...]:
        return self.resource_key_cols or self.coverage_key_cols

    @property
    def template_path(self) -> Path:
        # A `gaps/` subdir keeps the editable template from being mistaken for a
        # resource version by latest-by-token resource discovery (non-recursive).
        return self.resource_dir / "gaps" / f"{self.resource_stem}_to_fill.csv"


@dataclass(frozen=True)
class CoverageResult:
    spec_name: str
    generated_rows: int
    uncovered_rows: int
    uncovered_keys: int
    stale_rows: int
    gap_report_path: Path | None
    template_path: Path | None


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
def report_coverage(
    spec: CoverageSpec,
    generated_df: pd.DataFrame,
    *,
    diagnostics_dir: Path,
    resource_df: pd.DataFrame | None = None,
) -> CoverageResult:
    """Write the gap report + refresh the fill-ready template (no resource writes)."""
    if resource_df is None:
        resource_path = latest_resource_path(spec)
        resource_df = read_table(resource_path) if resource_path is not None else pd.DataFrame()

    uncovered, stale = split_coverage(generated_df, resource_df, spec.coverage_key_cols)
    uncovered_keys = int(coverage_key_series(uncovered, spec.coverage_key_cols).nunique())

    gap_report_path = diagnostics_dir / f"{spec.resource_stem}_coverage_gaps.tsv"
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    report = pd.concat(
        [
            uncovered.assign(**{COVERAGE_STATUS_COL: UNCOVERED}),
            stale.assign(**{COVERAGE_STATUS_COL: STALE}),
        ],
        ignore_index=True,
    )
    write_table(report, gap_report_path)

    logger.info(
        "Coverage[%s]: %d/%d generated rows uncovered (%d distinct keys), %d stale resource rows. "
        "Gap report: %s",
        spec.name,
        len(uncovered),
        len(generated_df),
        uncovered_keys,
        len(stale),
        gap_report_path,
    )
    return CoverageResult(
        spec_name=spec.name,
        generated_rows=len(generated_df),
        uncovered_rows=len(uncovered),
        uncovered_keys=uncovered_keys,
        stale_rows=len(stale),
        gap_report_path=gap_report_path,
        template_path=None,
    )


def accrete_filled_template(
    spec: CoverageSpec,
    *,
    today_str: str | None = None,
    transform: "Callable[[pd.DataFrame], pd.DataFrame] | None" = None,
) -> Path | None:
    """Merge the template's filled rows into a NEW timestamped resource version.

    Returns the new resource path, or ``None`` when there is nothing to accrete.
    Never overwrites an existing resource file. ``transform`` runs on the merged
    resource before it is written — used to populate derived columns (e.g. the
    gene-alteration ``Mapping_args``, regenerated from the filled curation cols).
    """
    if not spec.template_path.exists():
        return None
    template_df = read_table(spec.template_path)
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
    *,
    diagnostics_dir: Path | None = None,
    resource_df: pd.DataFrame | None = None,
) -> dict[str, object]:
    """Recompute coverage and rebuild the fill-ready template (no accretion).

    Run this AFTER processing (it needs the generated intermediates). The gap
    diagnostic and refreshed template reflect the latest resource version.
    """
    if resource_df is None:
        resource_path = latest_resource_path(spec)
        resource_df = read_table(resource_path) if resource_path is not None else pd.DataFrame()

    uncovered, stale = split_coverage(generated_df, resource_df, spec.coverage_key_cols)
    uncovered_keys = (
        int(coverage_key_series(uncovered, spec.coverage_key_cols).nunique())
        if not uncovered.empty
        else 0
    )

    if diagnostics_dir is not None:
        diagnostics_dir.mkdir(parents=True, exist_ok=True)
        report = pd.concat(
            [
                uncovered.assign(**{COVERAGE_STATUS_COL: UNCOVERED}),
                stale.assign(**{COVERAGE_STATUS_COL: STALE}),
            ],
            ignore_index=True,
        )
        write_table(report, diagnostics_dir / f"{spec.resource_stem}_coverage_gaps.tsv")

    template = build_fill_ready_template(uncovered, spec)
    write_table(template, spec.template_path)
    logger.info(
        "Coverage[%s]: %d generated rows, %d uncovered keys, %d stale | template -> %s",
        spec.name,
        len(generated_df),
        uncovered_keys,
        len(stale),
        spec.template_path,
    )
    return {
        "spec": spec.name,
        "uncovered_keys": uncovered_keys,
        "stale_rows": len(stale),
        "remaining_gaps": len(template),
    }


def audit_resource(
    spec: CoverageSpec,
    generated_df: pd.DataFrame,
    *,
    diagnostics_dir: Path | None = None,
    today_str: str | None = None,
    transform: "Callable[[pd.DataFrame], pd.DataFrame] | None" = None,
) -> dict[str, object]:
    """Full lifecycle for one resource: accrete filled template -> new version,
    then recompute coverage + rebuild the template. Used by the standalone audit;
    the pipeline splits this into pre-processing accretion + post-processing
    reporting so curator fills take effect the same run.
    """
    new_resource_path = accrete_filled_template(spec, today_str=today_str, transform=transform)
    resource_path = new_resource_path or latest_resource_path(spec)
    resource_df = read_table(resource_path) if resource_path is not None else pd.DataFrame()

    summary = refresh_coverage(
        spec, generated_df, diagnostics_dir=diagnostics_dir, resource_df=resource_df
    )
    summary["resource"] = str(resource_path) if resource_path else ""
    summary["new_version"] = str(new_resource_path) if new_resource_path else ""
    return summary
