"""Known, accepted QA findings — waived so the gates fail on NEW problems rather than on a standing backlog.

A gate that fails every cycle for a known reason trains people to ignore it, which costs more than the finding it
reports. A waiver keeps the finding VISIBLE (the gates surface waived items as a WARN, never silently) while
letting a clean cycle report PASS.

Deliberately a code constant, not a data file: `data/` is gitignored, so a waiver here shows up in review and in
`git log` — adding one is a reviewable decision, not an invisible edit.

**Every waiver must name the follow-up.** These five are tracked in `docs/v2_agentic_handover.md` under
"EXTRACTION MISSES ON SPECIFIC ARMS" — to be fixed in the same dedicated prompt session as the OncoTree
De Morgan item, since both are extraction/mapping prompt work.
"""
from __future__ import annotations

# arm_scope verdicts of `unexplained` that are ACCEPTED for now. Confirmed genuine extraction misses (2026-07-29):
# a full cache-bypass re-extraction at the signed-off prompts left all five empty, so they are systematic rather
# than sampling flukes and cannot be repaired without a prompt change.
WAIVED_EMPTY_ARMS: dict[str, str] = {
    "NCT06400472::LY4170156 (Enrichment Cohort A3)":
        "raw stage captures only the Exclusion Criteria block for cohorts A3-A5; the 'Have one of the following "
        "solid tumor cancers' inclusion list never reaches them. Siblings A1/A2/A6/B1-B4 extract correctly.",
    "NCT06400472::LY4170156 (Combination Cohort A4)":
        "same raw under-capture as cohort A3.",
    "NCT06400472::LY4170156 (Combination Cohort A5)":
        "same raw under-capture as cohort A3.",
    "NCT04419649::Long-term Extension Cohort":
        "extension cohort gets no rows though the source states MDS (IPSS-R very low/low/intermediate) "
        "eligibility; the 12 sibling cohorts of the same trial extract correctly.",
    "NCT05538130::Phase 1a Monotherapy Dose Escalation":
        "Phase 1a gets no rows though the trial is in 'People With Advanced Solid Tumors' with BRAF-mutant "
        "melanoma in the official title; the Phase 1b arm carries both of the trial's rows.",
}
