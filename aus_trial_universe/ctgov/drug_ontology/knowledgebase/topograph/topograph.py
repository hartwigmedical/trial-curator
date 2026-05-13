from __future__ import annotations

"""Parser/normalizer for TOPOGRAPH-master.tsv.

Design notes
------------
TOPOGRAPH's ``Drugs`` column is semi-denormalized:

* semicolon (``;``) separates alternative therapy options;
* plus (``+``) separates components inside a single combination option.

Therefore this module emits three grains:

* raw assertion: one source TSV row;
* therapy option: one source row x one semicolon-split therapy option;
* therapy component: one therapy option x one plus-split component.

No CTGov linking is done here. CTGov linkage is expressed later in SQL views so
that the source-of-truth remains SQL tables/views and TSVs remain dumps only.
"""

import csv
import hashlib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

try:
    from aus_trial_universe.ctgov.drug_ontology.rxnorm.matcher import normalize_lookup_text
except Exception:  # pragma: no cover - fallback only for isolated parser use.
    normalize_lookup_text = None  # type: ignore[assignment]

REQUIRED_COLUMNS = ["Tier", "Biomarker", "Alteration", "Tumour Type", "Drugs", "Comments", "Evidence"]
SEMICOLON_SPLIT_RE = re.compile(r"\s*;\s*")
PLUS_SPLIT_RE = re.compile(r"\s*\+\s*")
WHITESPACE_RE = re.compile(r"\s+")
NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class TopographRawAssertion:
    raw_assertion_key: str
    topograph_raw_row_index: int
    tier: str
    biomarker: str
    alteration: str
    tumour_type: str
    drugs: str
    comments: str
    evidence: str
    evidence_direction: str
    source_version: str


@dataclass(frozen=True)
class TopographTherapyOption:
    therapy_option_key: str
    raw_assertion_key: str
    topograph_raw_row_index: int
    therapy_option_index: int
    therapy_option_raw: str
    therapy_option_norm: str
    therapy_option_type: str
    component_count: int
    source_version: str


@dataclass(frozen=True)
class TopographTherapyComponent:
    therapy_component_key: str
    therapy_option_key: str
    raw_assertion_key: str
    topograph_raw_row_index: int
    therapy_option_index: int
    component_index: int
    component_raw: str
    component_norm: str
    source_version: str


@dataclass(frozen=True)
class TopographRecords:
    raw_assertions: list[TopographRawAssertion]
    therapy_options: list[TopographTherapyOption]
    therapy_components: list[TopographTherapyComponent]


def clean_text(value: object | None) -> str:
    """Return stable one-line text suitable for DB storage and TSV export."""
    if value is None:
        return ""
    text = str(value).replace("\ufeff", "")
    text = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return WHITESPACE_RE.sub(" ", text).strip()


def normalize_topograph_text(value: object | None) -> str:
    """Normalize therapy text using the project RxNorm normalizer when present.

    The fallback intentionally stays conservative and exact-match oriented.
    """
    text = clean_text(value)
    if not text:
        return ""
    if normalize_lookup_text is not None:
        return clean_text(normalize_lookup_text(text))

    # Local fallback for parser unit tests if the full project matcher is not importable.
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = text.replace("&", " and ")
    text = NON_ALNUM_RE.sub(" ", text)
    return WHITESPACE_RE.sub(" ", text).strip()


def stable_key(*parts: object) -> str:
    """Make a deterministic compact key from version/row/option metadata."""
    payload = "\x1f".join(clean_text(part) for part in parts)
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
    return digest


def split_semicolon_values(value: str) -> list[str]:
    return [part for part in (clean_text(piece) for piece in SEMICOLON_SPLIT_RE.split(clean_text(value))) if part]


def split_plus_components(therapy_option: str) -> list[str]:
    return [part for part in (clean_text(piece) for piece in PLUS_SPLIT_RE.split(clean_text(therapy_option))) if part]


def evidence_direction_for_tier(tier: str) -> str:
    tier_clean = clean_text(tier).upper()
    if tier_clean in {"R1", "R2"}:
        return "resistance_or_lack_of_activity"
    if tier_clean:
        return "sensitivity_or_activity"
    return "unknown"


def therapy_option_type_for_components(components: Sequence[str]) -> str:
    return "combination" if len(components) > 1 else "monotherapy"


def read_topograph_master_tsv(input_tsv: Path, source_version: str) -> TopographRecords:
    """Read TOPOGRAPH-master.tsv and emit normalized raw/option/component records."""
    if not input_tsv.exists():
        raise FileNotFoundError(f"TOPOGRAPH TSV not found: {input_tsv}")
    if input_tsv.suffix.lower() != ".tsv":
        raise ValueError(f"Expected a .tsv file: {input_tsv}")

    raw_assertions: list[TopographRawAssertion] = []
    therapy_options: list[TopographTherapyOption] = []
    therapy_components: list[TopographTherapyComponent] = []

    with input_tsv.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames != REQUIRED_COLUMNS:
            raise ValueError(
                "Unexpected TOPOGRAPH columns. "
                f"Expected {REQUIRED_COLUMNS}; got {reader.fieldnames}"
            )

        for row_index, row in enumerate(reader, start=1):
            tier = clean_text(row.get("Tier"))
            biomarker = clean_text(row.get("Biomarker"))
            alteration = clean_text(row.get("Alteration"))
            tumour_type = clean_text(row.get("Tumour Type"))
            drugs = clean_text(row.get("Drugs"))
            comments = clean_text(row.get("Comments"))
            evidence = clean_text(row.get("Evidence"))

            if not drugs:
                # A TOPOGRAPH evidence assertion without therapy cannot link to CTGov drug interventions.
                continue

            raw_assertion_key = f"topograph:{source_version}:row:{row_index}"
            raw_assertions.append(
                TopographRawAssertion(
                    raw_assertion_key=raw_assertion_key,
                    topograph_raw_row_index=row_index,
                    tier=tier,
                    biomarker=biomarker,
                    alteration=alteration,
                    tumour_type=tumour_type,
                    drugs=drugs,
                    comments=comments,
                    evidence=evidence,
                    evidence_direction=evidence_direction_for_tier(tier),
                    source_version=source_version,
                )
            )

            for option_index, therapy_option_raw in enumerate(split_semicolon_values(drugs), start=1):
                components = split_plus_components(therapy_option_raw)
                therapy_option_key = f"topograph:{source_version}:row:{row_index}:option:{option_index}"
                therapy_options.append(
                    TopographTherapyOption(
                        therapy_option_key=therapy_option_key,
                        raw_assertion_key=raw_assertion_key,
                        topograph_raw_row_index=row_index,
                        therapy_option_index=option_index,
                        therapy_option_raw=therapy_option_raw,
                        therapy_option_norm=normalize_topograph_text(therapy_option_raw),
                        therapy_option_type=therapy_option_type_for_components(components),
                        component_count=len(components),
                        source_version=source_version,
                    )
                )

                for component_index, component_raw in enumerate(components, start=1):
                    therapy_components.append(
                        TopographTherapyComponent(
                            therapy_component_key=f"topograph:{source_version}:row:{row_index}:option:{option_index}:component:{component_index}",
                            therapy_option_key=therapy_option_key,
                            raw_assertion_key=raw_assertion_key,
                            topograph_raw_row_index=row_index,
                            therapy_option_index=option_index,
                            component_index=component_index,
                            component_raw=component_raw,
                            component_norm=normalize_topograph_text(component_raw),
                            source_version=source_version,
                        )
                    )

    return TopographRecords(
        raw_assertions=raw_assertions,
        therapy_options=therapy_options,
        therapy_components=therapy_components,
    )


def distinct_norms(values: Iterable[str]) -> set[str]:
    return {norm for norm in (normalize_topograph_text(value) for value in values) if norm}
