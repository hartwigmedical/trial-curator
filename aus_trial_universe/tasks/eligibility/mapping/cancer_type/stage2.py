"""STAGE 2 of OncoTree mapping — CANONICALISATION. Deterministic, pure, single-value.

Spec agreed with the user 2026-08-06. Stage 2 does exactly two things, both mechanical:

    S2.2  ALGEBRAIC NORMALISATION — rewrite the faithful stage-1 shape into the form the consumer requires.
          · flatten a nested NOT via De Morgan -> distribute -> absorb:
                "solid tumours (excluding CNS tumours other than IDHwt glioblastoma)"
                  Solid tumour AND NOT(BRAIN AND NOT(GB))          direct mapping (stage 1)
                  Solid tumour AND (NOT(BRAIN) OR GB)              De Morgan
                  (Solid tumour AND NOT(BRAIN)) OR (Solid tumour AND GB)   distribute
                  (Solid tumour AND NOT(BRAIN)) OR GB              absorb: GB is inside the carved-out scope
          · factor NOT(A) AND NOT(B) -> NOT(A OR B), sort commutative branches, dedupe, strip redundant parens
          · TWO carve-outs stay nested because the matching engine special-cases them:
                SKIN AND NOT(MEL)      NSCLC AND NOT(LUSC)

    S2.3  VACUOUS EXCLUSION REMOVAL — drop a NOT(X) where X is disjoint from every positive code.
          This is NOT a logical identity, and the spec records why it is nonetheless valid: it holds only under the
          assumption that the cell describes ONE tumour type of ONE patient. The worked case is the user's BLCA
          ruling — a source excluding upper-tract and urethral urothelial carcinoma from a bladder-cancer cohort is
          excluding SIBLINGS of BLCA, so as a tumour-TYPE criterion the exclusion is a no-op; what the trial is
          really excluding is a concurrent second primary, which is not a tumour-type criterion and cannot be
          expressed in this cell at all.

WHY STAGE 2 IS DETERMINISTIC-ONLY, AND WHY THAT MATTERS MOST
-----------------------------------------------------------
Earlier designs gave stage 2 an LLM cross-value consistency job (detect values that mean the same thing but map
differently, and unify them). It was dropped, and the reason is the whole point of this module: a function of a
SINGLE value cannot be perturbed by other values entering the corpus, so **churn becomes structurally impossible**
rather than merely mitigated. The previous reconciler re-decided whole groups whenever group membership shifted,
which silently re-rolled 7 unrelated values on 2026-08-05 and undid two genuine improvements. That failure mode
does not exist here.

The cross-value job was measured before being dropped: of 20 divergent groups, this pass dissolves 8 as pure
OR-ordering noise, and the surviving 12 are held by stage 3 rulings. Eleven of the twelve are stage 1 answering the
same question two ways on a qualifier variant, so the root fix belongs to stage 1 — handover to-do B6.

OUT OF SCOPE, deliberately:
  · re-mapping a value from its source text  -> stage 1's information set; every regression in the 2026-08-05/06
    work came from a value being re-opened downstream
  · applying the adjudication register       -> stage 3
  · per-value LLM repair (the old R4)        -> had exactly stage 1's inputs, so it was stage 1's job all along
"""
from __future__ import annotations

from aus_trial_universe.tasks.eligibility.mapping.cancer_type.vocab import (
    Problem,
    canonical_form,
    expression_problems,
    normalise_code_expression,
)

#: Bounded convergence. `canonical_form` already iterates internally to a fixed point; this outer bound exists
#: because the two passes can expose work for each other (resolving an operand can reveal a flattenable nested
#: NOT). Idempotence is asserted over the whole live corpus by `qa/invariants.py` C2, not merely hoped for.
MAX_PASSES = 3


def canonicalise(code_expression: str) -> str:
    """S2.2 + S2.3 for ONE value. Pure: same input -> same output, no I/O, no other values consulted.

    Idempotent by construction — it runs to a fixed point and returns early once nothing changes.
    """
    out = code_expression or ""
    for _ in range(MAX_PASSES):
        nxt = canonical_form(normalise_code_expression(out), drop_vacuous=True)
        if nxt == out:
            return out
        out = nxt
    return out


def postcondition_problems(value: str, code_expression: str) -> list[Problem]:
    """Error-severity defects in stage 2's OWN output — a POSTCONDITION, never a repair trigger.

    Single-value validity (real codes, well-formed, satisfiable) is stage 1's gate. It is re-asserted here because
    stage 2's rewrites can in principle introduce a defect, and because a defect that appears between stages would
    otherwise be invisible. It must NOT cause the value to be re-mapped: re-opening a value is what produced every
    regression this work has seen. A failure here is a bug in the rewrite, to be fixed in code.
    """
    return [p for p in expression_problems(code_expression) if p.severity == "error"]


def run(initial: dict[str, str]) -> tuple[dict[str, str], dict[str, list[Problem]]]:
    """Apply stage 2 to every value. Returns (reconciled {value -> code}, postcondition failures by value).

    `initial` is stage 1's output ({cancer_type -> oncotree_code}). No LLM, no network, no store access: this is a
    pure transform, so it can be re-run at any time and is safe to call from a test.
    """
    reconciled = {v: canonicalise(code) for v, code in initial.items()}
    failures = {v: probs for v, code in reconciled.items()
                if (probs := postcondition_problems(v, code))}
    return reconciled, failures
