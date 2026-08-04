"""Cross-path arm referential-integrity check — every `trial_arm_id` referenced by the eligibility content tables
(`arm_eligibility_raw` / `interpreted_eligibility`) AND by the drug path's `trial_to_intervention` must EXIST in
the SHARED `trial_arms` registry. That FK is the join key both paths share; a dangling reference means a row would
be silently dropped (or an arm lost) at join time.

Arm identity now lives in ONE place (the shared registry, populated via the shared cohort-identification module),
so the two paths cannot disagree on labels — this check verifies neither path references an arm the registry does
not contain, and reports registry arms referenced by neither path (informational).

    python -m aus_trial_universe.qa.arm_consistency   # exit 0 = consistent, 1 = dangling
"""
from __future__ import annotations

import logging

from aus_trial_universe.tasks.drug_utility.store import DrugRefStore
from aus_trial_universe.tasks.eligibility.store import EligStore
from aus_trial_universe.tasks.shared.cohorts import trial_id_of
from aus_trial_universe.tasks.shared.store import TrialArmStore

logger = logging.getLogger(__name__)


def _eligibility_arm_ids() -> set[str]:
    """Every trial_arm_id the eligibility tables reference (raw + interpreted)."""
    store = EligStore.load()
    ids = {r.trial_arm_id for rows in store.raw.values() for r in rows}
    ids |= {e.trial_arm_id for rows in store.interpreted.values() for e in rows}
    return {i for i in ids if i}


def check() -> dict:
    """Return a report dict: registry / elig_refs / drug_refs / role_refs counts + any dangling FKs + unused
    registry arms."""
    registry = TrialArmStore.load().ids()
    elig = _eligibility_arm_ids()
    drug_store = DrugRefStore.load()
    drug = drug_store.trial_arm_ids()
    roles = drug_store.role_trial_arm_ids()
    return {
        "registry": len(registry), "elig_refs": len(elig), "drug_refs": len(drug), "role_refs": len(roles),
        "elig_dangling": sorted(elig - registry),
        "drug_dangling": sorted(drug - registry),
        "role_dangling": sorted(roles - registry),
        "unused": sorted(registry - elig - drug - roles),
    }


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    r = check()
    logger.info("arm-consistency · trial_arms registry=%d · eligibility refs=%d · drug refs=%d · role refs=%d",
                r["registry"], r["elig_refs"], r["drug_refs"], r["role_refs"])

    ed = r["elig_dangling"]
    logger.info("  eligibility trial_arm_id(s) NOT in the registry (these BREAK the join): %d", len(ed))
    for taid in ed[:20]:
        logger.info("  ✗ %s (trial %s)", taid, trial_id_of(taid))

    dd = r["drug_dangling"]
    logger.info("  drug trial_to_intervention trial_arm_id(s) NOT in the registry: %d", len(dd))
    for taid in dd[:20]:
        logger.info("  ✗ %s (trial %s)", taid, trial_id_of(taid))

    rd = r["role_dangling"]
    logger.info("  drug trial_arm_drug_role trial_arm_id(s) NOT in the registry: %d", len(rd))
    for taid in rd[:20]:
        logger.info("  ✗ %s (trial %s)", taid, trial_id_of(taid))

    if r["unused"]:
        logger.info("  (info) %d registry arm(s) referenced by neither path yet", len(r["unused"]))

    ok = not ed and not dd and not rd
    logger.info("RESULT: %s", "CONSISTENT ✓" if ok else f"{len(ed) + len(dd) + len(rd)} dangling trial_arm_id(s) ✗")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
