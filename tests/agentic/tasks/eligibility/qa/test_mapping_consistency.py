"""Cross-value consistency check: semantically-equivalent inputs must not map to different codes."""
from __future__ import annotations

from aus_trial_universe.agentic.tasks.eligibility.qa.mapping_consistency import canonical_key, find_inconsistencies


def test_canonical_key_strips_qualifier_noise_but_keeps_meaning():
    assert canonical_key("metastatic breast cancer") == canonical_key("breast cancer") == "breast cancer"
    assert canonical_key("Stage IV histologically confirmed NSCLC") == "nsclc"
    # meaningful words are kept -> genuinely different concepts stay apart
    assert canonical_key("high-grade serous ovarian cancer") != canonical_key("ovarian cancer")
    assert canonical_key("melanoma") != canonical_key("melanoma AND NOT(uveal melanoma)")


def test_find_inconsistencies_flags_divergent_only():
    inc = find_inconsistencies({
        "breast cancer": "BREAST",
        "metastatic breast cancer": "BRCA",     # same concept, different code -> FLAG
        "melanoma": "MEL",
        "metastatic melanoma": "MEL",           # same concept, same code -> not flagged
    })
    assert set(inc) == {"breast cancer"}
    assert set(inc["breast cancer"]) == {"BREAST", "BRCA"}
