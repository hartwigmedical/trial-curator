from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path
from typing import Iterable


def raise_csv_field_size_limit() -> None:
    """Allow compact TSV fields containing large JSON summaries."""
    limit = sys.maxsize
    while True:
        try:
            csv.field_size_limit(limit)
            return
        except OverflowError:
            limit = limit // 10


MUST_MATCH = [
    ("pembrolizumab", "CHEMBL3137343"),
    ("keytruda", "CHEMBL3137343"),
    ("nivolumab", "CHEMBL2108738"),
    ("opdivo", "CHEMBL2108738"),
    ("trastuzumab deruxtecan", "CHEMBL4297844"),
    ("enhertu", "CHEMBL4297844"),
    ("ado-trastuzumab emtansine", "CHEMBL1743082"),
    ("kadcyla", "CHEMBL1743082"),
    ("polatuzumab vedotin", "CHEMBL3301582"),
    ("enfortumab vedotin", "CHEMBL3301589"),
    ("padcev", "CHEMBL3301589"),
    ("sacituzumab govitecan", "CHEMBL3545262"),
    ("trodelvy", "CHEMBL3545262"),
    ("besponsa", "CHEMBL2108611"),
    ("adct-402", "CHEMBL4297778"),
]

# Source-level gold case. Only required if the CTGov compact output contains
# this term; otherwise it belongs to a separate ChEMBL source-index test.
MUST_MATCH_IF_PRESENT = [
    ("polivy", "CHEMBL3301582"),
]

MUST_NOT_COLLAPSE_BY_ANCHOR_NAME = [
    ("trastuzumab", {"CHEMBL4297844", "CHEMBL1743082"}),
    ("enfortumab", {"CHEMBL3301589"}),
    ("sacituzumab", {"CHEMBL3545262"}),
    ("vipivotide tetraxetan", {"CHEMBL4594406"}),
]

MUST_NOT_INCLUDE_FOR_TERM = [
    ("trodelvy", {"CHEMBL3707405"}),
    ("kadcyla", {"CHEMBL1201585"}),
    ("besponsa", {"CHEMBL4297558"}),
    ("adct-402", {"CHEMBL4297238"}),
    ("opdualag", {"CHEMBL2108738"}),
]


def norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").lower()).strip()


def read_tsv(path: Path) -> list[dict[str, str]]:
    raise_csv_field_size_limit()
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def row_mentions(row: dict[str, str], term: str) -> bool:
    haystack = " | ".join([
        row.get("anchor_name", ""),
        row.get("represented_input_names_summary", ""),
        row.get("chembl_pref_names_summary", ""),
    ])
    return norm(term) in norm(haystack)


def write_tsv(path: Path, rows: Iterable[dict[str, object]]) -> None:
    """Write TSVs whose row dictionaries may have non-identical fields."""
    rows = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return

    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Regression gate for ChEMBL compact resolution TSV.")
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--out_dir", required=True, type=Path)
    args = parser.parse_args()

    rows = read_tsv(args.candidate)
    failures: list[dict[str, object]] = []
    gold_rows: list[dict[str, object]] = []

    for term, expected_chembl in MUST_MATCH:
        matching_rows = [row for row in rows if row_mentions(row, term)]
        found = any(expected_chembl in row.get("chembl_ids_summary", "") for row in matching_rows)
        status = "PASS" if found else "FAIL"
        gold_rows.append({
            "case_type": "MUST_MATCH",
            "term": term,
            "expected": expected_chembl,
            "status": status,
            "matched_rows": len(matching_rows),
            "observed_chembl_ids": " | ".join(row.get("chembl_ids_summary", "") for row in matching_rows[:5]),
        })
        if not found:
            failures.append(gold_rows[-1])

    for term, expected_chembl in MUST_MATCH_IF_PRESENT:
        matching_rows = [row for row in rows if row_mentions(row, term)]
        if not matching_rows:
            status = "SKIP_NOT_PRESENT"
            gold_rows.append({
                "case_type": "MUST_MATCH_IF_PRESENT",
                "term": term,
                "expected": expected_chembl,
                "status": status,
                "matched_rows": 0,
                "observed_chembl_ids": "",
            })
            continue
        found = any(expected_chembl in row.get("chembl_ids_summary", "") for row in matching_rows)
        status = "PASS" if found else "FAIL"
        gold_rows.append({
            "case_type": "MUST_MATCH_IF_PRESENT",
            "term": term,
            "expected": expected_chembl,
            "status": status,
            "matched_rows": len(matching_rows),
            "observed_chembl_ids": " | ".join(row.get("chembl_ids_summary", "") for row in matching_rows[:5]),
        })
        if not found:
            failures.append(gold_rows[-1])

    for term, forbidden in MUST_NOT_COLLAPSE_BY_ANCHOR_NAME:
        matching_rows = [row for row in rows if norm(row.get("anchor_name", "")) == norm(term)]
        observed = " | ".join(row.get("chembl_ids_summary", "") for row in matching_rows)
        bad = sorted(code for code in forbidden if code in observed)
        status = "PASS" if not bad else "FAIL"
        gold_rows.append({
            "case_type": "MUST_NOT_COLLAPSE_BY_ANCHOR_NAME",
            "term": term,
            "expected": "no forbidden CHEMBL IDs",
            "status": status,
            "matched_rows": len(matching_rows),
            "observed_chembl_ids": observed,
            "forbidden_found": ",".join(bad),
        })
        if bad:
            failures.append(gold_rows[-1])

    for term, forbidden in MUST_NOT_INCLUDE_FOR_TERM:
        matching_rows = [row for row in rows if row_mentions(row, term)]
        observed = " | ".join(row.get("chembl_ids_summary", "") for row in matching_rows)
        bad = sorted(code for code in forbidden if code in observed)
        status = "PASS" if not bad else "FAIL"
        gold_rows.append({
            "case_type": "MUST_NOT_INCLUDE_FOR_TERM",
            "term": term,
            "expected": "no forbidden CHEMBL IDs in rows mentioning term",
            "status": status,
            "matched_rows": len(matching_rows),
            "observed_chembl_ids": observed,
            "forbidden_found": ",".join(bad),
        })
        if bad:
            failures.append(gold_rows[-1])

    # Shape gates: compact rows should not explode and duplicate anchor keys should not appear.
    anchor_counts: dict[str, int] = {}
    for row in rows:
        key = row.get("anchor_key", "")
        if key:
            anchor_counts[key] = anchor_counts.get(key, 0) + 1
    dup_keys = sorted(key for key, count in anchor_counts.items() if count > 1)
    if dup_keys:
        failures.append({
            "case_type": "SHAPE",
            "term": "anchor_key uniqueness",
            "expected": "no duplicate anchor_key rows",
            "status": "FAIL",
            "observed_chembl_ids": "",
            "forbidden_found": ",".join(dup_keys[:20]),
        })

    if len(rows) > 20000:
        failures.append({
            "case_type": "SHAPE",
            "term": "compact row count",
            "expected": "<= 20000",
            "status": "FAIL",
            "observed_chembl_ids": str(len(rows)),
            "forbidden_found": "",
        })

    write_tsv(args.out_dir / "chembl_regression_gold_case_results.tsv", gold_rows)
    write_tsv(args.out_dir / "chembl_regression_failures.tsv", failures)

    print(f"ChEMBL regression gate: {len(failures)} failure(s)")
    print(f"candidate rows={len(rows)}; matched rows={sum(1 for row in rows if row.get('link_status') == 'MATCHED_CHEMBL_MOLECULE')}")
    print(f"wrote {args.out_dir / 'chembl_regression_gold_case_results.tsv'}")
    print(f"wrote {args.out_dir / 'chembl_regression_failures.tsv'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
