from __future__ import annotations

import json
import logging
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterator, Sequence

import pandas as pd

from aus_trial_universe.drug_utility_path.ctgov.drug_ontology.shared.schema import (
    COL_INTERVENTION_ALL_ALIASES,
    COL_INTERVENTION_ALL_ALIASES_NORMALISED,
    COL_INTERVENTION_ALL_ALIASES_NORMALISED_FULL,
    COL_INTERVENTION_ARM_GROUP_LABELS,
    COL_INTERVENTION_DESCRIPTION,
    COL_INTERVENTION_INDEX,
    COL_INTERVENTION_NAME,
    COL_INTERVENTION_OTHER_NAMES,
    COL_INTERVENTION_TYPE,
    COL_NCT_ID,
    CORE_INTERVENTION_COLUMNS,
    DISPLAY_DELIMITER,
    TARGET_DRUG_INTERVENTION_TYPES,
)

logger = logging.getLogger(__name__)

TRADEMARK_SYMBOL_RE = re.compile(r"[®™\ufe0f]")
TRADEMARK_TEXT_RE = re.compile(
    r"(?i)(?:\(\s*tm\s*\)|\^\s*tm\b|(?<=\w)tm\b)"
)


def strip_trademark_markers(value: object) -> str:
    text = "" if value is None else str(value)
    text = TRADEMARK_SYMBOL_RE.sub("", text)
    text = unicodedata.normalize("NFKC", text)
    text = TRADEMARK_SYMBOL_RE.sub("", text)
    text = TRADEMARK_TEXT_RE.sub("", text)
    return text


DASH_RE = re.compile(r"[\u2010\u2011\u2012\u2013\u2014\u2212]")
MULTISPACE_RE = re.compile(r"\s+")
LEADING_LABEL_RE = re.compile(
    r"^\s*(?:drug|biological|combination\s*product|other|arm\s+[a-z0-9]+|cohort\s+[a-z0-9]+|part\s+[a-z0-9]+)\s*[:\-]\s*",
    re.IGNORECASE,
)
PARENS_RE = re.compile(r"\(([^()]*)\)")
DOSE_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*"
    r"(?:mg|mcg|µg|g|kg|ng|iu|units?|u|ml|mL|l|L|mmol|mEq|gy|gray|auc|%)"
    r"(?:\s*/\s*(?:m\^?2|m2|kg|ml|mL|l|L|day|week|month|\d+(?:\.\d+)?\s*(?:ml|mL|l|L)))?\b",
    re.IGNORECASE,
)
SCHEDULE_OR_TRIAL_QUALIFIER_RE = re.compile(
    r"\b(?:"
    r"for\s+the\s+first|starting\s+from|cycle|cycles|day|days|week|weeks|month|months|"
    r"once\s+daily|twice\s+daily|daily|weekly|monthly|qd|bid|tid|qid|q\d+w|q\d+d|"
    r"dose\s*level|dl\d+|dose\s*expansion|dose\s*escalation|rde|rp2d|recommended\s+.*dose|"
    r"phase\s*\d+[a-z]?|part\s*[a-z0-9]+|cohort\s*[a-z0-9]+|arm\s*[a-z0-9]+|"
    r"monotherapy|combination\s+therapy|open-label|single-arm"
    r")\b",
    re.IGNORECASE,
)
DOSAGE_FORM_ROUTE_RE = re.compile(
    r"\b(?:"
    r"oral|intravenous|iv|subcutaneous|sc|intramuscular|im|topical|ophthalmic|otic|nasal|"
    r"inhaled|inhalation|infusion|injection|injectable|tablet|tablets|capsule|capsules|"
    r"solution|suspension|powder|lyophili[sz]ed|vial|syringe|implant|cream|gel|drops?"
    r")\b",
    re.IGNORECASE,
)
GENERIC_NON_LOOKUP_RE = re.compile(
    r"^(?:"
    r"chemotherapy|chemotherapy\s+drug|chemotherapeutic\s+agent|immunotherapy|hormones?|"
    r"standard\s+of\s+care|soc|investigator'?s\s+choice|physician'?s\s+choice|"
    r"investigator'?s\s+choice\s+comparative\s+therapy|comparative\s+therapy|"
    r"rescue\s+medications?|supportive\s+care\s+measures?|background\s+chemotherapy|"
    r"anticancer\s+therapy\s*\d*|antitumor\s+therapy|study\s+drug|placebo|"
    r"normal\s+saline|dextrose|saline\s+solution|0\.9%\s+saline\s+solution|"
    r"no\s+treatment|no\s+intervention|observational\s+study|usual\s+care|"
    r"quality[-\s]?of[-\s]?life\s+assessment|questionnaire\s+administration|"
    r"laboratory\s+biomarker\s+analysis|pharmacological\s+study|pharmacogenomic\s+study|"
    r"cessation|ssa\s+cessation|ssa\s+continuation|"
    r"local\s+equivalent|equivalent|physician\s+choice|"
    r"medical\s+laser\s+admini?stration|"
    r"no\s+other\s+names?|none\s+aht|other\s+aht"
    r")$",
    re.IGNORECASE,
)
GENERIC_CLASS_NON_LOOKUP_RE = re.compile(
    r"^(?:"
    r"(?:an?\s+)?(?:"
    r"aromatase\s+inhibitors?|cdk4/?6i|cdk4\s+inhibitors?|"
    r"estrogen\s+receptor\s+antagonists?|kinase\s+inhibitors?|"
    r"antiangiogenic\s+agents?|immune\s+checkpoint\s+inhibitors?|ici|"
    r"monoclonal\s+antibod(?:y|ies)|platinum[-\s]?based\s+chemotherapy|"
    r"taxane[-\s]?based\s+chemotherapy|endocrine\s+therapy|"
    r"somatostatin\s+analogues?\s+cessation"
    r"))$",
    re.IGNORECASE,
)
TRAILING_GENERIC_SUFFIX_RE = re.compile(
    r"\b(?:regimen|oral\s+tablet|iv\s+infusion|for\s+injection|single\s+dose|multiple\s+dose|fractionated\s+dose)\b$",
    re.IGNORECASE,
)
SPONSOR_SUFFIX_RE = re.compile(
    r",\s*[^,]*(?:pharmaceuticals?|pharma|healthcare|therapeutics?|biotech|biosciences?|inc\.?|ltd\.?|llc|corp\.?|company|pty\s+limited)\.?$",
    re.IGNORECASE,
)
MATCHED_OR_PLACEBO_RE = re.compile(
    r"^(?:.*[-\s])?(?:matched|matching)?[-\s]*placebo(?:\b|$)|^placebo(?:\b|$)|^.+\s+placebo$",
    re.IGNORECASE,
)
BIOSIMILAR_NON_SPECIFIC_RE = re.compile(r"^.+\bbiosimilar\b$", re.IGNORECASE)
SOMATOSTATIN_CONTINUATION_RE = re.compile(r"^continuation\s+of\s+(somatostatin\s+analogues?)$", re.IGNORECASE)
SOMATOSTATIN_CESSATION_RE = re.compile(r"^(?:cessation\s+of\s+somatostatin\s+analogues?|ssa\s+cessation)$", re.IGNORECASE)
IOPOFOSINE_I131_DOSE_RE = re.compile(r"^(Iopofosine\s+I)\s+131\s+(?:single|multiple|fractionated)\s+dose$", re.IGNORECASE)
NEW_FORMULATION_PREFIX_RE = re.compile(r"^new\s+formulation\s+of\s+", re.IGNORECASE)
EXAMPLE_INTRO_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"such\s+as|"
    r"including(?:\s+but\s+not\s+limited\s+to)?|"
    r"for\s+example|"
    r"for\s+instance|"
    r"e\.?g\.?|"
    r"eg\.?|"
    r"like"
    r")\s*[:,;\-]?\s+",
    re.IGNORECASE,
)
COMBO_CONNECTOR_RE = re.compile(r"\s*(?:\+|;|/|\bplus\b|\band\b|\bor\b|\bwith\b|\bin\s+combination\s+with\b)\s*", re.IGNORECASE)
COMMA_LIST_RE = re.compile(r"\s*,\s*")

DISCARD_NORMALISED_TERM_PATTERNS = (
    GENERIC_NON_LOOKUP_RE,
    GENERIC_CLASS_NON_LOOKUP_RE,
    MATCHED_OR_PLACEBO_RE,
    BIOSIMILAR_NON_SPECIFIC_RE,
    SOMATOSTATIN_CESSATION_RE,
)


def is_missing(value: object) -> bool:
    return value is None or (isinstance(value, float) and pd.isna(value))


def clean_text(value: object) -> str:
    return "" if is_missing(value) else str(value).strip()


def ensure_list(value: object) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def normalise_for_dedupe(text: str) -> str:
    return MULTISPACE_RE.sub(" ", text.strip().lower())


def ordered_unique(values: Sequence[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value:
            continue
        key = normalise_for_dedupe(value)
        if key not in seen:
            seen.add(key)
            ordered.append(value)
    return ordered


def ordered_unique_nonblank(values: Sequence[object]) -> list[str]:
    return ordered_unique([clean_text(value) for value in values])


def join_pipe(values: Sequence[object]) -> str:
    return DISPLAY_DELIMITER.join(ordered_unique_nonblank(values))


def basic_text_normalise(text: object) -> str:
    value = strip_trademark_markers(clean_text(text))
    value = DASH_RE.sub("-", value)
    value = value.replace("，", ",")
    return MULTISPACE_RE.sub(" ", value).strip()


def is_qualifier_parenthetical(content: str) -> bool:
    cleaned = basic_text_normalise(content)
    return not cleaned or bool(
        DOSE_RE.search(cleaned)
        or SCHEDULE_OR_TRIAL_QUALIFIER_RE.search(cleaned)
        or cleaned.lower() in {"drug", "procedure", "active name"}
    )


def extract_parenthetical_terms(text: str) -> tuple[str, list[str]]:
    extra_terms: list[str] = []

    def replace_match(match: re.Match[str]) -> str:
        content = match.group(1).strip()
        if is_qualifier_parenthetical(content):
            return " "
        extra_terms.extend(split_alias_component(content))
        return " "

    # Square brackets often encode radiolabels such as [177Lu] or [212Pb].
    # They are deliberately left attached to the term.
    return PARENS_RE.sub(replace_match, text), extra_terms


def strip_dose_form_route_schedule(text: str) -> str:
    current = DOSE_RE.sub(" ", text)
    current = SCHEDULE_OR_TRIAL_QUALIFIER_RE.sub(" ", current)
    current = DOSAGE_FORM_ROUTE_RE.sub(" ", current)
    return MULTISPACE_RE.sub(" ", current).strip(" ,;:+-/")


def clean_normalised_term(text: object) -> str:
    current = basic_text_normalise(text)
    current = LEADING_LABEL_RE.sub("", current)
    current = SPONSOR_SUFFIX_RE.sub("", current)
    current = NEW_FORMULATION_PREFIX_RE.sub("", current)
    current = EXAMPLE_INTRO_PREFIX_RE.sub("", current)

    if match := IOPOFOSINE_I131_DOSE_RE.match(current):
        current = match.group(1)
    if match := SOMATOSTATIN_CONTINUATION_RE.match(current):
        current = match.group(1)

    replacements = (
        (r"\bmatched[-\s]?placebo\b", "placebo"),
        (r"\bplacebo\s+(?:to|for|matching)\b.*$", "Placebo"),
        (r"\b(?:or\s+)?(?:local\s+)?equivalent\b", " "),
        (r"\bphysician\s+choice\b", " "),
    )
    for pattern, replacement in replacements:
        current = re.sub(pattern, replacement, current, flags=re.IGNORECASE)

    current = TRAILING_GENERIC_SUFFIX_RE.sub("", current)
    current = strip_dose_form_route_schedule(current)
    current = TRAILING_GENERIC_SUFFIX_RE.sub("", current)
    current = MULTISPACE_RE.sub(" ", current)
    return current.strip(" ,;:+-/")


def should_discard_normalised_term(text: str) -> bool:
    cleaned = clean_normalised_term(text)
    return not cleaned or any(pattern.match(cleaned) for pattern in DISCARD_NORMALISED_TERM_PATTERNS)


def should_split_on_comma(text: str) -> bool:
    """Split comma lists only when the string looks like a short intervention list."""
    if "," not in text:
        return False
    if re.search(r"\b(?:and|or|plus|with)\b", text, flags=re.IGNORECASE):
        return True
    pieces = [piece.strip() for piece in text.split(",") if piece.strip()]
    return 2 <= len(pieces) <= 4 and all(len(piece.split()) <= 4 for piece in pieces)


def ordered_unique_clean_terms(values: Sequence[str]) -> list[str]:
    cleaned_values = [clean_normalised_term(value) for value in values]
    return ordered_unique([value for value in cleaned_values if value])


def split_alias_component(component: str) -> list[str]:
    component = basic_text_normalise(component)
    if not component:
        return []

    component, parenthetical_terms = extract_parenthetical_terms(component)
    component = clean_normalised_term(component)

    parts = [piece for piece in COMBO_CONNECTOR_RE.split(component) if piece.strip()] if component else []
    expanded: list[str] = []
    for part in parts:
        part = clean_normalised_term(part)
        if not part:
            continue
        expanded.extend(COMMA_LIST_RE.split(part) if should_split_on_comma(part) else [part])

    expanded.extend(parenthetical_terms)
    return ordered_unique_clean_terms(
        [part for part in expanded if not should_discard_normalised_term(part)]
    )


def normalise_intervention_aliases(intervention_all_aliases: object) -> str:
    raw_value = clean_text(intervention_all_aliases)
    if not raw_value:
        return ""

    normalised_terms: list[str] = []
    for alias in raw_value.split(DISPLAY_DELIMITER):
        normalised_terms.extend(split_alias_component(alias))
    return DISPLAY_DELIMITER.join(ordered_unique_clean_terms(normalised_terms))


def normalise_intervention_aliases_full(intervention_all_aliases: object) -> str:
    """Clean aliases but preserve full combination/regimen strings for exact alias matching."""
    raw_value = clean_text(intervention_all_aliases)
    if not raw_value:
        return ""

    normalised_terms: list[str] = []
    for alias in raw_value.split(DISPLAY_DELIMITER):
        alias = basic_text_normalise(alias)
        alias, parenthetical_terms = extract_parenthetical_terms(alias)
        alias = clean_normalised_term(alias)

        if alias and not should_discard_normalised_term(alias):
            normalised_terms.append(alias)
        normalised_terms.extend(
            term for term in parenthetical_terms if not should_discard_normalised_term(term)
        )

    return DISPLAY_DELIMITER.join(ordered_unique_clean_terms(normalised_terms))


def should_normalise_intervention_type(intervention_type: str) -> bool:
    return intervention_type.strip().upper() in TARGET_DRUG_INTERVENTION_TYPES


def extract_study_records(payload: Any) -> list[dict[str, Any]]:
    """Accept list payloads, studies wrappers, or a single CTGov study object."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if isinstance(payload, dict):
        for key in ("studies", "Studies"):
            if isinstance(payload.get(key), list):
                return [item for item in payload[key] if isinstance(item, dict)]
        if "protocolSection" in payload:
            return [payload]

    raise ValueError(
        "Unsupported JSON structure. Expected a list of studies, a studies wrapper, "
        "or a single study object."
    )


def iter_study_records_from_json(input_json: Path) -> Iterator[dict[str, Any]]:
    """Read standard JSON or JSON Lines / NDJSON containing CTGov study records."""
    if not input_json.exists():
        raise FileNotFoundError(f"Input JSON not found: {input_json}")

    text = input_json.read_text(encoding="utf-8").strip()
    if not text:
        return

    try:
        for record in extract_study_records(json.loads(text)):
            yield record
        return
    except json.JSONDecodeError:
        pass

    with input_json.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Failed to parse JSON on line {line_number} of {input_json}: {exc}"
                ) from exc
            if not isinstance(payload, dict):
                raise ValueError(
                    f"Expected each JSON line to be an object, got {type(payload).__name__} "
                    f"on line {line_number}"
                )
            yield payload


def build_intervention_all_aliases(intervention_name: str, other_names: Sequence[object]) -> str:
    return join_pipe([intervention_name, *other_names])


def extract_intervention_rows(study_record: dict[str, Any]) -> list[dict[str, str]]:
    protocol_section = study_record.get("protocolSection") or {}
    identification_module = protocol_section.get("identificationModule") or {}
    arms_module = protocol_section.get("armsInterventionsModule") or {}

    nct_id = clean_text(identification_module.get("nctId"))
    interventions = ensure_list(arms_module.get("interventions"))

    rows: list[dict[str, str]] = []
    for intervention_index, intervention in enumerate(interventions):
        if not isinstance(intervention, dict):
            logger.warning(
                "Skipping non-dict intervention at nct_id=%r intervention_index=%d",
                nct_id,
                intervention_index,
            )
            continue

        intervention_type = clean_text(intervention.get("type"))
        intervention_name = clean_text(intervention.get("name"))
        intervention_description = clean_text(intervention.get("description"))
        other_names = ensure_list(intervention.get("otherNames"))
        arm_group_labels = ensure_list(intervention.get("armGroupLabels"))
        intervention_all_aliases = build_intervention_all_aliases(intervention_name, other_names)

        normalised_aliases = ""
        normalised_full_aliases = ""
        if should_normalise_intervention_type(intervention_type):
            normalised_aliases = normalise_intervention_aliases(intervention_all_aliases)
            normalised_full_aliases = normalise_intervention_aliases_full(intervention_all_aliases)

        rows.append(
            {
                COL_NCT_ID: nct_id,
                COL_INTERVENTION_INDEX: str(intervention_index),
                COL_INTERVENTION_TYPE: intervention_type,
                COL_INTERVENTION_NAME: intervention_name,
                COL_INTERVENTION_DESCRIPTION: intervention_description,
                COL_INTERVENTION_OTHER_NAMES: join_pipe(other_names),
                COL_INTERVENTION_ARM_GROUP_LABELS: join_pipe(arm_group_labels),
                COL_INTERVENTION_ALL_ALIASES: intervention_all_aliases,
                COL_INTERVENTION_ALL_ALIASES_NORMALISED: normalised_aliases,
                COL_INTERVENTION_ALL_ALIASES_NORMALISED_FULL: normalised_full_aliases,
            }
        )

    return rows


def build_interventions_dataframe(input_json: Path) -> pd.DataFrame:
    """Build the canonical intervention dataframe from CTGov JSON/NDJSON.

    This function does not write TSV output. The canonical pipeline is:
      JSON -> PostgreSQL -> SQL-derived TSV dump.
    """
    rows: list[dict[str, str]] = []
    for study_number, study_record in enumerate(iter_study_records_from_json(input_json), start=1):
        rows.extend(extract_intervention_rows(study_record))
        if study_number % 100 == 0:
            logger.info("Processed %d study records", study_number)

    return pd.DataFrame(rows, columns=CORE_INTERVENTION_COLUMNS)
