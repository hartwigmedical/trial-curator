"""Drug-reference build workflow (spec §6.1).

Per-drug, trial-independent, incremental. For a list of raw drug names:
  Stage 1   canonicalize each NEW raw name (LLM judgement) -> alias; resolve rxcui deterministically (RxNorm);
            seed the canonical identity.
  Stage 2+3 for each canonical that is NEW or STALE: annotate + approvals (LLM, in parallel across drugs),
            plus deterministic pottr_drug_class (POTTR) + atc_code (RxNorm/ATC); write drug_ref + drug_target +
            drug_indication. Processed in BATCHES with a checkpoint save after each (resilient to interruption).
An existing, non-stale canonical is a pure LOOKUP (no LLM) — the reuse / speed win.

Plain-Python orchestration; the LLM fills only the judgement steps (each on a bounded refine loop); the
deterministic facts (rxcui / pottr / atc) come from rxnorm.py / pottr.py.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from aus_trial_universe.core.client import LlmClient
from aus_trial_universe.core.logfmt import kv, line, stage
from aus_trial_universe.core.review import review_refine
from aus_trial_universe.core.workflow import CheckResult, run_parallel
from aus_trial_universe.tasks.drug_utility import pottr, rxnorm
from aus_trial_universe.tasks.drug_utility.agents import (
    build_annotator,
    build_annotator_reviewer,
    build_approval_agent,
    build_approval_reviewer,
    build_canonicalizer,
    build_canonicalizer_reviewer,
    build_role_classifier,
    build_role_reviewer,
)
from aus_trial_universe.tasks.drug_utility.schema import (
    MODALITIES,
    ROLES,
    UNKNOWN,
    APPROVED,
    NOT_APPROVED,
    ApprovalByIndication,
    Canonicalization,
    DrugAnnotation,
    DrugRegulatoryApproval,
    DrugAnnotationsCore,
    DrugTargetAction,
    TrialArmDrugRole,
    canonical_id_for,
)
from aus_trial_universe.tasks.drug_utility.store import DrugRefStore

logger = logging.getLogger(__name__)

_VALID_STATUS = {APPROVED, NOT_APPROVED, UNKNOWN}

# A canonical_name still holding a multi-drug join word means the combination was NOT split (deterministic
# backstop to the reviewer). No single-ingredient INN contains these; a fixed-dose combo (which SHOULD split)
# does, so flagging them is correct. Word-boundaried so "and"/"or"/"plus" inside an INN (e.g. "sorafenib") are safe.
_COMBO_LEFTOVER = re.compile(r"\+|/|;|&|\bor\b|\band\b|\bplus\b", re.I)


def _dedup(names) -> list[str]:
    return list(dict.fromkeys(n.strip() for n in names if n and n.strip()))


# --------------------------------------------------------------------------- #
# Stage functions (each: doer -> validate + reviewer -> bounded refine)
# --------------------------------------------------------------------------- #
def canonicalize(client: LlmClient, raw_name: str, *, max_attempts: int = 3, use_reviewer: bool = True) -> Canonicalization:
    doer = build_canonicalizer(client)
    reviewer = build_canonicalizer_reviewer(client) if use_reviewer else None

    def produce(feedback: str = "", prior=None) -> Canonicalization:
        prompt = f"Raw drug name: {raw_name}"
        return doer(prompt if not feedback else f"{prompt}\n\n[Reviewer feedback — fix these]:\n{feedback}")

    def check(c: Canonicalization, escalate: bool = False) -> CheckResult:
        leftover = [comp.canonical_name for comp in c.components if _COMBO_LEFTOVER.search(comp.canonical_name)]
        if leftover:
            return CheckResult(ok=False, problems=[
                f"component {n!r} still contains a combination join word — split into standalone drugs" for n in leftover])
        if reviewer is not None:
            comps = "; ".join(f"{comp.canonical_name!r}(investigational={comp.is_investigational}, "
                              f"aliases={comp.aliases})" for comp in c.components) or "(none — not a drug)"
            v = reviewer(f"RAW name: {raw_name}\n\nPROPOSED components: {comps}\nnotes={c.notes!r}")
            if not v.faithful:
                return CheckResult(ok=False, problems=v.problems or ["reviewer flagged the canonicalization"])
        return CheckResult(ok=True)

    return review_refine(produce, check, max_attempts=max_attempts).value


def annotate(client: LlmClient, canonical_name: str, *, max_attempts: int = 3, use_reviewer: bool = True) -> DrugAnnotation:
    doer = build_annotator(client)
    reviewer = build_annotator_reviewer(client) if use_reviewer else None

    def produce(feedback: str = "", prior=None) -> DrugAnnotation:
        prompt = f"Canonical drug: {canonical_name}"
        return doer(prompt if not feedback else f"{prompt}\n\n[Reviewer feedback — fix these]:\n{feedback}")

    def check(a: DrugAnnotation, escalate: bool = False) -> CheckResult:
        if a.modality not in MODALITIES:
            return CheckResult(ok=False, problems=[f"modality must be exactly one of {list(MODALITIES)}, got {a.modality!r}"])
        if reviewer is not None:
            targets = "; ".join(f"{t.target}:{t.action}" + (f"({t.note})" if t.note else "") for t in a.targets)
            v = reviewer(f"Canonical drug: {canonical_name}\n\nPROPOSED modality={a.modality!r}; "
                         f"targets=[{targets}]; drug_class={a.drug_class!r}; fda_status={a.fda_status!r}; "
                         f"ema_status={a.ema_status!r}; sources={a.sources!r}")
            if not v.faithful:
                return CheckResult(ok=False, problems=v.problems or ["reviewer flagged the annotation"])
        return CheckResult(ok=True)

    return review_refine(produce, check, max_attempts=max_attempts).value


def approvals(client: LlmClient, canonical_name: str, *, max_attempts: int = 3, use_reviewer: bool = True) -> ApprovalByIndication:
    doer = build_approval_agent(client)
    reviewer = build_approval_reviewer(client) if use_reviewer else None

    def produce(feedback: str = "", prior=None) -> ApprovalByIndication:
        prompt = f"Canonical drug: {canonical_name}"
        return doer(prompt if not feedback else f"{prompt}\n\n[Reviewer feedback — fix these]:\n{feedback}")

    def check(a: ApprovalByIndication, escalate: bool = False) -> CheckResult:
        bad = [f"{lbl}={s!r} not in approved/not_approved/unknown"
               for ind in a.indications
               for s, lbl in ((ind.tga_status, "tga_status"), (ind.pbs_status, "pbs_status"))
               if s and s not in _VALID_STATUS]
        if bad:
            return CheckResult(ok=False, problems=bad)
        if reviewer is not None:
            proposed = "\n".join(f"- {ind.model_dump()}" for ind in a.indications) or "(none)"
            v = reviewer(f"Canonical drug: {canonical_name}\n\nPROPOSED indications:\n{proposed}")
            if not v.faithful:
                return CheckResult(ok=False, problems=v.problems or ["reviewer flagged the approvals"])
        return CheckResult(ok=True)

    return review_refine(produce, check, max_attempts=max_attempts).value


# --------------------------------------------------------------------------- #
# Row builders
# --------------------------------------------------------------------------- #
def _to_ref(cid: str, cn: str, seed: DrugAnnotationsCore, ann: DrugAnnotation, pottr_cls: str, atc: str, stamp: str) -> DrugAnnotationsCore:
    return DrugAnnotationsCore(
        canonical_id=cid, canonical_name=cn, rxcui=seed.rxcui, aliases=seed.aliases,
        modality=ann.modality, drug_class=ann.drug_class,
        pottr_drug_class=pottr_cls, atc_code=atc,          # deterministic lookups
        fda_status=ann.fda_status, ema_status=ann.ema_status, sources=ann.sources, researched_on=stamp,
    )


def _to_targets(cid: str, ann: DrugAnnotation) -> list[DrugTargetAction]:
    return [DrugTargetAction(canonical_id=cid, target=t.target.strip(), action=t.action.strip(), note=t.note.strip())
            for t in ann.targets if t.target.strip()]


def _clean_pp(value: str) -> str:
    """Drop the uninformative generic 'patients' population value (spec §6.1 polish)."""
    v = (value or "").strip()
    return "" if v.lower() == "patients" else v


def _to_indications(cid: str, appr: ApprovalByIndication, stamp: str) -> list[DrugRegulatoryApproval]:
    return [
        DrugRegulatoryApproval(
            canonical_id=cid, indication_id=str(i + 1), indication_raw=ind.indication_raw,
            cancer_type=ind.cancer_type, biomarker=ind.biomarker, stage=ind.stage,
            line_of_therapy=ind.line_of_therapy, prior_therapy=ind.prior_therapy,
            combination=ind.combination, setting=ind.setting, patient_population=_clean_pp(ind.patient_population),
            tga_status=ind.tga_status, tga_date=ind.tga_date, tga_evidence_url=ind.tga_evidence_url,
            pbs_status=ind.pbs_status, pbs_date=ind.pbs_date, pbs_evidence_url=ind.pbs_evidence_url,
            researched_on=stamp,
        )
        for i, ind in enumerate(appr.indications)
    ]


# --------------------------------------------------------------------------- #
# Incremental build orchestrator
# --------------------------------------------------------------------------- #
@dataclass
class BuildSummary:
    canonicalized: int = 0
    reused_alias: int = 0
    researched: int = 0
    reused_ref: int = 0
    non_drug: int = 0
    failed: int = 0          # drugs skipped after an unrecoverable error (run continues)
    problems: list[str] = field(default_factory=list)


def _needs(cid: str, store: DrugRefStore, refresh: bool, refresh_days: int | None, today: date | None) -> bool:
    r = store.ref(cid)
    if r is None or not r.researched_on or refresh:
        return True
    return refresh_days is not None and store.is_stale(cid, refresh_days, today=today)


def build_drug_ref(
    client: LlmClient,
    raw_names,
    store: DrugRefStore,
    *,
    occurrences=None,
    refresh: bool = False,
    refresh_days: int | None = None,
    use_reviewer: bool = True,
    max_attempts: int = 3,
    today: date | None = None,
    workers: int = 8,
    checkpoint: Callable[[], None] | None = None,
) -> BuildSummary:
    """Incrementally add/refresh the given raw drug names in `store` (mutated in place).

    `occurrences`, if given, is an iterable of (trial_arm_id, input_intervention_name) tuples recording which trial
    ARM each input name came from — persisted to the `trial_to_intervention` table (the provenance/traceability
    record; trial_arm_id links to the shared trial_arms registry). Canonicalization is still done once per distinct
    input string and reused across every trial that uses it.
    `workers` sets both the parallelism (fan_out max_workers) and the checkpoint batch size — output is
    identical regardless of `workers`; it only changes throughput (higher may hit API rate limits → retries).
    `checkpoint`, if given, is called after each batch — wire it to store.save() so a long run persists progress
    (an interruption loses at most the in-flight batch)."""
    stamp = (today or date.today()).isoformat()
    summary = BuildSummary()
    raws = _dedup(raw_names)
    for trial_arm_id, name in (occurrences or []):   # provenance (traceability) — deterministic
        store.add_occurrence(trial_arm_id, name)

    # --- Stage 1: canonicalize (LLM judgement) -> mapping + deterministic rxcui + seeded identity ---
    # Parallel across drugs; each finished drug is applied + CHECKPOINTED straightaway (run_parallel per-item sink),
    # so a crash / lost connection loses only the still-running drugs, never a finished one.
    to_canon = [r for r in raws if refresh or not store.has_mapping(r)]
    summary.reused_alias = len(raws) - len(to_canon)
    logger.info(stage("DRUG-REF · canonicalize"))
    logger.info(line(f"{len(to_canon)} new · {summary.reused_alias} reused · workers={workers} · per-drug checkpoint"))

    def _canon_sink(raw, c, exc):
        if exc is not None or c is None:
            summary.failed += 1
            logger.info(line(f"FAILED · canonicalize {raw} · {type(exc).__name__ if exc else 'none'}", indent=4))
            return
        summary.canonicalized += 1
        comps = [comp for comp in c.components if comp.canonical_name.strip()]
        if not comps:                                        # a non-drug (procedure / placebo / …)
            summary.non_drug += 1
            store.set_mapping(raw, [])                        # record input as processed-but-not-a-drug (skip next run)
            logger.info(line(f"{raw}  →  (not a drug)", indent=4))
        else:
            pairs: list[tuple[str, str]] = []                 # (raw_name_to_map, canonical_id) per component
            for comp in comps:                                # 1 input -> N canonicals (combination split into atoms)
                cn = comp.canonical_name.strip()
                rxcui = rxnorm.resolve_rxcui(cn)              # DETERMINISTIC
                cid = canonical_id_for(cn, rxcui)
                pairs.append((comp.raw_name_to_map, cid))
                if not store.has_ref(cid):
                    store.put_ref(DrugAnnotationsCore(canonical_id=cid, canonical_name=cn, rxcui=rxcui,
                                          aliases=" | ".join(comp.aliases)))
                elif comp.aliases:   # already seeded by another raw name — UNION the aliases (order-independent,
                    ref = store.ref(cid)                      # so parallel completion order doesn't change output)
                    have = [a.strip() for a in ref.aliases.split("|") if a.strip()]
                    ref.aliases = " | ".join(have + [a.strip() for a in comp.aliases
                                                     if a.strip() and a.strip() not in have])
            store.set_mapping(raw, pairs)
            logger.info(line(f"{raw}  →  {' + '.join(comp.canonical_name.strip() for comp in comps)}", indent=4))
        if checkpoint:
            checkpoint()

    run_parallel(to_canon,
                 lambda r: canonicalize(client, r, max_attempts=max_attempts, use_reviewer=use_reviewer),
                 _canon_sink, max_workers=workers)

    # --- Stage 2+3: annotate + approvals (LLM) + deterministic pottr/atc; parallel, per-drug checkpoint ---
    cids = list(dict.fromkeys(cid for r in raws for cid in store.canonical_ids_for(r)))
    to_research = [cid for cid in cids if _needs(cid, store, refresh, refresh_days, today)]
    summary.reused_ref = len(cids) - len(to_research)
    logger.info("")
    logger.info(stage("DRUG-REF · annotate + approvals"))
    logger.info(line(f"{len(to_research)} to research · {summary.reused_ref} reused (fresh) · workers={workers} · "
                     f"per-drug checkpoint"))

    def _research(cid: str):
        cn = store.ref(cid).canonical_name
        ann = annotate(client, cn, max_attempts=max_attempts, use_reviewer=use_reviewer)
        appr = approvals(client, cn, max_attempts=max_attempts, use_reviewer=use_reviewer)
        return cid, cn, ann, appr

    def _research_sink(cid, res, exc):
        if exc is not None or res is None:
            summary.failed += 1
            logger.info(line(f"FAILED · research {cid} · {type(exc).__name__ if exc else 'none'}", indent=4))
            return
        _cid, cn, ann, appr = res
        base = store.ref(cid)
        # POTTR: try the canonical name then each alias in order (POTTR's spelling may differ from ours)
        pottr_cls = pottr.pottr_class_for_any([cn, *(a.strip() for a in base.aliases.split("|") if a.strip())])
        store.put_ref(_to_ref(cid, cn, base, ann, pottr_cls, rxnorm.atc_code_for(cn), stamp))
        store.put_targets(cid, _to_targets(cid, ann))
        store.put_indications(cid, _to_indications(cid, appr, stamp))
        summary.researched += 1
        logger.info("")
        logger.info(line(f"{cn}", indent=2))
        logger.info(kv("modality", ann.modality, indent=4, pad=12))
        logger.info(kv("targets", "; ".join(f"{t.target}:{t.action}" for t in ann.targets) or "(none)", indent=4, pad=12))
        logger.info(kv("pottr", pottr_cls or "(not in POTTR)", indent=4, pad=12))
        logger.info(kv("atc", rxnorm.atc_code_for(cn) or "(none)", indent=4, pad=12))
        logger.info(kv("indications", str(len(appr.indications)), indent=4, pad=12))
        for ind in appr.indications:
            bio = f" [{ind.biomarker}]" if ind.biomarker else ""
            logger.info(line(f"· {ind.cancer_type}{bio} — TGA {ind.tga_status}, PBS {ind.pbs_status}", indent=8))
        if checkpoint:
            checkpoint()

    run_parallel(to_research, _research, _research_sink, max_workers=workers)
    return summary


# --------------------------------------------------------------------------- #
# Phase 2 — per-arm drug role (main vs auxiliary). Arm-keyed; separate from the drug-keyed build above.
# --------------------------------------------------------------------------- #
@dataclass
class ArmContext:
    """The arm-level metadata only the caller (build) knows, from the trial's Cohorts. The arm's DRUGS are resolved
    from the store (trial_to_intervention ⋈ intervention_to_canonical ⋈ drug_annotations_core), so this stays lean."""

    trial_arm_id: str
    label: str = ""
    arm_type: str = ""
    description: str = ""


@dataclass
class RoleSummary:
    classified: int = 0      # arms freshly classified this run
    reused: int = 0          # arms already classified (lookup-first skip)
    no_drugs: int = 0        # arms with no resolvable canonical drug (no role rows)
    failed: int = 0          # arms skipped after an unrecoverable error (run continues)


def _arm_drugs(store: DrugRefStore, trial_arm_id: str) -> list[tuple[str, str, str, str]]:
    """The arm's distinct canonical drugs as (canonical_id, canonical_name, drug_class, modality), resolved from
    the store: its trial_to_intervention input names -> intervention_to_canonical ids -> drug_annotations_core."""
    inputs = [inp for (taid, inp) in store.occurrences if taid == trial_arm_id]
    drugs: list[tuple[str, str, str, str]] = []
    seen: set[str] = set()
    for inp in inputs:
        for cid in store.canonical_ids_for(inp):
            if cid in seen:
                continue
            seen.add(cid)
            ref = store.ref(cid)
            drugs.append((cid, ref.canonical_name if ref else "", ref.drug_class if ref else "",
                          ref.modality if ref else ""))
    return drugs


def classify_arm_roles(
    client: LlmClient,
    arm_contexts,
    store: DrugRefStore,
    *,
    refresh: bool = False,
    use_reviewer: bool = True,
    max_attempts: int = 3,
    workers: int = 8,
    checkpoint: Callable[[], None] | None = None,
) -> RoleSummary:
    """Assign every drug in each trial ARM a `main`/`auxiliary` role (Phase 2) and write the `trial_arm_drug_role`
    rows into `store` (mutated in place). Arm-keyed doer->reviewer on the shared refine loop; NO web search.

    `arm_contexts` is an iterable of `ArmContext` (arm label / arm_type / description). Each arm's drugs are resolved
    from the store, so an arm with no resolvable canonical drug is skipped (no role rows). Lookup-first: an arm
    already in `store.roles` is reused unless `refresh`. `checkpoint`, if given, is called after each arm (durable)."""
    summary = RoleSummary()
    # Dedup arm contexts by trial_arm_id (a trial's arms are stable; keep the first seen), keep only arms with drugs.
    by_id: dict[str, ArmContext] = {}
    for ctx in arm_contexts:
        if ctx.trial_arm_id and ctx.trial_arm_id not in by_id:
            by_id[ctx.trial_arm_id] = ctx

    todo: list[ArmContext] = []
    for taid, ctx in by_id.items():
        if not _arm_drugs(store, taid):
            summary.no_drugs += 1
            continue
        if not refresh and store.roles_for(taid):
            summary.reused += 1
            continue
        todo.append(ctx)

    logger.info("")
    logger.info(stage("DRUG-REF · classify roles (main/auxiliary)"))
    logger.info(line(f"{len(todo)} arm(s) to classify · {summary.reused} reused · {summary.no_drugs} no-drug · "
                     f"workers={workers} · per-arm checkpoint"))

    doer = build_role_classifier(client)
    reviewer = build_role_reviewer(client) if use_reviewer else None

    def _classify(ctx: ArmContext):
        drugs = _arm_drugs(store, ctx.trial_arm_id)
        expected = {cid for cid, *_ in drugs}
        drug_lines = "\n".join(f"- canonical_id={cid} | name={cn!r} | class={dc!r} | modality={md!r}"
                               for cid, cn, dc, md in drugs)
        base = (f"Arm label: {ctx.label!r}\nArm type: {ctx.arm_type or '(unspecified)'}\n"
                f"Arm description: {ctx.description or '(none)'}\n\nDrugs in this arm:\n{drug_lines}")

        def produce(feedback: str = "", prior=None):
            return doer(base if not feedback else f"{base}\n\n[Reviewer feedback — fix these]:\n{feedback}")

        def check(c, escalate: bool = False) -> CheckResult:
            got = [a.canonical_id for a in c.assignments]
            bad_role = [f"{a.canonical_id}={a.role!r} not in {list(ROLES)}" for a in c.assignments if a.role not in ROLES]
            if bad_role:
                return CheckResult(ok=False, problems=bad_role)
            if set(got) != expected or len(got) != len(expected):
                missing = sorted(expected - set(got))
                extra = sorted(set(got) - expected)
                dup = len(got) != len(set(got))
                probs = []
                if missing:
                    probs.append(f"missing an assignment for canonical_id(s): {missing}")
                if extra:
                    probs.append(f"assigned unknown canonical_id(s) not in this arm: {extra}")
                if dup and not missing and not extra:
                    probs.append("a canonical_id was assigned more than once")
                return CheckResult(ok=False, problems=probs or ["every drug must get exactly one role"])
            if reviewer is not None:
                proposed = "; ".join(f"{a.canonical_id}={a.role}" for a in c.assignments)
                v = reviewer(f"{base}\n\nPROPOSED roles: {proposed}\nnotes={c.notes!r}")
                if not v.faithful:
                    return CheckResult(ok=False, problems=v.problems or ["reviewer flagged the role classification"])
            return CheckResult(ok=True)

        result = review_refine(produce, check, max_attempts=max_attempts).value
        rows = [TrialArmDrugRole(trial_arm_id=ctx.trial_arm_id, canonical_id=a.canonical_id, role=a.role)
                for a in result.assignments]
        return ctx.trial_arm_id, rows

    def _sink(ctx: ArmContext, res, exc):
        if exc is not None or res is None:
            summary.failed += 1
            logger.info(line(f"FAILED · roles {ctx.trial_arm_id} · {type(exc).__name__ if exc else 'none'}", indent=4))
            return
        taid, rows = res
        store.put_roles(taid, rows)
        summary.classified += 1
        main = [r.canonical_id for r in rows if r.role == "main"]
        aux = [r.canonical_id for r in rows if r.role == "auxiliary"]
        logger.info(line(f"{taid}  →  main={len(main)} · auxiliary={len(aux)}", indent=4))
        if checkpoint:
            checkpoint()

    run_parallel(todo, _classify, _sink, max_workers=workers)
    return summary
