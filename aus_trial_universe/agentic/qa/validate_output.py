"""Independent output validator — a *review of the reviewer agents*.

Deterministic, runs OUTSIDE the agentic workflow: it re-checks a finished output TSV so it
can catch what the in-loop reviewers let through (mapping degrades gracefully — output is
written even when a stage finishes `faithful=False`). It is a **testing-period QA step**, not
part of the production path; keep it in sync with the pipeline's own validators.

Two layers of checks:
1. Re-run the pipeline's OWN validators on the final cells (they run during mapping, but
   graceful degradation can still emit flagged values): OncoTree code validity + logic
   (`tools/oncotree`, `mapping.workflow._oncotree_logic_problems`) and finding-model syntax +
   logic (`tools/finding_model`).
2. Cross-row DNF / cohort / exclusion checks that NO single agent performs (each reviewer
   judges one dimension of one table; nothing audits the assembled TSV as a whole):
   unsatisfiable positive-AND-positive cancer_type, all-empty eligibility rows, exact-duplicate
   rows, in-cell `X AND NOT(X)`, and prior_therapy subsuming-twin over-enumeration.

Usage:
    python -m aus_trial_universe.agentic.qa.validate_output [COMBINED.tsv]
    (no arg -> newest data/agentic/output/<timestamp>/combined.tsv)
"""
from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from aus_trial_universe.agentic.tasks.mapping.workflow import _oncotree_logic_problems, strip_provenance
from aus_trial_universe.agentic.tools.finding_model import finding_model_problems
from aus_trial_universe.agentic.tools.oncotree import invalid_codes

OUTPUT_DIR = Path(__file__).resolve().parents[3] / "data" / "agentic" / "output"
ELIGIBILITY_COLUMNS = ("cancer_type", "gene_alteration", "molecular_signature", "molecular_biomarker", "prior_therapy")


@dataclass
class TrialReport:
    trial_id: str
    n_rows: int
    problems: list[str] = field(default_factory=list)


def positive_type_fragments(cancer_type: str) -> list[str]:
    """Top-level ' AND ' fragments of cancer_type that assert a POSITIVE type.

    A fragment containing NOT(...) anywhere is an exclusion / conditional carve-out, not a
    positive type (this is why a single-type cell with conditional NOT() clauses is NOT flagged).
    Two-or-more positive fragments = two tumour types ANDed = almost always unsatisfiable.
    """
    parts, depth, cur, i = [], 0, "", 0
    while i < len(cancer_type):
        c = cancer_type[i]
        if c == "(":
            depth += 1; cur += c
        elif c == ")":
            depth -= 1; cur += c
        elif depth == 0 and cancer_type[i:i + 5] == " AND ":
            parts.append(cur); cur = ""; i += 5; continue
        else:
            cur += c
        i += 1
    parts.append(cur)
    return [p.strip() for p in parts if p.strip() and "NOT(" not in p]


def _and_terms(expr: str) -> set[str]:
    return {t.strip() for t in re.split(r"\s+AND\s+", expr) if t.strip()}


def validate_rows(rows: list[dict]) -> list[TrialReport]:
    by_trial: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_trial[r["trialId"]].append(r)

    reports: list[TrialReport] = []
    for tid, trows in by_trial.items():
        problems: list[str] = []

        # --- layer 1: re-run the pipeline's own validators on the final mapped cells --------- #
        for code in {r.get("oncotree_code", "") for r in trows}:
            if not code.strip():
                continue
            bad = invalid_codes(code)
            if bad:
                problems.append(f"[oncotree] invalid code(s) {bad}: {code!r}")
            for lp in _oncotree_logic_problems(code):
                problems.append(f"[oncotree-logic] {lp}  (code={code!r})")
        for col in ("gene_alteration_findingmodel", "molecular_signature_findingmodel"):
            for fm in {r.get(col, "") for r in trows}:
                if not fm.strip():
                    continue
                for p in finding_model_problems(fm):
                    problems.append(f"[finding-model] {p}  ({col}={fm[:70]!r})")

        # --- layer 2: cross-row DNF / cohort / exclusion checks (nothing else does these) ---- #
        seen: set[tuple] = set()
        for i, r in enumerate(trows):
            ct = strip_provenance(r.get("cancer_type", ""))
            pos = positive_type_fragments(ct)
            if len(pos) > 1:
                problems.append(f"[dnf] row {i}: cancer_type ANDs {len(pos)} positive tumour types "
                                f"(likely unsatisfiable): {ct[:90]!r}")
            if not any(strip_provenance(r.get(c, "")) for c in ELIGIBILITY_COLUMNS):
                problems.append(f"[dnf] row {i}: all {len(ELIGIBILITY_COLUMNS)} eligibility columns empty")
            key = tuple(r.get(c, "") for c in r)
            if key in seen:
                problems.append(f"[dnf] row {i}: exact-duplicate row")
            seen.add(key)
            for col in ELIGIBILITY_COLUMNS:
                v = strip_provenance(r.get(col, ""))
                for body in re.findall(r"NOT\(([^()]+)\)", v):
                    body = body.strip()
                    rest = re.sub(r"NOT\([^()]+\)", "", v)  # positive remainder
                    if body and re.search(r"\b" + re.escape(body) + r"\b", rest):
                        problems.append(f"[logic] row {i} {col}: X AND NOT(X) self-contradiction: {v[:80]!r}")

        # prior_therapy subsuming-twin over-enumeration (per cohort × other eligibility cols)
        groups: dict[tuple, list[str]] = defaultdict(list)
        for r in trows:
            k = (r["cohort"], *(strip_provenance(r.get(c, "")) for c in ELIGIBILITY_COLUMNS if c != "prior_therapy"))
            groups[k].append(strip_provenance(r.get("prior_therapy", "")))
        for k, pts in groups.items():
            tsets = [_and_terms(p) for p in pts]
            for a in range(len(tsets)):
                if any(a != b and tsets[a] < tsets[b] for b in range(len(tsets))):
                    problems.append(f"[over-enum] cohort={k[0][:30]!r}: prior_therapy has a subsuming twin "
                                    f"(a stricter near-duplicate row — collapse to one)")
                    break

        reports.append(TrialReport(trial_id=tid, n_rows=len(trows), problems=problems))
    return reports


def _newest_output() -> Path:
    """Newest combined view: <timestamp>/combined.tsv (falls back to the legacy trial_resource_*.tsv)."""
    candidates = list(OUTPUT_DIR.glob("*/combined.tsv")) + list(OUTPUT_DIR.glob("trial_resource_*.tsv"))
    if not candidates:
        raise SystemExit(f"no <timestamp>/combined.tsv (or legacy trial_resource_*.tsv) under {OUTPUT_DIR}")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Independent validator (review of the reviewer agents) for an agentic output TSV.")
    parser.add_argument("output", nargs="?", help="combined-view TSV (default: newest <timestamp>/combined.tsv under data/agentic/output/)")
    args = parser.parse_args(argv)

    path = Path(args.output) if args.output else _newest_output()
    rows = list(csv.DictReader(open(path, encoding="utf-8"), delimiter="\t"))
    reports = validate_rows(rows)

    total = sum(len(r.problems) for r in reports)
    print(f"# Independent validation (review of the reviewers): {path.name}")
    print(f"# {len(rows)} rows across {len(reports)} trials\n")
    for rep in reports:
        status = "OK" if not rep.problems else f"{len(rep.problems)} PROBLEM(S)"
        print(f"== {rep.trial_id} ({rep.n_rows} rows) — {status} ==")
        for p in rep.problems[:50]:
            print(f"   - {p}")
        if len(rep.problems) > 50:
            print(f"   ... +{len(rep.problems) - 50} more")
    clean = sum(1 for r in reports if not r.problems)
    print(f"\n# {clean}/{len(reports)} trials clean · {total} total problem(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
