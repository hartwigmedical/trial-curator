# Planning & specs

Live plans and specs sit here. **`archive/` holds work that has SHIPPED** — kept for provenance (why a design is
the way it is, and what was rejected), not as a to-do list.

| archived | shipped | what it records |
|---|---|---|
| `v2_oncotree_correction_spec.md` | 2026-08-04 | the OncoTree mapping correction: defect catalogue, the 3-layer fix, the migration, and §10 — the playbook + lessons for repeating it on another column (gene_alteration is next) |
| `v2_mapping_and_shared_loop_plan.md` | 2026-07-28 | Mapping Step-1 + the shared doer→reviewer harness (`core/review.py`) |
| `handover_history_20260804.md` | 2026-08-04 | the handover's narrative record up to session 8 — session-by-session detail, superseded START-HERE blocks, and the pre-fix OncoTree inventory. Later extractions get their own `handover_history_<YYYYMMDD>.md` rather than being appended |
| `eligibility_path/`, `drug_utility_path/` | superseded | docs for the legacy code trees deleted in session 5; retained until handover item A3 formally retires them |

The authoritative to-do list is **`../v2_agentic_handover.md`**; the system design is **`../v2_agentic_pipeline_spec.md`**.
