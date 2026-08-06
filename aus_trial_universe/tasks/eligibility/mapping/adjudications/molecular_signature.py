"""Approved rulings for the `molecular_signature` column — EMPTY, deliberately.

Not a placeholder: the module exists so the column has a register the moment one is needed, and so
`for_column("molecular_signature")` answers without a special case. Signature mapping is the smallest and most
constrained of the three vocabularies — six permitted terms, and `""` is common and correct for a non-signature —
so the 2026-08-05 full-corpus audit found **no defect** among its 171 values, and nothing has been adjudicated.

Add entries here exactly as the other two columns do; `mapping/reconcile.py` R7 already applies this register.
"""
from __future__ import annotations

from aus_trial_universe.tasks.eligibility.mapping.adjudications import Adjudication  # noqa: F401  (re-exported for symmetry)

APPROVED: dict[str, "Adjudication"] = {}
