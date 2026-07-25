"""trial_to_intervention -> trial_arm_id migration: re-key against the fresh trial_arms registry and classify
kept / deleted (arm drifted away) / added (registry arm with no intervention row)."""
from __future__ import annotations

import csv
from pathlib import Path

from aus_trial_universe.agentic.tasks.drug_utility.migrate_trial_arms import build_report
from aus_trial_universe.agentic.tasks.shared.cohorts import trial_arm_id


def _write_old_t2i(path: Path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["trialId", "registry", "arm", "arm_type", "input_intervention_name"])
        for r in rows:
            w.writerow(r)


def test_rekey_keeps_matches_deletes_drift_and_reports_additions(tmp_path):
    p = tmp_path / "t2i.tsv"
    _write_old_t2i(p, [
        ("ACTRN1", "anzctr", "intervention", "EXPERIMENTAL", "drugA"),   # arm survives -> kept
        ("ACTRN1", "anzctr", "comparator", "ACTIVE_COMPARATOR", "drugB"),  # arm gone in fresh -> deletion
        ("NCT1", "ctgov", "Arm A", "EXPERIMENTAL", "drugC"),            # arm survives -> kept
    ])
    registry = {trial_arm_id("ACTRN1", "intervention"), trial_arm_id("NCT1", "Arm A"),
                trial_arm_id("NCT1", "Arm B")}   # NCT1::Arm B is new (no intervention row) -> addition

    rep = build_report(p, registry)
    assert [k["trial_arm_id"] for k in rep.kept] == [trial_arm_id("ACTRN1", "intervention"), trial_arm_id("NCT1", "Arm A")]
    assert len(rep.deleted) == 1 and rep.deleted[0]["arm"] == "comparator" and rep.deleted[0]["input_intervention_name"] == "drugB"
    assert rep.added_arms == [trial_arm_id("NCT1", "Arm B")]


def test_already_migrated_file_is_idempotent(tmp_path):
    # a file already carrying trial_arm_id re-keys to itself (no spurious deletions)
    p = tmp_path / "t2i_new.tsv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["trial_arm_id", "input_intervention_name"])
        w.writerow([trial_arm_id("NCT1", "Arm A"), "drugC"])
    rep = build_report(p, {trial_arm_id("NCT1", "Arm A")})
    assert rep.kept == [{"trial_arm_id": trial_arm_id("NCT1", "Arm A"), "input_intervention_name": "drugC"}]
    assert not rep.deleted and not rep.added_arms


def test_dedups_repeated_intervention_rows(tmp_path):
    p = tmp_path / "t2i.tsv"
    _write_old_t2i(p, [
        ("NCT1", "ctgov", "Arm A", "EXPERIMENTAL", "drugC"),
        ("NCT1", "ctgov", "Arm A", "EXPERIMENTAL", "drugC"),   # duplicate -> collapsed
    ])
    rep = build_report(p, {trial_arm_id("NCT1", "Arm A")})
    assert len(rep.kept) == 1
