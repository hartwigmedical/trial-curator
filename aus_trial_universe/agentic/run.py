"""v2 agentic ELIGIBILITY pipeline orchestrator (spec §6.1) — incremental, lookup-first.

Per trial: EXTRACT eligibility into DNF conjunctions, then MAP each distinct extracted cell to the eligibility
vocabulary — reusing the persistent value->vocabulary cache so only genuinely-NEW values hit the LLM. Drug
annotations are a SEPARATE incremental store (`drug_annotations/`), topped up here for any new drug the trials
introduce (existing drugs = pure lookup — no web search). At the end, one grand flat file joins
eligibility ⋈ vocab maps ⋈ drug annotations on the (trialId, arm) key.

The arm spine (`trial_arms`) is the SHARED central registry at `data/agentic/trial_arms/current_version/`
(written by whichever path processes a trial; both paths link to it by `trial_arm_id`). The eligibility store at
`data/agentic/eligibility/current_output/` holds ONLY its content masters, keyed by trial_arm_id:
    arm_eligibility_raw.tsv · interpreted_eligibility.tsv · cancer_type_map.tsv ·
    gene_alteration_map.tsv · molecular_signature_map.tsv
The grand flat view (`combined.tsv` — the masters joined, for consumers) is DENORMALIZED, not 3NF, so it is
written OUTSIDE the store, to `data/agentic/eligibility/combined/combined.tsv` (single overwritten file).
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

from aus_trial_universe.agentic.core.paths import (
    CACHE_DIR, COMBINED, COMBINED_FILE, ELIG_CURRENT_OUTPUT, ELIGIBILITY_OUTPUT,
)

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
# PARKED (2026-07-24): only built for non-extract-only runs; the current focus is the 3 masters below.
COMBINED_COLUMNS = [
    "trialId", "arm", "arm_type", "conjunction_index",
    "cancer_type_interpreted", "oncotree_name", "oncotree_code",
    "gene_alteration_interpreted", "gene_alteration_findingmodel",
    "molecular_signature_interpreted", "molecular_signature_findingmodel",
    "molecular_biomarker_interpreted", "prior_therapy_interpreted",
    "arm_drugs", "drug_class", "pottr_drug_class",
]


def _arm_rows(trial_id, registry, result):
    """ExtractionResult -> (TrialArm rows [shared registry], ArmEligibilityRaw rows, InterpretedEligibility rows).

    Arm identity comes from result.cohorts (the resolved arms — label + arm_type); the two eligibility content
    tables link to it by trial_arm_id. conjunction_index numbers the DNF conjunctions within an arm from
    result.rows."""
    from aus_trial_universe.agentic.tasks.eligibility.schema import ArmEligibilityRaw, InterpretedEligibility
    from aus_trial_universe.agentic.tasks.shared.cohorts import trial_arm_id
    from aus_trial_universe.agentic.tasks.shared.schema import TrialArm

    arms = [TrialArm(trial_arm_id=trial_arm_id(trial_id, c.label), trialId=trial_id, registry=registry,
                     arm=c.label, arm_type=c.arm_type) for c in result.cohorts]
    raw = [ArmEligibilityRaw(
        trial_arm_id=trial_arm_id(trial_id, ar.arm), cancer_type_raw=ar.cancer_type_raw,
        gene_alteration_raw=ar.gene_alteration_raw, molecular_signature_raw=ar.molecular_signature_raw,
        molecular_biomarker_raw=ar.molecular_biomarker_raw, prior_therapy_raw=ar.prior_therapy_raw,
    ) for ar in result.arm_raw]

    conj: dict[str, int] = {}
    interp: list[InterpretedEligibility] = []
    for r in result.rows:
        taid = trial_arm_id(r.trialId, r.cohort)
        conj[taid] = conj.get(taid, 0) + 1
        interp.append(InterpretedEligibility(
            trial_arm_id=taid, conjunction_index=conj[taid],
            cancer_type_interpreted=r.cancer_type, gene_alteration_interpreted=r.gene_alteration,
            molecular_signature_interpreted=r.molecular_signature,
            molecular_biomarker_interpreted=r.molecular_biomarker, prior_therapy_interpreted=r.prior_therapy))
    return arms, raw, interp


def _drug_occurrences(result, strip_provenance):
    """Distinct (trial_arm_id, drug_name) occurrences + distinct drug names from a trial's DNF rows."""
    from aus_trial_universe.agentic.tasks.shared.cohorts import trial_arm_id
    occ: list[tuple] = []
    names: list[str] = []
    seen: set[tuple] = set()
    for r in result.rows:
        taid = trial_arm_id(r.trialId, r.cohort)
        for name in strip_provenance(r.drug).split(";"):
            name = name.strip()
            if not name:
                continue
            key = (taid, name)
            if key in seen:
                continue
            seen.add(key)
            occ.append((taid, name))
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


def _arm_drug_facts(drug_store, trial_arm_id_value):
    """Join to the drug store on trial_arm_id: distinct canonical drug names + classes + POTTR classes."""
    if drug_store is None:
        return "", "", ""
    inputs = [o.input_intervention_name for (taid, _inp), o in drug_store.occurrences.items()
              if taid == trial_arm_id_value]
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


def _build_combined(elig_store, arm_store, drug_store, strip_provenance) -> list[dict]:
    """Materialize the grand flat view: interpreted eligibility ⋈ shared trial_arms ⋈ vocab maps ⋈ drug
    annotations, all on trial_arm_id.

    PARKED (2026-07-24) — regenerated only for non-extract-only runs; kept correct so `make agentic-run` works."""
    from aus_trial_universe.agentic.tasks.shared.cohorts import trial_id_of
    rows: list[dict] = []
    arm_by_id = {a.trial_arm_id: a for arms in arm_store.arms.values() for a in arms}
    drug_cache: dict[str, tuple] = {}
    for _trial_id, elig_rows in elig_store.interpreted.items():
        for e in elig_rows:
            ct = elig_store.lookup_cancer_type(strip_provenance(e.cancer_type_interpreted))
            ga = elig_store.lookup_gene_alteration(strip_provenance(e.gene_alteration_interpreted))
            sig = elig_store.lookup_molecular_signature(strip_provenance(e.molecular_signature_interpreted))
            ta = arm_by_id.get(e.trial_arm_id)
            if e.trial_arm_id not in drug_cache:
                drug_cache[e.trial_arm_id] = _arm_drug_facts(drug_store, e.trial_arm_id)
            arm_drugs, drug_class, pottr = drug_cache[e.trial_arm_id]
            rows.append({
                "trialId": ta.trialId if ta else trial_id_of(e.trial_arm_id),
                "arm": ta.arm if ta else "", "arm_type": ta.arm_type if ta else "",
                "conjunction_index": e.conjunction_index,
                "cancer_type_interpreted": e.cancer_type_interpreted,
                "oncotree_name": ct.oncotree_name if ct else "", "oncotree_code": ct.oncotree_code if ct else "",
                "gene_alteration_interpreted": e.gene_alteration_interpreted,
                "gene_alteration_findingmodel": ga.finding_model if ga else "",
                "molecular_signature_interpreted": e.molecular_signature_interpreted,
                "molecular_signature_findingmodel": sig.finding_model if sig else "",
                "molecular_biomarker_interpreted": e.molecular_biomarker_interpreted,
                "prior_therapy_interpreted": e.prior_therapy_interpreted,
                "arm_drugs": arm_drugs, "drug_class": drug_class, "pottr_drug_class": pottr,
            })
    return rows


def _run_map_only(client, elig_store, run_dir, *, workers, max_attempts, use_reviewer, log) -> int:
    """MAPPING-ONLY pass (Step 1): map every DISTINCT interpreted cell of the loaded store into the three
    value->vocabulary tables, then save. The raw + interpreted content tables are read-only here (never re-derived);
    only the 3 map tables are (re)built. Fresh — every distinct value is mapped, not looked up against prior maps."""
    from aus_trial_universe.agentic.tasks.eligibility.mapping.workflow import (
        map_cancer_types, map_gene_alterations, map_molecular_signatures,
    )
    from aus_trial_universe.agentic.tasks.eligibility.schema import (
        CancerTypeMap, GeneAlterationMap, MolecularSignatureMap,
    )
    from aus_trial_universe.agentic.core.logfmt import stage
    rows = [e for elist in elig_store.interpreted.values() for e in elist]
    log.info(stage(f"MAPPING (map-only) · {len(rows)} interpreted row(s) · {workers} workers"))
    kw = dict(max_attempts=max_attempts, use_reviewer=use_reviewer, workers=workers)

    ct = map_cancer_types(client, [r.cancer_type_interpreted for r in rows], **kw)
    for v, r in ct.items():
        elig_store.put_cancer_type(CancerTypeMap(cancer_type=v, oncotree_name=r.oncotree_name, oncotree_code=r.oncotree_code))
    ga = map_gene_alterations(client, [r.gene_alteration_interpreted for r in rows], **kw)
    for v, r in ga.items():
        elig_store.put_gene_alteration(GeneAlterationMap(gene_alteration=v, finding_model=r.finding_model))
    sig = map_molecular_signatures(client, [r.molecular_signature_interpreted for r in rows], **kw)
    for v, r in sig.items():
        elig_store.put_molecular_signature(MolecularSignatureMap(molecular_signature=v, finding_model=r.finding_model))

    elig_store.save(run_dir)   # writes all 5 tables; raw + interpreted are re-persisted unchanged, + the 3 maps

    def _stats(d, empty):
        return f"{len(d)} distinct · {sum(1 for r in d.values() if not empty(r))} mapped · " \
               f"{sum(1 for r in d.values() if empty(r))} empty · {sum(1 for r in d.values() if not r.faithful)} unfaithful"
    print(f"\n{'═' * 70}\nMAP-ONLY · 3 map table(s) → {run_dir}/\n")
    print(f"  cancer_type         · {_stats(ct, lambda r: not r.oncotree_code)}")
    print(f"  gene_alteration     · {_stats(ga, lambda r: not r.finding_model)}")
    print(f"  molecular_signature · {_stats(sig, lambda r: not r.finding_model)}")
    return 0


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
    parser.add_argument("--map-only", action="store_true",
                        help="MAPPING only — map the distinct interpreted cells of the EXISTING store into the 3 "
                             "value->vocabulary tables (cancer_type/gene_alteration/molecular_signature), then save. "
                             "Skips extraction, drug top-up and combined; the raw + interpreted content tables are "
                             "left untouched. Every distinct value is mapped afresh (no reliance on prior maps).")
    parser.add_argument("--skip-drug", action="store_true",
                        help="Skip the incremental drug-annotation top-up (still joins existing drug data).")
    parser.add_argument("--no-cache", action="store_true",
                        help="Disable the on-disk LLM response cache (default: cache under data/agentic/cache/, "
                             "so re-runs of unchanged trials/values are near-instant).")
    parser.add_argument("--no-cache-prune", action="store_true",
                        help="Skip the automatic prune of cache entries from OUTDATED prompts at run start "
                             "(default: on when the cache is enabled; removes only stale entries, never live/legacy).")
    parser.add_argument("--workers", type=int, default=8,
                        help="Trials extracted in PARALLEL (default 8). Each trial also fans out its reviewer panel, "
                             "so true peak concurrency ≈ workers × fan_out unless --max-concurrency caps it. Logs "
                             "interleave at >1 — review the output TSVs, not the live log. Use 1 for a readable log.")
    parser.add_argument("--max-concurrency", type=int, default=None,
                        help="GLOBAL cap on concurrent LLM API calls across all trials × their reviewer fan-outs "
                             "(the deterministic governor for large runs; set it to the account's empirical "
                             "rate-limit ceiling and raise --workers freely). Default: uncapped.")
    parser.add_argument("--max-attempts", type=int, default=6,
                        help="UPPER bound on refine attempts (default 6). Not a fixed count: lenient reviewers let "
                             "standard trials pass in 1, and refine returns the best attempt + stops early once it "
                             "stops converging — so only genuinely-hard trials use the full budget.")
    parser.add_argument("--resume", action="store_true",
                        help="Skip trials already present in the store (process only the not-yet-done ones) and "
                             "EXIT NON-ZERO if any requested trial is still missing afterwards — so a loop driver "
                             "can re-run until complete. Combined with the per-trial checkpoint, a resumed run "
                             "redoes at most the trial that was in flight.")
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
    from aus_trial_universe.agentic.tasks.shared.store import TrialArmStore
    from aus_trial_universe.agentic.core import paths as _paths   # read TRIAL_ARMS_ROOT dynamically (test-redirectable)

    ids = [x for x in args.ids.split(",")] if args.ids else None
    # map-only works off the EXISTING store (the 3 map tables over interpreted_eligibility), so it needs no trial
    # inputs — skip the (potentially all-1,999) input load entirely.
    trials = [] if args.map_only else load_trials(id=args.id, ids=ids)
    if not args.map_only and not trials:
        parser.error("no trials with usable text found")
    requested_ids = [t[1] for t in trials]   # display ids of everything asked for (before any --resume filter)

    store_root = Path(args.store_root) if args.store_root else ELIGIBILITY_OUTPUT
    # The live accumulating store is `current_output/` (current/archive pattern, parallels drug_annotations'
    # current_version/): each run loads it, upserts, and writes it back in place. Superseded runs are archived
    # manually (move current_output/ -> archive/<date>/). `--out-dir` overrides for a one-off/isolated run.
    run_dir = Path(args.out_dir) if args.out_dir else store_root / ELIG_CURRENT_OUTPUT
    # The joined flat view is denormalized (NOT 3NF), so it is written OUTSIDE the store dir — into a sibling
    # `combined/` under the store root (default: data/agentic/eligibility/combined/). current_output/ holds only
    # the pure-3NF masters. Single overwritten file, regenerable from the store.
    combined_dir = store_root / COMBINED

    cache = None if args.no_cache else DiskCache(CACHE_DIR)
    _client_kw = dict(cache=cache, max_concurrency=args.max_concurrency)
    client = LlmClient(model=args.model, **_client_kw) if args.model else LlmClient(**_client_kw)
    kw = dict(max_attempts=args.max_attempts, use_reviewer=not args.no_review)
    log = logging.getLogger("agentic.pipeline")

    # GC cache entries from outdated prompts before we start (only stale entries; never live/legacy).
    if cache is not None and not args.no_cache_prune:
        from aus_trial_universe.agentic.core.cache_prune import prune_cache, summary_line
        rep = prune_cache(CACHE_DIR, apply=True)
        if rep.removed:
            log.info(summary_line(rep))
    log.info("run · %d trial(s) · judge=%s · review=%s · extract_only=%s · skip_drug=%s → %s/",
             len(trials), not args.no_judge, not args.no_review, args.extract_only, args.skip_drug, run_dir.name)

    trial_arms_root = _paths.TRIAL_ARMS_ROOT  # the SHARED registry's home (read at call time so tests can redirect)
    elig_store = EligStore.load(store_root)   # load the latest EXISTING snapshot before creating this run's dir
    run_dir.mkdir(parents=True, exist_ok=True)
    if args.map_only:   # MAPPING-only: map the store's interpreted cells -> 3 map tables; content tables untouched
        return _run_map_only(client, elig_store, run_dir, workers=args.workers, max_attempts=args.max_attempts,
                             use_reviewer=not args.no_review, log=log)
    arm_store = TrialArmStore.load(trial_arms_root)   # the SHARED arm registry (accumulates across both paths + runs)
    if args.resume:   # process only the not-yet-done trials (the per-trial checkpoint makes this safe)
        _pending = [t for t in trials if not elig_store.has_trial(t[1])]
        log.info("resume · %d/%d requested already in store → %d to process",
                 len(trials) - len(_pending), len(trials), len(_pending))
        trials = _pending
    # ANZCTR arms are derived FRESH per trial via the shared cohort-identification module (inside extract_trial —
    # CTGov trials pass their deterministic armGroup cohorts, ANZCTR trials pass None and derive). The resolved
    # arms are written to the shared trial_arms registry; the drug store's trial_to_intervention is re-keyed to
    # them in a SEPARATE post-run migration step. The drug store is only needed for the map/drug stages.
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

    # Offline circuit-breaker (only under --resume, where the loop retries): if many trials fail back-to-back it
    # is almost certainly a lost connection, so stop burning the rest as failures — defer them for the next
    # --resume pass instead. Trials already in flight finish; queued ones skip fast.
    circuit = {"open": False, "consec_fail": 0}
    _OFFLINE_N = max(8, args.workers)   # ~one full wave of consecutive failures signals an outage

    class _CircuitOpen(RuntimeError):
        pass

    def _work(item):
        if circuit["open"]:
            raise _CircuitOpen()
        source, trial_id, base_text, cohorts = item
        t0 = time.perf_counter()
        result = extract_trial(client, trial_id=trial_id, source_text=base_text, cohorts=cohorts,
                               max_attempts=args.max_attempts, use_judge=not args.no_judge)
        maps = None if args.extract_only else _compute_new_maps(client, elig_store, result.rows, strip_provenance, kw)
        return result, maps, time.perf_counter() - t0

    def _sink(item, res, exc):   # runs single-threaded, in completion order — the durable write point
        source, trial_id, _text, _cohorts = item
        if isinstance(exc, _CircuitOpen):
            log.info("  ⏸ %s · deferred (offline — will resume)", trial_id)
            return
        if exc is not None or res is None:
            circuit["consec_fail"] += 1
            failures.append((trial_id, f"{type(exc).__name__}: {exc}" if exc else "no result"))
            log.info("  FAILED · %s · %s (skipped)", trial_id, type(exc).__name__ if exc else "no result")
            if args.resume and not circuit["open"] and circuit["consec_fail"] >= _OFFLINE_N:
                circuit["open"] = True
                log.info("  ⏸ circuit OPEN after %d consecutive failures — likely offline; deferring the rest "
                         "(re-run with --resume when back online)", circuit["consec_fail"])
            return
        circuit["consec_fail"] = 0
        result, maps, t_work = res
        if maps is not None:
            ct, ga, sig = maps
            for m in ct:
                elig_store.put_cancer_type(m)
            for m in ga:
                elig_store.put_gene_alteration(m)
            for m in sig:
                elig_store.put_molecular_signature(m)
        arm_rows, raw_rows, interp_rows = _arm_rows(trial_id, source, result)
        arm_store.set_trial_arms(trial_id, arm_rows)
        elig_store.set_trial(trial_id, raw_rows, interp_rows)
        if not args.extract_only:
            occ, names = _drug_occurrences(result, strip_provenance)
            all_occ.extend(occ)
            for n in names:
                if n not in all_drug_names:
                    all_drug_names.append(n)
        arm_store.save(trial_arms_root)   # PER-TRIAL CHECKPOINT — trial_arms (shared registry) + the eligibility
        elig_store.save(run_dir)          #   tables are written together the moment the trial completes (durable)
        nonlocal_total[0] += len(interp_rows)
        summaries.append((source, trial_id, len(interp_rows), result.faithful, result.attempts))
        trial_times.append((trial_id, t_work, len(interp_rows)))
        log.info("  ✓ %s · %d conjunction(s) · %d arm(s) · %.0fs · %d/%d saved → %s/", trial_id, len(interp_rows),
                 len(arm_rows), t_work, len(summaries), len(trials), run_dir.name)

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

    # --- persist the 3NF store snapshots, then the joined flat view (elsewhere) - #
    arm_store.save(trial_arms_root)                # the shared trial_arms registry
    elig_store.save(run_dir)                       # the pure-3NF masters -> run_dir (current_output/)
    # combined.tsv is PARKED (2026-07-24): the grand flat join is being reworked, so it is skipped for
    # extraction-only runs (the current 3-table focus). Non-extract-only runs still materialize it.
    combined_note = " · combined SKIPPED (parked)"
    if not args.extract_only:
        combined = _build_combined(elig_store, arm_store, drug_store, strip_provenance)
        combined_dir.mkdir(parents=True, exist_ok=True)
        with open(combined_dir / COMBINED_FILE, "w", newline="", encoding="utf-8") as fc:
            w = csv.DictWriter(fc, fieldnames=COMBINED_COLUMNS, delimiter="\t", lineterminator="\n",
                               extrasaction="ignore")
            w.writeheader()
            w.writerows(combined)
        combined_note = f" · combined → {combined_dir}/"

    print(f"\n{'═' * 70}\n{len(trials)} trial(s): {len(summaries)} ok, {len(failures)} failed · "
          f"{total} conjunction(s) · 3NF → {run_dir}/{combined_note}\n")
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

    # Under --resume, signal completeness via the exit code so a loop driver knows whether to re-run: 0 = every
    # requested trial is now in the store; 3 = some are still missing (failed / deferred / offline) -> retry.
    if args.resume:
        missing = [i for i in requested_ids if not elig_store.has_trial(i)]
        if missing:
            print(f"\n  INCOMPLETE · {len(missing)}/{len(requested_ids)} requested trial(s) still missing "
                  f"(e.g. {', '.join(missing[:5])}{' …' if len(missing) > 5 else ''}) — re-run with --resume\n")
            return 3
        print(f"\n  COMPLETE · all {len(requested_ids)} requested trial(s) present in the store\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
