# docs/

Only two documents live at the top level — everything else is filed by kind.

| | |
|---|---|
| **`v2_agentic_handover.md`** | START HERE. Current state + the authoritative, dependency-ordered to-do list. |
| **`v2_agentic_pipeline_spec.md`** | The system design: stages, schemas, locked decisions. |
| `reference/` | How to run it and what the tables mean (`combined_agentic_run.md`, `drug_ref_schema.md`). |
| `planning/` | Live plans and specs; `planning/archive/` is shipped work, kept for provenance. |
| `diagrams/` | Workflow diagrams. Published to fixed Claude Artifact URLs — **always overwrite, never mint new** (`scripts/publish_diagram_artifact.sh`). |
| `presentation/` | Decks, split by audience: `internal/`, `external/` (CKB), plus `assets/` for the official Hartwig template. |

`docs/build/` is not tracked: it holds regenerable PNG intermediates from `scripts/build_ckb_deck.py`.
