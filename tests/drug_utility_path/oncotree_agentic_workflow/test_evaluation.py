from __future__ import annotations

from aus_trial_universe.drug_utility_path.oncotree_agentic_workflow.evaluation import (
    RowScore,
    score_row,
    _aggregate,
)


def test_score_row_exact_match():
    p, r, f1, missing, extra = score_row(["OVARY", "LUAD"], ["LUAD", "OVARY"])
    assert (p, r, f1) == (1.0, 1.0, 1.0)
    assert missing == () and extra == ()


def test_score_row_partial_with_missing_and_extra():
    p, r, f1, missing, extra = score_row(["OVARY", "BRCA"], ["OVARY", "PERITONEUM"])
    # predicted 2, gold 2, 1 correct -> P=0.5 R=0.5
    assert p == 0.5 and r == 0.5
    assert missing == ("PERITONEUM",)
    assert extra == ("BRCA",)


def test_score_row_order_independent():
    a = score_row(["A", "B", "C"], ["C", "B", "A"])
    assert a[:3] == (1.0, 1.0, 1.0)


def test_aggregate_counts_exact_and_micro():
    scores = [
        RowScore("x", ("GB",), ("GB",), "accepted", 1.0, 1.0, 1.0, (), ()),
        RowScore(
            "y", ("OVARY", "PERITONEUM"), ("OVARY",), "accepted",
            1.0, 0.5, 0.667, ("PERITONEUM",), (),
        ),
    ]
    report = _aggregate(scores)
    assert "Rows: 2 | exact matches: 1 (50%)" in report
    # micro: tp=2, pred=2, gold=3 -> P=1.00 R=0.67
    assert "Micro  P=1.00 R=0.67" in report
