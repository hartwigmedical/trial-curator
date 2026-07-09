# Combined Agentic Run

How to set up and run the **v2 agentic pipeline** (`aus_trial_universe/agentic/`) — one command
takes a trial from free text all the way to a fully-enriched DNF (disjunctive normal form) resource table.

- **Pipeline:** extract → map (OncoTree + finding-model) → drug enrichment, in **one streamed pass**.
- **One run = one output + one log.** No intermediate files.
- Design detail lives in `docs/v2_agentic_pipeline_spec.md` (single spec) and the diagram `docs/v2_workflow_diagram.html`.

---

## Make Commands

There are exactly three:

| Command | What it does |
|---|---|
| `make agentic-run` | Full pipeline (extract → map → drug), streamed to one output + one log. Runs the unit tests first (aborts on failure). |
| `make agentic-clean` | Wipe run artifacts under `data/agentic/{output,log,cache}` (handy between test runs). |
| `make agentic-tests` | Run the unit-test suite (no API calls). |

### `make agentic-run` modes
```bash
make agentic-run ID=NCT06881784                 # one trial (source auto-detected from the id)
make agentic-run ID=ACTRN12605000025639         # ANZCTR auto-detected
make agentic-run IDS=NCT1,ACTRN2,NCT3           # a specific set (comma-separated)
make agentic-run                                # ALL trials (every ctgov + anzctr trial)
```

### Optional variables (append to any run)
| Var | Effect |
|---|---|
| `MODEL=<name>` | Override the OpenAI model (default `gpt-5.5`). |
| `NO_JUDGE=1` | Skip the extraction reviewer panel (cheaper/faster). |
| `NO_REVIEW=1` | Skip the mapping + drug reviewers (cheaper/faster). |

Example: `make agentic-run ID=NCT05417594 NO_JUDGE=1`.

### The review set (10 complex trials)
A curated set of complex (cohort × cancer × gene) trials for review is written to
`data/agentic/analysis/review_trials_ids.txt` (copy-paste ready):
```bash
make agentic-run IDS=$(cat data/agentic/analysis/review_trials_ids.txt)
```

---

## Environment and API keys

- **Conda env:** `trial_curator` (the driver auto-selects `/opt/anaconda3/envs/trial_curator/bin/python`,
  or `~/anaconda3/...`, or `$PYTHON_BIN`; it just needs `openai` + `pydantic`). Nothing to activate manually.
- **`OPENAI_API_KEY`:** auto-loaded from `.env` (then `.env.local`) at the repo root — never printed.
- **OpenAI SDK:** `openai>=2.x` is required — the drug stage uses the **Responses API `web_search` tool** for
  TGA/PBS research. (`chat.completions.parse` still powers extraction/mapping.)

If no suitable Python is found the driver exits with a clear message; activate `trial_curator` or set
`PYTHON_BIN=/path/to/python`.

---

## Standard Run

`make agentic-run …` runs, per trial, in one process and streams each trial's finished rows to disk as it
completes (so partial results survive an interrupt):

1. **SELECT & ASSEMBLE** — load the trial(s); assemble all relevant sections into one labelled document.
2. **COHORTS** — CTGov: one cohort per `armGroups` entry (deterministic, with `arm_type` + drug).
   ANZCTR: a conservative LLM cohort-detection + LLM drug extraction.
3. **EXTRACT** — one LLM agent → DNF eligibility rows (5 columns), reviewed by a 5-agent panel, bounded refine.
4. **MAP** — LLM mapper→reviewer procedures: `cancer_type` → OncoTree; `gene_alteration` /
   `molecular_signature` → Hartwig finding-model syntax.
5. **DRUG** — one web-search curator→reviewer per trial: main vs auxiliary drugs, POTTR + general drug class,
   TGA approval (+year) and PBS reimbursement for the main drug(s).
6. **WRITE** — the enriched rows stream to one TSV.

**Output:** `data/agentic/output/trial_resource_<id>.tsv` (single trial) or
`trial_resource_<YYYYMMDD_HHMMSS>.tsv` (multiple/all).
**Log:** `data/agentic/log/agentic_run_<label>_<timestamp>.log` (the whole run, both stages).

---

## Output Schema

One row per satisfiable (trial, cohort) conjunction (DNF: rows are ORed, cells within a row ANDed;
exclusions inline as `NOT(...)`). Columns, in order:

| Column | Stage | Notes |
|---|---|---|
| `trialId` | extract | NCT… or ACTRN… |
| `cohort` | extract | arm label, or `(all)` for a single-cohort trial |
| `arm_type` | extract | CTGov `armGroups[].type` (EXPERIMENTAL / ACTIVE_COMPARATOR / …) |
| `cancer_type` | extract | trial's wording, `value [source]` |
| `oncotree_name` / `oncotree_code` | map | OncoTree mapping of `cancer_type` |
| `gene_alteration` | extract | trial's wording, `value [source]` |
| `gene_alteration_findingmodel` | map | Hartwig finding-model syntax |
| `molecular_signature` | extract | `value [source]` |
| `molecular_signature_findingmodel` | map | finding-model syntax |
| `molecular_biomarker` | extract | `value [source]` |
| `prior_therapy` | extract | `value [source]` |
| `drug` | extract | the cohort's raw intervention drug(s) |
| `main_drugs` / `auxiliary_drugs` | drug | the **investigational agent(s) under study by judgement** vs comparators/backbone/SoC/placebo |
| `pottr_drug_class` | drug | POTTR class hierarchy of the main drug(s) |
| `drug_class` | drug | general (non-POTTR) class/mechanism of the main drug(s) |
| `tga_status` | drug | **per main drug**: `<drug>: Approved` / `<drug>: Not approved` (TGA/ARTG) |
| `pbs_status` | drug | **per main drug**: `<drug>: Approved` / `<drug>: Not approved` (PBS) |
| `tga_detail` | drug | per main drug: year + evidence + official source link (tga.gov.au / ARTG) |
| `pbs_detail` | drug | per main drug: year/indication + evidence + official source link (pbs.gov.au) |

`main_drugs` / `pottr_drug_class` / `drug_class` / `tga_status` / `pbs_status` / `tga_detail` / `pbs_detail` are
trial-level (repeated across that trial's cohort rows); `arm_type` and `drug` are per cohort.

> **Verification:** the finding-model / OncoTree / drug outputs are checked **manually** against the legacy
> `data/eligibility_path/exports/final/eligibility_*_resource_*.tsv` and the hand-curated resource files.
> The curated resources are held-out verification data, not training input (see the design memory).

---

## Testing
```bash
make agentic-tests        # 54 unit tests, no API, all fake-client
```
Every `make agentic-run` also runs these as a preflight and aborts if any fail.

---

## Clean between runs
```bash
make agentic-clean        # rm -rf data/agentic/{output,log,cache}; recreate output/ + log/
```
Scoped strictly to `data/agentic/` — never touches `data/trial_inputs/` or the legacy `data/eligibility_path/`.
