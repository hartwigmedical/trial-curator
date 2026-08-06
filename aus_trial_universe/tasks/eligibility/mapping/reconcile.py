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
from aus_trial_universe.qa import adjudications
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

MAX_CONVERGENCE_PASSES = 3

# --------------------------------------------------------------------------- #
# R0-R2 — the deterministic rewrite
# --------------------------------------------------------------------------- #
def deterministic_pass(code_expression: str, *, drop_vacuous: bool = True) -> str:
    """R0 -> R1 -> R2. Idempotent: applying it to its own output is a no-op (asserted by test)."""
    return canonical_form(normalise_code_expression(code_expression), drop_vacuous=drop_vacuous)


# --------------------------------------------------------------------------- #
# R4 / R6 — LLM hooks (Phase 1)
# --------------------------------------------------------------------------- #
def repair_value(client, cancer_type: str, code_expression: str, problems: list[Problem], *,
                 max_attempts: int = 4, use_reviewer: bool = True, trace=None) -> tuple[str, bool]:
    """R4 — per-value LLM repair, on the shared doer->reviewer harness.

    The doer sees the SOURCE cell, the current expression and its EXACT defects, so it repairs rather than
    re-derives. Returns (expression, ok). On failure the ORIGINAL is returned untouched — a repair that cannot be
    validated must never be shipped in place of a known-bad value we can still see.
    """
    from aus_trial_universe.core.review import review_refine
    from aus_trial_universe.core.workflow import CheckResult
    from aus_trial_universe.tasks.eligibility.mapping.cancer_type.agents import (
        build_oncotree_repair_reviewer,
        build_oncotree_repairer,
    )
    from aus_trial_universe.tasks.eligibility.mapping.schema import OncotreeRepair, ReviewVerdict

    doer = build_oncotree_repairer(client)
    reviewer = build_oncotree_repair_reviewer(client) if use_reviewer else None
    defect_lines = "\n".join(f"  - {p}" for p in problems) or "  - (none reported)"
    base = (f"SOURCE cancer-type wording:\n{cancer_type}\n\n"
            f"CURRENT oncotree_code:\n{code_expression}\n\n"
            f"DEFECTS the validator reported:\n{defect_lines}\n\n"
            f"Return the corrected oncotree_code.")

    _say = trace or (lambda _t: None)
    _say("  R4 repair  defects: " + "; ".join(str(p) for p in problems))

    def produce(feedback: str = "", prior: OncotreeRepair | None = None) -> OncotreeRepair:
        if not feedback:
            out = doer(base)
        else:
            prior_txt = f"\n\n[Your previous repair]:\n{prior.oncotree_code}" if prior is not None else ""
            out = doer(f"{base}{prior_txt}\n\n[Reviewer feedback — fix ONLY these]:\n{feedback}")
        _say(f"    repair doer     {out.oncotree_code or '(empty)'}"
             + (f"   ({out.rationale.strip()})" if (out.rationale or '').strip() else ""))
        return out

    def check(cand: OncotreeRepair, escalate: bool = False) -> CheckResult:
        candidate = deterministic_pass(cand.oncotree_code)
        remaining = expression_problems(candidate)
        if has_error(remaining):
            errs = [str(p) for p in remaining if p.severity == "error"]
            _say("    repair check    FAIL: " + "; ".join(errs))
            return CheckResult(ok=False, problems=errs)
        if reviewer is not None:
            v: ReviewVerdict = reviewer(
                f"{base}\n\nPROPOSED REPAIR:\n{candidate}" + (_ESCALATION if escalate else ""))
            if not v.faithful:
                probs = v.problems or ["reviewer flagged the repair"]
                if escalate and (v.suggested_fix or "").strip():
                    probs = probs + [f"SUGGESTED FIX: {v.suggested_fix.strip()}"]
                _say("    repair review   FAIL: " + "; ".join(probs))
                return CheckResult(ok=False, problems=probs)
            _say("    repair review   ok")
        return CheckResult(ok=True)

    result = review_refine(produce, check, max_attempts=max_attempts, escalate=use_reviewer)
    if not result.ok:
        return code_expression, False
    return deterministic_pass(result.value.oncotree_code), True


_ESCALATION = ("\n\n[ESCALATION-MODE] Earlier attempts did not resolve the problems. In ADDITION to `problems`, "
               "fill `suggested_fix` with the concrete corrected mapping you would expect (the exact value).")


def reconcile_group(client, members: list[tuple[str, str]], problems_by_value: dict[str, list[Problem]], *,
                    column: str = CANCER_TYPE, max_attempts: int = 4, use_reviewer: bool = True,
                    trace=None) -> dict[str, str]:
    """R6 — adjudicate one divergent group, now also given each member's defect list.

    `members` is [(source_value, current_code)]. Returns {source_value -> FINAL code}; a member the model drops
    keeps its pre-adjudication value.
    """
    from aus_trial_universe.core.review import review_refine
    from aus_trial_universe.core.workflow import CheckResult
    from aus_trial_universe.tasks.eligibility.mapping.cancer_type.agents import (
        build_oncotree_reconcile_reviewer,
        build_oncotree_reconciler,
    )
    from aus_trial_universe.tasks.eligibility.mapping.molecular_signature.agents import (
        build_findingmodel_reconcile_reviewer,
        build_findingmodel_reconciler,
    )
    from aus_trial_universe.tasks.eligibility.mapping.schema import GroupReconciliation, ReviewVerdict

    is_oncotree = column == CANCER_TYPE
    build_doer = build_oncotree_reconciler if is_oncotree else build_findingmodel_reconciler
    build_rev = build_oncotree_reconcile_reviewer if is_oncotree else build_findingmodel_reconcile_reviewer
    doer = build_doer(client)
    reviewer = build_rev(client) if use_reviewer else None
    inputs = sorted(v for v, _ in members)

    lines = ["GROUP (a consistency check flagged these as ONE concept; reconcile AND repair their mappings):"]
    for value, code in members:
        lines.append(f'  - "{value}"  ->  {code or "(empty)"}')
        for p in problems_by_value.get(value, []):
            lines.append(f"        defect: {p}")
    lines.append("\nReturn the FINAL value for EACH input above, exactly once.")
    base = "\n".join(lines)

    _say = trace or (lambda _v, _t: None)
    for value, code in members:
        _say(value, f"  R6 group   with {len(members) - 1} sibling(s): "
                    + "; ".join(f'{c or "(empty)"}' for v2, c in members if v2 != value)[:150])

    def produce(feedback: str = "", prior: GroupReconciliation | None = None) -> GroupReconciliation:
        if not feedback:
            out = doer(base)
        else:
            prior_txt = ""
            if prior is not None:
                prior_txt = "\n\n[Your previous decision]:\n" + "\n".join(
                    f'  "{m.input}" -> {m.final_value}' for m in prior.members)
            out = doer(f"{base}{prior_txt}\n\n[Reviewer feedback — fix ONLY these]:\n{feedback}")
        for m in out.members:
            _say(m.input, f"    group doer      {m.final_value or '(empty)'}")
        if (out.rationale or "").strip():
            for value, _c in members:
                _say(value, f"    rationale       {out.rationale.strip()[:200]}")
        return out

    def check(cand: GroupReconciliation, escalate: bool = False) -> CheckResult:
        problems: list[str] = []
        if sorted(m.input for m in cand.members) != inputs:
            problems.append("must return EXACTLY the group's inputs, once each")
        for m in cand.members:
            if is_oncotree:
                for p in expression_problems(deterministic_pass(m.final_value)):
                    if p.severity == "error":
                        problems.append(f'"{m.input[:40]}": {p}')
            else:
                fp = finding_model_problems(m.final_value)
                if fp:
                    problems.append(f'"{m.input[:40]}": {fp[0]}')
        if problems:
            return CheckResult(ok=False, problems=problems)
        if reviewer is not None:
            proposed = "\n\nPROPOSED:\n" + "\n".join(f'  "{m.input}" -> {m.final_value}' for m in cand.members)
            v: ReviewVerdict = reviewer(base + proposed + (_ESCALATION if escalate else ""))
            if not v.faithful:
                probs = v.problems or ["reviewer flagged the reconciliation"]
                if escalate and (v.suggested_fix or "").strip():
                    probs = probs + [f"SUGGESTED FIX: {v.suggested_fix.strip()}"]
                for value, _c in members:
                    _say(value, "    group review    FAIL: " + "; ".join(probs)[:200])
                return CheckResult(ok=False, problems=probs)
            for value, _c in members:
                _say(value, "    group review    ok")
        return CheckResult(ok=True)

    result = review_refine(produce, check, max_attempts=max_attempts, escalate=use_reviewer)
    out = {m.input: (deterministic_pass(m.final_value) if is_oncotree else m.final_value)
           for m in result.value.members}
    return {v: out.get(v, code) for v, code in members}


# --------------------------------------------------------------------------- #
# Per-column driver — the entry point run.py calls
# --------------------------------------------------------------------------- #
def reconcile_column(
    client, mapping: dict[str, str], *, column: str, workers: int, max_attempts: int, use_reviewer: bool,
    three_stage: bool = True,
) -> tuple[dict[str, str], list[tuple[str, str, list[str]]], int]:
    """Refine one column's {value -> Step-1 code}. Returns (final {value -> FINAL}, unresolved, n_groups).

    `unresolved` lists values with an operand that could not be resolved to a real code — the residue for a
    human. It replaces the old name->code repair's failure list.

    `three_stage` selects the ELIGIBILITY cancer_type path: stage 2 (deterministic canonicalisation) + stage 3
    (approved rulings), no LLM. The DRUG path passes `three_stage=False` and keeps the legacy R0-R8 behaviour,
    because its `approval_cancer_type_map` is a different table with its own signed-off values, and the
    2026-08-06 restructure was scoped to eligibility OncoTree mapping only. Changing the drug side silently would
    re-roll data nobody reviewed — which is exactly the accident this flag exists to prevent.
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
    if column == CANCER_TYPE and three_stage:
        from aus_trial_universe.tasks.eligibility.mapping.cancer_type import stage2, stage3
        reconciled, failures = stage2.run(mapping)
        if failures:
            for value, probs in list(failures.items())[:5]:
                logger.error("stage-2 POSTCONDITION failed for %r: %s", value[:70],
                             "; ".join(str(p) for p in probs))
            logger.error("stage 2 produced %d defective value(s) — this is a bug in the rewrite, not the data",
                         len(failures))
        finalised, overridden = stage3.run(reconciled)
        orphans = stage3.orphaned_rulings(reconciled)
        if orphans:
            logger.info("stage 3 · %d ruling(s) no longer match any value (prune candidates)", len(orphans))
        logger.info("cancer_type · stage 2 changed %d · stage 3 overrode %d",
                    sum(1 for v in mapping if mapping[v] != reconciled[v]), len(overridden))
        return finalised, [], 0

    if column == GENE_ALTERATION:
        from aus_trial_universe.tasks.eligibility.mapping.gene_alteration import reconcile as ga_reconcile
        return ga_reconcile.reconcile_column(
            client, mapping, workers=workers, max_attempts=max_attempts, use_reviewer=use_reviewer)

    # ---- R0-R3 -----------------------------------------------------------------
    refined: dict[str, str] = {}
    problems: dict[str, list[Problem]] = {}
    unresolved: list[tuple[str, str, list[str]]] = []
    is_oncotree = column == CANCER_TYPE
    for value, code in mapping.items():
        refined[value] = deterministic_pass(code) if is_oncotree else (code or "")
        problems[value] = expression_problems(refined[value]) if is_oncotree else []
        bad = [p.detail for p in problems[value] if p.defect == "lex_unknown_operand"]
        if bad:
            unresolved.append((value, code, bad))

    # ---- R4: repair every value still carrying an error -------------------------
    if is_oncotree:
        broken = [v for v, ps in problems.items() if has_error(ps)]
        if broken:
            repairs = fan_out([_soft(lambda v=v: repair_value(client, v, refined[v], problems[v],
                                                              max_attempts=max_attempts,
                                                              use_reviewer=use_reviewer), (refined[v], False))
                               for v in broken], max_workers=workers)
            for value, res in zip(broken, repairs):
                if res:
                    refined[value] = res[0]
                    problems[value] = expression_problems(refined[value])

    # ---- R5/R6: adjudicate divergent groups -------------------------------------
    groups = find_inconsistencies(refined)
    members = [[(v, refined[v]) for _c, vs in codes.items() for v in vs] for codes in groups.values()]
    if members:
        verdicts = fan_out([_soft(lambda m=m: reconcile_group(client, m, problems, column=column,
                                                              max_attempts=max_attempts,
                                                              use_reviewer=use_reviewer), {})
                            for m in members], max_workers=workers)
        rejected = 0
        for verdict in verdicts:
            if not verdict:
                continue
            if is_oncotree:
                # ANTI-BROADENING GUARD, scoped to the kind we are CERTAIN about. Group adjudication is the one
                # place an LLM rewrites a value that had no defect of its own, and it is how the 2026-08-05
                # regressions happened. But it is also how a genuine over-specification is repaired (`IDC` ->
                # `BREAST` for "metastatic breast cancer"), and unifying members that differ in specificity
                # necessarily lands on a parent — so rejecting every broadening would defeat the stage's purpose.
                # Only BROADEN_SENTINEL is rejected: unifying to a sentinel is never the most specific covering
                # code, so it is always wrong. Ancestor-broadening is allowed through and surfaced by the gate as
                # a WARN for review. See expr.broadening for the full argument.
                for value, proposed in list(verdict.items()):
                    found = broadening(refined.get(value, ""), proposed)
                    if found and found[0] == BROADEN_SENTINEL:
                        logger.warning("R6 adjudication REJECTED for %r: would broaden to a sentinel — %s",
                                       value[:70], found[1])
                        verdict.pop(value)
                        rejected += 1
            refined.update(verdict)
        if rejected:
            logger.info("R6 · %d adjudication(s) rejected (would broaden to a sentinel); prior mapping kept",
                        rejected)

    # ---- R7: converge (oncotree only), then apply any approved hand-ruling ------
    # The ruling is applied for EVERY column this function handles, not just oncotree: the register is looked up by
    # the `column` discriminator, so molecular_signature gets the hook for free the day it needs one.
    rulings = adjudications.for_column(column)
    for value, code in list(refined.items()):
        if is_oncotree:
            for _ in range(MAX_CONVERGENCE_PASSES):
                nxt = deterministic_pass(code)
                if nxt == code:
                    break
                code = nxt
        ruling = rulings.get(value)
        # NB `""` is a legitimate ruling — test identity, not truthiness.
        refined[value] = ruling.final if ruling is not None else code
    return refined, unresolved, len(members)


def _soft(fn, fallback):
    """One item's failure must not discard the pool: `fan_out` re-raises, so a single call that exhausts its
    retries against a rate-limit storm would throw away every completed value alongside it."""
    def wrapped():
        try:
            return fn()
        except Exception as exc:                     # noqa: BLE001 — deliberate: isolate, record, continue
            logger.warning("refinement item failed, left unchanged: %s", exc)
            return fallback
    return wrapped


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

    sig_initial = {v: m.finding_model for v, m in store.signature_map.items()}
    ST.MOLECULAR_SIGNATURE.write_all(
        Path(maps_dir), initial=sig_initial,
        finalised={v: sig_final.get(v, e) for v, e in sig_initial.items()})


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
