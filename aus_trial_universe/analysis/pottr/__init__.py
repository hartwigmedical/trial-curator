"""POTTR cross-check + the disease-derived alteration inference it inspired.

TWO COMPONENTS, deliberately in one workspace because the second is a prerequisite of the first:

**Component 2 — `disease_inference.py` (built FIRST).** POTTR asserts genetics that the trial's *disease* implies:
`NCT04924075`'s "von Hippel-Lindau (VHL) disease associated tumors" carries `VHL:oncogenic_mutation`, and its
"wild-type GIST" cohort carries `NOT KIT:oncogenic_mutation`. Our `gene_alteration` column has neither, because
the genetics live in the cancer-type wording. This module recovers them into a NEW derived column. It has to run
first: without it the component-1 gene comparison would score us as missing criteria we simply record elsewhere.

**Component 1 — `pottr_source.py` -> `crosswalk.py` -> `compare.py`.** Parse POTTR's DSL, map its terms into our
controlled vocabularies, and compare the two curations over the 269 shared trials.

⚠ POTTR IS A PEER, NOT GROUND TRUTH. Its `NCT04924075` wild-type-GIST row names **PDGFRB**, where the entity is
defined by KIT and **PDGFRA** wild-type. We do our own research and adjudicate against the registry source text,
never against POTTR's answer.
"""
