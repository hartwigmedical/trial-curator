"""THE PROMPT-CHANGE HARNESS — the standing tooling for changing an LLM prompt safely (handover item B5).

Promoted out of the throwaway `qa/mapping_review/` staging package on 2026-08-06, once the OncoTree stage-1 prompt
change it was built for had shipped. The two staging-only modules are gone: the candidate prompt is folded into
`mapping/cancer_type/agents.py` (a copy is kept at
`data/agentic/analysis/cancer_type_stage1_review/candidate_prompts_as_folded.py` purely as provenance), and the
remap driver was specific to that one run.

    compare_runs.py       THE GATE — defect counts and per-value regressions against BOTH baselines
    export_comparison.py  the review deliverable — one row per value, the runs side by side
    judgements.py         my recorded per-value verdicts, so a review is auditable rather than narrated

WHY IT EXISTS. A prompt change re-fingerprints its agent, prunes the response cache and re-rolls every value in the
column, so it cannot be evaluated by editing the prompt and glancing at the result. B1 shipped nine regressions
precisely because its review artifact reported defects FIXED and REMAINING but never quality LOST.

THE RULES IT ENFORCES (full statement in memory `feedback-prompt-change-regression-method`):
  · a rule belongs in a prompt only if it is a PRINCIPLE a domain expert would endorse without seeing our error
    list; anything that only works as "value X -> answer Y" belongs in `qa/adjudications/`
  · measure on the values NOT tuned against, and grade prompt-only separately from prompt+register
  · **zero regressions against the OLDEST reviewed baseline is a HARD gate** — 28 July here. On its first outing it
    caught a value 28 July mapped correctly, B1 broke, and the new run reproduced: invisible against 4 August alone
  · a defect count cannot see the worst regression class (a value staying deterministically clean while losing
    specificity), so clean-but-changed values are classified by DIRECTION as well

ONE KNOWN LIMITATION, learned the hard way on 2026-08-06: **the refine loop is not reproducible from cache.** A
byte-identical prompt does NOT guarantee identical output, because one missed cache entry mid-chain diverges every
call after it — 179 of 4,978 values differed on a re-run of an unchanged prompt. So a reviewed artifact must be
APPLIED to the store, never re-derived by re-running and hoping it matches.
"""
