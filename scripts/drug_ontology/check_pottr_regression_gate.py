#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path
from typing import Iterable

SUMMARY_DELIMITER = " | "


def norm(value: object) -> str:
    text = "" if value is None else str(value).lower()
    text = re.sub(r"[™®]", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def row_blob(row: dict[str, str]) -> str:
    fields = [
        "anchor_key",
        "anchor_name",
        "represented_input_names_summary",
        "pottr_concepts_summary",
        "pottr_direct_classes_summary",
        "pottr_expanded_classes_summary",
        "anchor_selection_strategy_distribution",
    ]
    return norm(" ".join(row.get(field, "") for field in fields))


def concept_blob(row: dict[str, str]) -> str:
    return norm(" ".join([row.get("pottr_concepts_summary", ""), row.get("pottr_direct_classes_summary", "")]))


def rows_for_anchor(rows: Iterable[dict[str, str]], anchor_phrase: str) -> list[dict[str, str]]:
    key = norm(anchor_phrase)
    out = []
    for row in rows:
        anchor_name = norm(row.get("anchor_name", ""))
        represented = norm(row.get("represented_input_names_summary", ""))
        anchor_key = norm(row.get("anchor_key", ""))
        if key == anchor_name or key in represented or key in anchor_key:
            out.append(row)
    return out




def rows_for_exact_anchor(rows: Iterable[dict[str, str]], anchor_phrase: str) -> list[dict[str, str]]:
    key = norm(anchor_phrase)
    return [row for row in rows if norm(row.get("anchor_name", "")) == key]

def any_row_contains(rows: Iterable[dict[str, str]], phrase: str, field: str | None = None) -> bool:
    target = norm(phrase)
    if not target:
        return False
    for row in rows:
        blob = norm(row.get(field, "")) if field else row_blob(row)
        if target in blob:
            return True
    return False


def write_tsv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def build_changed_anchors(baseline_rows: list[dict[str, str]], candidate_rows: list[dict[str, str]]) -> list[dict[str, object]]:
    fields = [
        "link_status",
        "pottr_concepts_summary",
        "pottr_direct_classes_summary",
        "pottr_expanded_classes_summary",
        "mapping_row_count",
        "anchor_selection_strategy_distribution",
    ]
    base = {row.get("anchor_key", ""): row for row in baseline_rows}
    cand = {row.get("anchor_key", ""): row for row in candidate_rows}
    changed = []
    for key in sorted(set(base) | set(cand)):
        b = base.get(key)
        c = cand.get(key)
        if b is None:
            changed.append({
                "anchor_key": key,
                "change_type": "ADDED",
                "baseline_link_status": "",
                "candidate_link_status": c.get("link_status", "") if c else "",
                "baseline_concepts": "",
                "candidate_concepts": c.get("pottr_concepts_summary", "") if c else "",
                "baseline_mapping_row_count": "",
                "candidate_mapping_row_count": c.get("mapping_row_count", "") if c else "",
            })
        elif c is None:
            changed.append({
                "anchor_key": key,
                "change_type": "REMOVED",
                "baseline_link_status": b.get("link_status", ""),
                "candidate_link_status": "",
                "baseline_concepts": b.get("pottr_concepts_summary", ""),
                "candidate_concepts": "",
                "baseline_mapping_row_count": b.get("mapping_row_count", ""),
                "candidate_mapping_row_count": "",
            })
        elif any(b.get(field, "") != c.get(field, "") for field in fields):
            changed.append({
                "anchor_key": key,
                "change_type": "CHANGED",
                "baseline_link_status": b.get("link_status", ""),
                "candidate_link_status": c.get("link_status", ""),
                "baseline_concepts": b.get("pottr_concepts_summary", ""),
                "candidate_concepts": c.get("pottr_concepts_summary", ""),
                "baseline_mapping_row_count": b.get("mapping_row_count", ""),
                "candidate_mapping_row_count": c.get("mapping_row_count", ""),
            })
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="Regression gate for POTTR compact check TSV.")
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--baseline", type=Path, default=None, help="Optional previous accepted/check TSV to produce changed-anchor audit.")
    parser.add_argument("--out_dir", type=Path, default=Path("data/ctgov/exports"))
    args = parser.parse_args()

    rows = read_tsv(args.candidate)
    failures: list[dict[str, object]] = []
    gold: list[dict[str, object]] = []

    def record(case_group: str, case_name: str, passed: bool, details: str) -> None:
        gold.append({"case_group": case_group, "case_name": case_name, "passed": int(passed), "details": details})
        if not passed:
            failures.append({"case_group": case_group, "case_name": case_name, "details": details})

    # 1. Must-stay-clean: generic anchors must not inherit regimen/specific product concepts.
    clean_cases = {
        "leucovorin": ["folfox regimen", "folfiri regimen", "folfirinox regimen"],
        "capecitabine": ["capox"],
        "rituximab": ["r chop", "r cvp"],
        "trastuzumab": ["trastuzumab deruxtecan", "trastuzumab emtansine"],
        "nivolumab": ["nivolumab relatlimab"],
    }
    for anchor, forbidden in clean_cases.items():
        matched_rows = rows_for_exact_anchor(rows, anchor)
        if not matched_rows:
            record("must_stay_clean", anchor, False, "anchor row not found")
            continue
        bad = []
        for row in matched_rows:
            blob = concept_blob(row)
            for phrase in forbidden:
                if norm(phrase) in blob:
                    bad.append(f"{phrase} in {row.get('pottr_concepts_summary','')}")
        record("must_stay_clean", anchor, not bad, "; ".join(bad) if bad else "ok")

    # 2. Must match exact/specific cases.
    must_match = {
        "FOLFOX": "FOLFOX regimen",
        "CAPOX": "CAPOX",
        "R-CHOP": "R-CHOP",
        "R-CVP": "R-CVP",
        "Kadcyla": "Trastuzumab Emtansine",
        "Enhertu": "Trastuzumab Deruxtecan",
        "Opdualag": "Nivolumab-Relatlimab",
        "Pluvicto": "Lu-177 vipivotide tetraxetan",
        "Nivolumab and relatlimab": "Nivolumab-Relatlimab",
        "(177Lu) vipivotide tetraxetan": "Lu-177 vipivotide tetraxetan",
    }
    for alias, expected in must_match.items():
        candidate_rows = rows_for_anchor(rows, alias)
        passed = any(norm(expected) in concept_blob(row) and row.get("link_status") == "MATCHED_POTTR_CLASS" for row in candidate_rows)
        details = "ok" if passed else f"expected {expected}; found {[r.get('pottr_concepts_summary','') for r in candidate_rows[:5]]}"
        record("must_match", alias, passed, details)

    # 3. Must not cross-match isotope/code-like drugs.
    forbidden_cross = {
        "[111In]In-FL-020": ["225Ac-FL-020"],
        "[161 Tb]Tb PSMA I&T": ["225Ac-PSMA-I&T"],
        "[225Ac]Ac-PSMA-617": ["Lu-177 vipivotide tetraxetan"],
        "Lutetium-177 PSMA-I&T": ["161Tb-PSMA-I&T", "225Ac-PSMA-I&T"],
    }
    for alias, forbidden in forbidden_cross.items():
        candidate_rows = rows_for_anchor(rows, alias)
        bad = []
        for row in candidate_rows:
            blob = concept_blob(row)
            for phrase in forbidden:
                if norm(phrase) in blob:
                    bad.append(f"{phrase} in {row.get('pottr_concepts_summary','')}")
        # If the alias is absent, this case is not applicable; do not fail.
        passed = not bad
        details = "ok" if passed else "; ".join(bad)
        record("must_not_cross_match", alias, passed, details)

    # 4. Shape gate from compact TSV.
    total_rows = len(rows)
    matched = sum(1 for r in rows if r.get("link_status") == "MATCHED_POTTR_CLASS")
    no_match = sum(1 for r in rows if r.get("link_status") == "NO_POTTR_CLASS_FOR_ANCHOR")
    total_mapping_rows = sum(int(r.get("mapping_row_count") or 0) for r in rows)
    record("shape", "compact_rows_controlled", 3000 <= total_rows <= 4500, f"rows={total_rows}")
    record("shape", "mapping_rows_controlled", 3000 <= total_mapping_rows <= 9000, f"mapping_row_count_sum={total_mapping_rows}; matched_anchors={matched}; no_match={no_match}")

    out_dir = args.out_dir
    write_tsv(
        out_dir / "pottr_regression_gold_case_results.tsv",
        gold,
        ["case_group", "case_name", "passed", "details"],
    )
    write_tsv(
        out_dir / "pottr_regression_failures.tsv",
        failures,
        ["case_group", "case_name", "details"],
    )

    if args.baseline:
        changed = build_changed_anchors(read_tsv(args.baseline), rows)
        write_tsv(
            out_dir / "pottr_regression_changed_anchors.tsv",
            changed,
            [
                "anchor_key", "change_type", "baseline_link_status", "candidate_link_status",
                "baseline_concepts", "candidate_concepts", "baseline_mapping_row_count", "candidate_mapping_row_count",
            ],
        )

    print(f"POTTR regression gate: {len(failures)} failure(s)")
    print(f"candidate rows={total_rows}; matched anchors={matched}; no-match anchors={no_match}; mapping row sum={total_mapping_rows}")
    print(f"wrote {out_dir / 'pottr_regression_gold_case_results.tsv'}")
    print(f"wrote {out_dir / 'pottr_regression_failures.tsv'}")
    if failures:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
