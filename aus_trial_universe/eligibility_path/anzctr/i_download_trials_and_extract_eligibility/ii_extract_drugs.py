from __future__ import annotations

import argparse
from datetime import datetime
import json
import logging
import re
import tempfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import pandas as pd

from aus_trial_universe.drug_utility_path.ctgov.drug_ontology.identity.rxnorm.matcher import (
    CURATED_INGREDIENT_RESCUE_EXPANSIONS,
    RXNCONSO_FILENAME,
    RXNREL_FILENAME,
    SINGLE_AGENT_ABBREVIATION_EXPANSIONS,
    RxnConsoIndex,
    RxnConsoRow,
    RxnRelIndex,
    choose_substring_anchor,
    is_explicitly_approved_short_term,
    normalize_lookup_text,
    normalize_output_text,
    rank_conso_row,
    resolve_term,
    should_skip_obvious_non_drug_lookup_term,
    should_skip_short_ambiguous_term,
    token_boundary_pattern,
    tokenize,
)
from aus_trial_universe.eligibility_path.anzctr.i_download_trials_and_extract_eligibility.i_select_trials_and_fields import (
    DEFAULT_INPUT_XLSX,
    extract_fields_to_csv,
)
from trialcurator.drug_openai_client import DrugOpenaiClient

logger = logging.getLogger(__name__)

DEFAULT_INPUT_CSV = Path("data/trial_inputs/anzctr/extracted_trials/anzctr_field_extractions.csv")
DEFAULT_OUTPUT_CSV = DEFAULT_INPUT_CSV
DEFAULT_RXNORM_RRF_DIR = Path(
    "data/drug_utility_path/drug_ontology/raw_inputs/RxNorm"
)
RXNORM_RRF_ROOT_CANDIDATES = (
    DEFAULT_RXNORM_RRF_DIR,
    Path("data/drug_ontology/raw_inputs/RxNorm"),
    Path("data/ctgov/drug_ontology/raw_inputs/RxNorm"),
)

INTERVENTIONS_COLUMN = "INTERVENTIONS"
SCIENTIFIC_TITLE_COLUMN = "SCIENTIFIC TITLE"
RXNORM_MATCHED_DRUGS_COLUMN = "DRUG_rxnorm_matched"
RXNORM_MATCHED_INTERVENTIONS_COLUMN = "DRUG_rxnorm_matched_interventions"
RXNORM_MATCHED_TITLE_COLUMN = "DRUG_rxnorm_matched_title"
LLM_DRUG_TO_REMOVE_COLUMN = "llm_drug_to_remove"
LLM_DRUGS_TO_ADD_COLUMN = "llm_drugs_to_add"
LLM_DRUGS_TO_CORRECT_COLUMN = "llm_drugs_to_correct"
LLM_REASONING_COLUMN = "llm_reasoning"
LLM_REVIEW_COLUMNS = (
    LLM_DRUG_TO_REMOVE_COLUMN,
    LLM_DRUGS_TO_ADD_COLUMN,
    LLM_DRUGS_TO_CORRECT_COLUMN,
    LLM_REASONING_COLUMN,
)
DISPLAY_DELIMITER = " | "
CORRECTION_DELIMITER = " => "
MULTISPACE_RE = re.compile(r"\s+")
EDGE_PUNCT_RE = re.compile(r"^[\s\.,;:/\\+\-]+|[\s\.,;:/\\+\-]+$")
RXNORM_VERSION_DIR_RE = re.compile(r"^version_(\d{8})$")

MENTION_TTYS = {"IN", "PIN", "MIN", "BN"}
MENTION_SABS = {"RXNORM"}
ANZCTR_MENTION_STOP_TERMS = {
    "active",
    "anhydrous lactose",
    "antimony",
    "arsenic",
    "cadmium",
    "calcium",
    "chromium",
    "complete",
    "control",
    "copper",
    "creatinine",
    "cytosine",
    "cyclohexane",
    "gelatin",
    "hyaluronate",
    "hyaluronic acid",
    "hypromellose",
    "iodine",
    "magnesium",
    "magnesium stearate",
    "maintain",
    "manganese",
    "mercury",
    "methylcellulose",
    "microcrystalline cellulose",
    "nickel",
    "perform",
    "potassium",
    "process",
    "selenium",
    "silver",
    "sodium",
    "sodium chloride",
    "sodium stearyl fumarate",
    "starch",
    "sterile water",
    "tablet",
    "tablets",
    "thallium",
    "therapeutic",
    "water",
}
ANZCTR_BLOCKED_ABBREVIATION_EXPANSIONS = {
    "g-csf",
}
PRIOR_TREATMENT_CONTEXT_PATTERNS = (
    re.compile(
        r"\bpreviously\s+"
        r"(?:received|treated\s+with|administered|exposed\s+to)\b[^.;:\n]{0,80}$"
    ),
    re.compile(
        r"\bprior\s+"
        r"(?:treatment|therapy|chemotherapy|exposure)\s*(?:with|to)?\b[^.;:\n]{0,80}$"
    ),
    re.compile(r"\b(?:failed|failing)\b[^.;:\n]{0,80}$"),
    re.compile(r"\bfailure\s+of\b[^.;:\n]{0,80}$"),
    re.compile(r"\brefractory\s+to\b[^.;:\n]{0,80}$"),
    re.compile(r"\bprogress(?:ed|ion)\s+(?:on|after)\b[^.;:\n]{0,80}$"),
)
PRIOR_TREATMENT_RESET_PATTERN = re.compile(
    r"\b(?:will\s+be\s+treated|will\s+receive|will\s+be\s+given|"
    r"will\s+be\s+administered|is\s+treated|are\s+treated|"
    r"administered|given|intervention|intervention\s+group|"
    r"combination\s+of)\b"
)
ANZCTR_CURATED_DRUG_ALIASES = {
    # Australian spellings, common local names, and typos not present as native
    # RxNorm mention anchors in this release.
    "adrenaline": "epinephrine",
    "flurouracil": "fluorouracil",
    "lignocaine": "lidocaine",
    "oestriol": "estriol",
    "paracetamol": "paracetamol",
    # Named ANZCTR investigational/proprietary agents and vaccines missed by
    # the strict RxNorm IN/PIN/MIN/BN pass.
    "10vPCV": "10vPCV",
    "13-valent pneumococcal conjugate vaccine": "13-valent pneumococcal conjugate vaccine",
    "177Lutetium-PSMA-I&T": "177Lu-PSMA-I&T",
    "177Lu-PSMA": "177Lu-PSMA",
    "177Lu-PSMA-I&T": "177Lu-PSMA-I&T",
    "177Lu-RAD202": "177Lu-RAD202",
    "177Lu-RAD202im": "177Lu-RAD202",
    "177Lu-RAD202tr": "177Lu-RAD202",
    "177Lu-RAD204": "177Lu-RAD204",
    "177Lu-RAD204im": "177Lu-RAD204",
    "177Lu-RAD204tr": "177Lu-RAD204",
    "177Lutetium-Prostate-specfic Membrane Antigen-597": "177Lu-PSMA-597",
    "212-Pb": "212-Pb",
    "7vPCV": "7vPCV",
    "8-PEG20": "8-PEG20",
    "[177Lu]Lu-PSMA": "177Lu-PSMA",
    "[177Lu]Lu-PSMA-597": "177Lu-PSMA-597",
    "[89Zr]-barecetamab-DFO": "barecetamab",
    "ACS2015": "ACS2015",
    "ADVC001": "ADVC001",
    "Afuresertib": "Afuresertib",
    "AN8025": "AN8025",
    "AP23573": "AP23573",
    "Apomab": "Apomab",
    "AT-0174": "AT-0174",
    "ATNM-400": "ATNM-400",
    "AUY922": "AUY922",
    "AXA-042": "AXA-042",
    "AZD5153": "AZD5153",
    "barecetamab": "barecetamab",
    "bel-sar": "bel-sar",
    "BMS-986504": "BMS-986504",
    "Brivanib": "Brivanib",
    "c2 tablet": "c2",
    "Cediranib": "Cediranib",
    "Ceralasertib": "Ceralasertib",
    "certepetide": "certepetide",
    "Cloretazine": "Cloretazine",
    "Coramsine": "Coramsine",
    "CS1003": "CS1003",
    "Dulanermin": "Dulanermin",
    "E7080": "E7080",
    "E-EDV-D682": "E-EDV-D682",
    "E-EDV-D682/GC": "E-EDV-D682/GC",
    "EIK1005": "EIK1005",
    "Elacytarabine": "Elacytarabine",
    "EN002": "EN002",
    "Enzomenib": "Enzomenib",
    "EO1001": "EO1001",
    "FLD-103": "FLD-103",
    "FSD147L": "FSD147L",
    "GB221": "GB221",
    "GQ1001": "GQ1001",
    "GRWD5769": "GRWD5769",
    "HH3806": "HH3806",
    "HLX20": "HLX20",
    "HMPL-504": "Volitinib",
    "Hypera": "Hypera",
    "IMD-101": "IMD-101",
    "IMD303": "IMD303",
    "intravenous immunoglobulin": "immunoglobulin",
    "ISB 1442": "ISB 1442",
    "KN046": "KN046",
    "KUVA-01": "KUVA-01",
    "Labetuzumab": "Labetuzumab",
    "MDMA": "MDMA",
    "MDX-1097": "MDX-1097",
    "Medicinal Cannabis": "Medicinal Cannabis",
    "MK-8669": "MK-8669",
    "neoantigen peptide vaccine": "neoantigen peptide vaccine",
    "nimotuzumab": "nimotuzumab",
    "NRT": "nicotine",
    "oral c2": "c2",
    "OZ-001": "OZ-001",
    "PAX 1": "PAX 1",
    "PENAO": "PENAO",
    "PEX010": "PEX010",
    "Pexa-Vec": "Pexastimogene Devacirepvec",
    "Pexastimogene Devacirepvec": "Pexastimogene Devacirepvec",
    "PF-804": "PF-804",
    "PHI-101": "PHI-101",
    "Phenoxodiol": "Phenoxodiol",
    "PI-88": "PI-88",
    "PMCC-COE19 CAR-T Cells": "PMCC-COE19 CAR-T Cells",
    "PMO-Gli1": "PMO-Gli1",
    "PNU-159682": "PNU-159682",
    "PTX-108": "PTX-108",
    "QV0": "QV0",
    "RPH-203": "RPH-203",
    "RPH203": "RPH-203",
    "RX108": "RX108",
    "RX108-A": "RX108-A",
    "SL-28": "SL-28",
    "SNDX5613": "SNDX5613",
    "ST-617": "ST-617",
    "sub-cutaneous immunoglobulin": "immunoglobulin",
    "subcutaneous immunoglobulin": "immunoglobulin",
    "Surovatamig": "Surovatamig",
    "Synflorix": "Synflorix",
    "THC Oil": "THC Oil",
    "trivalent seasonal flu vaccine": "trivalent seasonal flu vaccine",
    "Veliparib": "Veliparib",
    "VMCL vaccine": "VMCL vaccine",
    "Volitinib": "Volitinib",
    "VNP40101M": "Cloretazine",
    "ZE74-0282": "ZE74-0282",
    "Zhenqi Fuzheng Capsule": "Zhenqi Fuzheng Capsule",
}


@dataclass(frozen=True)
class DrugNameCorrection:
    from_name: str
    to_name: str


@dataclass(frozen=True)
class DrugExtractionReview:
    drugs_to_remove: tuple[str, ...] = ()
    drugs_to_add: tuple[str, ...] = ()
    drugs_to_correct: tuple[DrugNameCorrection, ...] = ()
    summary_text: str = ""

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "DrugExtractionReview":
        return cls(
            drugs_to_remove=clean_review_names(payload.get("drugs_to_remove", [])),
            drugs_to_add=clean_review_names(payload.get("drugs_to_add", [])),
            drugs_to_correct=clean_review_corrections(
                payload.get("drugs_to_correct", [])
            ),
            summary_text=clean_summary_text(payload.get("summary_text", "")),
        )

    def as_csv_columns(
        self,
        *,
        list_delimiter: str = DISPLAY_DELIMITER,
        correction_delimiter: str = CORRECTION_DELIMITER,
    ) -> tuple[str, str, str]:
        return (
            list_delimiter.join(self.drugs_to_remove),
            list_delimiter.join(self.drugs_to_add),
            list_delimiter.join(
                f"{correction.from_name}{correction_delimiter}{correction.to_name}"
                for correction in self.drugs_to_correct
            ),
        )


@dataclass(frozen=True)
class LlmReviewTask:
    row_index: int
    scientific_title: object
    interventions: object
    rxnorm_matched_drugs: tuple[str, ...]


def clean_review_text(value: object) -> str:
    text = "" if value is None else str(value)
    return EDGE_PUNCT_RE.sub("", MULTISPACE_RE.sub(" ", text.strip())).strip()


def clean_summary_text(value: object) -> str:
    text = "" if value is None else str(value)
    return MULTISPACE_RE.sub(" ", text.strip()).strip()


def clean_review_names(value: object) -> tuple[str, ...]:
    if isinstance(value, list):
        return ordered_unique_clean_names(value)
    if isinstance(value, str):
        return ordered_unique_clean_names([value])
    return ()


def clean_review_corrections(value: object) -> tuple[DrugNameCorrection, ...]:
    if not isinstance(value, list):
        return ()

    corrections: list[DrugNameCorrection] = []
    seen: set[tuple[str, str]] = set()
    for item in value:
        if not isinstance(item, Mapping):
            continue
        from_name = clean_review_text(item.get("from", ""))
        to_name = clean_review_text(item.get("to", ""))
        key = (from_name.casefold(), to_name.casefold())
        if from_name and to_name and key not in seen:
            seen.add(key)
            corrections.append(DrugNameCorrection(from_name=from_name, to_name=to_name))

    return tuple(corrections)


def ordered_unique_clean_names(values: list[object]) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = clean_review_text(value)
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            out.append(cleaned)
    return tuple(out)


@dataclass(frozen=True)
class MentionLexiconEntry:
    surface: str
    surface_norm: str
    rxcui: str
    output_name: str | None = None


@dataclass(frozen=True)
class SpanMatch:
    start: int
    end: int
    entries: tuple[MentionLexiconEntry, ...]


class DrugMentionLexicon:
    def __init__(self, entries: Sequence[MentionLexiconEntry]) -> None:
        self.entries_by_anchor: dict[str, list[MentionLexiconEntry]] = defaultdict(list)
        for entry in entries:
            anchor = choose_substring_anchor(tokenize(entry.surface_norm))
            if anchor:
                self.entries_by_anchor[anchor].append(entry)

    @classmethod
    def from_rxnorm_index(cls, conso_index: RxnConsoIndex) -> "DrugMentionLexicon":
        entries = build_rxnorm_mention_entries(conso_index)
        logger.info("Built ANZCTR RxNorm mention lexicon with %d entries", len(entries))
        return cls(entries)

    def find_entries(self, text: object) -> list[SpanMatch]:
        text_norm = normalize_lookup_text(text)
        if not text_norm:
            return []

        candidate_entries = {
            entry
            for token in set(tokenize(text_norm))
            for entry in self.entries_by_anchor.get(token, [])
        }

        matches_by_span: dict[tuple[int, int], list[MentionLexiconEntry]] = defaultdict(
            list
        )
        for entry in sorted(
            candidate_entries,
            key=lambda item: (item.surface_norm, item.rxcui, item.output_name or ""),
        ):
            pattern = token_boundary_pattern(entry.surface_norm)
            for match in pattern.finditer(text_norm):
                matches_by_span[(match.start(), match.end())].append(entry)

        spans = [
            SpanMatch(start=start, end=end, entries=tuple(entries))
            for (start, end), entries in matches_by_span.items()
        ]
        return select_longest_non_overlapping_spans(spans)


def candidate_surface_is_usable(surface: object) -> bool:
    surface_norm = normalize_lookup_text(surface)
    compact = "".join(tokenize(surface_norm))
    if not surface_norm or not compact:
        return False
    if surface_norm in ANZCTR_MENTION_STOP_TERMS:
        return False
    if is_explicitly_approved_short_term(surface_norm):
        return True
    if should_skip_short_ambiguous_term(surface_norm):
        return False
    if should_skip_obvious_non_drug_lookup_term(surface_norm):
        return False
    return len(compact) >= 4


def build_rxnorm_mention_entries(
    conso_index: RxnConsoIndex,
) -> list[MentionLexiconEntry]:
    best_rows_by_surface: dict[str, RxnConsoRow] = {}

    for row in conso_index.rows:
        if (
            row.lat != "ENG"
            or row.suppress == "O"
            or row.sab not in MENTION_SABS
            or row.tty not in MENTION_TTYS
        ):
            continue
        if not candidate_surface_is_usable(row.str_value):
            continue

        surface_norm = normalize_lookup_text(row.str_value)
        existing = best_rows_by_surface.get(surface_norm)
        if existing is None or rank_conso_row(row) < rank_conso_row(existing):
            best_rows_by_surface[surface_norm] = row

    entries_by_key: dict[tuple[str, str], MentionLexiconEntry] = {
        (surface_norm, row.rxcui): MentionLexiconEntry(
            surface=row.str_value,
            surface_norm=surface_norm,
            rxcui=row.rxcui,
        )
        for surface_norm, row in best_rows_by_surface.items()
    }

    for abbreviation, expansions in abbreviation_expansions().items():
        surface_norm = normalize_lookup_text(abbreviation)
        if not candidate_surface_is_usable(abbreviation):
            continue
        for expansion in expansions:
            resolution = resolve_term(expansion, conso_index)
            if resolution.match_status != "MATCHED" or not resolution.rxcui:
                continue
            entries_by_key[(surface_norm, resolution.rxcui)] = MentionLexiconEntry(
                surface=abbreviation,
                surface_norm=surface_norm,
                rxcui=resolution.rxcui,
            )

    for alias, output_name in ANZCTR_CURATED_DRUG_ALIASES.items():
        surface_norm = normalize_lookup_text(alias)
        if not surface_norm or surface_norm in ANZCTR_MENTION_STOP_TERMS:
            continue
        entries_by_key[
            (surface_norm, f"CURATED:{normalize_lookup_text(output_name)}")
        ] = MentionLexiconEntry(
            surface=alias,
            surface_norm=surface_norm,
            rxcui="",
            output_name=output_name,
        )

    return list(entries_by_key.values())


def abbreviation_expansions() -> dict[str, list[str]]:
    combined: dict[str, list[str]] = {}
    combined.update(SINGLE_AGENT_ABBREVIATION_EXPANSIONS)
    combined.update(CURATED_INGREDIENT_RESCUE_EXPANSIONS)
    for abbreviation in list(combined):
        if normalize_lookup_text(abbreviation) in ANZCTR_BLOCKED_ABBREVIATION_EXPANSIONS:
            combined.pop(abbreviation)
    return combined


def select_longest_non_overlapping_spans(spans: Sequence[SpanMatch]) -> list[SpanMatch]:
    selected: list[SpanMatch] = []
    occupied: list[tuple[int, int]] = []

    for span in sorted(spans, key=lambda item: (item.start, -(item.end - item.start))):
        if any(span.start < end and start < span.end for start, end in occupied):
            continue
        selected.append(span)
        occupied.append((span.start, span.end))

    return sorted(selected, key=lambda item: item.start)


def canonical_drug_name(
    entry: MentionLexiconEntry,
    conso_index: RxnConsoIndex,
    rel_index: RxnRelIndex | None,
    cache: dict[str, str],
) -> str:
    if entry.output_name is not None:
        return normalize_output_text(entry.output_name)

    if not entry.rxcui:
        return normalize_output_text(entry.surface)

    if entry.rxcui in cache:
        return cache[entry.rxcui]

    if rel_index is not None:
        ingredient = rel_index.resolve_ingredient(
            entry.rxcui, conso_index, source_term=entry.surface
        )
        if ingredient.ingredient_name:
            cache[entry.rxcui] = normalize_output_text(ingredient.ingredient_name)
            return cache[entry.rxcui]

    best_row = conso_index.best_row_for_rxcui(entry.rxcui)
    cache[entry.rxcui] = normalize_output_text(
        best_row.str_value if best_row else entry.surface
    )
    return cache[entry.rxcui]


def ordered_unique_drug_names(names: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for name in names:
        cleaned = normalize_output_text(name)
        key = normalize_lookup_text(cleaned)
        if cleaned and key not in seen:
            seen.add(key)
            out.append(cleaned)
    return out


def extract_rxnorm_matched_drugs(
    interventions: object,
    lexicon: DrugMentionLexicon,
    conso_index: RxnConsoIndex,
    rel_index: RxnRelIndex | None,
    canonical_cache: dict[str, str],
    *,
    exclude_prior_treatment_context: bool = True,
) -> list[str]:
    text_norm = normalize_lookup_text(interventions)
    names: list[str] = []
    for span in lexicon.find_entries(interventions):
        if exclude_prior_treatment_context and is_prior_treatment_context(
            text_norm,
            span,
        ):
            continue
        for entry in span.entries:
            names.append(
                canonical_drug_name(entry, conso_index, rel_index, canonical_cache)
            )
    return ordered_unique_drug_names(names)


def is_prior_treatment_context(text_norm: str, span: SpanMatch) -> bool:
    before = text_norm[max(0, span.start - 160) : span.start]
    for pattern in PRIOR_TREATMENT_CONTEXT_PATTERNS:
        match = pattern.search(before)
        if not match:
            continue
        if PRIOR_TREATMENT_RESET_PATTERN.search(before[match.start() :]):
            continue
        return True
    return False


def stringify_cell(value: object) -> str:
    return "" if pd.isna(value) else str(value)


def llm_review_columns(review: Mapping[str, object]) -> tuple[str, str, str]:
    return DrugExtractionReview.from_mapping(review).as_csv_columns(
        list_delimiter=DISPLAY_DELIMITER,
        correction_delimiter=CORRECTION_DELIMITER,
    )


def build_llm_review_prompts(
    scientific_title: object,
    interventions: object,
    rxnorm_matched_drugs: Sequence[str],
) -> tuple[str, str]:
    system_prompt = (
        "You are a clinical-trial drug extraction reviewer. "
        "Return only JSON. Do not include markdown. "
        "Prefer canonical active ingredient or product names over brand names."
    )
    user_prompt = f"""
Review this ANZCTR source text and the deterministic RxNorm drug matches.

Goal:
Produce a conservative audit of intervention-drug extraction. DRUG_rxnorm_matched
is intended to contain canonical active ingredient or specific therapeutic product
names, not every synonym found in the source text.

Tasks:
1. Identify extracted drugs that should be removed because they are not specific
   intervention drugs in the source text, are incorrect matches, or are only
   excipients, diluents, lab markers, procedures, broad classes, prior therapies,
   historical therapies, eligibility context, or generic support text. Use exact
   names from DRUG_rxnorm_matched.
2. Identify specific named intervention drugs, biologics, vaccines, hormones,
   radiopharmaceuticals, or cell/gene therapies present in SCIENTIFIC TITLE or
   INTERVENTIONS but missing from DRUG_rxnorm_matched.
3. Identify extracted names that should be corrected rather than removed/added.
   Prefer corrections when an extracted name is conceptually right but should be
   normalized, for example removing a generic suffix such as "vaccine" from a
   named vaccine product.
4. Do not add brand names, trade names, abbreviations, or synonyms if an equivalent
   canonical active ingredient or product is already present in DRUG_rxnorm_matched.
   Examples: do not add Avastin when bevacizumab is present; do not add Xeloda when
   capecitabine is present; do not add Glivec when imatinib is present; do not add
   Taxotere when docetaxel is present; do not add 5-FU when fluorouracil is present.
5. Do not replace a canonical active ingredient with a brand name. For example, do
   not remove octreotide just because Sandostatin LAR is named in the source text.
6. Do not list broad classes such as chemotherapy, immunotherapy, study drug,
   treatment, standard of care, placebo, surgery, or radiotherapy unless a specific
   agent is named.
7. Do not duplicate the same issue across remove/add/correct. Prefer correct when
   the extracted drug is conceptually right but the name should change.
8. Write a concise summary_text explaining the reasoning, especially any removals,
   additions, or corrections. If no change is needed, say why briefly.

Return JSON with this exact shape:
{{
  "drugs_to_remove": ["exact extracted drug name"],
  "drugs_to_add": ["missing drug name"],
  "drugs_to_correct": [
    {{"from": "exact extracted drug name", "to": "corrected drug name"}}
  ],
  "summary_text": "brief reasoning summary"
}}

DRUG_rxnorm_matched:
{json.dumps(list(rxnorm_matched_drugs), ensure_ascii=False)}

SCIENTIFIC TITLE:
{stringify_cell(scientific_title)}

INTERVENTIONS:
{stringify_cell(interventions)}
"""
    return system_prompt, user_prompt


def llm_review_drug_extraction(
    scientific_title: object,
    interventions: object,
    rxnorm_matched_drugs: Sequence[str],
    *,
    model: str | None = None,
    max_retries: int = DrugOpenaiClient.MAX_RETRIES,
    retry_initial_delay_seconds: float = DrugOpenaiClient.RETRY_INITIAL_DELAY_SECONDS,
    retry_max_delay_seconds: float = DrugOpenaiClient.RETRY_MAX_DELAY_SECONDS,
) -> DrugExtractionReview:
    client = DrugOpenaiClient(
        model=model,
        max_retries=max_retries,
        retry_initial_delay_seconds=retry_initial_delay_seconds,
        retry_max_delay_seconds=retry_max_delay_seconds,
    )
    system_prompt, user_prompt = build_llm_review_prompts(
        scientific_title,
        interventions,
        rxnorm_matched_drugs,
    )
    return DrugExtractionReview.from_mapping(
        client.ask_json(user_prompt=user_prompt, system_prompt=system_prompt)
    )


def run_llm_review_task(
    task: LlmReviewTask,
    *,
    llm_reviewer: Callable[..., DrugExtractionReview],
    llm_model: str | None,
    llm_max_retries: int,
    llm_retry_initial_delay_seconds: float,
    llm_retry_max_delay_seconds: float,
) -> tuple[int, DrugExtractionReview]:
    return (
        task.row_index,
        llm_reviewer(
            task.scientific_title,
            task.interventions,
            list(task.rxnorm_matched_drugs),
            model=llm_model,
            max_retries=llm_max_retries,
            retry_initial_delay_seconds=llm_retry_initial_delay_seconds,
            retry_max_delay_seconds=llm_retry_max_delay_seconds,
        ),
    )


def run_llm_reviews(
    tasks: Sequence[LlmReviewTask],
    *,
    llm_reviewer: Callable[..., DrugExtractionReview],
    llm_model: str | None,
    llm_workers: int,
    llm_max_retries: int,
    llm_retry_initial_delay_seconds: float,
    llm_retry_max_delay_seconds: float,
) -> dict[int, DrugExtractionReview]:
    if llm_workers < 1:
        raise ValueError("--llm_workers must be at least 1")

    if not tasks:
        return {}

    if llm_workers == 1:
        return {
            row_index: review
            for row_index, review in (
                run_llm_review_task(
                    task,
                    llm_reviewer=llm_reviewer,
                    llm_model=llm_model,
                    llm_max_retries=llm_max_retries,
                    llm_retry_initial_delay_seconds=llm_retry_initial_delay_seconds,
                    llm_retry_max_delay_seconds=llm_retry_max_delay_seconds,
                )
                for task in tasks
            )
        }

    logger.info(
        "Running LLM drug review for %d ANZCTR rows with %d workers",
        len(tasks),
        llm_workers,
    )
    reviews: dict[int, DrugExtractionReview] = {}
    with ThreadPoolExecutor(max_workers=llm_workers) as executor:
        futures = {
            executor.submit(
                run_llm_review_task,
                task,
                llm_reviewer=llm_reviewer,
                llm_model=llm_model,
                llm_max_retries=llm_max_retries,
                llm_retry_initial_delay_seconds=llm_retry_initial_delay_seconds,
                llm_retry_max_delay_seconds=llm_retry_max_delay_seconds,
            ): task.row_index
            for task in tasks
        }
        for completed_count, future in enumerate(as_completed(futures), start=1):
            row_index, review = future.result()
            reviews[row_index] = review
            if completed_count % 10 == 0 or completed_count == len(tasks):
                logger.info(
                    "Completed %d/%d ANZCTR LLM drug reviews",
                    completed_count,
                    len(tasks),
                )
    return reviews


def append_drug_columns(
    frame: pd.DataFrame,
    *,
    lexicon: DrugMentionLexicon,
    conso_index: RxnConsoIndex,
    rel_index: RxnRelIndex | None,
    llm_review: bool = False,
    llm_model: str | None = None,
    llm_limit: int | None = None,
    llm_workers: int = 1,
    llm_max_retries: int = DrugOpenaiClient.MAX_RETRIES,
    llm_retry_initial_delay_seconds: float = (
        DrugOpenaiClient.RETRY_INITIAL_DELAY_SECONDS
    ),
    llm_retry_max_delay_seconds: float = DrugOpenaiClient.RETRY_MAX_DELAY_SECONDS,
    llm_reviewer: Callable[..., DrugExtractionReview] = llm_review_drug_extraction,
) -> pd.DataFrame:
    missing = [
        column
        for column in [INTERVENTIONS_COLUMN, SCIENTIFIC_TITLE_COLUMN]
        if column not in frame.columns
    ]
    if missing:
        raise ValueError(f"Input CSV is missing required columns: {', '.join(missing)}")

    out = frame.copy()
    canonical_cache: dict[str, str] = {}
    intervention_values: list[str] = []
    title_values: list[str] = []
    combined_values: list[str] = []
    llm_remove_values: list[str] = []
    llm_add_values: list[str] = []
    llm_correct_values: list[str] = []
    llm_reasoning_values: list[str] = []
    llm_review_tasks: list[LlmReviewTask] = []

    source_rows = zip(
        out[INTERVENTIONS_COLUMN].tolist(),
        out[SCIENTIFIC_TITLE_COLUMN].tolist(),
    )
    for row_index, (intervention_text, title_text) in enumerate(
        source_rows,
        start=1,
    ):
        intervention_drugs = extract_rxnorm_matched_drugs(
            intervention_text,
            lexicon,
            conso_index,
            rel_index,
            canonical_cache,
        )
        title_drugs = extract_rxnorm_matched_drugs(
            title_text,
            lexicon,
            conso_index,
            rel_index,
            canonical_cache,
        )
        combined_drugs = ordered_unique_drug_names([*intervention_drugs, *title_drugs])

        intervention_values.append(DISPLAY_DELIMITER.join(intervention_drugs))
        title_values.append(DISPLAY_DELIMITER.join(title_drugs))
        combined_values.append(DISPLAY_DELIMITER.join(combined_drugs))

        should_review = llm_review and (llm_limit is None or row_index <= llm_limit)
        if should_review:
            llm_review_tasks.append(
                LlmReviewTask(
                    row_index=row_index,
                    scientific_title=title_text,
                    interventions=intervention_text,
                    rxnorm_matched_drugs=tuple(combined_drugs),
                )
            )
        llm_remove_values.append("")
        llm_add_values.append("")
        llm_correct_values.append("")
        llm_reasoning_values.append("")

        if row_index % 50 == 0:
            logger.info("Processed %d ANZCTR intervention rows", row_index)

    llm_reviews = run_llm_reviews(
        llm_review_tasks,
        llm_reviewer=llm_reviewer,
        llm_model=llm_model,
        llm_workers=llm_workers,
        llm_max_retries=llm_max_retries,
        llm_retry_initial_delay_seconds=llm_retry_initial_delay_seconds,
        llm_retry_max_delay_seconds=llm_retry_max_delay_seconds,
    )
    for row_index, review in llm_reviews.items():
        (
            drugs_to_remove,
            drugs_to_add,
            drugs_to_correct,
        ) = review.as_csv_columns(
            list_delimiter=DISPLAY_DELIMITER,
            correction_delimiter=CORRECTION_DELIMITER,
        )
        output_index = row_index - 1
        llm_remove_values[output_index] = drugs_to_remove
        llm_add_values[output_index] = drugs_to_add
        llm_correct_values[output_index] = drugs_to_correct
        llm_reasoning_values[output_index] = review.summary_text

    out[RXNORM_MATCHED_INTERVENTIONS_COLUMN] = intervention_values
    out[RXNORM_MATCHED_TITLE_COLUMN] = title_values
    out[RXNORM_MATCHED_DRUGS_COLUMN] = combined_values
    out[LLM_DRUG_TO_REMOVE_COLUMN] = llm_remove_values
    out[LLM_DRUGS_TO_ADD_COLUMN] = llm_add_values
    out[LLM_DRUGS_TO_CORRECT_COLUMN] = llm_correct_values
    out[LLM_REASONING_COLUMN] = llm_reasoning_values
    return out


def ensure_input_csv(
    input_csv: Path,
    *,
    input_xlsx: Path = DEFAULT_INPUT_XLSX,
    create_input_csv: bool = False,
) -> Path:
    if input_csv.exists():
        return input_csv

    if not create_input_csv:
        raise FileNotFoundError(
            f"ANZCTR field extraction CSV does not exist: {input_csv}. "
            "Rerun this command with --create_input_csv or --refresh_input_csv."
        )

    logger.info(
        "ANZCTR field extraction CSV does not exist; creating %s from %s",
        input_csv,
        input_xlsx,
    )
    return extract_fields_to_csv(input_xlsx, input_csv)


def rxnorm_required_filenames(*, ingredient_resolution: bool = True) -> tuple[str, ...]:
    filenames = [RXNCONSO_FILENAME]
    if ingredient_resolution:
        filenames.append(RXNREL_FILENAME)
    return tuple(filenames)


def missing_rxnorm_rrf_files(
    rxnorm_rrf_dir: Path,
    *,
    ingredient_resolution: bool = True,
) -> tuple[str, ...]:
    return tuple(
        filename
        for filename in rxnorm_required_filenames(
            ingredient_resolution=ingredient_resolution
        )
        if not (rxnorm_rrf_dir / filename).is_file()
    )


def is_rxnorm_rrf_dir(
    rxnorm_rrf_dir: Path,
    *,
    ingredient_resolution: bool = True,
) -> bool:
    return not missing_rxnorm_rrf_files(
        rxnorm_rrf_dir,
        ingredient_resolution=ingredient_resolution,
    )


def rxnorm_version_sort_key(path: Path) -> tuple[int, str, str]:
    match = RXNORM_VERSION_DIR_RE.match(path.name)
    if not match:
        return (0, path.name, str(path))

    try:
        version_date = datetime.strptime(match.group(1), "%d%m%Y").date()
    except ValueError:
        return (0, path.name, str(path))
    return (1, version_date.isoformat(), str(path))


def candidate_rxnorm_version_dirs(
    rxnorm_rrf_root: Path,
    *,
    ingredient_resolution: bool = True,
) -> list[Path]:
    if not rxnorm_rrf_root.is_dir():
        return []

    return sorted(
        (
            path
            for path in rxnorm_rrf_root.iterdir()
            if path.is_dir()
            and path.name.startswith("version_")
            and is_rxnorm_rrf_dir(
                path,
                ingredient_resolution=ingredient_resolution,
            )
        ),
        key=rxnorm_version_sort_key,
    )


def resolve_rxnorm_rrf_dir(
    rxnorm_rrf_dir: str | Path,
    *,
    ingredient_resolution: bool = True,
) -> Path:
    """Resolve a concrete RxNorm RRF directory from a version dir or root dir."""

    requested_path = Path(rxnorm_rrf_dir)
    search_roots: list[Path] = [requested_path]
    for candidate in RXNORM_RRF_ROOT_CANDIDATES:
        if candidate not in search_roots:
            search_roots.append(candidate)

    checked: list[str] = []
    for search_root in search_roots:
        if is_rxnorm_rrf_dir(
            search_root,
            ingredient_resolution=ingredient_resolution,
        ):
            return search_root

        version_dirs = candidate_rxnorm_version_dirs(
            search_root,
            ingredient_resolution=ingredient_resolution,
        )
        if version_dirs:
            return version_dirs[-1]

        if search_root.exists():
            missing = missing_rxnorm_rrf_files(
                search_root,
                ingredient_resolution=ingredient_resolution,
            )
            if missing:
                checked.append(f"{search_root} (missing: {', '.join(missing)})")
            else:
                checked.append(f"{search_root} (no valid version_* subdirectory)")
        else:
            checked.append(f"{search_root} (does not exist)")

    required = ", ".join(
        rxnorm_required_filenames(ingredient_resolution=ingredient_resolution)
    )
    checked_text = "; ".join(checked)
    raise FileNotFoundError(
        "Could not resolve an RxNorm RRF directory. Provide either a directory "
        f"containing {required}, or a root containing valid version_* directories. "
        f"Checked: {checked_text}"
    )


def extract_drugs_to_csv(
    input_csv: str | Path,
    output_csv: str | Path,
    *,
    input_xlsx: str | Path = DEFAULT_INPUT_XLSX,
    create_input_csv: bool = False,
    refresh_input_csv: bool = False,
    rxnorm_rrf_dir: str | Path = DEFAULT_RXNORM_RRF_DIR,
    ingredient_resolution: bool = True,
    llm_review: bool = False,
    llm_model: str | None = None,
    llm_limit: int | None = None,
    llm_workers: int = 1,
    llm_max_retries: int = DrugOpenaiClient.MAX_RETRIES,
    llm_retry_initial_delay_seconds: float = (
        DrugOpenaiClient.RETRY_INITIAL_DELAY_SECONDS
    ),
    llm_retry_max_delay_seconds: float = DrugOpenaiClient.RETRY_MAX_DELAY_SECONDS,
) -> Path:
    input_csv = Path(input_csv)
    output_csv = Path(output_csv)
    input_xlsx = Path(input_xlsx)
    rxnorm_rrf_dir = Path(rxnorm_rrf_dir)

    existing_llm_reviews: pd.DataFrame | None = None
    if output_csv.exists() and not llm_review:
        existing_output = pd.read_csv(output_csv, dtype=str, keep_default_na=False)
        if "ACTRN" in existing_output.columns:
            existing_llm_columns = [
                column for column in LLM_REVIEW_COLUMNS if column in existing_output.columns
            ]
            if existing_llm_columns:
                existing_llm_reviews = (
                    existing_output[["ACTRN", *existing_llm_columns]]
                    .drop_duplicates(subset=["ACTRN"], keep="last")
                    .set_index("ACTRN")
                )
                logger.info(
                    "Will preserve %d existing ANZCTR LLM review column(s) from %s",
                    len(existing_llm_columns),
                    output_csv,
                )

    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    read_input_csv = input_csv
    should_build_input_csv = refresh_input_csv or (
        create_input_csv and not input_csv.exists()
    )
    if should_build_input_csv:
        if input_csv == output_csv:
            temp_dir = tempfile.TemporaryDirectory()
            read_input_csv = Path(temp_dir.name) / "anzctr_field_extractions_base.csv"
        logger.info(
            "Creating ANZCTR field extraction rows from %s",
            input_xlsx,
        )
        extract_fields_to_csv(input_xlsx, read_input_csv)
    else:
        ensure_input_csv(
            input_csv,
            input_xlsx=input_xlsx,
            create_input_csv=False,
        )

    rxnorm_rrf_dir = resolve_rxnorm_rrf_dir(
        rxnorm_rrf_dir,
        ingredient_resolution=ingredient_resolution,
    )

    logger.info("Loading ANZCTR field extractions from %s", read_input_csv)
    frame = pd.read_csv(read_input_csv)

    logger.info("Loading RxNorm concept index from %s", rxnorm_rrf_dir)
    conso_index = RxnConsoIndex.from_rrf_dir(rxnorm_rrf_dir)
    lexicon = DrugMentionLexicon.from_rxnorm_index(conso_index)

    rel_index: RxnRelIndex | None = None
    if ingredient_resolution:
        logger.info("Loading RxNorm relationship index from %s", rxnorm_rrf_dir)
        rel_index = RxnRelIndex.from_rrf_dir(rxnorm_rrf_dir)

    output = append_drug_columns(
        frame,
        lexicon=lexicon,
        conso_index=conso_index,
        rel_index=rel_index,
        llm_review=llm_review,
        llm_model=llm_model,
        llm_limit=llm_limit,
        llm_workers=llm_workers,
        llm_max_retries=llm_max_retries,
        llm_retry_initial_delay_seconds=llm_retry_initial_delay_seconds,
        llm_retry_max_delay_seconds=llm_retry_max_delay_seconds,
    )
    if existing_llm_reviews is not None and "ACTRN" in output.columns:
        output_indexed = output.set_index("ACTRN", drop=False)
        matching_trial_ids = output_indexed.index.intersection(existing_llm_reviews.index)
        for column in existing_llm_reviews.columns:
            output_indexed.loc[matching_trial_ids, column] = existing_llm_reviews.loc[
                matching_trial_ids,
                column,
            ]
        output = output_indexed.reset_index(drop=True)

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(output_csv, index=False)
    logger.info("Wrote %s", output_csv)
    if temp_dir is not None:
        temp_dir.cleanup()
    return output_csv


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract RxNorm-matched drugs from ANZCTR intervention text."
    )
    parser.add_argument(
        "--input_csv",
        type=Path,
        default=DEFAULT_INPUT_CSV,
        help=f"Input ANZCTR field extractions CSV. Default: {DEFAULT_INPUT_CSV}",
    )
    parser.add_argument(
        "--create_input_csv",
        action="store_true",
        help=(
            "Create ANZCTR field extraction rows from the raw workbook if "
            "--input_csv is missing."
        ),
    )
    parser.add_argument(
        "--refresh_input_csv",
        action="store_true",
        help=(
            "Recreate ANZCTR field extraction rows from the raw workbook even "
            "when --input_csv exists. Existing LLM review columns in "
            "--output_csv are preserved by ACTRN unless --llm_review is set."
        ),
    )
    parser.add_argument(
        "--input_xlsx",
        type=Path,
        default=DEFAULT_INPUT_XLSX,
        help=(
            "Raw ANZCTR workbook used with --create_input_csv or "
            "--refresh_input_csv. "
            f"Default: {DEFAULT_INPUT_XLSX}"
        ),
    )
    parser.add_argument(
        "--output_csv",
        type=Path,
        default=DEFAULT_OUTPUT_CSV,
        help=f"Output CSV. Default: {DEFAULT_OUTPUT_CSV}",
    )
    parser.add_argument(
        "--rxnorm_rrf_dir",
        type=Path,
        default=DEFAULT_RXNORM_RRF_DIR,
        help=(
            "RxNorm RRF directory, or a root directory containing version_* "
            f"subdirectories. Default: {DEFAULT_RXNORM_RRF_DIR}"
        ),
    )
    parser.add_argument(
        "--no_ingredient_resolution",
        action="store_true",
        help="Skip RXNREL ingredient resolution and output best matched RxNorm names.",
    )
    parser.add_argument(
        "--llm_review",
        action="store_true",
        help=(
            "Call OpenAI to populate LLM remove/add/correct drug review columns "
            "and llm_reasoning."
        ),
    )
    parser.add_argument(
        "--llm_model",
        default=None,
        help=f"OpenAI model for --llm_review. Default: {DrugOpenaiClient.MODEL}",
    )
    parser.add_argument(
        "--llm_limit",
        type=int,
        default=None,
        help="Maximum number of rows to send for --llm_review.",
    )
    parser.add_argument(
        "--llm_workers",
        type=int,
        default=1,
        help=(
            "Number of concurrent LLM review calls for --llm_review. "
            "Default: 1."
        ),
    )
    parser.add_argument(
        "--llm_max_retries",
        type=int,
        default=DrugOpenaiClient.MAX_RETRIES,
        help=(
            "Maximum retry attempts per LLM request after rate-limit/transient "
            f"errors. Default: {DrugOpenaiClient.MAX_RETRIES}."
        ),
    )
    parser.add_argument(
        "--llm_retry_initial_delay",
        type=float,
        default=DrugOpenaiClient.RETRY_INITIAL_DELAY_SECONDS,
        help=(
            "Initial retry delay in seconds when the API does not provide a "
            "retry hint. "
            f"Default: {DrugOpenaiClient.RETRY_INITIAL_DELAY_SECONDS}."
        ),
    )
    parser.add_argument(
        "--llm_retry_max_delay",
        type=float,
        default=DrugOpenaiClient.RETRY_MAX_DELAY_SECONDS,
        help=(
            "Maximum retry delay in seconds. "
            f"Default: {DrugOpenaiClient.RETRY_MAX_DELAY_SECONDS}."
        ),
    )
    parser.add_argument(
        "--log_level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging level.",
    )
    args = parser.parse_args()
    if args.llm_workers < 1:
        parser.error("--llm_workers must be at least 1")
    if args.llm_max_retries < 0:
        parser.error("--llm_max_retries must be non-negative")
    if args.llm_retry_initial_delay <= 0:
        parser.error("--llm_retry_initial_delay must be positive")
    if args.llm_retry_max_delay <= 0:
        parser.error("--llm_retry_max_delay must be positive")

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    extract_drugs_to_csv(
        args.input_csv,
        args.output_csv,
        input_xlsx=args.input_xlsx,
        create_input_csv=args.create_input_csv,
        refresh_input_csv=args.refresh_input_csv,
        rxnorm_rrf_dir=args.rxnorm_rrf_dir,
        ingredient_resolution=not args.no_ingredient_resolution,
        llm_review=args.llm_review,
        llm_model=args.llm_model,
        llm_limit=args.llm_limit,
        llm_workers=args.llm_workers,
        llm_max_retries=args.llm_max_retries,
        llm_retry_initial_delay_seconds=args.llm_retry_initial_delay,
        llm_retry_max_delay_seconds=args.llm_retry_max_delay,
    )


if __name__ == "__main__":
    main()
