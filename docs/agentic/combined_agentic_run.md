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
| `make agentic-arm-consistency` | **Referential-integrity check** on the arm join key: every `trial_arm_id` referenced by the eligibility tables + drug `trial_to_intervention` + `trial_arm_drug_role` exists in the shared `trial_arms` registry. Exit 0 = consistent. No API. |
| `make agentic-export` | **Build the matching-engine export.** Set A = the wide flat `export/trial_eligibility.tsv` (trial info + eligibility + intervention, one row per `(trial_arm_id, conjunction)`); Set B = the 6 drug 3NF tables, referenced in place (a `MANIFEST.md` points at them). Deterministic join; no API. `SNAPSHOT=1` also mints an immutable self-contained `export/snapshot_<ts>/` bundle (Set A + a frozen copy of Set B). |
| `make agentic-drug-migrate-trial-arms` | Re-key drug `trial_to_intervention` to `trial_arm_id` against the fresh registry + report intervention-input additions/deletions → `data/agentic/analysis/`. Dry-run by default; `APPLY=1` rewrites ONLY that drug file. No API. |
| `make drug-ref-build` | Build/refresh the drug reference (6 tables). See below. |
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
`raw_name_to_map` (the input fragment each canonical came from). `trial_to_intervention` records which trial ARM
each input name came from as `(trial_arm_id, input_intervention_name)` (linking to the shared `trial_arms`
registry, which the build also populates), and the build **logs per-trial drug attribution**. Incremental
(existing non-stale drugs are pure lookups), batched with a checkpoint save after each batch (resumable),
soft-fails per drug. Logs to `data/agentic/log/`.
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

**Output:** the shared `trial_arms` registry `data/agentic/trial_arms/current_version/trial_arms.tsv` (the arm
spine) + the eligibility content store `data/agentic/eligibility/current_output/` (`arm_eligibility_raw.tsv`,
`interpreted_eligibility.tsv`, the three `*_map.tsv` lookups + the three `finalised_*_map.tsv`), all keyed by
`trial_arm_id`. The materialized matching-engine flat file (`trial_eligibility.tsv`) is built SEPARATELY by
`make agentic-export` (see the Export section), not by `agentic-run`.
**Log:** `data/agentic/log/agentic_run_<label>_<timestamp>.log` (the whole run, both stages).

---

## Output Schema

Arm identity lives ONCE in the **shared `trial_arms` registry** (`data/agentic/trial_arms/current_version/
trial_arms.tsv`: `trial_arm_id, trialId, registry, arm, arm_type` — both registries; `trial_arm_id` is the
deterministic slug `{trialId}::{arm}`). Both paths link to it by `trial_arm_id`:
- **Eligibility** (`eligibility/current_output/`): `arm_eligibility_raw` + `interpreted_eligibility` (each keyed by
  `trial_arm_id`) + the 3 value→vocab map tables.
- **Drug** (`drug_annotations/current_version/`): `trial_to_intervention` = `(trial_arm_id, input_intervention_name)`.

## The matching-engine export (`make agentic-export`)

The deliverable is TWO sets (built by `aus_trial_universe.agentic.export`, deterministic, no API):

**Set A — `data/agentic/export/trial_eligibility.tsv`** — one wide flat file, **one row per
`(trial_arm_id, conjunction_index)`** = one satisfiable eligibility path of one arm (DNF: rows sharing an arm are
ORed, cells ANDed, exclusions inline `NOT()`). It joins `interpreted_eligibility` ⋈ `trial_arms` ⋈ the new
`trial_info` master ⋈ the **FINAL** vocab maps (`finalised_*_map.tsv`, `*_FINAL` columns; `oncotree_name` rendered
from the FINAL code) ⋈ the per-arm raw intervention names (the Set-B join key). **33
columns** in four groups (full list in the export `MANIFEST.md`):
- *keys/arm* — `trial_arm_id, conjunction_index, trialId, registry, arm, arm_type`
- *trial info* — `official_title, phase, overall_status, study_type, lead_sponsor, min_age, max_age, sex, start_date,
  primary_completion_date, completion_date, last_update_date, countries, has_AU_site, AU_site_status, AU_site_cities,
  trial_url` (from the `trial_info` master — raw CTGov/ANZCTR, deterministic)
- *eligibility* — `cancer_type_interpreted, oncotree_name, oncotree_code, gene_alteration_interpreted,
  gene_alteration_findingmodel, molecular_signature_interpreted, molecular_signature_findingmodel,
  molecular_biomarker_interpreted, prior_therapy_interpreted`
- *intervention* — `arm_intervention_names_raw` (the raw-intervention join key into Set B; canonical ids, the
  main/auxiliary role split, drug classes and TGA/PBS are reached through Set B, NOT duplicated into Set A)

**Set B — the 6 drug 3NF tables** at `drug_annotations/current_version/`, **referenced in place** (single source of
truth — a `MANIFEST.md` in `export/` points at them; NO duplicate). The engine joins from Set A into Set B:
`arm_intervention_names_raw → intervention_to_canonical → drug_annotations_core (pottr class) →
drug_regulatory_approvals (TGA/PBS per indication)`, and `trial_arm_id → trial_arm_drug_role (main/aux)`.

`make agentic-export SNAPSHOT=1` also writes an **immutable, self-contained** `export/snapshot_<ts>/` (Set A + a
frozen copy of Set B + manifest) for hand-off — a dated copy that can't drift.

**Owed (top follow-up):** TGA/PBS approval is *indication-specific*, so matching a trial's OncoTree code ↔ a drug's
approved indication needs the drug side's free-text `cancer_type`/`biomarker` mapped into the same vocab (the
symmetric-match mapping). Until then the engine string-matches / defers the approval leg.

> **Verification:** the finding-model / OncoTree / drug outputs are checked **manually** against the legacy
> `data/eligibility_path/exports/final/eligibility_*_resource_*.tsv` and the hand-curated resource files.
> The curated resources are held-out verification data, not training input (see the design memory).

---

## How the `interpreted_eligibility` table is decided

The LLM **interpreter** makes the *semantic* calls (guided by the extraction prompt); **deterministic code** does
the *structural* assembly.

- **Split into a new row (OR):** when the source offers mutually-substitutable alternatives — "A, B, or C",
  "and/or", or conditionals ("if cancer A → mutation X; if B → Y" → two rows). *LLM.*
- **AND within a cell:** when several requirements of the *same* criterion must all hold — e.g.
  `solid tumour AND NOT(melanoma)`, or histology + stage. *LLM.*
- **AND across cells:** implicit — a row is a conjunction across the 5 columns. *Structural.*
- **`NOT()`:** an excluded criterion — a span sourced from `[EXCLUSION CRITERIA]`, or "except / other than",
  "no prior X". *LLM, aided by the raw span's source tag.*
- Then **code** deterministically: distributes trial-wide criteria onto each cohort, de-dups commutative /
  identical rows, and enforces "never AND two different cancer types" (the cohort value wins).

### Paraphrasing — what an interpreted cell is
An interpreted cell is the LLM's **normalized restatement**, *not* a verbatim copy (the verbatim source lives in
`arm_eligibility_raw` — the audit anchor). Paraphrasing restructures the text into the DNF logic above, normalizes
terminology, drops irrelevant qualifiers, **and corrects obvious typos** (e.g. raw `squamus cell carcinoma` →
interpreted `squamous cell carcinoma`) — while preserving the eligibility meaning. To audit an interpreted cell,
join back to `arm_eligibility_raw` on `trial_arm_id` and compare against the verbatim spans + their `[source]`.

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
3. **Hand-editing `arm_eligibility_raw.tsv` / `interpreted_eligibility.tsv` (or the shared `trial_arms.tsv`) does
   NOT survive a re-run of that trial** —
   re-running replaces the trial's rows wholesale, and the cache may re-serve the old (uncorrected) LLM
   response. To force a specific trial to re-extract fresh without a prompt change, re-run it with `--no-cache`.
   There is currently no curated-override layer for extraction rows (only for mappings).

---

## Testing
```bash
make agentic-tests        # 134 unit tests, no API, all fake-client
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
