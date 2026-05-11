from __future__ import annotations

"""Database-agnostic FDA Drugs@FDA product parsing and RxNorm resolution.

This module intentionally preserves FDA product/application provenance.
It does not collapse combination products into delimiter-joined strings.
"""

import csv
import json
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Sequence

from aus_trial_universe.ctgov.drug_ontology.rxnorm.matcher import (
    IngredientResolution,
    RxnConsoIndex,
    RxnRelIndex,
    TermResolution,
    normalize_lookup_text,
    resolve_term,
)

logger = logging.getLogger(__name__)

FDA_ENCODING = "cp1252"
TSV_DELIMITER = "\t"
FEDERAL_REGISTER_NOTE_RE = re.compile(
    r"\s*\*\*\s*Federal Register determination that product was not discontinued or withdrawn "
    r"for safety or effectiveness reasons\s*\*\*\s*",
    re.IGNORECASE,
)
MULTISPACE_RE = re.compile(r"\s+")
TOKEN_RE = re.compile(r"[a-z0-9]+")

# FDA/RxNorm active-ingredient anchoring policy:
# - simple salts/forms should link to the broad active ingredient (e.g. erlotinib hydrochloride -> erlotinib)
# - biosimilar four-letter suffixes should link to the reference biologic ingredient
#   (e.g. trastuzumab-anns -> trastuzumab)
# - ADCs / radioligands / biologic conjugates should NOT collapse to the parent antibody
#   (e.g. fam-trastuzumab deruxtecan-nxki -> trastuzumab deruxtecan, not trastuzumab).
FDA_PROPER_NAME_PREFIXES = {"ado", "fam"}
FDA_BIOLOGIC_SUFFIX_RE = re.compile(r"-[a-z]{4}$", re.IGNORECASE)
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
}
INGREDIENT_ANCHOR_TTYS = {"IN", "MIN", "PIN"}
INGREDIENT_ANCHOR_TTY_PRIORITY = {"IN": 0, "MIN": 1, "PIN": 2}

PRODUCTS_FILENAME = "Products.txt"
APPLICATIONS_FILENAME = "Applications.txt"
MARKETING_STATUS_FILENAME = "MarketingStatus.txt"
MARKETING_STATUS_LOOKUP_FILENAME = "MarketingStatus_Lookup.txt"
SUBMISSIONS_FILENAME = "Submissions.txt"


@dataclass(frozen=True)
class FdaProductRow:
    appl_no: str
    product_no: str
    form: str
    strength: str
    reference_drug: str
    drug_name: str
    active_ingredient: str
    reference_standard: str


@dataclass(frozen=True)
class FdaApplicationRow:
    appl_no: str
    appl_type: str
    appl_public_notes: str
    sponsor_name: str


@dataclass(frozen=True)
class FdaMarketingStatusRow:
    marketing_status_id: str
    appl_no: str
    product_no: str


@dataclass(frozen=True)
class FdaMarketingStatusLookupRow:
    marketing_status_id: str
    marketing_status_description: str


@dataclass(frozen=True)
class FdaSubmissionSummary:
    original_submission_status: str
    original_submission_status_date: str
    latest_submission_status: str
    latest_submission_status_date: str
    latest_approved_submission_status_date: str
    has_approved_submission: bool


@dataclass(frozen=True)
class FdaLinkAnchor:
    link_anchor_rxcui: str
    link_anchor_name: str
    link_anchor_term_type: str
    link_anchor_strategy: str
    link_anchor_path: str
    link_anchor_manual_review_needed: bool


@dataclass(frozen=True)
class FdaProductIngredientComponent:
    appl_no: str
    product_no: str
    ingredient_position: int
    ingredient_count: int
    active_ingredient_raw: str
    active_ingredient_component: str
    active_ingredient_component_normalized: str
    strength_raw: str
    strength_component: str
    strength_parse_status: str
    component_parse_status: str
    drug_name: str
    form: str
    reference_drug: str
    reference_standard: str
    appl_type: str
    sponsor_name: str
    appl_public_notes: str
    marketing_status_id: str
    marketing_status_description: str
    original_submission_status: str
    original_submission_status_date: str
    latest_submission_status: str
    latest_submission_status_date: str
    latest_approved_submission_status_date: str
    has_approved_submission: bool
    fda_rxnorm_rxcui: str
    fda_rxnorm_canonical_name: str
    fda_rxnorm_term_type: str
    fda_rxnorm_ingredient_rxcui: str
    fda_rxnorm_ingredient_name: str
    fda_rxnorm_ingredient_term_type: str
    fda_rxnorm_match_stage: str
    fda_rxnorm_match_status: str
    fda_rxnorm_ingredient_resolution_stage: str
    fda_rxnorm_ingredient_path: str
    fda_link_anchor_rxcui: str
    fda_link_anchor_name: str
    fda_link_anchor_term_type: str
    fda_link_anchor_strategy: str
    fda_link_anchor_path: str
    fda_link_anchor_manual_review_needed: bool
    fda_manual_review_needed: bool
    resolution_payload: str


def clean_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "<na>", "null"}:
        return ""
    return MULTISPACE_RE.sub(" ", text)


def clean_strength_text(value: object) -> str:
    return clean_text(FEDERAL_REGISTER_NOTE_RE.sub(" ", "" if value is None else str(value)))


def normalize_fda_ingredient_text(value: object) -> str:
    return normalize_lookup_text(clean_text(value))


def read_tsv_dicts(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Required FDA source file not found: {path}")

    with path.open("r", encoding=FDA_ENCODING, newline="") as handle:
        reader = csv.DictReader(handle, delimiter=TSV_DELIMITER)
        if reader.fieldnames is None:
            raise ValueError(f"Could not read header from FDA source file: {path}")
        return [{key: clean_text(value) for key, value in row.items()} for row in reader]


def split_top_level_semicolons(value: object) -> list[str]:
    """Split on semicolons that are not inside parentheses.

    FDA ActiveIngredient can contain historical grouped strings such as
    "TRIPLE SULFA (SULFABENZAMIDE;SULFACETAMIDE;SULFATHIAZOLE)". Splitting
    those internal semicolons would create false component pairings, so this
    function only splits top-level semicolons.
    """
    text = clean_text(value)
    if not text:
        return []

    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    for char in text:
        if char == "(":
            depth += 1
            buf.append(char)
        elif char == ")":
            depth = max(0, depth - 1)
            buf.append(char)
        elif char == ";" and depth == 0:
            part = clean_text("".join(buf))
            if part:
                parts.append(part)
            buf = []
        else:
            buf.append(char)

    part = clean_text("".join(buf))
    if part:
        parts.append(part)
    return parts


def component_parse_status(active_ingredient_raw: str, components: Sequence[str]) -> str:
    if not clean_text(active_ingredient_raw):
        return "BLANK_ACTIVE_INGREDIENT"
    if ";" in active_ingredient_raw and len(components) == 1:
        return "NESTED_SEMICOLON_NOT_SPLIT"
    return "OK"


def strength_component_for_position(strength_raw: str, ingredient_count: int, position: int) -> tuple[str, str]:
    strength_raw = clean_strength_text(strength_raw)
    if not strength_raw:
        return "", "BLANK_STRENGTH"

    strength_parts = split_top_level_semicolons(strength_raw)
    if ingredient_count == 1:
        return strength_raw, "SINGLE_INGREDIENT_RAW_STRENGTH"

    if len(strength_parts) == ingredient_count:
        return strength_parts[position - 1], "POSITIONAL_STRENGTH_MATCH"

    return "", f"STRENGTH_COMPONENT_COUNT_MISMATCH_{len(strength_parts)}_VS_{ingredient_count}"


def parse_products(path: Path) -> list[FdaProductRow]:
    rows = []
    for row in read_tsv_dicts(path):
        rows.append(
            FdaProductRow(
                appl_no=row.get("ApplNo", ""),
                product_no=row.get("ProductNo", ""),
                form=row.get("Form", ""),
                strength=row.get("Strength", ""),
                reference_drug=row.get("ReferenceDrug", ""),
                drug_name=row.get("DrugName", ""),
                active_ingredient=row.get("ActiveIngredient", ""),
                reference_standard=row.get("ReferenceStandard", ""),
            )
        )
    return rows


def parse_applications(path: Path) -> dict[str, FdaApplicationRow]:
    out: dict[str, FdaApplicationRow] = {}
    for row in read_tsv_dicts(path):
        appl_no = row.get("ApplNo", "")
        if not appl_no:
            continue
        out[appl_no] = FdaApplicationRow(
            appl_no=appl_no,
            appl_type=row.get("ApplType", ""),
            appl_public_notes=row.get("ApplPublicNotes", ""),
            sponsor_name=row.get("SponsorName", ""),
        )
    return out


def parse_marketing_status(path: Path) -> dict[tuple[str, str], FdaMarketingStatusRow]:
    out: dict[tuple[str, str], FdaMarketingStatusRow] = {}
    for row in read_tsv_dicts(path):
        appl_no = row.get("ApplNo", "")
        product_no = row.get("ProductNo", "")
        if not appl_no or not product_no:
            continue
        out[(appl_no, product_no)] = FdaMarketingStatusRow(
            marketing_status_id=row.get("MarketingStatusID", ""),
            appl_no=appl_no,
            product_no=product_no,
        )
    return out


def parse_marketing_status_lookup(path: Path) -> dict[str, FdaMarketingStatusLookupRow]:
    out: dict[str, FdaMarketingStatusLookupRow] = {}
    for row in read_tsv_dicts(path):
        status_id = row.get("MarketingStatusID", "")
        if not status_id:
            continue
        out[status_id] = FdaMarketingStatusLookupRow(
            marketing_status_id=status_id,
            marketing_status_description=row.get("MarketingStatusDescription", ""),
        )
    return out


def parse_fda_date(value: object) -> str:
    text = clean_text(value)
    if not text:
        return ""
    return text[:10]


def parse_submission_summaries(path: Path) -> dict[str, FdaSubmissionSummary]:
    by_appl: dict[str, list[dict[str, str]]] = {}
    for row in read_tsv_dicts(path):
        appl_no = row.get("ApplNo", "")
        if not appl_no:
            continue
        by_appl.setdefault(appl_no, []).append(row)

    summaries: dict[str, FdaSubmissionSummary] = {}
    for appl_no, rows in by_appl.items():
        def date_key(row: Mapping[str, str]) -> str:
            return parse_fda_date(row.get("SubmissionStatusDate", ""))

        orig_rows = [row for row in rows if clean_text(row.get("SubmissionType", "")).upper() == "ORIG"]
        original = min(orig_rows, key=lambda row: (date_key(row), clean_text(row.get("SubmissionNo", "")))) if orig_rows else None

        dated_rows = [row for row in rows if date_key(row)]
        latest = max(dated_rows, key=lambda row: (date_key(row), clean_text(row.get("SubmissionNo", "")))) if dated_rows else None

        approved_rows = [row for row in rows if clean_text(row.get("SubmissionStatus", "")).upper() == "AP" and date_key(row)]
        latest_approved = max(approved_rows, key=lambda row: (date_key(row), clean_text(row.get("SubmissionNo", "")))) if approved_rows else None

        summaries[appl_no] = FdaSubmissionSummary(
            original_submission_status=clean_text(original.get("SubmissionStatus", "")) if original else "",
            original_submission_status_date=parse_fda_date(original.get("SubmissionStatusDate", "")) if original else "",
            latest_submission_status=clean_text(latest.get("SubmissionStatus", "")) if latest else "",
            latest_submission_status_date=parse_fda_date(latest.get("SubmissionStatusDate", "")) if latest else "",
            latest_approved_submission_status_date=parse_fda_date(latest_approved.get("SubmissionStatusDate", "")) if latest_approved else "",
            has_approved_submission=bool(approved_rows),
        )
    return summaries


def no_application_row(appl_no: str) -> FdaApplicationRow:
    return FdaApplicationRow(appl_no=appl_no, appl_type="", appl_public_notes="", sponsor_name="")


def no_submission_summary() -> FdaSubmissionSummary:
    return FdaSubmissionSummary("", "", "", "", "", False)


def tokens_for_anchor(value: object) -> set[str]:
    return set(TOKEN_RE.findall(normalize_lookup_text(value)))


def remove_fda_prefix_and_suffix(value: str) -> str:
    text = normalize_lookup_text(value)
    if not text:
        return ""

    first_piece, sep, rest = text.partition("-")
    if sep and first_piece in FDA_PROPER_NAME_PREFIXES and rest:
        text = rest

    text = FDA_BIOLOGIC_SUFFIX_RE.sub("", text)
    return clean_text(text)


def candidate_anchor_variants(value: object) -> list[str]:
    text = normalize_lookup_text(value)
    stripped = remove_fda_prefix_and_suffix(text)

    # Prefer the unsuffixed / unprefixed FDA proper-name core first.
    # Example: fam-trastuzumab deruxtecan-nxki should anchor to
    # trastuzumab deruxtecan, not to the FDA suffixed PIN concept.
    variants = [stripped, text] if stripped and stripped != text else [text]

    tokens = TOKEN_RE.findall(stripped or text)
    if tokens:
        filtered = [token for token in tokens if token not in SIMPLE_FORM_MODIFIER_TOKENS]
        if filtered and filtered != tokens:
            variants.append(" ".join(filtered))

    out: list[str] = []
    seen: set[str] = set()
    for variant in variants:
        variant = normalize_lookup_text(variant)
        if variant and variant not in seen:
            seen.add(variant)
            out.append(variant)
    return out


def choose_exact_anchor_row(
    term: str,
    conso_index: RxnConsoIndex,
    allowed_ttys: set[str] = INGREDIENT_ANCHOR_TTYS,
):
    scored = []
    for row in conso_index.exact_lookup(term):
        best = conso_index.best_row_for_rxcui(row.rxcui)
        if best is None or best.tty not in allowed_ttys:
            continue
        scored.append(
            (
                INGREDIENT_ANCHOR_TTY_PRIORITY.get(best.tty, 99),
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


def has_fda_proper_name_prefix(value: object) -> bool:
    text = normalize_lookup_text(value)
    first_piece, sep, _ = text.partition("-")
    return bool(sep and first_piece in FDA_PROPER_NAME_PREFIXES)


def has_biologic_suffix(value: object) -> bool:
    return bool(FDA_BIOLOGIC_SUFFIX_RE.search(normalize_lookup_text(value)))


def is_single_token_biosimilar_name(value: object) -> bool:
    text = normalize_lookup_text(value)
    if not has_biologic_suffix(text):
        return False
    core = FDA_BIOLOGIC_SUFFIX_RE.sub("", text)
    return bool(core) and " " not in core and "-" not in core


def has_non_collapsible_active_modifier(value: object) -> bool:
    return bool(tokens_for_anchor(value) & NON_COLLAPSIBLE_ACTIVE_TOKENS)


def broad_ingredient_is_compatible_simple_form(
    canonical_name: str,
    ingredient_name: str,
) -> bool:
    canonical_tokens = tokens_for_anchor(canonical_name)
    ingredient_tokens = tokens_for_anchor(ingredient_name)
    if not canonical_tokens or not ingredient_tokens:
        return False
    if not ingredient_tokens.issubset(canonical_tokens):
        return False
    remainder = canonical_tokens - ingredient_tokens
    return bool(remainder) and remainder.issubset(SIMPLE_FORM_MODIFIER_TOKENS)


def derive_fda_link_anchor_from_values(
    *,
    source_text: str,
    matched_rxcui: str,
    canonical_name: str,
    canonical_tty: str,
    ingredient_rxcui: str,
    ingredient_name: str,
    ingredient_tty: str,
    conso_index: RxnConsoIndex,
) -> FdaLinkAnchor:
    matched_rxcui = clean_text(matched_rxcui)
    canonical_name = clean_text(canonical_name)
    canonical_tty = clean_text(canonical_tty)
    ingredient_rxcui = clean_text(ingredient_rxcui)
    ingredient_name = clean_text(ingredient_name)
    ingredient_tty = clean_text(ingredient_tty)
    source_text = clean_text(source_text)

    if not matched_rxcui:
        return FdaLinkAnchor("", "", "", "NO_MATCHED_RXCUI", "", True)

    if canonical_tty in {"IN", "MIN"}:
        return FdaLinkAnchor(
            matched_rxcui,
            canonical_name,
            canonical_tty,
            "MATCHED_RXCUI_IS_BROAD_INGREDIENT",
            matched_rxcui,
            False,
        )

    if canonical_tty == "BN" and ingredient_rxcui:
        return FdaLinkAnchor(
            ingredient_rxcui,
            ingredient_name,
            ingredient_tty,
            "BRAND_TO_RXNORM_INGREDIENT",
            f"{matched_rxcui} -> {ingredient_rxcui}",
            False,
        )

    # FDA biologic proper-name prefixes/suffixes and ADC/linker/payload tokens are
    # therapeutically specific. Try to normalize to the unsuffixed precise concept,
    # not to the parent antibody/protein.
    precise_name = canonical_name or source_text
    should_preserve_precise = (
        has_fda_proper_name_prefix(precise_name)
        or (has_biologic_suffix(precise_name) and " " in remove_fda_prefix_and_suffix(precise_name))
        or has_non_collapsible_active_modifier(precise_name)
        or has_non_collapsible_active_modifier(source_text)
    )
    if canonical_tty == "PIN" and should_preserve_precise:
        for variant in candidate_anchor_variants(precise_name):
            row = choose_exact_anchor_row(variant, conso_index, allowed_ttys={"PIN", "IN", "MIN"})
            if row is not None and row.rxcui != ingredient_rxcui:
                return FdaLinkAnchor(
                    row.rxcui,
                    row.str_value,
                    row.tty,
                    "PRECISE_ACTIVE_CORE_EXACT",
                    f"{matched_rxcui} -> exact({variant}) -> {row.rxcui}",
                    False,
                )
        return FdaLinkAnchor(
            matched_rxcui,
            canonical_name,
            canonical_tty,
            "PRECISE_ACTIVE_MATCHED_RXCUI",
            matched_rxcui,
            False,
        )

    # Four-letter biosimilar suffixes such as trastuzumab-anns are safely grouped
    # with the reference biologic; unlike ADCs, these do not add a second active payload.
    if canonical_tty == "PIN" and is_single_token_biosimilar_name(precise_name) and ingredient_rxcui:
        return FdaLinkAnchor(
            ingredient_rxcui,
            ingredient_name,
            ingredient_tty,
            "BIOSIMILAR_SUFFIX_TO_INGREDIENT",
            f"{matched_rxcui} -> {ingredient_rxcui}",
            False,
        )

    # Simple salt / solvate / ion forms should link to the broad active ingredient.
    # This rescues common FDA names such as ERLOTINIB HYDROCHLORIDE and BLEOMYCIN SULFATE.
    for variant in candidate_anchor_variants(precise_name):
        row = choose_exact_anchor_row(variant, conso_index, allowed_ttys={"IN", "MIN"})
        if row is not None and row.rxcui != matched_rxcui:
            return FdaLinkAnchor(
                row.rxcui,
                row.str_value,
                row.tty,
                "SIMPLE_FORM_TO_BROAD_INGREDIENT_EXACT",
                f"{matched_rxcui} -> exact({variant}) -> {row.rxcui}",
                False,
            )

    if ingredient_rxcui and ingredient_tty in {"IN", "MIN"} and broad_ingredient_is_compatible_simple_form(
        precise_name, ingredient_name
    ):
        return FdaLinkAnchor(
            ingredient_rxcui,
            ingredient_name,
            ingredient_tty,
            "SIMPLE_FORM_TO_BROAD_INGREDIENT_GRAPH",
            f"{matched_rxcui} -> {ingredient_rxcui}",
            False,
        )

    if ingredient_rxcui and ingredient_tty in {"IN", "MIN"} and canonical_tty not in {"PIN"}:
        return FdaLinkAnchor(
            ingredient_rxcui,
            ingredient_name,
            ingredient_tty,
            "NON_PIN_TO_RXNORM_INGREDIENT",
            f"{matched_rxcui} -> {ingredient_rxcui}",
            False,
        )

    # Conservative fallback: keep the matched precise concept rather than risk
    # collapsing a therapeutically distinct component to an over-broad ingredient.
    return FdaLinkAnchor(
        matched_rxcui,
        canonical_name,
        canonical_tty,
        "MATCHED_RXCUI_PRECISE_FALLBACK",
        matched_rxcui,
        canonical_tty not in INGREDIENT_ANCHOR_TTYS,
    )


def derive_fda_link_anchor(
    source_text: str,
    term_resolution: TermResolution,
    ingredient_resolution: IngredientResolution,
    conso_index: RxnConsoIndex,
) -> FdaLinkAnchor:
    return derive_fda_link_anchor_from_values(
        source_text=source_text,
        matched_rxcui=term_resolution.rxcui,
        canonical_name=term_resolution.canonical_name,
        canonical_tty=term_resolution.canonical_tty,
        ingredient_rxcui=ingredient_resolution.ingredient_rxcui,
        ingredient_name=ingredient_resolution.ingredient_name,
        ingredient_tty=ingredient_resolution.ingredient_tty,
        conso_index=conso_index,
    )


def resolve_fda_component_to_rxnorm(
    component: str,
    conso_index: RxnConsoIndex,
    rel_index: RxnRelIndex,
) -> tuple[TermResolution, IngredientResolution, FdaLinkAnchor]:
    term_resolution = resolve_term(component, conso_index)
    ingredient_resolution = rel_index.resolve_ingredient(
        matched_rxcui=term_resolution.rxcui,
        conso_index=conso_index,
        source_term=component,
    )
    link_anchor = derive_fda_link_anchor(component, term_resolution, ingredient_resolution, conso_index)
    return term_resolution, ingredient_resolution, link_anchor


def fda_manual_review_needed(
    parse_status: str,
    term_resolution: TermResolution,
    ingredient_resolution: IngredientResolution,
    link_anchor: FdaLinkAnchor,
) -> bool:
    if parse_status != "OK":
        return True
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


def resolution_payload(
    product: FdaProductRow,
    component: str,
    term_resolution: TermResolution,
    ingredient_resolution: IngredientResolution,
    link_anchor: FdaLinkAnchor,
) -> str:
    return json.dumps(
        {
            "fda_product": asdict(product),
            "active_ingredient_component": component,
            "term_resolution": asdict(term_resolution),
            "ingredient_resolution": asdict(ingredient_resolution),
            "fda_link_anchor": asdict(link_anchor),
        },
        ensure_ascii=False,
    )


def build_fda_product_ingredient_components(
    fda_raw_dir: Path,
    conso_index: RxnConsoIndex,
    rel_index: RxnRelIndex,
) -> list[FdaProductIngredientComponent]:
    products = parse_products(fda_raw_dir / PRODUCTS_FILENAME)
    applications = parse_applications(fda_raw_dir / APPLICATIONS_FILENAME)
    marketing_status = parse_marketing_status(fda_raw_dir / MARKETING_STATUS_FILENAME)
    marketing_status_lookup = parse_marketing_status_lookup(fda_raw_dir / MARKETING_STATUS_LOOKUP_FILENAME)
    submission_summaries = parse_submission_summaries(fda_raw_dir / SUBMISSIONS_FILENAME)

    logger.info("Loaded %d FDA product rows", len(products))

    resolution_cache: dict[str, tuple[TermResolution, IngredientResolution, FdaLinkAnchor]] = {}
    components: list[FdaProductIngredientComponent] = []

    for product in products:
        ingredient_components = split_top_level_semicolons(product.active_ingredient)
        if not ingredient_components:
            ingredient_components = [""]

        parse_status = component_parse_status(product.active_ingredient, ingredient_components)
        ingredient_count = len(ingredient_components)
        application = applications.get(product.appl_no, no_application_row(product.appl_no))
        mstatus = marketing_status.get((product.appl_no, product.product_no))
        mstatus_lookup = marketing_status_lookup.get(mstatus.marketing_status_id if mstatus else "")
        submission_summary = submission_summaries.get(product.appl_no, no_submission_summary())

        for position, ingredient_component in enumerate(ingredient_components, start=1):
            ingredient_component = clean_text(ingredient_component)
            normalized = normalize_fda_ingredient_text(ingredient_component)
            strength_component, strength_status = strength_component_for_position(
                product.strength,
                ingredient_count=ingredient_count,
                position=position,
            )

            if normalized not in resolution_cache:
                resolution_cache[normalized] = resolve_fda_component_to_rxnorm(
                    ingredient_component,
                    conso_index=conso_index,
                    rel_index=rel_index,
                )
            term_resolution, ingredient_resolution, link_anchor = resolution_cache[normalized]

            components.append(
                FdaProductIngredientComponent(
                    appl_no=product.appl_no,
                    product_no=product.product_no,
                    ingredient_position=position,
                    ingredient_count=ingredient_count,
                    active_ingredient_raw=product.active_ingredient,
                    active_ingredient_component=ingredient_component,
                    active_ingredient_component_normalized=normalized,
                    strength_raw=product.strength,
                    strength_component=strength_component,
                    strength_parse_status=strength_status,
                    component_parse_status=parse_status,
                    drug_name=product.drug_name,
                    form=product.form,
                    reference_drug=product.reference_drug,
                    reference_standard=product.reference_standard,
                    appl_type=application.appl_type,
                    sponsor_name=application.sponsor_name,
                    appl_public_notes=application.appl_public_notes,
                    marketing_status_id=mstatus.marketing_status_id if mstatus else "",
                    marketing_status_description=(
                        mstatus_lookup.marketing_status_description if mstatus_lookup else ""
                    ),
                    original_submission_status=submission_summary.original_submission_status,
                    original_submission_status_date=submission_summary.original_submission_status_date,
                    latest_submission_status=submission_summary.latest_submission_status,
                    latest_submission_status_date=submission_summary.latest_submission_status_date,
                    latest_approved_submission_status_date=(
                        submission_summary.latest_approved_submission_status_date
                    ),
                    has_approved_submission=submission_summary.has_approved_submission,
                    fda_rxnorm_rxcui=term_resolution.rxcui,
                    fda_rxnorm_canonical_name=term_resolution.canonical_name,
                    fda_rxnorm_term_type=term_resolution.canonical_tty,
                    fda_rxnorm_ingredient_rxcui=ingredient_resolution.ingredient_rxcui,
                    fda_rxnorm_ingredient_name=ingredient_resolution.ingredient_name,
                    fda_rxnorm_ingredient_term_type=ingredient_resolution.ingredient_tty,
                    fda_rxnorm_match_stage=term_resolution.match_stage,
                    fda_rxnorm_match_status=term_resolution.match_status,
                    fda_rxnorm_ingredient_resolution_stage=(
                        ingredient_resolution.ingredient_resolution_stage
                    ),
                    fda_rxnorm_ingredient_path=ingredient_resolution.ingredient_path,
                    fda_link_anchor_rxcui=link_anchor.link_anchor_rxcui,
                    fda_link_anchor_name=link_anchor.link_anchor_name,
                    fda_link_anchor_term_type=link_anchor.link_anchor_term_type,
                    fda_link_anchor_strategy=link_anchor.link_anchor_strategy,
                    fda_link_anchor_path=link_anchor.link_anchor_path,
                    fda_link_anchor_manual_review_needed=link_anchor.link_anchor_manual_review_needed,
                    fda_manual_review_needed=fda_manual_review_needed(
                        parse_status,
                        term_resolution,
                        ingredient_resolution,
                        link_anchor,
                    ),
                    resolution_payload=resolution_payload(
                        product,
                        ingredient_component,
                        term_resolution,
                        ingredient_resolution,
                        link_anchor,
                    ),
                )
            )

    logger.info(
        "Built %d FDA product ingredient component rows from %d product rows",
        len(components),
        len(products),
    )
    return components
