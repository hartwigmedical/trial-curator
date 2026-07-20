"""Drug reference subsystem (spec §6.1 — the deferred `drug_annotations_core` dimension).

Five 3NF tables, built once per unique drug and reused across the whole universe:
  intervention_to_canonical  input_intervention_name (as written) -> canonical_id (+ raw_name_to_map); 1 input -> N
  trial_to_intervention      (trialId, registry) -> input_intervention_name (which trials used each name; provenance)
  drug_annotations_core                   canonical_id -> intrinsic facts (class, modality, targets, ATC, POTTR, FDA/EMA)
  drug_target_actions                (canonical_id, target, action) -> the mechanism as (target, action) pairs
  drug_regulatory_approvals            (canonical_id, indication) -> TGA/PBS approval (indication-specific)

Persisted as a datestamped versioned resource (data/agentic/resources/drug_ref/version_<ddmmyyyy>/);
incremental — an existing (non-stale) canonical is a pure lookup. See schema.py / store.py.
"""
