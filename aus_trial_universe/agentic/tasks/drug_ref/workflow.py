"""Drug-reference build workflow (spec §6.1).

Per-drug, trial-independent, incremental. For a list of raw drug names:
  Stage 1  canonicalize each NEW raw name -> alias (raw -> canonical_id); seed the canonical identity.
  Stage 2+3  for each canonical that is NEW or STALE, run annotate + approvals (in parallel across
             drugs), then write drug_ref + drug_indication rows.
An existing, non-stale canonical is a pure LOOKUP (no LLM) — the reuse / speed win.

Plain-Python orchestration; the LLM only fills the doer/reviewer steps (each on a bounded refine loop).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from aus_trial_universe.agentic.core.client import LlmClient
from aus_trial_universe.agentic.core.logfmt import FAIL, PASS, kv, line, stage
from aus_trial_universe.agentic.core.workflow import CheckResult, fan_out, refine
from aus_trial_universe.agentic.tasks.drug_ref.agents import (
    build_annotator,
    build_annotator_reviewer,
    build_approval_agent,
    build_approval_reviewer,
    build_canonicalizer,
    build_canonicalizer_reviewer,
)
from aus_trial_universe.agentic.tasks.drug_ref.schema import (
    APPROVED,
    NOT_APPROVED,
    UNKNOWN,
    ApprovalByIndication,
    Canonicalization,
    DrugAnnotation,
    DrugIndication,
    DrugRef,
    canonical_id_for,
)
from aus_trial_universe.agentic.tasks.drug_ref.store import DrugRefStore

logger = logging.getLogger(__name__)

_VALID_STATUS = {APPROVED, NOT_APPROVED, UNKNOWN}


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
        if feedback:
            prompt += f"\n\n[Reviewer feedback — fix these]:\n{feedback}"
        return doer(prompt)

    def check(c: Canonicalization) -> CheckResult:
        if c.rxcui and not c.rxcui.strip().isdigit():
            return CheckResult(ok=False, problems=[f"rxcui must be numeric or empty, got {c.rxcui!r}"])
        if reviewer is not None:
            v = reviewer(
                f"RAW name: {raw_name}\n\nPROPOSED canonical_name={c.canonical_name!r}; rxcui={c.rxcui!r}; "
                f"aliases={c.aliases}; is_investigational={c.is_investigational}; notes={c.notes!r}"
            )
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
        if feedback:
            prompt += f"\n\n[Reviewer feedback — fix these]:\n{feedback}"
        return doer(prompt)

    def check(a: DrugAnnotation) -> CheckResult:
        if reviewer is not None:
            v = reviewer(
                f"Canonical drug: {canonical_name}\n\nPROPOSED modality={a.modality!r}; mechanism={a.mechanism!r}; "
                f"drug_class={a.drug_class!r}; pottr_drug_class={a.pottr_drug_class!r}; atc_code={a.atc_code!r}; "
                f"fda_status={a.fda_status!r}; ema_status={a.ema_status!r}; sources={a.sources!r}"
            )
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
        if feedback:
            prompt += f"\n\n[Reviewer feedback — fix these]:\n{feedback}"
        return doer(prompt)

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
# Incremental build orchestrator
# --------------------------------------------------------------------------- #
@dataclass
class BuildSummary:
    canonicalized: int = 0   # raw names newly canonicalized (LLM)
    reused_alias: int = 0    # raw names already known (lookup)
    researched: int = 0      # canonicals newly annotated + approvals (LLM)
    reused_ref: int = 0      # canonicals already researched & fresh (lookup)
    non_drug: int = 0        # raw values resolved to non-drugs (procedure/placebo)
    problems: list[str] = field(default_factory=list)


def _to_ref(cid: str, cn: str, c: Canonicalization | DrugRef, ann: DrugAnnotation, stamp: str) -> DrugRef:
    return DrugRef(
        canonical_id=cid, canonical_name=cn,
        rxcui=c.rxcui, aliases=("; ".join(c.aliases) if isinstance(c, Canonicalization) else c.aliases),
        modality=ann.modality, mechanism=ann.mechanism, drug_class=ann.drug_class,
        pottr_drug_class=ann.pottr_drug_class, atc_code=ann.atc_code,
        fda_status=ann.fda_status, ema_status=ann.ema_status, sources=ann.sources, researched_on=stamp,
    )


def _to_indications(cid: str, appr: ApprovalByIndication, stamp: str) -> list[DrugIndication]:
    return [
        DrugIndication(
            canonical_id=cid, indication_id=str(i + 1), indication_raw=ind.indication_raw,
            cancer_type=ind.cancer_type, biomarker=ind.biomarker, line_of_therapy=ind.line_of_therapy,
            stage=ind.stage, tga_status=ind.tga_status, tga_date=ind.tga_date, tga_evidence_url=ind.tga_evidence_url,
            pbs_status=ind.pbs_status, pbs_date=ind.pbs_date, pbs_evidence_url=ind.pbs_evidence_url,
            researched_on=stamp,
        )
        for i, ind in enumerate(appr.indications)
    ]


def build_drug_ref(
    client: LlmClient,
    raw_names,
    store: DrugRefStore,
    *,
    refresh: bool = False,
    refresh_days: int | None = None,
    use_reviewer: bool = True,
    max_attempts: int = 3,
    today: date | None = None,
) -> BuildSummary:
    """Incrementally add/refresh the given raw drug names in `store` (mutated in place)."""
    stamp = (today or date.today()).isoformat()
    summary = BuildSummary()
    raws = _dedup(raw_names)

    # --- Stage 1: canonicalize new raw names -> aliases (+ seed canonical identity) ---
    to_canon = [r for r in raws if refresh or not store.has_alias(r)]
    summary.reused_alias = len(raws) - len(to_canon)
    logger.info(stage("DRUG-REF · canonicalize"))
    logger.info(line(f"{len(to_canon)} new · {summary.reused_alias} reused"))
    canon_results = fan_out([
        (lambda r=r: (r, canonicalize(client, r, max_attempts=max_attempts, use_reviewer=use_reviewer)))
        for r in to_canon
    ])
    for raw, c in canon_results:
        summary.canonicalized += 1
        cn = c.canonical_name.strip()
        if not cn:
            summary.non_drug += 1
            store.put_alias(raw, "")            # remember it's a non-drug; skip next time
            logger.info(line(f"{raw}  →  (not a drug)", indent=4))
            continue
        cid = canonical_id_for(cn)
        store.put_alias(raw, cid)
        if not store.has_ref(cid):              # seed identity; facts filled in Stage 2/3
            store.put_ref(DrugRef(canonical_id=cid, canonical_name=cn, rxcui=c.rxcui, aliases="; ".join(c.aliases)))
        logger.info(line(f"{raw}  →  {cn}" + (f"  (rxcui {c.rxcui})" if c.rxcui else "  (investigational)"), indent=4))

    # --- Stage 2+3: annotate + approvals for each referenced canonical that is new / stale ---
    cids = [cid for cid in dict.fromkeys(store.canonical_for(r) for r in raws) if cid]
    def _needs(cid: str) -> bool:
        r = store.ref(cid)
        if r is None or not r.researched_on:
            return True
        if refresh:
            return True
        return refresh_days is not None and store.is_stale(cid, refresh_days, today=today)
    to_research = [cid for cid in cids if _needs(cid)]
    summary.reused_ref = len(cids) - len(to_research)
    logger.info("")
    logger.info(stage("DRUG-REF · annotate + approvals"))
    logger.info(line(f"{len(to_research)} to research · {summary.reused_ref} reused (fresh)"))

    def _research(cid: str):
        cn = store.ref(cid).canonical_name
        ann = annotate(client, cn, max_attempts=max_attempts, use_reviewer=use_reviewer)
        appr = approvals(client, cn, max_attempts=max_attempts, use_reviewer=use_reviewer)
        return cid, cn, ann, appr

    for cid, cn, ann, appr in fan_out([(lambda c=c: _research(c)) for c in to_research]):
        base = store.ref(cid)
        store.put_ref(_to_ref(cid, cn, base, ann, stamp))
        store.put_indications(cid, _to_indications(cid, appr, stamp))
        summary.researched += 1
        logger.info("")
        logger.info(line(f"{cn}", indent=2))
        logger.info(kv("class", f"{ann.drug_class} · {ann.modality} · {ann.mechanism}".strip(" ·"), indent=4, pad=12))
        logger.info(kv("pottr", ann.pottr_drug_class or "(none)", indent=4, pad=12))
        logger.info(kv("atc", ann.atc_code or "(none)", indent=4, pad=12))
        logger.info(kv("indications", str(len(appr.indications)), indent=4, pad=12))
        for ind in appr.indications:
            bio = f" [{ind.biomarker}]" if ind.biomarker else ""
            logger.info(line(f"· {ind.cancer_type}{bio} — TGA {ind.tga_status}, PBS {ind.pbs_status}", indent=8))

    return summary
