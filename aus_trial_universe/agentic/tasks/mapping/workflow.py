"""Mapping-task workflow (spec §6.2).

OncoTree procedure: for each distinct cancer-type cell, run mapper -> (validate codes +
reviewer) -> bounded refine, then attach oncotree_name / oncotree_code back onto every row.
Plain-Python orchestration; the LLM only fills the mapper/reviewer steps.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from aus_trial_universe.agentic.core.client import LlmClient
from aus_trial_universe.agentic.core.workflow import CheckResult, fan_out, refine
from aus_trial_universe.agentic.tasks.mapping.agents import (
    build_drug_curator,
    build_drug_reviewer,
    build_gene_alteration_mapper,
    build_gene_alteration_reviewer,
    build_molecular_signature_mapper,
    build_molecular_signature_reviewer,
    build_oncotree_mapper,
    build_oncotree_reviewer,
)
from aus_trial_universe.agentic.tasks.mapping.schema import (
    DrugCuration,
    FindingModelMapping,
    OncotreeMapping,
    ReviewVerdict,
)
from aus_trial_universe.agentic.tools.finding_model import finding_model_problems
from aus_trial_universe.agentic.tools.oncotree import invalid_codes

logger = logging.getLogger(__name__)

_PROVENANCE_RE = re.compile(r"\s*\[[^\]]*\]\s*$")


def strip_provenance(cell: str) -> str:
    """'metastatic NSCLC [TITLE; ELIGIBILITY CRITERIA]' -> 'metastatic NSCLC'."""
    return _PROVENANCE_RE.sub("", cell or "").strip()


@dataclass
class OncotreeResult:
    source: str
    oncotree_name: str
    oncotree_code: str
    faithful: bool
    attempts: int
    problems: list[str]


def map_oncotree(
    client: LlmClient, source_expr: str, *, max_attempts: int = 3, use_reviewer: bool = True
) -> OncotreeResult:
    """Map ONE cancer-type expression to OncoTree name+code (mapper -> validate+review -> refine)."""
    mapper = build_oncotree_mapper(client)
    reviewer = build_oncotree_reviewer(client) if use_reviewer else None

    def produce(feedback: str = "") -> OncotreeMapping:
        prompt = source_expr if not feedback else f"{source_expr}\n\n[Reviewer feedback — fix these]:\n{feedback}"
        return mapper(prompt)

    def check(m: OncotreeMapping) -> CheckResult:
        bad = invalid_codes(m.oncotree_code)
        if bad:
            return CheckResult(ok=False, problems=[f"invalid OncoTree code(s): {', '.join(bad)}"])
        logic = _oncotree_logic_problems(m.oncotree_code)
        if logic:
            return CheckResult(ok=False, problems=logic)
        if reviewer is not None:
            v: ReviewVerdict = reviewer(_review_input(source_expr, m))
            if not v.faithful:
                return CheckResult(ok=False, problems=v.problems or ["reviewer flagged the mapping"])
        return CheckResult(ok=True)

    result = refine(
        produce=lambda: produce(""),
        check=check,
        repair=lambda m, problems: produce("\n".join(f"- {p}" for p in problems)),
        max_attempts=max_attempts,
    )
    m = result.value
    return OncotreeResult(
        source=source_expr,
        oncotree_name=m.oncotree_name.strip(),
        oncotree_code=m.oncotree_code.strip(),
        faithful=result.ok,
        attempts=result.attempts,
        problems=result.problems,
    )


def _review_input(source_expr: str, m: OncotreeMapping) -> str:
    return (
        f"SOURCE cancer-type expression:\n{source_expr}\n\n"
        f"PROPOSED oncotree_name: {m.oncotree_name}\n"
        f"PROPOSED oncotree_code: {m.oncotree_code}"
    )


def map_cancer_types(
    client: LlmClient, cancer_cells: list[str], *, max_attempts: int = 3, use_reviewer: bool = True
) -> dict[str, OncotreeResult]:
    """Map the DISTINCT (provenance-stripped) cancer-type values, concurrently.

    Returns {stripped_value -> OncotreeResult}. Empty values are skipped.
    """
    distinct = list(dict.fromkeys(strip_provenance(c) for c in cancer_cells if strip_provenance(c)))
    if not distinct:
        return {}
    logger.info("  ONCOTREE    mapping %d distinct cancer-type value(s)", len(distinct))
    results = fan_out(
        [(lambda v=v: map_oncotree(client, v, max_attempts=max_attempts, use_reviewer=use_reviewer)) for v in distinct]
    )
    out: dict[str, OncotreeResult] = {}
    for v, r in zip(distinct, results):
        out[v] = r
        logger.info("    %-55s -> %s%s", v[:55], r.oncotree_code or "?", "" if r.faithful else "  (unfaithful)")
    return out


_CODE_TOKEN_RE = re.compile(r"[A-Z][A-Z0-9_]+")
_NOT_BODY_RE = re.compile(r"NOT\(([^)]*)\)")
_KW = {"AND", "OR", "NOT"}


def _oncotree_logic_problems(code_expr: str) -> list[str]:
    """Catch the two OncoTree failure modes: [None] noise, and a code both included and excluded."""
    problems: list[str] = []
    if "[None]" in code_expr and code_expr.strip() != "[None]":
        problems.append("remove [None] terms — only a wholly non-cancer term may be [None]; drop subtype exclusions with no OncoTree node")
    neg = {t for body in _NOT_BODY_RE.findall(code_expr) for t in _CODE_TOKEN_RE.findall(body)} - _KW
    pos = set(_CODE_TOKEN_RE.findall(_NOT_BODY_RE.sub("", code_expr))) - _KW
    both = sorted(neg & pos)
    if both:
        problems.append(f"code(s) both included and excluded: {', '.join(both)}")
    return problems


# --------------------------------------------------------------------------- #
# gene_alteration / molecular_signature -> finding-model syntax
# --------------------------------------------------------------------------- #
@dataclass
class FindingModelResult:
    source: str
    finding_model: str
    faithful: bool
    attempts: int
    problems: list[str]


def _map_finding_model(
    client: LlmClient, source_expr: str, build_mapper, build_reviewer, *, max_attempts: int, use_reviewer: bool
) -> FindingModelResult:
    """Generic mapper -> validate(syntax) + reviewer -> refine for one gene/signature expression."""
    mapper = build_mapper(client)
    reviewer = build_reviewer(client) if use_reviewer else None

    def produce(feedback: str = "") -> FindingModelMapping:
        prompt = source_expr if not feedback else f"{source_expr}\n\n[Reviewer feedback — fix these]:\n{feedback}"
        return mapper(prompt)

    def check(m: FindingModelMapping) -> CheckResult:
        probs = finding_model_problems(m.finding_model)
        if probs:
            return CheckResult(ok=False, problems=[f"invalid syntax: {p}" for p in probs])
        if reviewer is not None:
            v: ReviewVerdict = reviewer(f"SOURCE: {source_expr}\n\nPROPOSED finding_model: {m.finding_model}")
            if not v.faithful:
                return CheckResult(ok=False, problems=v.problems or ["reviewer flagged the conversion"])
        return CheckResult(ok=True)

    result = refine(
        produce=lambda: produce(""),
        check=check,
        repair=lambda m, problems: produce("\n".join(f"- {p}" for p in problems)),
        max_attempts=max_attempts,
    )
    return FindingModelResult(
        source=source_expr,
        finding_model=result.value.finding_model.strip(),
        faithful=result.ok,
        attempts=result.attempts,
        problems=result.problems,
    )


def _map_column(client, cells, build_mapper, build_reviewer, label, *, max_attempts, use_reviewer):
    distinct = list(dict.fromkeys(strip_provenance(c) for c in cells if strip_provenance(c)))
    if not distinct:
        return {}
    logger.info("  %-11s mapping %d distinct value(s)", label, len(distinct))
    results = fan_out([
        (lambda v=v: _map_finding_model(client, v, build_mapper, build_reviewer,
                                        max_attempts=max_attempts, use_reviewer=use_reviewer))
        for v in distinct
    ])
    out: dict[str, FindingModelResult] = {}
    for v, r in zip(distinct, results):
        out[v] = r
        logger.info("    %-46s -> %s%s", v[:46], (r.finding_model or "?")[:66], "" if r.faithful else "  (unfaithful)")
    return out


def map_gene_alterations(client: LlmClient, cells: list[str], *, max_attempts: int = 3, use_reviewer: bool = True) -> dict[str, FindingModelResult]:
    return _map_column(client, cells, build_gene_alteration_mapper, build_gene_alteration_reviewer,
                       "GENE->FM", max_attempts=max_attempts, use_reviewer=use_reviewer)


def map_molecular_signatures(client: LlmClient, cells: list[str], *, max_attempts: int = 3, use_reviewer: bool = True) -> dict[str, FindingModelResult]:
    return _map_column(client, cells, build_molecular_signature_mapper, build_molecular_signature_reviewer,
                       "SIG->FM", max_attempts=max_attempts, use_reviewer=use_reviewer)


# --------------------------------------------------------------------------- #
# drug enrichment (trial-level): main/auxiliary + POTTR/general class + TGA/PBS (web search)
# --------------------------------------------------------------------------- #
@dataclass
class DrugCurationResult:
    main_drugs: str = ""
    auxiliary_drugs: str = ""
    pottr_drug_class: str = ""
    drug_class: str = ""
    tga_status: str = ""
    pbs_status: str = ""
    faithful: bool = True
    attempts: int = 0
    problems: list[str] = field(default_factory=list)


def _drug_problems(d: DrugCuration) -> list[str]:
    problems: list[str] = []
    if not d.main_drugs.strip():
        problems.append("main_drugs is empty (name the investigational drug(s)/regimen)")
    if not d.drug_class.strip():
        problems.append("drug_class is empty (give a general class/mechanism for the main drug(s))")
    if not d.tga_status.strip():
        problems.append("tga_status is empty")
    return problems


def curate_drugs(
    client: LlmClient, trial_text: str, drugs: list[str], *, max_attempts: int = 3, use_reviewer: bool = True
) -> DrugCurationResult:
    """Trial-level drug enrichment: main/auxiliary split + POTTR/general class + TGA/PBS (web search)."""
    if not drugs:
        return DrugCurationResult()
    curator = build_drug_curator(client)
    reviewer = build_drug_reviewer(client) if use_reviewer else None
    drug_line = "; ".join(dict.fromkeys(d for d in drugs if d.strip()))
    base = f"{trial_text}\n\nDrugs administered in this trial: {drug_line}"
    logger.info("  DRUG        curating (web search) for %d drug(s): %s", len(drugs), drug_line[:80])

    def produce(feedback: str = "") -> DrugCuration:
        prompt = base if not feedback else f"{base}\n\n[Reviewer feedback — fix these]:\n{feedback}"
        return curator(prompt)

    def check(d: DrugCuration) -> CheckResult:
        problems = _drug_problems(d)
        if problems:
            return CheckResult(ok=False, problems=problems)
        if reviewer is not None:
            v: ReviewVerdict = reviewer(
                f"{base}\n\nPROPOSED: main_drugs={d.main_drugs!r}; auxiliary_drugs={d.auxiliary_drugs!r}; "
                f"pottr_drug_class={d.pottr_drug_class!r}; drug_class={d.drug_class!r}; "
                f"tga_status={d.tga_status!r}; pbs_status={d.pbs_status!r}"
            )
            if not v.faithful:
                return CheckResult(ok=False, problems=v.problems or ["reviewer flagged the drug curation"])
        return CheckResult(ok=True)

    result = refine(
        produce=lambda: produce(""),
        check=check,
        repair=lambda d, problems: produce("\n".join(f"- {p}" for p in problems)),
        max_attempts=max_attempts,
    )
    d = result.value
    logger.info("    main=%s | tga=%s", d.main_drugs[:40], d.tga_status[:40])
    return DrugCurationResult(
        main_drugs=d.main_drugs.strip(), auxiliary_drugs=d.auxiliary_drugs.strip(),
        pottr_drug_class=d.pottr_drug_class.strip(), drug_class=d.drug_class.strip(),
        tga_status=d.tga_status.strip(), pbs_status=d.pbs_status.strip(),
        faithful=result.ok, attempts=result.attempts, problems=result.problems,
    )
