"""ONCOTREE MAPPING REFINEMENT — mapping Step 2 (spec `docs/planning/archive/v2_oncotree_correction_spec.md` §5).

Rewritten 2026-08-04. The stage is a sequence of deterministic rewrites and LLM judgements run to convergence,
not the single detect -> pre-pass -> adjudicate step it used to be:

    R0 operand normalisation   deterministic   name / case-variant -> CODE
    R1 structural canonical    deterministic   factor NOT(A) AND NOT(B), sort, dedupe, flatten, strip parens
    R2 logical simplification  deterministic   drop vacuous exclusions, flatten non-whitelisted nested negation
    R3 validation              deterministic   partition clean / warn / error
    R4 per-value repair        LLM             a value's OWN defects -> a corrected expression
    R5 consistency detection   deterministic   group divergent renderings of one concept
    R6 group reconciliation    LLM             adjudicate a group, given each member's defects
    R7 converge                deterministic   re-apply R0-R3 to LLM output, bounded
    R8 derive + write          deterministic   render names FROM codes, write the tables

**R4 is the path the old stage did not have.** It only ever showed the LLM a *group*, so a value broken on its
own with no divergent sibling was never seen by anything that could repair it.

Why the old version could not work: `repair_oncotree_code` was gated on `invalid_codes()`, which is blind to
mixed case, so the name->code repair never fired once; `normalize_or_order` bailed on anything containing
AND / NOT( / (, i.e. on every expression that could actually diverge; and the adjudicator was instructed to
"preserve each member's AND/OR/NOT structure", which is exactly why every structural defect survived Step 2.

The 3NF store is READ-ONLY here — Step 2 only adds the finalised map set and the flat joined view.
"""
from __future__ import annotations

import csv
import logging
from pathlib import Path

from aus_trial_universe.core.workflow import fan_out
from aus_trial_universe.tasks.eligibility.mapping import adjudications
from aus_trial_universe.tasks.eligibility.schema import MAPPED_ELIGIBILITY_COLUMNS
from aus_trial_universe.tasks.eligibility.mapping.cancer_type.vocab import (
    Problem,
    canonical_form,
    expression_problems,
    has_error,
    normalise_code_expression,
    render_name_expression,
)
from aus_trial_universe.tasks.eligibility.mapping.cancer_type.expr import BROADEN_SENTINEL, broadening
from aus_trial_universe.tasks.eligibility.mapping.workflow import strip_provenance
from aus_trial_universe.tasks.eligibility.mapping.finding_model import finding_model_problems

#: The three vocabulary columns stage 2 can reconcile. A discriminator rather than an `is_oncotree` boolean:
#: gene_alteration and molecular_signature are NOT interchangeable once gene_alteration has its own stage 2, so a
#: two-valued flag cannot express the dispatch.
CANCER_TYPE = "cancer_type"
GENE_ALTERATION = "gene_alteration"
MOLECULAR_SIGNATURE = "molecular_signature"
COLUMNS = (CANCER_TYPE, GENE_ALTERATION, MOLECULAR_SIGNATURE)

logger = logging.getLogger(__name__)

#: Below this many values, a caller is not the eligibility corpus the register was written against, so its
#: "orphaned ruling" count is meaningless. The live eligibility corpus is ~5,000 values; the drug approvals are
#: ~385. A threshold rather than a caller flag: the property that matters is "is this the corpus the register
#: describes", and passing a boolean would let a future caller assert that wrongly.
_ORPHAN_REPORT_MIN_VALUES = 1000

# --------------------------------------------------------------------------- #
# R0-R2 — the deterministic rewrite
# --------------------------------------------------------------------------- #
def deterministic_pass(code_expression: str, *, drop_vacuous: bool = True) -> str:
    """R0 -> R1 -> R2. Idempotent: applying it to its own output is a no-op (asserted by test)."""
    return canonical_form(normalise_code_expression(code_expression), drop_vacuous=drop_vacuous)


# --------------------------------------------------------------------------- #
# Per-column driver — the entry point run.py calls
# --------------------------------------------------------------------------- #
def reconcile_column(
    client, mapping: dict[str, str], *, column: str, workers: int, max_attempts: int, use_reviewer: bool,
) -> tuple[dict[str, str], list[tuple[str, str, list[str]]], int]:
    """Refine one column's {value -> Step-1 code}. Returns (final {value -> FINAL}, unresolved, n_groups).

    `unresolved` lists values with an operand that could not be resolved to a real code — the residue for a
    human. It replaces the old name->code repair's failure list.

    ⚠ THERE IS EXACTLY ONE MAPPING PATH PER COLUMN, and every caller takes it — eligibility and the drug
    approvals alike (user, 2026-08-06). The drug side used to pass `three_stage=False` and get a LEGACY
    reconciler with an LLM group adjudicator; that flag and that branch are DELETED. Two logics over one
    vocabulary is a correctness bug, because these maps exist to be compared with each other.

    No column reaches an LLM here. cancer_type and gene_alteration run deterministic canonicalisation, and
    molecular_signature's stage 2 is a pass-through; all three then apply their register. `client`, `workers`,
    `max_attempts` and `use_reviewer` are accepted and IGNORED — kept only so every caller can invoke this the
    same way.
    """
    from aus_trial_universe.tasks.eligibility.mapping.consistency import find_inconsistencies

    # gene_alteration has its OWN stage 2 (finding-model canonical form + concept-level grouping), so it is
    # dispatched rather than squeezed through the OncoTree pipeline below. Until 2026-08-04 the gene column had no
    # stage 2 at all — it short-circuited to `refined[value] = code`, which is why stage-1 output shipped
    # unreconciled. See `mapping/gene_alteration/reconcile.py`.
    # OncoTree mapping is now THREE explicit stages, all owned by `mapping/cancer_type/` (user, 2026-08-06):
    # stage 2 = deterministic canonicalisation, stage 3 = approved rulings. Neither uses an LLM, so this branch
    # makes no API calls at all. The old R4 (per-value LLM repair) and R5/R6 (regex detection + LLM group
    # adjudication) are GONE for cancer_type: R4 had exactly stage 1's information set, and R5/R6 were the source
    # of the cross-value churn — a function of a single value cannot be perturbed by other values arriving.
    if column == CANCER_TYPE:
        from aus_trial_universe.tasks.eligibility.mapping.cancer_type import stage2, stage3
        reconciled, failures = stage2.run(mapping)
        if failures:
            for value, probs in list(failures.items())[:5]:
                logger.error("stage-2 POSTCONDITION failed for %r: %s", value[:70],
                             "; ".join(str(p) for p in probs))
            logger.error("stage 2 produced %d defective value(s) — this is a bug in the rewrite, not the data",
                         len(failures))
        finalised, overridden = stage3.run(reconciled)
        # ORPHAN REPORTING IS ELIGIBILITY-ONLY. The register is keyed by VALUE and shared with the drug approvals
        # (deliberately — a ruling is about a value, not about which side of the join it came from). But the two
        # sides see different value SETS, so from the drug side almost the whole register looks orphaned: it
        # logged "58 ruling(s) no longer match any value" against a 385-value corpus, which reads as a data
        # problem and is nothing of the sort. A prune candidate is only meaningful against the corpus the register
        # was written for.
        if len(reconciled) >= _ORPHAN_REPORT_MIN_VALUES:
            orphans = stage3.orphaned_rulings(reconciled)
            if orphans:
                logger.info("stage 3 · %d ruling(s) match no value in the eligibility corpus (prune candidates)",
                            len(orphans))
        logger.info("cancer_type · stage 2 changed %d · stage 3 overrode %d",
                    sum(1 for v in mapping if mapping[v] != reconciled[v]), len(overridden))
        return finalised, [], 0

    if column == GENE_ALTERATION:
        from aus_trial_universe.tasks.eligibility.mapping.gene_alteration import reconcile as ga_reconcile
        return ga_reconcile.reconcile_column(
            client, mapping, workers=workers, max_attempts=max_attempts, use_reviewer=use_reviewer)

    if column == MOLECULAR_SIGNATURE:
        # STAGE 2 IS A PASS-THROUGH, then stage 3. No LLM, no cross-value work — a function of a single value.
        #
        # Until 2026-08-06 this column fell through to the legacy R0-R8 body below, which would group values by
        # `find_inconsistencies` and hand each group to an LLM adjudicator. That is the same cross-value churn
        # mechanism deleted from cancer_type and gene_alteration: a value with no defect of its own could be
        # rewritten because an unrelated value entered the corpus. It happened never to fire here (0 groups over
        # the 171 live values), which is precisely why it survived unnoticed — an unfired hazard looks identical
        # to no hazard. The column is now explicit about doing nothing rather than accidentally doing nothing.
        #
        # There IS no deterministic work to do yet: over the live corpus the finding-model canonical form changes
        # nothing. The slot exists so a real stage 2 can land here later without touching the plumbing.
        rulings = adjudications.for_column(column)
        finalised = {v: (rulings[v].final if v in rulings else (e or "")) for v, e in mapping.items()}
        logger.info("molecular_signature · stage 2 pass-through · stage 3 overrode %d",
                    sum(1 for v in mapping if v in rulings and rulings[v].final != (mapping[v] or "")))
        return finalised, [], 0

# --------------------------------------------------------------------------- #
# Writers (into the joined/ view dir)
# --------------------------------------------------------------------------- #
def _write_tsv(path: Path, columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, delimiter="\t", lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def write_finalised_maps(store, maps_dir: Path, ct_final, ga_final, sig_final) -> None:
    """Write every column's STAGE TABLES into the 3NF store dir (current_version/), beside the stage-1 maps.

    Each column goes through the one shared `stage_tables` spec, so the file names, the accreting columns and
    cancer_type's derived name column have exactly one definition (user, 2026-08-06: *"the idea is not to have
    duplicate code"*).

    The RECONCILED stage is recomputed here rather than threaded through the call chain: for both three-stage
    columns it is a pure deterministic function of stage 1, so recomputing is free and cannot disagree with what
    stage 3 saw. molecular_signature has no stage 2 at all, so its `finalised` sits directly on `initial`.
    """
    from aus_trial_universe.tasks.eligibility.mapping import stage_tables as ST
    from aus_trial_universe.tasks.eligibility.mapping.cancer_type import stage2 as ct_stage2
    from aus_trial_universe.tasks.eligibility.mapping.gene_alteration.reconcile import run_stage2 as ga_stage2

    ct_initial = {v: m.oncotree_code for v, m in store.cancer_map.items()}
    ct_reconciled, _ = ct_stage2.run(ct_initial)
    ST.CANCER_TYPE.write_all(
        Path(maps_dir), initial=ct_initial, reconciled=ct_reconciled,
        finalised={v: ct_final.get(v, ct_reconciled[v]) for v in ct_initial})

    ga_initial = {v: m.finding_model for v, m in store.gene_map.items()}
    ga_reconciled = {v: o.after_canon for v, o in ga_stage2(ga_initial).items()}
    ST.GENE_ALTERATION.write_all(
        Path(maps_dir), initial=ga_initial, reconciled=ga_reconciled,
        finalised={v: ga_final.get(v, ga_reconciled[v]) for v in ga_initial})

    # molecular_signature's stage 2 is a PASS-THROUGH today — the slot exists so a deterministic stage can be
    # dropped in later without a schema change (user, 2026-08-06). Written explicitly rather than defaulted, so
    # the day it stops being the identity there is one obvious line to change.
    sig_initial = {v: m.finding_model for v, m in store.signature_map.items()}
    sig_reconciled = dict(sig_initial)
    ST.MOLECULAR_SIGNATURE.write_all(
        Path(maps_dir), initial=sig_initial, reconciled=sig_reconciled,
        finalised={v: sig_final.get(v, sig_reconciled[v]) for v in sig_initial})


def write_finalised_mapped_eligibility(store, joined_dir: Path, ct_final, ga_final, sig_final) -> None:
    """The flat Step-2 view: mapped_eligibility columns + the reconciled `*_FINAL` values appended per row."""
    from aus_trial_universe.core.paths import FINALISED_MAPPED_ELIGIBILITY_FILE
    cols = MAPPED_ELIGIBILITY_COLUMNS + [
        "oncotree_code_FINAL", "gene_alteration_findingmodel_FINAL", "molecular_signature_findingmodel_FINAL"]
    out: list[dict] = []
    for rows in store.interpreted.values():
        for e in rows:
            ck = strip_provenance(e.cancer_type_interpreted)
            gk = strip_provenance(e.gene_alteration_interpreted)
            sk = strip_provenance(e.molecular_signature_interpreted)
            ct = store.cancer_map.get(ck)
            ga = store.gene_map.get(gk)
            sig = store.signature_map.get(sk)
            out.append({
                "trial_arm_id": e.trial_arm_id, "conjunction_index": e.conjunction_index,
                "cancer_type_interpreted": e.cancer_type_interpreted,
                "oncotree_name": render_name_expression(ct_final.get(ck, ct.oncotree_code if ct else "")),
                "oncotree_code": ct.oncotree_code if ct else "",
                "gene_alteration_interpreted": e.gene_alteration_interpreted,
                "gene_alteration_findingmodel": ga.finding_model if ga else "",
                "molecular_signature_interpreted": e.molecular_signature_interpreted,
                "molecular_signature_findingmodel": sig.finding_model if sig else "",
                "molecular_biomarker_interpreted": e.molecular_biomarker_interpreted,
                "prior_therapy_interpreted": e.prior_therapy_interpreted,
                "oncotree_code_FINAL": ct_final.get(ck, ct.oncotree_code if ct else ""),
                "gene_alteration_findingmodel_FINAL": ga_final.get(gk, ga.finding_model if ga else ""),
                "molecular_signature_findingmodel_FINAL": sig_final.get(sk, sig.finding_model if sig else ""),
            })
    _write_tsv(Path(joined_dir) / FINALISED_MAPPED_ELIGIBILITY_FILE, cols, out)
