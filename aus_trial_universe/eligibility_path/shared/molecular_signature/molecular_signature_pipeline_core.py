from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import pandas as pd

from aus_trial_universe.eligibility_path.shared.utils.load_curated_rules import (
    load_curated_rules,
)
from aus_trial_universe.eligibility_path.shared.utils.curated_files import (
    has_curated_py_files,
    iter_curated_py_files,
)
from aus_trial_universe.eligibility_path.shared.molecular_signature.mapping.molecular_signature_mapping import (
    build_molecular_signature_map,
)
from aus_trial_universe.eligibility_path.shared.molecular_signature.mapping.molecular_signature_overwrite import (
    get_molecular_signature_key_from_node,
)
from aus_trial_universe.eligibility_path.shared.utils import (
    clean_cell_str,
    find_best_file as _find_best_file,
    normalize_string as _normalize_string,
    output_path as _output_path,
    read_tabular_file as _read_tabular_file,
    resolve_path as _resolve_path,
    safe_bool as _safe_bool,
    SUPPORTED_OUTPUT_FORMATS,
    SUPPORTED_TABULAR_SUFFIXES,
    write_tabular_file as _write_tabular_file,
)
from aus_trial_universe.eligibility_path.shared.cohorts import (
    serialize_rule_cohorts,
)
from aus_trial_universe.eligibility_path.shared.utils.term_expression import (
    dedupe_preserve_order as _dedupe_preserve_order,
    split_top_level_or as _split_top_level_or,
    wrap_not as _wrap_not,
)

_display_cell = clean_cell_str

logger = logging.getLogger(__name__)

SUPPORTED_RESOURCE_SUFFIXES = SUPPORTED_TABULAR_SUFFIXES

DEFAULT_SHARED_RESOURCES_DIR = Path("data/eligibility_path/resources")
DEFAULT_MOLECULAR_SIGNATURE_RESOURCE_SUBDIR = Path("molecular_signature")
DEFAULT_PROCESSED_SUBDIR = Path("molecular_signature")

MAPPING_RESOURCE_STEM = "01_molecular_signature_mapping_resource"
MAPPED_CRITERIA_STEM = "02_molecular_signature_mapped_criteria"
TRIAL_LEVEL_STEM = "03a_molecular_signature_trial_level"

DEFAULT_OUTPUT_FORMAT = "tsv"

def _mapped_criteria_columns(trial_id_column: str) -> Sequence[str]:
    return (
        trial_id_column,
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
        "signature_input",
        "description_input",
        "molecular_signature_curation",
        "mapping_status",
    )


def _trial_level_columns(trial_id_column: str) -> Sequence[str]:
    return (
        trial_id_column,
        "molecular_signature_inclusive",
        "molecular_signature_exclusive",
    )


@dataclass(frozen=True)
class MolecularSignaturePipelineInputs:
    curated_dir: Path
    molecular_signature_resource_dir: Path
    mapping_resource_file: Path
    output_dir: Path
    output_format: str = DEFAULT_OUTPUT_FORMAT
    fail_on_error: bool = False


@dataclass(frozen=True)
class MolecularSignaturePipelineOutputs:
    mapping_resource_file: Path
    mapped_criteria_file: Path
    trial_level_molecular_signature_file: Path


@dataclass(frozen=True)
class MolecularSignatureRegistrySpec:
    """Registry-specific knobs for the molecular-signature pipeline.

    Everything else in this module is registry-independent; each registry
    (CTGov, ANZCTR) supplies one of these and binds it in a thin adapter that
    re-exports the public surface the rest of the pipeline imports.
    """

    registry_label: str
    trial_id_column: str
    trial_id_prefix: str
    default_eligibility_data_dir: Path
    default_curated_dir: Path
    normalize_trial_id_from_stem: Callable[[str], str]


# =============================================================================
# Mapping resource
# =============================================================================


def build_processed_mapping_resource(
    *,
    mapping_resource_file: Path,
) -> pd.DataFrame:
    """
    Copy the curated molecular-signature resource into processed/ with cleaned column names and stable string reading.
    """
    df = _read_tabular_file(mapping_resource_file)
    df.columns = [str(column).strip() for column in df.columns]

    required = [
        "Signature_lookup",
        "Findings_curation",
    ]
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError(
            f"Molecular signature mapping resource missing required columns: {missing}"
        )

    logger.info(
        "Loaded molecular signature mapping resource: rows=%d blank_findings_curation=%d",
        len(df),
        int((df["Findings_curation"].astype(str).str.strip() == "").sum()),
    )

    return df


# =============================================================================
# MolecularSignatureCriterion traversal
# =============================================================================


_PRIMITIVE_TYPES = (
    str,
    bytes,
    int,
    float,
    bool,
    type(None),
    Path,
)


def _is_not_criterion_node(node: Any) -> bool:
    name = type(node).__name__.casefold()
    return (
        name in {"notcriterion", "notnode", "not", "negationcriterion"}
        or (name.startswith("not") and "criterion" in name)
    )


def _iter_object_children(value: Any) -> Iterable[Tuple[str, Any]]:
    if isinstance(value, _PRIMITIVE_TYPES):
        return

    if isinstance(value, dict):
        for key, child in value.items():
            yield f"[{key!r}]", child
        return

    if isinstance(value, (list, tuple)):
        for idx, child in enumerate(value):
            yield f"[{idx}]", child
        return

    if isinstance(value, set):
        for idx, child in enumerate(sorted(value, key=repr)):
            yield f"[{idx}]", child
        return

    d = getattr(value, "__dict__", None)
    if isinstance(d, dict):
        for attr, child in d.items():
            if attr.startswith("_"):
                continue
            yield f".{attr}", child


def _walk_for_molecular_signature_nodes(
    value: Any,
    *,
    path: str,
    under_not_criterion: bool,
    seen: Set[int],
) -> Iterable[Tuple[Any, bool, str]]:
    if isinstance(value, _PRIMITIVE_TYPES):
        return

    value_id = id(value)
    if value_id in seen:
        return
    seen.add(value_id)

    current_under_not = under_not_criterion or _is_not_criterion_node(value)

    if type(value).__name__ == "MolecularSignatureCriterion":
        yield value, current_under_not, path

    for child_path, child in _iter_object_children(value):
        yield from _walk_for_molecular_signature_nodes(
            child,
            path=f"{path}{child_path}",
            under_not_criterion=current_under_not,
            seen=seen,
        )


def iter_molecular_signature_nodes(
    rule: Any,
    *,
    rule_index: int,
) -> Iterable[Tuple[Any, bool, str]]:
    yield from _walk_for_molecular_signature_nodes(
        rule,
        path=f"rule[{rule_index}]",
        under_not_criterion=False,
        seen=set(),
    )


# =============================================================================
# Criterion mapping and polarity
# =============================================================================


def _polarity_from_rule_and_not(
    *,
    rule_exclude: bool,
    under_not_criterion: bool,
) -> str:
    """
    Determine trial-level polarity for a mapped MolecularSignatureCriterion.

    Curated exclusion rules are represented as:

        Rule(exclude=True, curation=NotCriterion(...))

    In this convention, the NotCriterion wrapper under an exclusion rule is
    structural/canonical representation of the excluded condition, not an
    additional biological negation.

    Therefore a criterion is trial-level exclusive if either:
      - the source rule is an exclusion rule, or
      - the criterion is under NotCriterion in an inclusion rule.
    """
    return "exclusive" if bool(rule_exclude) or bool(under_not_criterion) else "inclusive"


def _derive_input_text_from_node(node: Any) -> str:
    description = _display_cell(getattr(node, "description", ""))
    if description:
        return description

    signature = _display_cell(getattr(node, "signature", ""))
    return signature


def _resolve_molecular_signature_curation(
    *,
    node: Any,
    mapping: Dict[str, str],
) -> Tuple[str, str]:
    existing = _display_cell(getattr(node, "molecular_signature_curation", ""))
    if existing:
        return existing, "already_curated"

    key = get_molecular_signature_key_from_node(node)
    if key is None:
        return "", "no_mapping_key"

    mapped = mapping.get(key)
    mapped = _display_cell(mapped)

    if not mapped:
        return "", "no_mapping_found"

    return mapped, "mapped_exact"


def build_mapped_criteria_table(
    *,
    spec: MolecularSignatureRegistrySpec,
    curated_dir: Path,
    mapping_resource_file: Path,
    fail_on_error: bool = False,
) -> pd.DataFrame:
    mapping = build_molecular_signature_map(mapping_resource_file)

    rows: List[Dict[str, object]] = []
    skipped_files = 0

    py_files = list(iter_curated_py_files(curated_dir, trial_id_prefix=spec.trial_id_prefix))
    logger.info("Found %d curated trial Python file(s) in %s", len(py_files), curated_dir)
    logger.info("Loaded %d molecular signature mapping key(s)", len(mapping))

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

        trial_id = spec.normalize_trial_id_from_stem(py_path.stem)
        criterion_counter = 0

        try:
            for rule_index, rule in enumerate(rules, start=1):
                rule_text = _display_cell(getattr(rule, "rule_text", ""))
                rule_exclude = _safe_bool(getattr(rule, "exclude", False))
                cohorts = serialize_rule_cohorts(rule)

                for node, under_not, criterion_path in iter_molecular_signature_nodes(
                    rule,
                    rule_index=rule_index,
                ):
                    criterion_counter += 1

                    curation, mapping_status = _resolve_molecular_signature_curation(
                        node=node,
                        mapping=mapping,
                    )

                    signature_input = _display_cell(getattr(node, "signature", ""))
                    description_input = _display_cell(getattr(node, "description", ""))

                    rows.append(
                        {
                            spec.trial_id_column: trial_id,
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
                            "input_text": _derive_input_text_from_node(node),
                            "signature_input": signature_input,
                            "description_input": description_input,
                            "molecular_signature_curation": curation,
                            "mapping_status": mapping_status,
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

    mapped_columns = _mapped_criteria_columns(spec.trial_id_column)
    for column in mapped_columns:
        if column not in df.columns:
            df[column] = ""

    df = df.loc[:, list(mapped_columns)]

    logger.info(
        "Built molecular signature mapped criteria table: rows=%d mapped_nonblank=%d",
        len(df),
        int((df["molecular_signature_curation"].astype(str).str.strip() != "").sum())
        if not df.empty
        else 0,
    )

    return df


# =============================================================================
# Trial-level collapse
# =============================================================================


def collapse_to_trial_level(
    mapped_df: pd.DataFrame,
    *,
    trial_id_column: str,
) -> pd.DataFrame:
    trial_level_columns = _trial_level_columns(trial_id_column)
    if mapped_df.empty:
        return pd.DataFrame(columns=list(trial_level_columns))

    required = [trial_id_column, "polarity", "molecular_signature_curation"]
    missing = [column for column in required if column not in mapped_df.columns]
    if missing:
        raise ValueError(f"Mapped criteria table missing required columns: {missing}")

    output_rows: List[Dict[str, str]] = []

    grouped = mapped_df.groupby(trial_id_column, sort=False, dropna=False)

    for trial_id_value, group in grouped:
        normalized_trial_id = _normalize_string(trial_id_value)
        if not normalized_trial_id:
            continue

        inclusive_terms: List[str] = []
        exclusive_terms: List[str] = []

        for _, row in group.iterrows():
            curation = _normalize_string(row.get("molecular_signature_curation", ""))
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
                trial_id_column: normalized_trial_id,
                "molecular_signature_inclusive": " | ".join(inclusive_terms),
                "molecular_signature_exclusive": " & ".join(exclusive_terms),
            }
        )

    return pd.DataFrame(output_rows, columns=list(trial_level_columns))


# =============================================================================
# Discovery / validation / pipeline orchestration
# =============================================================================


def discover_pipeline_inputs(
    *,
    spec: MolecularSignatureRegistrySpec,
    repo_root: Path,
    eligibility_data_dir: Path,
    curated_dir: Optional[Path],
    molecular_signature_resource_dir: Optional[Path],
    mapping_resource_file: Optional[Path],
    output_dir: Optional[Path],
    output_format: str,
    fail_on_error: bool,
) -> MolecularSignaturePipelineInputs:
    repo_root = repo_root.resolve()

    resolved_eligibility_data_dir = _resolve_path(eligibility_data_dir, repo_root)

    resolved_curated_dir = (
        _resolve_path(curated_dir, repo_root)
        if curated_dir is not None
        else _resolve_path(spec.default_curated_dir, repo_root)
    )

    resolved_molecular_signature_resource_dir = (
        _resolve_path(molecular_signature_resource_dir, repo_root)
        if molecular_signature_resource_dir is not None
        else _resolve_path(
            DEFAULT_SHARED_RESOURCES_DIR / DEFAULT_MOLECULAR_SIGNATURE_RESOURCE_SUBDIR,
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
            resolved_molecular_signature_resource_dir,
            token_groups=[
                ["molecularsignature", "molecular_signature"],
                ["curationresource", "curation_resource"],
            ],
            allowed_suffixes=SUPPORTED_RESOURCE_SUFFIXES,
            recursive=False,
            label="molecular signature curation resource",
        )
    )

    inputs = MolecularSignaturePipelineInputs(
        curated_dir=resolved_curated_dir,
        molecular_signature_resource_dir=resolved_molecular_signature_resource_dir,
        mapping_resource_file=resolved_mapping_resource_file,
        output_dir=resolved_output_dir,
        output_format=output_format,
        fail_on_error=fail_on_error,
    )

    validate_pipeline_inputs(inputs, spec=spec)
    return inputs


def validate_pipeline_inputs(
    inputs: MolecularSignaturePipelineInputs,
    *,
    spec: MolecularSignatureRegistrySpec,
) -> None:
    if not inputs.curated_dir.exists():
        raise FileNotFoundError(f"Curated rules path does not exist: {inputs.curated_dir}")

    if not has_curated_py_files(inputs.curated_dir, trial_id_prefix=spec.trial_id_prefix):
        raise FileNotFoundError(
            f"Curated rules path does not contain {spec.trial_id_prefix}*.py files directly: {inputs.curated_dir}"
        )

    if not inputs.molecular_signature_resource_dir.exists():
        raise FileNotFoundError(
            f"Molecular-signature resource directory does not exist: "
            f"{inputs.molecular_signature_resource_dir}"
        )

    if not inputs.molecular_signature_resource_dir.is_dir():
        raise ValueError(
            f"Molecular-signature resource path is not a directory: "
            f"{inputs.molecular_signature_resource_dir}"
        )

    if not inputs.mapping_resource_file.exists():
        raise FileNotFoundError(
            f"Molecular signature mapping resource does not exist: {inputs.mapping_resource_file}"
        )

    if not inputs.mapping_resource_file.is_file():
        raise ValueError(
            f"Molecular signature mapping resource is not a file: {inputs.mapping_resource_file}"
        )


def run_molecular_signature_pipeline(
    inputs: MolecularSignaturePipelineInputs,
    *,
    spec: MolecularSignatureRegistrySpec,
) -> MolecularSignaturePipelineOutputs:
    inputs.output_dir.mkdir(parents=True, exist_ok=True)

    mapping_resource_output_file = _output_path(
        inputs.output_dir,
        MAPPING_RESOURCE_STEM,
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

    logger.info("Molecular-signature pipeline inputs:")
    logger.info("  curated_dir:                        %s", inputs.curated_dir)
    logger.info("  molecular_signature_resource_dir:   %s", inputs.molecular_signature_resource_dir)
    logger.info("  mapping_resource_file:              %s", inputs.mapping_resource_file)
    logger.info("  output_dir:                         %s", inputs.output_dir)
    logger.info("  output_format:                      %s", inputs.output_format)

    logger.info("[1/3] Writing processed molecular signature mapping resource")
    mapping_resource_df = build_processed_mapping_resource(
        mapping_resource_file=inputs.mapping_resource_file,
    )
    _write_tabular_file(mapping_resource_df, mapping_resource_output_file)
    logger.info(
        "Wrote processed molecular signature mapping resource: %s rows=%d",
        mapping_resource_output_file,
        len(mapping_resource_df),
    )

    logger.info("[2/3] Building mapped MolecularSignatureCriterion table")
    mapped_criteria_df = build_mapped_criteria_table(
        spec=spec,
        curated_dir=inputs.curated_dir,
        mapping_resource_file=mapping_resource_output_file,
        fail_on_error=inputs.fail_on_error,
    )
    _write_tabular_file(mapped_criteria_df, mapped_criteria_file)
    logger.info(
        "Wrote molecular signature mapped criteria table: %s rows=%d",
        mapped_criteria_file,
        len(mapped_criteria_df),
    )

    logger.info("[3/3] Collapsing mapped criteria to trial-level molecular signature output")
    trial_level_df = collapse_to_trial_level(
        mapped_criteria_df, trial_id_column=spec.trial_id_column
    )
    _write_tabular_file(trial_level_df, trial_level_file)
    logger.info(
        "Wrote trial-level molecular signature table: %s rows=%d",
        trial_level_file,
        len(trial_level_df),
    )

    return MolecularSignaturePipelineOutputs(
        mapping_resource_file=mapping_resource_output_file,
        mapped_criteria_file=mapped_criteria_file,
        trial_level_molecular_signature_file=trial_level_file,
    )


# =============================================================================
# CLI
# =============================================================================


def main(spec: MolecularSignatureRegistrySpec, argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            f"Run the {spec.registry_label} molecular-signature eligibility pipeline: copy the "
            "curated mapping resource into processed/, map MolecularSignatureCriterion "
            "nodes, and collapse mapped criteria to trial-level output."
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
        default=spec.default_eligibility_data_dir,
        help=(
            f"{spec.registry_label} eligibility intermediate export root. "
            f"Defaults to {spec.default_eligibility_data_dir}."
        ),
    )
    parser.add_argument(
        "--curated_dir",
        type=Path,
        default=None,
        help=(
            f"Directory containing original curated {spec.trial_id_prefix}*.py files, or a single "
            f"{spec.trial_id_prefix}*.py file. Defaults to {spec.default_curated_dir}."
        ),
    )
    parser.add_argument(
        "--molecular_signature_resource_dir",
        type=Path,
        default=None,
        help=(
            "Directory containing MolecularSignatureCurationResource. "
            "Defaults to data/eligibility_path/resources/molecular_signature."
        ),
    )
    parser.add_argument(
        "--mapping_resource_file",
        type=Path,
        default=None,
        help=(
            "Optional explicit MolecularSignatureCurationResource file. "
            "If omitted, the pipeline discovers the most recent matching file."
        ),
    )
    parser.add_argument(
        "--output_dir",
        type=Path,
        default=None,
        help=(
            "Output directory for processed molecular-signature files. "
            "Defaults to eligibility_data_dir/molecular_signature."
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
        help="Raise immediately on the first curated rule file or mapping row that fails.",
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
        spec=spec,
        repo_root=args.repo_root,
        eligibility_data_dir=args.eligibility_data_dir,
        curated_dir=args.curated_dir,
        molecular_signature_resource_dir=args.molecular_signature_resource_dir,
        mapping_resource_file=args.mapping_resource_file,
        output_dir=args.output_dir,
        output_format=args.output_format,
        fail_on_error=args.fail_on_error,
    )

    outputs = run_molecular_signature_pipeline(inputs, spec=spec)

    logger.info("Molecular-signature pipeline complete.")
    logger.info("mapping_resource_file:                  %s", outputs.mapping_resource_file)
    logger.info("mapped_criteria_file:                   %s", outputs.mapped_criteria_file)
    logger.info(
        "trial_level_molecular_signature_file:   %s",
        outputs.trial_level_molecular_signature_file,
    )

    return 0
