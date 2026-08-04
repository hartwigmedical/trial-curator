"""APPROVED ADJUDICATIONS — cancer_type values the user has ruled on by hand.

Sits beside `waivers.py` for the same reason: `data/` is gitignored, so a decision that lives in code is
visible in review and in `git log`. Step 4 of the review workflow produces a hand-written FINAL for values the automated stages cannot settle. Each
one is recorded here rather than patched into the data, for the same reason `qa/waivers.py` is a code constant:
`data/` is gitignored, so a decision that lives in code is visible in review and in `git log`.

**An entry is an EXPECTATION FIRST, an override second.** The refinement runs normally; the comparison file then
reports, per adjudicated value, whether the pipeline reached the approved answer on its own:

    match     the prompts + logic produced the approved value unaided  -> the fix works
    override  they did not, so the approved value was substituted      -> the fix is INCOMPLETE

An `override` is not a success. It means the rule that should have produced it is missing or too weak, and the
correct response is to strengthen the prompt or the deterministic layer — not to accumulate overrides. The count
of overrides is therefore a quality metric for the correction itself.

Keyed by `cancer_type` (the interpreted source value — the map table's own key), because the CODE is what we are
changing and cannot be a stable key.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Adjudication:
    cancer_type: str          # the map table key (the interpreted source value)
    final_code: str           # the approved oncotree_code_FINAL
    rationale: str
    approved: str             # date + who


APPROVED: dict[str, Adjudication] = {}


def _add(a: Adjudication) -> None:
    APPROVED[a.cancer_type] = a


# --------------------------------------------------------------------------- #
# 2026-08-03
# --------------------------------------------------------------------------- #
_add(Adjudication(
    cancer_type=(
        "relapsed or refractory multiple myeloma AND NOT(ongoing myelodysplastic syndrome or B-cell malignancy "
        "other than multiple myeloma) AND NOT(active or high-risk recurrent malignancy other than multiple "
        "myeloma) AND NOT(active or prior CNS involvement or clinical signs of meningeal involvement of multiple "
        "myeloma)"
    ),
    final_code="PCM",
    rationale=(
        "Was `PCM AND NOT(MDS OR BLL OR (MBN AND NOT(PCM)))`. Four independent rules converge on plain PCM: "
        "(1) all three source exclusions are non-tumour-type — 'other than multiple myeloma' twice (second/"
        "recurrent malignancy) and 'CNS involvement OF multiple myeloma' (disease site), so D1 drops them; "
        "(2) MDS is myeloid and BLL is a precursor neoplasm, both disjoint from PCM, so E6/R2 strips them anyway; "
        "(3) MBN is PCM's own OncoTree parent, so the expression excludes the ancestor of the type it includes "
        "and is safe only if an engine resolves the nested double negation exactly as intended; "
        "(4) BLL is unsupported by the source — it says 'B-cell malignancy', and BLL is not under MBN."
    ),
    approved="2026-08-03, user",
))
