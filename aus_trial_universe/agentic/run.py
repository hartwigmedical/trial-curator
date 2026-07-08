"""v2 agentic pipeline orchestrator: extract -> map, one streamed output per run.

Per trial (single process): extract the DNF eligibility rows, then map that trial's cells
(cancer_type -> OncoTree; gene_alteration / molecular_signature -> finding-model), and stream
the fully-enriched rows to ONE output file. No intermediate extraction file.

  python -m aus_trial_universe.agentic.run --id NCT06881784     # one trial
  python -m aus_trial_universe.agentic.run --ids NCT1,ACTRN..   # a specific set
  python -m aus_trial_universe.agentic.run                      # ALL trials (ctgov + anzctr)

Output: data/agentic/output/trial_resource_<id>.tsv (single) or _<YYYYMMDD_HHMMSS>.tsv (multi/all).
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="v2 agentic pipeline: extract -> map (one output).")
    which = parser.add_mutually_exclusive_group()
    which.add_argument("--id", help="Run one trial (source auto-detected from the id).")
    which.add_argument("--ids", help="Run a specific set: comma-separated ids.")
    parser.add_argument("--out", help="Output TSV path (default: data/agentic/output/trial_resource_<...>.tsv).")
    parser.add_argument("--model", default=None, help="Override the OpenAI model.")
    parser.add_argument("--no-judge", action="store_true", help="Skip the extraction reviewer panel (cheaper).")
    parser.add_argument("--no-review", action="store_true", help="Skip the mapping reviewers (cheaper).")
    parser.add_argument("--max-attempts", type=int, default=3)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    for _noisy in ("httpx", "openai", "urllib3"):
        logging.getLogger(_noisy).setLevel(logging.WARNING)
    _load_openai_key()

    from aus_trial_universe.agentic.core.client import LlmClient
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

    # Output naming: single --id -> trial id; multiple/all -> timestamp.
    label = args.id if args.id else datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = Path(args.out) if args.out else OUT_DIR / f"trial_resource_{label}.tsv"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    out_cols = _insert_after(TSV_COLUMNS, "cancer_type", ["oncotree_name", "oncotree_code"])
    out_cols = _insert_after(out_cols, "gene_alteration", ["gene_alteration_findingmodel"])
    out_cols = _insert_after(out_cols, "molecular_signature", ["molecular_signature_findingmodel"])
    out_cols = _insert_after(out_cols, "drug", ["main_drugs", "auxiliary_drugs",
                                               "pottr_drug_class", "drug_class", "tga_status", "pbs_status"])

    client = LlmClient(model=args.model) if args.model else LlmClient()
    kw = dict(max_attempts=args.max_attempts, use_reviewer=not args.no_review)
    log = logging.getLogger("agentic.pipeline")
    log.info("SELECT      %d trial(s) | judge=%s | review=%s -> %s",
             len(trials), not args.no_judge, not args.no_review, out_path.name)

    total = 0
    summaries = []
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=out_cols, delimiter="\t", lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        fh.flush()
        for i, (source, trial_id, base_text, cohorts) in enumerate(trials, 1):
            log.info("")
            log.info("=" * 70)
            log.info("TRIAL %d/%d | [%s] %s | %d chars", i, len(trials), source, trial_id, len(base_text))
            log.info("=" * 70)
            # EXTRACT
            result = extract_trial(client, trial_id=trial_id, source_text=base_text, cohorts=cohorts,
                                   max_attempts=args.max_attempts, use_judge=not args.no_judge)
            rows = [{c: getattr(r, c) for c in TSV_COLUMNS} for r in result.rows]
            # MAP this trial's cells
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
            # DRUG enrichment (trial-level): main/auxiliary + POTTR/general class + TGA/PBS (web search)
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
            writer.writerows(rows)
            fh.flush()
            total += len(rows)
            summaries.append((source, trial_id, len(rows), result.faithful, result.attempts))
            log.info("  DONE        [%s] %s -> +%d row(s) (running total %d)", source, trial_id, len(rows), total)

    log.info("")
    log.info("WRITE       %d trial(s), %d row(s) -> %s", len(trials), total, out_path)
    print(f"\npipeline: {len(trials)} trial(s), {total} row(s) -> {out_path}\n")
    for source, trial_id, n, faithful, attempts in summaries[:40]:
        print(f"  [{source}] {trial_id}: rows={n} faithful={faithful} attempts={attempts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
