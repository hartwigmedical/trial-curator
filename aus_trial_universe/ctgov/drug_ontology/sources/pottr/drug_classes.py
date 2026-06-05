from __future__ import annotations

"""POTTR drug-class parsing and RxNorm/POTTR anchoring logic.

This module is deliberately database-agnostic. It parses POTTR source files,
resolves POTTR aliases to RxNorm where possible, and derives conservative
POTTR link anchors. Database loading belongs in load_pottr_mappings_to_postgres.py.
"""

import csv
import hashlib
import json
import logging
import re
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence

from aus_trial_universe.ctgov.drug_ontology.identity.rxnorm.matcher import (
    IngredientResolution,
    RxnConsoIndex,
    RxnRelIndex,
    TermResolution,
    normalize_lookup_text,
    resolve_term,
)

logger = logging.getLogger(__name__)

DRUG_DATABASE_FILENAME = "drug_database.txt"
DRUG_CLASS_HIERARCHY_FILENAME = "drug_class_hierarchy.txt"
TSV_DELIMITER = "\t"
SUMMARY_DELIMITER = " | "
TOKEN_RE = re.compile(r"[a-z0-9]+")
CLASS_SPLIT_RE = re.compile(r"\s*[,;]\s*")

# Anchor policy carried over from the FDA v6 lessons:
# - simple salts/forms should group to the broad ingredient;
# - biosimilar suffixes should group to the reference biologic;
# - ADCs/radioligands/conjugates should preserve the payload/conjugate core.
PROPER_NAME_PREFIXES = {"ado", "fam"}
BIOLOGIC_SUFFIX_RE = re.compile(r"-[a-z]{4}$", re.IGNORECASE)
SIMPLE_FORM_MODIFIER_TOKENS = {
    "hydrochloride", "hcl", "hydrobromide", "sulfate", "sulphate", "mesylate", "mesilate",
    "tosylate", "besylate", "fumarate", "succinate", "acetate", "phosphate", "diphosphate",
    "nitrate", "citrate", "tartrate", "bitartrate", "maleate", "malate", "calcium", "sodium",
    "potassium", "magnesium", "zinc", "chloride", "bromide", "iodide", "carbonate",
    "bicarbonate", "monohydrate", "dihydrate", "heptahydrate", "anhydrous", "hydrate",
    "base", "recombinant", "pegol", "alpha", "alfa", "beta", "gamma", "delta",
}
NON_COLLAPSIBLE_ACTIVE_TOKENS = {
    "emtansine", "deruxtecan", "vedotin", "govitecan", "mafodotin", "tesirine",
    "ozogamicin", "soravtansine", "ravtansine", "mertansine", "duocarmazine",
    "pyrrolobenzodiazepine", "pbd", "maytansine", "exatecan", "auristatin",
    "vipivotide", "tetraxetan", "lutetium", "lu", "iobenguane", "dotatate",
    "radioconjugate", "radioligand",
}
ANCHOR_TTYS = {"IN", "MIN", "PIN"}
ANCHOR_TTY_PRIORITY = {"IN": 0, "MIN": 1, "PIN": 2}


@dataclass(frozen=True)
class PottrDrugConcept:
    concept_key: str
    concept_index: int
    drug_raw: str
    canonical_drug_name: str
    aliases_raw: str
    direct_classes_raw: str


@dataclass(frozen=True)
class PottrLinkAnchor:
    link_anchor_rxcui: str
    link_anchor_name: str
    link_anchor_term_type: str
    link_anchor_strategy: str
    link_anchor_path: str
    link_anchor_manual_review_needed: bool


@dataclass(frozen=True)
class PottrDrugAlias:
    concept_key: str
    alias_position: int
    alias: str
    alias_normalized: str
    rxnorm_rxcui: str
    rxnorm_canonical_name: str
    rxnorm_term_type: str
    rxnorm_match_stage: str
    rxnorm_match_status: str
    rxnorm_ingredient_rxcui: str
    rxnorm_ingredient_name: str
    rxnorm_ingredient_term_type: str
    rxnorm_ingredient_resolution_stage: str
    rxnorm_ingredient_path: str
    pottr_link_anchor_rxcui: str
    pottr_link_anchor_name: str
    pottr_link_anchor_term_type: str
    pottr_link_anchor_strategy: str
    pottr_link_anchor_path: str
    manual_review_needed: bool
    resolution_payload: str


@dataclass(frozen=True)
class PottrDrugClassAssignment:
    assignment_key: str
    concept_key: str
    direct_class_name: str
    pottr_class_name: str
    class_relation: str
    class_depth_from_direct: int
    class_path: str
    class_in_hierarchy: bool


@dataclass(frozen=True)
class PottrBuildResult:
    concepts: list[PottrDrugConcept]
    aliases: list[PottrDrugAlias]
    class_assignments: list[PottrDrugClassAssignment]


def clean_text(value: object) -> str:
    if value is None:
        return ""
    text_value = str(value).strip()
    return "" if text_value.lower() in {"", "nan", "none", "<na>"} else text_value


def normalize_alias_key(value: object) -> str:
    return normalize_lookup_text(value)


def stable_key(*parts: object, prefix: str = "") -> str:
    payload = "\x1f".join(clean_text(part) for part in parts)
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}{digest}" if prefix else digest


def ordered_unique(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = clean_text(value)
        key = normalize_lookup_text(cleaned)
        if cleaned and key not in seen:
            seen.add(key)
            out.append(cleaned)
    return out


def split_aliases(drug_raw: object) -> list[str]:
    return ordered_unique(clean_text(part) for part in clean_text(drug_raw).split("|"))


def split_direct_classes(class_raw: object) -> list[str]:
    # POTTR uses commas for many multi-class rows and semicolons for many multi-target
    # rows. Do not split on slash or plus, because those often encode targets/regimens.
    return ordered_unique(part for part in CLASS_SPLIT_RE.split(clean_text(class_raw)) if clean_text(part))


def read_tsv_dicts(path: Path) -> Iterable[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Required POTTR file not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=TSV_DELIMITER)
        if reader.fieldnames is None:
            raise ValueError(f"Could not read header from {path}")
        for row in reader:
            yield {clean_text(k): clean_text(v) for k, v in row.items() if k is not None}


class PottrClassHierarchy:
    def __init__(self, roots: Sequence[str], edges: Sequence[tuple[str, str]]) -> None:
        self.roots = set(clean_text(root) for root in roots if clean_text(root))
        self.edges = [(clean_text(parent), clean_text(child)) for parent, child in edges if clean_text(parent) and clean_text(child)]
        self.parents: dict[str, set[str]] = defaultdict(set)
        self.children: dict[str, set[str]] = defaultdict(set)
        self.classes: set[str] = set(self.roots)
        for parent, child in self.edges:
            self.parents[child].add(parent)
            self.children[parent].add(child)
            self.classes.add(parent)
            self.classes.add(child)

    @classmethod
    def from_txt(cls, path: Path) -> "PottrClassHierarchy":
        if not path.exists():
            raise FileNotFoundError(f"Required POTTR hierarchy file not found: {path}")
        roots: list[str] = []
        edges: list[tuple[str, str]] = []
        with path.open("r", encoding="utf-8-sig") as handle:
            for line in handle:
                line = line.rstrip("\n")
                if not line.strip():
                    continue
                parts = [clean_text(part) for part in line.split("\t")]
                parts = [part for part in parts if part]
                if len(parts) == 1:
                    roots.append(parts[0])
                elif len(parts) >= 2:
                    edges.append((parts[0], parts[1]))
        logger.info("Loaded %d POTTR hierarchy roots and %d parent-child edges", len(roots), len(edges))
        return cls(roots, edges)

    def ancestor_assignments(self, direct_class: str, max_depth: int = 20) -> list[tuple[str, str, int, str, bool]]:
        """Return class assignments as (class_name, relation, depth, path, in_hierarchy)."""
        direct_class = clean_text(direct_class)
        if not direct_class:
            return []

        out: list[tuple[str, str, int, str, bool]] = []
        seen: set[tuple[str, int, str]] = set()
        in_hierarchy = direct_class in self.classes
        out.append((direct_class, "DIRECT", 0, direct_class, in_hierarchy))

        queue: deque[tuple[str, list[str]]] = deque([(direct_class, [direct_class])])
        visited_for_node = {direct_class}
        while queue:
            current, path_from_direct = queue.popleft()
            if len(path_from_direct) - 1 >= max_depth:
                continue
            for parent in sorted(self.parents.get(current, [])):
                # Allow a parent reachable through different paths, but prevent loops in a path.
                if parent in path_from_direct:
                    continue
                next_path = [*path_from_direct, parent]
                path_root_to_direct = " > ".join(reversed(next_path))
                key = (parent, len(next_path) - 1, path_root_to_direct)
                if key not in seen:
                    seen.add(key)
                    out.append((parent, "ANCESTOR", len(next_path) - 1, path_root_to_direct, True))
                if parent not in visited_for_node:
                    visited_for_node.add(parent)
                    queue.append((parent, next_path))

        return out


def tokens_for_anchor(value: object) -> set[str]:
    return set(TOKEN_RE.findall(normalize_lookup_text(value)))


def remove_proper_prefix_and_suffix(value: str) -> str:
    text = normalize_lookup_text(value)
    if not text:
        return ""
    first_piece, sep, rest = text.partition("-")
    if sep and first_piece in PROPER_NAME_PREFIXES and rest:
        text = rest
    text = BIOLOGIC_SUFFIX_RE.sub("", text)
    return clean_text(text)


def candidate_anchor_variants(value: object) -> list[str]:
    text = normalize_lookup_text(value)
    stripped = remove_proper_prefix_and_suffix(text)
    variants = [stripped, text] if stripped and stripped != text else [text]
    tokens = TOKEN_RE.findall(stripped or text)
    if tokens:
        filtered = [token for token in tokens if token not in SIMPLE_FORM_MODIFIER_TOKENS]
        if filtered and filtered != tokens:
            variants.append(" ".join(filtered))
    return ordered_unique(variants)


def choose_exact_anchor_row(term: str, conso_index: RxnConsoIndex, allowed_ttys: set[str] = ANCHOR_TTYS):
    scored = []
    for row in conso_index.exact_lookup(term):
        best = conso_index.best_row_for_rxcui(row.rxcui)
        if best is None or best.tty not in allowed_ttys:
            continue
        scored.append(
            (
                ANCHOR_TTY_PRIORITY.get(best.tty, 99),
                0 if best.sab == "RXNORM" else 1,
                0 if best.suppress != "O" else 1,
                normalize_lookup_text(best.str_value),
                best.rxcui,
                best,
            )
        )
    if not scored:
        return None
    return min(scored, key=lambda item: item[:5])[-1]


def has_proper_name_prefix(value: object) -> bool:
    text = normalize_lookup_text(value)
    first_piece, sep, _ = text.partition("-")
    return bool(sep and first_piece in PROPER_NAME_PREFIXES)


def has_biologic_suffix(value: object) -> bool:
    return bool(BIOLOGIC_SUFFIX_RE.search(normalize_lookup_text(value)))


def is_single_token_biosimilar_name(value: object) -> bool:
    text = normalize_lookup_text(value)
    if not has_biologic_suffix(text):
        return False
    core = BIOLOGIC_SUFFIX_RE.sub("", text)
    return bool(core) and " " not in core and "-" not in core


def has_non_collapsible_active_modifier(value: object) -> bool:
    return bool(tokens_for_anchor(value) & NON_COLLAPSIBLE_ACTIVE_TOKENS)


def broad_ingredient_is_compatible_simple_form(canonical_name: str, ingredient_name: str) -> bool:
    canonical_tokens = tokens_for_anchor(canonical_name)
    ingredient_tokens = tokens_for_anchor(ingredient_name)
    if not canonical_tokens or not ingredient_tokens:
        return False
    if not ingredient_tokens.issubset(canonical_tokens):
        return False
    remainder = canonical_tokens - ingredient_tokens
    return bool(remainder) and remainder.issubset(SIMPLE_FORM_MODIFIER_TOKENS)


def derive_pottr_link_anchor_from_values(
    *,
    source_text: str,
    matched_rxcui: str,
    canonical_name: str,
    canonical_tty: str,
    ingredient_rxcui: str,
    ingredient_name: str,
    ingredient_tty: str,
    conso_index: RxnConsoIndex,
) -> PottrLinkAnchor:
    matched_rxcui = clean_text(matched_rxcui)
    canonical_name = clean_text(canonical_name)
    canonical_tty = clean_text(canonical_tty)
    ingredient_rxcui = clean_text(ingredient_rxcui)
    ingredient_name = clean_text(ingredient_name)
    ingredient_tty = clean_text(ingredient_tty)
    source_text = clean_text(source_text)

    if not matched_rxcui:
        return PottrLinkAnchor("", "", "", "NO_MATCHED_RXCUI", "", True)

    if canonical_tty in {"IN", "MIN"}:
        return PottrLinkAnchor(matched_rxcui, canonical_name, canonical_tty, "MATCHED_RXCUI_IS_BROAD_INGREDIENT", matched_rxcui, False)

    if canonical_tty == "BN" and ingredient_rxcui:
        return PottrLinkAnchor(ingredient_rxcui, ingredient_name, ingredient_tty, "BRAND_TO_RXNORM_INGREDIENT", f"{matched_rxcui} -> {ingredient_rxcui}", False)

    precise_name = canonical_name or source_text
    should_preserve_precise = (
        has_proper_name_prefix(precise_name)
        or (has_biologic_suffix(precise_name) and " " in remove_proper_prefix_and_suffix(precise_name))
        or has_non_collapsible_active_modifier(precise_name)
        or has_non_collapsible_active_modifier(source_text)
    )
    if canonical_tty == "PIN" and should_preserve_precise:
        for variant in candidate_anchor_variants(precise_name):
            row = choose_exact_anchor_row(variant, conso_index, allowed_ttys={"PIN", "IN", "MIN"})
            if row is not None and row.rxcui != ingredient_rxcui:
                return PottrLinkAnchor(
                    row.rxcui,
                    row.str_value,
                    row.tty,
                    "PRECISE_ACTIVE_CORE_EXACT",
                    f"{matched_rxcui} -> exact({variant}) -> {row.rxcui}",
                    False,
                )
        return PottrLinkAnchor(matched_rxcui, canonical_name, canonical_tty, "PRECISE_ACTIVE_MATCHED_RXCUI", matched_rxcui, False)

    if canonical_tty == "PIN" and is_single_token_biosimilar_name(precise_name) and ingredient_rxcui:
        return PottrLinkAnchor(ingredient_rxcui, ingredient_name, ingredient_tty, "BIOSIMILAR_SUFFIX_TO_INGREDIENT", f"{matched_rxcui} -> {ingredient_rxcui}", False)

    for variant in candidate_anchor_variants(precise_name):
        row = choose_exact_anchor_row(variant, conso_index, allowed_ttys={"IN", "MIN"})
        if row is not None and row.rxcui != matched_rxcui:
            return PottrLinkAnchor(
                row.rxcui,
                row.str_value,
                row.tty,
                "SIMPLE_FORM_TO_BROAD_INGREDIENT_EXACT",
                f"{matched_rxcui} -> exact({variant}) -> {row.rxcui}",
                False,
            )

    if ingredient_rxcui and ingredient_tty in {"IN", "MIN"} and broad_ingredient_is_compatible_simple_form(precise_name, ingredient_name):
        return PottrLinkAnchor(ingredient_rxcui, ingredient_name, ingredient_tty, "SIMPLE_FORM_TO_BROAD_INGREDIENT_GRAPH", f"{matched_rxcui} -> {ingredient_rxcui}", False)

    if ingredient_rxcui and ingredient_tty in {"IN", "MIN"} and canonical_tty not in {"PIN"}:
        return PottrLinkAnchor(ingredient_rxcui, ingredient_name, ingredient_tty, "NON_PIN_TO_RXNORM_INGREDIENT", f"{matched_rxcui} -> {ingredient_rxcui}", False)

    return PottrLinkAnchor(matched_rxcui, canonical_name, canonical_tty, "MATCHED_RXCUI_PRECISE_FALLBACK", matched_rxcui, canonical_tty not in ANCHOR_TTYS)


def derive_pottr_link_anchor(
    source_text: str,
    term_resolution: TermResolution,
    ingredient_resolution: IngredientResolution,
    conso_index: RxnConsoIndex,
) -> PottrLinkAnchor:
    return derive_pottr_link_anchor_from_values(
        source_text=source_text,
        matched_rxcui=term_resolution.rxcui,
        canonical_name=term_resolution.canonical_name,
        canonical_tty=term_resolution.canonical_tty,
        ingredient_rxcui=ingredient_resolution.ingredient_rxcui,
        ingredient_name=ingredient_resolution.ingredient_name,
        ingredient_tty=ingredient_resolution.ingredient_tty,
        conso_index=conso_index,
    )


def resolve_pottr_alias_to_rxnorm(
    alias: str,
    conso_index: RxnConsoIndex,
    rel_index: RxnRelIndex,
) -> tuple[TermResolution, IngredientResolution, PottrLinkAnchor]:
    term_resolution = resolve_term(alias, conso_index)
    ingredient_resolution = rel_index.resolve_ingredient(
        matched_rxcui=term_resolution.rxcui,
        conso_index=conso_index,
        source_term=alias,
    )
    link_anchor = derive_pottr_link_anchor(alias, term_resolution, ingredient_resolution, conso_index)
    return term_resolution, ingredient_resolution, link_anchor


def pottr_alias_manual_review_needed(
    term_resolution: TermResolution,
    ingredient_resolution: IngredientResolution,
    link_anchor: PottrLinkAnchor,
) -> bool:
    if term_resolution.manual_review_needed:
        return True
    if term_resolution.match_status != "MATCHED":
        return True
    if not ingredient_resolution.ingredient_rxcui:
        return True
    if not link_anchor.link_anchor_rxcui:
        return True
    if link_anchor.link_anchor_manual_review_needed:
        return True
    return False


def alias_resolution_payload(
    alias: str,
    term_resolution: TermResolution,
    ingredient_resolution: IngredientResolution,
    link_anchor: PottrLinkAnchor,
) -> str:
    return json.dumps(
        {
            "alias": alias,
            "term_resolution": asdict(term_resolution),
            "ingredient_resolution": asdict(ingredient_resolution),
            "pottr_link_anchor": asdict(link_anchor),
        },
        ensure_ascii=False,
    )


def build_pottr_records(
    pottr_raw_dir: Path,
    conso_index: RxnConsoIndex,
    rel_index: RxnRelIndex,
) -> PottrBuildResult:
    hierarchy = PottrClassHierarchy.from_txt(pottr_raw_dir / DRUG_CLASS_HIERARCHY_FILENAME)
    resolution_cache: dict[str, tuple[TermResolution, IngredientResolution, PottrLinkAnchor]] = {}

    concepts: list[PottrDrugConcept] = []
    aliases_out: list[PottrDrugAlias] = []
    class_assignments: list[PottrDrugClassAssignment] = []

    for concept_index, row in enumerate(read_tsv_dicts(pottr_raw_dir / DRUG_DATABASE_FILENAME), start=1):
        drug_raw = clean_text(row.get("drug", ""))
        class_raw = clean_text(row.get("drug_class", ""))
        aliases = split_aliases(drug_raw)
        direct_classes = split_direct_classes(class_raw)
        canonical_alias = aliases[0] if aliases else drug_raw
        concept_key = stable_key(drug_raw, class_raw, prefix="pottr_concept_")

        concepts.append(
            PottrDrugConcept(
                concept_key=concept_key,
                concept_index=concept_index,
                drug_raw=drug_raw,
                canonical_drug_name=canonical_alias,
                aliases_raw="|".join(aliases),
                direct_classes_raw=",".join(direct_classes),
            )
        )

        for alias_position, alias in enumerate(aliases, start=1):
            alias_norm = normalize_alias_key(alias)
            if alias_norm not in resolution_cache:
                resolution_cache[alias_norm] = resolve_pottr_alias_to_rxnorm(alias, conso_index, rel_index)
            term_resolution, ingredient_resolution, link_anchor = resolution_cache[alias_norm]
            aliases_out.append(
                PottrDrugAlias(
                    concept_key=concept_key,
                    alias_position=alias_position,
                    alias=alias,
                    alias_normalized=alias_norm,
                    rxnorm_rxcui=term_resolution.rxcui,
                    rxnorm_canonical_name=term_resolution.canonical_name,
                    rxnorm_term_type=term_resolution.canonical_tty,
                    rxnorm_match_stage=term_resolution.match_stage,
                    rxnorm_match_status=term_resolution.match_status,
                    rxnorm_ingredient_rxcui=ingredient_resolution.ingredient_rxcui,
                    rxnorm_ingredient_name=ingredient_resolution.ingredient_name,
                    rxnorm_ingredient_term_type=ingredient_resolution.ingredient_tty,
                    rxnorm_ingredient_resolution_stage=ingredient_resolution.ingredient_resolution_stage,
                    rxnorm_ingredient_path=ingredient_resolution.ingredient_path,
                    pottr_link_anchor_rxcui=link_anchor.link_anchor_rxcui,
                    pottr_link_anchor_name=link_anchor.link_anchor_name,
                    pottr_link_anchor_term_type=link_anchor.link_anchor_term_type,
                    pottr_link_anchor_strategy=link_anchor.link_anchor_strategy,
                    pottr_link_anchor_path=link_anchor.link_anchor_path,
                    manual_review_needed=pottr_alias_manual_review_needed(term_resolution, ingredient_resolution, link_anchor),
                    resolution_payload=alias_resolution_payload(alias, term_resolution, ingredient_resolution, link_anchor),
                )
            )

        seen_assignments: set[str] = set()
        for direct_class in direct_classes:
            for class_name, relation, depth, path, in_hierarchy in hierarchy.ancestor_assignments(direct_class):
                assignment_key = stable_key(concept_key, direct_class, class_name, relation, depth, path, prefix="pottr_class_assignment_")
                if assignment_key in seen_assignments:
                    continue
                seen_assignments.add(assignment_key)
                class_assignments.append(
                    PottrDrugClassAssignment(
                        assignment_key=assignment_key,
                        concept_key=concept_key,
                        direct_class_name=direct_class,
                        pottr_class_name=class_name,
                        class_relation=relation,
                        class_depth_from_direct=depth,
                        class_path=path,
                        class_in_hierarchy=in_hierarchy,
                    )
                )

    logger.info(
        "Built %d POTTR concepts, %d aliases, and %d class assignment rows",
        len(concepts),
        len(aliases_out),
        len(class_assignments),
    )
    return PottrBuildResult(concepts=concepts, aliases=aliases_out, class_assignments=class_assignments)


def summarize_values(values: Iterable[str], limit: int = 12) -> str:
    unique = ordered_unique(values)
    if len(unique) <= limit:
        return SUMMARY_DELIMITER.join(unique)
    return SUMMARY_DELIMITER.join(unique[:limit]) + f" | ... (+{len(unique) - limit} more)"
