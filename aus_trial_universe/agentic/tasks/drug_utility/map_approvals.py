"""Symmetric-match vocab for drug regulatory approvals (spec §6.1 follow-up).

Maps the `drug_regulatory_approvals` free-text `cancer_type` / `biomarker` into the SAME vocabulary the trial
eligibility side uses (OncoTree code + finding-model), so a drug's approved indication and a trial's eligibility can
be matched on the same axes. Reuses the SIGNED-OFF eligibility mappers — nothing is re-implemented:

  cancer_type  -> map_cancer_types            -> approval_cancer_type_map.tsv (cancer_type -> OncoTree)
  biomarker    -> split (doer->reviewer)      -> approval_biomarker_map.tsv
                  then the gene / signature parts -> map_gene_alterations / map_molecular_signatures (finding-model);
                  the expression/IHC part (molecular_biomarker) stays free text — symmetric with the trial side,
                  whose molecular_biomarker column is never vocab-mapped either.

Cross-domain CONSISTENCY: before mapping, each distinct value is looked up in the trial side's FINAL (Step-2
reconciled) maps; an exact-string match reuses the trial's FINAL code with NO LLM call — so a concept shared by a
trial and a drug ("breast cancer" -> the same OncoTree code) is guaranteed identical, and the pass is cheaper.
Novel values go through the mappers (same prompts + shared cache, so still consistent).

Purely ADDITIVE: writes only the two new tables via `DrugRefStore.save_approval_maps` — the 5 core drug tables +
`trial_arm_drug_role` are left byte-untouched. Deterministic assembly; the LLM fills only the split + the mappers.

  python -m aus_trial_universe.agentic.tasks.drug_utility.map_approvals --workers 80 --max-concurrency 500
  options: --no-review  --no-seed  --limit N  --model <name>  --no-cache  --no-cache-prune

Run via `make drug-ref-map-approvals`. Requires OPENAI_API_KEY.
"""
from __future__ import annotations

import argparse
import csv
import logging
from dataclasses import dataclass, field
from pathlib import Path

from aus_trial_universe.agentic.core.client import LlmClient
from aus_trial_universe.agentic.core.logfmt import line, stage
from aus_trial_universe.agentic.core.review import review_refine
from aus_trial_universe.agentic.core.workflow import CheckResult, fan_out
from aus_trial_universe.agentic.tasks.drug_utility.agents import (
    build_biomarker_split_reviewer,
    build_biomarker_splitter,
)
from aus_trial_universe.agentic.tasks.drug_utility.schema import (
    ApprovalBiomarkerMap,
    ApprovalCancerTypeMap,
    BiomarkerSplit,
)
from aus_trial_universe.agentic.tasks.drug_utility.store import DrugRefStore

logger = logging.getLogger("agentic.map_approvals")


# --------------------------------------------------------------------------- #
# Stage — split ONE approval biomarker into the trial side's 3 buckets (doer -> reviewer -> bounded refine).
# --------------------------------------------------------------------------- #
def split_one_biomarker(client: LlmClient, phrase: str, *, max_attempts: int = 6,
                        use_reviewer: bool = True) -> BiomarkerSplit:
    doer = build_biomarker_splitter(client)
    reviewer = build_biomarker_split_reviewer(client) if use_reviewer else None

    def produce(feedback: str = "", prior=None) -> BiomarkerSplit:
        prompt = f"Biomarker phrase: {phrase}"
        return doer(prompt if not feedback else f"{prompt}\n\n[Reviewer feedback — fix these]:\n{feedback}")

    def check(s: BiomarkerSplit, escalate: bool = False) -> CheckResult:
        if reviewer is not None:
            v = reviewer(f"SOURCE biomarker phrase: {phrase}\n\nPROPOSED split:\n"
                         f"- gene_alteration: {s.gene_alteration!r}\n"
                         f"- molecular_signature: {s.molecular_signature!r}\n"
                         f"- molecular_biomarker: {s.molecular_biomarker!r}")
            if not v.faithful:
                return CheckResult(ok=False, problems=v.problems or ["reviewer flagged the split"])
        return CheckResult(ok=True)

    return review_refine(produce, check, max_attempts=max_attempts).value  # plain critique loop (no suggested_fix)


def split_biomarkers(client: LlmClient, cells, *, max_attempts: int = 6, use_reviewer: bool = True,
                     workers: int = 8) -> dict[str, BiomarkerSplit]:
    """Split the DISTINCT biomarker values into {value -> BiomarkerSplit}, concurrently. Empty values skipped."""
    distinct = list(dict.fromkeys(c.strip() for c in cells if c and c.strip()))
    if not distinct:
        return {}
    logger.info("")
    logger.info(stage("APPROVAL-MAP · split biomarkers"))
    logger.info(line(f"{len(distinct)} distinct value(s) · workers={workers}"))
    results = fan_out(
        [(lambda v=v: split_one_biomarker(client, v, max_attempts=max_attempts, use_reviewer=use_reviewer))
         for v in distinct], max_workers=workers)
    return {v: r for v, r in zip(distinct, results)}


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
@dataclass
class ApprovalMapSummary:
    ct_total: int = 0
    ct_seeded: int = 0        # cancer_type reused from the trial FINAL map (no LLM)
    ct_mapped: int = 0        # cancer_type mapped fresh
    ct_reconciled_groups: int = 0  # Step-2 groups adjudicated (semantically-equivalent -> one code)
    ct_empty: int = 0         # FINAL code empty (non-cancer / unmappable)
    bm_total: int = 0
    bm_with_gene: int = 0     # biomarkers whose split has a gene_alteration part
    bm_with_signature: int = 0
    bm_with_expression: int = 0  # split has a molecular_biomarker (free-text) part
    seeded_fm: int = 0        # gene/signature sub-cells reused from the trial FINAL maps (no LLM)
    problems: list[str] = field(default_factory=list)


def _distinct(values) -> list[str]:
    return list(dict.fromkeys(v.strip() for v in values if v and v.strip()))


def map_approvals(client: LlmClient, store: DrugRefStore, *, workers: int = 8, max_attempts: int = 6,
                  use_reviewer: bool = True, seed: bool = True, limit: int | None = None) -> ApprovalMapSummary:
    """Map the store's distinct approval cancer_type / biomarker values into the eligibility vocab, writing the two
    new map tables into `store` (mutated in place). Seeds from the trial FINAL maps unless `seed=False`."""
    from aus_trial_universe.agentic.export import _read_map, _render_oncotree_name
    from aus_trial_universe.agentic.core.paths import ELIG_CURRENT_OUTPUT, ELIGIBILITY_OUTPUT
    from aus_trial_universe.agentic.tasks.eligibility.mapping.reconcile import reconcile_column
    from aus_trial_universe.agentic.tasks.eligibility.mapping.workflow import map_all_columns
    from aus_trial_universe.agentic.tasks.eligibility.tools.oncotree import oncotree_vocab

    summary = ApprovalMapSummary()
    indications = [ind for rows in store.indications.values() for ind in rows]
    ct_values = _distinct(ind.cancer_type for ind in indications)
    bm_values = _distinct(ind.biomarker for ind in indications)
    if limit is not None:
        ct_values, bm_values = ct_values[:limit], bm_values[:limit]
    summary.ct_total, summary.bm_total = len(ct_values), len(bm_values)

    # Trial FINAL (Step-2 reconciled) maps — the seed source for cross-domain consistency.
    elig_dir = ELIGIBILITY_OUTPUT / ELIG_CURRENT_OUTPUT
    seed_ct = _read_map(elig_dir / "finalised_cancer_type_map.tsv", "cancer_type", "oncotree_code_FINAL") if seed else {}
    seed_ga = _read_map(elig_dir / "finalised_gene_alteration_map.tsv", "gene_alteration", "finding_model_FINAL") if seed else {}
    seed_sig = _read_map(elig_dir / "finalised_molecular_signature_map.tsv", "molecular_signature", "finding_model_FINAL") if seed else {}
    logger.info("")
    logger.info(stage("APPROVAL-MAP · symmetric-match vocab"))
    logger.info(line(f"cancer_type {summary.ct_total} · biomarker {summary.bm_total} distinct · seed={seed} "
                     f"(trial FINAL: ct {len(seed_ct)} · gene {len(seed_ga)} · sig {len(seed_sig)}) · workers={workers}"))

    # 1) Split every distinct biomarker into (gene / signature / expression) sub-cells (the trial side's 3 buckets).
    splits = split_biomarkers(client, bm_values, max_attempts=max_attempts, use_reviewer=use_reviewer, workers=workers)

    # 2) Map the NOVEL (unseeded) distinct values of all three axes in ONE combined pool (reuses map_all_columns).
    ct_novel = [v for v in ct_values if v not in seed_ct]
    gene_cells = _distinct(s.gene_alteration for s in splits.values())
    sig_cells = _distinct(s.molecular_signature for s in splits.values())
    gene_novel = [v for v in gene_cells if v not in seed_ga]
    sig_novel = [v for v in sig_cells if v not in seed_sig]
    ct_res, ga_res, sig_res = map_all_columns(
        client, ct_novel, gene_novel, sig_novel,
        max_attempts=max_attempts, use_reviewer=use_reviewer, workers=workers)

    # 3) cancer_type Step-1 code per value (seed FINAL where shared; else the fresh mapping).
    ct_step1: dict[str, str] = {}
    for ct in ct_values:
        if ct in seed_ct:
            ct_step1[ct] = seed_ct[ct]
            summary.ct_seeded += 1
        else:
            r = ct_res.get(ct)
            ct_step1[ct] = r.oncotree_code if r else ""
            summary.ct_mapped += 1

    # 4) Step-2 cancer_type reconciliation — REUSE the eligibility shared logic (no rewrite): unify
    #    semantically-equivalent drug cancer_type values to ONE code, keeping genuine grade/subtype distinctions apart.
    ct_final, _unresolved, summary.ct_reconciled_groups = reconcile_column(
        client, ct_step1, is_oncotree=True, workers=workers, max_attempts=max_attempts, use_reviewer=use_reviewer)
    logger.info(line(f"cancer_type reconciliation · {summary.ct_reconciled_groups} group(s) adjudicated"))

    vocab = oncotree_vocab()
    for ct in ct_values:
        code1 = ct_step1[ct]
        codeF = ct_final.get(ct, code1)
        store.put_approval_cancer_type(ApprovalCancerTypeMap(
            cancer_type=ct, oncotree_code=code1, oncotree_code_FINAL=codeF,
            oncotree_name=_render_oncotree_name(codeF, vocab)))
        if not codeF:
            summary.ct_empty += 1

    # 5) Assemble approval_biomarker_map (the split + the two finding-model renderings). Gene/signature are NOT
    #    reconciled: the consistency detector is cancer_type-tuned, and seeding from the trial FINAL gene/sig maps
    #    already gives cross-domain identity for shared sub-cells.
    def _fm(cell: str, seed_map, res_map) -> str:
        if not cell:
            return ""
        if cell in seed_map:
            summary.seeded_fm += 1
            return seed_map[cell]
        r = res_map.get(cell)
        return r.finding_model if r else ""

    for bm in bm_values:
        s = splits.get(bm) or BiomarkerSplit()
        ga_fm = _fm(s.gene_alteration, seed_ga, ga_res)
        sig_fm = _fm(s.molecular_signature, seed_sig, sig_res)
        store.put_approval_biomarker(ApprovalBiomarkerMap(
            biomarker=bm, gene_alteration=s.gene_alteration, molecular_signature=s.molecular_signature,
            molecular_biomarker=s.molecular_biomarker,
            gene_alteration_findingmodel=ga_fm, molecular_signature_findingmodel=sig_fm))
        summary.bm_with_gene += bool(s.gene_alteration.strip())
        summary.bm_with_signature += bool(s.molecular_signature.strip())
        summary.bm_with_expression += bool(s.molecular_biomarker.strip())
    return summary


# --------------------------------------------------------------------------- #
# Joined view (denormalized; NOT 3NF -> lives in joined/drug_annotations/, not the store)
# --------------------------------------------------------------------------- #
MAPPED_APPROVALS_COLUMNS = [
    "canonical_id", "indication_id", "cancer_type", "oncotree_name", "oncotree_code", "oncotree_code_FINAL",
    "biomarker", "gene_alteration", "gene_alteration_findingmodel", "molecular_signature",
    "molecular_signature_findingmodel", "molecular_biomarker", "tga_status", "pbs_status",
]


def write_mapped_approvals(store: DrugRefStore, joined_dir: Path) -> Path:
    """Denormalized flat view: every `drug_regulatory_approvals` row ⋈ its cancer_type / biomarker vocab mappings
    (one row per (canonical_id, indication_id)). The drug-side analog of joined/eligibility/mapped_eligibility.tsv —
    NOT 3NF, so it lives under joined/, never in the store. Regenerable from the 3NF tables."""
    rows: list[dict] = []
    for inds in store.indications.values():
        for ind in inds:
            ct = store.approval_cancer_type_map.get(ind.cancer_type)
            bm = store.approval_biomarker_map.get(ind.biomarker)
            rows.append({
                "canonical_id": ind.canonical_id, "indication_id": ind.indication_id,
                "cancer_type": ind.cancer_type,
                "oncotree_name": ct.oncotree_name if ct else "",
                "oncotree_code": ct.oncotree_code if ct else "",
                "oncotree_code_FINAL": ct.oncotree_code_FINAL if ct else "",
                "biomarker": ind.biomarker,
                "gene_alteration": bm.gene_alteration if bm else "",
                "gene_alteration_findingmodel": bm.gene_alteration_findingmodel if bm else "",
                "molecular_signature": bm.molecular_signature if bm else "",
                "molecular_signature_findingmodel": bm.molecular_signature_findingmodel if bm else "",
                "molecular_biomarker": bm.molecular_biomarker if bm else "",
                "tga_status": ind.tga_status, "pbs_status": ind.pbs_status,
            })
    joined_dir = Path(joined_dir)
    joined_dir.mkdir(parents=True, exist_ok=True)
    from aus_trial_universe.agentic.core.paths import MAPPED_APPROVALS_FILE
    path = joined_dir / MAPPED_APPROVALS_FILE
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=MAPPED_APPROVALS_COLUMNS, delimiter="\t", lineterminator="\n",
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return path


def _drug_joined_dir() -> Path:
    """joined/drug_annotations/ — sibling of the drug store root (so a demo re-root of DATA_ROOT follows)."""
    from aus_trial_universe.agentic.core.paths import DRUG_ANNOTATIONS_ROOT, JOINED_DRUG
    return DRUG_ANNOTATIONS_ROOT.parent / "joined" / JOINED_DRUG


def main(argv: list[str] | None = None) -> int:
    from aus_trial_universe.agentic.run import _load_openai_key

    parser = argparse.ArgumentParser(description="Map drug-approval cancer_type/biomarker into the eligibility vocab "
                                                 "(symmetric matching); additive — writes only the 2 approval-map tables.")
    parser.add_argument("--workers", type=int, default=8, help="Parallel values + split width (default 8).")
    parser.add_argument("--max-concurrency", type=int, default=None,
                        help="GLOBAL cap on concurrent LLM API calls (probe the account's rate-limit ceiling).")
    parser.add_argument("--max-attempts", type=int, default=6, help="Refine cap per value (default 6).")
    parser.add_argument("--no-review", action="store_true", help="Skip the reviewers (splitter + mappers); cheaper.")
    parser.add_argument("--no-seed", action="store_true",
                        help="Do NOT seed from the trial FINAL maps — map every value fresh (loses guaranteed "
                             "cross-domain code identity; slower).")
    parser.add_argument("--limit", type=int, default=None, help="Cap distinct cancer_type/biomarker values (smoke test).")
    parser.add_argument("--model", default=None, help="Override the OpenAI model.")
    parser.add_argument("--no-cache", action="store_true", help="Disable the on-disk LLM response cache.")
    parser.add_argument("--no-cache-prune", action="store_true",
                        help="Skip the automatic prune of cache entries from OUTDATED prompts at start.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for _n in ("httpx", "openai", "urllib3", "numexpr"):
        logging.getLogger(_n).setLevel(logging.WARNING)
    _load_openai_key()

    from aus_trial_universe.agentic.core.client import DiskCache
    from aus_trial_universe.agentic.core.paths import CACHE_DIR

    cache = None if args.no_cache else DiskCache(CACHE_DIR)
    _client_kw = dict(cache=cache, max_concurrency=args.max_concurrency)
    client = LlmClient(model=args.model, **_client_kw) if args.model else LlmClient(**_client_kw)

    if cache is not None and not args.no_cache_prune:
        from aus_trial_universe.agentic.core.cache_prune import prune_cache, summary_line
        _rep = prune_cache(CACHE_DIR, apply=True)
        if _rep.removed:
            logger.info(summary_line(_rep))

    store = DrugRefStore.load()
    if not store.indications:
        parser.error("no drug_regulatory_approvals in the store — run `make drug-ref-build` first")

    summary = map_approvals(client, store, workers=args.workers, max_attempts=args.max_attempts,
                            use_reviewer=not args.no_review, seed=not args.no_seed, limit=args.limit)
    vdir = store.save_approval_maps()                       # 3NF maps -> the store (additive)
    joined = write_mapped_approvals(store, _drug_joined_dir())   # denormalized flat view -> joined/drug_annotations/

    print(f"\n{'═' * 70}\napproval vocab maps → {vdir}/\n"
          f"  cancer_type: {summary.ct_total} distinct · seeded={summary.ct_seeded} · mapped={summary.ct_mapped} · "
          f"reconciled_groups={summary.ct_reconciled_groups} · empty_FINAL={summary.ct_empty}\n"
          f"  biomarker:   {summary.bm_total} distinct · with_gene={summary.bm_with_gene} · "
          f"with_signature={summary.bm_with_signature} · with_expression={summary.bm_with_expression} · "
          f"seeded_fm={summary.seeded_fm}\n"
          f"  tables:      approval_cancer_type_map.tsv ({len(store.approval_cancer_type_map)}) · "
          f"approval_biomarker_map.tsv ({len(store.approval_biomarker_map)})\n"
          f"  joined view: {joined}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
