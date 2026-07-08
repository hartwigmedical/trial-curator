# Trial Curator v2 — Agentic Free-Text Pipeline Spec

- **Status:** initial spec, locked for the first build slice. Living document.
- **Branch:** `AUS-328-Aus-trial-universe-v2`
- **Fallback:** tag `aus-trial-eligibility-path-resource-generation-v1` freezes the pre-rewrite state; old branch `AUS-328-Aus-trial-universe` untouched.
- **LLM provider:** OpenAI only (no Claude API access in this org).

> Provisional names are marked **[TBD]**. The v2 runtime + tasks live in a new subfolder `aus_trial_universe/agentic/` **[TBD]** (see §9) — rename freely.

---

## 1. Context & motivation

Every hard problem in this pipeline — both the eligibility path and the drug-utility path — is really the same problem: turning **large, messy free text** into structured, normalized data. Today that's handled by a scatter of bespoke LLM scripts and structured `.py` curations (`pydantic_curator`), plus separate client layers (`trialcurator`, `trial_drug_curation/llm`) and a one-off agentic loop (`oncotree_agentic_workflow`).

v2 replaces all of that with **one reusable agentic machinery** for free-text tasks. The behavior we want is the one you get by handing a task to Claude Code: analyze → dispatch specialists → consolidate → check → loop on error. The insight (see §3) is that we replicate that *behavior* with a **deterministic, code-driven orchestrator** rather than an autonomous LLM planner — so it is testable, reproducible, cheap, and debuggable across thousands of trials.

## 2. Principles (locked)

1. **Clean slate.** Nothing from the current setup is a fixed point. Old modules are mined for *learnings only*, never preserved or extended.
2. **Schema-first.** The caller *always* pins the exact output structure (the table). The framework never infers or negotiates output shape. This is the core contract and makes every task unit-testable against its schema, independent of the LLM.
3. **Deterministic orchestration (pattern B).** Control flow is Python. LLMs power the *stages*, never the *plan*.
4. **OpenAI, model-agnostic.** One client; the model is configuration, swappable per agent.
5. **Determinism knobs on by default.** `temperature=0` + seed + response caching baked in from day one (keeps the door open for whatever replaces the old run-to-run diff).
6. **One workflow, both paths.** The same machinery is meant to serve eligibility *and* drug-utility tasks. If it succeeds, the `eligibility_path` / `drug_utility_path` split dissolves into a single task catalog on one runtime (see §9). This is a hypothesis, validated incrementally — eligibility first.

## 3. Architecture overview

Two layers. The orchestrator is **code, not an LLM** — this is the whole point of pattern B.

|                | What it is                          | Decides *what runs next*? | Deterministic? |
|----------------|-------------------------------------|---------------------------|----------------|
| **Orchestrator** (a Workflow) | plain Python                        | **Yes** — fixed steps     | Yes            |
| **Agent** (a specialist)      | one OpenAI call + required schema   | No — does its one job     | No (temp=0 tightens) |

Intelligence lives *inside* agents (parsing messy text). Control flow — which agents run, in what order, how results merge, when to retry — lives in code. We copy the *shape* of a Claude Code session and freeze it into Python; no model chooses the plan.

## 4. Primitives layer — unified `client.py`

Replaces `trialcurator/{openai_client,drug_openai_client,llm_client}` and `trial_drug_curation/llm/client.py` with **one** client. Responsibilities:

- **Structured output:** given a pydantic output schema, return a *validated* object (OpenAI strict JSON-schema / SDK pydantic parsing). Retries on schema-mismatch.
- **Reliability:** retries + backoff; rate-limited concurrency (async).
- **Determinism:** `temperature=0`, seed, and a **response cache** keyed on (model, prompt, schema, params) → identical inputs return identical outputs.
- **Model-agnostic:** model + params passed via config; swapping models is a config change, not a code change.
- **Tracing:** per-call log of input/output/tokens for debugging agentic loops.

## 5. Agent & Workflow layer

- **Agent** = `prompt template + input schema + output schema + model config + optional tools`. One well-scoped LLM job. May use tools (e.g. an OncoTree lookup) and self-loop *within* its bounded job, but never decides the overall workflow.
- **Workflow** = code-defined composition:
  - **fan-out** — dispatch specialist agents on sub-aspects (often one per output column).
  - **consolidate** — merge/dedup into the target rows (code, or an LLM *synthesizer* agent if needed).
  - **check** — schema validation + rule checks + optional LLM/adversarial judge.
  - **loop** — bounded evaluator–optimizer: feed check failures back to the offending agent, re-run, cap at `MAX_ATTEMPTS`.

Reference shape (eligibility extraction):

```python
def extract_eligibility(trial_free_text):            # orchestrator = plain Python
    # FAN-OUT: each specialist = one schema-validated LLM call
    cancer = cancer_type_agent(trial_free_text)        # -> {oncotree_name, oncotree_code}
    genes  = gene_alteration_agent(trial_free_text)    # -> [{gene, alteration_desc}, ...]
    # ... more column specialists

    rows = build_dnf_rows(cancer, genes, ...)          # CONSOLIDATE (code)
    problems = validate(rows)                          # CHECK (schema + rules [+ LLM judge])

    attempts = 0                                       # LOOP (bounded)
    while problems and attempts < MAX_ATTEMPTS:
        rows = repair(rows, problems)                  # re-run only the failing agent w/ feedback
        problems = validate(rows)
        attempts += 1
    return rows
```

Every task in §7 is one such Workflow reusing this machinery.

## 6. Eligibility path — first concrete application

**Output = a table the user pins**, e.g.:

```
trialId | cohort | cancer_type | gene_alteration | molecular_signature | prior_therapy | ...
```

### 6.0 Input — ALL relevant trial sections
The extractor **and** the faithfulness judge receive **all relevant sections of the trial input, not just the eligibility criteria** — cancer type and molecular selection are often stated in the title, conditions, or description. Extractor and judge must see the *same* assembled text (else the judge flags title/description-derived facts as "invented").
- **CTGov (structured):** brief title, official title, brief + detailed description, conditions, keywords, eligibility criteria.
- **ANZCTR (from `anzctr_field_extractions.csv`):** study title, scientific title, health condition, inclusion criteria.
Assembly is per-source; the workflow takes one `source_text` blob per (trial, cohort).

### 6.1 Combination logic — Disjunctive Normal Form (DNF)
- **One row = one satisfiable conjunction** (all its cells ANDed). Rows sharing a `(trialId, cohort)` are **ORed**.
- **Conditionals become co-occurrence.** "if cancer A then mutation X; if cancer B then mutation Y" →
  ```
  T1 | C1 | A | X
  T1 | C1 | B | Y
  ```
- **`group_id`** curbs combinatorial blow-up when independent OR-dimensions AND together (share common criteria instead of full cross-product).
- **Inclusion vs exclusion** handled by negation (a `negate`/exclusion mechanism per cell/row). *(exact mechanism finalized when we spec the columns)*
- **Rationale:** keeps the flat table, makes downstream matching trivial ("patient satisfies any row"), stays readable for the human curator. Escalate to a normalized criteria table only if real trials produce genuine blow-up.

### 6.2 Cell contents & the curation boundary
Two consumers, in sequence: **human curator first, then the automated matching engine.** So there are two representations with a curation boundary between them:

```
free text ─[extract WF]─▶ human-readable DNF table ─[human curates]─▶ ─[convert WF]─▶ matcher-encoded table
                          cells = NORMALIZED terms                     cells = finding-model syntax / codes
```

- **Normalization sits PRE-curation** (what the human sees & edits): `cancer_type` = OncoTree name (+ code); `gene_alteration` = normalized human description. The curator verifies *meaning*.
- **Finding-model syntax conversion is a SEPARATE downstream Workflow** run on the curated table (term → e.g. `SmallVariant[gene=EGFR]`). The finding-model syntax is **term-level only (no booleans)** — all boolean logic stays in the DNF rows.
- **Consequence:** with one curation pass on the human-readable table, the conversion step's output is not human-reviewed → its check/adversarial-judge must carry more weight.

## 7. Task catalog (each = one Workflow)

| Task | Input → Output | Notes |
|------|----------------|-------|
| **Eligibility extraction** | free text → DNF table (normalized cells) | first slice |
| **Cohort alignment** | free text cohorts ↔ structured cohort data | slice 2 (hardest; separable) |
| **Finding-model conversion** | curated term → finding-model syntax string | post-curation; pull grammar from [finding-datamodel](https://github.com/hartwigmedical/hmftools/tree/master/finding-datamodel) when building |
| **Cancer-type → OncoTree** | free text → OncoTree name + code | used as the `cancer_type` specialist |

## 8. First build slice (thin, end-to-end)

1. Scaffold `aus_trial_universe/agentic/` **[TBD]** (see §9 layout).
2. **`client.py`** — §4 (structured outputs, retries, temp=0/seed/cache, tracing).
3. **Workflow engine** — generic fan-out / consolidate / check / bounded-loop.
4. **One eligibility-extraction Workflow** with **2 specialist agents** (`cancer_type`→OncoTree, `gene_alteration`) to prove the spine; `cohort` treated as given/single; emit a DNF table with pre-curation normalized cells.
5. **Check** = schema/grammar validation + an LLM faithfulness judge (does the DNF cover the source without hallucination); loop cap ~2–3.

More columns = more specialist agents, added incrementally. Tools start minimal (reference data provided inline; function-calling added once the spine works).

## 9. Repo layout & module retirement schedule

Everything stays under `aus_trial_universe/` (still AU/NZ trials). The v2 runtime **and** its tasks live in one new sibling subfolder — provisionally `aus_trial_universe/agentic/` **[TBD]** — deliberately *not* split by path, because the same machinery serves both:

```
aus_trial_universe/
  eligibility_path/      # legacy — reference; retired as v2 supersedes it
  drug_utility_path/     # legacy — reference; retired as v2 supersedes it
  agentic/               # [TBD] the v2 approach (unifies both)
    core/                # runtime: client.py, agent.py, workflow.py, cache + tracing
    tasks/               # one Workflow per task: extraction/, cohort_alignment/,
                         #   finding_model_conversion/, oncotree_mapping/, drug_curation/, ...
    tools/               # reference-data lookups agents can call (OncoTree, finding-model vocab, ...)
```

- **`client.py`** lives in `agentic/core/`; hoist to a more shared location only if something outside `aus_trial_universe/` ever needs it (unlikely).
- **Unification:** eligibility and drug-utility tasks are just entries in one `tasks/` catalog on one runtime — no path split inside `agentic/`.
- **Cross-path coupling learning:** the RxNorm identity code physically lives in `drug_utility_path` but is imported by the eligibility path — do **not** reintroduce this; shared identity/reference code belongs in `agentic/tools/`.

| Module | Action |
|--------|--------|
| `ui/` | ✅ deleted (staged) |
| `actin_curator/` | retire **with** the Dockerfile/README rewrite (it's the Docker `ENTRYPOINT` + README subject) once v2 has an entrypoint |
| `pydantic_curator/` | keep as *learnings* reference → delete when v2 extraction supersedes it (imported by batch-run steps + `load_curated_rules`) |
| `trialcurator/` | keep as client reference → delete when unified `client.py` lands (imported by `iii_extract_drugs`) |
| `qa/` (run-to-run diff) | keep → delete when the new run-comparison is specced (imported by `recursive_end_to_end_workflow`) |

> Note: `eligibility_path/` and `drug_utility_path/` themselves become retirement candidates once their tasks are re-implemented as Workflows under `agentic/` — validated incrementally, eligibility first.

## 10. Open / deferred decisions

- **Run-comparison method** — replacement for `qa/final_resource_diff.py`; deferred, in scope later.
- **Reproducibility final stance** — determinism knobs are baked, but how the new comparison treats residual LLM variance is TBD.
- **Cohort alignment** — full free-text ↔ structured design (slice 2).
- **Finding-model grammar** — pulled from the finding-datamodel repo when we build the conversion Workflow; not needed for slice 1.
- **Exclusion/negation mechanism** in the DNF table — finalized with the column spec.
- **Model tier defaults** — per-agent model config; pick concrete OpenAI models (cheap default + escalation) when wiring, from the current model list.
- **Package name** — `agentflow/` is provisional.

## 11. Verification approach

- **Orchestration/consolidate/check code:** unit-testable with mocked agents — no LLM needed (deterministic Python).
- **Agents:** schema-conformance tests (mocked) + a small "external" suite hitting the real model on sample trials.
- **Slice acceptance:** run the thin eligibility Workflow end-to-end on a handful of real trials; inspect the DNF table for faithfulness and schema validity.

## 12. Non-goals

- No autonomous LLM orchestrator (pattern A).
- No structured `.py` curation output (the old `pydantic_curator` format).
- No preservation of the `qa/` run-to-run diff.
- No new cross-path coupling.
