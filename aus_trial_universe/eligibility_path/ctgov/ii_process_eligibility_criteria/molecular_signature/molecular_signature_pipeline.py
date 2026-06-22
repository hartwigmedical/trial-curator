from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

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
    is_effectively_empty,
    output_path as _output_path,
    read_tabular_file as _read_tabular_file,
    resolve_path as _resolve_path,
    SUPPORTED_OUTPUT_FORMATS,
    SUPPORTED_TABULAR_SUFFIXES,
    write_tabular_file as _write_tabular_file,
)
from aus_trial_universe.eligibility_path.shared.cohorts import (
    serialize_rule_cohorts,
)

logger = logging.getLogger(__name__)

SUPPORTED_RESOURCE_SUFFIXES = SUPPORTED_TABULAR_SUFFIXES

DEFAULT_ELIGIBILITY_DATA_DIR = Path("data/eligibility_path/exports/intermediates/ctgov")
DEFAULT_SHARED_RESOURCES_DIR = Path("data/eligibility_path/resources")
DEFAULT_CURATED_DIR = Path("data/trial_inputs/ctgov/eligibility_curations")
DEFAULT_MOLECULAR_SIGNATURE_RESOURCE_SUBDIR = Path("molecular_signature")
DEFAULT_PROCESSED_SUBDIR = Path("molecular_signature")

MAPPING_RESOURCE_STEM = "01_molecular_signature_mapping_resource"
MAPPED_CRITERIA_STEM = "02_molecular_signature_mapped_criteria"
TRIAL_LEVEL_STEM = "03_trial_level_molecular_signature"

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
    "signature_input",
    "description_input",
    "molecular_signature_curation",
    "mapping_status",
)

TRIAL_LEVEL_COLUMNS: Sequence[str] = (
    "nct_id",
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
    curated_dir: Path,
    mapping_resource_file: Path,
    fail_on_error: bool = False,
) -> pd.DataFrame:
    mapping = build_molecular_signature_map(mapping_resource_file)

    rows: List[Dict[str, object]] = []
    skipped_files = 0

    py_files = list(iter_curated_py_files(curated_dir, trial_id_prefix="NCT"))
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

        nct_id = py_path.stem.upper()
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

    for column in MAPPED_CRITERIA_COLUMNS:
        if column not in df.columns:
            df[column] = ""

    df = df.loc[:, list(MAPPED_CRITERIA_COLUMNS)]

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

    required = ["nct_id", "polarity", "molecular_signature_curation"]
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
                "nct_id": normalized_nct_id,
                "molecular_signature_inclusive": " | ".join(inclusive_terms),
                "molecular_signature_exclusive": " & ".join(exclusive_terms),
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
        else _resolve_path(DEFAULT_CURATED_DIR, repo_root)
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

    validate_pipeline_inputs(inputs)
    return inputs


def validate_pipeline_inputs(inputs: MolecularSignaturePipelineInputs) -> None:
    if not inputs.curated_dir.exists():
        raise FileNotFoundError(f"Curated rules path does not exist: {inputs.curated_dir}")

    if not _contains_nct_py_files(inputs.curated_dir):
        raise FileNotFoundError(
            f"Curated rules path does not contain NCT*.py files directly: {inputs.curated_dir}"
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
    trial_level_df = collapse_to_trial_level(mapped_criteria_df)
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


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the CTGov molecular-signature eligibility pipeline: copy the "
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
        repo_root=args.repo_root,
        eligibility_data_dir=args.eligibility_data_dir,
        curated_dir=args.curated_dir,
        molecular_signature_resource_dir=args.molecular_signature_resource_dir,
        mapping_resource_file=args.mapping_resource_file,
        output_dir=args.output_dir,
        output_format=args.output_format,
        fail_on_error=args.fail_on_error,
    )

    outputs = run_molecular_signature_pipeline(inputs)

    logger.info("Molecular-signature pipeline complete.")
    logger.info("mapping_resource_file:                  %s", outputs.mapping_resource_file)
    logger.info("mapped_criteria_file:                   %s", outputs.mapped_criteria_file)
    logger.info(
        "trial_level_molecular_signature_file:   %s",
        outputs.trial_level_molecular_signature_file,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
