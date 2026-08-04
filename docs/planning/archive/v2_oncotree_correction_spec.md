# OncoTree mapping correction — SPEC (code changes + rerun steps)

Status: **✅ COMPLETE — signed off and migrated to production 2026-08-04.** (Historical note: this began as a pre-implementation spec.) Companion record: `data/backups/known_good_20260804_post_oncotree/`. Companion: `docs/v2_agentic_handover.md` §B1 holds the measured defect inventory.

## 0. Constraints set by the user (2026-08-03)

1. **One comparison file**, shaped like `finalised_cancer_type_map.tsv`, with current vs updated name and code
   side by side. Not a workspace tree.
2. **Fix the errors in the initial mapping doer/reviewer prompts and/or the reconciliation logic and prompts**, as
   appropriate to each defect. (So: not a bolt-on post-processor. Where a defect is the prompt's fault it is fixed
   in the prompt; where it is a missing deterministic rule it is fixed in reconciliation.)
3. **`oncotree_name` and `oncotree_code` must never disagree — by design.** If one is chosen the other is derived
   programmatically.
4. **No existing data is modified until sign-off.**

---

## 1. Change A — the single comparison file (replaces the workspace tree)

**Delete** `summary.md` / `findings.tsv` / `side_by_side.tsv` / `review_queue.tsv` / `equivalence_groups.tsv` and
the per-run subfolders. **Emit one file**, overwritten in place:

```
data/agentic/analysis/oncotree_review/finalised_cancer_type_map_review.tsv
```

| column | meaning |
|---|---|
| `cancer_type` | the key — identical to the production map table |
| `oncotree_name_current` | today's `oncotree_name` (LLM-authored, Step 1) |
| `oncotree_code_current` | today's `oncotree_code_FINAL` — the value that ships to the engine |
| `oncotree_name_updated` | proposed name, **derived from** `oncotree_code_updated` |
| `oncotree_code_updated` | proposed code |
| `changed` | `yes` / `no` |
| `issues_fixed` | defect ids the update resolves |
| `issues_remaining` | defect ids still open (empty = fully corrected) |
| `arms` | how many trial arms use this value — sorts the review by impact |

Sorted `changed` first, then by `arms` descending, so the top of the file is the highest-impact review.
Row count and key set stay identical to `finalised_cancer_type_map.tsv`, so the two diff cleanly.
`make oncotree-review` keeps its name; `DROP_VACUOUS=1` still selects the policy variant.

*(Drop any of the last three columns if you want it barer — they are diagnostics, not the comparison itself.)*

---

## 2. Change B — name/code can never disagree (constraint 3)

**Root cause:** `OncotreeMapping` asks the LLM for two independent renderings of the same expression and stores
both. Nothing checks them against each other, and Step 2 rewrites only the code — hence 46 mismatched rows and
69 leaked-name operands.

**Fix — one authoritative field, the other always derived.**

| | now | after |
|---|---|---|
| `mapping/schema.py:OncotreeMapping` | `oncotree_name` + `oncotree_code` | **`oncotree_code` only** |
| `schema.py:CancerTypeMap.oncotree_name` | LLM-authored, persisted | **derived at write time**, never LLM-authored (column kept, so no downstream reader changes) |
| name rendering | `export.py:_render_oncotree_name` (private, export-only) | moved to `tools/oncotree.py:render_name_expression()` — the single source, imported by `export.py`, `store.py`, `reconcile.py`, `map_approvals.py` |

**Two new functions in `tools/oncotree.py`:**

- `normalise_code_expression(expr) -> (code_expr, problems)` — resolves every operand to a CODE. Accepts a code, a
  name, or a case-variant of either, and converts it. *This is the "if a name is chosen the code is selected too"
  half*, and it makes `lex_leaked_name` + `lex_case_variant_*` structurally unable to survive.
- `render_name_expression(code_expr) -> name_expr` — the reverse. *The "if a code is chosen the name is selected
  too" half.*

Both operate on the parsed AST, not on text substitution. `render(normalise(x))` is idempotent — asserted by test.

**Effect:** `xf_name_code_mismatch` (46) and `lex_leaked_name` (69) become impossible rather than detected.

---

## 3. Change C — the deterministic validator (`tools/oncotree.py`)

Port `oncotree_review/expr.py` + `checks.py` into `tools/oncotree.py` (parser, canonical form, `CATALOGUE`,
detectors), with unit tests, and **delete `aus_trial_universe/oncotree_review/`**. `invalid_codes()` is replaced by
`expression_problems(expr) -> list[Problem]`; each problem carries its catalogue id and severity.

Consumed by three call sites, so one definition governs everything:
- `mapping/workflow.py:map_oncotree.check()` — `error` severity fails the refine loop.
- `mapping/reconcile.py:adjudicate_group.check()` — same gate on the reconciled value.
- a new `qa/gates.py` gate — any `error` in the store fails the run (this is the production backstop that would
  have caught all of this months ago).

`warn` severity is reported, never fatal.

---

## 4. Change D — mapper prompts (doer + reviewer)

Added to `_ONCOTREE_RULES` and mirrored in `ONCOTREE_REVIEWER_INSTRUCTIONS` (the reviewer must fail what the doer
must not emit — asymmetry there is how defects survive). Every rule below gets a counter-example drawn from a real
traced value, in the existing `MAPPINGS YOU MUST FAIL` style.

| # | rule | why (real value it kills) |
|---|---|---|
| D1 | **An exclusion must be a tumour-TYPE carve-out from the stated positive scope.** Prior/second-malignancy history, CNS involvement, metastasis site, synchronous second primaries are NOT tumour types — drop them. | `NOT(history of breast cancer)` → `NOT(BREAST)`; `NOT(any hematologic malignancies)` → `BREAST AND NOT(Haematological malignancy)`; `NOT(synchronous NSCLC disease)` → `NSCLC AND NOT(LUSC)` |
| D2 | **A positive scope is mandatory.** Never emit an expression that is only exclusions — if the cell states no tumour type, return `""`. | `NOT(BREAST)`, `NOT(BRAIN) AND NOT(BLL)` |
| D3 | **Never negate a sentinel.** | `NOT(Pan-cancer)`, `BREAST AND NOT(Haematological malignancy)` |
| D4 | **No nested `NOT()` — except two whitelisted idioms** (user, 2026-08-03; the matching engine will special-case them). `SKIN AND NOT(MEL)` (non-melanomatous skin cancer) and `NSCLC AND NOT(LUSC)` (non-squamous NSCLC) MAY appear inside a `NOT()`. Depth 2 maximum, and the inner difference must be one of those two pairs. Everything else flattens; if the carve-out condition is inexpressible, OMIT the exclusion — never broaden it. | legal: `Pan-cancer AND NOT(SKIN AND NOT(MEL))` · illegal: `HGSOC AND NOT(UCEC AND NOT(UEC))` |
| D10 | **A broad term and its own subtype must not appear together — pick the one the source supports.** The existing rule covers only the AND case ("if it genuinely spans the group, use OR"), which is what licensed `OVARY OR HGSOC`. Extend it to OR. | `CCOV OR EOV OR HGSOC OR OVARY OR PERITONEUM` from a source naming exactly three histologies |
| D5 | **Never AND two mutually exclusive tumour types** (a patient has one tumour). Use OR, or pick the one the source means. Applies inside `NOT()` too. | `DLBCLNOS AND CLLSLL`; `NOT(HL AND NHL)`; `(PRSCC OR PRNE) AND PRAD` |
| D6 | **Never exclude an ancestor of a positive code**, and never leave an OR branch that a sibling branch excludes. | `PCM AND NOT(… MBN …)`; `(Solid tumour AND NOT(BRAIN)) OR GB` |
| ~~D7~~ | ~~Don't state an exclusion disjoint from the positive scope~~ — **WITHDRAWN (user, 2026-08-03).** The mapper's job is a FAITHFUL translation of one value; removing a no-op is a cross-value cleanup and belongs to reconciliation (E6). The **reviewer must not flag it either**, or the refine loop burns attempts fighting a faithful mapping. Consequence: Step-1 `oncotree_code` keeps the faithful form and `oncotree_code_FINAL` the cleaned one — which is exactly the audit trail we want. | — |
| D8 | **Emit the canonical form directly:** one factored `NOT(A OR B)`, no redundant parens, no OR branch subsumed by another. | `Diffuse Glioma AND NOT(DMG) AND NOT(HGGNOS)`; `(MBN OR BL OR DLBCLNOS OR PMBL)` |
| D9 | **Output CODES.** (The `oncotree_name` field is gone; the vocabulary block already shows `Name (CODE)`.) | every leaked name |

---

## 5. Change E — **ONCOTREE MAPPING REFINEMENT** (Step 2, renamed and re-designed)

"Reconciliation" undersells what this stage has to be (user, 2026-08-03). It is an alternating sequence of
**deterministic rewrite** and **LLM judgement**, run to convergence — not one step.

**What it is today:** repair-leaked-names (**never fires** — gated on `invalid_codes()`, which is blind to mixed
case) → sort a flat OR (**bails on anything containing `AND` / `NOT(` / `(`**, i.e. on everything that could
diverge) → group by input-text similarity → LLM-adjudicate the groups.

**The structural gap that explains the 35 broken values:** the LLM only ever sees *groups*. A value that is
individually broken but has no divergent sibling is never shown to anything that could fix it. There is no
per-value repair path at all. **R4 below is that missing path.**

### The sub-stages

| id | sub-stage | kind | what it does | on failure |
|---|---|---|---|---|
| **R0** | **Operand normalisation** | deterministic | Resolve every operand to a CODE — accepts a code, an OncoTree name, or a case-variant of either. Collapse whitespace. Guarantees the name/code invariant (Change B). | operand unresolvable → `lex_unknown_operand`, hand to R4 |
| **R1** | **Structural canonicalisation** | deterministic | Factor `NOT(A) AND NOT(B)` → `NOT(A OR B)`; sort OR branches; dedupe operands; flatten same-operator nesting; strip redundant parentheses. | — (total function) |
| **R2** | **Logical simplification** | deterministic | Drop vacuous exclusions (E6/user decision); resolve direct double negation `NOT(NOT(X))` → `X`. **Only rewrites that are meaning-preserving under BOTH engine models** (exact code match and hierarchical match) — anything else is left for R4. | — |

> **⚠ `log_or_redundant_ancestor` (`MBN OR BL` → `MBN`) — resolved 2026-08-03 after two wrong turns.**
> I first listed it as a safe R2 rewrite, then withdrew it on the grounds that it holds only under hierarchical
> matching. **That withdrawal was wrong:** hierarchical matching is not optional for this deliverable — no patient
> is ever coded `Solid tumour`, so an exact-match engine would match nobody for any sentinel-mapped trial and the
> whole granularity doctrine (organ node when histology unstated) would be pointless. `parent OR child ≡ parent`
> is therefore sound, as the user said.
> **But the equivalence does not say WHICH term survives, and in this data the parent is often the error.**
> Of 23 distinct values, **19 are a parent CODE plus its own subtypes**, and several read as an over-broad
> umbrella the mapper added on top of correct specifics: `CCOV OR EOV OR HGSOC OR OVARY OR PERITONEUM` comes from
> *"high grade serous, high grade endometrioid or clear cell ovarian cancer, fallopian tube carcinoma"* — absorbing
> to `OVARY OR PERITONEUM` admits every ovarian histology, when the correct repair is to DROP `OVARY` and keep the
> three. Blind absorption would cement the umbrella and delete the correct codes: the inverse of the right fix.
> **Resolution — split by whether the umbrella is a sentinel:**
> - **4 values with a SENTINEL branch → absorb deterministically in R2.** A sentinel is only ever emitted when the
>   source states the whole group, so it is the intended scope (`CLLSLL OR Haematological malignancy OR NHL OR PCM`
>   from *"haematological malignancy, including CLL, MM…"* → `Haematological malignancy`).
> - **19 values with a parent CODE → route to R4** with the source cell; the LLM chooses the survivor. Backed by
>   the new prompt rule D10 so the mapper stops emitting the pair in the first place.
>
> The other R1/R2 rewrites are safe under either model: dropping a vacuous exclusion (the excluded code is
> unreachable either way), De Morgan factoring, OR-branch sorting, dedupe, paren stripping, name→code resolution.
| **R3** | **Validation** | deterministic | `expression_problems()` (Change C). Partition every value into clean / warn-only / **error**. | errors → R4 |
| **R4** | **Per-value repair** | **LLM** doer→reviewer | **NEW.** For each value R3 marks `error`, the doer receives the SOURCE cell, the current mapping, **the specific defect list**, and the D-rules; it returns a corrected expression. Re-validated by R3. This is the path that today does not exist. | still failing after `--max-attempts` → review queue, value left unchanged |
| **R5** | **Consistency detection** | deterministic | `find_inconsistencies` over **both** the canonical form and the input-text key. Grouping on the canonical form is what makes the two germ-cell renderings meet (+49 groups / 57 renderings collapsed; 168 / 544 with vacuous dropped). | — |
| **R6** | **Group reconciliation** | **LLM** doer→reviewer | The existing adjudicator, now also given each member's defect list. Prompt changed from *"preserve each member's AND/OR/NOT structure"* to *"repair it"* — that one instruction is what preserved every structural defect in the inventory. | → review queue |
| **R7** | **Converge** | deterministic | Re-run R0–R3 over everything R4/R6 emitted. Loop while anything changed, bounded at 3 passes. Nothing with a surviving `error` ships silently — it goes to the review queue. | bound exceeded → review queue |
| **R8** | **Derive + write** | deterministic | Render `oncotree_name` from the final code; write the finalised map tables, the joined view, and the comparison file. | — |

**Why the order matters:** R0–R2 shrink the problem before any LLM call (1,185 → 641 distinct expressions with
vacuous dropping), so R4/R6 are asked to think only about what deterministic rules genuinely cannot settle. And
R7 re-applies the deterministic layer to LLM output, so the model cannot re-introduce a non-canonical rendering.

**Idempotence is the acceptance test:** running the refinement twice must be a no-op. `R8(R0..R7(x)) == R0..R7(x)`.

---

## 6. Which layer fixes which defect

| defect | validator | canonical form | mapper prompt | reconciler | structural |
|---|:--:|:--:|:--:|:--:|:--:|
| `lex_leaked_name`, `lex_case_variant_*` | ✔ gate | ✔ | D9 | ✔ | **✔ primary** |
| `lex_unknown_operand`, `syn_unparseable`, `syn_empty_not` | **✔ primary** | | | | |
| `lex_whitespace_noise`, `syn_redundant_outer_parens`, `syn_or_order_noncanonical`, `log_duplicate_operand` | | **✔ primary** | D8 | ✔ | |
| `syn_multi_not_clauses` | | **✔ primary** | D8 | ✔ | |
| `syn_nested_not` | ✔ gate | | **✔ D4** | ✔ | |
| `syn_ambiguous_precedence`, `log_include_exclude_same`, `log_subtype_and_parent`, `log_sentinel_and_specific` | ✔ *(already gated — zero occurrences; keep)* | | ✔ *(already)* | | |
| `log_disjoint_and`, `log_disjoint_and_inside_not` | ✔ gate | | **✔ D5** | ✔ | |
| `log_exclude_ancestor`, `log_or_branch_excluded` | ✔ gate | | **✔ D6** | ✔ | |
| `log_negation_only`, `log_negated_sentinel` | ✔ gate | | **✔ D2/D3** | ✔ | |
| `log_or_redundant_ancestor` | | **✔ primary** | D8 | ✔ | |
| `log_vacuous_exclusion` | ✔ warn | ✔ *(decision 1)* | D7 | ✔ | |
| `xf_name_code_mismatch` | | | | ✔ E5 | **✔ primary** |
| `src_nontumour_exclusion` | | | **✔ D1** | ✔ | |
| `cov_unmapped_value`, `src_empty_for_cancer` | ✔ gate | | | | |
| `src_qualifier_in_interpreted` (1,057) | | | ✖ **out of scope** | ✖ | |

⚠ **`src_qualifier_in_interpreted` cannot be fixed here.** It is a defect in the *interpreted* cell
(`sarcomatoid histology <=30%`, `NOT(active brain metastases)` inside `cancer_type_interpreted`), which the export
ships as its own column. No mapping change touches it — it needs an **extraction** prompt change, which re-runs
extraction, not mapping. **Recommend tracking it as a separate item and not folding it into this correction.**

---

## 7. Where the code lives during testing (user, 2026-08-03)

**Nothing under `tasks/eligibility/` is edited until the corrections are approved.** The updated modules are
written inside the sandbox, under filenames that mirror their eventual homes, so approval is a file copy:

```
aus_trial_universe/oncotree_review/
  expr.py  checks.py            # already exist — the parser + defect catalogue
  updated/
    oncotree.py                 # -> tasks/eligibility/tools/oncotree.py
    schema.py                   # -> tasks/eligibility/mapping/schema.py   (OncotreeMapping loses oncotree_name)
    agents.py                   # -> tasks/eligibility/mapping/agents.py   (D1-D6, D8, D9 + E4)
    reconcile.py                # -> tasks/eligibility/mapping/reconcile.py (E1-E6)
  bind.py                       # test-only: injects `updated/` into the real pipeline at import time
  compare.py                    # builds the single comparison file
```

⚠ **The swap set is FOUR modules, not two.** `agents.py` and `reconcile.py` cannot deliver the fix alone: the
name/code guarantee needs `mapping/schema.py` (drop the LLM-authored name) and the validator needs
`tools/oncotree.py`. Both are imported by production code, so both must be sandboxed too.

**`bind.py`** follows the `demo.py` precedent — rebind the module attributes, then drive the REAL entry points:
it points `mapping.workflow`'s agent builders and `mapping.reconcile`'s helpers at the `updated/` versions before
`run.py` is invoked. The alternative (a sandbox copy of `workflow.py` + `run.py`) would let the sandbox drift from
the code path we are actually shipping; injection exercises the real one. `bind.py` is deleted at approval.

**On approval**, `updated/*.py` replace their four targets and the follow-on edits land in the same commit:

| file | change |
|---|---|
| `tasks/eligibility/mapping/workflow.py` | `check()` uses `expression_problems`; `OncotreeResult.oncotree_name` derived; `_oncotree_logic_problems` deleted (absorbed into `tools/oncotree.py`) |
| `tasks/eligibility/store.py`, `schema.py` | `oncotree_name` derived on write |
| `run.py` | `CancerTypeMap(...)` construction (2 sites) |
| `export.py` | `_render_oncotree_name` → import from `tools/oncotree.py` (single source) |
| `tasks/drug_utility/map_approvals.py` | same import; name derived |
| `qa/gates.py` | new `oncotree_expressions` gate |
| `tests/agentic/…` | parser / canonical form / normalise-render round-trip, one test per `error` detector, gate failure direction |
| `aus_trial_universe/oncotree_review/` | **deleted**; the comparison file moves to `qa/oncotree_compare.py` |

---

## 8. Rerun steps

**Cache note:** the response-cache key hashes the prompt text, so editing a prompt *automatically* misses — there
is no manual invalidation. Old entries become unreachable and `make agentic-cache-prune --apply` GCs them.
Because only the OncoTree prompts change, **gene_alteration and molecular_signature re-map as pure cache hits** —
`--map-only` re-runs all three columns but only cancer_type costs anything.

⚠ **The drug side uses the same mappers.** `make drug-ref-map-approvals` reuses `map_all_columns`, so
`approval_cancer_type_map` must be re-run in the same cycle or the symmetric match breaks (trial side on new
codes, drug side on old).

### Phase 0 — deterministic only, NO API, NO data change
**Phase 0 is NOT a re-mapping.** It applies only the deterministic sub-stages (R0–R3, R8) to the mappings ALREADY
IN THE STORE — rewriting existing expressions mechanically. The LLM is never asked to map anything. It runs first
because it is free, reviewable, and it tells us exactly which values genuinely need the model: 491 of 1,185 become
fully clean with zero API calls (1,106 with vacuous exclusions dropped). **Phase 1 is where the LLM re-maps.**

```bash
make agentic-tests                       # port + new unit tests green
make oncotree-review                     # regenerate the single comparison file
make oncotree-review DROP_VACUOUS=1      # the policy variant
```
→ **You review `finalised_cancer_type_map_review.tsv`** and sign off the deterministic layer.
Acceptance: 0 `error`-severity issues remaining except the values reserved for Phase 1.

**PHASE 0 RESULT (2026-08-03, `make oncotree-review`, no API, no data modified):**
distinct code expressions **1,185 → 635** · **1,324 of 4,971 map rows changed** · **4,899 rows clean** ·
27 warn-only · **45 rows still needing a decision** · idempotence verified (0 non-idempotent values).
Remaining, all of it Phase-1 prompt work: `log_or_redundant_ancestor` 28 (deliberately not auto-fixed — see the
correction note in §5) · `syn_nested_not` 17 · `log_disjoint_and` 14 · `log_vacuous_exclusion` 12 (inside kept
non-atom branches) · `log_negation_only` 8 · `log_negated_sentinel` 7 · `log_or_branch_excluded` 2.

### Phase 1 — prompt changes, live API, SANDBOXED
**Implementation note (2026-08-03): `--store-root` was not needed and is not used.** A dedicated sandbox runner,
`oncotree_review/run_phase1.py`, reads the store and writes only into the review folder — so there is no store
copy that could go wrong and no code path that could reach `masters/`. It drives the updated agents through the
SAME `core.review.review_refine` harness production uses, so the validated path is the real one.
It also uses its **own cache** (`analysis/oncotree_review/cache/`) rather than the shared one: the shared cache
must not be polluted with responses from prompts that may never be adopted, and `cache_prune` GCs by
(agent name, prompt sha) against the PRODUCTION registry — which does not know these agents, so a prune would
delete them, and a prune *from* here could delete production entries whose prompts the sandbox shadows.

**Concurrency:** sized from the live `x-ratelimit-*` headers rather than guessed (`--probe`). Measured
2026-08-03: **15,000 RPM / 40,000,000 TPM** → 500 workers / 500 max-concurrency, the same setting that hit ~92 %
TPM on the last full Step-1 build.

**Smoke test first — it earned its keep.** A 40-value run surfaced a regression *introduced by the new prompt*:
`metastatic invasive breast carcinoma` mapped to `BRCA` instead of `BREAST`. Cause: condensing the reviewer's
`MAPPINGS YOU MUST FAIL` list dropped four load-bearing counter-examples, including the one warning that `BRCA`
is NAMED "Invasive Breast Carcinoma" and is a near-synonym of the organ node. All four restored (BRCA/BREAST,
SCLC-vs-NSCLC wrong node, lazy empty, dropped OR-alternative) and the mapper's BREAST rule now names the trap
explicitly. Re-run: clean. **Lesson: a counter-example removed from the reviewer is a rule deleted** — exactly the
doer/reviewer asymmetry this whole correction is about, reproduced by me in the act of fixing it.

### (superseded) Phase 1 via `--store-root`
```bash
SB=data/agentic/analysis/oncotree_review/sandbox
mkdir -p $SB && cp -R data/agentic/masters/eligibility/current_version $SB/current_version

caffeinate -i /opt/anaconda3/envs/trial_curator/bin/python -m aus_trial_universe.run \
  --map-only  --store-root $SB --workers 500 --max-concurrency 500 --no-cache-prune
caffeinate -i /opt/anaconda3/envs/trial_curator/bin/python -m aus_trial_universe.run \
  --reconcile --store-root $SB --workers 200 --max-concurrency 400

make oncotree-review          # compares production (current) vs $SB (updated)
```
`--store-root` redirects reads **and** writes, including the joined views, so production cannot be touched.
Scale reference: the last full Step-1 build covered 5,879 distinct values across 3 columns at 500/500 (~92 % TPM,
0 rate-limit errors); here ~4,853 cancer_type values are live and the other ~1,026 are cache hits.
→ **You review the comparison file again** — now with the LLM-level corrections — and sign off the prompts.

**PHASE 1 RESULT (2026-08-03, final run — 11.0 min, 400/400, 0 failures, 0 rate-limit errors):**

| | before | after |
|---|---:|---:|
| distinct code expressions | 1,185 | **573** |
| map rows clean | 419-equivalent | **4,968 / 4,971** |
| rows with an `error` | — | **0** |
| `oncotree_name` ≠ its code | 46 | **0** |
| canonical forms with >1 rendering (De Morgan duplicates) | 49 | **0** |
| map rows changed | — | 1,414 |

`r4_repaired = 0` — the mapper produced clean output first time for all 4,971 values, so the per-value repairer
never fired. `adjudication:match = 1`: the PCM ruling was reached UNAIDED, not overridden. 3 warn-only rows remain
(2 × the add-back idiom, 1 × a broad-term-plus-subtype exclusion list). 38 values ended `faithful=False` — the
refine loop did not fully converge, but their output still validates clean.

Two detector bugs were found by inspecting the output and fixed:
- **`log_or_branch_excluded` was a false positive.** `(Solid tumour AND NOT(BRAIN)) OR GB` is the legitimate
  add-back idiom, (S \ B) ∪ GB. Flagging it as an error made the repairer rewrite a correct expression into a
  30-code organ enumeration. Downgraded to warn: visible, never auto-repaired.
- **`SMAHN AND MDSEB` is not an unsatisfiable AND.** Systemic mastocytosis with an associated haematologic
  neoplasm is a WHO entity DEFINED by co-occurrence, and OncoTree has the composite node. New
  `log_composite_entity_split` check + a mapper rule; all 6 SM-AHN values now map to `SMAHN` alone.

**PHASE 1 FINAL (2026-08-03, after 3 review→modify→review iterations). AWAITING YOUR REVIEW.**

| | current store | updated |
|---|---:|---:|
| distinct code expressions | 1,185 | **579** |
| rows with any defect (error/warn) | many | **0 / 4,971** |
| `oncotree_name` ≠ its code | 46 | **0** |
| De Morgan / duplicate renderings | 49 | **0** |
| nested `NOT()` | 16 | 3 (all whitelisted) |
| ≥4-code OR enumerations | 226 | 122 |
| `NOT()` clauses | 1,428 | 432 |
| rows changed | — | 1,427 (4,600 arm-uses) |

**What each iteration found and fixed** — every change a general rule, never a patch for its example:
| iter | found | fixed by |
|---|---|---|
| 1 | `MIXED`/`OTHER` catch-all nodes used as tumour types; SM-AHN composite split | **programmatic** (closed 2-node ban + composite reduction) — verified first that every OTHER generic-sounding node (MPAL*, MGCT, MFH, CUP, MXOV) was legitimate |
| 1 | `PBT`→`BRAIN`, `ASTR`→`DIFG` under-granularity | **prompt**: restored the under-/over-granular FAILURE DIRECTIONS the condensed reviewer had lost |
| 1 | "MTC syndrome spectrum" → `Solid tumour` | **prompt**: bounded D10 — a group with no node of its own may not escalate to a sentinel |
| 1 | `NOT(HER2+ breast adenoca OR gastric adenoca)` dropped whole | **prompt**: inexpressible-exclusion rule applies PER TERM |
| 2 | 61 values oscillating (reviewer alternately "over-" then "under-granular") | **prompt**: precedence — an ancestor is correct when every finer node would infer an unstated attribute; + mirrored the doer's named conventions into the reviewer |
| 3 | ~6% of values move run-to-run | **measured and surfaced**, not "fixed" — new `stable_across_runs` column |

**Rejected on evidence** (recorded so it is not re-tried): grouping on the positive scope only, to unite the
germ-cell family — measured over the corpus it took divergent groups from 4 to **29**. It over-merges.

**Verified correct, deliberately unchanged:** the 4 divergent groups R6 leaves are real distinctions
(ASTR3/ASTR4, LGGNOS/HGGNOS, ASTR2/ASTR3 — `canonical_key` strips "grade N" so they collide as candidates and R6
correctly refuses to merge them) · the LBCL and MPAL enumerations are precision gains, since neither group has a
node of its own · CNS handling now splits correctly, `NOT(primary brain/CNS tumour)` → `NOT(PBT)` while "known
brain metastases" is dropped.

**Residual, needing YOUR decision — 1 item.** Two arms of one germ-cell trial, both stating "any primary site",
map to a 3-code and a 6-code form (16 arm-uses). Two of the three renderings already handle it correctly, so a
mapper rule would be a near-no-op that distorts everything else; this is what the adjudications register is for,
and no ruling has been invented.

### Phase 2 — PRODUCTION SWAP. ✅ EXECUTED 2026-08-04.

**Outcome:** store == the approved artifact on all 4,971 rows · export 17,830 rows / 572 distinct codes /
**0 error defects, 0 warnings, 0 name-code mismatches** · gates **WARN (0 FAIL)** with the new
`oncotree_expressions` gate PASSing · 252 tests green · every non-OncoTree master byte-identical.

**Stage C proved the code swap is faithful before any data moved:** the real `run.py --map-only` / `--reconcile`
entry points reproduced **4,963 of 4,971 values exactly (99.84 %)**, and all 8 divergences sit on rows with
**0 arm-uses**, so none reaches the export. A 30-value stratified check through the production path ran with
**0 live API calls** — only possible if the prompts, schema, reviewer input and refine-loop revision text are all
byte-identical to the approved run.

**Five wiring defects the unit tests could not have caught** (found by testing the live connection, at the user's
prompting):
1. the refine loop still read `prior.oncotree_name` — would crash on any value needing a 2nd attempt;
2. `_ONCOTREE_REPAIR_RULES` was never spliced — `build_oncotree_repairer` would `NameError` on first use;
3. **the class docstring is hashed into the response-cache key** (pydantic folds it into the JSON schema), so an
   improved docstring silently orphaned every cached decision — `schema.py` now carries a warning saying so;
4. the new gate was scoped to a hardcoded store path, so it graded the live store while every other gate graded
   the test fixture — a gate that lies. Re-scoped to the export, which is what actually ships;
5. `prompt_registry.py` did not list the new agents, and `cache_prune` deletes any unregistered (agent, prompt)
   pair as stale. Also fixed a PRE-EXISTING instance of the same bug: the two `approval_biomarker_split*` agents
   were unregistered, so a prune would have binned 497 live entries. Prune now reports **stale=0**.



**The governing risk, and why we do NOT re-derive.** 291 values (5.9 %) land differently on a cache-cold run —
these prompts have no temperature or seed, so a fresh production run would NOT reproduce the artifact that was
approved. **The approved TSV is therefore the source of truth for the DATA; the code swap governs FUTURE runs.**
Re-deriving would silently discard the review.

The 198 MB sandbox cache (`transient/oncotree_review_cache/`) is the reproducibility anchor — every approved value
is a cache hit in it. **Do not delete it until Phase 2 is complete and snapshotted.**

#### Stage A — snapshot and freeze (no changes)
```bash
D=data/backups/pre_oncotree_fix_$(date +%Y%m%d)
mkdir -p $D && cp -R data/agentic/masters $D/ && cp -R data/agentic/derived $D/
cp data/agentic/analysis/oncotree_review/finalised_cancer_type_map_review.tsv $D/APPROVED_2026-08-04.tsv
cd $D && find . -type f -name '*.tsv' | sort | xargs md5 > MD5SUMS.txt
```
The approved TSV is frozen INTO the backup as the sign-off record.

#### Stage B — code swap (no data change)
| move | note |
|---|---|
| `oncotree_review/{expr,checks}.py` → `tasks/eligibility/tools/` (private) + merge `updated/oncotree.py` into `tools/oncotree.py` | `invalid_codes` deleted |
| `updated/schema.py` → `mapping/schema.py` | `OncotreeMapping` loses `oncotree_name` |
| `updated/agents.py` → `mapping/agents.py` | |
| `updated/reconcile.py` → `mapping/reconcile.py` | `repair_oncotree_code` / `normalize_or_order` deleted |

Follow-on edits (each is forced by the above, none optional):
- `mapping/workflow.py` — `check()` uses `expression_problems`; `syn_nested_not` non-blocking at Step 1;
  `OncotreeResult.oncotree_name` derived; `_oncotree_logic_problems` deleted.
  ⚠ **`_review_input` must be made BYTE-IDENTICAL to the sandbox's** (drop the `PROPOSED oncotree_name:` line) —
  the reviewer input is hashed into the cache key, so any difference makes the sandbox cache unusable and Stage C
  impossible.
- ⚠ **`run.py --max-attempts` defaults to 6; the approved run used 4.** Must be pinned to 4 for Stage C, or the
  refine loop takes extra attempts on hard values and diverges.
- `store.py` / `schema.py` — `oncotree_name` derived on write · `run.py` — 2 `CancerTypeMap(...)` sites ·
  `export.py` + `map_approvals.py` — import `render_name_expression` · `qa/validate_output.py` — **repoint off
  the deleted `invalid_codes` / `_oncotree_logic_problems`** (its flag count will drop sharply; that is expected).
- `qa/gates.py` — new `oncotree_expressions` gate: any `error`-severity finding in the store fails the run.
- Tests: `tests/agentic/oncotree_review/` → `tests/agentic/tasks/eligibility/`; rewrite the assertions in
  `test_mapping_oncotree.py` / `test_reconcile.py` that call the deleted functions. Delete `oncotree_review/`.
- **Gate: `make agentic-tests` green.**

#### Stage C — prove the production path reproduces the approved data (still no store writes)
**This is the stage that matters.** The sandbox drove the agents through its own runner; production goes
`run.py → map_all_columns → map_oncotree`, a DIFFERENT orchestration that has never been exercised with these
prompts.
```bash
cp -R data/agentic/transient/oncotree_review_cache/* data/agentic/transient/cache/   # transplant the anchor
SB=/tmp/oncotree_verify && mkdir -p $SB && cp -R data/agentic/masters/eligibility/current_version $SB/
python -m aus_trial_universe.run --map-only  --store-root $SB --max-attempts 4 --workers 380 --max-concurrency 380
python -m aus_trial_universe.run --reconcile --store-root $SB --max-attempts 4 --workers 200 --max-concurrency 400
```
Diff `$SB/current_version/finalised_cancer_type_map.tsv` against the approved TSV.
**If any row differs, STOP** — reconcile the difference before touching the store. A clean diff proves the code
swap and the approved data are the same thing.

#### Stage D — data swap
1. Write the approved `oncotree_code_FINAL` into `masters/eligibility/current_version/finalised_cancer_type_map.tsv`,
   with `oncotree_name` DERIVED from it, and the Step-1 `oncotree_code` taken from the same verified run so the
   table is internally coherent (Step-1 faithful, FINAL cleaned).
2. **Byte-verify nothing else moved:** `arm_eligibility_raw`, `interpreted_eligibility`, `arm_scope`,
   `gene_alteration_map`, `molecular_signature_map` + their finalised twins, `trial_arms`, `trial_info` and the
   6 core drug tables must be md5-identical to Stage A.
3. Regenerate `joined/eligibility/finalised_mapped_eligibility.tsv`.
4. `make drug-ref-map-approvals` — **the symmetric side must move with the trial side**, or drug approvals stay on
   old codes and the match breaks.
5. `make agentic-export` · `make agentic-gates`.

#### Stage E — verify and re-snapshot
- `make oncotree-review` must now report **0 changed rows** (the store equals the approved values).
- Snapshot to `data/backups/known_good_$(date +%Y%m%d)_post_oncotree/` with `MD5SUMS.txt`.
- `make agentic-cache-prune --apply`; only then delete `transient/oncotree_review_cache/`.

**Rollback at any point:** restore `masters/` + `derived/` from the Stage-A snapshot (`RECOVERY.md` has the
copy-back commands); the code is in git and uncommitted work is in the working tree.

**Not in scope, tracked separately:** `src_qualifier_in_interpreted` (the interpreted cell still carries
inexpressible clinical text) is an EXTRACTION defect and needs an extraction re-run, not a mapping change.

## 9. Decisions — settled 2026-08-03

1. ✅ **Vacuous exclusions: DROP — in RECONCILIATION, not the mapper.** The mapper translates faithfully; the
   cleanup is the next step. D7 withdrawn; implemented as E6.
2. ✅ **Nested `NOT()` type (a) — the "unless" clause: drop the exclusion.** 6 values / 12 map rows / 48 arm-uses,
   all one trial family, all `UCEC minus UEC`. `HGSOC AND NOT(UCEC AND NOT(UEC))` → `HGSOC`.
3. ✅ Comparison file column set (§1) accepted.
4. ✅ `src_qualifier_in_interpreted` tracked separately as an extraction item.
5. ✅ Updated modules live in `oncotree_review/updated/` during testing (§7).

**✅ SETTLED 2026-08-04 — the nested-`NOT()` whitelist stays at TWO entries and is not extended.**
`SKIN AND NOT(MEL)` and `NSCLC AND NOT(LUSC)` only. Every other nested negation — including
`BRAIN AND NOT(GB)` — is FLATTENED to the engine form by the deterministic rewrite
(`expr.flatten_nested_nots`: De Morgan → distribute → absorb). The mapper writes the faithful transcription and
the refinement converts it; verified in the final output, where the only surviving nested negations are the three
rows carrying the whitelisted `Pan-cancer AND NOT(SKIN AND NOT(MEL))`.

*(historical — the question that this settles)* **nested `NOT()` type (b), the genuine named set difference.** Enumerated in
`data/agentic/analysis/oncotree_review/nested_not_cases.tsv`. **2 contested values / 2 map rows / 6 arm-uses:**

| value | inner difference | source wording | arms |
|---|---|---|---:|
| `Pan-cancer AND NOT(SKIN AND NOT(MEL))` | SKIN minus MEL | "non-melanomatous skin cancer" | 5 |
| `Solid tumour AND NOT((NSCLC AND NOT(LUSC)) OR …)` | NSCLC minus LUSC | "NSCLC (non-squamous)" | 1 |

Two of the original four fall out on re-examination and are **not** part of this decision:
- `NOT(Pan-cancer AND NOT(SKIN AND NOT(MEL)))` — same idiom, but a prior-malignancy exclusion; removed by D2/D3.
- **`PCM AND NOT(MDS OR BLL OR (MBN AND NOT(PCM)))` → `PCM` (re-adjudicated 2026-08-03).** Not a set-difference
  case at all. All three source exclusions are non-tumour-type ("ongoing MDS or B-cell malignancy **other than**
  MM"; "recurrent malignancy **other than** MM"; "CNS involvement **of** MM") — second-malignancy and
  disease-site exclusions, so D1 drops them; `MDS`/`BLL` are disjoint from `PCM` so E6 would strip them anyway;
  **`MBN` is `PCM`'s own OncoTree parent**, so the expression excludes the ancestor of the type it includes and
  is safe only if an engine resolves the nested double negation exactly as intended; and `BLL` is unsupported by
  the source ("B-cell malignancy" — `BLL` is a precursor neoplasm, not under `MBN`). Four independent rules
  converge on plain `PCM` / `Plasma Cell Myeloma`.

**The decisive context: the SAME construct occurs 155 distinct values / 332 map rows / 1,237 arm-uses at the TOP
level** (`X AND NOT(Y)` where Y is a subtype of X — `NSCLC AND NOT(LUSC)`, `BLCA AND NOT(UCU OR UTUC)`, …), also in
that file. So the matching engine must handle set-difference semantics anyway, at ~137× the volume of the nested
form. Nesting is the same construct one level deeper in 3 values. If the engine handles the 1,237, allowing the 8
is a small increment; if it does not, the 1,237 are already broken and the nested 8 are a rounding error.
Blocks D4 only — everything else can be built now.


---

## 10. Doing this for another column (the playbook)

`gene_alteration` is next (handover §B1b). What transfers, and what has to be built per column:

| | reusable as-is | per-column |
|---|---|---|
| defect **catalogue** shape (id → layer, severity, description) | pattern | the ids themselves |
| **parser / grammar validator** | — | OncoTree has `oncotree_expr`; finding-model already has `finding_model_problems` |
| **canonical form** (sort, dedupe, factor negation, idempotent) | pattern + tests | the rewrite rules |
| **refinement R0–R8** in `mapping/reconcile.py` | ✔ already branches on column | wire the non-oncotree path |
| **review sandbox** (one comparison file, per-value audit log, `stable_across_runs`) | ✔ | — |
| **population-level review harness** (class-level checks, not hand-picked examples) | ✔ | the shape rules |
| **Phase 0 → C → D → E migration plan** | ✔ | — |

**Lessons that cost iterations — do not relearn them:**
1. **Condensing a prompt deletes rules.** Four regressions came from trimming the reviewer's counter-examples and
   its named failure directions. Restore the general statement; do not patch back the one example that surfaced.
2. **Sharpening a reviewer without a tie-breaker causes oscillation.** Adding under-/over-granular checks made 61
   values flip forever between two codes. Any pair of opposing checks needs an explicit precedence rule.
3. **Prefer a deterministic fix to a prompt fix** when the rule is mechanical — it is testable and cannot regress
   elsewhere. Reserve prompts for judgements that need the source text.
4. **Verify a rule change against the whole corpus**, never against its motivating value.
5. **A class docstring and Field descriptions are hashed into the response-cache key.**
6. **Register every new agent** in `prompt_registry.py`, or `cache_prune` deletes its cache as stale.
7. **Test the live connection, not just the imports** — 6 of the migration's defects were invisible to a green
   unit-test suite.
