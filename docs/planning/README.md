# Planning & specs

Live plans and specs sit here. **`archive/` holds work that has SHIPPED** — kept for provenance (why a design is
the way it is, and what was rejected), not as a to-do list.

| archived | shipped | what it records |
|---|---|---|
| `v2_gene_alteration_correction_spec.md` | 2026-08-04/05 | the gene_alteration correction **and** the `mapping/` per-column restructure: six iterations with what each one's review found, the endorsed biology, the five resolved content calls, **§3b — THE DNF INVARIANT stated precisely**, and §7b — the migration with its three traps |
| `gene_alteration_engine_dry_run_baseline.md` | 2026-08-04 | the PRE-correction matching-engine dry run (14 defects of ours, 285 engine-side) — the baseline the corrected run is measured against |
| `v2_oncotree_correction_spec.md` | 2026-08-04 | the OncoTree mapping correction: defect catalogue, the 3-layer fix, the migration, and §10 — the playbook + lessons for repeating it on another column (followed for gene_alteration) |
| `v2_mapping_and_shared_loop_plan.md` | 2026-07-28 | Mapping Step-1 + the shared doer→reviewer harness (`core/review.py`) |
| `handover_history_20260804.md` | 2026-08-04 | the handover's narrative record up to session 8 — session-by-session detail, superseded START-HERE blocks, and the pre-fix OncoTree inventory. Later extractions get their own `handover_history_<YYYYMMDD>.md` rather than being appended |
| `eligibility_path/`, `drug_utility_path/` | superseded | docs for the legacy code trees deleted in session 5; retained until handover item A3 formally retires them |

**Live (not archived):** `matching_engine_capability_gaps.md` — 7 engine-side gaps (G1-G8) measured against the corrected mapping, with `file:line` references into `oncoact/trial-matching`. Ready to send to the matching-engine team; **not yet sent**.

The authoritative to-do list is **`../v2_agentic_handover.md`**; the system design is **`../v2_agentic_pipeline_spec.md`**.
