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
from aus_trial_universe.agentic.core.logfmt import FAIL, PASS, bullet, kv, line
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
from aus_trial_universe.agentic.tools.oncotree import invalid_codes, is_subcode

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
    logger.info("")
    logger.info("oncotree · %d value(s)", len(distinct))
    results = fan_out(
        [(lambda v=v: map_oncotree(client, v, max_attempts=max_attempts, use_reviewer=use_reviewer)) for v in distinct]
    )
    out = {v: r for v, r in zip(distinct, results)}
    _log_doer_reviewer(distinct, results, use_reviewer=use_reviewer, render=lambda r: r.oncotree_code or "?")
    return out


def _log_doer_reviewer(distinct, results, *, use_reviewer, render) -> None:
    """Log a mapping procedure as doer (value → result) then reviewer (verdict per value)."""
    logger.info("")
    logger.info(line("doer"))
    for v, r in zip(distinct, results):
        logger.info(line(f"{v}  →  {render(r)}", indent=8))
    logger.info("")
    logger.info(line("reviewer" if use_reviewer else "reviewer · skipped (--no-review); validator only"))
    for v, r in zip(distinct, results):
        logger.info(line(f"{PASS if r.faithful else FAIL}  {v}", indent=8))
        if not r.faithful and r.problems:
            logger.info(bullet(r.problems[0], indent=12))


_CODE_TOKEN_RE = re.compile(r"[A-Z][A-Z0-9_]+")
_NOT_BODY_RE = re.compile(r"NOT\(([^)]*)\)")
_OR_SPLIT_RE = re.compile(r"\bOR\b")
_KW = {"AND", "OR", "NOT"}
_SENTINEL_NAMES = ("Pan-cancer", "Solid tumour", "Haematological malignancy")


def _oncotree_logic_problems(code_expr: str) -> list[str]:
    """Catch OncoTree logic errors so a mapping is never self-contradictory or redundant.

    Flags: any [None]; within each OR-alternative — a code both included and excluded (X AND NOT(X)),
    a duplicated code (X AND X), a broad sentinel ANDed with a specific code, and a subtype ANDed with
    its OncoTree parent. (A cancer_type conjunction is one OR-group; OR-alternatives are checked apart.)
    """
    problems: list[str] = []
    expr = code_expr or ""
    if "[None]" in expr:
        problems.append("remove [None] — a non-cancer term is not allowed in cancer_type; leave the mapping empty instead")
    for group in _OR_SPLIT_RE.split(expr):
        neg = {t for body in _NOT_BODY_RE.findall(group) for t in _CODE_TOKEN_RE.findall(body)} - _KW
        positive_text = _NOT_BODY_RE.sub("", group)
        pos = [t for t in _CODE_TOKEN_RE.findall(positive_text) if t not in _KW]
        pos_set = set(pos)
        both = sorted(neg & pos_set)
        if both:
            problems.append(f"code(s) both included and excluded: {', '.join(both)} — a mapping cannot be self-contradictory (X AND NOT(X))")
        dups = sorted({t for t in pos if pos.count(t) > 1})
        if dups:
            problems.append(f"duplicate code(s) in a conjunction: {', '.join(dups)} — X AND X = X, list each once")
        pos_sentinels = [s for s in _SENTINEL_NAMES if s in positive_text]
        if pos_sentinels and pos_set:
            problems.append(
                f"broad term(s) [{', '.join(pos_sentinels)}] ANDed with specific code(s) [{', '.join(sorted(pos_set))}] "
                "— a broad type and its subtype are OR-alternatives, not AND; drop the broad term or use OR")
        subset = sorted({f"{a}⊂{b}" for a in pos_set for b in pos_set if a != b and is_subcode(a, b)})
        if subset:
            problems.append(f"subtype ANDed with its parent ({', '.join(subset)}) — use OR or keep only the intended type, not AND")
    return list(dict.fromkeys(problems))


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
    logger.info("")
    logger.info("%s · %d value(s)", label, len(distinct))
    results = fan_out([
        (lambda v=v: _map_finding_model(client, v, build_mapper, build_reviewer,
                                        max_attempts=max_attempts, use_reviewer=use_reviewer))
        for v in distinct
    ])
    out = {v: r for v, r in zip(distinct, results)}
    _log_doer_reviewer(distinct, results, use_reviewer=use_reviewer, render=lambda r: r.finding_model or "?")
    return out


def map_gene_alterations(client: LlmClient, cells: list[str], *, max_attempts: int = 3, use_reviewer: bool = True) -> dict[str, FindingModelResult]:
    return _map_column(client, cells, build_gene_alteration_mapper, build_gene_alteration_reviewer,
                       "gene→fm", max_attempts=max_attempts, use_reviewer=use_reviewer)


def map_molecular_signatures(client: LlmClient, cells: list[str], *, max_attempts: int = 3, use_reviewer: bool = True) -> dict[str, FindingModelResult]:
    return _map_column(client, cells, build_molecular_signature_mapper, build_molecular_signature_reviewer,
                       "sig→fm", max_attempts=max_attempts, use_reviewer=use_reviewer)


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
    tga_detail: str = ""
    pbs_detail: str = ""
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
    logger.info("")
    logger.info("doer")
    logger.info(line(f"web search · {len(drugs)} drug(s): {drug_line}"))

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
    logger.info("")
    if reviewer is not None:
        logger.info("reviewer · %s", PASS if result.ok else FAIL)
        if not result.ok:
            for p in result.problems:
                logger.info(bullet(p))
    else:
        logger.info("reviewer · skipped (--no-review)")
    logger.info("")
    logger.info("result")
    for lbl, val in (("main_drugs", d.main_drugs), ("auxiliary_drugs", d.auxiliary_drugs),
                     ("pottr_drug_class", d.pottr_drug_class), ("drug_class", d.drug_class),
                     ("tga_status", d.tga_status), ("pbs_status", d.pbs_status),
                     ("tga_detail", d.tga_detail), ("pbs_detail", d.pbs_detail)):
        logger.info(kv(lbl, val.strip() or "(none)", indent=4, pad=18))
    return DrugCurationResult(
        main_drugs=d.main_drugs.strip(), auxiliary_drugs=d.auxiliary_drugs.strip(),
        pottr_drug_class=d.pottr_drug_class.strip(), drug_class=d.drug_class.strip(),
        tga_status=d.tga_status.strip(), pbs_status=d.pbs_status.strip(),
        tga_detail=d.tga_detail.strip(), pbs_detail=d.pbs_detail.strip(),
        faithful=result.ok, attempts=result.attempts, problems=result.problems,
    )
