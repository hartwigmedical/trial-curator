"""STAGE 3 of OncoTree mapping — FINALISATION by approved ruling. Deterministic, pure, single-value.

Spec agreed with the user 2026-08-06: *"this is the final overwrite using the registry. This is the last resort for
issues we can't fix in stages 1 and 2."*

Stage 3 does one thing: where `qa/adjudications/cancer_type.py` holds an approved ruling for a value, that ruling is
the answer. Everything else passes through from stage 2 untouched.

WHY THIS IS ITS OWN STAGE
-------------------------
Until 2026-08-06 the register was applied INSIDE the reconciler, as step "R7" of a nine-step pass. Two problems
followed from that, both of which this split fixes:

  · It was invisible. Every discussion of "the finalised mapping" had to be qualified with "…which includes the
    hand-rulings applied at R7", and a reader of the store could not tell which values were the pipeline's answer
    and which were an override. Now the store says so: `_reconciled` is what the pipeline produced, `_finalised` is
    what ships, and a difference between them IS the override.
  · It was miscategorised. A per-value human ruling is not cross-value reconciliation work — it is a correction to a
    single value's mapping. Bundling it into stage 2 made stage 2 look like it did more than algebra.

APPLIED LAST, AND THAT IS LOAD-BEARING. A ruling must be immune to everything upstream: it exists precisely because
stages 1 and 2 got the value wrong. Applying it last is what makes it immune — the same reason it sat at the END of
the old R0-R8 sequence.

`''` IS A LEGITIMATE RULING. The correct mapping for a value that states no CURRENT tumour type — a prior-malignancy
cohort, a screening or prevention population — is empty. So callers must test `is not None`, never truthiness. A
test asserting `ruling.final.strip()` had to be relaxed on 2026-08-06 for exactly this reason.

THE REGISTER IS A LIABILITY, NOT AN ASSET. Every entry is an admission that a prompt or a deterministic rule is
still too weak, so the entry count is the quality metric for the correction and the register is meant to SHRINK. An
entry the pipeline reaches unaided should be RETIRED — `qa/prompt_harness/export_review.py` reports exactly that
as `ruling_status=retirable`. 21 entries were retired on this basis on 2026-08-06; 17 were added the same day to
hold the qualifier-variant inconsistency stage 2 no longer owns (handover to-do B6).
"""
from __future__ import annotations


def rulings() -> dict[str, str]:
    """`{value -> approved final code}` for the cancer_type column. Read fresh so a register edit needs no reload."""
    from aus_trial_universe.qa import adjudications
    return {value: ruling.final for value, ruling in adjudications.for_column("cancer_type").items()}


def run(reconciled: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """Apply stage 3 to stage 2's output. Returns (finalised {value -> code}, values a ruling actually changed).

    Pure and total: every input value appears in the output. The second element is the audit trail — the values
    where the register overrode the pipeline, which is what a run report and the drift gate should surface.
    """
    approved = rulings()
    finalised, overridden = {}, []
    for value, code in reconciled.items():
        ruled = approved.get(value)
        # NB `''` is a legitimate ruling, so test membership rather than truthiness.
        if value in approved and ruled != code:
            finalised[value] = ruled
            overridden.append(value)
        else:
            finalised[value] = code
    return finalised, overridden


def orphaned_rulings(reconciled: dict[str, str]) -> list[str]:
    """Rulings whose value no longer exists in the corpus — dead weight to prune.

    A trial expiring or an extraction changing can strand a ruling. It then sits in the register forever, counted
    against the quality metric, protecting nothing.
    """
    return sorted(set(rulings()) - set(reconciled))
