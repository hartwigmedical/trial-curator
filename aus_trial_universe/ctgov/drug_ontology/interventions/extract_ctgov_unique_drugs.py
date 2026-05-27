from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import pandas as pd

from aus_trial_universe.ctgov.drug_ontology.common.classification_schema import (
    COL_INTERVENTION_ALL_ALIASES_NORMALISED,
    COL_INTERVENTION_INDEX,
    COL_INTERVENTION_TYPE,
    COL_NCT_ID,
    CORE_INTERVENTION_COLUMNS,
    DISPLAY_DELIMITER,
    TARGET_DRUG_INTERVENTION_TYPES,
)
from aus_trial_universe.ctgov.drug_ontology.interventions.extract import (
    build_interventions_dataframe,
    clean_text,
    normalise_for_dedupe,
)

logger = logging.getLogger(__name__)

DEFAULT_INPUT_JSON = Path("data/ctgov/drug_ontology/raw/ctgov_input.json")
DEFAULT_INTERVENTIONS_OUTPUT_FILENAME = "ctgov_interventions.tsv"
DEFAULT_UNIQUE_OUTPUT_FILENAME = "ctgov_unique_drugs.tsv"
DEFAULT_LINK_OUTPUT_FILENAME = "ctgov_intervention_drug_terms.tsv"
UNIQUE_DRUG_COLUMN = "input_drug_name"
INTERVENTION_KEY_COLUMN = "ctgov_intervention_key"


@dataclass(frozen=True)
class CtgovDrugExtractionStats:
    input_intervention_rows: int
    filtered_intervention_rows: int
    filtered_interventions_with_normalised_terms: int
    filtered_interventions_without_normalised_terms: int
    expanded_intervention_drug_links: int
    unique_input_drug_names: int
    duplicate_intervention_drug_links_removed: int


def default_output_dir_for_input(input_json: Path) -> Path:
    """Return the default processed output directory for a CTGov input JSON path."""
    if input_json.parent.name == "raw":
        return input_json.parent.parent / "processed"
    return input_json.parent / "processed"


def resolve_output_file(
    explicit_path: Path | None,
    output_dir: Path,
    default_filename: str,
) -> Path:
    """Resolve an output file path, allowing an existing directory as a target."""
    if explicit_path is None:
        return output_dir / default_filename
    if explicit_path.exists() and explicit_path.is_dir():
        return explicit_path / default_filename
    return explicit_path


def normalise_intervention_type(value: object) -> str:
    return clean_text(value).strip().upper()


def target_intervention_types_upper() -> set[str]:
    return {normalise_intervention_type(value) for value in TARGET_DRUG_INTERVENTION_TYPES}


def filter_target_drug_interventions(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only DRUG / BIOLOGICAL / COMBINATION_PRODUCT interventions.

    The target type set comes from the existing classification schema so that this
    script stays aligned with the rest of the drug-ontology pipeline.
    """
    required_columns = [COL_INTERVENTION_TYPE]
    missing = [column for column in required_columns if column not in df.columns]
    if missing:
        raise ValueError(
            f"Interventions dataframe is missing required columns: {missing}. "
            f"Found columns: {list(df.columns)}"
        )

    target_types = target_intervention_types_upper()
    mask = df[COL_INTERVENTION_TYPE].map(normalise_intervention_type).isin(target_types)
    filtered = df.loc[mask].copy()

    if COL_INTERVENTION_INDEX in filtered.columns:
        filtered[COL_INTERVENTION_INDEX] = pd.to_numeric(
            filtered[COL_INTERVENTION_INDEX],
            errors="raise",
        ).astype(int)

    # Preserve the canonical column order from the existing extractor when possible.
    ordered_columns = [column for column in CORE_INTERVENTION_COLUMNS if column in filtered.columns]
    remaining_columns = [column for column in filtered.columns if column not in ordered_columns]
    return filtered[ordered_columns + remaining_columns]


def split_display_delimited_terms(value: object) -> list[str]:
    """Split the existing normalised alias field into individual drug terms."""
    text = clean_text(value)
    if not text:
        return []
    return [clean_text(term) for term in text.split(DISPLAY_DELIMITER) if clean_text(term)]


def stable_ctgov_intervention_key(nct_id: object, intervention_index: object) -> str:
    return f"{clean_text(nct_id)}::{int(intervention_index)}"


def iter_intervention_drug_links(filtered_interventions: pd.DataFrame) -> Iterable[dict[str, object]]:
    """Yield one row per filtered CTGov intervention × normalised drug term."""
    required_columns = [
        COL_NCT_ID,
        COL_INTERVENTION_INDEX,
        COL_INTERVENTION_ALL_ALIASES_NORMALISED,
    ]
    missing = [column for column in required_columns if column not in filtered_interventions.columns]
    if missing:
        raise ValueError(
            f"Filtered interventions dataframe is missing required columns: {missing}. "
            f"Found columns: {list(filtered_interventions.columns)}"
        )

    for row in filtered_interventions.to_dict(orient="records"):
        nct_id = row[COL_NCT_ID]
        intervention_index = row[COL_INTERVENTION_INDEX]
        intervention_key = stable_ctgov_intervention_key(nct_id, intervention_index)

        seen_in_intervention: set[str] = set()
        for term in split_display_delimited_terms(row[COL_INTERVENTION_ALL_ALIASES_NORMALISED]):
            key = normalise_for_dedupe(term)
            if not key or key in seen_in_intervention:
                continue
            seen_in_intervention.add(key)
            yield {
                INTERVENTION_KEY_COLUMN: intervention_key,
                COL_NCT_ID: clean_text(nct_id),
                COL_INTERVENTION_INDEX: int(intervention_index),
                UNIQUE_DRUG_COLUMN: term,
            }


def build_intervention_drug_link_dataframe(filtered_interventions: pd.DataFrame) -> pd.DataFrame:
    columns = [
        INTERVENTION_KEY_COLUMN,
        COL_NCT_ID,
        COL_INTERVENTION_INDEX,
        UNIQUE_DRUG_COLUMN,
    ]
    return pd.DataFrame(iter_intervention_drug_links(filtered_interventions), columns=columns)


def build_unique_drug_dataframe(
    intervention_drug_links: pd.DataFrame,
    *,
    sort_unique_drugs: bool = False,
) -> pd.DataFrame:
    """Build one row per unique CTGov input drug name, preserving first-seen spelling."""
    if UNIQUE_DRUG_COLUMN not in intervention_drug_links.columns:
        raise ValueError(
            f"Drug-link dataframe is missing required column {UNIQUE_DRUG_COLUMN!r}. "
            f"Found columns: {list(intervention_drug_links.columns)}"
        )

    unique_terms: list[str] = []
    seen: set[str] = set()
    for term in intervention_drug_links[UNIQUE_DRUG_COLUMN].tolist():
        term = clean_text(term)
        key = normalise_for_dedupe(term)
        if not key or key in seen:
            continue
        seen.add(key)
        unique_terms.append(term)

    if sort_unique_drugs:
        unique_terms = sorted(unique_terms, key=lambda value: normalise_for_dedupe(value))

    return pd.DataFrame({UNIQUE_DRUG_COLUMN: unique_terms})


def write_tsv(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, sep="\t", index=False, na_rep="")


def build_ctgov_drug_outputs(
    *,
    input_json: Path,
    interventions_output_tsv: Path,
    unique_output_tsv: Path,
    link_output_tsv: Path | None = None,
    sort_unique_drugs: bool = False,
) -> CtgovDrugExtractionStats:
    """Build CTGov filtered intervention and unique-drug TSV outputs.

    This deliberately reuses the existing CTGov intervention extractor. That
    extractor performs the alias cleaning, connector splitting, placebo/generic
    term removal, and dose/form/route stripping. This module only filters to the
    drug-relevant intervention types and changes the grain to unique input terms.
    """
    if not input_json.exists():
        raise FileNotFoundError(f"Input JSON does not exist: {input_json}")
    if not input_json.is_file():
        raise IsADirectoryError(f"Input path is not a file: {input_json}")

    all_interventions = build_interventions_dataframe(input_json)
    filtered_interventions = filter_target_drug_interventions(all_interventions)
    intervention_drug_links = build_intervention_drug_link_dataframe(filtered_interventions)
    unique_drugs = build_unique_drug_dataframe(
        intervention_drug_links,
        sort_unique_drugs=sort_unique_drugs,
    )

    write_tsv(interventions_output_tsv, filtered_interventions)
    write_tsv(unique_output_tsv, unique_drugs)
    if link_output_tsv is not None:
        write_tsv(link_output_tsv, intervention_drug_links)

    normalised_terms_series = filtered_interventions[COL_INTERVENTION_ALL_ALIASES_NORMALISED].map(clean_text)
    filtered_with_terms = int((normalised_terms_series != "").sum())
    raw_links_count = sum(len(split_display_delimited_terms(value)) for value in normalised_terms_series)

    return CtgovDrugExtractionStats(
        input_intervention_rows=len(all_interventions),
        filtered_intervention_rows=len(filtered_interventions),
        filtered_interventions_with_normalised_terms=filtered_with_terms,
        filtered_interventions_without_normalised_terms=len(filtered_interventions) - filtered_with_terms,
        expanded_intervention_drug_links=len(intervention_drug_links),
        unique_input_drug_names=len(unique_drugs),
        duplicate_intervention_drug_links_removed=raw_links_count - len(intervention_drug_links),
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Extract CTGov drug-relevant interventions and a unique CTGov input-drug list "
            "from ctgov_input.json using the existing intervention cleaning module."
        )
    )
    parser.add_argument(
        "--input-json",
        "--input_json",
        dest="input_json",
        type=Path,
        default=DEFAULT_INPUT_JSON,
        help=(
            "Path to CTGov JSON/NDJSON input. "
            f"Default: {DEFAULT_INPUT_JSON}"
        ),
    )
    parser.add_argument(
        "--output-dir",
        "--output_dir",
        dest="output_dir",
        type=Path,
        default=None,
        help=(
            "Output directory. Defaults to ../processed when the input is under a raw/ directory; "
            "otherwise defaults to <input-json-dir>/processed."
        ),
    )
    parser.add_argument(
        "--interventions-output-file",
        "--interventions_output_file",
        dest="interventions_output_file",
        type=Path,
        default=None,
        help=(
            f"Filtered intervention TSV path. Defaults to <output-dir>/{DEFAULT_INTERVENTIONS_OUTPUT_FILENAME}. "
            "If an existing directory is provided, writes the default filename inside it."
        ),
    )
    parser.add_argument(
        "--unique-output-file",
        "--unique_output_file",
        dest="unique_output_file",
        type=Path,
        default=None,
        help=(
            f"Unique CTGov drug TSV path. Defaults to <output-dir>/{DEFAULT_UNIQUE_OUTPUT_FILENAME}. "
            "If an existing directory is provided, writes the default filename inside it."
        ),
    )
    parser.add_argument(
        "--link-output-file",
        "--link_output_file",
        dest="link_output_file",
        type=Path,
        default=None,
        help=(
            f"Optional intervention × drug-term link TSV path. Defaults to <output-dir>/{DEFAULT_LINK_OUTPUT_FILENAME}. "
            "If an existing directory is provided, writes the default filename inside it."
        ),
    )
    parser.add_argument(
        "--no-link-output",
        "--no_link_output",
        dest="no_link_output",
        action="store_true",
        help="Do not write the intervention × drug-term link TSV.",
    )
    parser.add_argument(
        "--sort",
        action="store_true",
        help="Sort the final unique drug list case-insensitively instead of preserving first-seen order.",
    )
    parser.add_argument(
        "--log-level",
        "--log_level",
        dest="log_level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    try:
        input_json = args.input_json
        output_dir = args.output_dir if args.output_dir is not None else default_output_dir_for_input(input_json)

        interventions_output_file = resolve_output_file(
            args.interventions_output_file,
            output_dir,
            DEFAULT_INTERVENTIONS_OUTPUT_FILENAME,
        )
        unique_output_file = resolve_output_file(
            args.unique_output_file,
            output_dir,
            DEFAULT_UNIQUE_OUTPUT_FILENAME,
        )
        link_output_file = None
        if not args.no_link_output:
            link_output_file = resolve_output_file(
                args.link_output_file,
                output_dir,
                DEFAULT_LINK_OUTPUT_FILENAME,
            )

        stats = build_ctgov_drug_outputs(
            input_json=input_json,
            interventions_output_tsv=interventions_output_file,
            unique_output_tsv=unique_output_file,
            link_output_tsv=link_output_file,
            sort_unique_drugs=args.sort,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Input JSON: {input_json}", file=sys.stderr)
    print(f"Output directory: {output_dir}", file=sys.stderr)
    print(f"Wrote filtered interventions: {interventions_output_file}", file=sys.stderr)
    print(f"Wrote unique CTGov drugs: {unique_output_file}", file=sys.stderr)
    if link_output_file is not None:
        print(f"Wrote intervention-drug links: {link_output_file}", file=sys.stderr)
    print(f"Target intervention types: {', '.join(sorted(target_intervention_types_upper()))}", file=sys.stderr)
    print(f"Input intervention rows: {stats.input_intervention_rows}", file=sys.stderr)
    print(f"Filtered intervention rows: {stats.filtered_intervention_rows}", file=sys.stderr)
    print(
        f"Filtered interventions with normalised terms: {stats.filtered_interventions_with_normalised_terms}",
        file=sys.stderr,
    )
    print(
        f"Filtered interventions without normalised terms: {stats.filtered_interventions_without_normalised_terms}",
        file=sys.stderr,
    )
    print(f"Expanded intervention-drug links: {stats.expanded_intervention_drug_links}", file=sys.stderr)
    print(f"Unique input drug names: {stats.unique_input_drug_names}", file=sys.stderr)
    print(
        f"Duplicate intervention-drug links removed: {stats.duplicate_intervention_drug_links_removed}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
