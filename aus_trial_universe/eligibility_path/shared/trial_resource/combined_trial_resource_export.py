from __future__ import annotations

import argparse
import ast
import logging
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional, Sequence

import pandas as pd

from aus_trial_universe.eligibility_path.shared.utils.pipeline_io import (
    read_tabular_file,
    resolve_path,
    write_tabular_file,
)

LOGGER = logging.getLogger(__name__)

DEFAULT_ELIGIBILITY_DATA_DIR = Path("data/eligibility_path")
DEFAULT_EXPORT_SUFFIX_FORMAT = "%d%m%Y"
DEFAULT_REGISTRY_TRIAL_EXPORT_STEM = "trial_resource"
DEFAULT_REGISTRY_COHORT_EXPORT_STEM = "cohort_resource"
DEFAULT_COMBINED_TRIAL_EXPORT_STEM = "eligibility_trial_resource"
DEFAULT_COMBINED_COHORT_EXPORT_STEM = "eligibility_cohort_resource"
DEFAULT_EXPORT_DIR = Path("exports/final")

REGISTRY_COLUMN = "registry"
TRIAL_ID_COLUMN = "trialId"
COHORT_COLUMN = "cohort"
DISPLAY_DELIMITER = " | "

ANZCTR_LLM_REVIEW_COLUMNS: Sequence[str] = (
    "llm_drug_to_remove",
    "llm_drugs_to_add",
    "llm_drugs_to_correct",
    "llm_reasoning",
)

ELIGIBILITY_VALUE_COLUMNS: Sequence[str] = (
    "cancer_type_inclusive",
    "cancer_type_exclusive",
    "gene_alteration_inclusive",
    "gene_alteration_exclusive",
    "molecular_signature_inclusive",
    "molecular_signature_exclusive",
)

FINAL_TRIAL_COLUMNS: Sequence[str] = (
    REGISTRY_COLUMN,
    TRIAL_ID_COLUMN,
    "title",
    "scientific_title",
    "recruitment_status",
    "phase",
    "primary_sponsor_name",
    "health_condition",
    "min_age",
    "max_age",
    "recruitment_country",
    "recruitment_state",
    "intervention_type_or_code",
    "intervention_name",
    *ANZCTR_LLM_REVIEW_COLUMNS,
    *ELIGIBILITY_VALUE_COLUMNS,
)

FINAL_COHORT_COLUMNS: Sequence[str] = (
    REGISTRY_COLUMN,
    TRIAL_ID_COLUMN,
    COHORT_COLUMN,
    *[column for column in FINAL_TRIAL_COLUMNS if column not in {REGISTRY_COLUMN, TRIAL_ID_COLUMN}],
)

# Explicit cross-registry schema for the top-level eligibility resource exports.
# Registry-specific trial/cohort resources remain as support/debug outputs.  The
# top-level files deliberately do not keep every source column; they map CTGov
# and ANZCTR fields onto the common columns above.
SCHEMA_DOCUMENTATION: Sequence[tuple[str, str, str]] = (
    ("registry", 'fixed "ctgov"', 'fixed "anzctr"'),
    ("trialId", "nctId", "trialId"),
    ("title", "briefTitle", "STUDY TITLE"),
    ("scientific_title", "officialTitle", "SCIENTIFIC TITLE"),
    ("recruitment_status", "status", "RECRUITMENT STATUS"),
    ("phase", "phases", "PHASE"),
    ("primary_sponsor_name", "leadSponsor", "PRIMARY SPONSOR NAME"),
    ("health_condition", "conditions", "HEALTH CONDITION"),
    ("min_age", "minAge", "MIN AGE + MIN AGE TYPE"),
    ("max_age", "maxAge", "MAX AGE + MAX AGE TYPE"),
    ("recruitment_country", "derived from address", "RECRUITMENT COUNTRY"),
    ("recruitment_state", "derived from address", "RECRUITMENT STATE"),
    ("intervention_type_or_code", "interventionType", "anzctr_intervention_codes"),
    ("intervention_name", "interventionName", "DRUG_rxnorm_matched"),
    ("llm_drug_to_remove", "", "llm_drug_to_remove"),
    ("llm_drugs_to_add", "", "llm_drugs_to_add"),
    ("llm_drugs_to_correct", "", "llm_drugs_to_correct"),
    ("llm_reasoning", "", "llm_reasoning"),
    ("cancer_type_inclusive", "cancer_type_inclusive", "cancer_type_inclusive"),
    ("cancer_type_exclusive", "cancer_type_exclusive", "cancer_type_exclusive"),
    ("gene_alteration_inclusive", "gene_alteration_inclusive", "gene_alteration_inclusive"),
    ("gene_alteration_exclusive", "gene_alteration_exclusive", "gene_alteration_exclusive"),
    ("molecular_signature_inclusive", "molecular_signature_inclusive", "molecular_signature_inclusive"),
    ("molecular_signature_exclusive", "molecular_signature_exclusive", "molecular_signature_exclusive"),
)

STATE_TO_ABBREVIATION: dict[str, str] = {
    "new south wales": "NSW",
    "nsw": "NSW",
    "victoria": "VIC",
    "vic": "VIC",
    "queensland": "QLD",
    "qld": "QLD",
    "south australia": "SA",
    "sa": "SA",
    "western australia": "WA",
    "wa": "WA",
    "tasmania": "TAS",
    "tas": "TAS",
    "northern territory": "NT",
    "nt": "NT",
    "australian capital territory": "ACT",
    "act": "ACT",
}


@dataclass(frozen=True)
class CombinedEligibilityResourceInputs:
    ctgov_trial_resource_file: Path
    anzctr_trial_resource_file: Path
    ctgov_cohort_resource_file: Path
    anzctr_cohort_resource_file: Path
    trial_output_file: Path
    cohort_output_file: Path


@dataclass(frozen=True)
class CombinedEligibilityResourceOutputs:
    trial_output_file: Path
    cohort_output_file: Path

    @property
    def output_file(self) -> Path:
        return self.trial_output_file


CombinedTrialResourceInputs = CombinedEligibilityResourceInputs
CombinedTrialResourceOutputs = CombinedEligibilityResourceOutputs


def _validate_export_date(export_date: Optional[str]) -> str:
    if export_date is None:
        export_date = date.today().strftime(DEFAULT_EXPORT_SUFFIX_FORMAT)

    export_date = str(export_date).strip()
    if not re.fullmatch(r"\d{8}", export_date):
        raise ValueError(
            f"export_date must be in ddmmyyyy format, got {export_date!r}"
        )
    return export_date


def default_registry_resource_file(
    eligibility_data_dir: Path,
    registry: str,
    *,
    export_date: Optional[str],
    cohort_level: bool,
) -> Path:
    suffix = _validate_export_date(export_date)
    stem = (
        DEFAULT_REGISTRY_COHORT_EXPORT_STEM
        if cohort_level
        else DEFAULT_REGISTRY_TRIAL_EXPORT_STEM
    )
    return eligibility_data_dir / DEFAULT_EXPORT_DIR / registry / f"{stem}_{suffix}.tsv"


def default_combined_resource_file(
    eligibility_data_dir: Path,
    *,
    export_date: Optional[str],
    cohort_level: bool,
) -> Path:
    suffix = _validate_export_date(export_date)
    stem = (
        DEFAULT_COMBINED_COHORT_EXPORT_STEM
        if cohort_level
        else DEFAULT_COMBINED_TRIAL_EXPORT_STEM
    )
    return eligibility_data_dir / DEFAULT_EXPORT_DIR / f"{stem}_{suffix}.tsv"


def clean_text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return ""
    return re.sub(r"\s+", " ", text)


def parse_list_like_cell(value: object) -> list[str]:
    text = clean_text(value)
    if not text:
        return []
    if text.startswith("[") and text.endswith("]"):
        try:
            parsed = ast.literal_eval(text)
        except (SyntaxError, ValueError):
            parsed = None
        if isinstance(parsed, list):
            return [clean_text(item) for item in parsed if clean_text(item)]
    return [text]


def ordered_unique(values: Sequence[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = clean_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def join_values(values: Sequence[str]) -> str:
    return DISPLAY_DELIMITER.join(ordered_unique(list(values)))


def source_column(frame: pd.DataFrame, column: str) -> pd.Series:
    if column in frame.columns:
        return frame[column].fillna("").map(clean_text)
    return pd.Series([""] * len(frame), index=frame.index, dtype=object)


def combine_anzctr_age(value: object, unit: object) -> str:
    value_text = clean_text(value)
    unit_text = clean_text(unit)
    if not value_text or unit_text.casefold() == "not stated":
        return ""
    if re.fullmatch(r"\d+\.0", value_text):
        value_text = value_text[:-2]
    return clean_text(f"{value_text} {unit_text}" if unit_text else value_text)


def derive_ctgov_location_fields(address_value: object) -> tuple[str, str]:
    addresses = parse_list_like_cell(address_value)
    countries: list[str] = []
    states: list[str] = []

    for address in addresses:
        parts = [part.strip() for part in address.split(",") if part.strip()]
        if parts:
            countries.append(parts[-1])
        if len(parts) >= 3:
            state = parts[-3]
            states.append(STATE_TO_ABBREVIATION.get(state.casefold(), state))

    return join_values(countries), join_values(states)


def normalize_ctgov_resource(frame: pd.DataFrame, *, cohort_level: bool = False) -> pd.DataFrame:
    out = pd.DataFrame(index=frame.index)
    out[REGISTRY_COLUMN] = "ctgov"
    out[TRIAL_ID_COLUMN] = source_column(frame, "nctId")
    if cohort_level:
        out[COHORT_COLUMN] = source_column(frame, COHORT_COLUMN)

    country_state = frame.apply(
        lambda row: derive_ctgov_location_fields(row.get("address", "")),
        axis=1,
        result_type="expand",
    )

    out["title"] = source_column(frame, "briefTitle")
    out["scientific_title"] = source_column(frame, "officialTitle")
    out["recruitment_status"] = source_column(frame, "status")
    out["phase"] = source_column(frame, "phases")
    out["primary_sponsor_name"] = source_column(frame, "leadSponsor")
    out["health_condition"] = source_column(frame, "conditions")
    out["min_age"] = source_column(frame, "minAge")
    out["max_age"] = source_column(frame, "maxAge")
    out["recruitment_country"] = country_state[0].fillna("").map(clean_text)
    out["recruitment_state"] = country_state[1].fillna("").map(clean_text)
    out["intervention_type_or_code"] = source_column(frame, "interventionType")
    out["intervention_name"] = source_column(frame, "interventionName")

    for column in ANZCTR_LLM_REVIEW_COLUMNS:
        out[column] = ""
    for column in ELIGIBILITY_VALUE_COLUMNS:
        out[column] = source_column(frame, column)

    return out.loc[:, FINAL_COHORT_COLUMNS if cohort_level else FINAL_TRIAL_COLUMNS]


def normalize_anzctr_resource(frame: pd.DataFrame, *, cohort_level: bool = False) -> pd.DataFrame:
    out = pd.DataFrame(index=frame.index)
    out[REGISTRY_COLUMN] = "anzctr"
    out[TRIAL_ID_COLUMN] = source_column(frame, "trialId")
    if cohort_level:
        out[COHORT_COLUMN] = source_column(frame, COHORT_COLUMN)

    out["title"] = source_column(frame, "STUDY TITLE")
    out["scientific_title"] = source_column(frame, "SCIENTIFIC TITLE")
    out["recruitment_status"] = source_column(frame, "RECRUITMENT STATUS")
    out["phase"] = source_column(frame, "PHASE")
    out["primary_sponsor_name"] = source_column(frame, "PRIMARY SPONSOR NAME")
    out["health_condition"] = source_column(frame, "HEALTH CONDITION")
    out["min_age"] = [
        combine_anzctr_age(value, unit)
        for value, unit in zip(
            source_column(frame, "MIN AGE"),
            source_column(frame, "MIN AGE TYPE"),
        )
    ]
    out["max_age"] = [
        combine_anzctr_age(value, unit)
        for value, unit in zip(
            source_column(frame, "MAX AGE"),
            source_column(frame, "MAX AGE TYPE"),
        )
    ]
    out["recruitment_country"] = source_column(frame, "RECRUITMENT COUNTRY")
    out["recruitment_state"] = source_column(frame, "RECRUITMENT STATE")
    out["intervention_type_or_code"] = source_column(frame, "anzctr_intervention_codes")
    out["intervention_name"] = source_column(frame, "DRUG_rxnorm_matched")

    for column in ANZCTR_LLM_REVIEW_COLUMNS:
        out[column] = source_column(frame, column)
    for column in ELIGIBILITY_VALUE_COLUMNS:
        out[column] = source_column(frame, column)

    return out.loc[:, FINAL_COHORT_COLUMNS if cohort_level else FINAL_TRIAL_COLUMNS]


def combine_trial_resource_tables(
    registry_frames: Sequence[tuple[str, str, pd.DataFrame]],
) -> pd.DataFrame:
    normalized_frames: list[pd.DataFrame] = []
    for registry, _source_id_column, frame in registry_frames:
        if registry == "ctgov":
            normalized_frames.append(normalize_ctgov_resource(frame, cohort_level=False))
        elif registry == "anzctr":
            normalized_frames.append(normalize_anzctr_resource(frame, cohort_level=False))
        else:
            raise ValueError(f"Unsupported registry: {registry}")

    return pd.concat(normalized_frames, ignore_index=True).loc[:, FINAL_TRIAL_COLUMNS]


def combine_cohort_resource_tables(
    registry_frames: Sequence[tuple[str, str, pd.DataFrame]],
) -> pd.DataFrame:
    normalized_frames: list[pd.DataFrame] = []
    for registry, _source_id_column, frame in registry_frames:
        if registry == "ctgov":
            normalized_frames.append(normalize_ctgov_resource(frame, cohort_level=True))
        elif registry == "anzctr":
            normalized_frames.append(normalize_anzctr_resource(frame, cohort_level=True))
        else:
            raise ValueError(f"Unsupported registry: {registry}")

    return pd.concat(normalized_frames, ignore_index=True).loc[:, FINAL_COHORT_COLUMNS]


def discover_pipeline_inputs(
    *,
    repo_root: Path,
    eligibility_data_dir: Path = DEFAULT_ELIGIBILITY_DATA_DIR,
    ctgov_trial_resource_file: Optional[Path],
    anzctr_trial_resource_file: Optional[Path],
    ctgov_cohort_resource_file: Optional[Path] = None,
    anzctr_cohort_resource_file: Optional[Path] = None,
    output_file: Optional[Path] = None,
    trial_output_file: Optional[Path] = None,
    cohort_output_file: Optional[Path] = None,
    export_date: Optional[str],
) -> CombinedEligibilityResourceInputs:
    repo_root = repo_root.resolve()
    eligibility_data_dir = resolve_path(eligibility_data_dir, repo_root)

    resolved_ctgov_trial_file = (
        resolve_path(ctgov_trial_resource_file, repo_root)
        if ctgov_trial_resource_file is not None
        else default_registry_resource_file(
            eligibility_data_dir,
            "ctgov",
            export_date=export_date,
            cohort_level=False,
        )
    )
    resolved_anzctr_trial_file = (
        resolve_path(anzctr_trial_resource_file, repo_root)
        if anzctr_trial_resource_file is not None
        else default_registry_resource_file(
            eligibility_data_dir,
            "anzctr",
            export_date=export_date,
            cohort_level=False,
        )
    )
    resolved_ctgov_cohort_file = (
        resolve_path(ctgov_cohort_resource_file, repo_root)
        if ctgov_cohort_resource_file is not None
        else default_registry_resource_file(
            eligibility_data_dir,
            "ctgov",
            export_date=export_date,
            cohort_level=True,
        )
    )
    resolved_anzctr_cohort_file = (
        resolve_path(anzctr_cohort_resource_file, repo_root)
        if anzctr_cohort_resource_file is not None
        else default_registry_resource_file(
            eligibility_data_dir,
            "anzctr",
            export_date=export_date,
            cohort_level=True,
        )
    )

    if trial_output_file is None and output_file is not None:
        trial_output_file = output_file
    resolved_trial_output_file = (
        resolve_path(trial_output_file, repo_root)
        if trial_output_file is not None
        else default_combined_resource_file(
            eligibility_data_dir,
            export_date=export_date,
            cohort_level=False,
        )
    )
    resolved_cohort_output_file = (
        resolve_path(cohort_output_file, repo_root)
        if cohort_output_file is not None
        else default_combined_resource_file(
            eligibility_data_dir,
            export_date=export_date,
            cohort_level=True,
        )
    )

    inputs = CombinedEligibilityResourceInputs(
        ctgov_trial_resource_file=resolved_ctgov_trial_file,
        anzctr_trial_resource_file=resolved_anzctr_trial_file,
        ctgov_cohort_resource_file=resolved_ctgov_cohort_file,
        anzctr_cohort_resource_file=resolved_anzctr_cohort_file,
        trial_output_file=resolved_trial_output_file,
        cohort_output_file=resolved_cohort_output_file,
    )
    validate_pipeline_inputs(inputs)
    return inputs


def validate_pipeline_inputs(inputs: CombinedEligibilityResourceInputs) -> None:
    for label, path in (
        ("CTGov trial resource", inputs.ctgov_trial_resource_file),
        ("ANZCTR trial resource", inputs.anzctr_trial_resource_file),
        ("CTGov cohort resource", inputs.ctgov_cohort_resource_file),
        ("ANZCTR cohort resource", inputs.anzctr_cohort_resource_file),
    ):
        if not path.exists():
            raise FileNotFoundError(f"{label} does not exist: {path}")
        if not path.is_file():
            raise ValueError(f"{label} is not a file: {path}")


def run_combined_trial_resource_export(
    inputs: CombinedEligibilityResourceInputs,
) -> CombinedEligibilityResourceOutputs:
    trial_combined = combine_trial_resource_tables(
        [
            ("ctgov", "nctId", read_tabular_file(inputs.ctgov_trial_resource_file)),
            ("anzctr", "trialId", read_tabular_file(inputs.anzctr_trial_resource_file)),
        ]
    )
    cohort_combined = combine_cohort_resource_tables(
        [
            ("ctgov", "nctId", read_tabular_file(inputs.ctgov_cohort_resource_file)),
            ("anzctr", "trialId", read_tabular_file(inputs.anzctr_cohort_resource_file)),
        ]
    )

    write_tabular_file(trial_combined, inputs.trial_output_file)
    write_tabular_file(cohort_combined, inputs.cohort_output_file)
    LOGGER.info(
        "Wrote combined eligibility trial resource: %s rows=%d columns=%d",
        inputs.trial_output_file,
        len(trial_combined),
        len(trial_combined.columns),
    )
    LOGGER.info(
        "Wrote combined eligibility cohort resource: %s rows=%d columns=%d",
        inputs.cohort_output_file,
        len(cohort_combined),
        len(cohort_combined.columns),
    )
    return CombinedEligibilityResourceOutputs(
        trial_output_file=inputs.trial_output_file,
        cohort_output_file=inputs.cohort_output_file,
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build combined CTGov/ANZCTR eligibility trial and cohort resources."
    )
    parser.add_argument("--repo_root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--eligibility_data_dir",
        type=Path,
        default=DEFAULT_ELIGIBILITY_DATA_DIR,
        help="Eligibility path data root. Defaults to data/eligibility_path.",
    )
    parser.add_argument(
        "--ctgov_trial_resource_file",
        type=Path,
        default=None,
        help=(
            "CTGov trial resource TSV. Defaults to "
            "data/eligibility_path/exports/final/ctgov/trial_resource_<ddmmyyyy>.tsv."
        ),
    )
    parser.add_argument(
        "--anzctr_trial_resource_file",
        type=Path,
        default=None,
        help=(
            "ANZCTR trial resource TSV. Defaults to "
            "data/eligibility_path/exports/final/anzctr/trial_resource_<ddmmyyyy>.tsv."
        ),
    )
    parser.add_argument(
        "--ctgov_cohort_resource_file",
        type=Path,
        default=None,
        help=(
            "CTGov cohort resource TSV. Defaults to "
            "data/eligibility_path/exports/final/ctgov/cohort_resource_<ddmmyyyy>.tsv."
        ),
    )
    parser.add_argument(
        "--anzctr_cohort_resource_file",
        type=Path,
        default=None,
        help=(
            "ANZCTR cohort resource TSV. Defaults to "
            "data/eligibility_path/exports/final/anzctr/cohort_resource_<ddmmyyyy>.tsv."
        ),
    )
    parser.add_argument(
        "--output_file",
        type=Path,
        default=None,
        help=(
            "Backward-compatible alias for --trial_output_file. Defaults to "
            "data/eligibility_path/exports/final/eligibility_trial_resource_<ddmmyyyy>.tsv."
        ),
    )
    parser.add_argument(
        "--trial_output_file",
        type=Path,
        default=None,
        help=(
            "Combined trial output TSV. Defaults to "
            "data/eligibility_path/exports/final/eligibility_trial_resource_<ddmmyyyy>.tsv."
        ),
    )
    parser.add_argument(
        "--cohort_output_file",
        type=Path,
        default=None,
        help=(
            "Combined cohort output TSV. Defaults to "
            "data/eligibility_path/exports/final/eligibility_cohort_resource_<ddmmyyyy>.tsv."
        ),
    )
    parser.add_argument(
        "--export_date",
        default=None,
        help="Date suffix for default filenames, in ddmmyyyy format.",
    )
    parser.add_argument("--log_level", default="INFO")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    inputs = discover_pipeline_inputs(
        repo_root=args.repo_root,
        eligibility_data_dir=args.eligibility_data_dir,
        ctgov_trial_resource_file=args.ctgov_trial_resource_file,
        anzctr_trial_resource_file=args.anzctr_trial_resource_file,
        ctgov_cohort_resource_file=args.ctgov_cohort_resource_file,
        anzctr_cohort_resource_file=args.anzctr_cohort_resource_file,
        output_file=args.output_file,
        trial_output_file=args.trial_output_file,
        cohort_output_file=args.cohort_output_file,
        export_date=args.export_date,
    )
    outputs = run_combined_trial_resource_export(inputs)
    LOGGER.info(
        "Combined eligibility resource export complete: trial=%s cohort=%s",
        outputs.trial_output_file,
        outputs.cohort_output_file,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
