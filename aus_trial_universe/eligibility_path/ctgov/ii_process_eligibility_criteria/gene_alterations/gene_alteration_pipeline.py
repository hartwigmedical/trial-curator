from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

from aus_trial_universe.eligibility_path.shared.utils.load_curated_rules import (
    load_curated_rules,
)
from aus_trial_universe.eligibility_path.shared.utils.curated_files import (
    has_curated_py_files,
    iter_curated_py_files,
)
from aus_trial_universe.eligibility_path.shared.gene_alterations.mapping.generate_gene_alteration_mapping import (
    map_row_to_args,
    postprocess_args_mapping,
)
from aus_trial_universe.eligibility_path.shared.gene_alterations.mapping.gene_alteration_manual_filter import (
    build_manual_filter_cohort_level_report,
    build_manual_filter_report,
    derive_input_text_from_node,
    iter_gene_alteration_nodes,
    load_manual_reject_not_mapped_keys,
    should_drop_gene_alteration_criterion,
)
from aus_trial_universe.eligibility_path.shared.gene_alterations.mapping.gene_alteration_mapping import (
    build_gene_alteration_map,
)
from aus_trial_universe.eligibility_path.shared.gene_alterations.mapping.gene_alteration_mapping_corrections import (
    apply_mapping_corrections,
)
from aus_trial_universe.eligibility_path.shared.gene_alterations.mapping.gene_alteration_overwrite import (
    get_gene_alteration_key_from_node,
)
from aus_trial_universe.eligibility_path.shared.gene_alterations.qa.gene_alteration_conflicts import (
    build_gene_alteration_conflict_report,
)
from aus_trial_universe.eligibility_path.shared.cohorts import (
    serialize_rule_cohorts,
)
from aus_trial_universe.eligibility_path.shared.utils import (
    clean_cell_str,
    find_best_file as _find_best_file,
    is_effectively_empty,
    output_path as _output_path,
    read_tabular_file as _read_tabular_file,
    resolve_path as _resolve_path,
    SUPPORTED_OUTPUT_FORMATS,
    SUPPORTED_TABULAR_SUFFIXES,
    write_tabular_file as _write_tabular_file,
)

logger = logging.getLogger(__name__)

SUPPORTED_RESOURCE_SUFFIXES = SUPPORTED_TABULAR_SUFFIXES

DEFAULT_ELIGIBILITY_DATA_DIR = Path("data/eligibility_path/exports/intermediates/ctgov")
DEFAULT_SHARED_RESOURCES_DIR = Path("data/eligibility_path/resources")
DEFAULT_CURATED_DIR = Path("data/trial_inputs/ctgov/eligibility_curations")
DEFAULT_GENE_ALTERATION_RESOURCE_SUBDIR = Path("gene_alteration")
DEFAULT_PROCESSED_SUBDIR = Path("gene_alteration")

GENERATED_MAPPING_RESOURCE_STEM = "01_gene_alteration_mapping_resource"
MAPPING_CORRECTIONS_FILENAME = "mapping_corrections.xlsx"
MANUAL_FILTER_TRIAL_LEVEL_STEM = "02a_gene_alteration_manual_filter_trial_level"
MANUAL_FILTER_COHORT_LEVEL_STEM = "02b_gene_alteration_manual_filter_cohort_level"
MAPPED_CRITERIA_STEM = "03_gene_alteration_mapped_criteria"
TRIAL_LEVEL_STEM = "04a_gene_alteration_trial_level"
TRIAL_LEVEL_CONFLICTS_STEM = "99a_gene_alteration_conflicts_trial_level"

DEFAULT_OUTPUT_FORMAT = "tsv"

MAPPED_CRITERIA_COLUMNS: Sequence[str] = (
    "nct_id",
    "source_file",
    "rule_index",
    "criterion_index",
    "criterion_path",
    "rule_text",
    "rule_exclude",
    "cohorts",
    "under_not_criterion",
    "polarity",
    "input_text",
    "gene_input",
    "alteration_input",
    "variant_input",
    "description_input",
    "gene_alteration_curation",
    "mapping_status",
    "manual_filter_action",
    "manual_filter_reason",
)

TRIAL_LEVEL_COLUMNS: Sequence[str] = (
    "nct_id",
    "gene_alteration_inclusive",
    "gene_alteration_exclusive",
)


@dataclass(frozen=True)
class GeneAlterationPipelineInputs:
    curated_dir: Path
    gene_alteration_resource_dir: Path
    mapping_resource_file: Path
    manual_overwrite_file: Path
    output_dir: Path
    output_format: str = DEFAULT_OUTPUT_FORMAT
    fail_on_error: bool = False


@dataclass(frozen=True)
class GeneAlterationPipelineOutputs:
    generated_mapping_resource_file: Path
    manual_filter_trial_level_file: Path
    manual_filter_cohort_level_file: Path
    mapped_criteria_file: Path
    trial_level_gene_alteration_file: Path
    trial_level_conflict_report_file: Path


def _contains_nct_py_files(path: Path) -> bool:
    return has_curated_py_files(path, trial_id_prefix="NCT")


def _normalize_string(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _display_cell(value: object) -> str:
    if value is None or is_effectively_empty(value):
        return ""
    return clean_cell_str(value)


def _safe_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return _normalize_string(value).casefold() in {"true", "1", "yes", "y"}


# =============================================================================
# Mapping resource generation
# =============================================================================


def _generate_mapping_args_for_row(
    row: pd.Series,
    *,
    row_number: int,
    fail_on_error: bool,
) -> str:
    try:
        return postprocess_args_mapping(map_row_to_args(row))
    except Exception as exc:
        if fail_on_error:
            raise
        logger.exception(
            "Failed to generate Mapping_args for mapping resource row %d: %s",
            row_number,
            exc,
        )
        return ""


def build_generated_mapping_resource(
    *,
    mapping_resource_file: Path,
    fail_on_error: bool = False,
) -> pd.DataFrame:
    """
    Read the curated GeneAlterationCurationResource, regenerate the final
    Mapping_args column from the mapping input columns, and return a derived
    resource dataframe.

    This intentionally does not mutate the source file under resources/.
    The generated resource is written to the registry's gene alteration
    intermediate export directory and is the version used by the rest of the
    production pipeline.

    The persisted processed resource contains only one final mapping column:
    Mapping_args. Diagnostic columns such as Mapping_args_previous and
    Args_postprocessed are intentionally not written.
    """
    df = _read_tabular_file(mapping_resource_file)
    df.columns = [str(column).strip() for column in df.columns]

    previous_mapping_args = (
        df["Mapping_args"].fillna("").astype(str)
        if "Mapping_args" in df.columns
        else pd.Series([""] * len(df), index=df.index)
    )

    final_mapping_args: List[str] = []
    for idx, row in df.iterrows():
        final_mapping_args.append(
            _generate_mapping_args_for_row(
                row,
                row_number=int(idx) + 2,
                fail_on_error=fail_on_error,
            )
        )

    df["Mapping_args"] = final_mapping_args

    # Remove old diagnostic columns if they are present in the source workbook
    # or from a prior generated output. Downstream modules consume Mapping_args.
    df = df.drop(
        columns=[
            "Mapping_args_previous",
            "Args_postprocessed",
        ],
        errors="ignore",
    )

    final_series = pd.Series(final_mapping_args, index=df.index).fillna("").astype(str)
    changed = previous_mapping_args != final_series

    wildtype_count = final_series.str.contains(
        r"Wildtype\[",
        regex=True,
        na=False,
    ).sum()

    logger.info(
        "Generated mapping resource rows=%d changed_mapping_args=%d blank_mapping_args=%d wildtype_mapping_args=%d",
        len(df),
        int(changed.sum()),
        int((final_series.str.strip() == "").sum()),
        int(wildtype_count),
    )

    return df


# =============================================================================
# Gene-alteration criterion mapping and polarity
# =============================================================================


def _polarity_from_rule_and_not(
    *,
    rule_exclude: bool,
    under_not_criterion: bool,
) -> str:
    """
    Determine trial-level polarity for a mapped GeneAlterationCriterion.

    CTGov exclusion rules are represented as:

        Rule(exclude=True, curation=NotCriterion(...))

    In this convention, the NotCriterion wrapper under an exclusion rule is
    structural/canonical representation of the excluded condition, not an
    additional biological negation.

    Therefore a criterion is trial-level exclusive if either:
      - the source rule is an exclusion rule, or
      - the criterion is under NotCriterion in an inclusion rule.
    """
    return "exclusive" if bool(rule_exclude) or bool(under_not_criterion) else "inclusive"


def _resolve_gene_alteration_curation(
    *,
    node: Any,
    mapping: Dict[Tuple[str, str, str], str],
) -> Tuple[str, str]:
    existing = _display_cell(getattr(node, "gene_alteration_curation", ""))
    if existing:
        return existing, "already_curated"

    key = get_gene_alteration_key_from_node(node)
    if key is None:
        return "", "no_mapping_key"

    mapped = mapping.get(key)
    mapped = _display_cell(mapped)

    if not mapped:
        return "", "no_mapping_found"

    return mapped, "mapped_exact"


def build_mapped_criteria_table(
    *,
    curated_dir: Path,
    mapping_resource_file: Path,
    manual_overwrite_file: Path,
    fail_on_error: bool = False,
) -> pd.DataFrame:
    mapping = build_gene_alteration_map(mapping_resource_file)
    reject_keys = load_manual_reject_not_mapped_keys(manual_overwrite_file)

    rows: List[Dict[str, object]] = []
    skipped_files = 0

    py_files = list(iter_curated_py_files(curated_dir, trial_id_prefix="NCT"))
    logger.info("Found %d curated trial Python file(s) in %s", len(py_files), curated_dir)
    logger.info("Loaded %d gene alteration mapping key(s)", len(mapping))

    for py_path in py_files:
        try:
            rules = load_curated_rules(py_path)
            if rules is None:
                logger.warning("Skipping %s because load_curated_rules returned None", py_path)
                continue
            if isinstance(rules, (str, bytes)) or not isinstance(rules, Sequence):
                logger.warning("Skipping %s because loaded rules are not a sequence", py_path)
                continue
        except Exception as exc:
            if fail_on_error:
                raise
            skipped_files += 1
            logger.exception("Skipping %s due to load error: %s", py_path, exc)
            continue

        nct_id = py_path.stem.upper()
        criterion_counter = 0

        try:
            for rule_index, rule in enumerate(rules, start=1):
                rule_text = _display_cell(getattr(rule, "rule_text", ""))
                rule_exclude = _safe_bool(getattr(rule, "exclude", False))
                cohorts = serialize_rule_cohorts(rule)

                for node, under_not, criterion_path in iter_gene_alteration_nodes(rule):
                    criterion_counter += 1

                    input_text = derive_input_text_from_node(node)
                    should_drop = should_drop_gene_alteration_criterion(
                        trial_id=nct_id,
                        rule_text=rule_text,
                        input_text=input_text,
                        reject_keys=reject_keys,
                    )

                    if should_drop:
                        continue

                    curation, mapping_status = _resolve_gene_alteration_curation(
                        node=node,
                        mapping=mapping,
                    )

                    rows.append(
                        {
                            "nct_id": nct_id,
                            "source_file": py_path.name,
                            "rule_index": rule_index,
                            "criterion_index": criterion_counter,
                            "criterion_path": criterion_path,
                            "rule_text": rule_text,
                            "rule_exclude": rule_exclude,
                            "cohorts": cohorts,
                            "under_not_criterion": bool(under_not),
                            "polarity": _polarity_from_rule_and_not(
                                rule_exclude=rule_exclude,
                                under_not_criterion=bool(under_not),
                            ),
                            "input_text": input_text,
                            "gene_input": _display_cell(getattr(node, "gene", "")),
                            "alteration_input": _display_cell(getattr(node, "alteration", "")),
                            "variant_input": _display_cell(getattr(node, "variant", "")),
                            "description_input": _display_cell(getattr(node, "description", "")),
                            "gene_alteration_curation": curation,
                            "mapping_status": mapping_status,
                            "manual_filter_action": "keep",
                            "manual_filter_reason": "",
                        }
                    )
        except Exception as exc:
            if fail_on_error:
                raise
            skipped_files += 1
            logger.exception("Skipping %s due to traversal/mapping error: %s", py_path, exc)
            continue

    if skipped_files:
        logger.warning("Skipped %d curated file(s) due to errors", skipped_files)

    df = pd.DataFrame(rows)

    for column in MAPPED_CRITERIA_COLUMNS:
        if column not in df.columns:
            df[column] = ""

    df = df.loc[:, list(MAPPED_CRITERIA_COLUMNS)]

    logger.info(
        "Built mapped criteria table: rows=%d mapped_nonblank=%d",
        len(df),
        int((df["gene_alteration_curation"].astype(str).str.strip() != "").sum())
        if not df.empty
        else 0,
    )

    return df


# =============================================================================
# Trial-level collapse
# =============================================================================


def _split_top_level_or(expr: object) -> List[str]:
    text = _normalize_string(expr)
    if not text:
        return []

    terms: List[str] = []
    buf: List[str] = []
    paren = 0
    bracket = 0
    i = 0

    while i < len(text):
        ch = text[i]

        if ch == "(":
            paren += 1
            buf.append(ch)
            i += 1
            continue

        if ch == ")":
            paren = max(paren - 1, 0)
            buf.append(ch)
            i += 1
            continue

        if ch == "[":
            bracket += 1
            buf.append(ch)
            i += 1
            continue

        if ch == "]":
            bracket = max(bracket - 1, 0)
            buf.append(ch)
            i += 1
            continue

        if ch == "|" and paren == 0 and bracket == 0:
            part = "".join(buf).strip()
            if part:
                terms.append(part)
            buf = []
            i += 1
            continue

        buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        terms.append(tail)

    return terms


def _dedupe_preserve_order(values: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []

    for value in values:
        cleaned = _normalize_string(value)
        if not cleaned:
            continue
        if cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)

    return out


def _wrap_not(term: str) -> str:
    term = _normalize_string(term)
    if not term:
        return ""
    if term.startswith("NOT(") and term.endswith(")"):
        return term
    return f"NOT({term})"


def collapse_to_trial_level(mapped_df: pd.DataFrame) -> pd.DataFrame:
    if mapped_df.empty:
        return pd.DataFrame(columns=list(TRIAL_LEVEL_COLUMNS))

    required = ["nct_id", "polarity", "gene_alteration_curation"]
    missing = [column for column in required if column not in mapped_df.columns]
    if missing:
        raise ValueError(f"Mapped criteria table missing required columns: {missing}")

    output_rows: List[Dict[str, str]] = []

    grouped = mapped_df.groupby("nct_id", sort=False, dropna=False)

    for nct_id, group in grouped:
        normalized_nct_id = _normalize_string(nct_id)
        if not normalized_nct_id:
            continue

        inclusive_terms: List[str] = []
        exclusive_terms: List[str] = []

        for _, row in group.iterrows():
            curation = _normalize_string(row.get("gene_alteration_curation", ""))
            if not curation:
                continue

            terms = _split_top_level_or(curation)
            polarity = _normalize_string(row.get("polarity", "")).casefold()

            if polarity == "exclusive":
                exclusive_terms.extend(_wrap_not(term) for term in terms if term)
            else:
                inclusive_terms.extend(term for term in terms if term)

        inclusive_terms = _dedupe_preserve_order(inclusive_terms)
        exclusive_terms = _dedupe_preserve_order(exclusive_terms)

        if not inclusive_terms and not exclusive_terms:
            continue

        output_rows.append(
            {
                "nct_id": normalized_nct_id,
                "gene_alteration_inclusive": " | ".join(inclusive_terms),
                "gene_alteration_exclusive": " & ".join(exclusive_terms),
            }
        )

    return pd.DataFrame(output_rows, columns=list(TRIAL_LEVEL_COLUMNS))


# =============================================================================
# Discovery / validation / pipeline orchestration
# =============================================================================


def discover_pipeline_inputs(
    *,
    repo_root: Path,
    eligibility_data_dir: Path,
    curated_dir: Optional[Path],
    gene_alteration_resource_dir: Optional[Path],
    mapping_resource_file: Optional[Path],
    manual_overwrite_file: Optional[Path],
    output_dir: Optional[Path],
    output_format: str,
    fail_on_error: bool,
) -> GeneAlterationPipelineInputs:
    repo_root = repo_root.resolve()

    resolved_eligibility_data_dir = _resolve_path(eligibility_data_dir, repo_root)

    resolved_curated_dir = (
        _resolve_path(curated_dir, repo_root)
        if curated_dir is not None
        else _resolve_path(DEFAULT_CURATED_DIR, repo_root)
    )

    resolved_gene_alteration_resource_dir = (
        _resolve_path(gene_alteration_resource_dir, repo_root)
        if gene_alteration_resource_dir is not None
        else _resolve_path(
            DEFAULT_SHARED_RESOURCES_DIR / DEFAULT_GENE_ALTERATION_RESOURCE_SUBDIR,
            repo_root,
        )
    )

    resolved_output_dir = (
        _resolve_path(output_dir, repo_root)
        if output_dir is not None
        else resolved_eligibility_data_dir / DEFAULT_PROCESSED_SUBDIR
    )

    resolved_mapping_resource_file = (
        _resolve_path(mapping_resource_file, repo_root)
        if mapping_resource_file is not None
        else _find_best_file(
            resolved_gene_alteration_resource_dir,
            token_groups=[["genealteration", "gene_alteration"], ["curationresource", "curation_resource"]],
            allowed_suffixes=SUPPORTED_RESOURCE_SUFFIXES,
            recursive=False,
            label="gene alteration curation resource",
        )
    )

    resolved_manual_overwrite_file = (
        _resolve_path(manual_overwrite_file, repo_root)
        if manual_overwrite_file is not None
        else _find_best_file(
            resolved_gene_alteration_resource_dir,
            token_groups=[["manual"], ["overwrite"]],
            allowed_suffixes=SUPPORTED_RESOURCE_SUFFIXES,
            recursive=False,
            label="gene alteration manual overwrite resource",
        )
    )

    inputs = GeneAlterationPipelineInputs(
        curated_dir=resolved_curated_dir,
        gene_alteration_resource_dir=resolved_gene_alteration_resource_dir,
        mapping_resource_file=resolved_mapping_resource_file,
        manual_overwrite_file=resolved_manual_overwrite_file,
        output_dir=resolved_output_dir,
        output_format=output_format,
        fail_on_error=fail_on_error,
    )

    validate_pipeline_inputs(inputs)
    return inputs


def validate_pipeline_inputs(inputs: GeneAlterationPipelineInputs) -> None:
    if not inputs.curated_dir.exists():
        raise FileNotFoundError(f"Curated rules path does not exist: {inputs.curated_dir}")

    if not _contains_nct_py_files(inputs.curated_dir):
        raise FileNotFoundError(
            f"Curated rules path does not contain NCT*.py files directly: {inputs.curated_dir}"
        )

    if not inputs.gene_alteration_resource_dir.exists():
        raise FileNotFoundError(
            f"Gene-alteration resource directory does not exist: {inputs.gene_alteration_resource_dir}"
        )

    if not inputs.gene_alteration_resource_dir.is_dir():
        raise ValueError(
            f"Gene-alteration resource path is not a directory: {inputs.gene_alteration_resource_dir}"
        )

    mapping_corrections_file = (
        inputs.gene_alteration_resource_dir / MAPPING_CORRECTIONS_FILENAME
    )

    required_files = [
        ("gene alteration mapping resource", inputs.mapping_resource_file),
        ("gene alteration manual overwrite resource", inputs.manual_overwrite_file),
        ("gene alteration mapping corrections resource", mapping_corrections_file),
    ]

    for label, path in required_files:
        if not path.exists():
            raise FileNotFoundError(f"{label} does not exist: {path}")
        if not path.is_file():
            raise ValueError(f"{label} is not a file: {path}")


def run_gene_alteration_pipeline(
    inputs: GeneAlterationPipelineInputs,
) -> GeneAlterationPipelineOutputs:
    inputs.output_dir.mkdir(parents=True, exist_ok=True)

    generated_mapping_resource_file = _output_path(
        inputs.output_dir,
        GENERATED_MAPPING_RESOURCE_STEM,
        inputs.output_format,
    )
    manual_filter_trial_level_file = _output_path(
        inputs.output_dir,
        MANUAL_FILTER_TRIAL_LEVEL_STEM,
        inputs.output_format,
    )
    manual_filter_cohort_level_file = _output_path(
        inputs.output_dir,
        MANUAL_FILTER_COHORT_LEVEL_STEM,
        inputs.output_format,
    )
    mapped_criteria_file = _output_path(
        inputs.output_dir,
        MAPPED_CRITERIA_STEM,
        inputs.output_format,
    )
    trial_level_file = _output_path(
        inputs.output_dir,
        TRIAL_LEVEL_STEM,
        inputs.output_format,
    )
    trial_level_conflict_report_file = _output_path(
        inputs.output_dir,
        TRIAL_LEVEL_CONFLICTS_STEM,
        inputs.output_format,
    )

    mapping_corrections_file = (
        inputs.gene_alteration_resource_dir / MAPPING_CORRECTIONS_FILENAME
    )

    logger.info("Gene-alteration pipeline inputs:")
    logger.info("  curated_dir:                    %s", inputs.curated_dir)
    logger.info("  gene_alteration_resource_dir:   %s", inputs.gene_alteration_resource_dir)
    logger.info("  mapping_resource_file:          %s", inputs.mapping_resource_file)
    logger.info("  mapping_corrections_file:       %s", mapping_corrections_file)
    logger.info("  manual_overwrite_file:          %s", inputs.manual_overwrite_file)
    logger.info("  output_dir:                     %s", inputs.output_dir)
    logger.info("  output_format:                  %s", inputs.output_format)

    logger.info("[1/5] Generating production Mapping_args from curation resource")
    generated_mapping_resource_df = build_generated_mapping_resource(
        mapping_resource_file=inputs.mapping_resource_file,
        fail_on_error=inputs.fail_on_error,
    )

    logger.info(
        "Applying reviewed Mapping_args correction overlay: %s",
        mapping_corrections_file,
    )
    generated_mapping_resource_df = apply_mapping_corrections(
        generated_mapping_resource_df,
        mapping_corrections_file,
    )

    _write_tabular_file(generated_mapping_resource_df, generated_mapping_resource_file)
    logger.info(
        "Wrote corrected generated mapping resource: %s rows=%d",
        generated_mapping_resource_file,
        len(generated_mapping_resource_df),
    )

    logger.info("[2/5] Building manual reject/not_mapped filter reports")
    manual_filter_trial_level_df = build_manual_filter_report(
        input_dir=inputs.curated_dir,
        manual_overwrite_file=inputs.manual_overwrite_file,
        fail_on_error=inputs.fail_on_error,
    )
    _write_tabular_file(manual_filter_trial_level_df, manual_filter_trial_level_file)
    logger.info(
        "Wrote trial-level manual filter report: %s rows=%d",
        manual_filter_trial_level_file,
        len(manual_filter_trial_level_df),
    )

    manual_filter_cohort_level_df = build_manual_filter_cohort_level_report(
        trial_level_df=manual_filter_trial_level_df,
        input_dir=inputs.curated_dir,
        fail_on_error=inputs.fail_on_error,
    )
    _write_tabular_file(manual_filter_cohort_level_df, manual_filter_cohort_level_file)
    logger.info(
        "Wrote cohort-level manual filter report: %s rows=%d",
        manual_filter_cohort_level_file,
        len(manual_filter_cohort_level_df),
    )

    logger.info("[3/5] Building mapped GeneAlterationCriterion table")
    mapped_criteria_df = build_mapped_criteria_table(
        curated_dir=inputs.curated_dir,
        mapping_resource_file=generated_mapping_resource_file,
        manual_overwrite_file=inputs.manual_overwrite_file,
        fail_on_error=inputs.fail_on_error,
    )
    _write_tabular_file(mapped_criteria_df, mapped_criteria_file)
    logger.info(
        "Wrote mapped criteria table: %s rows=%d",
        mapped_criteria_file,
        len(mapped_criteria_df),
    )

    logger.info("[4/5] Collapsing mapped criteria to trial-level gene alteration output")
    trial_level_df = collapse_to_trial_level(mapped_criteria_df)
    _write_tabular_file(trial_level_df, trial_level_file)
    logger.info(
        "Wrote trial-level gene alteration table: %s rows=%d",
        trial_level_file,
        len(trial_level_df),
    )

    logger.info("[5/5] Building trial-level gene alteration conflict QA report")
    trial_level_conflict_report_df = build_gene_alteration_conflict_report(mapped_criteria_df)
    _write_tabular_file(trial_level_conflict_report_df, trial_level_conflict_report_file)
    logger.info(
        "Wrote trial-level gene alteration conflict report: %s rows=%d",
        trial_level_conflict_report_file,
        len(trial_level_conflict_report_df),
    )

    return GeneAlterationPipelineOutputs(
        generated_mapping_resource_file=generated_mapping_resource_file,
        manual_filter_trial_level_file=manual_filter_trial_level_file,
        manual_filter_cohort_level_file=manual_filter_cohort_level_file,
        mapped_criteria_file=mapped_criteria_file,
        trial_level_gene_alteration_file=trial_level_file,
        trial_level_conflict_report_file=trial_level_conflict_report_file,
    )


# =============================================================================
# CLI
# =============================================================================


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the CTGov gene-alteration eligibility pipeline: regenerate "
            "Mapping_args from the curation resource, apply reviewed mapping "
            "corrections, apply manual reject/not_mapped filter, map remaining "
            "criteria, collapse to trial-level output, and produce trial-level QA reports."
        )
    )

    parser.add_argument(
        "--repo_root",
        type=Path,
        default=Path.cwd(),
        help="Repository root. Defaults to current working directory.",
    )
    parser.add_argument(
        "--eligibility_data_dir",
        type=Path,
        default=DEFAULT_ELIGIBILITY_DATA_DIR,
        help=(
            "CTGov eligibility intermediate export root. Defaults to "
            "data/eligibility_path/exports/intermediates/ctgov."
        ),
    )
    parser.add_argument(
        "--curated_dir",
        type=Path,
        default=None,
        help=(
            "Directory containing original curated NCT*.py files, or a single NCT*.py file. "
            "Defaults to data/trial_inputs/ctgov/eligibility_curations."
        ),
    )
    parser.add_argument(
        "--gene_alteration_resource_dir",
        type=Path,
        default=None,
        help=(
            "Directory containing GeneAlterationCurationResource, mapping corrections, "
            "and manual overwrite resource. Defaults to "
            "data/eligibility_path/resources/gene_alteration."
        ),
    )
    parser.add_argument(
        "--mapping_resource_file",
        type=Path,
        default=None,
        help=(
            "Optional explicit GeneAlterationCurationResource file. "
            "If omitted, the pipeline discovers the most recent matching file."
        ),
    )
    parser.add_argument(
        "--manual_overwrite_file",
        type=Path,
        default=None,
        help=(
            "Optional explicit manual overwrite file. "
            "If omitted, the pipeline discovers the most recent manual overwrite file."
        ),
    )
    parser.add_argument(
        "--output_dir",
        type=Path,
        default=None,
        help=(
            "Output directory for processed gene-alteration files. "
            "Defaults to eligibility_data_dir/gene_alteration."
        ),
    )
    parser.add_argument(
        "--output_format",
        default=DEFAULT_OUTPUT_FORMAT,
        choices=sorted(SUPPORTED_OUTPUT_FORMATS),
        help="Output format for processed files. Defaults to tsv.",
    )
    parser.add_argument(
        "--fail_on_error",
        action="store_true",
        help="Raise immediately on the first curated rule file or mapping-resource row that fails.",
    )
    parser.add_argument(
        "--log_level",
        default="INFO",
        help="Logging level: DEBUG, INFO, WARNING, ERROR.",
    )

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    inputs = discover_pipeline_inputs(
        repo_root=args.repo_root,
        eligibility_data_dir=args.eligibility_data_dir,
        curated_dir=args.curated_dir,
        gene_alteration_resource_dir=args.gene_alteration_resource_dir,
        mapping_resource_file=args.mapping_resource_file,
        manual_overwrite_file=args.manual_overwrite_file,
        output_dir=args.output_dir,
        output_format=args.output_format,
        fail_on_error=args.fail_on_error,
    )

    outputs = run_gene_alteration_pipeline(inputs)

    logger.info("Gene-alteration pipeline complete.")
    logger.info(
        "generated_mapping_resource_file:   %s",
        outputs.generated_mapping_resource_file,
    )
    logger.info(
        "manual_filter_trial_level_file:    %s",
        outputs.manual_filter_trial_level_file,
    )
    logger.info(
        "manual_filter_cohort_level_file:   %s",
        outputs.manual_filter_cohort_level_file,
    )
    logger.info("mapped_criteria_file:              %s", outputs.mapped_criteria_file)
    logger.info(
        "trial_level_gene_alteration_file:  %s",
        outputs.trial_level_gene_alteration_file,
    )
    logger.info(
        "trial_level_conflict_report_file:  %s",
        outputs.trial_level_conflict_report_file,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
