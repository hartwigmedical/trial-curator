"""Slice-1 eligibility-extraction workflow (spec §6, §8).

Deterministic orchestrator: extractor agent -> fan-out OncoTree normalization ->
consolidate into DNF rows -> check (rules + faithfulness judge) -> bounded refine.
Plain Python control flow; the LLM only fills the individual agent steps.
"""
from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass

from aus_trial_universe.agentic.core.client import LlmClient
from aus_trial_universe.agentic.core.workflow import CheckResult, fan_out, refine
from aus_trial_universe.agentic.tasks.eligibility_extraction.agents import (
    build_extractor_agent,
    build_judge_agent,
    build_oncotree_agent,
)
from aus_trial_universe.agentic.tasks.eligibility_extraction.schema import (
    DnfRow,
    EligibilityExtraction,
    JudgeVerdict,
    OncotreeMapping,
)

logger = logging.getLogger(__name__)

TSV_COLUMNS = ["trialId", "cohort", "cancer_type", "oncotree_name", "oncotree_code", "gene_alteration"]


@dataclass
class ExtractionResult:
    rows: list[DnfRow]
    faithful: bool
    attempts: int
    problems: list[str]


def extract_eligibility(
    client: LlmClient,
    *,
    trial_id: str,
    cohort: str,
    source_text: str,
    max_attempts: int = 3,
    use_judge: bool = True,
) -> ExtractionResult:
    """Extract a cohort's eligibility into a DNF table (see module docstring)."""
    extractor = build_extractor_agent(client)
    oncotree = build_oncotree_agent(client)
    judge = build_judge_agent(client) if use_judge else None

    def produce(feedback: str = "") -> list[DnfRow]:
        prompt = source_text
        if feedback:
            prompt = f"{source_text}\n\n[Reviewer feedback — fix these issues]:\n{feedback}"
            logger.info("  re-extracting with reviewer feedback")
        extraction: EligibilityExtraction = extractor(prompt)
        logger.info("  extractor: %d candidate row(s)", len(extraction.rows))

        # Fan out OncoTree normalization over the distinct cancer-type strings.
        distinct = list(dict.fromkeys(r.cancer_type for r in extraction.rows if r.cancer_type.strip()))
        mappings = fan_out([(lambda c=c: oncotree(c)) for c in distinct])
        mapping_by_type: dict[str, OncotreeMapping] = dict(zip(distinct, mappings))
        if distinct:
            logger.info(
                "  oncotree: %s",
                "; ".join(f"{c!r} -> {mapping_by_type[c].oncotree_code or '?'}" for c in distinct),
            )

        rows: list[DnfRow] = []
        for r in extraction.rows:
            m = mapping_by_type.get(r.cancer_type)
            rows.append(
                DnfRow(
                    trialId=trial_id,
                    cohort=cohort,
                    cancer_type=r.cancer_type,
                    oncotree_name=m.oncotree_name if m else "",
                    oncotree_code=m.oncotree_code if m else "",
                    gene_alteration=r.gene_alteration,
                )
            )
        return rows

    def check(rows: list[DnfRow]) -> CheckResult:
        problems = _rule_problems(rows)
        if problems:
            logger.info("  check: %d rule problem(s): %s", len(problems), "; ".join(problems))
            return CheckResult(ok=False, problems=problems)
        if judge is not None:
            verdict: JudgeVerdict = judge(_judge_input(source_text, rows))
            if not verdict.faithful:
                detail = "; ".join(verdict.problems) or "(no detail given)"
                logger.info("  check: judge NOT faithful — %s", detail)
                return CheckResult(
                    ok=False,
                    problems=verdict.problems or ["faithfulness judge flagged the table (no detail given)"],
                )
            logger.info("  check: judge faithful")
        else:
            logger.info("  check: rules OK (judge skipped)")
        return CheckResult(ok=True)

    result = refine(
        produce=lambda: produce(""),
        check=check,
        repair=lambda rows, problems: produce("\n".join(f"- {p}" for p in problems)),
        max_attempts=max_attempts,
    )
    logger.info(
        "  result: %d row(s), faithful=%s, attempts=%d", len(result.value), result.ok, result.attempts
    )
    return ExtractionResult(
        rows=result.value, faithful=result.ok, attempts=result.attempts, problems=result.problems
    )


def _rule_problems(rows: list[DnfRow]) -> list[str]:
    problems: list[str] = []
    if not rows:
        problems.append("no eligibility rows were extracted")
    for i, r in enumerate(rows):
        if not r.cancer_type.strip():
            problems.append(f"row {i}: empty cancer_type")
        elif not r.oncotree_code.strip():
            problems.append(f"row {i}: no OncoTree code resolved for cancer_type {r.cancer_type!r}")
    return problems


def _judge_input(source_text: str, rows: list[DnfRow]) -> str:
    table = "\n".join(
        f"- cancer_type={r.cancer_type!r}, oncotree_code={r.oncotree_code!r}, gene_alteration={r.gene_alteration!r}"
        for r in rows
    ) or "(no rows)"
    return (
        f"SOURCE TRIAL TEXT (all relevant sections — title, description, conditions, eligibility):\n{source_text}\n\n"
        f"EXTRACTED DNF TABLE (rows are ORed; cells within a row are ANDed):\n{table}"
    )


def rows_to_tsv(rows: list[DnfRow]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, delimiter="\t", lineterminator="\n")
    writer.writerow(TSV_COLUMNS)
    for r in rows:
        writer.writerow(
            [r.trialId, r.cohort, r.cancer_type, r.oncotree_name, r.oncotree_code, r.gene_alteration]
        )
    return out.getvalue()
