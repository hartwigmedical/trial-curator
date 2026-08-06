"""THE PROMPT-CHANGE HARNESS — the standing tooling for changing an LLM mapping prompt safely.

Column-generic: every entry point takes `--column`. Used to refine and ship all three vocabulary columns
(cancer_type, gene_alteration, molecular_signature) during 2026-08-05/06.

    columns.py         the per-column spec: baselines, key/value columns, defect signal, direction classifier
    remap.py           run a candidate prompt over the frozen corpus, IN ISOLATION (nothing production is touched)
    compare.py         THE GATE — defect counts and per-value regressions against every baseline
    review_judge.py    the LLM judge that supplies each verdict and its reason
    export_review.py   THE DELIVERABLE — one row per value, the runs side by side, for the user to sign off
    apply_reviewed.py  the guarded migration: apply the reviewed artifact to the store

WHY IT EXISTS. A prompt change re-fingerprints its agent, prunes the response cache and re-rolls every value in
the column, so it cannot be evaluated by editing the prompt and glancing at the result. B1 shipped nine
regressions precisely because its review artifact reported defects FIXED and REMAINING but never quality LOST.

THE RULES IT ENFORCES (full statement in memory `feedback-prompt-change-regression-method`):
  · a rule belongs in a prompt only if it is a PRINCIPLE a domain expert would endorse without seeing our error
    list; anything that only works as "value X -> answer Y" belongs in `qa/adjudications/`
  · measure on the values NOT tuned against, and grade prompt-only separately from prompt+register
  · **zero regressions against the OLDEST reviewed baseline is a HARD gate**
  · a defect count cannot see the worst regression class (a value staying clean while losing quality), so
    clean-but-changed values are classified by DIRECTION as well — and the dangerous direction is POLARITY-
    dependent: broadening an inclusion is a superset (fine), broadening an exclusion is an over-exclusion (not)
  · **the QUALITY verdict is an LLM judgement, never a string/pattern heuristic** (user, 2026-08-06). Only
    *which* values differ is deterministic, and that is exact: canonicalise both sides, compare parsed ASTs.

TWO KNOWN TRAPS, both learned the hard way:
  · **The refine loop is not reproducible from cache.** A byte-identical prompt does NOT guarantee identical
    output — 179 of 4,978 values differed on a re-run of an unchanged prompt. So a reviewed artifact must be
    APPLIED to the store, never re-derived by re-running and hoping it matches.
  · **Never build the review set by INTERSECTING version key-sets.** It silently drops values that postdate the
    oldest baseline. The universe is the CURRENT corpus.

The cancer_type review's 1,015 lines of recorded per-value hand judgements were retired to
`data/agentic/analysis/cancer_type_stage1_review/judgements_as_reviewed.py` — column-specific DATA for a
completed review, not machinery. `compare_runs.py` / `export_comparison.py` were deleted with them.
"""
