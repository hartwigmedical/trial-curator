"""Trial-universe ingestion (spec §6.1 — the INPUT layer under data/agentic/inputs/trial_universe/).

Full download of the raw registry records the agentic pipeline consumes:
  ctgov.py      ClinicalTrials.gov API v2 full download (status/country/drug/condition + POTTR broad-net + id-append)
  pottr_ids.py  POTTR-listed trial IDs / id-aliases + the manual removal list (self-contained; no eligibility_path)

Self-contained ports of the proven eligibility-path download logic (the legacy tree is being retired), owning their
own copies rather than importing it. Data lands under CTGOV_ROOT/current_version/ (superseded snapshots -> archive/).
"""
