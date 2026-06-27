"""Standalone resource-coverage audit (the ``make eligibility-path-resource-audit``
entry point).

For each hand-curated resource it:
  * accretes any rows a curator filled in the fill-ready template into a NEW
    timestamped resource version (never overwriting the existing files),
  * recomputes coverage of the latest generated data (unioned across CTGov and
    ANZCTR) against that resource,
  * writes a gap diagnostic and refreshes the fill-ready template.

Generated data is read from the registry intermediate exports the processing
steps already write, so this can run any time after a pipeline run.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

import pandas as pd

from aus_trial_universe.eligibility_path.shared.resource_curation import coverage
from aus_trial_universe.eligibility_path.shared.resource_curation.coverage import CoverageSpec

logger = logging.getLogger(__name__)

DEFAULT_RESOURCES_ROOT = Path("data/eligibility_path/resources")
DEFAULT_INTERMEDIATES_ROOT = Path("data/eligibility_path/exports/intermediates")
DEFAULT_REGISTRIES = ("ctgov", "anzctr")
_GENERATED_SUFFIXES = (".tsv", ".csv", ".xlsx")


def _populate_gene_alteration_mapping_args(resource_df: pd.DataFrame) -> pd.DataFrame:
    """Regenerate Mapping_args for accreted gene-alteration rows from their filled
    curation columns, using the shared mapping generator. Existing (already
    curated) Mapping_args values are left untouched.
    """
    from aus_trial_universe.eligibility_path.shared.gene_alterations.mapping import (
        generate_gene_alteration_mapping as gen,
    )

    out = resource_df.copy()
    if "Mapping_args" not in out.columns:
        out["Mapping_args"] = ""
    existing = out["Mapping_args"].fillna("").astype(str)
    for idx, row in out.iterrows():
        if existing.loc[idx].strip():
            continue
        try:
            out.at[idx, "Mapping_args"] = gen.postprocess_args_mapping(gen.map_row_to_args(row))
        except Exception as exc:  # a malformed curation row must not abort the audit
            logger.warning(
                "Could not generate Mapping_args for accreted gene-alteration row %s: %s",
                idx,
                exc,
            )
    return out


@dataclass(frozen=True)
class AuditEntry:
    """A resource to audit and where to read its generated data from."""

    spec: CoverageSpec
    # Path (without suffix) of the generated table within each registry's
    # intermediate dir, e.g. "cancer_type/02_primary_vs_conditions".
    generated_rel_path: str
    # Value-mapping pipelines mark unmapped rows with a status column; keep only
    # those rows as the gap, and rename the generated key columns to the
    # resource's key column names so coverage can be computed.
    status_col: str | None = None
    status_keep: tuple[str, ...] = ()
    rename: dict[str, str] = field(default_factory=dict)
    # Runs on the merged resource during accretion to populate derived columns.
    accretion_transform: Callable[[pd.DataFrame], pd.DataFrame] | None = None


def cancer_type_entry(resources_root: Path) -> AuditEntry:
    spec = CoverageSpec(
        name="cancer_type_manual_overwrite",
        resource_dir=resources_root / "cancer_type",
        resource_stem="manual_overwrite",
        resource_suffix=".csv",
        resource_tokens=(("manual",), ("overwrite",)),
        coverage_key_cols=("nct_id",),  # gap at trial level
        resource_key_cols=(
            "nct_id",
            "primary_tumor_type",
            "primary_tumor_location",
            "conditions_original",
        ),
        value_cols=("manual_overwrite",),
        context_cols=("primary_vs_conditions_relation",),
    )
    return AuditEntry(spec=spec, generated_rel_path="cancer_type/02_primary_vs_conditions")


def gene_alteration_entry(resources_root: Path) -> AuditEntry:
    spec = CoverageSpec(
        name="gene_alteration_curation",
        resource_dir=resources_root / "gene_alteration",
        resource_stem="GeneAlterationCurationResource",
        resource_suffix=".xlsx",
        resource_tokens=(("genealteration", "gene_alteration"), ("curationresource", "curation_resource")),
        coverage_key_cols=("Gene_lookup", "Alteration_lookup", "Variant_lookup"),
        # Mapping_args is regenerated from these curation fields, so the curator
        # fills them, not Mapping_args itself.
        value_cols=(
            "Move_to",
            "Gene_curation",
            "Variant_curation",
            "FindingsModel_curation",
            "Gene_type",
            "Fusion_both",
            "Fusion_FivePrime",
            "Fusion_ThreePrime",
        ),
        context_cols=("input_text", "description_input"),
    )
    return AuditEntry(
        spec=spec,
        generated_rel_path="gene_alteration/03_gene_alteration_mapped_criteria",
        status_col="mapping_status",
        status_keep=("no_mapping_found",),
        rename={
            "gene_input": "Gene_lookup",
            "alteration_input": "Alteration_lookup",
            "variant_input": "Variant_lookup",
        },
        accretion_transform=_populate_gene_alteration_mapping_args,
    )


def molecular_signature_entry(resources_root: Path) -> AuditEntry:
    spec = CoverageSpec(
        name="molecular_signature_curation",
        resource_dir=resources_root / "molecular_signature",
        resource_stem="MolecularSignatureCurationResource",
        resource_suffix=".xlsx",
        resource_tokens=(
            ("molecularsignature", "molecular_signature"),
            ("curationresource", "curation_resource"),
        ),
        coverage_key_cols=("Signature_lookup",),
        value_cols=("Findings_curation", "Move_to"),
        context_cols=("description_input",),
    )
    return AuditEntry(
        spec=spec,
        generated_rel_path="molecular_signature/02_molecular_signature_mapped_criteria",
        status_col="mapping_status",
        status_keep=("no_mapping_found",),
        rename={"signature_input": "Signature_lookup"},
    )


def default_entries(resources_root: Path) -> list[AuditEntry]:
    return [
        cancer_type_entry(resources_root),
        gene_alteration_entry(resources_root),
        molecular_signature_entry(resources_root),
    ]


def _resolve_generated_file(base_without_suffix: Path) -> Path | None:
    for suffix in _GENERATED_SUFFIXES:
        candidate = base_without_suffix.with_suffix(suffix)
        if candidate.exists():
            return candidate
    return None


def load_generated_union(
    entry: AuditEntry,
    *,
    intermediates_root: Path,
    registries: tuple[str, ...],
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for registry in registries:
        base = intermediates_root / registry / entry.generated_rel_path
        path = _resolve_generated_file(base)
        if path is None:
            logger.info(
                "Coverage audit[%s]: no generated table for %s (skipped).",
                entry.spec.name,
                registry,
            )
            continue
        frame = coverage.read_table(path)
        if entry.status_col and entry.status_col in frame.columns and entry.status_keep:
            frame = frame.loc[frame[entry.status_col].isin(entry.status_keep)]
        if entry.rename:
            frame = frame.rename(columns=entry.rename)
        frames.append(frame.reset_index(drop=True))
        logger.info(
            "Coverage audit[%s]: loaded %s generated gap rows from %s.",
            entry.spec.name,
            len(frames[-1]),
            path,
        )
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def run_accretion(
    entries: list[AuditEntry],
    *,
    today_str: str | None = None,
) -> list[Path]:
    """Pre-processing phase: graduate filled fill-ready templates into new
    timestamped resource versions, so THIS run's processing uses the curator's
    latest fills.
    """
    new_versions: list[Path] = []
    for entry in entries:
        new_path = coverage.accrete_filled_template(
            entry.spec, today_str=today_str, transform=entry.accretion_transform
        )
        if new_path is not None:
            new_versions.append(new_path)
    return new_versions


def run_report(
    entries: list[AuditEntry],
    *,
    intermediates_root: Path = DEFAULT_INTERMEDIATES_ROOT,
    registries: tuple[str, ...] = DEFAULT_REGISTRIES,
) -> list[dict[str, object]]:
    """Post-processing phase: recompute coverage from the generated intermediates
    and rebuild the fill-ready templates."""
    diagnostics_dir = intermediates_root / "resource_audit"
    summaries: list[dict[str, object]] = []
    for entry in entries:
        generated = load_generated_union(
            entry, intermediates_root=intermediates_root, registries=registries
        )
        if generated.empty:
            logger.warning(
                "Coverage audit[%s]: no generated data found; skipping report.",
                entry.spec.name,
            )
            continue
        summaries.append(
            coverage.refresh_coverage(entry.spec, generated, diagnostics_dir=diagnostics_dir)
        )
    return summaries


def run_audit(
    entries: list[AuditEntry],
    *,
    intermediates_root: Path = DEFAULT_INTERMEDIATES_ROOT,
    registries: tuple[str, ...] = DEFAULT_REGISTRIES,
    today_str: str | None = None,
) -> list[dict[str, object]]:
    """Standalone full audit: accrete fills, then report gaps."""
    run_accretion(entries, today_str=today_str)
    return run_report(entries, intermediates_root=intermediates_root, registries=registries)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audit hand-curated eligibility resources against the freshly-generated "
            "trial set: accrete filled templates into new resource versions and "
            "flag remaining coverage gaps."
        )
    )
    parser.add_argument("--resources_root", type=Path, default=DEFAULT_RESOURCES_ROOT)
    parser.add_argument(
        "--intermediates_root", type=Path, default=DEFAULT_INTERMEDIATES_ROOT
    )
    parser.add_argument(
        "--phase",
        choices=["accrete", "report", "all"],
        default="all",
        help=(
            "accrete: pre-processing (graduate fills into new versions). "
            "report: post-processing (flag gaps + rebuild templates). "
            "all: both (standalone)."
        ),
    )
    parser.add_argument("--export_date", default=None, help="ddmmyyyy stamp for new versions.")
    parser.add_argument("--log_level", default="INFO")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    today_str = args.export_date or date.today().strftime("%d%m%Y")
    entries = default_entries(args.resources_root)

    if args.phase in ("accrete", "all"):
        for path in run_accretion(entries, today_str=today_str):
            logger.info("Accreted new resource version: %s", path)
    if args.phase in ("report", "all"):
        for summary in run_report(entries, intermediates_root=args.intermediates_root):
            logger.info(
                "Resource %s: %s gap(s) remaining", summary["spec"], summary["remaining_gaps"]
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
