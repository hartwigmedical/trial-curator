"""Drug reference subsystem (spec §6.1 — the deferred `drug_ref` dimension).

Three 3NF tables, built once per unique drug and reused across the whole universe:
  drug_alias       raw_name (as written) -> canonical_id
  drug_ref         canonical_id -> intrinsic facts (class, modality, mechanism, ATC, FDA/EMA)
  drug_indication  (canonical_id, indication) -> TGA/PBS approval (indication-specific, OncoTree-keyed)

Persisted as a datestamped versioned resource (data/agentic/resources/drug_ref/version_<ddmmyyyy>/);
incremental — an existing (non-stale) canonical is a pure lookup. See schema.py / store.py.
"""
