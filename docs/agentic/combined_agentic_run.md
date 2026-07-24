# Combined Agentic Run

How to set up and run the **v2 agentic pipeline** (`aus_trial_universe/agentic/`) — one command
takes a trial from free text all the way to a fully-enriched DNF (disjunctive normal form) resource table.

- **Pipeline:** extract → map (OncoTree + finding-model) → drug enrichment, in **one streamed pass**.
- **One run = one output + one log.** No intermediate files.
- Design detail lives in `docs/v2_agentic_pipeline_spec.md` (single spec) and the diagram `docs/v2_workflow_diagram.html`.

---

## Make Commands

| Command | What it does |
|---|---|
| `make agentic-run` | Full pipeline (extract → map → drug), streamed to one output + one log. Runs the unit tests first (aborts on failure). |
| `make agentic-validate` | **Independent output validator** — a *review of the reviewer agents*. Re-checks a finished output TSV OUTSIDE the workflow (see below). `OUT=<tsv>` or newest. No API calls. |
| `make agentic-clean` | Wipe transient run artifacts under `data/agentic/{eligibility,log,cache}` — never touches the colocated inputs/resources/drug_annotations. |
| `make agentic-cache-prune` | **Prune the response cache of OUTDATED-prompt entries** (both paths share one cache). Dry-run by default; `APPLY=1` deletes, `PURGE_UNKNOWN=1` also drops legacy entries. See below. No API calls. |
| `make drug-ref-build` | Build/refresh the drug reference (5 tables). See below. |
| `make drug-ref-refresh-pottr` | Download the current POTTR files from GitHub (archives the previous). See below. |
| `make agentic-tests` | Run the unit-test suite (no API calls). |

### `make agentic-run` modes
```bash
make agentic-run ID=NCT06881784                 # one trial (source auto-detected from the id) CTGOV trial
make agentic-run ID=ACTRN12605000025639         # ANZCTR trial
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

### The review sets (10 complex + 10 typical trials)
Curated ID lists for review (copy-paste ready): `data/agentic/analysis/complex_trials_ids.txt`
(complex cohort × cancer × gene trials) and `data/agentic/analysis/typical_trials_ids.txt`
(average CTGov cancer trials):
```bash
make agentic-run IDS=$(cat data/agentic/analysis/complex_trials_ids.txt)
make agentic-run IDS=$(cat data/agentic/analysis/complex_trials_ids.txt),$(cat data/agentic/analysis/typical_trials_ids.txt)
```

### `make agentic-validate` — the independent "review of the reviewers"
A **deterministic** validator that runs OUTSIDE the agentic workflow, so it catches what the in-loop
reviewer agents let through (mapping degrades gracefully — output is written even when a stage finishes
`faithful=False`). **It is a testing-period QA step, not part of the production path** — always run it on a
fresh output while iterating, and keep its checks in sync with the pipeline's validators. It (1) re-runs the
pipeline's own OncoTree + finding-model validators on the final cells, and (2) adds cross-row DNF / cohort /
exclusion checks nothing else performs: unsatisfiable `A AND B` cancer_type, all-empty rows, exact-duplicate
rows, in-cell `X AND NOT(X)`, and prior_therapy subsuming-twin over-enumeration.
```bash
make agentic-validate                 # data/agentic/eligibility/combined/combined.tsv
make agentic-validate OUT=<path.tsv>  # a specific run
```
Prints a per-trial problem list + a `N/M trials clean` summary. `aus_trial_universe/agentic/tasks/eligibility/qa/validate_output.py`.

### `make drug-ref-build` — the drug-reference resource (spec §6.1)
Builds the standalone drug reference (5 tables: `intervention_to_canonical`, `trial_to_intervention`, `drug_annotations_core`,
`drug_target_actions`, `drug_regulatory_approvals`) at `<DATA_ROOT>/drug_annotations/current_version/`. LLM doer→reviewer for
judgement (canonicalize / annotate / approvals); deterministic offline lookups for `rxcui`+`atc_code` (RxNorm) and
`pottr_drug_class` (POTTR). `canonicalize` splits a combination/regimen token into its component standalone drugs
(`1 input → N` canonicals; a single engineered molecule like an ADC/bispecific stays one) and records
`raw_name_to_map` (the input fragment each canonical came from). `trial_to_intervention` records which trial +
registry each input name came from (deterministic provenance / traceability), and the build **logs per-trial drug
attribution**. Incremental (existing non-stale drugs are pure lookups), batched with a checkpoint save after each
batch (resumable), soft-fails per drug. Logs to `data/agentic/log/`.
```bash
make drug-ref-build DRUGS="pembrolizumab; Keytruda; Ris-Rez"   # explicit list
make drug-ref-build IDS=NCT07099898,NCT05009992 LIMIT=5        # drugs from specific trials (CTGov)
make drug-ref-build ALL_TRIALS=1                               # every distinct drug across all ctgov + anzctr
#   optional: WORKERS=<n> (concurrency, default 8) · REFRESH_DRUGS=1 · NO_REVIEW=1 · MODEL=<name>
```
### `make drug-ref-refresh-pottr` — refresh the POTTR reference data
Downloads the two public POTTR files (`drug_database.txt`, `drug_class_hierarchy.txt`) from
`raw.githubusercontent.com/fpylin/POTTR` into `<DATA_ROOT>/resources/drug_utility/pottr/current_version/`, moving the
previous version to `…/pottr/archive/<date>/` and recording the download date in `SOURCE.txt`. RxNorm is a manual
drop-in (UMLS-licensed) under `resources/drug_utility/rxnorm/current_version/`.
```bash
make drug-ref-refresh-pottr
```

`WORKERS` sets both concurrency and checkpoint-batch size (output identical regardless — see memory
`feedback-max-allowable-concurrency`). Entry: `aus_trial_universe/agentic/tasks/drug_utility/build.py`.

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

**Output:** the pure-3NF store `data/agentic/eligibility/current_output/` (`regime.tsv`,
`extracted_eligibility.tsv`, and the three `*_map.tsv` lookups). The materialized-join `combined.tsv` (the flat,
self-contained rows the matching engine reads) is denormalized, not 3NF, so it is written OUTSIDE the store, to
`data/agentic/eligibility/combined/combined.tsv`.
**Log:** `data/agentic/log/agentic_run_<label>_<timestamp>.log` (the whole run, both stages).

---

## Output Schema

The pipeline emits **5 pure-3NF masters** (in `eligibility/current_output/`) and a **denormalized joined view**
`combined.tsv` (in `eligibility/combined/` — outside the store). `combined.tsv` is the flat file the matching
engine reads: one row per satisfiable `(trialId, arm, conj_id)` conjunction (DNF — rows sharing `(trialId, arm)`
are ORed, cells within a row ANDed, exclusions inline as `NOT(...)`). Its **16 columns**, in order:

| Column | Source | Notes |
|---|---|---|
| `trialId` | regime/elig | NCT… or ACTRN… |
| `arm` | regime/elig | CTGov `armGroups[].label`; ANZCTR `intervention`/`comparator`; `all` — the join key to the drug path |
| `arm_type` | regime | CTGov `armGroups[].type` (EXPERIMENTAL / ACTIVE_COMPARATOR / …) |
| `conj_id` | elig | conjunction index within `(trialId, arm)` |
| `cancer_type` | extract | trial's wording, `value [source]`, inline `NOT()` |
| `oncotree_name` / `oncotree_code` | map | OncoTree mapping of `cancer_type` (via `cancer_type_map`) |
| `gene_alteration` | extract | trial's wording, `value [source]` |
| `gene_alteration_findingmodel` | map | Hartwig finding-model syntax (via `gene_alteration_map`) |
| `molecular_signature` | extract | `value [source]` |
| `molecular_signature_findingmodel` | map | finding-model syntax (via `molecular_signature_map`) |
| `molecular_biomarker` | extract | `value [source]` |
| `prior_therapy` | extract | `value [source]` |
| `arm_drugs` | drug join | distinct canonical drug names for the arm (`(trialId, arm)` → drug store), `; `-joined |
| `drug_class` | drug join | general drug class(es) of the arm's drugs, `; `-joined |
| `pottr_drug_class` | drug join | POTTR class hierarchy of the arm's drugs, ` | `-joined |

Drug facts join in via the `(trialId, arm)` key to the drug utility path. **Not yet in `combined.tsv`** (owed —
see the handover): TGA/PBS regulatory status + the **main vs auxiliary** drug-role distinction (drug Phase 2).

> **Verification:** the finding-model / OncoTree / drug outputs are checked **manually** against the legacy
> `data/eligibility_path/exports/final/eligibility_*_resource_*.tsv` and the hand-curated resource files.
> The curated resources are held-out verification data, not training input (see the design memory).

---

## Response cache, corrections & pruning
The LLM response cache (`data/agentic/cache/`) is the pipeline's **determinism layer** and its
**fast-re-run** mechanism — one directory shared by BOTH paths (eligibility `run.py` + drug `build.py`).
Each entry is content-addressed: the filename is a SHA-256 of the full request `{model, instructions
(the prompt), input, schema, …}`, and the file holds the validated response. Identical request → cache
hit → no API call. Disable per-run with `--no-cache`.

**Growth.** The cache only grows — it is content-addressed, has no eviction/TTL, and never self-prunes.
Re-running the same trials adds zero files (same fingerprints → same files). A *new* file appears only for a
new fingerprint: a new trial input, or **a changed prompt/schema/model**. So iterating on prompts leaves the
old entries behind as **orphans from outdated prompts** — harmless but accumulating. The dir is fully
disposable: `make agentic-clean` wipes it (next run just re-pays the API).

**Provenance + pruning (`make agentic-cache-prune`).** Each entry records its provenance — the agent `name`
and a `prompt_sha` (SHA-256 of that agent's `instructions`). The prune tool enumerates the *current* live
agents across both paths (offline — building an agent never calls the LLM) and classifies every entry:
`live` (its `(name, prompt_sha)` matches a current agent → keep), `stale` (the agent's prompt changed, or the
agent was removed → an **outdated prompt** → remove), or `unknown` (legacy pre-provenance entry → kept unless
`--purge-unknown`).
```bash
make agentic-cache-prune                       # dry-run report (scanned / live / stale / unknown)
make agentic-cache-prune APPLY=1               # delete the outdated-prompt entries
make agentic-cache-prune APPLY=1 PURGE_UNKNOWN=1   # also drop legacy/untagged entries
```
This also runs **automatically at the start of every `agentic-run` / `drug-ref-build`** (removing only `stale`
entries, never `live` or legacy), so a prompt edit self-cleans its old cache on the next run. Skip it with
`--no-cache-prune`.

**Making corrections** (three channels — know which is durable):
1. **Fix the prompt / validator / schema** — the intended channel. Changing an agent's `instructions` changes
   its fingerprint, so those calls recompute live (and auto-prune drops the old entries); everything unchanged
   stays cached. Applies the fix across all trials. This is how you fix a *systematic* error.
2. **Hand-edit a map table** (`cancer_type_map.tsv` / `gene_alteration_map.tsv` / `molecular_signature_map.tsv`
   in `current_output/`) — a durable curated override. Mapping is **lookup-first**: a value already in the map
   is never recomputed, so your edit sticks and propagates to every trial sharing that value. Use for one-off
   mapping errors.
3. **Hand-editing `extracted_eligibility.tsv` / `regime.tsv` does NOT survive a re-run of that trial** —
   re-running replaces the trial's rows wholesale, and the cache may re-serve the old (uncorrected) LLM
   response. To force a specific trial to re-extract fresh without a prompt change, re-run it with `--no-cache`.
   There is currently no curated-override layer for extraction rows (only for mappings).

---

## Testing
```bash
make agentic-tests        # 113 unit tests, no API, all fake-client
```
Every `make agentic-run` also runs these as a preflight and aborts if any fail.

---

## Clean between runs
```bash
make agentic-clean        # rm -rf data/agentic/{eligibility,log,cache}; recreate eligibility/ + log/
```
Scoped strictly to the transient artifacts `data/agentic/{eligibility,log,cache}` — it never touches the colocated
inputs/resources (`trial_universe/`, `resources/`, `drug_annotations/`, `analysis/`). Note this wipes the whole
`eligibility/` tree (the accumulating `current_output/` store, `combined/`, and `archive/`) — all regenerable.
