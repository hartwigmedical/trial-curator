from __future__ import annotations

import pandas as pd
import pytest

from aus_trial_universe.eligibility_path.shared.cancer_types.final_determination.primary_vs_conditions import (
    MANUAL_COL,
    apply_manual_overwrite_concat,
)


def _generated(nct_ids, *, extra=None):
    data = {
        "nct_id": list(nct_ids),
        "primary_tumor_type": [f"type_{n}" for n in nct_ids],
        "primary_tumor_location": [f"loc_{n}" for n in nct_ids],
        "conditions_original": [f"cond_{n}" for n in nct_ids],
    }
    if extra:
        data.update(extra)
    return pd.DataFrame(data)


def _manual(nct_ids, values):
    return pd.DataFrame(
        {
            "nct_id": list(nct_ids),
            "primary_tumor_type": [f"type_{n}" for n in nct_ids],
            "primary_tumor_location": [f"loc_{n}" for n in nct_ids],
            "conditions_original": [f"cond_{n}" for n in nct_ids],
            MANUAL_COL: list(values),
        }
    )


def test_manual_overwrite_matches_by_key_regardless_of_order_or_count():
    # NCT3 is newly generated (no manual entry); manual rows are in a different
    # order and include NCT9 which no longer has a generated row.
    generated = _generated(["NCT1", "NCT2", "NCT3"], extra={"other": ["a", "b", "c"]})
    manual = _manual(["NCT2", "NCT1", "NCT9"], ["BREAST", "LUNG", "IGNORED"])

    out = apply_manual_overwrite_concat(generated, manual)

    # left order + count preserved (no fan-out, no extra rows)
    assert out["nct_id"].tolist() == ["NCT1", "NCT2", "NCT3"]
    assert out["other"].tolist() == ["a", "b", "c"]
    by_nct = dict(zip(out["nct_id"], out[MANUAL_COL]))
    assert by_nct["NCT1"] == "LUNG"
    assert by_nct["NCT2"] == "BREAST"
    assert by_nct["NCT3"] == ""  # new generated row gets a blank overwrite


def test_manual_overwrite_applies_same_value_to_duplicate_generated_keys():
    generated = _generated(["NCT1", "NCT1"])
    manual = _manual(["NCT1"], ["VALUE"])

    out = apply_manual_overwrite_concat(generated, manual)

    assert len(out) == 2
    assert out[MANUAL_COL].tolist() == ["VALUE", "VALUE"]


def test_manual_overwrite_dedupes_duplicate_manual_keys_without_fanout():
    generated = _generated(["NCT1"])
    manual = _manual(["NCT1", "NCT1"], ["FIRST", "SECOND"])

    out = apply_manual_overwrite_concat(generated, manual)

    assert len(out) == 1
    assert out[MANUAL_COL].iloc[0] == "FIRST"  # keep first per key


def test_manual_overwrite_requires_key_and_value_columns():
    generated = pd.DataFrame({"nct_id": ["NCT1"]})  # missing other key columns
    manual = _manual(["NCT1"], ["V"])

    with pytest.raises(ValueError):
        apply_manual_overwrite_concat(generated, manual)
