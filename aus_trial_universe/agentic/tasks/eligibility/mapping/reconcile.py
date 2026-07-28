"""Mapping STEP 2 — cross-value reconciliation (the final eligibility-curation step).

Step 1 mapped each DISTINCT value independently, so near-equivalent phrasings can diverge. Step 2 makes
semantically-equivalent inputs share ONE vocabulary value, while keeping genuinely-distinct ones (a real
OncoTree/finding-model-encodable difference) correctly apart:

  detect (deterministic find_inconsistencies) -> pre-pass (name->code repair + OR-order normalise, deterministic)
  -> re-detect -> LLM adjudicator (doer->reviewer) on the remaining SEMANTIC groups -> FINAL value per input.

The 3NF store (current_output/) is READ-ONLY here; Step 2 writes only into the top-level joined/ view dir:
the finalised map-table set (Step-1 cols + a `*_FINAL` col) + the flat finalised_mapped_eligibility.tsv.
"""
from __future__ import annotations

import csv
import logging
from pathlib import Path

from aus_trial_universe.agentic.core.client import LlmClient
from aus_trial_universe.agentic.core.review import review_refine
from aus_trial_universe.agentic.core.workflow import CheckResult, fan_out
from aus_trial_universe.agentic.tasks.eligibility.mapping.agents import (
    build_findingmodel_reconcile_reviewer,
    build_findingmodel_reconciler,
    build_oncotree_reconcile_reviewer,
    build_oncotree_reconciler,
)
from aus_trial_universe.agentic.tasks.eligibility.mapping.schema import GroupReconciliation, ReviewVerdict
from aus_trial_universe.agentic.tasks.eligibility.mapping.workflow import (
    _ESCALATION,
    _oncotree_logic_problems,
    strip_provenance,
)
from aus_trial_universe.agentic.tasks.eligibility.qa.mapping_consistency import find_inconsistencies
from aus_trial_universe.agentic.tasks.eligibility.schema import MAPPED_ELIGIBILITY_COLUMNS
from aus_trial_universe.agentic.tasks.eligibility.tools.finding_model import finding_model_problems
from aus_trial_universe.agentic.tasks.eligibility.tools.oncotree import invalid_codes, name_to_code

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Deterministic pre-pass
# --------------------------------------------------------------------------- #
def _names_by_len_desc() -> list[str]:
    """OncoTree NAMES longest-first (so a longer name is substituted before a shorter one it contains)."""
    return sorted(name_to_code(), key=len, reverse=True)


def repair_oncotree_code(code_expr: str) -> tuple[str, bool, list[str]]:
    """Fix a cancer_type code expression where the mapper leaked a NAME into the code field (a faithful=False
    residual). Only runs when the expression already has invalid code tokens. Substitutes each OncoTree NAME found
    with its CODE (longest name first). Returns (repaired_expr, changed, still_invalid_tokens)."""
    if not code_expr or not invalid_codes(code_expr):
        return code_expr, False, []
    n2c = name_to_code()
    repaired = code_expr
    for name in _names_by_len_desc():
        if name in repaired:
            repaired = repaired.replace(name, n2c[name])
    return repaired, repaired != code_expr, invalid_codes(repaired)


def normalize_or_order(expr: str) -> str:
    """Canonicalise a FLAT top-level OR of simple codes by sorting its branches (so 'B OR A' == 'A OR B'); leaves
    anything with AND / NOT() / parentheses untouched (order there is not free to reorder)."""
    if not expr or " AND " in expr or "NOT(" in expr or "(" in expr:
        return expr
    parts = [p.strip() for p in expr.split(" OR ") if p.strip()]
    return " OR ".join(sorted(parts)) if len(parts) > 1 else expr


# --------------------------------------------------------------------------- #
# LLM adjudicator (one flagged group)
# --------------------------------------------------------------------------- #
def _render_group(members: list[tuple[str, str]]) -> str:
    lines = ["GROUP (a consistency check flagged these as ONE concept; reconcile their mappings):"]
    lines += [f'  - "{v}"  ->  {code or "(empty)"}' for v, code in members]
    lines.append("\nReturn the FINAL value for EACH input above, exactly once.")
    return "\n".join(lines)


def adjudicate_group(
    client: LlmClient, members: list[tuple[str, str]], build_doer, build_reviewer, *,
    is_oncotree: bool, max_attempts: int, use_reviewer: bool,
) -> dict[str, str]:
    """Adjudicate ONE flagged group via doer->reviewer->refine. Returns {input -> FINAL value}."""
    doer = build_doer(client)
    reviewer = build_reviewer(client) if use_reviewer else None
    inputs = sorted(v for v, _ in members)
    base = _render_group(members)

    def produce(feedback: str = "", prior: GroupReconciliation | None = None) -> GroupReconciliation:
        if not feedback:
            return doer(base)
        prior_txt = ""
        if prior is not None:
            prior_txt = "\n\n[Your previous decision]:\n" + "\n".join(
                f'  "{m.input}" -> {m.final_value}' for m in prior.members)
        return doer(f"{base}{prior_txt}\n\n[Reviewer feedback — fix ONLY these]:\n{feedback}")

    def check(cand: GroupReconciliation, escalate: bool = False) -> CheckResult:
        problems: list[str] = []
        if sorted(m.input for m in cand.members) != inputs:
            problems.append("must return EXACTLY the group's inputs, once each")
        for m in cand.members:
            if is_oncotree:
                bad = invalid_codes(m.final_value)
                if bad:
                    problems.append(f'"{m.input[:40]}": invalid code(s) {bad}')
                logic = _oncotree_logic_problems(m.final_value)
                if logic:
                    problems.append(f'"{m.input[:40]}": {logic[0]}')
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
                return CheckResult(ok=False, problems=probs)
        return CheckResult(ok=True)

    result = review_refine(produce, check, max_attempts=max_attempts, escalate=use_reviewer)
    out = {m.input: m.final_value for m in result.value.members}
    # keep any input the model dropped at its pre-adjudication value (safety)
    return {v: out.get(v, code) for v, code in members}


# --------------------------------------------------------------------------- #
# Per-column reconciliation
# --------------------------------------------------------------------------- #
def reconcile_column(
    client: LlmClient, mapping: dict[str, str], *, is_oncotree: bool, workers: int, max_attempts: int,
    use_reviewer: bool,
) -> tuple[dict[str, str], list[tuple[str, str, list[str]]], int]:
    """Reconcile one column's {value -> Step-1 code}. Returns (final {value -> FINAL code}, unresolved-repairs,
    n_groups_adjudicated)."""
    build_doer = build_oncotree_reconciler if is_oncotree else build_findingmodel_reconciler
    build_reviewer = build_oncotree_reconcile_reviewer if is_oncotree else build_findingmodel_reconcile_reviewer

    # 1. deterministic per-value pre-pass (repair leaked names + normalise OR order)
    repaired: dict[str, str] = {}
    unresolved: list[tuple[str, str, list[str]]] = []
    for value, code in mapping.items():
        c = code
        if is_oncotree:
            c, _changed, unres = repair_oncotree_code(c)
            if unres:
                unresolved.append((value, code, unres))
            c = normalize_or_order(c)
        repaired[value] = c

    # 2. detect inconsistent groups on the repaired codes
    groups = find_inconsistencies(repaired)   # {canonical_key: {code: [values]}}
    final = dict(repaired)

    # 3. LLM-adjudicate each remaining group (concurrent)
    group_members = [[(v, repaired[v]) for _c, vs in codes.items() for v in vs] for codes in groups.values()]
    if group_members:
        verdicts = fan_out(
            [(lambda m=m: adjudicate_group(client, m, build_doer, build_reviewer, is_oncotree=is_oncotree,
                                           max_attempts=max_attempts, use_reviewer=use_reviewer))
             for m in group_members],
            max_workers=workers,
        )
        for verdict in verdicts:
            final.update(verdict)
    return final, unresolved, len(group_members)


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
    """The finalised map-table SET (still 3NF single-key lookups) — each = the Step-1 map columns + an appended
    `*_FINAL` column. Written into the 3NF store dir (current_output/) alongside the Step-1 maps, NOT joined/."""
    from aus_trial_universe.agentic.core.paths import FINALISED_MAP_FILES
    _write_tsv(Path(maps_dir) / FINALISED_MAP_FILES["cancer_type_map"],
               ["cancer_type", "oncotree_name", "oncotree_code", "oncotree_code_FINAL"],
               [{"cancer_type": v, "oncotree_name": m.oncotree_name, "oncotree_code": m.oncotree_code,
                 "oncotree_code_FINAL": ct_final.get(v, m.oncotree_code)} for v, m in store.cancer_map.items()])
    _write_tsv(Path(maps_dir) / FINALISED_MAP_FILES["gene_alteration_map"],
               ["gene_alteration", "finding_model", "finding_model_FINAL"],
               [{"gene_alteration": v, "finding_model": m.finding_model,
                 "finding_model_FINAL": ga_final.get(v, m.finding_model)} for v, m in store.gene_map.items()])
    _write_tsv(Path(maps_dir) / FINALISED_MAP_FILES["molecular_signature_map"],
               ["molecular_signature", "finding_model", "finding_model_FINAL"],
               [{"molecular_signature": v, "finding_model": m.finding_model,
                 "finding_model_FINAL": sig_final.get(v, m.finding_model)} for v, m in store.signature_map.items()])


def write_finalised_mapped_eligibility(store, joined_dir: Path, ct_final, ga_final, sig_final) -> None:
    """The flat Step-2 view: mapped_eligibility columns + the reconciled `*_FINAL` values appended per row."""
    from aus_trial_universe.agentic.core.paths import FINALISED_MAPPED_ELIGIBILITY_FILE
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
                "oncotree_name": ct.oncotree_name if ct else "",
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
