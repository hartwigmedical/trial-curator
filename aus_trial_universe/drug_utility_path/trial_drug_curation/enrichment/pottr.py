POTTR_DRUG_CLASS_HIERARCHY_URL = (
    "https://github.com/fpylin/POTTR/blob/master/data/drug_class_hierarchy.txt"
)
POTTR_DRUG_DATABASE_URL = (
    "https://github.com/fpylin/POTTR/blob/master/data/drug_database.txt"
)

POTTR_CLASS_PROMPT = """
POTTR drug class mapping:
- pottrDrugClass: All relevant POTTR drug class hierarchy elements for this
  drug, joined by ` ->`.
- Use only POTTR. If the drug is not in POTTR, leave blank.
- Prefer supplied local drug ontology/POTTR outputs where available; otherwise
  use the supplied POTTR source URLs for `pottrDrugClass`.
""".strip()
