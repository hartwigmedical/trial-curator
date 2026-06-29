"""Helpers for the pipeline's curation term-expression mini-language.

Mapped curations are ``A | B | C`` expressions whose terms may contain nested
``[...]`` / ``(...)`` that themselves include ``|`` or ``&`` (e.g.
``GainDeletion[gene=TP53 & type=HOM_DEL]``).  Splitting on the *top-level* ``|``
only, idempotent ``NOT(...)`` wrapping, and order-preserving de-duplication are
shared by the gene-alteration and molecular-signature cores, so the rules live
here in one tested place rather than being copied per pipeline.
"""

from __future__ import annotations

from typing import Iterable, List

from aus_trial_universe.eligibility_path.shared.utils.text_normalisation import (
    blank_safe_str,
)


def _normalize(value: object) -> str:
    return blank_safe_str(value).strip()


def split_top_level_or(expr: object) -> List[str]:
    """Split on top-level ``|``, ignoring any ``|`` nested inside ``[]`` or ``()``."""
    text = _normalize(expr)
    if not text:
        return []

    terms: List[str] = []
    buf: List[str] = []
    paren = 0
    bracket = 0
    i = 0

    while i < len(text):
        ch = text[i]

        if ch == "(":
            paren += 1
            buf.append(ch)
            i += 1
            continue

        if ch == ")":
            paren = max(paren - 1, 0)
            buf.append(ch)
            i += 1
            continue

        if ch == "[":
            bracket += 1
            buf.append(ch)
            i += 1
            continue

        if ch == "]":
            bracket = max(bracket - 1, 0)
            buf.append(ch)
            i += 1
            continue

        if ch == "|" and paren == 0 and bracket == 0:
            part = "".join(buf).strip()
            if part:
                terms.append(part)
            buf = []
            i += 1
            continue

        buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        terms.append(tail)

    return terms


def dedupe_preserve_order(values: Iterable[object]) -> List[str]:
    """Normalise, drop blanks, and de-duplicate while preserving first-seen order."""
    seen: set[str] = set()
    out: List[str] = []

    for value in values:
        cleaned = _normalize(value)
        if not cleaned:
            continue
        if cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)

    return out


def wrap_not(term: str) -> str:
    """Wrap a term in ``NOT(...)`` idempotently (no double-wrapping)."""
    term = _normalize(term)
    if not term:
        return ""
    if term.startswith("NOT(") and term.endswith(")"):
        return term
    return f"NOT({term})"
