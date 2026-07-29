"""Schema for the shared `trial_arms` table (spec §6.1).

`trial_arms` is the CENTRAL arm registry both paths reference. Arm identity is stored ONCE here; the drug path's
`trial_to_intervention` and the eligibility path's `arm_eligibility_raw` / `interpreted_eligibility` link to it
by `trial_arm_id` (a real FK, replacing the old string-match on `(trialId, arm)`).

The `trial_arm_id` is a DETERMINISTIC slug of the natural key `(trialId, arm)` — reproducible with no sequence
authority (so parallel workers and re-runs reproduce the same id), and human-readable in the TSVs. `trialId`,
`registry` and `arm` are kept as their own columns for readability and joins.
"""
from __future__ import annotations

from dataclasses import dataclass, fields


@dataclass
class TrialArm:
    """One trial arm/regime — the central arm registry row. Grain: (trialId, arm). CTGov: one per drug-bearing
    armGroup (label + type); ANZCTR: `intervention` (EXPERIMENTAL) + optional `comparator` (ACTIVE_COMPARATOR);
    fallback `all`. `trial_arm_id` is the deterministic slug both other paths link to."""

    trial_arm_id: str = ""   # deterministic slug of (trialId, arm) — the FK other tables reference
    trialId: str = ""        # NCT... (ctgov) | ACTRN... (anzctr)
    registry: str = ""       # "ctgov" | "anzctr"
    arm: str = ""            # CTGov armGroups[].label | ANZCTR intervention|comparator | "all"
    arm_type: str = ""       # EXPERIMENTAL / ACTIVE_COMPARATOR / PLACEBO_COMPARATOR / ... (flags control arms)


def _columns(dc) -> list[str]:
    return [f.name for f in fields(dc)]


TRIAL_ARMS_COLUMNS = _columns(TrialArm)

TABLE_FILES = {
    "trial_arms": "trial_arms.tsv",
}
