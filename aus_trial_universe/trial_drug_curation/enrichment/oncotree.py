ONCOTREE_MAPPING_PROMPT = """
OncoTree mapping:
- Oncotree: OncoTree code(s) from the supplied OncoTree YAML file.
- For broad solid tumor / pan-cancer trials, use custom value `Solid-Tumor` or
  `Pan-cancer`.
- Prefer the supplied local OncoTree YAML file for OncoTree mapping.
""".strip()
