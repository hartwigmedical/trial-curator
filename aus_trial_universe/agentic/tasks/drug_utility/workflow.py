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

from aus_trial_universe.agentic.core.client import LlmClient
from aus_trial_universe.agentic.core.logfmt import kv, line, stage
from aus_trial_universe.agentic.core.workflow import CheckResult, refine, run_parallel
from aus_trial_universe.agentic.tasks.drug_utility import pottr, rxnorm
from aus_trial_universe.agentic.tasks.drug_utility.agents import (
    build_annotator,
    build_annotator_reviewer,
    build_approval_agent,
    build_approval_reviewer,
    build_canonicalizer,
    build_canonicalizer_reviewer,
)
from aus_trial_universe.agentic.tasks.drug_utility.schema import (
    MODALITIES,
    UNKNOWN,
    APPROVED,
    NOT_APPROVED,
    ApprovalByIndication,
    Canonicalization,
    DrugAnnotation,
    DrugRegulatoryApproval,
    DrugAnnotationsCore,
    DrugTargetAction,
    canonical_id_for,
)
from aus_trial_universe.agentic.tasks.drug_utility.store import DrugRefStore

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

    def produce(feedback: str = "") -> Canonicalization:
        prompt = f"Raw drug name: {raw_name}"
        return doer(prompt if not feedback else f"{prompt}\n\n[Reviewer feedback — fix these]:\n{feedback}")

    def check(c: Canonicalization) -> CheckResult:
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

    return refine(produce=lambda: produce(""), check=check,
                  repair=lambda c, probs: produce("\n".join(f"- {p}" for p in probs)),
                  max_attempts=max_attempts).value


def annotate(client: LlmClient, canonical_name: str, *, max_attempts: int = 3, use_reviewer: bool = True) -> DrugAnnotation:
    doer = build_annotator(client)
    reviewer = build_annotator_reviewer(client) if use_reviewer else None

    def produce(feedback: str = "") -> DrugAnnotation:
        prompt = f"Canonical drug: {canonical_name}"
        return doer(prompt if not feedback else f"{prompt}\n\n[Reviewer feedback — fix these]:\n{feedback}")

    def check(a: DrugAnnotation) -> CheckResult:
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

    return refine(produce=lambda: produce(""), check=check,
                  repair=lambda a, probs: produce("\n".join(f"- {p}" for p in probs)),
                  max_attempts=max_attempts).value


def approvals(client: LlmClient, canonical_name: str, *, max_attempts: int = 3, use_reviewer: bool = True) -> ApprovalByIndication:
    doer = build_approval_agent(client)
    reviewer = build_approval_reviewer(client) if use_reviewer else None

    def produce(feedback: str = "") -> ApprovalByIndication:
        prompt = f"Canonical drug: {canonical_name}"
        return doer(prompt if not feedback else f"{prompt}\n\n[Reviewer feedback — fix these]:\n{feedback}")

    def check(a: ApprovalByIndication) -> CheckResult:
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

    return refine(produce=lambda: produce(""), check=check,
                  repair=lambda a, probs: produce("\n".join(f"- {p}" for p in probs)),
                  max_attempts=max_attempts).value


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

    `occurrences`, if given, is an iterable of (trialId, registry, input_intervention_name) tuples recording which
    trial each input name came from — persisted to the `trial_to_intervention` table (the provenance/traceability
    record). Canonicalization is still done once per distinct input string and reused across every trial that uses it.
    `workers` sets both the parallelism (fan_out max_workers) and the checkpoint batch size — output is
    identical regardless of `workers`; it only changes throughput (higher may hit API rate limits → retries).
    `checkpoint`, if given, is called after each batch — wire it to store.save() so a long run persists progress
    (an interruption loses at most the in-flight batch)."""
    stamp = (today or date.today()).isoformat()
    summary = BuildSummary()
    raws = _dedup(raw_names)
    for trial_id, registry, arm, arm_type, name in (occurrences or []):   # provenance (traceability) — deterministic
        store.add_occurrence(trial_id, registry, arm, arm_type, name)

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
