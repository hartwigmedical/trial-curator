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
# ⚠ The stage pattern is DELIBERATELY bounded (rewritten 2026-08-05). It used to be
#     r"\bstage\s*[0-9ivabc/,\-\s]*"
# whose character class contains a, b, c, i and v — so, case-insensitively, it ate into the FOLLOWING WORD:
#     "stage III breast cancer"    -> "reast cancer"       (the 'b' consumed)
#     "stage IV colorectal cancer" -> "olorectal cancer"
#     "stage I-IVA cervical cancer"-> "ervical cancer"
# 775 of 4,978 live values were being mangled. That silently DEFEATS the consistency check it feeds: the mangled
# key no longer equals the un-staged phrasing's key, so "stage III breast cancer" and "breast cancer" were never
# compared. Now the numeral run is matched explicitly, an optional A/B/C sub-stage must end on a word boundary,
# and ranges ("I-IVA", "II/III") are handled — so nothing beyond the stage token is consumed.
# It is also correctly conservative about non-numeric uses: "extensive-stage" (a real SCLC distinction) and TNM
# ("clinical tumour stage T2-T4a") keep the word, because neither is followed by a plain stage numeral.
_STAGE = re.compile(r"\bstage\s*[0-9ivx]+[abc]?(?:\s*[-/,]\s*[0-9ivx]+[abc]?)*\b", re.I)

# ⚠ NUMERIC GRADE IS NO LONGER STRIPPED (2026-08-05). It used to be, alongside stage — and that was a real defect:
# `WHO grade 2 glioma`, `WHO grade 3 glioma` and `WHO grade 4 glioma` all collapsed to the key `who glioma`, so the
# consistency check reported them as ONE concept mapping to three different codes and handed them to the LLM to
# unify. But grade IS the discriminating feature for gliomas — grade 2/3 are ASTR2/ASTR3/ODG2/ODG3 and grade 4 is
# glioblastoma — so "unifying" them can only destroy a real distinction. It did: a grade-2/3 glioma value was
# flattened from `ASTR2 OR ASTR3 OR ODG2 OR ODG3` to `ASTR OR ODG`. All four groups the check still reported were
# this same false positive (the worst pairing LGGNOS with HGGNOS).
#
# Why removal is strictly safe, not a trade-off: grouping only ever ACTS when members map to DIFFERENT codes.
# Where grade does not change the code (`grade 3 breast cancer` and `breast cancer` are both BREAST) the members
# agree, so no group forms and nothing was gained by merging them. Where grade DOES change the code, merging is
# actively harmful. So stripping grade could only ever cause harm.
#
# NB `_QUALIFIERS` deliberately keeps the WORD forms too ("high-grade", "low-grade"), for the same reason.


#: A normalisation pass can EXPOSE a pattern the previous pass could not see — stripping punctuation turns
#: "stage <=2" into "stage 2", which `_STAGE` then matches. So one pass is not a fixed point, and a key that
#: depends on how many times you call it is not a key: two values that differ only in punctuation would land on
#: different keys and never be compared. Found by the 2026-08-05 invariant sweep (12 live values). Iterating to a
#: fixed point makes `canonical_key` idempotent by construction; 4 is far above the observed need (2).
_MAX_NORMALISE_PASSES = 4


def _normalise_once(v: str) -> str:
    v = _STAGE.sub(" ", v)
    v = _QUALIFIERS.sub(" ", v)
    v = re.sub(r"[^a-z0-9]+", " ", v)
    return " ".join(v.split())


def canonical_key(value: str) -> str:
    """Normalise a cancer_type value to a canonical concept key by dropping qualifier / stage noise and
    punctuation (meaningful tumour words, GRADE and NOT(...) bodies are kept, so genuinely different scopes and
    grades stay apart). Idempotent: applied to its own output it is a no-op."""
    v = (value or "").lower()
    for _ in range(_MAX_NORMALISE_PASSES):
        nxt = _normalise_once(v)
        if nxt == v:
            break
        v = nxt
    return v


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
