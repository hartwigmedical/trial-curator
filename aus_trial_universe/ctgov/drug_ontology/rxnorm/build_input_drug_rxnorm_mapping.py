#!/usr/bin/env python3
from __future__ import annotations

"""Build a source-level input-drug-name -> RxNorm mapping TSV.

Sources
-------
- CTGov
- Topograph
- POTTR

Output grain
------------
CTGov and Topograph: one row per unique input drug name per source.
POTTR: one row per merged POTTR alias/RxNorm concept cluster.

CTGov scope
-----------
CTGov terms are extracted only from drug-relevant intervention types:
DRUG, BIOLOGICAL, and COMBINATION_PRODUCT, as defined by the shared
TARGET_DRUG_INTERVENTION_TYPES configuration used by
filter_target_drug_interventions().

POTTR handling
--------------
For POTTR, the initial extraction grain is one row per POTTR canonical drug
name, with non-canonical POTTR aliases carried in pottr_aliases. RxNorm lookup
is attempted against the canonical name and every alias. POTTR records are then
merged transitively when their matched RxNorm concept RXCUIs overlap. This
handles cases where different POTTR canonical records are actually aliases for
the same investigational compound, but RxNorm has separate atoms/RXCUIs for
different source names.

Output columns
--------------
input_name
pottr_aliases
source
pottr_match_term
rxnorm_concept_id
rxnorm_preferred_concept_name
rxnorm_ingredient_id
rxnorm_ingredient_name

Notes
-----
- rxnorm_concept_id is the selected RxNorm RXCUI. For merged POTTR rows it may
  contain multiple DISPLAY_DELIMITER-separated RXCUIs.
- rxnorm_preferred_concept_name is the selected display/canonical name returned
  by the existing RxNorm matcher for that RXCUI. For merged POTTR rows it may
  contain multiple DISPLAY_DELIMITER-separated names in first-seen RXCUI order.
- pottr_match_term is populated only for POTTR rows, where successful matches
  may come from either the POTTR canonical name or any of its aliases. For
  merged POTTR rows it may contain multiple DISPLAY_DELIMITER-separated terms.
- If there is no confident RxNorm match, pottr_match_term,
  rxnorm_preferred_concept_name, rxnorm_concept_id, rxnorm_ingredient_id, and
  rxnorm_ingredient_name are left blank.
"""

import argparse
import csv
import logging
import re
import sys
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from aus_trial_universe.ctgov.drug_ontology.interventions.extract import (
    build_interventions_dataframe,
)
from aus_trial_universe.ctgov.drug_ontology.interventions.extract_ctgov_unique_drugs import (
    UNIQUE_DRUG_COLUMN,
    build_intervention_drug_link_dataframe,
    build_unique_drug_dataframe,
    filter_target_drug_interventions,
)
from aus_trial_universe.ctgov.drug_ontology.rxnorm.matcher import (
    IngredientResolution,
    RxnConsoIndex,
    RxnRelIndex,
    TermResolution,
    resolve_term,
)
from aus_trial_universe.ctgov.drug_ontology.classification.pottr.pottr_drug_classes import (
    DRUG_DATABASE_FILENAME,
    split_aliases as split_pottr_aliases,
)

logger = logging.getLogger(__name__)

DEFAULT_CTGOV_INPUT_JSON = Path("data/ctgov/drug_ontology/raw/ctgov_input.json")
DEFAULT_TOPOGRAPH_INPUT_TSV = Path("data/ctgov/drug_ontology/raw/Topograph_13052026/TOPOGRAPH-master.tsv")
DEFAULT_POTTR_RAW_DIR = Path("data/ctgov/drug_ontology/raw/POTTR/version_29052026")
DEFAULT_RXNORM_RRF_DIR = Path("data/ctgov/drug_ontology/raw/RxNorm_full_03022026")
DEFAULT_OUTPUT_TSV = Path("data/ctgov/drug_ontology/processed/input_drug_rxnorm_mapping.tsv")

DISPLAY_DELIMITER = " | "
TOPOGRAPH_DRUGS_COLUMN = "Drugs"

TOPOGRAPH_MISSING_DRUG_VALUES = {"", "na", "n/a", "nan", "none", "null", "."}
SEMICOLON_SPLIT_RE = re.compile(r"\s*;\s*")
PLUS_SPLIT_RE = re.compile(r"\s*\+\s*")
WHITESPACE_RE = re.compile(r"\s+")

OUTPUT_COLUMNS = [
    "input_name",
    "pottr_aliases",
    "source",
    "pottr_match_term",
    "rxnorm_concept_id",
    "rxnorm_preferred_concept_name",
    "rxnorm_ingredient_id",
    "rxnorm_ingredient_name",
]


@dataclass(frozen=True)
class SourceDrugTerm:
    input_name: str
    source: str
    pottr_aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class SelectedRxNormMatch:
    lookup_term: str
    term_resolution: TermResolution
    ingredient_resolution: IngredientResolution


@dataclass(frozen=True)
class ResolvedPottrSourceTerm:
    source_term: SourceDrugTerm
    lookup_terms: tuple[str, ...]
    matches: tuple[SelectedRxNormMatch, ...]
    first_term_resolution: TermResolution


class UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))
        self.rank = [0] * size

    def find(self, item: int) -> int:
        parent = self.parent[item]
        if parent != item:
            self.parent[item] = self.find(parent)
        return self.parent[item]

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return

        if self.rank[left_root] < self.rank[right_root]:
            left_root, right_root = right_root, left_root

        self.parent[right_root] = left_root
        if self.rank[left_root] == self.rank[right_root]:
            self.rank[left_root] += 1


def clean_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).replace("\ufeff", "")
    text = WHITESPACE_RE.sub(" ", text).strip()
    return "" if text.casefold() in {"", "nan", "none", "<na>"} else text


def normalize_key(value: object) -> str:
    return " ".join(clean_text(value).casefold().split())


def ordered_unique(values: Iterable[object]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    for value in values:
        text = clean_text(value)
        if not text:
            continue
        key = normalize_key(text)
        if key not in seen:
            seen.add(key)
            out.append(text)

    return out


def join_values(values: Iterable[object]) -> str:
    return DISPLAY_DELIMITER.join(ordered_unique(values))


def bool_text(value: bool) -> str:
    return "true" if value else "false"


def write_tsv(path: Path, rows: Sequence[Mapping[str, object]], fieldnames: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(fieldnames),
            delimiter="\t",
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def validate_inputs(
    *,
    ctgov_input_json: Path | None,
    topograph_input_tsv: Path | None,
    pottr_raw_dir: Path | None,
    rxnorm_rrf_dir: Path,
    include_ctgov: bool,
    include_topograph: bool,
    include_pottr: bool,
) -> None:
    required_files: list[Path] = [
        rxnorm_rrf_dir / "RXNCONSO.RRF",
        rxnorm_rrf_dir / "RXNREL.RRF",
    ]

    if include_ctgov:
        if ctgov_input_json is None:
            raise ValueError("include_ctgov=True but ctgov_input_json is None")
        required_files.append(ctgov_input_json)

    if include_topograph:
        if topograph_input_tsv is None:
            raise ValueError("include_topograph=True but topograph_input_tsv is None")
        required_files.append(topograph_input_tsv)

    if include_pottr:
        if pottr_raw_dir is None:
            raise ValueError("include_pottr=True but pottr_raw_dir is None")
        required_files.append(pottr_raw_dir / DRUG_DATABASE_FILENAME)

    missing = [path for path in required_files if not path.exists()]
    if missing:
        missing_text = "\n".join(f"  - {path}" for path in missing)
        raise FileNotFoundError(f"Required input files are missing:\n{missing_text}")

    for path in required_files:
        if path.exists() and not path.is_file():
            raise IsADirectoryError(f"Required input path is not a file: {path}")


def extract_ctgov_terms_from_raw(ctgov_input_json: Path, *, sort_terms: bool = False) -> list[SourceDrugTerm]:
    """Extract unique CTGov drug terms from drug-relevant interventions only.

    The filtering is delegated to filter_target_drug_interventions(), which keeps
    the shared configured target intervention types: DRUG, BIOLOGICAL, and
    COMBINATION_PRODUCT.
    """
    logger.info("Extracting CTGov terms from %s", ctgov_input_json)

    all_interventions = build_interventions_dataframe(ctgov_input_json)
    filtered_interventions = filter_target_drug_interventions(all_interventions)
    intervention_drug_links = build_intervention_drug_link_dataframe(filtered_interventions)
    unique_drugs = build_unique_drug_dataframe(
        intervention_drug_links,
        sort_unique_drugs=sort_terms,
    )

    out: list[SourceDrugTerm] = []
    seen: set[str] = set()

    for value in unique_drugs[UNIQUE_DRUG_COLUMN].tolist():
        term = clean_text(value)
        key = normalize_key(term)
        if term and key not in seen:
            seen.add(key)
            out.append(SourceDrugTerm(input_name=term, source="ctgov"))

    logger.info("Extracted %d unique CTGov drug terms", len(out))
    return out


def normalize_topograph_drug_name(value: object) -> str:
    text = unicodedata.normalize("NFKC", "" if value is None else str(value))
    text = text.replace("\u00a0", " ")
    text = WHITESPACE_RE.sub(" ", text).strip()

    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1].strip()

    return text


def is_missing_topograph_drug_value(value: object) -> bool:
    return normalize_topograph_drug_name(value).casefold() in TOPOGRAPH_MISSING_DRUG_VALUES


def split_topograph_drugs_cell(value: object) -> list[str]:
    text = normalize_topograph_drug_name(value)
    if is_missing_topograph_drug_value(text):
        return []

    terms: list[str] = []

    semicolon_groups = [
        normalize_topograph_drug_name(part)
        for part in SEMICOLON_SPLIT_RE.split(text)
        if normalize_topograph_drug_name(part)
    ]

    for group in semicolon_groups:
        components = [
            normalize_topograph_drug_name(part)
            for part in PLUS_SPLIT_RE.split(group)
            if normalize_topograph_drug_name(part)
        ]
        terms.extend(components)

    return ordered_unique(terms)


def extract_topograph_terms_from_raw(
    topograph_input_tsv: Path,
    *,
    drug_column: str = TOPOGRAPH_DRUGS_COLUMN,
    sort_terms: bool = False,
) -> list[SourceDrugTerm]:
    logger.info("Extracting Topograph terms from %s", topograph_input_tsv)

    unique_by_key: OrderedDict[str, str] = OrderedDict()

    with topograph_input_tsv.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames is None:
            raise ValueError(f"Topograph input has no header row: {topograph_input_tsv}")
        if drug_column not in reader.fieldnames:
            raise ValueError(
                f"Topograph drug column {drug_column!r} not found in {topograph_input_tsv}. "
                f"Available columns: {reader.fieldnames}"
            )

        for row in reader:
            for term in split_topograph_drugs_cell(row.get(drug_column, "")):
                key = normalize_key(term)
                if key and key not in unique_by_key:
                    unique_by_key[key] = term

    terms = list(unique_by_key.values())
    if sort_terms:
        terms = sorted(terms, key=normalize_key)

    out = [SourceDrugTerm(input_name=term, source="topograph") for term in terms]
    logger.info("Extracted %d unique Topograph drug terms", len(out))
    return out


def read_tsv_dicts(path: Path) -> Iterable[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames is None:
            raise ValueError(f"Input TSV has no header row: {path}")

        for row in reader:
            yield {clean_text(key): clean_text(value) for key, value in row.items() if key is not None}


def extract_pottr_terms_from_raw(pottr_raw_dir: Path, *, sort_terms: bool = False) -> list[SourceDrugTerm]:
    logger.info("Extracting POTTR terms from %s", pottr_raw_dir)

    path = pottr_raw_dir / DRUG_DATABASE_FILENAME
    grouped_aliases_by_canonical_key: OrderedDict[str, tuple[str, list[str]]] = OrderedDict()

    for row in read_tsv_dicts(path):
        drug_raw = clean_text(row.get("drug", ""))
        aliases = ordered_unique(split_pottr_aliases(drug_raw))
        if not aliases:
            continue

        canonical = aliases[0]
        canonical_key = normalize_key(canonical)
        noncanonical_aliases = [
            alias for alias in aliases if normalize_key(alias) != canonical_key
        ]

        if canonical_key not in grouped_aliases_by_canonical_key:
            grouped_aliases_by_canonical_key[canonical_key] = (canonical, [])

        _, existing_aliases = grouped_aliases_by_canonical_key[canonical_key]
        existing_aliases.extend(noncanonical_aliases)

    terms = [
        SourceDrugTerm(
            input_name=canonical,
            source="pottr",
            pottr_aliases=tuple(ordered_unique(aliases)),
        )
        for canonical, aliases in grouped_aliases_by_canonical_key.values()
    ]

    if sort_terms:
        terms = sorted(terms, key=lambda item: normalize_key(item.input_name))

    logger.info("Extracted %d unique POTTR canonical drug terms", len(terms))
    return terms


def load_rxnorm_indexes(rxnorm_rrf_dir: Path) -> tuple[RxnConsoIndex, RxnRelIndex]:
    logger.info("Loading RxNorm indexes from %s", rxnorm_rrf_dir)
    conso_index = RxnConsoIndex.from_rrf_dir(rxnorm_rrf_dir)
    rel_index = RxnRelIndex.from_rrf_dir(rxnorm_rrf_dir)
    logger.info("Loaded RxNorm indexes")
    return conso_index, rel_index


def resolve_lookup_term(
    lookup_term: str,
    *,
    conso_index: RxnConsoIndex,
    rel_index: RxnRelIndex,
    cache: dict[str, tuple[TermResolution, IngredientResolution]],
) -> tuple[TermResolution, IngredientResolution]:
    key = normalize_key(lookup_term)
    if key in cache:
        return cache[key]

    term_resolution = resolve_term(lookup_term, conso_index)
    ingredient_resolution = rel_index.resolve_ingredient(
        matched_rxcui=term_resolution.rxcui,
        conso_index=conso_index,
        source_term=lookup_term,
    )
    cache[key] = (term_resolution, ingredient_resolution)
    return cache[key]


def lookup_terms_for_source_term(source_term: SourceDrugTerm) -> list[str]:
    if source_term.source == "pottr":
        return ordered_unique([source_term.input_name, *source_term.pottr_aliases])
    return [source_term.input_name]


def select_rxnorm_match_for_source_term(
    source_term: SourceDrugTerm,
    *,
    conso_index: RxnConsoIndex,
    rel_index: RxnRelIndex,
    cache: dict[str, tuple[TermResolution, IngredientResolution]],
) -> SelectedRxNormMatch | None:
    """Return the first selected confident RxNorm match, or None.

    This is kept for CTGov and Topograph, whose behaviour remains one input
    term -> first confident RxNorm match. POTTR is handled by
    build_pottr_rows(), which evaluates every alias and then merges records by
    overlapping matched concept RXCUIs.
    """
    for lookup_term in lookup_terms_for_source_term(source_term):
        term_resolution, ingredient_resolution = resolve_lookup_term(
            lookup_term,
            conso_index=conso_index,
            rel_index=rel_index,
            cache=cache,
        )

        if term_resolution.match_status == "MATCHED":
            return SelectedRxNormMatch(
                lookup_term=lookup_term,
                term_resolution=term_resolution,
                ingredient_resolution=ingredient_resolution,
            )

    return None


def resolve_pottr_source_term(
    source_term: SourceDrugTerm,
    *,
    conso_index: RxnConsoIndex,
    rel_index: RxnRelIndex,
    cache: dict[str, tuple[TermResolution, IngredientResolution]],
) -> ResolvedPottrSourceTerm:
    """Resolve a POTTR canonical term plus all aliases without early stopping."""
    if source_term.source != "pottr":
        raise ValueError(
            "resolve_pottr_source_term() received non-POTTR source: "
            f"{source_term.source!r}"
        )

    lookup_terms = tuple(lookup_terms_for_source_term(source_term))
    first_term_resolution: TermResolution | None = None
    matches: list[SelectedRxNormMatch] = []

    for lookup_term in lookup_terms:
        term_resolution, ingredient_resolution = resolve_lookup_term(
            lookup_term,
            conso_index=conso_index,
            rel_index=rel_index,
            cache=cache,
        )

        if first_term_resolution is None:
            first_term_resolution = term_resolution

        if term_resolution.match_status != "MATCHED":
            continue

        if not clean_text(term_resolution.rxcui):
            continue

        matches.append(
            SelectedRxNormMatch(
                lookup_term=lookup_term,
                term_resolution=term_resolution,
                ingredient_resolution=ingredient_resolution,
            )
        )

    if first_term_resolution is None:
        raise ValueError(f"POTTR source term has no lookup terms: {source_term!r}")

    return ResolvedPottrSourceTerm(
        source_term=source_term,
        lookup_terms=lookup_terms,
        matches=tuple(matches),
        first_term_resolution=first_term_resolution,
    )


def row_for_pottr_resolved_group(group: Sequence[ResolvedPottrSourceTerm]) -> dict[str, object]:
    """Build one output row for a transitively merged POTTR group."""
    if not group:
        raise ValueError("Cannot build a POTTR output row from an empty group")

    source_terms = [resolved.source_term for resolved in group]
    input_names = ordered_unique(source_term.input_name for source_term in source_terms)
    input_name = input_names[0] if input_names else ""
    input_name_key = normalize_key(input_name)

    alias_values: list[str] = []
    # Preserve merged canonical names as aliases so the merge remains auditable.
    alias_values.extend(input_names[1:])
    for source_term in source_terms:
        alias_values.extend(source_term.pottr_aliases)

    pottr_aliases = join_values(
        alias for alias in alias_values if normalize_key(alias) != input_name_key
    )

    all_matches = [match for resolved in group for match in resolved.matches]
    unique_concept_matches: list[SelectedRxNormMatch] = []
    seen_rxcuis: set[str] = set()
    for match in all_matches:
        rxcui = clean_text(match.term_resolution.rxcui)
        if not rxcui or rxcui in seen_rxcuis:
            continue
        seen_rxcuis.add(rxcui)
        unique_concept_matches.append(match)

    if not unique_concept_matches:
        first_resolution = group[0].first_term_resolution
        return {
            "input_name": input_name,
            "pottr_aliases": pottr_aliases,
            "source": "pottr",
            "pottr_match_term": "",
            "rxnorm_preferred_concept_name": "",
            "rxnorm_concept_id": "",
            "rxnorm_ingredient_id": "",
            "rxnorm_ingredient_name": "",
            "rxnorm_match_status": first_resolution.match_status,
            "rxnorm_match_stage": first_resolution.match_stage,
            "manual_review_needed": "true",
        }

    concept_ids = [clean_text(match.term_resolution.rxcui) for match in unique_concept_matches]
    concept_names = [
        clean_text(match.term_resolution.canonical_name)
        for match in unique_concept_matches
    ]
    ingredient_ids = [
        clean_text(match.ingredient_resolution.ingredient_rxcui)
        for match in unique_concept_matches
    ]
    ingredient_names = [
        clean_text(match.ingredient_resolution.ingredient_name)
        for match in unique_concept_matches
    ]

    manual_review_needed = bool(
        len(ordered_unique(concept_ids)) > 1
        or any(match.term_resolution.manual_review_needed for match in unique_concept_matches)
        or any(match.term_resolution.match_status != "MATCHED" for match in unique_concept_matches)
        or any(
            not clean_text(match.ingredient_resolution.ingredient_rxcui)
            for match in unique_concept_matches
        )
    )

    return {
        "input_name": input_name,
        "pottr_aliases": pottr_aliases,
        "source": "pottr",
        "pottr_match_term": join_values(match.lookup_term for match in all_matches),
        "rxnorm_concept_id": join_values(concept_ids),
        "rxnorm_preferred_concept_name": join_values(concept_names),
        "rxnorm_ingredient_id": join_values(ingredient_ids),
        "rxnorm_ingredient_name": join_values(ingredient_names),
        "rxnorm_match_status": "MATCHED",
        "rxnorm_match_stage": join_values(
            match.term_resolution.match_stage for match in all_matches
        ),
        "manual_review_needed": bool_text(manual_review_needed),
    }


def build_pottr_rows(
    pottr_terms: Sequence[SourceDrugTerm],
    *,
    conso_index: RxnConsoIndex,
    rel_index: RxnRelIndex,
    cache: dict[str, tuple[TermResolution, IngredientResolution]],
) -> list[dict[str, object]]:
    """Resolve all POTTR aliases, then merge rows that share matched RXCUIs.

    The merge is transitive. For example, if A shares an RXCUI with B and B
    shares another RXCUI with C, A/B/C are emitted as one POTTR output row.
    """
    resolved_terms = [
        resolve_pottr_source_term(
            source_term,
            conso_index=conso_index,
            rel_index=rel_index,
            cache=cache,
        )
        for source_term in pottr_terms
    ]

    union_find = UnionFind(len(resolved_terms))
    indices_by_rxcui: OrderedDict[str, list[int]] = OrderedDict()

    for index, resolved in enumerate(resolved_terms):
        for match in resolved.matches:
            rxcui = clean_text(match.term_resolution.rxcui)
            if rxcui:
                indices_by_rxcui.setdefault(rxcui, []).append(index)

    for indices in indices_by_rxcui.values():
        if len(indices) < 2:
            continue
        first_index = indices[0]
        for other_index in indices[1:]:
            union_find.union(first_index, other_index)

    grouped_indices: OrderedDict[int, list[int]] = OrderedDict()
    for index in range(len(resolved_terms)):
        root = union_find.find(index)
        grouped_indices.setdefault(root, []).append(index)

    return [
        row_for_pottr_resolved_group([resolved_terms[index] for index in indices])
        for indices in grouped_indices.values()
    ]


def row_for_source_term(
    source_term: SourceDrugTerm,
    selected_match: SelectedRxNormMatch | None,
    *,
    conso_index: RxnConsoIndex,
    rel_index: RxnRelIndex,
    cache: dict[str, tuple[TermResolution, IngredientResolution]],
) -> dict[str, object]:
    pottr_aliases = join_values(source_term.pottr_aliases) if source_term.source == "pottr" else ""

    if selected_match is None:
        # Resolve the first attempted term only for audit status/stage, while
        # leaving RxNorm ID/name columns blank as requested.
        first_lookup_term = source_term.input_name
        term_resolution, _ingredient_resolution = resolve_lookup_term(
            first_lookup_term,
            conso_index=conso_index,
            rel_index=rel_index,
            cache=cache,
        )

        return {
            "input_name": source_term.input_name,
            "pottr_aliases": pottr_aliases,
            "source": source_term.source,
            "pottr_match_term": "",
            "rxnorm_preferred_concept_name": "",
            "rxnorm_concept_id": "",
            "rxnorm_ingredient_id": "",
            "rxnorm_ingredient_name": "",
            "rxnorm_match_status": term_resolution.match_status,
            "rxnorm_match_stage": term_resolution.match_stage,
            "manual_review_needed": "true",
        }

    term_resolution = selected_match.term_resolution
    ingredient_resolution = selected_match.ingredient_resolution

    manual_review_needed = bool(
        term_resolution.manual_review_needed
        or term_resolution.match_status != "MATCHED"
        or not clean_text(ingredient_resolution.ingredient_rxcui)
    )

    return {
        "input_name": source_term.input_name,
        "pottr_aliases": pottr_aliases,
        "source": source_term.source,
        "pottr_match_term": selected_match.lookup_term if source_term.source == "pottr" else "",
        "rxnorm_concept_id": term_resolution.rxcui,
        "rxnorm_preferred_concept_name": term_resolution.canonical_name,
        "rxnorm_ingredient_id": ingredient_resolution.ingredient_rxcui,
        "rxnorm_ingredient_name": ingredient_resolution.ingredient_name,
        "rxnorm_match_status": term_resolution.match_status,
        "rxnorm_match_stage": term_resolution.match_stage,
        "manual_review_needed": bool_text(manual_review_needed),
    }


def build_input_drug_rxnorm_mapping(
    *,
    ctgov_input_json: Path | None,
    topograph_input_tsv: Path | None,
    pottr_raw_dir: Path | None,
    rxnorm_rrf_dir: Path,
    output_tsv: Path,
    include_ctgov: bool = True,
    include_topograph: bool = True,
    include_pottr: bool = True,
    topograph_drug_column: str = TOPOGRAPH_DRUGS_COLUMN,
    sort_terms: bool = False,
) -> int:
    validate_inputs(
        ctgov_input_json=ctgov_input_json,
        topograph_input_tsv=topograph_input_tsv,
        pottr_raw_dir=pottr_raw_dir,
        rxnorm_rrf_dir=rxnorm_rrf_dir,
        include_ctgov=include_ctgov,
        include_topograph=include_topograph,
        include_pottr=include_pottr,
    )

    source_terms: list[SourceDrugTerm] = []

    if include_ctgov:
        assert ctgov_input_json is not None
        source_terms.extend(
            extract_ctgov_terms_from_raw(
                ctgov_input_json,
                sort_terms=sort_terms,
            )
        )

    if include_topograph:
        assert topograph_input_tsv is not None
        source_terms.extend(
            extract_topograph_terms_from_raw(
                topograph_input_tsv,
                drug_column=topograph_drug_column,
                sort_terms=sort_terms,
            )
        )

    if include_pottr:
        assert pottr_raw_dir is not None
        source_terms.extend(
            extract_pottr_terms_from_raw(
                pottr_raw_dir,
                sort_terms=sort_terms,
            )
        )

    conso_index, rel_index = load_rxnorm_indexes(rxnorm_rrf_dir)
    resolution_cache: dict[str, tuple[TermResolution, IngredientResolution]] = {}

    rows: list[dict[str, object]] = []
    non_pottr_terms = [source_term for source_term in source_terms if source_term.source != "pottr"]
    pottr_terms = [source_term for source_term in source_terms if source_term.source == "pottr"]

    for index, source_term in enumerate(non_pottr_terms, start=1):
        if index % 1000 == 0:
            logger.info(
                "Mapped %d/%d non-POTTR input drug rows",
                index,
                len(non_pottr_terms),
            )

        selected_match = select_rxnorm_match_for_source_term(
            source_term,
            conso_index=conso_index,
            rel_index=rel_index,
            cache=resolution_cache,
        )
        rows.append(
            row_for_source_term(
                source_term,
                selected_match,
                conso_index=conso_index,
                rel_index=rel_index,
                cache=resolution_cache,
            )
        )

    if pottr_terms:
        logger.info(
            "Mapping and merging %d POTTR canonical input drug rows",
            len(pottr_terms),
        )
        pottr_rows = build_pottr_rows(
            pottr_terms,
            conso_index=conso_index,
            rel_index=rel_index,
            cache=resolution_cache,
        )
        logger.info(
            "Collapsed %d POTTR canonical input drug rows into %d POTTR output rows",
            len(pottr_terms),
            len(pottr_rows),
        )
        rows.extend(pottr_rows)

    write_tsv(output_tsv, rows, OUTPUT_COLUMNS)
    return len(rows)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build one TSV mapping CTGov, Topograph, and POTTR input drug names "
            "to RxNorm concept IDs and preferred concept names."
        )
    )
    parser.add_argument(
        "--ctgov-input-json",
        "--ctgov_input_json",
        dest="ctgov_input_json",
        type=Path,
        default=DEFAULT_CTGOV_INPUT_JSON,
        help=f"Raw CTGov JSON/NDJSON input. Default: {DEFAULT_CTGOV_INPUT_JSON}",
    )
    parser.add_argument(
        "--topograph-input-tsv",
        "--topograph_input_tsv",
        dest="topograph_input_tsv",
        type=Path,
        default=DEFAULT_TOPOGRAPH_INPUT_TSV,
        help=f"Raw Topograph TSV. Default: {DEFAULT_TOPOGRAPH_INPUT_TSV}",
    )
    parser.add_argument(
        "--topograph-drug-column",
        "--topograph_drug_column",
        dest="topograph_drug_column",
        default=TOPOGRAPH_DRUGS_COLUMN,
        help=f"Topograph drug column. Default: {TOPOGRAPH_DRUGS_COLUMN}",
    )
    parser.add_argument(
        "--pottr-raw-dir",
        "--pottr_raw_dir",
        dest="pottr_raw_dir",
        type=Path,
        default=DEFAULT_POTTR_RAW_DIR,
        help=f"Raw POTTR directory containing {DRUG_DATABASE_FILENAME}. Default: {DEFAULT_POTTR_RAW_DIR}",
    )
    parser.add_argument(
        "--rxnorm-rrf-dir",
        "--rxnorm_rrf_dir",
        dest="rxnorm_rrf_dir",
        type=Path,
        default=DEFAULT_RXNORM_RRF_DIR,
        help=f"RxNorm RRF directory. Default: {DEFAULT_RXNORM_RRF_DIR}",
    )
    parser.add_argument(
        "--output-tsv",
        "--output_tsv",
        dest="output_tsv",
        type=Path,
        default=DEFAULT_OUTPUT_TSV,
        help=f"Output mapping TSV. Default: {DEFAULT_OUTPUT_TSV}",
    )
    parser.add_argument(
        "--no-ctgov",
        dest="no_ctgov",
        action="store_true",
        help="Exclude CTGov source.",
    )
    parser.add_argument(
        "--no-topograph",
        dest="no_topograph",
        action="store_true",
        help="Exclude Topograph source.",
    )
    parser.add_argument(
        "--no-pottr",
        dest="no_pottr",
        action="store_true",
        help="Exclude POTTR source.",
    )
    parser.add_argument(
        "--sort",
        action="store_true",
        help="Sort terms within each source instead of preserving first-seen order.",
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

    include_ctgov = not args.no_ctgov
    include_topograph = not args.no_topograph
    include_pottr = not args.no_pottr

    if not (include_ctgov or include_topograph or include_pottr):
        print("ERROR: At least one source must be included.", file=sys.stderr)
        return 1

    try:
        output_rows = build_input_drug_rxnorm_mapping(
            ctgov_input_json=args.ctgov_input_json,
            topograph_input_tsv=args.topograph_input_tsv,
            pottr_raw_dir=args.pottr_raw_dir,
            rxnorm_rrf_dir=args.rxnorm_rrf_dir,
            output_tsv=args.output_tsv,
            include_ctgov=include_ctgov,
            include_topograph=include_topograph,
            include_pottr=include_pottr,
            topograph_drug_column=args.topograph_drug_column,
            sort_terms=args.sort,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote input drug RxNorm mapping rows: {output_rows}", file=sys.stderr)
    print(f"Output TSV: {args.output_tsv}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
