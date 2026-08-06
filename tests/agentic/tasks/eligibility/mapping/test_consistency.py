"""Cross-value consistency check: semantically-equivalent inputs must not map to different codes."""
from __future__ import annotations

from aus_trial_universe.tasks.eligibility.mapping.consistency import canonical_key, find_inconsistencies


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


# --------------------------------------------------------------------------- #
# Two defects found 2026-08-05 while tracing why one glioma value churned on every reconcile. Both are in
# `canonical_key`, which decides what the LLM adjudicator is asked to unify — so a bad key does not merely
# mis-report, it actively destroys correct mappings.
# --------------------------------------------------------------------------- #
def test_numeric_grade_is_kept_because_it_discriminates():
    """Grade used to be stripped, so `WHO grade 2/3/4 glioma` collapsed to one key and the adjudicator was asked
    to give three genuinely different tumour types (ASTR2/ASTR3 · glioblastoma) a single code. It obliged once,
    flattening `ASTR2 OR ASTR3 OR ODG2 OR ODG3` to `ASTR OR ODG`."""
    k2, k3, k4 = (canonical_key(f"WHO grade {n} glioma") for n in (2, 3, 4))
    assert k2 != k3 != k4 and k2 != k4
    # the word forms were always kept; the numeric form must behave the same way
    assert canonical_key("high-grade glioma") != canonical_key("low-grade glioma")


def test_grouping_still_ignores_grade_when_grade_does_not_change_the_code():
    """Keeping grade costs nothing where it is not discriminating: the members agree, so no group forms either
    way. This is why removing the stripping is strictly safe rather than a trade-off."""
    assert find_inconsistencies({"grade 3 breast cancer": "BREAST", "breast cancer": "BREAST"}) == {}


def test_stage_pattern_does_not_eat_the_following_word():
    """The old class `[0-9ivabc/,\\-\\s]*` consumed the leading letter of the next word (case-insensitively a, b,
    c, i, v), mangling 775 of 4,978 live values and silently preventing the very comparison it exists for."""
    assert canonical_key("stage III breast cancer") == canonical_key("breast cancer")
    assert canonical_key("stage IV colorectal cancer") == canonical_key("colorectal cancer")
    assert canonical_key("stage I-IVA cervical cancer") == canonical_key("cervical cancer")
    assert canonical_key("stage IIIB non-small cell lung cancer") == canonical_key("non-small cell lung cancer")
    assert canonical_key("stage 4 breast cancer") == canonical_key("breast cancer")


def test_stage_pattern_leaves_non_numeric_uses_alone():
    """`extensive-stage` is a real SCLC distinction and `tumour stage T2` is TNM — neither is a plain stage
    numeral, so the word must survive."""
    assert "stage" in canonical_key("extensive-stage small cell lung cancer")
    assert "stage" in canonical_key("urinary bladder neoplasm with clinical tumour stage T2-T4a")
    assert canonical_key("extensive-stage SCLC") != canonical_key("limited-stage SCLC")
