# Eligibility Path — Analysis

Ad-hoc analyses over the eligibility-path outputs. Currently: the **POTTR ↔ Hartwig eligibility
comparison** (`pottr_comparison.py`), which reconciles the curated eligibility criteria in the Hartwig
final trial resource against the POTTR AU eligibility file, per trial and per criterion.

## Running the comparison

From the repo root (with the `trial_curator` conda env active, or otherwise set `PYTHON_BIN` to a
pandas-enabled interpreter — the bare repo `python` lacks pandas):

```bash
make eligibility-path-pottr-comparison
```

This is a read-only analysis over the newest existing outputs, so it skips the preflight tests and the
resource accrete/report steps. To do a full refresh and then the comparison in one go, chain the targets
(`make` runs them in order and stops if the download fails):

```bash
make eligibility-path-run-all-trials-download eligibility-path-pottr-comparison
```

Or run the module directly:

```bash
~/anaconda3/bin/python aus_trial_universe/eligibility_path/analysis/pottr_comparison.py
```

Re-run after a pipeline run regenerates the final resource, or after editing the conditions resource.
It prints per-column verdict counts and writes the output TSV.

### Inputs (auto-selected by the newest ddmmyyyy date in the filename — no dates to edit)
- **Hartwig resource** — newest `data/eligibility_path/exports/final/eligibility_trial_resource_*.tsv`
- **POTTR snapshot** — newest `data/eligibility_path/analysis/pottr_trial_eligibility.AU_snapshot_*.tsv`,
  a verbatim copy of POTTR's `trial_eligibility.AU.tsv` refreshed by the fresh-download workflow (see below)
- **CTGov POTTR-id aliases** — newest `data/trial_inputs/ctgov/input_trials/version_*/02b_pottr_id_aliases_ctgov.tsv`
  (lets an aliased POTTR NCT id resolve to the id the resource actually carries)
- **OncoTree** — `data/eligibility_path/resources/oncotree/oncotree_expanded/oncotree_expanded.csv`

The final resource advances every pipeline run; the POTTR snapshot and alias table advance only on a
fresh-**download** run (`make eligibility-path-run-all-trials-download[-w-llm]`) — `run-all` reprocesses
without touching them, so it stays drift-free. Each is picked by the newest parsed `ddmmyyyy` date (not
mtime, which a re-touched older file would fool; not text order, since `ddmmyyyy` doesn't sort as text),
matching `version_dir_sort_key` / `final_resource_diff` elsewhere.

### Output
`data/eligibility_path/analysis/eligibility_vs_pottr_comparison_<resource-date>.tsv` — 252 trials
(every POTTR-eligibility trial), **24 columns with a two-row header**. Read it with `skiprows=1` in
pandas so row 2 (the column names) is the header; row 1 is the criterion-group band.

## Column layout

Row 1 groups the columns below it; each criterion group is `[hartwig cols] + pottr_<criterion> + [comparison col(s)]`.

| Group | Columns |
|---|---|
| `trial_info` | trialId, registry, pottr_criteria_count, pottr_eligibility_criteria |
| `cancer_type` | hartwig_original_health_conditions, hartwig_cancer_type_inclusive/exclusive, pottr_cancer_type, cancer_type_inclusive/exclusive_comparison |
| `gene_alteration` | hartwig_gene_alteration_inclusive/exclusive, pottr_gene_alteration, gene_inclusive/exclusive_comparison, variant_alteration_inclusive/exclusive_comparison |
| `molecular_signature` | hartwig_molecular_signature_inclusive/exclusive, pottr_molecular_signature, molecular_signature_inclusive/exclusive_comparison |
| `prior_therapy` / `expression_ihc` | pottr_prior_therapy / pottr_expression_ihc (POTTR-only dims — display only; no comparison, since Hartwig models neither) |

`pottr_*` columns render POTTR's parsed constituents for that criterion, `NOT:`-prefixed for exclusions.

## Verdict vocabulary

The comparison is **POTTR-centric**: if POTTR is silent for a criterion+polarity, the cell is blank.

- **blank** — POTTR has no curation for that criterion + polarity.
- **`identical`** — sets reconcile (hierarchy/generic-aware). Cancer tags provenance: `identical (text match)`
  (free-text of the cancer names matched) or `identical (oncotree match)` (reconciled via OncoTree).
- **`pottr_subset` / `pottr_superset`** — one side's set is contained in the other's (with `[pottr: …]` detail).
- **`diff - pottr missing: <X>;\n diff - hartwig missing: <Y>`** — both sides have uncovered elements
  (each side on its own line).
- **`hartwig-missing: <pottr items>`** — POTTR has it, Hartwig has nothing (cancer / molecular_signature).
  Gene/variant/signature emit **`no hartwig curation`** instead when Hartwig has none of that polarity.

### Reconciliation rules
- **Cancer type** — OncoTree hierarchy: `Pan-cancer` (root `Cancer`) covers all; `Solid tumour` and
  `Haematological malignancy` are its two children (all solid / all heme). Codes display via `disp_code()`
  (root shows as `Pan-cancer`).
- **Cancer free-text short-circuit** — if the normalised POTTR `catype:` free-text set equals the normalised
  Hartwig original-condition set, output `identical (text match)` (handles same-cancer/different-code-granularity,
  e.g. `Mesothelioma`→PLMESO vs PEMESO|PLMESO, `Neuroendocrine tumour`≈`Neuroendocrine Tumors`).
- **cancer_type_exclusive — three categories** (`cancer_excl_verdict`): `identical (oncotree match)`;
  `coverage too broad - hartwig <broadest ancestor> missing NOT(<pe>)` (POTTR excludes a strict descendant of a
  broader Hartwig-included umbrella, no competing exclusion); `wrong curation - …` (any hard mismatch: same-
  granularity conflict, ancestor-of / out-of-scope, or an unmatched extra Hartwig exclusion). Multi-note verdicts
  use one category prefix with notes joined by `;\n`.
- **Gene / variant** — generic ⊃ specific (POTTR `KRAS:oncogenic_mutation` covers Hartwig `KRAS:G12C`);
  gene-family aliases `IDH`→IDH1/2, `BRCA`→BRCA1/2. Hartwig's `SmallVariant` syntax is normalised to POTTR's
  descriptor vocabulary: `affectedExon=N & effects=INFRAME_INSERTION|DELETION` → `exon_N_insertion` /
  `exon_N_deletion`; `affectedExon` alone → `exon_N`; `effects` alone → `inframe_insertion`/`inframe_deletion`;
  `hgvsProteinImpact=p.X` → the protein change. Coverage relationships (directional — the broader form
  covers the specific, not vice versa):
  - **codon wildcard** covers specific substitutions at that codon: Hartwig `V600X` (X = any) or a POTTR
    bare codon `R132`/`G12`/`V600` covers `V600E` / `R132C` etc.;
  - **exon-generic** covers exon-specific: `exon_20` covers `exon_20_insertion`/`exon_20_deletion`;
  - **gene-wide inframe** covers the exon-scoped indel: `inframe_insertion` covers `exon_20_insertion`.

  `variant_alteration` is compared **only when the gene-level verdict is `identical`** (else blank — a
  gene-level diff makes specific-variant detail moot).
- **Molecular signature** — MSI/MSS are complementary states of one axis (`NOT MSI == MSS`), folded onto the
  inclusive side. `POTTR ⊆ hartwig` (exact match within Hartwig's set) is reported as `identical`.
- **Polarity** — POTTR non-`NOT` → inclusive, `NOT …` → exclusive. Hartwig uses the literal column, **except
  `Wildtype[X]`**, routed to the exclusive side so it matches POTTR `NOT GENE:oncogenic_mutation`.

## Cancer-type curation fixes (companion workflow)

Wrong-looking Hartwig cancer types are fixed as **data, not logic**: edit the newest
`data/eligibility_path/resources/cancer_type/ConditionsCurationResource_<ddmmyyyy>.xlsx` (auto-selected by
newest mtime), then re-run the pipeline + this comparison.

- Column `Conditions_lookup` → `Oncotree_curation`, format `Name (CODE)`, multi `A (X) | B (Y)`; lookup is
  case-insensitive + whitespace-stripped; unmapped strings are dropped.
- **Spurious `Pan-cancer`** comes from a condition string that's unmapped (falls back to `Pan-cancer`) or from a
  bare molecular-marker string mapped to `Pan-cancer` that collapses a co-occurring real tumour type. Fixes: add
  the missing tumour string → its code; blank a molecular-marker string; map a generic-solid string → `Solid tumour`
  (heme → `Haematological malignancy`). Trace via
  `data/eligibility_path/exports/intermediates/{anzctr,ctgov}/cancer_type/01_*` (conditions),
  `03_row_level_cancer_type.tsv` (per-row), `04a_*` (trial level).
- **openpyxl gotcha:** blank a cell with `ws.cell(r, c).value = None` — `ws.cell(r, c, None)` is a silent no-op.
