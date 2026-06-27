from __future__ import annotations

import argparse
import ast
import logging
import re
from dataclasses import dataclass
from datetime import date
from io import StringIO
from pathlib import Path
from typing import Optional, Sequence
from urllib.request import urlopen

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
DEFAULT_POTTR_TRIAL_ELIGIBILITY_URL = (
    "https://github.com/fpylin/POTTR/blob/master/data/trial_eligibility.AU.tsv"
)
DEFAULT_POTTR_TRIAL_REGISTRY_URL = (
    "https://github.com/fpylin/POTTR/blob/master/data/trial_registry.AU.tsv"
)

REGISTRY_COLUMN = "registry"
TRIAL_ID_COLUMN = "trialId"
COHORT_COLUMN = "cohort"
DISPLAY_DELIMITER = " | "

ELIGIBILITY_VALUE_COLUMNS: Sequence[str] = (
    "cancer_type_inclusive",
    "cancer_type_exclusive",
    "gene_alteration_inclusive",
    "gene_alteration_exclusive",
    "molecular_signature_inclusive",
    "molecular_signature_exclusive",
)

FINAL_TRIAL_COLUMNS: Sequence[str] = (
    TRIAL_ID_COLUMN,
    REGISTRY_COLUMN,
    "title",
    "scientific_title",
    "primary_sponsor",
    "recruitment_status",
    "phase",
    "country",
    "original_health_conditions",
    "intervention_type",
    "intervention_name",
    *ELIGIBILITY_VALUE_COLUMNS,
)

FINAL_COHORT_COLUMNS: Sequence[str] = (
    TRIAL_ID_COLUMN,
    REGISTRY_COLUMN,
    COHORT_COLUMN,
    *[
        column
        for column in FINAL_TRIAL_COLUMNS
        if column not in {TRIAL_ID_COLUMN, REGISTRY_COLUMN}
    ],
)

# Explicit cross-registry schema for the top-level eligibility resource exports.
# Registry-specific trial/cohort resources remain as support/debug outputs.  The
# top-level files deliberately do not keep every source column; they map CTGov
# and ANZCTR fields onto the common columns above.
SCHEMA_DOCUMENTATION: Sequence[tuple[str, str, str]] = (
    ("trialId", "nctId", "trialId"),
    ("registry", 'fixed "ctgov"', 'fixed "anzctr"'),
    ("title", "briefTitle", "STUDY TITLE"),
    ("scientific_title", "officialTitle", "SCIENTIFIC TITLE"),
    ("primary_sponsor", "leadSponsor", "PRIMARY SPONSOR NAME"),
    ("recruitment_status", "status", "RECRUITMENT STATUS"),
    ("phase", "phases", "PHASE"),
    ("country", "derived from address", "RECRUITMENT COUNTRY"),
    ("original_health_conditions", "conditions", "HEALTH CONDITION"),
    ("intervention_type", "interventionType", "anzctr_intervention_codes"),
    ("intervention_name", "interventionName", "DRUG_rxnorm_matched"),
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
    pottr_trial_eligibility_file: str
    pottr_trial_registry_file: str


@dataclass(frozen=True)
class CombinedEligibilityResourceOutputs:
    trial_output_file: Path
    cohort_output_file: Path
    missing_pottr_trials: pd.DataFrame

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


def normalize_delimited_cell(value: object, delimiter: str = ",") -> str:
    parts = [part for part in clean_text(value).split(delimiter)]
    return delimiter.join(ordered_unique(parts))


def source_column(frame: pd.DataFrame, column: str) -> pd.Series:
    if column in frame.columns:
        return frame[column].fillna("").map(clean_text)
    return pd.Series([""] * len(frame), index=frame.index, dtype=object)


def source_list_column(frame: pd.DataFrame, column: str) -> pd.Series:
    return source_column(frame, column).map(lambda value: join_values(parse_list_like_cell(value)))


def normalize_trial_id(value: object) -> str:
    return clean_text(value).upper()


def is_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def normalize_github_tsv_url(value: str) -> str:
    text = clean_text(value)
    if text.startswith("https://github.com/") and "/blob/" in text:
        return text.replace("https://github.com/", "https://raw.githubusercontent.com/").replace(
            "/blob/",
            "/",
            1,
        )
    return text


def resolve_source_path_or_url(value: str | Path, repo_root: Path) -> str:
    text = normalize_github_tsv_url(str(value))
    if is_url(text):
        return text
    return str(resolve_path(Path(text), repo_root))


def read_pottr_tsv_source(source: str | Path) -> pd.DataFrame:
    source_text = normalize_github_tsv_url(str(source))
    if is_url(source_text):
        with urlopen(source_text, timeout=30) as response:
            text = response.read().decode("utf-8")
        return pd.read_csv(StringIO(text), sep="\t", dtype=str, keep_default_na=False)

    return pd.read_csv(Path(source_text), sep="\t", dtype=str, keep_default_na=False)


def load_pottr_trial_source(source: str | Path, *, label: str) -> pd.DataFrame:
    frame = read_pottr_tsv_source(source)
    if "trial_id" not in frame.columns:
        raise ValueError(
            f"POTTR {label} file must contain 'trial_id'. Found columns: {list(frame.columns)}"
        )
    frame = frame.copy()
    frame["trial_id"] = frame["trial_id"].map(normalize_trial_id)
    frame = frame.loc[frame["trial_id"].ne("")]
    return frame.drop_duplicates(subset=["trial_id"], keep="first")


def infer_pottr_trial_registry(trial_id: object) -> str:
    text = normalize_trial_id(trial_id)
    if text.startswith("NCT"):
        return "ctgov"
    if text.startswith("ACTRN"):
        return "anzctr"
    return ""


def build_pottr_trial_index(
    pottr_trial_eligibility: pd.DataFrame,
    pottr_trial_registry_frame: pd.DataFrame,
) -> pd.DataFrame:
    eligibility = pottr_trial_eligibility.copy()
    registry = pottr_trial_registry_frame.copy()

    eligibility = eligibility.rename(
        columns={
            column: f"eligibility_{column}"
            for column in eligibility.columns
            if column != "trial_id" and column != "eligibility_criteria"
        }
    )
    eligibility["in_pottr_trial_eligibility"] = True
    registry["in_pottr_trial_registry"] = True

    merged = eligibility.merge(registry, on="trial_id", how="outer")
    for column in ("in_pottr_trial_eligibility", "in_pottr_trial_registry"):
        merged[column] = merged[column].fillna(False).astype(bool)
    merged = merged.fillna("")
    merged["registry"] = merged["trial_id"].map(infer_pottr_trial_registry)

    leading_columns = [
        "trial_id",
        "registry",
        "in_pottr_trial_eligibility",
        "in_pottr_trial_registry",
    ]
    other_columns = [column for column in merged.columns if column not in leading_columns]
    return merged.loc[:, leading_columns + other_columns].sort_values(
        "trial_id",
        kind="stable",
    )


def build_missing_pottr_trials(
    trial_resource: pd.DataFrame,
    cohort_resource: pd.DataFrame,
    pottr_trial_eligibility: pd.DataFrame,
    pottr_trial_registry: pd.DataFrame,
) -> pd.DataFrame:
    resource_trial_ids = set()
    for frame in (trial_resource, cohort_resource):
        if TRIAL_ID_COLUMN not in frame.columns:
            raise ValueError(
                f"Final resource file must contain {TRIAL_ID_COLUMN!r}. "
                f"Found columns: {list(frame.columns)}"
            )
        resource_trial_ids.update(frame[TRIAL_ID_COLUMN].map(normalize_trial_id))

    pottr_trials = build_pottr_trial_index(
        pottr_trial_eligibility,
        pottr_trial_registry,
    )
    return pottr_trials.loc[
        ~pottr_trials["trial_id"].isin(resource_trial_ids)
    ].reset_index(drop=True)


def build_missing_pottr_trials_from_sources(
    trial_resource: pd.DataFrame,
    cohort_resource: pd.DataFrame,
    *,
    pottr_trial_eligibility_file: str | Path,
    pottr_trial_registry_file: str | Path,
) -> pd.DataFrame:
    return build_missing_pottr_trials(
        trial_resource,
        cohort_resource,
        load_pottr_trial_source(
            pottr_trial_eligibility_file,
            label="trial eligibility",
        ),
        load_pottr_trial_source(
            pottr_trial_registry_file,
            label="trial registry",
        ),
    )


def load_pottr_trial_ids(
    *,
    pottr_trial_eligibility_file: str | Path = DEFAULT_POTTR_TRIAL_ELIGIBILITY_URL,
    pottr_trial_registry_file: str | Path = DEFAULT_POTTR_TRIAL_REGISTRY_URL,
    registry: str | None = None,
) -> set[str]:
    """Return the set of normalized POTTR-listed trial IDs.

    POTTR lists the trials that must always appear in the final eligibility
    output. The extract/select steps use this set to exempt POTTR trials from
    the cohort (drug-intervention) filter so they are never dropped before
    processing. When ``registry`` is "ctgov" or "anzctr", only IDs that map to
    that registry are returned.
    """
    ids: set[str] = set()
    for source, label in (
        (pottr_trial_eligibility_file, "trial eligibility"),
        (pottr_trial_registry_file, "trial registry"),
    ):
        frame = load_pottr_trial_source(source, label=label)
        ids.update(frame["trial_id"])
    if registry is not None:
        ids = {tid for tid in ids if infer_pottr_trial_registry(tid) == registry}
    return ids


def load_pottr_trial_ids_best_effort(
    *,
    registry: str | None = None,
    pottr_trial_eligibility_file: str | Path = DEFAULT_POTTR_TRIAL_ELIGIBILITY_URL,
    pottr_trial_registry_file: str | Path = DEFAULT_POTTR_TRIAL_REGISTRY_URL,
) -> set[str]:
    """Load POTTR trial IDs, returning an empty set if the source is unreachable.

    The extract steps call this so a transient POTTR-source failure (e.g. no
    network) degrades to "no exemption this run" rather than crashing the
    pipeline. Any POTTR trial dropped as a result is still reported as missing
    by the combined export, which converges gracefully.
    """
    try:
        return load_pottr_trial_ids(
            registry=registry,
            pottr_trial_eligibility_file=pottr_trial_eligibility_file,
            pottr_trial_registry_file=pottr_trial_registry_file,
        )
    except Exception as error:  # noqa: BLE001 - best-effort, must never block extraction
        LOGGER.warning(
            "Could not load POTTR trial list for exemption (%s); proceeding "
            "without POTTR exemption this run. POTTR trials may be filtered out "
            "and reported as missing by the combined export.",
            error,
        )
        return set()


def normalize_cancer_type_cell(value: object) -> str:
    text = clean_text(value)
    if not text:
        return ""

    def replace_mapping(match: re.Match[str]) -> str:
        not_prefix = match.group("not") or ""
        close = ")" if not_prefix and match.group("close") else ""
        return f"{match.group('prefix')}{not_prefix}{match.group('code')}{close}"

    text = re.sub(
        r"(?P<prefix>^|[|&]\s*)(?P<not>NOT\()?(?P<name>[^|&]+?)\s+\((?P<code>[A-Z0-9_./-]+)\)(?P<close>\))?",
        replace_mapping,
        text,
    )
    text = re.sub(
        r"(?P<prefix>,\s*NOT\()(?P<name>[^,|&]+?)\s+\((?P<code>[A-Z0-9_./-]+)\)(?P<close>\))",
        lambda match: f"{match.group('prefix')}{match.group('code')})",
        text,
    )
    return re.sub(
        r"(?P<prefix>NOT\([A-Z0-9_./-]+\),\s*)(?P<name>[A-Za-z][^,|&]+?)\s+\((?P<code>[A-Z0-9_./-]+)\)",
        lambda match: f"{match.group('prefix')}{match.group('code')}",
        text,
    )


def derive_ctgov_location_fields(address_value: object) -> tuple[str, str]:
    addresses = parse_list_like_cell(address_value)
    countries: list[str] = []
    states: list[str] = []

    for address in addresses:
        parts = [part.strip() for part in address.split(",") if part.strip()]
        if parts:
            countries.append(parts[-1])
        if len(parts) >= 4:
            state = parts[-3]
            states.append(STATE_TO_ABBREVIATION.get(state.casefold(), state))
        elif len(parts) == 3:
            state = parts[-2]
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
    out["primary_sponsor"] = source_column(frame, "leadSponsor")
    out["recruitment_status"] = source_column(frame, "status")
    out["phase"] = source_list_column(frame, "phases")
    out["country"] = country_state[0].fillna("").map(clean_text)
    out["original_health_conditions"] = source_list_column(frame, "conditions")
    out["intervention_type"] = source_list_column(frame, "interventionType")
    out["intervention_name"] = source_list_column(frame, "interventionName")

    for column in ELIGIBILITY_VALUE_COLUMNS:
        out[column] = source_column(frame, column)
    for column in ("cancer_type_inclusive", "cancer_type_exclusive"):
        out[column] = out[column].map(normalize_cancer_type_cell)

    return out.loc[:, FINAL_COHORT_COLUMNS if cohort_level else FINAL_TRIAL_COLUMNS]


def normalize_anzctr_resource(frame: pd.DataFrame, *, cohort_level: bool = False) -> pd.DataFrame:
    out = pd.DataFrame(index=frame.index)
    out[REGISTRY_COLUMN] = "anzctr"
    out[TRIAL_ID_COLUMN] = source_column(frame, "trialId")
    if cohort_level:
        out[COHORT_COLUMN] = source_column(frame, COHORT_COLUMN)

    out["title"] = source_column(frame, "STUDY TITLE")
    out["scientific_title"] = source_column(frame, "SCIENTIFIC TITLE")
    out["primary_sponsor"] = source_column(frame, "PRIMARY SPONSOR NAME")
    out["recruitment_status"] = source_column(frame, "RECRUITMENT STATUS")
    out["phase"] = source_column(frame, "PHASE")
    out["country"] = source_column(frame, "RECRUITMENT COUNTRY").map(
        normalize_delimited_cell
    )
    out["original_health_conditions"] = source_column(frame, "HEALTH CONDITION")
    out["intervention_type"] = source_column(frame, "anzctr_intervention_codes")
    out["intervention_name"] = source_column(frame, "DRUG_rxnorm_matched")

    for column in ELIGIBILITY_VALUE_COLUMNS:
        out[column] = source_column(frame, column)
    for column in ("cancer_type_inclusive", "cancer_type_exclusive"):
        out[column] = out[column].map(normalize_cancer_type_cell)

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
    pottr_trial_eligibility_file: str | Path | None = None,
    pottr_trial_registry_file: str | Path | None = None,
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
    resolved_pottr_trial_eligibility_file = resolve_source_path_or_url(
        pottr_trial_eligibility_file or DEFAULT_POTTR_TRIAL_ELIGIBILITY_URL,
        repo_root,
    )
    resolved_pottr_trial_registry_file = resolve_source_path_or_url(
        pottr_trial_registry_file or DEFAULT_POTTR_TRIAL_REGISTRY_URL,
        repo_root,
    )

    inputs = CombinedEligibilityResourceInputs(
        ctgov_trial_resource_file=resolved_ctgov_trial_file,
        anzctr_trial_resource_file=resolved_anzctr_trial_file,
        ctgov_cohort_resource_file=resolved_ctgov_cohort_file,
        anzctr_cohort_resource_file=resolved_anzctr_cohort_file,
        trial_output_file=resolved_trial_output_file,
        cohort_output_file=resolved_cohort_output_file,
        pottr_trial_eligibility_file=resolved_pottr_trial_eligibility_file,
        pottr_trial_registry_file=resolved_pottr_trial_registry_file,
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
    missing_pottr_trials = build_missing_pottr_trials_from_sources(
        trial_combined,
        cohort_combined,
        pottr_trial_eligibility_file=inputs.pottr_trial_eligibility_file,
        pottr_trial_registry_file=inputs.pottr_trial_registry_file,
    )
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
    LOGGER.info(
        "Computed missing POTTR trials rows=%d columns=%d",
        len(missing_pottr_trials),
        len(missing_pottr_trials.columns),
    )
    if not missing_pottr_trials.empty:
        LOGGER.info(
            "Missing POTTR trials:\n%s",
            missing_pottr_trials.to_csv(sep="\t", index=False).strip(),
        )
    return CombinedEligibilityResourceOutputs(
        trial_output_file=inputs.trial_output_file,
        cohort_output_file=inputs.cohort_output_file,
        missing_pottr_trials=missing_pottr_trials,
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
        "--pottr_trial_eligibility_file",
        default=None,
        help=(
            "POTTR trial eligibility TSV path or URL. Defaults to POTTR's "
            "trial_eligibility.AU.tsv on GitHub."
        ),
    )
    parser.add_argument(
        "--pottr_trial_registry_file",
        default=None,
        help=(
            "POTTR trial registry TSV path or URL. Defaults to POTTR's "
            "trial_registry.AU.tsv on GitHub."
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
        pottr_trial_eligibility_file=args.pottr_trial_eligibility_file,
        pottr_trial_registry_file=args.pottr_trial_registry_file,
        export_date=args.export_date,
    )
    outputs = run_combined_trial_resource_export(inputs)
    LOGGER.info(
        "Combined eligibility resource export complete: trial=%s cohort=%s missing_pottr_rows=%d",
        outputs.trial_output_file,
        outputs.cohort_output_file,
        len(outputs.missing_pottr_trials),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
