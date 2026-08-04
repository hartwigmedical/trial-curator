"""v2 agentic ELIGIBILITY pipeline orchestrator (spec §6.1) — incremental, lookup-first.

Per trial: EXTRACT eligibility into DNF conjunctions, then MAP each distinct extracted cell to the eligibility
vocabulary — reusing the persistent value->vocabulary cache so only genuinely-NEW values hit the LLM. Drug
annotations are a SEPARATE incremental store (`drug_annotations/`), topped up here for any new drug the trials
introduce (existing drugs = pure lookup — no web search).

The arm spine (`trial_arms`) is the SHARED central registry at `data/agentic/trial_arms/current_version/`
(written by whichever path processes a trial; both paths link to it by `trial_arm_id`). The eligibility store at
`data/agentic/eligibility/current_output/` holds ONLY its content masters, keyed by trial_arm_id:
    arm_eligibility_raw.tsv · interpreted_eligibility.tsv · cancer_type_map.tsv ·
    gene_alteration_map.tsv · molecular_signature_map.tsv
Re-running a trial replaces its rows in place. The drug store persists to `drug_annotations/current_version/`.
The grand flat matching-engine view (`trial_eligibility.tsv`, the masters joined for consumers) is built by the
SEPARATE `aus_trial_universe.export` module (`make agentic-export`), not here.

  python -m aus_trial_universe.run --id NCT06881784          # one trial
  python -m aus_trial_universe.run --ids NCT1,ACTRN2         # a specific set
  python -m aus_trial_universe.run                          # ALL trials
Options: --model · --no-judge (skip extraction panel) · --no-review (skip mapping reviewers) ·
         --extract-only (extraction only) · --skip-drug (skip the drug-annotation top-up) · --out-dir · --max-attempts
Requires OPENAI_API_KEY (auto-loaded from .env / .env.local). Run via `make agentic-run`.
"""
from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import datetime
from pathlib import Path

from aus_trial_universe.core.paths import (
    CACHE_DIR, CURRENT_VERSION, ELIGIBILITY_OUTPUT, JOINED_ELIGIBILITY, JOINED_ROOT,
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


def _arm_rows(trial_id, registry, result):
    """ExtractionResult -> (TrialArm rows [shared registry], ArmEligibilityRaw rows, InterpretedEligibility rows).

    Arm identity comes from result.cohorts (the resolved arms — label + arm_type); the two eligibility content
    tables link to it by trial_arm_id. conjunction_index numbers the DNF conjunctions within an arm from
    result.rows."""
    from aus_trial_universe.tasks.eligibility.schema import ArmEligibilityRaw, InterpretedEligibility
    from aus_trial_universe.tasks.shared.cohorts import trial_arm_id
    from aus_trial_universe.tasks.shared.schema import TrialArm

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
    from aus_trial_universe.tasks.shared.cohorts import trial_arm_id
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
    from aus_trial_universe.tasks.eligibility.mapping.workflow import (
        map_cancer_types, map_gene_alterations, map_molecular_signatures,
    )
    from aus_trial_universe.tasks.eligibility.schema import (
        CancerTypeMap, GeneAlterationMap, MolecularSignatureMap,
    )

    def _new(cells, lookup):
        vals = list(dict.fromkeys(strip_provenance(c) for c in cells if strip_provenance(c)))
        return [v for v in vals if lookup(v) is None]

    ct = [CancerTypeMap(cancer_type=v, oncotree_name=r.oncotree_name, oncotree_code=r.oncotree_code)  # name derived in map_oncotree
          for v, r in map_cancer_types(client, _new([r.cancer_type for r in elig_rows],
                                                    elig_store.lookup_cancer_type), **kw).items()]
    ga = [GeneAlterationMap(gene_alteration=v, finding_model=r.finding_model)
          for v, r in map_gene_alterations(client, _new([r.gene_alteration for r in elig_rows],
                                                        elig_store.lookup_gene_alteration), **kw).items()]
    sig = [MolecularSignatureMap(molecular_signature=v, finding_model=r.finding_model)
           for v, r in map_molecular_signatures(client, _new([r.molecular_signature for r in elig_rows],
                                                             elig_store.lookup_molecular_signature), **kw).items()]
    return ct, ga, sig


def _run_map_only(client, elig_store, run_dir, joined_dir, *, workers, max_attempts, use_reviewer, log) -> int:
    """MAPPING-ONLY pass (Step 1): map every DISTINCT interpreted cell across ALL THREE columns in ONE concurrent
    pool, then write the outputs — the 3 value->vocab map tables (3NF) into ``run_dir`` (current_output/), and the
    DENORMALIZED per-row `mapped_eligibility.tsv` into ``joined_dir`` (the top-level joined/ view dir; keeps the
    store strictly 3NF). `arm_eligibility_raw` + `interpreted_eligibility` are the read-only source of truth and are
    NEVER re-persisted. Fresh — every distinct value is mapped, not looked up."""
    from aus_trial_universe.tasks.eligibility.mapping.workflow import map_all_columns
    from aus_trial_universe.tasks.eligibility.schema import (
        CancerTypeMap, GeneAlterationMap, MolecularSignatureMap, TABLE_FILES,
    )
    from aus_trial_universe.core.logfmt import stage
    rows = [e for elist in elig_store.interpreted.values() for e in elist]
    log.info(stage(f"MAPPING (map-only) · {len(rows)} interpreted row(s) · single pool · {workers} workers"))

    ct, ga, sig = map_all_columns(
        client,
        [r.cancer_type_interpreted for r in rows],
        [r.gene_alteration_interpreted for r in rows],
        [r.molecular_signature_interpreted for r in rows],
        max_attempts=max_attempts, use_reviewer=use_reviewer, workers=workers,
    )
    for v, r in ct.items():
        elig_store.put_cancer_type(CancerTypeMap(cancer_type=v, oncotree_name=r.oncotree_name, oncotree_code=r.oncotree_code))
    for v, r in ga.items():
        elig_store.put_gene_alteration(GeneAlterationMap(gene_alteration=v, finding_model=r.finding_model))
    for v, r in sig.items():
        elig_store.put_molecular_signature(MolecularSignatureMap(molecular_signature=v, finding_model=r.finding_model))

    # 3NF map tables -> current_output/ (raw/interpreted untouched); the denormalized flat view -> joined/.
    elig_store.save_maps(run_dir)
    elig_store.save_mapped_eligibility(joined_dir)

    def _stats(d, empty):
        return f"{len(d)} distinct · {sum(1 for r in d.values() if not empty(r))} mapped · " \
               f"{sum(1 for r in d.values() if empty(r))} empty · {sum(1 for r in d.values() if not r.faithful)} unfaithful"
    print(f"\n{'═' * 70}\nMAP-ONLY · 3 map tables → {run_dir}/  ·  {TABLE_FILES['mapped_eligibility']} → {joined_dir}/  (raw/interpreted untouched)\n")
    print(f"  cancer_type         · {_stats(ct, lambda r: not r.oncotree_code)}")
    print(f"  gene_alteration     · {_stats(ga, lambda r: not r.finding_model)}")
    print(f"  molecular_signature · {_stats(sig, lambda r: not r.finding_model)}")
    return 0


def _run_reconcile(client, elig_store, maps_dir, joined_dir, *, workers, max_attempts, use_reviewer, log) -> int:
    """MAPPING STEP 2 — cross-value reconciliation. Reads the store's 3 map tables, reconciles each column
    (deterministic name->code repair + OR-order normalise, then LLM-adjudicate the remaining semantic groups),
    and writes: the finalised map-table SET (still 3NF single-key lookups) into ``maps_dir`` (the store,
    current_output/), and the DENORMALIZED flat finalised_mapped_eligibility.tsv into ``joined_dir``. The Step-1
    content + map tables are NOT modified (only new finalised_* files are added)."""
    from aus_trial_universe.tasks.eligibility.mapping.reconcile import (
        CANCER_TYPE, GENE_ALTERATION, MOLECULAR_SIGNATURE,
        reconcile_column, write_finalised_mapped_eligibility, write_finalised_maps,
    )
    from aus_trial_universe.tasks.eligibility.qa.mapping_consistency import find_inconsistencies
    from aus_trial_universe.core.logfmt import stage
    log.info(stage(f"RECONCILE (Step 2) · cancer {len(elig_store.cancer_map)} · gene {len(elig_store.gene_map)} · "
                   f"signature {len(elig_store.signature_map)} distinct · {workers} workers"))
    kw = dict(workers=workers, max_attempts=max_attempts, use_reviewer=use_reviewer)
    ct_before = len(find_inconsistencies({v: m.oncotree_code for v, m in elig_store.cancer_map.items()}))
    ga_before = len(find_inconsistencies({v: m.finding_model for v, m in elig_store.gene_map.items()}))
    sig_before = len(find_inconsistencies({v: m.finding_model for v, m in elig_store.signature_map.items()}))

    ct_final, ct_unres, ct_groups = reconcile_column(
        client, {v: m.oncotree_code for v, m in elig_store.cancer_map.items()}, column=CANCER_TYPE, **kw)
    ga_final, _gu, ga_groups = reconcile_column(
        client, {v: m.finding_model for v, m in elig_store.gene_map.items()}, column=GENE_ALTERATION, **kw)
    sig_final, _su, sig_groups = reconcile_column(
        client, {v: m.finding_model for v, m in elig_store.signature_map.items()}, column=MOLECULAR_SIGNATURE, **kw)

    write_finalised_maps(elig_store, maps_dir, ct_final, ga_final, sig_final)   # 3NF lookups -> the store
    write_finalised_mapped_eligibility(elig_store, joined_dir, ct_final, ga_final, sig_final)   # flat view -> joined/

    ct_after = len(find_inconsistencies(ct_final))
    ga_after = len(find_inconsistencies(ga_final))
    sig_after = len(find_inconsistencies(sig_final))
    print(f"\n{'═' * 70}\nRECONCILE (Step 2) · finalised map set (3NF) → {maps_dir}/  ·  "
          f"finalised_mapped_eligibility.tsv → {joined_dir}/  (Step-1 tables untouched)\n")
    print(f"  cancer_type         · groups adjudicated {ct_groups} · inconsistent {ct_before}→{ct_after} · "
          f"unresolved name→code repairs {len(ct_unres)}")
    print(f"  gene_alteration     · groups adjudicated {ga_groups} · inconsistent {ga_before}→{ga_after}")
    print(f"  molecular_signature · groups adjudicated {sig_groups} · inconsistent {sig_before}→{sig_after}")
    if ct_unres:
        print("\n  UNRESOLVED name→code repairs (MANUAL REVIEW — invalid code tokens remain):")
        for value, code, bad in ct_unres[:40]:
            print(f"    {bad} in: {value[:70]}")
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
    parser.add_argument("--reconcile", action="store_true",
                        help="MAPPING Step 2 — cross-value reconciliation over the EXISTING store's map tables: "
                             "detect same-concept/different-code groups, deterministic name->code repair + OR-order "
                             "normalise, LLM-adjudicate the rest, then write the finalised map-table set + flat "
                             "finalised_mapped_eligibility.tsv into joined/. The 3NF store is left untouched.")
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

    from aus_trial_universe.core.client import DiskCache, LlmClient
    from aus_trial_universe.core.logfmt import FAIL, PASS, stage
    from aus_trial_universe.core.workflow import run_parallel
    from aus_trial_universe.tasks.drug_utility.store import DrugRefStore
    from aus_trial_universe.tasks.drug_utility.workflow import build_drug_ref
    from aus_trial_universe.tasks.eligibility.extraction.loaders import load_trials
    from aus_trial_universe.tasks.eligibility.extraction.workflow import extract_trial
    from aus_trial_universe.tasks.eligibility.mapping.workflow import strip_provenance
    from aus_trial_universe.tasks.eligibility.store import EligStore
    from aus_trial_universe.tasks.shared.store import TrialArmStore
    from aus_trial_universe.core import paths as _paths   # read TRIAL_ARMS_ROOT dynamically (test-redirectable)

    ids = [x for x in args.ids.split(",")] if args.ids else None
    # map-only works off the EXISTING store (the 3 map tables over interpreted_eligibility), so it needs no trial
    # inputs — skip the (potentially all-1,999) input load entirely.
    _store_only = args.map_only or args.reconcile   # both work off the EXISTING store; no trial inputs needed
    trials = [] if _store_only else load_trials(id=args.id, ids=ids)
    if not _store_only and not trials:
        parser.error("no trials with usable text found")
    requested_ids = [t[1] for t in trials]   # display ids of everything asked for (before any --resume filter)

    store_root = Path(args.store_root) if args.store_root else ELIGIBILITY_OUTPUT
    # The live accumulating store is `current_output/` (current/archive pattern, parallels drug_annotations'
    # current_version/): each run loads it, upserts, and writes it back in place. Superseded runs are archived
    # manually (move current_output/ -> archive/<date>/). `--out-dir` overrides for a one-off/isolated run.
    run_dir = Path(args.out_dir) if args.out_dir else store_root / CURRENT_VERSION
    # The denormalized Step-1/Step-2 joined views (mapped_eligibility, finalised_mapped_eligibility) live under
    # `derived/joined/eligibility/` (the drug path writes `derived/joined/drug_annotations/`). Keeps the masters/
    # stores strictly 3NF. In production this is the bucketed `JOINED_ROOT`; under a `--store-root <tmp>` run (tests)
    # it is derived as a sibling of the tmp store so the run writes into <tmp>/joined/eligibility/, never real /data.
    # The grand matching-engine flat file is built separately by `export.py`.
    joined_root = (Path(args.store_root).parent / "joined") if args.store_root else JOINED_ROOT
    joined_dir = joined_root / JOINED_ELIGIBILITY

    cache = None if args.no_cache else DiskCache(CACHE_DIR)
    _client_kw = dict(cache=cache, max_concurrency=args.max_concurrency)
    client = LlmClient(model=args.model, **_client_kw) if args.model else LlmClient(**_client_kw)
    kw = dict(max_attempts=args.max_attempts, use_reviewer=not args.no_review)
    log = logging.getLogger("agentic.pipeline")

    # GC cache entries from outdated prompts before we start (only stale entries; never live/legacy).
    if cache is not None and not args.no_cache_prune:
        from aus_trial_universe.core.cache_prune import prune_cache, summary_line
        rep = prune_cache(CACHE_DIR, apply=True)
        if rep.removed:
            log.info(summary_line(rep))
    log.info("run · %d trial(s) · judge=%s · review=%s · extract_only=%s · skip_drug=%s → %s/",
             len(trials), not args.no_judge, not args.no_review, args.extract_only, args.skip_drug, run_dir.name)

    trial_arms_root = _paths.TRIAL_ARMS_ROOT  # the SHARED registry's home (read at call time so tests can redirect)
    elig_store = EligStore.load(store_root)   # load the latest EXISTING snapshot before creating this run's dir
    run_dir.mkdir(parents=True, exist_ok=True)
    if args.reconcile:   # Step 2: reconcile the map tables -> finalised 3NF maps (current_output) + flat view (joined)
        return _run_reconcile(client, elig_store, run_dir, joined_dir, workers=args.workers,
                              max_attempts=args.max_attempts, use_reviewer=not args.no_review, log=log)
    if args.map_only:   # MAPPING-only: map the store's interpreted cells -> 3 map tables; content tables untouched
        return _run_map_only(client, elig_store, run_dir, joined_dir, workers=args.workers,
                             max_attempts=args.max_attempts, use_reviewer=not args.no_review, log=log)
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

    # --- persist the 3NF store snapshots ---------------------------------- #
    arm_store.save(trial_arms_root)                # the shared trial_arms registry
    elig_store.save(run_dir)                       # the pure-3NF masters -> run_dir (current_output/)
    # The grand matching-engine flat file (trial_eligibility.tsv) is built SEPARATELY by `make agentic-export`
    # (aus_trial_universe.export) over the finalised maps + trial_info + drug tables — not here.

    print(f"\n{'═' * 70}\n{len(trials)} trial(s): {len(summaries)} ok, {len(failures)} failed · "
          f"{total} conjunction(s) · 3NF → {run_dir}/\n")
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
