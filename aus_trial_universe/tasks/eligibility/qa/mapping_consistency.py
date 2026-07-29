"""Deterministic cross-value CONSISTENCY check for a value->code map.

Requirement (user, 2026-07-27): the final resource must NOT map semantically-equivalent inputs to different
outputs. Exact-string consistency is already guaranteed (one map row per distinct value), so the risk is
*qualifier-noise* variants of one concept diverging — e.g. "breast cancer" -> BREAST but "metastatic breast
cancer" -> BRCA. This module normalises each input to a canonical key (stripping the non-discriminating
qualifier / stage / grade noise, NOT the meaningful tumour words) and flags any key whose members map to more
than one distinct code. It SPOTS inconsistencies for review; Step-2 reconciliation is what resolves them.
"""
from __future__ import annotations

import re
from collections import defaultdict

# Non-discriminating qualifiers that describe disease STATE/CONTEXT, not the tumour TYPE — safe to strip for the
# key. Deliberately EXCLUDES meaningful words like "invasive" / "high-grade" / "serous" / "squamous" that DO
# change the mapping.
_QUALIFIERS = re.compile(
    r"\b(advanced|metastatic|locally\s+advanced|unresectable|resectable|recurrent|relapsed|refractory|persistent|"
    r"progressive|newly\s+diagnosed|previously\s+treated|untreated|treated|histologically|cytologically|"
    r"pathologically|clinically|radiographically|confirmed|documented|proven|diagnosed|surgically|completely|"
    r"partially|adequately|first[- ]line|second[- ]line|frontline|not\s+curable|curable|measurable)\b",
    re.I,
)
_STAGE = re.compile(r"\bstage\s*[0-9ivabc/,\-\s]*", re.I)
_GRADE = re.compile(r"\bgrade\s*[0-9]+\b", re.I)


def canonical_key(value: str) -> str:
    """Normalise a cancer_type value to a canonical concept key by dropping qualifier / stage / grade noise and
    punctuation (meaningful tumour words and NOT(...) bodies are kept, so genuinely different scopes stay apart)."""
    v = (value or "").lower()
    v = _STAGE.sub(" ", v)
    v = _GRADE.sub(" ", v)
    v = _QUALIFIERS.sub(" ", v)
    v = re.sub(r"[^a-z0-9]+", " ", v)
    return " ".join(v.split())


def find_inconsistencies(mapping: dict[str, str]) -> dict[str, dict[str, list[str]]]:
    """Given ``{value: code}``, return ``{canonical_key: {code: [values]}}`` for every key that maps to MORE THAN
    ONE distinct code — i.e. the semantically-equivalent inputs that diverged."""
    groups: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for value, code in mapping.items():
        groups[canonical_key(value)][code].append(value)
    return {k: {c: vs for c, vs in codes.items()} for k, codes in groups.items() if len(codes) > 1}


def format_report(inconsistencies: dict[str, dict[str, list[str]]]) -> str:
    """Human-readable report of the divergent groups (most-divergent first)."""
    if not inconsistencies:
        return "consistency: OK — no semantically-equivalent inputs map to different codes."
    lines = [f"consistency: {len(inconsistencies)} inconsistent group(s) — same concept, different code:"]
    for key, codes in sorted(inconsistencies.items(), key=lambda kv: -len(kv[1])):
        lines.append(f"  [{key}] -> {len(codes)} codes:")
        for code, vals in codes.items():
            lines.append(f"      {code or '(empty)'}:  " + " | ".join(v[:60] for v in vals[:4]))
    return "\n".join(lines)
