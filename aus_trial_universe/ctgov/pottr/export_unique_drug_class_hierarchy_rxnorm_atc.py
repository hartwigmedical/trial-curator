#!/usr/bin/env python3
"""Export canonical POTTR drug -> aliases -> POTTR classes -> RxNorm -> ATC.

Final output grain:
    one row per unique POTTR canonical drug name

Important implementation details:
    * POTTR aliases are aggregated into a separate pottr_drug_aliases column.
    * POTTR drug classes are reconstructed from drug_database.txt and split on
      semicolon only. Commas are valid inside POTTR class names, e.g.
      radioligand,LAT1/LAT2-targeting and ALK_inhibitor,third_generation.
    * POTTR hierarchy paths are reconstructed directly from
      drug_class_hierarchy.txt.
    * RxNorm and ATC annotations use ordered fallback lookup:
        1. try the canonical POTTR drug name first;
        2. if and only if that has no confident RxNorm match, try aliases in
           their POTTR order;
        3. stop at the first confident RxNorm match and use only that term for
           downstream RxNorm/ATC annotation;
        4. if no candidate term has a confident RxNorm match, emit a no-match
           RxNorm/ATC row.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sys
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence


DEFAULT_POTTR_RAW_DIR = Path("data/ctgov/drug_ontology/raw/POTTR/version_29052026")
DEFAULT_PROCESSED_DIR = Path("data/pottr_hartwig_version/processed")
DEFAULT_EXPORTS_DIR = Path("data/pottr_hartwig_version/exports")

DRUG_DATABASE_FILENAME = "drug_database.txt"
DRUG_CLASS_HIERARCHY_FILENAME = "drug_class_hierarchy.txt"
DEFAULT_ALIAS_ATC_FILENAME = "pottr_unique_drugs.atc_annotated.tsv"
DEFAULT_ALIAS_ATC_ESSENTIAL_FILENAME = "pottr_unique_drugs_atc_essential.tsv"
DEFAULT_OUTPUT_FILENAME = "pottr_unique_drug_class_hierarchy_rxnorm_atc.tsv"

SUMMARY_DELIMITER = " | "
MULTISPACE_RE = re.compile(r"\s+")
SUMMARY_SPLIT_RE = re.compile(r"\s+\|\s+")
DIRECT_CLASS_SPLIT_RE = re.compile(r"\s*;\s*")


OUTPUT_COLUMNS = [
    "pottr_canonical_drug_name",
    "pottr_drug_aliases",
    "pottr_direct_class_names",
    "pottr_class_hierarchy_paths",
    "rxnorm_lookup_term",
    "rxnorm_lookup_term_source",
    "rxnorm_lookup_terms_tried",
    "rxnorm_match_statuses",
    "rxnorm_match_stages",
    "manual_review_needed",
    "rxnorm_rxcuis",
    "rxnorm_canonical_names",
    "rxnorm_term_types",
    "rxnorm_ingredient_rxcuis",
    "rxnorm_ingredient_names",
    "rxnorm_ingredient_term_types",
    "ingredient_resolution_stages",
    "pottr_link_anchor_rxcuis",
    "pottr_link_anchor_names",
    "pottr_link_anchor_term_types",
    "pottr_link_anchor_strategies",
    "atc_match_statuses",
    "atc_codes",
    "atc_names",
    "atc_l1_codes",
    "atc_l1_names",
    "atc_l2_codes",
    "atc_l2_names",
    "atc_l3_codes",
    "atc_l3_names",
    "atc_l4_codes",
    "atc_l4_names",
    "atc_l5_codes",
    "atc_l5_names",
    "atc_bridge_applied",
    "atc_original_codes",
]


class PottrConceptRecord(dict):
    """Dictionary subclass used only for clearer type intent."""


@dataclass(frozen=True)
class LookupSelection:
    """Selected alias rows after canonical-first, then alias fallback lookup."""

    annotation_rows: Sequence[Mapping[str, object]]
    attempted_rows: Sequence[Mapping[str, object]]
    lookup_term: str
    lookup_term_source: str
    lookup_terms_tried: Sequence[str]
    rxnorm_matched: bool


def clean_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).replace("\ufeff", "")
    return MULTISPACE_RE.sub(" ", text).strip()


def normalize_key(value: object) -> str:
    return clean_text(value).casefold()


def stable_key(*parts: object, prefix: str = "") -> str:
    payload = "\x1f".join(clean_text(part) for part in parts)
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}{digest}" if prefix else digest


def ordered_unique(values: Iterable[object]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = clean_text(value)
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key not in seen:
            seen.add(key)
            out.append(cleaned)
    return out


def ordered_join(values: Iterable[object]) -> str:
    return SUMMARY_DELIMITER.join(ordered_unique(values))


def split_summary_values(value: object) -> list[str]:
    text = clean_text(value)
    if not text:
        return []
    return [clean_text(part) for part in SUMMARY_SPLIT_RE.split(text) if clean_text(part)]


def split_aliases(drug_raw: object) -> list[str]:
    return ordered_unique(part for part in clean_text(drug_raw).split("|") if clean_text(part))


def split_direct_classes(class_raw: object) -> list[str]:
    # POTTR class names commonly contain commas. Only semicolon separates
    # multiple direct class assignments in drug_database.txt.
    return ordered_unique(part for part in DIRECT_CLASS_SPLIT_RE.split(clean_text(class_raw)) if clean_text(part))


def true_false_any(values: Iterable[object]) -> str:
    cleaned = [clean_text(value).casefold() for value in values if clean_text(value)]
    if any(value in {"true", "1", "yes"} for value in cleaned):
        return "True"
    if any(value in {"false", "0", "no"} for value in cleaned):
        return "False"
    return ""


def first_present(row: Mapping[str, object], columns: Sequence[str]) -> str:
    for column in columns:
        value = clean_text(row.get(column, ""))
        if value:
            return value
    return ""


def values_from_columns(row: Mapping[str, object], columns: Sequence[str]) -> list[str]:
    values: list[str] = []
    for column in columns:
        raw = clean_text(row.get(column, ""))
        if not raw:
            continue
        values.extend(split_summary_values(raw))
    return values


def read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames is None:
            raise ValueError(f"Input file has no header row: {path}")
        return [dict(row) for row in reader]


def write_tsv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=OUTPUT_COLUMNS,
            delimiter="\t",
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def load_pottr_concepts(pottr_raw_dir: Path) -> list[PottrConceptRecord]:
    path = pottr_raw_dir / DRUG_DATABASE_FILENAME
    if not path.exists():
        raise FileNotFoundError(f"POTTR drug database not found: {path}")

    concepts: list[PottrConceptRecord] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames is None:
            raise ValueError(f"POTTR drug database has no header row: {path}")

        required = {"drug", "drug_class"}
        missing = required - set(reader.fieldnames)
        if missing:
            raise ValueError(f"POTTR drug database missing columns {sorted(missing)}: {path}")

        for row_index, row in enumerate(reader, start=1):
            drug_raw = clean_text(row.get("drug", ""))
            class_raw = clean_text(row.get("drug_class", ""))
            aliases = split_aliases(drug_raw)
            canonical = aliases[0] if aliases else drug_raw
            concept_key = stable_key(drug_raw, class_raw, prefix="pottr_concept_")
            concepts.append(
                PottrConceptRecord(
                    row_index=row_index,
                    concept_key=concept_key,
                    canonical_drug_name=canonical,
                    canonical_key=normalize_key(canonical),
                    aliases=aliases,
                    direct_classes=split_direct_classes(class_raw),
                    drug_raw=drug_raw,
                    class_raw=class_raw,
                )
            )

    return concepts


def group_concepts_by_canonical(concepts: Sequence[PottrConceptRecord]) -> OrderedDict[str, list[PottrConceptRecord]]:
    grouped: OrderedDict[str, list[PottrConceptRecord]] = OrderedDict()
    for concept in concepts:
        key = clean_text(concept.get("canonical_key", ""))
        if key:
            grouped.setdefault(key, []).append(concept)
    return grouped


def load_hierarchy_paths_by_class(pottr_raw_dir: Path) -> dict[str, list[str]]:
    path = pottr_raw_dir / DRUG_CLASS_HIERARCHY_FILENAME
    if not path.exists():
        raise FileNotFoundError(f"POTTR class hierarchy file not found: {path}")

    paths_by_class: dict[str, list[str]] = defaultdict(list)
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            parts = [clean_text(part) for part in line.split("\t")]
            parts = [part for part in parts if part]
            if not parts:
                continue

            path_text = " > ".join(parts)
            for class_name in parts:
                paths_by_class[class_name].append(path_text)

    return {key: ordered_unique(paths) for key, paths in paths_by_class.items()}


def hierarchy_paths_for_direct_classes(
    direct_classes: Sequence[str],
    hierarchy_paths_by_class: Mapping[str, Sequence[str]],
) -> list[str]:
    paths: list[str] = []
    for direct_class in direct_classes:
        paths.extend(hierarchy_paths_by_class.get(direct_class, []))
    return ordered_unique(paths)


def alias_atc_drug_name(row: Mapping[str, object]) -> str:
    return first_present(row, ["alias", "pottr_drug_name", "drug_name"])


def row_concept_keys(row: Mapping[str, object]) -> list[str]:
    return ordered_unique(
        key
        for column in ["concept_key", "pottr_concept_key", "pottr_concept_keys"]
        for key in split_summary_values(row.get(column, ""))
    )


def row_canonical_names(
    row: Mapping[str, object],
    concept_key_to_canonical: Mapping[str, str],
) -> list[str]:
    names: list[str] = []

    for concept_key in row_concept_keys(row):
        canonical = concept_key_to_canonical.get(concept_key, "")
        if canonical:
            names.append(canonical)

    for column in ["canonical_drug_name", "pottr_canonical_drug_name"]:
        names.extend(split_summary_values(row.get(column, "")))

    return ordered_unique(names)


def row_canonical_keys(
    row: Mapping[str, object],
    concept_key_to_canonical: Mapping[str, str],
) -> list[str]:
    return ordered_unique(normalize_key(name) for name in row_canonical_names(row, concept_key_to_canonical))


def group_alias_atc_rows_by_canonical_key(
    rows: Sequence[Mapping[str, object]],
    concept_key_to_canonical: Mapping[str, str],
) -> dict[str, list[Mapping[str, object]]]:
    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        for canonical_key in row_canonical_keys(row, concept_key_to_canonical):
            if canonical_key:
                grouped[canonical_key].append(row)
    return grouped


def row_has_confident_rxnorm_match(row: Mapping[str, object]) -> bool:
    return "MATCHED" in set(values_from_columns(row, ["rxnorm_match_status", "rxnorm_match_statuses"]))


def rows_have_confident_rxnorm_match(rows: Sequence[Mapping[str, object]]) -> bool:
    return any(row_has_confident_rxnorm_match(row) for row in rows)


def rows_for_lookup_term(
    rows: Sequence[Mapping[str, object]],
    lookup_term: str,
) -> list[Mapping[str, object]]:
    lookup_key = normalize_key(lookup_term)
    return [row for row in rows if normalize_key(alias_atc_drug_name(row)) == lookup_key]


def lookup_terms_for_concepts(canonical_concepts: Sequence[PottrConceptRecord]) -> list[str]:
    canonical_name = clean_text(canonical_concepts[0].get("canonical_drug_name", ""))
    aliases = ordered_unique(
        alias
        for concept in canonical_concepts
        for alias in concept.get("aliases", [])
    )
    noncanonical_aliases = [alias for alias in aliases if normalize_key(alias) != normalize_key(canonical_name)]
    return ordered_unique([canonical_name, *noncanonical_aliases])


def select_lookup_rows(
    canonical_concepts: Sequence[PottrConceptRecord],
    candidate_rows: Sequence[Mapping[str, object]],
) -> LookupSelection:
    canonical_name = clean_text(canonical_concepts[0].get("canonical_drug_name", ""))
    canonical_key = normalize_key(canonical_name)

    terms_tried: list[str] = []
    attempted_rows: list[Mapping[str, object]] = []

    for lookup_term in lookup_terms_for_concepts(canonical_concepts):
        if not lookup_term:
            continue

        terms_tried.append(lookup_term)
        term_rows = rows_for_lookup_term(candidate_rows, lookup_term)
        attempted_rows.extend(term_rows)

        if rows_have_confident_rxnorm_match(term_rows):
            annotation_rows = [row for row in term_rows if row_has_confident_rxnorm_match(row)]
            lookup_term_source = "canonical" if normalize_key(lookup_term) == canonical_key else "alias"
            return LookupSelection(
                annotation_rows=annotation_rows,
                attempted_rows=attempted_rows,
                lookup_term=lookup_term,
                lookup_term_source=lookup_term_source,
                lookup_terms_tried=terms_tried,
                rxnorm_matched=True,
            )

    return LookupSelection(
        annotation_rows=[],
        attempted_rows=attempted_rows,
        lookup_term="",
        lookup_term_source="no_rxnorm_match",
        lookup_terms_tried=terms_tried,
        rxnorm_matched=False,
    )


def collect(rows: Sequence[Mapping[str, object]], columns: Sequence[str]) -> list[str]:
    return [value for row in rows for value in values_from_columns(row, columns)]


def build_output_row(
    canonical_concepts: Sequence[PottrConceptRecord],
    lookup_selection: LookupSelection,
    hierarchy_paths_by_class: Mapping[str, Sequence[str]],
) -> dict[str, object]:
    canonical_name = clean_text(canonical_concepts[0].get("canonical_drug_name", ""))

    all_aliases = ordered_unique(
        alias
        for concept in canonical_concepts
        for alias in concept.get("aliases", [])
    )
    noncanonical_aliases = [alias for alias in all_aliases if normalize_key(alias) != normalize_key(canonical_name)]

    direct_classes = ordered_unique(
        direct_class
        for concept in canonical_concepts
        for direct_class in concept.get("direct_classes", [])
    )
    hierarchy_paths = hierarchy_paths_for_direct_classes(direct_classes, hierarchy_paths_by_class)

    annotation_rows = list(lookup_selection.annotation_rows)
    manual_review_rows = annotation_rows if annotation_rows else list(lookup_selection.attempted_rows)

    rxnorm_match_statuses = ordered_join(collect(annotation_rows, ["rxnorm_match_status", "rxnorm_match_statuses"]))
    atc_match_statuses = ordered_join(collect(annotation_rows, ["atc_match_status", "atc_match_statuses"]))

    if not lookup_selection.rxnorm_matched:
        rxnorm_match_statuses = "NO_MATCH"
        atc_match_statuses = "NO_CONFIDENT_RXNORM_MATCH"

    return {
        "pottr_canonical_drug_name": canonical_name,
        "pottr_drug_aliases": ordered_join(noncanonical_aliases),
        "pottr_direct_class_names": ordered_join(direct_classes),
        "pottr_class_hierarchy_paths": ordered_join(hierarchy_paths),
        "rxnorm_lookup_term": lookup_selection.lookup_term,
        "rxnorm_lookup_term_source": lookup_selection.lookup_term_source,
        "rxnorm_lookup_terms_tried": ordered_join(lookup_selection.lookup_terms_tried),
        "rxnorm_match_statuses": rxnorm_match_statuses,
        "rxnorm_match_stages": ordered_join(collect(annotation_rows, ["rxnorm_match_stage", "rxnorm_match_stages"])),
        "manual_review_needed": true_false_any(collect(manual_review_rows, ["manual_review_needed"])),
        "rxnorm_rxcuis": ordered_join(collect(annotation_rows, ["rxnorm_rxcui", "rxnorm_rxcuis"])),
        "rxnorm_canonical_names": ordered_join(collect(annotation_rows, ["rxnorm_canonical_name", "rxnorm_canonical_names"])),
        "rxnorm_term_types": ordered_join(collect(annotation_rows, ["rxnorm_term_type", "rxnorm_term_types"])),
        "rxnorm_ingredient_rxcuis": ordered_join(collect(annotation_rows, ["rxnorm_ingredient_rxcui", "rxnorm_ingredient_rxcuis"])),
        "rxnorm_ingredient_names": ordered_join(collect(annotation_rows, ["rxnorm_ingredient_name", "rxnorm_ingredient_names"])),
        "rxnorm_ingredient_term_types": ordered_join(collect(annotation_rows, ["rxnorm_ingredient_term_type", "rxnorm_ingredient_term_types"])),
        "ingredient_resolution_stages": ordered_join(
            collect(annotation_rows, ["rxnorm_ingredient_resolution_stage", "ingredient_resolution_stage", "ingredient_resolution_stages"])
        ),
        "pottr_link_anchor_rxcuis": ordered_join(collect(annotation_rows, ["pottr_link_anchor_rxcui", "pottr_link_anchor_rxcuis"])),
        "pottr_link_anchor_names": ordered_join(collect(annotation_rows, ["pottr_link_anchor_name", "pottr_link_anchor_names"])),
        "pottr_link_anchor_term_types": ordered_join(collect(annotation_rows, ["pottr_link_anchor_term_type", "pottr_link_anchor_term_types"])),
        "pottr_link_anchor_strategies": ordered_join(collect(annotation_rows, ["pottr_link_anchor_strategy", "pottr_link_anchor_strategies"])),
        "atc_match_statuses": atc_match_statuses,
        "atc_codes": ordered_join(collect(annotation_rows, ["atc_code", "atc_codes"])),
        "atc_names": ordered_join(collect(annotation_rows, ["atc_name", "atc_names"])),
        "atc_l1_codes": ordered_join(collect(annotation_rows, ["atc_l1_code", "atc_l1_codes"])),
        "atc_l1_names": ordered_join(collect(annotation_rows, ["atc_l1_name", "atc_l1_names"])),
        "atc_l2_codes": ordered_join(collect(annotation_rows, ["atc_l2_code", "atc_l2_codes"])),
        "atc_l2_names": ordered_join(collect(annotation_rows, ["atc_l2_name", "atc_l2_names"])),
        "atc_l3_codes": ordered_join(collect(annotation_rows, ["atc_l3_code", "atc_l3_codes"])),
        "atc_l3_names": ordered_join(collect(annotation_rows, ["atc_l3_name", "atc_l3_names"])),
        "atc_l4_codes": ordered_join(collect(annotation_rows, ["atc_l4_code", "atc_l4_codes"])),
        "atc_l4_names": ordered_join(collect(annotation_rows, ["atc_l4_name", "atc_l4_names"])),
        "atc_l5_codes": ordered_join(collect(annotation_rows, ["atc_l5_code", "atc_l5_codes"])),
        "atc_l5_names": ordered_join(collect(annotation_rows, ["atc_l5_name", "atc_l5_names"])),
        "atc_bridge_applied": true_false_any(collect(annotation_rows, ["atc_bridge_applied"])),
        "atc_original_codes": ordered_join(collect(annotation_rows, ["atc_original_code", "atc_original_codes"])),
    }


def find_default_input(processed_dir: Path, exports_dir: Path) -> Path:
    detailed = processed_dir / DEFAULT_ALIAS_ATC_FILENAME
    if detailed.exists():
        return detailed

    essential = exports_dir / DEFAULT_ALIAS_ATC_ESSENTIAL_FILENAME
    if essential.exists():
        return essential

    raise FileNotFoundError(
        "Could not find a POTTR alias/ATC input file. Tried:\n"
        f"  - {detailed}\n"
        f"  - {essential}\n"
        "Run build_pottr_drug_class_atc first, or pass --input-file explicitly."
    )


def build_canonical_drug_class_rxnorm_atc_export(
    *,
    input_file: Path,
    output_file: Path,
    pottr_raw_dir: Path,
) -> tuple[int, int, int, int]:
    concepts = load_pottr_concepts(pottr_raw_dir)
    concepts_by_canonical = group_concepts_by_canonical(concepts)
    concept_key_to_canonical = {
        clean_text(concept["concept_key"]): clean_text(concept["canonical_drug_name"])
        for concept in concepts
    }
    hierarchy_paths_by_class = load_hierarchy_paths_by_class(pottr_raw_dir)

    alias_atc_rows = read_tsv(input_file)
    alias_atc_rows_by_canonical_key = group_alias_atc_rows_by_canonical_key(alias_atc_rows, concept_key_to_canonical)

    output_rows = [
        build_output_row(
            canonical_concepts=canonical_concepts,
            lookup_selection=select_lookup_rows(
                canonical_concepts=canonical_concepts,
                candidate_rows=alias_atc_rows_by_canonical_key.get(canonical_key, []),
            ),
            hierarchy_paths_by_class=hierarchy_paths_by_class,
        )
        for canonical_key, canonical_concepts in concepts_by_canonical.items()
    ]
    write_tsv(output_file, output_rows)

    rows_with_rxnorm_ingredient = sum(1 for row in output_rows if clean_text(row.get("rxnorm_ingredient_rxcuis", "")))
    rows_with_matched_atc = sum(
        1
        for row in output_rows
        if "MATCHED_ATC" in set(split_summary_values(row.get("atc_match_statuses", "")))
        and clean_text(row.get("atc_codes", ""))
    )
    return len(alias_atc_rows), len(output_rows), rows_with_rxnorm_ingredient, rows_with_matched_atc


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create one POTTR export file at canonical POTTR drug grain, with "
            "aliases, corrected POTTR direct classes/hierarchy, and RxNorm/ATC "
            "annotation using canonical-first, then alias-fallback RxNorm lookup."
        )
    )
    parser.add_argument(
        "--pottr-raw-dir",
        "--pottr_raw_dir",
        dest="pottr_raw_dir",
        type=Path,
        default=DEFAULT_POTTR_RAW_DIR,
        help=f"Directory containing drug_database.txt and drug_class_hierarchy.txt. Default: {DEFAULT_POTTR_RAW_DIR}",
    )
    parser.add_argument(
        "--processed-dir",
        "--processed_dir",
        dest="processed_dir",
        type=Path,
        default=DEFAULT_PROCESSED_DIR,
        help=f"Processed POTTR directory. Default: {DEFAULT_PROCESSED_DIR}",
    )
    parser.add_argument(
        "--exports-dir",
        "--exports_dir",
        dest="exports_dir",
        type=Path,
        default=DEFAULT_EXPORTS_DIR,
        help=f"Export POTTR directory. Default: {DEFAULT_EXPORTS_DIR}",
    )
    parser.add_argument(
        "--input-file",
        "--input_file",
        dest="input_file",
        type=Path,
        default=None,
        help=(
            f"Input alias/ATC TSV. Defaults to <processed-dir>/{DEFAULT_ALIAS_ATC_FILENAME}, "
            f"falling back to <exports-dir>/{DEFAULT_ALIAS_ATC_ESSENTIAL_FILENAME}."
        ),
    )
    parser.add_argument(
        "--output-file",
        "--output_file",
        dest="output_file",
        type=Path,
        default=None,
        help=f"Output TSV. Default: <exports-dir>/{DEFAULT_OUTPUT_FILENAME}",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        input_file = args.input_file or find_default_input(args.processed_dir, args.exports_dir)
        output_file = args.output_file or (args.exports_dir / DEFAULT_OUTPUT_FILENAME)
        input_rows, output_rows, rows_with_rxnorm_ingredient, rows_with_matched_atc = (
            build_canonical_drug_class_rxnorm_atc_export(
                input_file=input_file,
                output_file=output_file,
                pottr_raw_dir=args.pottr_raw_dir,
            )
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Input file: {input_file}", file=sys.stderr)
    print(f"POTTR raw dir: {args.pottr_raw_dir}", file=sys.stderr)
    print(f"Output file: {output_file}", file=sys.stderr)
    print(f"Input alias/ATC rows: {input_rows}", file=sys.stderr)
    print(f"Canonical POTTR drug rows: {output_rows}", file=sys.stderr)
    print(f"Rows with selected RxNorm ingredient: {rows_with_rxnorm_ingredient}", file=sys.stderr)
    print(f"Rows with selected matched ATC: {rows_with_matched_atc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())