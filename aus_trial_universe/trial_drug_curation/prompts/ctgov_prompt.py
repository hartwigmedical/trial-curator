from aus_trial_universe.trial_drug_curation.enrichment.oncotree import (
    ONCOTREE_MAPPING_PROMPT,
)
from aus_trial_universe.trial_drug_curation.enrichment.pbs import PBS_INDICATION_PROMPT
from aus_trial_universe.trial_drug_curation.enrichment.pottr import POTTR_CLASS_PROMPT
from aus_trial_universe.trial_drug_curation.enrichment.tga import TGA_STATUS_PROMPT
from aus_trial_universe.trial_drug_curation.prompts.base_prompt import (
    CORE_EXTRACTION_PROMPT,
)


CTGOV_SOURCE_PROMPT = """
CTGOV-specific rules:
- trialId: CTGOV NctId.
- Drug names may be extracted from CTGOV title, intervention names,
  intervention descriptions, arm descriptions, otherNames, and eligibility
  criteria.
- trialArm: Use CTGOV arm type where the drug is part of an arm. Use `Neither`
  for drugs only mentioned as prior therapy, background therapy, eligibility
  criteria, or other non-intervention context.
- drugEvidenceSource: Use `title`, `intervention`, `armDescription`,
  `eligibilityCriteria`, and/or `otherNames`.
- cancerTypes: Use CTGOV condition names and relevant cancer type context from
  arm/eligibility text where needed.
- Prefer the supplied local CTGOV input file for trial-specific drug names,
  regimens, arms, dosage, and cancer type context.
- For drugs mentioned only in eligibility/prior therapy and not administered as
  trial treatment, set `trialArm = Neither` and leave `drugRegime` blank unless a
  relevant regimen is explicitly stated.
""".strip()


CTGOV_DRUG_CURATION_PROMPT = "\n\n".join(
    (
        CORE_EXTRACTION_PROMPT,
        CTGOV_SOURCE_PROMPT,
        ONCOTREE_MAPPING_PROMPT,
        POTTR_CLASS_PROMPT,
        TGA_STATUS_PROMPT,
        PBS_INDICATION_PROMPT,
    )
)

CTGOV_PROMPT = CTGOV_DRUG_CURATION_PROMPT
