from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Iterable, List, Tuple

import pandas as pd

LOGGER = logging.getLogger(__name__)

CORRECTION_KEY_COLUMNS: Tuple[str, str, str] = (
    "Gene_lookup",
    "Alteration_lookup",
    "Variant_lookup",
)

CORRECTED_MAPPING_COLUMN = "Mapping_args_corrected"
TARGET_MAPPING_COLUMN = "Mapping_args"


class MappingCorrectionError(RuntimeError):
    pass


def _normalize_key_value(v: object) -> str:
    if v is None:
        return ""
    s = str(v)
    if s.lower() == "nan":
        return ""
    return re.sub(r"\s+", " ", s).strip().casefold()


def _make_key(df: pd.DataFrame) -> pd.Series:
    return (
        df["Gene_lookup"].map(_normalize_key_value)
        + "\t"
        + df["Alteration_lookup"].map(_normalize_key_value)
        + "\t"
        + df["Variant_lookup"].map(_normalize_key_value)
    )


def _is_blank(v: object) -> bool:
    if v is None:
        return True
    s = str(v).strip()
    return s == "" or s == "_" or s.lower() == "nan"


def _check_required_columns(
    df: pd.DataFrame,
    required_columns: Iterable[str],
    *,
    label: str,
) -> None:
    missing = [c for c in required_columns if c not in df.columns]
    if missing:
        raise MappingCorrectionError(
            f"{label} is missing required columns: {missing}"
        )


def _has_balanced_delimiters(s: str) -> bool:
    pairs = {"[": "]", "(": ")"}
    stack: List[str] = []

    for ch in s:
        if ch in pairs:
            stack.append(pairs[ch])
        elif ch in pairs.values():
            if not stack or stack.pop() != ch:
                return False

    return not stack


_SMALLVARIANT_RE = re.compile(r"SmallVariant\[(.*?)\]")


def _find_unscoped_smallvariant_terms(mapping_args: str) -> List[str]:
    """
    Flag SmallVariant[...] terms where a variant-level constraint appears
    without gene=... inside the same SmallVariant bracket.
    """
    bad_terms: List[str] = []

    for match in _SMALLVARIANT_RE.finditer(mapping_args or ""):
        inside = match.group(1)
        term = match.group(0)

        has_gene = re.search(r"\bgene\s*=", inside) is not None
        has_variant_only_constraint = (
            "transcriptImpact.effects=INFRAME_INSERTION" in inside
            or "inSpliceRegion" in inside
        )

        if has_variant_only_constraint and not has_gene:
            bad_terms.append(term)

    return bad_terms


def validate_mapping_args(mapping_args: object, *, context: str) -> None:
    s = "" if mapping_args is None else str(mapping_args).strip()
    if _is_blank(s):
        return

    if not _has_balanced_delimiters(s):
        raise MappingCorrectionError(
            f"Unbalanced delimiters in corrected Mapping_args for {context}: {s}"
        )

    bad_terms = _find_unscoped_smallvariant_terms(s)
    if bad_terms:
        raise MappingCorrectionError(
            f"Unscoped SmallVariant term(s) remain in corrected Mapping_args "
            f"for {context}: {bad_terms}; full Mapping_args={s}"
        )


def apply_mapping_corrections(
    mapping_df: pd.DataFrame,
    corrections_xlsx: Path,
) -> pd.DataFrame:
    """
    Apply reviewed Mapping_args corrections by lookup key.

    Corrections are matched on:
      Gene_lookup + Alteration_lookup + Variant_lookup

    For every row in mapping_corrections.xlsx where Mapping_args_corrected
    is nonblank, overwrite mapping_df.Mapping_args.
    """
    corrections_xlsx = Path(corrections_xlsx)

    if not corrections_xlsx.exists():
        raise MappingCorrectionError(
            f"Mapping corrections file does not exist: {corrections_xlsx}"
        )

    out = mapping_df.copy()

    _check_required_columns(
        out,
        [*CORRECTION_KEY_COLUMNS, TARGET_MAPPING_COLUMN],
        label="generated mapping resource",
    )

    corrections_df = pd.read_excel(
        corrections_xlsx,
        dtype=str,
        keep_default_na=False,
    )

    _check_required_columns(
        corrections_df,
        [*CORRECTION_KEY_COLUMNS, CORRECTED_MAPPING_COLUMN],
        label=str(corrections_xlsx),
    )

    corrections_df = corrections_df[
        ~corrections_df[CORRECTED_MAPPING_COLUMN].map(_is_blank)
    ].copy()

    if corrections_df.empty:
        LOGGER.info("No nonblank mapping corrections found in %s", corrections_xlsx)
        return out

    out["_correction_key"] = _make_key(out)
    corrections_df["_correction_key"] = _make_key(corrections_df)

    duplicated_resource_keys = out.loc[
        out["_correction_key"].duplicated(keep=False),
        [*CORRECTION_KEY_COLUMNS, "_correction_key"],
    ]
    if not duplicated_resource_keys.empty:
        raise MappingCorrectionError(
            "Generated mapping resource has duplicate correction keys. "
            f"Examples:\n{duplicated_resource_keys.head(20).to_string(index=False)}"
        )

    duplicated_correction_keys = corrections_df.loc[
        corrections_df["_correction_key"].duplicated(keep=False),
        [*CORRECTION_KEY_COLUMNS, CORRECTED_MAPPING_COLUMN, "_correction_key"],
    ]
    if not duplicated_correction_keys.empty:
        raise MappingCorrectionError(
            "mapping_corrections.xlsx has duplicate correction keys. "
            f"Examples:\n{duplicated_correction_keys.head(20).to_string(index=False)}"
        )

    resource_keys = set(out["_correction_key"])
    correction_keys = set(corrections_df["_correction_key"])

    missing_keys = sorted(correction_keys - resource_keys)
    if missing_keys:
        missing_examples = corrections_df[
            corrections_df["_correction_key"].isin(missing_keys)
        ][[*CORRECTION_KEY_COLUMNS, CORRECTED_MAPPING_COLUMN]].head(20)

        raise MappingCorrectionError(
            "Some correction keys were not found in generated mapping resource. "
            f"Examples:\n{missing_examples.to_string(index=False)}"
        )

    correction_map = dict(
        zip(
            corrections_df["_correction_key"],
            corrections_df[CORRECTED_MAPPING_COLUMN],
        )
    )

    for key, corrected_args in correction_map.items():
        context_row = corrections_df.loc[
            corrections_df["_correction_key"] == key,
            list(CORRECTION_KEY_COLUMNS),
        ].iloc[0]

        context = (
            f"Gene_lookup={context_row['Gene_lookup']!r}, "
            f"Alteration_lookup={context_row['Alteration_lookup']!r}, "
            f"Variant_lookup={context_row['Variant_lookup']!r}"
        )

        validate_mapping_args(corrected_args, context=context)

        out.loc[out["_correction_key"] == key, TARGET_MAPPING_COLUMN] = corrected_args

    # Final global safety check after applying all corrections.
    for idx, row in out.iterrows():
        context = (
            f"resource row index={idx}, "
            f"Gene_lookup={row.get('Gene_lookup', '')!r}, "
            f"Alteration_lookup={row.get('Alteration_lookup', '')!r}, "
            f"Variant_lookup={row.get('Variant_lookup', '')!r}"
        )
        validate_mapping_args(row.get(TARGET_MAPPING_COLUMN, ""), context=context)

    out = out.drop(columns=["_correction_key"])

    LOGGER.info(
        "Applied %d Mapping_args correction(s) from %s",
        len(correction_map),
        corrections_xlsx,
    )

    return out