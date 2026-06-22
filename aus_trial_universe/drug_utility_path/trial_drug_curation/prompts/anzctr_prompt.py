from aus_trial_universe.drug_utility_path.trial_drug_curation.enrichment.pbs import PBS_INDICATION_PROMPT
from aus_trial_universe.drug_utility_path.trial_drug_curation.enrichment.pottr import POTTR_CLASS_PROMPT
from aus_trial_universe.drug_utility_path.trial_drug_curation.enrichment.tga import TGA_STATUS_PROMPT
from aus_trial_universe.drug_utility_path.trial_drug_curation.prompts.base_prompt import (
    CORE_EXTRACTION_PROMPT,
)


ANZCTR_SOURCE_PROMPT = """
ANZCTR-specific rules:
- trialId: ANZCTR ACTRN from the `TRIAL` sheet `ACTRN` column. Use `TRIAL ID`
  only as the workbook join key across sheets, or as a fallback if ACTRN is
  missing.
- Prefer the supplied ANZCTR workbook for trial-specific drug names, regimens,
  arms, dosage, and cancer type context.
- Drug names may be extracted from `STUDY TITLE`, `SCIENTIFIC TITLE`,
  `TRIAL ACRONYM`, `INTERVENTIONS`, `COMPARATOR`, `CONTROL`,
  `INCLUSIVE CRITERIA`, `EXCLUSIVE CRITERIA`, `BRIEF SUMMARY`, and
  `PUBLIC NOTES`.
- Build drugRegime primarily from `INTERVENTIONS`, `COMPARATOR`, and `CONTROL`.
  Use titles only when they explicitly state a regimen or combination.
- trialArm: Use `Experimental` for drugs administered in `INTERVENTIONS`; use
  `Control` for drugs administered in `COMPARATOR` or `CONTROL`; use `Neither`
  for drugs only mentioned as prior therapy, prohibited therapy, background
  therapy, eligibility criteria, disease history, or other non-intervention
  context.
- drugEvidenceSource: Use `studyTitle`, `scientificTitle`, `trialAcronym`,
  `interventions`, `comparator`, `control`, `inclusionCriteria`,
  `exclusionCriteria`, `briefSummary`, `publicNotes`, `healthCondition`, and/or
  `conditionCode`.
- cancerTypes: Prefer `HEALTH CONDITION` and `CONDITION CODE` sheet values. Use
  title, eligibility, and summary text where needed for more precise cancer
  context.
- dosage: Prefer dosage, route, schedule, cycle, duration, and
  treatment-continuation details from `INTERVENTIONS`, `COMPARATOR`, and
  `CONTROL`.
- Use `INTERVENTION CODE = Treatment: Drugs` to confirm drug-treatment trials,
  but do not treat the intervention code itself as a drug name.
- Preserve trial-provided drug names in `drugName`; put normalized ingredient
  names only in `standardisedName`.
""".strip()


ANZCTR_DRUG_CURATION_PROMPT = "\n\n".join(
    (
        CORE_EXTRACTION_PROMPT,
        ANZCTR_SOURCE_PROMPT,
        POTTR_CLASS_PROMPT,
        TGA_STATUS_PROMPT,
        PBS_INDICATION_PROMPT,
    )
)

ANZCTR_PROMPT = ANZCTR_DRUG_CURATION_PROMPT
