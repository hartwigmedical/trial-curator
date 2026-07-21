"""v2 agentic ELIGIBILITY pipeline orchestrator (spec §6.1) — incremental, lookup-first.

Per trial: EXTRACT eligibility into DNF conjunctions, then MAP each distinct extracted cell to the eligibility
vocabulary — reusing the persistent value->vocabulary cache so only genuinely-NEW values hit the LLM. Drug
annotations are a SEPARATE incremental store (`drug_annotations/`), topped up here for any new drug the trials
introduce (existing drugs = pure lookup — no web search). At the end, one grand flat file joins
eligibility ⋈ vocab maps ⋈ drug annotations on the (trialId, arm) key.

The eligibility relational tables are an accumulating store: each run writes a fresh date-stamped full-state
snapshot to `data/agentic/eligibility/<YYYYMMDD_HHMMSS>/`:
    regime.tsv · extracted_eligibility.tsv · cancer_type_map.tsv · gene_alteration_map.tsv ·
    molecular_signature_map.tsv        (the 3NF masters)
    combined.tsv                       (the grand flat view — masters joined, for consumers)
Re-running a trial replaces its rows in place. The drug store persists to `drug_annotations/current_version/`.

  python -m aus_trial_universe.agentic.run --id NCT06881784          # one trial
  python -m aus_trial_universe.agentic.run --ids NCT1,ACTRN2         # a specific set
  python -m aus_trial_universe.agentic.run                          # ALL trials
Options: --model · --no-judge (skip extraction panel) · --no-review (skip mapping reviewers) ·
         --extract-only (extraction only) · --skip-drug (skip the drug-annotation top-up) · --out-dir · --max-attempts
Requires OPENAI_API_KEY (auto-loaded from .env / .env.local). Run via `make agentic-run`.
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import time
from datetime import datetime
from pathlib import Path

from aus_trial_universe.agentic.core.paths import CACHE_DIR, ELIG_CURRENT_OUTPUT, ELIGIBILITY_OUTPUT

REPO_ROOT = Path(__file__).resolve().parents[2]


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


# The grand flat view (combined.tsv): eligibility ⋈ vocab maps ⋈ drug annotations, one row per DNF conjunction.
COMBINED_COLUMNS = [
    "trialId", "arm", "arm_type", "conj_id",
    "cancer_type", "oncotree_name", "oncotree_code",
    "gene_alteration", "gene_alteration_findingmodel",
    "molecular_signature", "molecular_signature_findingmodel",
    "molecular_biomarker", "prior_therapy",
    "arm_drugs", "drug_class", "pottr_drug_class",
]


def _regimes_and_rows(result):
    """DnfRow list -> (Regime rows, ExtractedEligibility rows). conj_id numbers conjunctions within (trialId, arm)."""
    from aus_trial_universe.agentic.tasks.eligibility.schema import ExtractedEligibility, Regime

    regimes: dict[tuple, str] = {}
    for r in result.rows:
        regimes.setdefault((r.trialId, r.cohort), r.arm_type)
    regime_rows = [Regime(trialId=t, arm=a, arm_type=at) for (t, a), at in regimes.items()]

    conj: dict[tuple, int] = {}
    elig_rows: list[ExtractedEligibility] = []
    for r in result.rows:
        key = (r.trialId, r.cohort)
        conj[key] = conj.get(key, 0) + 1
        elig_rows.append(ExtractedEligibility(
            trialId=r.trialId, arm=r.cohort, conj_id=conj[key],
            cancer_type=r.cancer_type, gene_alteration=r.gene_alteration,
            molecular_signature=r.molecular_signature, molecular_biomarker=r.molecular_biomarker,
            prior_therapy=r.prior_therapy))
    return regime_rows, elig_rows


def _drug_occurrences(result, registry, strip_provenance):
    """Distinct (trialId, registry, arm, arm_type, drug_name) + distinct drug names from a trial's DNF rows."""
    occ: list[tuple] = []
    names: list[str] = []
    seen: set[tuple] = set()
    for r in result.rows:
        for name in strip_provenance(r.drug).split(";"):
            name = name.strip()
            if not name:
                continue
            key = (r.trialId, r.cohort, name)
            if key in seen:
                continue
            seen.add(key)
            occ.append((r.trialId, registry, r.cohort, r.arm_type, name))
            if name not in names:
                names.append(name)
    return occ, names


def _compute_new_maps(client, elig_store, elig_rows, strip_provenance, kw):
    """Lookup-first mapping (COMPUTE ONLY — no store mutation, so it is safe to call from a worker thread).

    Reads the store to skip values already mapped (lookup-first; the DiskCache dedups any identical value mapped
    concurrently by another trial), maps the rest, and RETURNS the new mappings as ready-to-store rows. The caller
    applies + persists them in the single-threaded per-item sink."""
    from aus_trial_universe.agentic.tasks.eligibility.mapping.workflow import (
        map_cancer_types, map_gene_alterations, map_molecular_signatures,
    )
    from aus_trial_universe.agentic.tasks.eligibility.schema import (
        CancerTypeMap, GeneAlterationMap, MolecularSignatureMap,
    )

    def _new(cells, lookup):
        vals = list(dict.fromkeys(strip_provenance(c) for c in cells if strip_provenance(c)))
        return [v for v in vals if lookup(v) is None]

    ct = [CancerTypeMap(cancer_type=v, oncotree_name=r.oncotree_name, oncotree_code=r.oncotree_code)
          for v, r in map_cancer_types(client, _new([r.cancer_type for r in elig_rows],
                                                    elig_store.lookup_cancer_type), **kw).items()]
    ga = [GeneAlterationMap(gene_alteration=v, finding_model=r.finding_model)
          for v, r in map_gene_alterations(client, _new([r.gene_alteration for r in elig_rows],
                                                        elig_store.lookup_gene_alteration), **kw).items()]
    sig = [MolecularSignatureMap(molecular_signature=v, finding_model=r.finding_model)
           for v, r in map_molecular_signatures(client, _new([r.molecular_signature for r in elig_rows],
                                                             elig_store.lookup_molecular_signature), **kw).items()]
    return ct, ga, sig


def _arm_drug_facts(drug_store, trial_id, arm):
    """Join to the drug store on (trialId, arm): distinct canonical drug names + classes + POTTR classes."""
    if drug_store is None:
        return "", "", ""
    inputs = [o.input_intervention_name for (t, _reg, a, _inp), o in drug_store.occurrences.items()
              if t == trial_id and a == arm]
    cids: list[str] = []
    for inp in inputs:
        for cid in drug_store.canonical_ids_for(inp):
            if cid not in cids:
                cids.append(cid)
    names, classes, pottr = [], [], []
    for cid in cids:
        ref = drug_store.ref(cid)
        if not ref:
            continue
        for lst, val in ((names, ref.canonical_name), (classes, ref.drug_class), (pottr, ref.pottr_drug_class)):
            if val and val not in lst:
                lst.append(val)
    return "; ".join(names), "; ".join(classes), " | ".join(pottr)


def _build_combined(elig_store, drug_store, strip_provenance) -> list[dict]:
    """Materialize the grand flat view: eligibility ⋈ vocab maps ⋈ drug annotations on (trialId, arm)."""
    rows: list[dict] = []
    arm_type = {(r.trialId, r.arm): r.arm_type for regs in elig_store.regimes.values() for r in regs}
    drug_cache: dict[tuple, tuple] = {}
    for _trial_id, elig_rows in elig_store.eligibility.items():
        for e in elig_rows:
            ct = elig_store.lookup_cancer_type(strip_provenance(e.cancer_type))
            ga = elig_store.lookup_gene_alteration(strip_provenance(e.gene_alteration))
            sig = elig_store.lookup_molecular_signature(strip_provenance(e.molecular_signature))
            key = (e.trialId, e.arm)
            if key not in drug_cache:
                drug_cache[key] = _arm_drug_facts(drug_store, e.trialId, e.arm)
            arm_drugs, drug_class, pottr = drug_cache[key]
            rows.append({
                "trialId": e.trialId, "arm": e.arm, "arm_type": arm_type.get(key, ""), "conj_id": e.conj_id,
                "cancer_type": e.cancer_type,
                "oncotree_name": ct.oncotree_name if ct else "", "oncotree_code": ct.oncotree_code if ct else "",
                "gene_alteration": e.gene_alteration,
                "gene_alteration_findingmodel": ga.finding_model if ga else "",
                "molecular_signature": e.molecular_signature,
                "molecular_signature_findingmodel": sig.finding_model if sig else "",
                "molecular_biomarker": e.molecular_biomarker, "prior_therapy": e.prior_therapy,
                "arm_drugs": arm_drugs, "drug_class": drug_class, "pottr_drug_class": pottr,
            })
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="v2 agentic eligibility pipeline (incremental, lookup-first).")
    which = parser.add_mutually_exclusive_group()
    which.add_argument("--id", help="Run one trial (source auto-detected from the id).")
    which.add_argument("--ids", help="Run a specific set: comma-separated ids.")
    parser.add_argument("--store-root", default=None,
                        help="Accumulating store root (default: data/agentic/eligibility/). Load reads its newest "
                             "snapshot; the run writes a new one under it.")
    parser.add_argument("--out-dir", help="Explicit snapshot dir for this run (default: <store-root>/<timestamp>/).")
    parser.add_argument("--model", default=None, help="Override the OpenAI model.")
    parser.add_argument("--no-judge", action="store_true", help="Skip the extraction reviewer panel (cheaper).")
    parser.add_argument("--no-review", action="store_true", help="Skip the mapping reviewers (cheaper).")
    parser.add_argument("--extract-only", action="store_true",
                        help="Extraction only — skip mapping + drug top-up.")
    parser.add_argument("--skip-drug", action="store_true",
                        help="Skip the incremental drug-annotation top-up (still joins existing drug data).")
    parser.add_argument("--no-cache", action="store_true",
                        help="Disable the on-disk LLM response cache (default: cache under data/agentic/cache/, "
                             "so re-runs of unchanged trials/values are near-instant).")
    parser.add_argument("--workers", type=int, default=8,
                        help="Trials extracted in PARALLEL (default 8 — highest reasonable concurrency; each trial "
                             "is independent). Logs interleave at >1 — review the output TSVs, not the live log. "
                             "Use 1 for a readable single-trial log.")
    parser.add_argument("--max-attempts", type=int, default=6,
                        help="UPPER bound on refine attempts (default 6). Not a fixed count: lenient reviewers let "
                             "standard trials pass in 1, and refine returns the best attempt + stops early once it "
                             "stops converging — so only genuinely-hard trials use the full budget.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for _noisy in ("httpx", "openai", "urllib3", "numexpr"):
        logging.getLogger(_noisy).setLevel(logging.WARNING)
    _load_openai_key()

    from aus_trial_universe.agentic.core.client import DiskCache, LlmClient
    from aus_trial_universe.agentic.core.logfmt import FAIL, PASS, stage
    from aus_trial_universe.agentic.core.workflow import run_parallel
    from aus_trial_universe.agentic.tasks.drug_utility.store import DrugRefStore
    from aus_trial_universe.agentic.tasks.drug_utility.workflow import build_drug_ref
    from aus_trial_universe.agentic.tasks.eligibility.extraction.loaders import load_trials
    from aus_trial_universe.agentic.tasks.eligibility.extraction.workflow import extract_trial
    from aus_trial_universe.agentic.tasks.eligibility.mapping.workflow import strip_provenance
    from aus_trial_universe.agentic.tasks.eligibility.store import EligStore

    ids = [x for x in args.ids.split(",")] if args.ids else None
    trials = load_trials(id=args.id, ids=ids)
    if not trials:
        parser.error("no trials with usable text found")

    store_root = Path(args.store_root) if args.store_root else ELIGIBILITY_OUTPUT
    # The live accumulating store is `current_output/` (current/archive pattern, parallels drug_annotations'
    # current_version/): each run loads it, upserts, and writes it back in place. Superseded runs are archived
    # manually (move current_output/ -> archive/<date>/). `--out-dir` overrides for a one-off/isolated run.
    run_dir = Path(args.out_dir) if args.out_dir else store_root / ELIG_CURRENT_OUTPUT

    cache = None if args.no_cache else DiskCache(CACHE_DIR)
    client = LlmClient(model=args.model, cache=cache) if args.model else LlmClient(cache=cache)
    kw = dict(max_attempts=args.max_attempts, use_reviewer=not args.no_review)
    log = logging.getLogger("agentic.pipeline")
    log.info("run · %d trial(s) · judge=%s · review=%s · extract_only=%s · skip_drug=%s → %s/",
             len(trials), not args.no_judge, not args.no_review, args.extract_only, args.skip_drug, run_dir.name)

    elig_store = EligStore.load(store_root)   # load the latest EXISTING snapshot before creating this run's dir
    run_dir.mkdir(parents=True, exist_ok=True)
    drug_store = DrugRefStore.load() if not args.extract_only else None
    all_occ: list[tuple] = []
    all_drug_names: list[str] = []

    total = 0
    summaries: list[tuple] = []
    failures: list[tuple[str, str]] = []
    trial_times: list[tuple[str, float, int]] = []          # (trial_id, work_s, n_conj)
    t_phase = time.perf_counter()

    # Each trial is extracted + mapped in a WORKER thread (parallel); the finished trial is then applied to the
    # store and SAVED in this thread, one at a time, the moment it completes (run_parallel's per-item sink). So a
    # completed trial is written straightaway — a crash / lost connection loses only the still-running trials,
    # never a finished one. (The DiskCache also preserves every LLM response, so a re-run resumes the rest cheaply.)
    log.info("")
    log.info(stage(f"EXTRACTION + MAPPING · {len(trials)} trial(s) · {args.workers} parallel · per-trial checkpoint"))

    def _work(item):
        source, trial_id, base_text, cohorts = item
        t0 = time.perf_counter()
        result = extract_trial(client, trial_id=trial_id, source_text=base_text, cohorts=cohorts,
                               max_attempts=args.max_attempts, use_judge=not args.no_judge)
        maps = None if args.extract_only else _compute_new_maps(client, elig_store, result.rows, strip_provenance, kw)
        return result, maps, time.perf_counter() - t0

    def _sink(item, res, exc):   # runs single-threaded, in completion order — the durable write point
        source, trial_id, _text, _cohorts = item
        if exc is not None or res is None:
            failures.append((trial_id, f"{type(exc).__name__}: {exc}" if exc else "no result"))
            log.info("  FAILED · %s · %s (skipped)", trial_id, type(exc).__name__ if exc else "no result")
            return
        result, maps, t_work = res
        if maps is not None:
            ct, ga, sig = maps
            for m in ct:
                elig_store.put_cancer_type(m)
            for m in ga:
                elig_store.put_gene_alteration(m)
            for m in sig:
                elig_store.put_molecular_signature(m)
        regime_rows, elig_rows = _regimes_and_rows(result)
        elig_store.set_trial(trial_id, regime_rows, elig_rows)
        if not args.extract_only:
            occ, names = _drug_occurrences(result, source, strip_provenance)
            all_occ.extend(occ)
            for n in names:
                if n not in all_drug_names:
                    all_drug_names.append(n)
        elig_store.save(run_dir)   # PER-TRIAL CHECKPOINT — the completed trial is now durably on disk
        nonlocal_total[0] += len(elig_rows)
        summaries.append((source, trial_id, len(elig_rows), result.faithful, result.attempts))
        trial_times.append((trial_id, t_work, len(elig_rows)))
        log.info("  ✓ %s · %d conjunction(s) · %d arm(s) · %.0fs · %d/%d saved → %s/", trial_id, len(elig_rows),
                 len(regime_rows), t_work, len(summaries), len(trials), run_dir.name)

    nonlocal_total = [0]   # mutable cell (assigned inside the nested sink)
    run_parallel(trials, _work, _sink, max_workers=args.workers)
    total = nonlocal_total[0]
    timing = {"extract_map": time.perf_counter() - t_phase, "drug": 0.0}

    # --- incremental drug-annotation top-up (only NEW drugs are researched) --- #
    if not args.extract_only and not args.skip_drug and all_drug_names:
        log.info("")
        log.info(stage("DRUG top-up (incremental)"))
        t2 = time.perf_counter()
        build_drug_ref(client, all_drug_names, drug_store, occurrences=all_occ,
                       use_reviewer=not args.no_review, max_attempts=args.max_attempts,
                       checkpoint=lambda: drug_store.save())
        drug_store.save()
        timing["drug"] = time.perf_counter() - t2

    # --- persist the eligibility snapshot + the grand flat view --------------- #
    elig_store.save(run_dir)
    combined = _build_combined(elig_store, drug_store, strip_provenance)  # drug_store None (extract-only) -> no drug cols
    with open(run_dir / "combined.tsv", "w", newline="", encoding="utf-8") as fc:
        w = csv.DictWriter(fc, fieldnames=COMBINED_COLUMNS, delimiter="\t", lineterminator="\n",
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(combined)

    print(f"\n{'═' * 70}\n{len(trials)} trial(s): {len(summaries)} ok, {len(failures)} failed · "
          f"{total} conjunction(s) → {run_dir}/\n")
    for source, trial_id, n, faithful, attempts in summaries[:40]:
        print(f"  {PASS if faithful else FAIL}  {trial_id} · conjunctions={n} · attempts={attempts}")
    for trial_id, err in failures:
        print(f"  FAILED  {trial_id} · {err[:90]}")

    # Timing breakdown — which stage dominates. extract+map is the parallel per-trial phase (its wall-clock ≈ the
    # slowest trial, not the sum); drug enrichment is now a SEPARATE stage that only researches genuinely-new drugs.
    print(f"\n  timing (wall-clock) · extract+map {timing['extract_map']:.0f}s (parallel x{args.workers}) · "
          f"drug-topup {timing['drug']:.0f}s")
    for trial_id, t_work, n in sorted(trial_times, key=lambda x: -x[1])[:5]:
        print(f"    slowest trial: {trial_id} · {t_work:.0f}s · {n} conjunction(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
