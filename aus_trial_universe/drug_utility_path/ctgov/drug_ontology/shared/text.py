from __future__ import annotations

import re
from collections.abc import Callable, Iterable


DISPLAY_DELIMITER = " | "
MISSING_TEXT_VALUES = {"", "nan", "none", "<na>"}
WHITESPACE_RE = re.compile(r"\s+")


def clean_text(value: object) -> str:
    """Return a stripped display string, treating common missing values as blank."""
    if value is None:
        return ""

    text = WHITESPACE_RE.sub(" ", str(value).replace("\ufeff", "")).strip()
    return "" if text.casefold() in MISSING_TEXT_VALUES else text


def normalize_key(value: object) -> str:
    """Return a whitespace-normalized casefold key for exact-ish comparisons."""
    return " ".join(clean_text(value).casefold().split())


def ordered_unique(
    values: Iterable[object],
    *,
    key_func: Callable[[object], str] = normalize_key,
) -> list[str]:
    """Return stable unique cleaned strings."""
    out: list[str] = []
    seen: set[str] = set()

    for value in values:
        text = clean_text(value)
        if not text:
            continue
        key = key_func(text)
        if key not in seen:
            seen.add(key)
            out.append(text)

    return out


def ordered_join(values: Iterable[object], delimiter: str = DISPLAY_DELIMITER) -> str:
    """Join stable unique display values."""
    return delimiter.join(ordered_unique(values))


def split_display_values(value: object, delimiter: str = DISPLAY_DELIMITER) -> list[str]:
    """Split and de-duplicate a flat display column."""
    text = clean_text(value)
    if not text:
        return []
    return ordered_unique(part.strip() for part in text.split(delimiter))


def bool_text(value: bool) -> str:
    return "true" if value else "false"
