from aus_trial_universe.trial_drug_curation.utils.schema import TSV_COLUMNS

CORE_OUTPUT_COLUMNS = TSV_COLUMNS


CORE_EXTRACTION_PROMPT = """
Extract all drug interventions and return a TSV table.

Data grain:
Return one row per unique: trialId x drugName x drugRegime x trialArm

Columns:
- trialId: Source trial identifier.
- drugName: The unique drug name as provided by the trial.
- drugRegime: Normalized drug combination/regimen that this drug is a member
  of, e.g. `GDC-6036 + Atezolizumab`.
- trialArm: `Experimental`, `Control`, or `Neither`.
- drugEvidenceSource: Source text section(s), joined with ` | ` when multiple
  sources support the row.
- cancerTypes: Trial cancer type(s).
- Oncotree: OncoTree code(s) or supported custom broad-cancer values.
- standardisedName: RxNorm-standardized ingredient name. If RxNorm has no
  match, use INN/USAN/registry aliases where available; otherwise leave blank.
- pottrDrugClass: Relevant POTTR drug class hierarchy elements for this drug.
- cancerDrug: Boolean. `True` if the drug is an anticancer therapeutic; `False`
  if auxiliary/supportive/non-oncology.
- dosage: Dosage information exactly as provided by the trial, preserving route,
  dose, schedule, and cycle details where available.
- TGAStatus: Drug-level Australian approval status.
- PBSIndicationStatus: PBS reimbursement status by cancer indication.

Rules:
- The first four columns (`trialId`, `drugName`, `drugRegime`, `trialArm`) must
  form a unique key.
- Use ` | ` to separate multiple values within a TSV cell.
- Use ` -> ` inside `pottrDrugClass` for hierarchy/path elements.
- Leave a field blank when it is not applicable or no confident value is
  available. Do not write `NA`.
- Preserve trial-provided drug names in `drugName`; put normalized ingredient
  names only in `standardisedName`.
""".strip()


BASE_PROMPT = CORE_EXTRACTION_PROMPT
