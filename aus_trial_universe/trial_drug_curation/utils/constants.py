from pathlib import Path

CTGOV_TRIALS_DIR = Path("data/ctgov/trials")
CTGOV_INPUT_FILENAME = "ctgov_input.json"
ANZCTR_TRIALS_DIR = Path("data/anzctr/trials")
ANZCTR_INPUT_FILENAME = "anzctr_input.xlsx"

DEFAULT_ONCOTREE_YAML = "data/eligibility_resources/oncotree/oncotree.yaml"
DEFAULT_OUTPUT_DIR = Path("data/trial_drug_curation")
DEFAULT_OUTPUT_PREFIX = "trial_drug_curation"
SKIPPED_OUTPUT_PREFIX = "skipped_trials"

SUPPORTED_REGISTRIES = ("ctgov", "anzctr")
REGISTRY_LABELS = {"ctgov": "CTGOV", "anzctr": "ANZCTR"}

MAX_TEXT_FIELD_CHARS = 12000

CTGOV_PROTOCOL_MODULES = (
    "identificationModule",
    "conditionsModule",
    "armsInterventionsModule",
    "eligibilityModule",
    "descriptionModule",
    "designModule",
)

ANZCTR_TRIAL_COLUMNS = (
    "TRIAL ID",
    "ACTRN",
    "STUDY TITLE",
    "SCIENTIFIC TITLE",
    "TRIAL ACRONYM",
    "INTERVENTIONS",
    "COMPARATOR",
    "CONTROL",
    "INCLUSIVE CRITERIA",
    "EXCLUSIVE CRITERIA",
    "STUDY TYPE",
    "PURPOSE",
    "ALLOCATION",
    "MASKING",
    "ASSIGNMENT",
    "ENDPOINT",
    "PHASE",
    "BRIEF SUMMARY",
    "PUBLIC NOTES",
)

