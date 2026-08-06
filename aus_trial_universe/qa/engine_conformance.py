"""DRY RUN — push every mapped expression through the ported matching engine and triage the failures by OWNER.


    python -m aus_trial_universe.qa.engine_conformance                      # grade the SHIPPED export
    python -m aus_trial_universe.qa.engine_conformance --input <cmp>.tsv --column before_production

With no `--input` it grades `derived/export/trial_eligibility.tsv`, so it is re-runnable on any future cycle. Point
`--input` at a review comparison TSV (e.g. one archived beside a superseded store) plus `--column` to grade a
before/after pair — the only way to say whether a change helped.

The point is NOT to make our expressions fit the engine. It is to separate:
  OURS    a defect in the mapping, which we fix here;
  ENGINE  well-formed finding-model the engine cannot yet consume, which goes to
          docs/planning/matching_engine_capability_gaps.md as a capability request.
Never resolve an ENGINE row by degrading the mapping.
"""
from __future__ import annotations

import argparse
import collections
import csv
import re
from pathlib import Path

from aus_trial_universe.core.paths import ANALYSIS_DIR
from aus_trial_universe.qa.engine_port import GeneticAlterationParser, TrialGeneticsParser

ENGINE_COMMIT = "a97142938 (oncoact, branch trial_matching, 2026-05-22)"
DEFAULT_OUTPUT = ANALYSIS_DIR / "gene_alteration_engine_dry_run.md"

# finding id -> (owner, gap/defect id, one-line effect on matching)
FINDINGS: dict[str, tuple[str, str, str]] = {
    "SPLICE_EFFECT": ("OURS", "A1", "transcriptImpact.effects=SPLICE is not a VariantEffect -> expression dropped"),
    "HLA_PGX": ("OURS", "A5", "HLA emitted as PharmocoGenotype instead of HlaAllele"),
    "POS_CONJUNCTION": ("ENGINE", "G1", "a positive conjunction has no representation in GeneticCriteria"),
    "AND_NOT_SPLIT": ("ENGINE", "G2", "firstPass only splits & after a NOT( token -> the term is mis-parsed"),
    "GATE_DISABLED": ("ENGINE", "G3", "positives end up empty -> the biomarker gate is silently disabled"),
    "FUSION_ANY": ("ENGINE", "G4", "Fusion[geneStart=X | geneEnd=X] -> Fusion(null,null) = ANY fusion"),
    "HET_DEL": ("ENGINE", "G5", "HET_DEL / CN_NEUTRAL_LOH dropped -> matches any GainDeletion of the gene"),
    "CODING_EFFECT_UNREAD": ("ENGINE", "G6", "transcriptImpact.codingEffect is never read -> silently ignored"),
    "CODON_UNREAD": ("ENGINE", "G6", "transcriptImpact.affectedCodon is never read -> silently ignored"),
    "CLASS_SKIPPED": ("ENGINE", "G8", "Wildtype / Virus / PharmocoGenotype / HlaAllele section skipped, not matched"),
}
_SKIPPED_CLASSES = ("Wildtype", "PharmocoGenotype", "Virus", "HlaAllele")
_FUSION_ALT = re.compile(r"Fusion\[\s*geneStart=[\w.\-]+\s*\|\s*geneEnd=[\w.\-]+\s*\]")


def _split_top(e: str, op: str = "&") -> list[str]:
    parts, d, cur = [], 0, ""
    for c in e:
        if c in "[(":
            d += 1
            cur += c
        elif c in "])":
            d -= 1
            cur += c
        elif d == 0 and c == op:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += c
    if cur.strip():
        parts.append(cur.strip())
    return [p for p in parts if p]


def analyse(expression: str) -> set[str]:
    """Every conformance finding for one expression, as FINDINGS keys."""
    e = (expression or "").strip()
    if not e:
        return set()
    found: set[str] = set()

    if "transcriptImpact.effects=SPLICE" in e:
        found.add("SPLICE_EFFECT")
    if re.search(r"PharmocoGenotype\[gene=HLA", e):
        found.add("HLA_PGX")
    if "transcriptImpact.codingEffect" in e:
        found.add("CODING_EFFECT_UNREAD")
    if "transcriptImpact.affectedCodon" in e:
        found.add("CODON_UNREAD")
    if "type=HET_DEL" in e or "type=CN_NEUTRAL_LOH" in e:
        found.add("HET_DEL")
    if _FUSION_ALT.search(e):
        found.add("FUSION_ANY")
    if any(f"{c}[" in e for c in _SKIPPED_CLASSES):
        found.add("CLASS_SKIPPED")

    conjuncts = _split_top(e)
    positives = [c for c in conjuncts
                 if not c.startswith("NOT(") and not any(f"{k}[" in c for k in _SKIPPED_CLASSES)]
    if len(positives) > 1:
        found.add("POS_CONJUNCTION")

    parser = TrialGeneticsParser(e)
    got = parser.parse()
    if parser.failures:
        found.add("AND_NOT_SPLIT")
    for alteration, _sign, _txt in got:
        gene = alteration[1] if alteration[0] in ("Interruption", "Fluctuation", "SmallVariantAlteration") else None
        if gene and any(ch in str(gene) for ch in "][()"):
            found.add("AND_NOT_SPLIT")
        if alteration[0] == "Fusion" and alteration[1] is None and alteration[2] is None:
            found.add("FUSION_ANY")
        if alteration[0] == "Fluctuation" and alteration[2] is None:
            found.add("HET_DEL")
    if positives and not [1 for a, s, _ in got if s]:
        found.add("GATE_DISABLED")
    return found


def _terms_count(e: str) -> int:
    return len(GeneticAlterationParser.__module__ and re.findall(r"[A-Za-z][A-Za-z0-9]*\[", e or ""))


def build_report(rows: list[dict], column: str) -> tuple[str, dict]:
    findings_by_row: list[tuple[dict, set[str]]] = [(r, analyse(r.get(column, ""))) for r in rows]
    non_empty = [(r, f) for r, f in findings_by_row if (r.get(column) or "").strip()]
    broken = [(r, f) for r, f in non_empty if f]
    ours = [(r, f) for r, f in broken if any(FINDINGS[k][0] == "OURS" for k in f)]
    engine_only = [(r, f) for r, f in broken if not any(FINDINGS[k][0] == "OURS" for k in f)]

    cnt: collections.Counter = collections.Counter()
    for _r, f in non_empty:
        for k in f:
            cnt[k] += 1

    L: list[str] = []
    L.append(f"# Matching-engine DRY RUN — column `{column}`\n")
    L.append(f"Engine parser transcribed from **{ENGINE_COMMIT}**; fidelity asserted by "
             f"`tests/agentic/qa/test_engine_port.py` (all 12 of the engine's own parser tests).")
    L.append("No engine code was modified or executed.\n")
    L.append("| | expressions |")
    L.append("|---|---:|")
    L.append(f"| non-empty expressions | {len(non_empty)} |")
    L.append(f"| clean through the engine | {len(non_empty) - len(broken)} |")
    L.append(f"| **needs an ENGINE upgrade only** | **{len(engine_only)}** |")
    L.append(f"| **has a defect of OURS** | **{len(ours)}** |\n")

    L.append("## Findings by cause\n")
    L.append("| owner | id | expressions | effect |")
    L.append("|---|---|---:|---|")
    for k, n in cnt.most_common():
        owner, gid, desc = FINDINGS[k]
        L.append(f"| {owner} | {gid} | {n} | {desc} |")
    L.append("")

    if ours:
        L.append(f"## ⚠ OURS to fix ({len(ours)})\n")
        for r, f in ours:
            ids = ", ".join(sorted(FINDINGS[k][1] for k in f if FINDINGS[k][0] == "OURS"))
            L.append(f"- `{r['gene_alteration'][:120]}`")
            L.append(f"  - expression: `{r.get(column, '')[:160]}`")
            L.append(f"  - defect(s): {ids}")
        L.append("")

    L.append(f"## ENGINE-side, do NOT degrade the mapping ({len(engine_only)})\n")
    by_gap: dict[str, list[dict]] = {}
    for r, f in engine_only:
        for k in f:
            by_gap.setdefault(FINDINGS[k][1], []).append(r)
    for gid in sorted(by_gap):
        rows_g = by_gap[gid]
        L.append(f"### {gid} — {len(rows_g)} expression(s)")
        for r in rows_g[:6]:
            L.append(f"- `{(r.get(column) or '')[:150]}`")
        if len(rows_g) > 6:
            L.append(f"- … +{len(rows_g) - 6} more")
        L.append("")

    summary = {
        "column": column, "non_empty": len(non_empty), "clean": len(non_empty) - len(broken),
        "ours": len(ours), "engine_only": len(engine_only), "by_cause": dict(cnt),
    }
    return "\n".join(L) + "\n", summary


def _rows_from_export(path: Path) -> list[dict]:
    """Read the shipped export directly, so the dry run is re-runnable on any future cycle.

    The review comparison file is a one-off migration artifact; the export is permanent. Deduped on
    (interpreted source, expression) so each distinct mapping is graded once.
    """
    csv.field_size_limit(10 ** 9)
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            expression = (row.get("gene_alteration_findingmodel") or "").strip()
            source = (row.get("gene_alteration_interpreted") or "").strip()
            if not expression or (source, expression) in seen:
                continue
            seen.add((source, expression))
            out.append({"gene_alteration": source, "after_stage2_FINAL": expression})
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="dry-run the mapped expressions through the ported matching engine")
    p.add_argument("--input", type=Path, default=None,
                   help="a review comparison TSV. Default: grade the shipped export instead (--from-export).")
    p.add_argument("--from-export", action="store_true", default=None,
                   help="grade data/agentic/derived/export/trial_eligibility.tsv (the default when --input is absent)")
    p.add_argument("--column", default="after_stage2_FINAL",
                   help="which comparison column to test (e.g. before_production, after_stage1)")
    p.add_argument("--output", type=Path, default=None)
    args = p.parse_args(argv)

    if args.input is None:
        from aus_trial_universe.core.paths import EXPORT_FILE, EXPORT_ROOT
        export = Path(EXPORT_ROOT) / EXPORT_FILE
        rows = _rows_from_export(export)
        args.column = "after_stage2_FINAL"
        print(f"grading the shipped export: {export}")
    else:
        with open(args.input, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh, delimiter="\t"))
    report, summary = build_report(rows, args.column)
    out = args.output or (DEFAULT_OUTPUT if args.column == "after_stage2_FINAL"
                          else DEFAULT_OUTPUT.with_name(f"{DEFAULT_OUTPUT.stem}_{args.column}.md"))
    out.write_text(report, encoding="utf-8")
    print(f"wrote {out}")
    print(f"  non-empty={summary['non_empty']}  clean={summary['clean']}  "
          f"OURS={summary['ours']}  ENGINE-only={summary['engine_only']}")
    for k, n in sorted(summary["by_cause"].items(), key=lambda kv: -kv[1]):
        owner, gid, _ = FINDINGS[k]
        print(f"    {owner:7} {gid:4} {n:>5}  {k}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
