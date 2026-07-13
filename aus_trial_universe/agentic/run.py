"""v2 agentic pipeline orchestrator: extract -> map -> drug, per-run output.

Per trial (single process): extract the DNF eligibility rows, then map that trial's cells
(cancer_type -> OncoTree; gene_alteration / molecular_signature -> finding-model), then drug
enrichment. Each run writes a timestamped directory with the normalized 3NF masters
(regime.tsv, eligibility.tsv) + the combined flat view (combined.tsv) — see spec §6.1.

  python -m aus_trial_universe.agentic.run --id NCT06881784     # one trial
  python -m aus_trial_universe.agentic.run --ids NCT1,ACTRN..   # a specific set
  python -m aus_trial_universe.agentic.run                      # ALL trials (ctgov + anzctr)

Output: data/agentic/output/<YYYYMMDD_HHMMSS>/ containing regime.tsv, eligibility.tsv, combined.tsv.
Requires OPENAI_API_KEY (auto-loaded from .env / .env.local). Run via `make agentic-run`.
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "data/agentic/output"


def _load_openai_key() -> None:
    if os.environ.get("OPENAI_API_KEY"):
        return
    for fname in (".env", ".env.local"):
        path = REPO_ROOT / fname
        if path.exists():
            for line in path.read_text().splitlines():
                s = line.strip()
                if s.startswith("OPENAI_API_KEY=") and not s.startswith("#"):
                    os.environ["OPENAI_API_KEY"] = s.split("=", 1)[1].strip().strip('"').strip("'")


def _insert_after(cols: list[str], anchor: str, new: list[str]) -> list[str]:
    out: list[str] = []
    for c in cols:
        out.append(c)
        if c == anchor:
            out.extend(new)
    return out


# Output tables (spec §6.1): normalized 3NF masters + the combined flat view (their materialized join).
REGIME_COLS = ["trialId", "cohort", "arm_type", "drug",
               "main_drugs", "auxiliary_drugs", "pottr_drug_class", "drug_class",
               "tga_status", "pbs_status", "tga_detail", "pbs_detail"]
ELIGIBILITY_COLS = ["trialId", "cohort", "conj_id",
                    "cancer_type", "oncotree_name", "oncotree_code",
                    "gene_alteration", "gene_alteration_findingmodel",
                    "molecular_signature", "molecular_signature_findingmodel",
                    "molecular_biomarker", "prior_therapy"]


def _write_trial(rows: list[dict], combined_w, regime_w, elig_w) -> None:
    """Split one trial's enriched rows across the three output tables (spec §6.1).

    `conj_id` numbers the eligibility conjunctions within each (trialId, cohort); the regime table
    gets the distinct (trialId, cohort). All three DictWriters use extrasaction='ignore', so the same
    row dict is projected onto each table's own columns; missing columns (e.g. under --extract-only)
    are written empty.
    """
    conj: dict[tuple, int] = {}
    seen_regime: set[tuple] = set()
    for r in rows:
        key = (r["trialId"], r["cohort"])
        conj[key] = conj.get(key, 0) + 1
        r = {**r, "conj_id": conj[key]}
        combined_w.writerow(r)
        elig_w.writerow(r)
        if key not in seen_regime:
            seen_regime.add(key)
            regime_w.writerow(r)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="v2 agentic pipeline: extract -> map (one output).")
    which = parser.add_mutually_exclusive_group()
    which.add_argument("--id", help="Run one trial (source auto-detected from the id).")
    which.add_argument("--ids", help="Run a specific set: comma-separated ids.")
    parser.add_argument("--out-dir", help="Run output directory (default: data/agentic/output/<timestamp>/).")
    parser.add_argument("--model", default=None, help="Override the OpenAI model.")
    parser.add_argument("--no-judge", action="store_true", help="Skip the extraction reviewer panel (cheaper).")
    parser.add_argument("--no-review", action="store_true", help="Skip the mapping reviewers (cheaper).")
    parser.add_argument("--extract-only", action="store_true",
                        help="Extraction only — skip mapping + drug enrichment (fast; leaves those columns empty).")
    parser.add_argument("--max-attempts", type=int, default=3)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for _noisy in ("httpx", "openai", "urllib3", "numexpr"):
        logging.getLogger(_noisy).setLevel(logging.WARNING)
    _load_openai_key()

    from aus_trial_universe.agentic.core.client import LlmClient
    from aus_trial_universe.agentic.core.logfmt import FAIL, PASS, stage
    from aus_trial_universe.agentic.tasks.extraction.loaders import load_trials
    from aus_trial_universe.agentic.tasks.extraction.workflow import TSV_COLUMNS, extract_trial
    from aus_trial_universe.agentic.tasks.mapping.workflow import (
        curate_drugs,
        map_cancer_types,
        map_gene_alterations,
        map_molecular_signatures,
        strip_provenance,
    )

    ids = [x for x in args.ids.split(",")] if args.ids else None
    trials = load_trials(id=args.id, ids=ids)
    if not trials:
        parser.error("no trials with usable text found")

    # Output: one timestamped run directory with the 3NF masters + the combined flat view (spec §6.1).
    run_dir = Path(args.out_dir) if args.out_dir else OUT_DIR / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    combined_cols = _insert_after(TSV_COLUMNS, "arm_type", ["conj_id"])
    combined_cols = _insert_after(combined_cols, "cancer_type", ["oncotree_name", "oncotree_code"])
    combined_cols = _insert_after(combined_cols, "gene_alteration", ["gene_alteration_findingmodel"])
    combined_cols = _insert_after(combined_cols, "molecular_signature", ["molecular_signature_findingmodel"])
    combined_cols = _insert_after(combined_cols, "drug", ["main_drugs", "auxiliary_drugs",
                                                          "pottr_drug_class", "drug_class", "tga_status",
                                                          "pbs_status", "tga_detail", "pbs_detail"])

    client = LlmClient(model=args.model) if args.model else LlmClient()
    kw = dict(max_attempts=args.max_attempts, use_reviewer=not args.no_review)
    log = logging.getLogger("agentic.pipeline")
    log.info("run · %d trial(s) · judge=%s · review=%s · extract_only=%s → %s/",
             len(trials), not args.no_judge, not args.no_review, args.extract_only, run_dir.name)

    total = 0
    summaries = []
    failures: list[tuple[str, str]] = []  # (trial_id, error) — batch continues past a failed trial
    with open(run_dir / "combined.tsv", "w", newline="", encoding="utf-8") as fc, \
         open(run_dir / "regime.tsv", "w", newline="", encoding="utf-8") as fr, \
         open(run_dir / "eligibility.tsv", "w", newline="", encoding="utf-8") as fe:
        combined_w = csv.DictWriter(fc, fieldnames=combined_cols, delimiter="\t", lineterminator="\n", extrasaction="ignore")
        regime_w = csv.DictWriter(fr, fieldnames=REGIME_COLS, delimiter="\t", lineterminator="\n", extrasaction="ignore")
        elig_w = csv.DictWriter(fe, fieldnames=ELIGIBILITY_COLS, delimiter="\t", lineterminator="\n", extrasaction="ignore")
        for w in (combined_w, regime_w, elig_w):
            w.writeheader()
        for fh in (fc, fr, fe):
            fh.flush()
        for i, (source, trial_id, base_text, cohorts) in enumerate(trials, 1):
            log.info("")
            log.info("═" * 70)
            log.info("TRIAL %d/%d · %s · %s", i, len(trials), source, trial_id)
            log.info("═" * 70)
            try:
                # STAGE I: EXTRACTION
                log.info("")
                log.info(stage("EXTRACTION"))
                result = extract_trial(client, trial_id=trial_id, source_text=base_text, cohorts=cohorts,
                                       max_attempts=args.max_attempts, use_judge=not args.no_judge)
                rows = [{c: getattr(r, c) for c in TSV_COLUMNS} for r in result.rows]
                if not args.extract_only:
                    # STAGE II: MAPPING (this trial's cells)
                    log.info("")
                    log.info(stage("MAPPING"))
                    onco = map_cancer_types(client, [r["cancer_type"] for r in rows], **kw)
                    gene = map_gene_alterations(client, [r["gene_alteration"] for r in rows], **kw)
                    sig = map_molecular_signatures(client, [r["molecular_signature"] for r in rows], **kw)
                    for r in rows:
                        o = onco.get(strip_provenance(r["cancer_type"]))
                        r["oncotree_name"] = o.oncotree_name if o else ""
                        r["oncotree_code"] = o.oncotree_code if o else ""
                        g = gene.get(strip_provenance(r["gene_alteration"]))
                        r["gene_alteration_findingmodel"] = g.finding_model if g else ""
                        s = sig.get(strip_provenance(r["molecular_signature"]))
                        r["molecular_signature_findingmodel"] = s.finding_model if s else ""
                    # STAGE III: DRUG enrichment (trial-level for now; moves per-regime with drug_ref — spec §6.1)
                    log.info("")
                    log.info(stage("DRUG"))
                    drug_set: list[str] = []
                    for r in rows:
                        for dn in strip_provenance(r["drug"]).split(";"):
                            dn = dn.strip()
                            if dn and dn not in drug_set:
                                drug_set.append(dn)
                    dc = curate_drugs(client, base_text, drug_set, **kw)
                    for r in rows:
                        r["main_drugs"] = dc.main_drugs
                        r["auxiliary_drugs"] = dc.auxiliary_drugs
                        r["pottr_drug_class"] = dc.pottr_drug_class
                        r["drug_class"] = dc.drug_class
                        r["tga_status"] = dc.tga_status
                        r["pbs_status"] = dc.pbs_status
                        r["tga_detail"] = dc.tga_detail
                        r["pbs_detail"] = dc.pbs_detail
                _write_trial(rows, combined_w, regime_w, elig_w)
                for fh in (fc, fr, fe):
                    fh.flush()
                total += len(rows)
                summaries.append((source, trial_id, len(rows), result.faithful, result.attempts))
                log.info("")
                log.info("done · %s · %d row(s) · total %d", trial_id, len(rows), total)
            except Exception as exc:  # one flaky/API-failing trial must not kill the whole batch
                failures.append((trial_id, f"{type(exc).__name__}: {exc}"))
                log.info("")
                log.info("FAILED · %s · %s (skipped; continuing)", trial_id, f"{type(exc).__name__}: {exc}")

    print(f"\n{'═' * 70}\n{len(trials)} trial(s): {len(summaries)} ok, {len(failures)} failed · "
          f"{total} row(s) → {run_dir}/ (regime.tsv · eligibility.tsv · combined.tsv)\n")
    for source, trial_id, n, faithful, attempts in summaries[:40]:
        print(f"  {PASS if faithful else FAIL}  {trial_id} · rows={n} · attempts={attempts}")
    for trial_id, err in failures:
        print(f"  FAILED  {trial_id} · {err[:90]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
