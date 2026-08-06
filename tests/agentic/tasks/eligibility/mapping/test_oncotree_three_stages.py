"""THE PLUMBING TEST for OncoTree mapping's three stages — is everything actually WIRED TOGETHER?

Every other test here checks a component. This one checks the CONNECTIONS, because that is where this work kept
going wrong: each piece was individually correct while the wiring between them was not.

Real defects this file exists to catch, all found on 2026-08-05/06:
  · `export.py` looked up the cancer_type map with the RAW interpreted cell instead of the provenance-stripped key,
    so two rows shipped `oncotree_code=""` for a value that HAS a mapping. Component-correct, wiring wrong.
  · the drug path shared `reconcile_column(column=CANCER_TYPE)`, so making eligibility deterministic would have
    silently re-rolled reviewed `approval_cancer_type_map` data.
  · `--map-only` / `--reconcile` were whole-store commands, so a cancer-type-only change re-rolled gene_alteration.
  · a stage's output file was renamed while a reader still looked for the old name — caught only by running it.
"""
from __future__ import annotations

import csv
from pathlib import Path

from aus_trial_universe.tasks.eligibility.mapping.cancer_type import stage2, stage3
from aus_trial_universe.tasks.eligibility.mapping.stage_tables import CANCER_TYPE as tables


def _read(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


# --------------------------------------------------------------------------- stage boundaries
def test_stage2_is_pure_and_idempotent():
    """Stage 2 must be a function of ONE value. That is what makes cross-value churn structurally impossible —
    the property the whole deterministic-only design was chosen for."""
    initial = {"a": "NOT(MEL) AND NOT(SKCM) AND SKIN", "b": "BREAST", "c": "PRAD AND NOT(PRSCC)"}
    once, failures = stage2.run(initial)
    twice, _ = stage2.run(once)
    assert not failures
    assert once == twice                                   # idempotent
    assert stage2.run(initial)[0] == once                  # deterministic across calls
    # and a value's answer must not depend on which OTHER values are present
    assert stage2.canonicalise("BREAST") == stage2.run({"only": "BREAST"})[0]["only"] == "BREAST"


def test_stage2_does_the_two_jobs_and_nothing_else():
    # S2.2 factoring: NOT(A) AND NOT(B) -> NOT(A OR B), operands sorted. Both excluded codes must be DESCENDANTS
    # of the positive term, or S2.3 legitimately drops them as vacuous first and there is nothing left to factor.
    assert stage2.canonicalise("BREAST AND NOT(ILC) AND NOT(IDC)") == "BREAST AND NOT(IDC OR ILC)"
    # …and the vacuous case, for contrast: PRAD/SKIN are disjoint, so the exclusions go rather than being factored
    assert stage2.canonicalise("SKIN AND NOT(BREAST) AND NOT(PRAD)") == "SKIN"
    # S2.3 vacuous exclusion: PRSCC is a SIBLING of PRAD, so excluding it is a no-op and goes
    assert stage2.canonicalise("PRAD AND NOT(PRSCC)") == "PRAD"
    # a REAL exclusion (descendant of the positive term) must survive
    assert "NOT(" in stage2.canonicalise("BREAST AND NOT(IDC)")


def test_stage3_applies_rulings_last_and_reports_overrides():
    """A ruling must be the last word — that is what makes it immune to anything upstream."""
    from aus_trial_universe.tasks.eligibility.mapping import adjudications
    ruled = adjudications.for_column("cancer_type")
    assert ruled, "the register should not be empty — this test would pass vacuously"
    value, ruling = next(iter(ruled.items()))
    finalised, overridden = stage3.run({value: "SOMETHING_ELSE", "untouched": "BREAST"})
    assert finalised[value] == ruling.final          # the ruling wins
    assert finalised["untouched"] == "BREAST"        # everything else passes through
    assert value in overridden                       # and the override is REPORTED, not silent


def test_empty_string_is_a_legitimate_ruling():
    """`''` is the correct mapping for a value stating no CURRENT tumour type (prior-malignancy / screening cohort),
    so stage 3 must apply it. A truthiness test here would silently skip it — the trap that broke a test on
    2026-08-06 and that `reconcile.py` documents at its own ruling lookup."""
    import aus_trial_universe.tasks.eligibility.mapping.cancer_type.stage3 as s3
    real = s3.rulings
    try:
        s3.rulings = lambda: {"prior malignancy only": ""}
        finalised, overridden = s3.run({"prior malignancy only": "BREAST"})
    finally:
        s3.rulings = real
    assert finalised["prior malignancy only"] == ""
    assert overridden == ["prior malignancy only"]


# --------------------------------------------------------------------------- the wiring
def test_the_three_tables_carry_the_whole_provenance_chain(tmp_path):
    initial = {"v": "PRAD AND NOT(PRSCC)"}
    reconciled, _ = stage2.run(initial)
    finalised = {"v": "PRNE"}                      # pretend a ruling fired
    tables.write_all(tmp_path, initial=initial, reconciled=reconciled, finalised=finalised)
    row = _read(tmp_path / tables.file("finalised"))[0]
    assert row["oncotree_code_initial"] == "PRAD AND NOT(PRSCC)"
    assert row["oncotree_code_reconciled"] == "PRAD"          # stage 2 dropped the vacuous exclusion
    assert row["oncotree_code_finalised"] == "PRNE"           # stage 3 overrode it
    # every name is DERIVED from the code beside it — never authored, so they cannot disagree
    assert row["oncotree_name_finalised"] == "Prostate Neuroendocrine Carcinoma"
    assert row["oncotree_name_reconciled"] == "Prostate Adenocarcinoma"
    # the earlier stages' own tables agree with the accreted columns
    assert _read(tmp_path / tables.file("initial"))[0]["oncotree_code_initial"] == "PRAD AND NOT(PRSCC)"
    assert _read(tmp_path / tables.file("reconciled"))[0]["oncotree_code_reconciled"] == "PRAD"


def test_every_reader_points_at_the_stage_it_should():
    """The renames of 2026-08-06 are only safe if every consumer moved with them. A reader left on an old filename
    fails OPEN — it reads nothing and ships blanks — so this asserts the wiring explicitly."""
    import inspect
    from aus_trial_universe import export
    from aus_trial_universe.qa import gates, invariants
    from aus_trial_universe.tasks.drug_utility import map_approvals

    # the export ships STAGE 3, and strips provenance before the lookup (the bug that dropped two rows)
    src = inspect.getsource(export.build_export_rows)
    assert 'CANCER_TYPE.load(elig_dir, "finalised")' in src
    assert "strip_provenance(e.cancer_type_interpreted)" in src
    # the drift gate diffs the shipping mapping
    assert '"oncotree_code_finalised"' in inspect.getsource(gates._mapping_drift_gate)
    # the invariant sweep reads the shipping table
    assert "cancer_type_map_finalised.tsv" in inspect.getsource(invariants)
    # the DRUG path stays on the legacy reconciler — its table was explicitly out of scope
    assert "three_stage=False" in inspect.getsource(map_approvals)


def test_reconcile_column_dispatches_cancer_type_to_the_deterministic_path():
    """cancer_type must reach stages 2+3 and make NO LLM call. A client that raises proves it never calls one."""
    from aus_trial_universe.tasks.eligibility.mapping import reconcile as rec

    class ExplodingClient:
        def parse(self, *a, **k):
            raise AssertionError("stage 2/3 must not call the LLM")

    final, unresolved, groups = rec.reconcile_column(
        ExplodingClient(), {"metastatic breast cancer": "BREAST", "x": "PRAD AND NOT(PRSCC)"},
        column=rec.CANCER_TYPE, workers=1, max_attempts=1, use_reviewer=True)
    assert final["x"] == "PRAD"          # stage 2 ran
    assert groups == 0 and unresolved == []
    # …and the legacy path is still reachable for the drug caller
    assert "three_stage" in rec.reconcile_column.__doc__


def test_stage1_is_reachable_from_both_its_new_home_and_the_shim():
    """Stage 1 moved into `cancer_type/` on 2026-08-06. The shim in `workflow.py` keeps `map_all_columns`, `run.py`
    and the existing tests working; if it ever diverges, the three-column pool silently uses a different mapper."""
    from aus_trial_universe.tasks.eligibility.mapping import workflow
    from aus_trial_universe.tasks.eligibility.mapping.cancer_type import stage1
    assert workflow.map_oncotree is stage1.map_oncotree
    assert workflow.map_cancer_types is stage1.map_cancer_types
    assert workflow.OncotreeResult is stage1.OncotreeResult


def test_column_scoping_is_available_on_the_mapping_entry_points():
    """Without this, a cancer-type-only run re-rolls gene_alteration — which happened, and drifted 3 values."""
    import inspect
    from aus_trial_universe import run
    assert "--columns" in inspect.getsource(run.main)
    for fn in (run._run_map_only, run._run_reconcile):
        assert "columns" in inspect.signature(fn).parameters


# --------------------------------------------------------------------------- END TO END
def test_interpreted_text_through_all_three_stages_to_the_export(tmp_path, monkeypatch):
    """THE MIGRATION TEST: interpreted input text -> stage 1 table -> stage 2 -> stage 3 -> export, in one flow.

    Everything above tests a piece. This drives the REAL entry points (`run.main(--reconcile)` then
    `export.run_export`) over a seeded store and follows four values chosen to exercise a different part of the
    chain each. If any link is mis-wired — a renamed file a reader missed, a lookup key that does not match, a
    stage's output not reaching the next — one of these assertions fails.
    """
    import aus_trial_universe.tasks.eligibility.mapping.cancer_type.stage3 as s3
    from aus_trial_universe import export as EX, run
    from aus_trial_universe.tasks.eligibility.schema import (
        ArmEligibilityRaw, CancerTypeMap, InterpretedEligibility,
    )
    from aus_trial_universe.tasks.eligibility.store import EligStore
    from aus_trial_universe.tasks.shared.schema import TrialArm

    #                    interpreted text                          stage-1 code           what it proves
    CASES = [
        ("A", "metastatic breast cancer",                          "BREAST",              "clean passthrough"),
        ("B", "prostate adenocarcinoma excluding squamous",         "PRAD AND NOT(PRSCC)", "stage 2 drops a vacuous NOT"),
        ("C", "breast cancer excluding lobular and ductal",         "BREAST AND NOT(ILC) AND NOT(IDC)",
                                                                                          "stage 2 factors the NOTs"),
        # the trailing bracket is TNM detail, not a provenance tag — this is the shape that broke the export lookup
        ("D", "stage IV small-cell lung cancer (SCLC) [T any, N any, M1 a/b/c]", "SCLC",   "provenance-stripped join"),
    ]
    store_root = tmp_path / "elig"
    s = EligStore()
    for arm, text, code, _why in CASES:
        s.set_trial(f"NCT{arm}",
                    [ArmEligibilityRaw(trial_arm_id=f"NCT{arm}::1", cancer_type_raw=text)],
                    [InterpretedEligibility(trial_arm_id=f"NCT{arm}::1", conjunction_index=1,
                                            cancer_type_interpreted=text)])
    # stage 1's table is keyed on the PROVENANCE-STRIPPED value, which is what makes case D a real test
    from aus_trial_universe.tasks.eligibility.mapping.workflow import strip_provenance
    s.cancer_map = {strip_provenance(t): CancerTypeMap(cancer_type=strip_provenance(t), oncotree_code=c)
                    for _a, t, c, _w in CASES}
    s.save(store_root / "current_version")

    # stage 3: one ruling, so the override is observable end to end
    monkeypatch.setattr(s3, "rulings", lambda: {"metastatic breast cancer": "IDC"})

    rc = run.main(["--reconcile", "--store-root", str(store_root), "--columns", "cancer_type",
                   "--no-cache", "--no-cache-prune", "--workers", "2"])
    assert rc == 0
    cur = store_root / "current_version"

    # ---- the three stage tables, traced from the interpreted text ----------------------------------------
    init = {r["cancer_type"]: r for r in _read(cur / tables.file("initial"))}
    reco = {r["cancer_type"]: r for r in _read(cur / tables.file("reconciled"))}
    fin = {r["cancer_type"]: r for r in _read(cur / tables.file("finalised"))}
    assert set(init) == set(reco) == set(fin), "every stage must cover exactly the same value set"

    assert init["prostate adenocarcinoma excluding squamous"]["oncotree_code_initial"] == "PRAD AND NOT(PRSCC)"
    assert reco["prostate adenocarcinoma excluding squamous"]["oncotree_code_reconciled"] == "PRAD"   # S2.3
    assert reco["breast cancer excluding lobular and ductal"]["oncotree_code_reconciled"] == \
        "BREAST AND NOT(IDC OR ILC)"                                                                 # S2.2
    assert fin["metastatic breast cancer"]["oncotree_code_reconciled"] == "BREAST"                    # stage 2 kept it
    assert fin["metastatic breast cancer"]["oncotree_code_finalised"] == "IDC"                        # stage 3 overrode
    assert fin["metastatic breast cancer"]["oncotree_name_finalised"] == "Breast Invasive Ductal Carcinoma"

    # ---- the export ships stage 3, joined on the provenance-stripped key -----------------------------------
    # `build_export_rows` is called directly rather than via `run_export`: this asserts the JOIN, and going through
    # the CLI would drag in trial_info, the drug store and the arm registry, none of which is what is being tested.
    class _Arms:
        arms = {f"NCT{a}": [TrialArm(trial_arm_id=f"NCT{a}::1", trialId=f"NCT{a}", registry="ctgov", arm="1")]
                for a, _t, _c, _w in CASES}

    class _Drug:
        occurrences: list = []

    rows = EX.build_export_rows(EligStore.load(store_root), _Arms(), _Drug(), {}, elig_dir=cur)
    by_arm = {r["trial_arm_id"]: r for r in rows}
    assert len(by_arm) == len(CASES), f"expected one row per arm, got {sorted(by_arm)}"
    assert by_arm["NCTA::1"]["oncotree_code"] == "IDC"                       # stage 3's answer ships
    assert by_arm["NCTB::1"]["oncotree_code"] == "PRAD"                      # stage 2's answer ships
    assert by_arm["NCTC::1"]["oncotree_code"] == "BREAST AND NOT(IDC OR ILC)"
    # …and case D: the interpreted cell has a bracketed tail, so a raw-key lookup would ship "" here
    assert by_arm["NCTD::1"]["oncotree_code"] == "SCLC"
    assert by_arm["NCTD::1"]["oncotree_name"] == "Small Cell Lung Cancer"
