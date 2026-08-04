"""Production gates — the checks that decide whether an unattended cycle is trustworthy.

These tests assert the FAILURE directions, because that is the whole point: in production nobody reads the log, so
a gate that cannot fail is decoration.
"""
from __future__ import annotations

import csv

import pytest

from aus_trial_universe.qa import gates as G
from aus_trial_universe.tasks.ingestion.expiry import ExpiryReport


def _export(tmp_path, n_cols=33, trials=("NCT1",), name="trial_eligibility.tsv"):
    """A minimal but VALID Set-A export. `cancer_type_interpreted` carries a real value so the output_validator
    gate has nothing to flag — an all-empty row would trip its "all eligibility columns empty" check and make
    every test WARN for the wrong reason."""
    p = tmp_path / name
    cols = ["trialId", "trial_arm_id", "arm", "cancer_type_interpreted"] + [f"c{i}" for i in range(n_cols - 4)]
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(cols)
        for t in trials:
            w.writerow([t, f"{t}::A", "A", "advanced NSCLC"] + [""] * (n_cols - 4))
    return p


@pytest.fixture
def clean(monkeypatch):
    """A green baseline: patch every store/module the gates read so each test can break exactly one thing."""
    from aus_trial_universe.tasks.eligibility import scope as SC
    from aus_trial_universe.tasks.eligibility.store import EligStore
    from aus_trial_universe.tasks.shared.schema import TrialArm
    from aus_trial_universe.tasks.shared.store import TrialArmStore

    elig = EligStore()
    elig.interpreted = {"NCT1": [type("E", (), {"trial_arm_id": "NCT1::A"})()]}
    arms = TrialArmStore()
    arms.arms = {"NCT1": [TrialArm(trial_arm_id="NCT1::A", trialId="NCT1", registry="ctgov", arm="A")]}
    monkeypatch.setattr(EligStore, "load", classmethod(lambda cls, *a, **k: elig))
    monkeypatch.setattr(TrialArmStore, "load", classmethod(lambda cls, *a, **k: arms))
    monkeypatch.setattr("aus_trial_universe.tasks.eligibility.qa.arm_consistency.check",
                        lambda: {"registry": 1, "elig_refs": 1, "drug_refs": 1, "role_refs": 1,
                                 "elig_dangling": [], "drug_dangling": [], "role_dangling": [], "unused": []})
    monkeypatch.setattr("aus_trial_universe.trial_info.load_trial_info", lambda *a, **k: {"NCT1": object()})
    # NB: `unexplained_arms` is deliberately NOT patched here — on a healthy store it returns [] on its own, and
    # patching it would mask the very behaviour test_empty_arm_without_any_verdict_fails exists to check.
    assert SC.unexplained_arms(elig, []) == []
    return elig


def _run(tmp_path, **kw):
    base = dict(before={"trials curated": 1}, after={"trials curated": 1}, kept={"NCT1"},
                expiry=ExpiryReport(applied=True), curated_ids=[], export_path=_export(tmp_path), elig_rc=0)
    return G.run_gates(**{**base, **kw})


def test_all_green_baseline(clean, tmp_path):
    rep = _run(tmp_path)
    assert rep.ok and rep.verdict == G.PASS, [(g.name, g.status, g.detail) for g in rep.gates]


def test_dangling_fk_fails(clean, tmp_path, monkeypatch):
    monkeypatch.setattr("aus_trial_universe.tasks.eligibility.qa.arm_consistency.check",
                        lambda: {"registry": 1, "elig_refs": 2, "drug_refs": 1, "role_refs": 1,
                                 "elig_dangling": ["NCT9::A"], "drug_dangling": [], "role_dangling": [],
                                 "unused": []})
    rep = _run(tmp_path)
    assert not rep.ok and [g.name for g in rep.failed] == ["fk_integrity"]


def test_expired_trial_still_in_registry_fails(clean, tmp_path):
    rep = _run(tmp_path, expiry=ExpiryReport(expired=["NCT1"], applied=True))
    assert "expiry_completeness" in [g.name for g in rep.failed]


def test_trial_info_orphan_fails(clean, tmp_path, monkeypatch):
    monkeypatch.setattr("aus_trial_universe.trial_info.load_trial_info",
                        lambda *a, **k: {"NCT1": object(), "NCT_GHOST": object()})
    rep = _run(tmp_path)
    g = next(g for g in rep.gates if g.name == "expiry_completeness")
    assert g.status == G.FAIL and "not in the registry" in g.detail


def test_reference_data_shrink_fails(clean, tmp_path):
    """A drug/vocab table shrinking is never legitimate in a refresh — that is data loss."""
    rep = _run(tmp_path, before={"trials curated": 1, "drugs (canonical)": 1309},
               after={"trials curated": 1, "drugs (canonical)": 1300})
    g = next(g for g in rep.gates if g.name == "additive_safety")
    assert g.status == G.FAIL and "SHRANK" in g.detail


def test_trial_count_identity_is_exact(clean, tmp_path):
    """after == before − expired + curated. Anything else means rows appeared or vanished unaccountably."""
    rep = _run(tmp_path, before={"trials curated": 10}, after={"trials curated": 10}, curated_ids=["NCT1"])
    g = next(g for g in rep.gates if g.name == "additive_safety")
    assert g.status == G.FAIL and "identity broken" in g.detail
    # …and holds when the arithmetic works out.
    rep = _run(tmp_path, before={"trials curated": 10}, after={"trials curated": 9},
               expiry=ExpiryReport(expired=["NCT_X", "NCT_Y"], applied=True), curated_ids=["NCT1"])
    assert next(g for g in rep.gates if g.name == "additive_safety").status == G.PASS


def test_missing_kept_trial_or_bad_rc_fails_curation(clean, tmp_path):
    rep = _run(tmp_path, kept={"NCT1", "NCT_NEVER_CURATED"})
    g = next(g for g in rep.gates if g.name == "curation_completeness")
    assert g.status == G.FAIL and "MISSING" in g.detail
    assert next(g for g in _run(tmp_path, elig_rc=3).gates
                if g.name == "curation_completeness").status == G.FAIL


def test_unexplained_empty_arm_fails(clean, tmp_path, monkeypatch):
    """The gate that turns a silent extraction miss into a failed run."""
    from aus_trial_universe.tasks.eligibility import scope as SC
    monkeypatch.setattr(SC, "unexplained_arms", lambda store, empty: ["NCT1::B"])
    rep = _run(tmp_path)
    g = next(g for g in rep.gates if g.name == "empty_output_reasons")
    assert g.status == G.FAIL and "UNEXPLAINED" in g.detail


def test_empty_arm_without_any_verdict_fails(clean, tmp_path):
    """An empty arm that was never classified at all must also fail — not just an explicit 'unexplained'."""
    from aus_trial_universe.tasks.shared.schema import TrialArm
    from aus_trial_universe.tasks.shared.store import TrialArmStore
    TrialArmStore.load().arms["NCT1"].append(
        TrialArm(trial_arm_id="NCT1::B", trialId="NCT1", registry="ctgov", arm="B"))
    rep = _run(tmp_path)
    g = next(g for g in rep.gates if g.name == "empty_output_reasons")
    assert g.status == G.FAIL and "NCT1::B" in g.detail


def test_export_shape_and_coverage(clean, tmp_path):
    assert next(g for g in _run(tmp_path, export_path=tmp_path / "gone.tsv").gates
                if g.name == "export_integrity").status == G.FAIL          # missing file
    # NB: distinct filenames — _run writes its own default export, which would otherwise overwrite these.
    assert next(g for g in _run(tmp_path, export_path=_export(tmp_path, n_cols=31, name="narrow.tsv")).gates
                if g.name == "export_integrity").status == G.FAIL          # wrong column count
    # a curated trial absent from the export with no scope verdict
    rep = _run(tmp_path, export_path=_export(tmp_path, trials=("NCT_OTHER",), name="other.tsv"))
    g = next(g for g in rep.gates if g.name == "export_integrity")
    assert g.status == G.FAIL and "absent from the export" in g.detail


def test_expiry_guard_trip_fails_the_cycle(clean, tmp_path):
    """A tripped guard means the download was judged untrustworthy — the cycle must not be reported clean."""
    rep = _run(tmp_path, expiry=ExpiryReport(expired=["a", "b"], guarded=True))
    assert next(g for g in rep.gates if g.name == "expiry_guard").status == G.FAIL


def test_output_validator_warns_but_never_fails(clean, tmp_path):
    """The "review of the reviewers" is surfaced every run but must not fail a cycle — some of its flags are
    validator crudeness (a same-type histology+stage AND is satisfiable and faithful)."""
    p = tmp_path / "flagged.tsv"
    cols = ["trialId", "trial_arm_id", "arm", "cancer_type_interpreted", "oncotree_code"] + [f"c{i}" for i in range(28)]
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(cols)
        w.writerow(["NCT1", "NCT1::A", "A", "clear cell RCC AND pT2 RCC", "CCRCC"] + [""] * 28)
    rep = _run(tmp_path, export_path=p)
    g = next(g for g in rep.gates if g.name == "output_validator")
    assert g.status == G.WARN and "flag(s)" in g.detail
    assert rep.ok                                   # WARN never fails the cycle


def test_universe_swing_warns_without_failing(clean, tmp_path):
    rep = _run(tmp_path, previous_kept=100)          # 1 vs 100 kept = a huge swing
    g = next(g for g in rep.gates if g.name == "universe_swing")
    assert g.status == G.WARN and rep.ok and rep.verdict == G.WARN


# --- oncotree_expressions: the backstop for the 2026-08-04 correction ------------- #
def test_oncotree_expression_gate_fails_on_a_defective_export(clean, tmp_path):
    """A gate that cannot fail is decoration. This one grades the EXPORT — the artifact that actually ships —
    so a regression in the mapping stage cannot reach a consumer unnoticed."""
    export = tmp_path / "bad.tsv"
    export.write_text("trialId\toncotree_code\nNCT1\tNOT(Pan-cancer)\n", encoding="utf-8")
    rep = G.GateReport()
    G._oncotree_expression_gate(rep, export)
    (gate,) = rep.gates
    assert gate.name == "oncotree_expressions" and gate.status == G.FAIL
    assert "log_negation_only" in gate.detail or "log_negated_sentinel" in gate.detail


def test_oncotree_expression_gate_passes_clean_and_warns_without_failing(clean, tmp_path):
    good = tmp_path / "good.tsv"
    good.write_text("trialId\toncotree_code\nNCT1\tNSCLC\nNCT2\tBREAST\n", encoding="utf-8")
    rep = G.GateReport()
    G._oncotree_expression_gate(rep, good)
    assert rep.gates[0].status == G.PASS

    warny = tmp_path / "warn.tsv"                      # a no-op exclusion is noise, never fatal
    warny.write_text("trialId\toncotree_code\nNCT1\tPAAD AND NOT(PANET)\n", encoding="utf-8")
    rep = G.GateReport()
    G._oncotree_expression_gate(rep, warny)
    assert rep.gates[0].status == G.WARN and not rep.failed


# --- gene_alteration_expressions: the backstop for the 2026-08-04 gene correction --- #
def test_gene_alteration_expression_gate_fails_on_a_defective_export(clean, tmp_path):
    """A gate that cannot fail is decoration. `effects=SPLICE` is the exact defect that shipped for months:
    SPLICE is a member of CodingEffect, not VariantEffect, and the pre-correction validator blessed it because its
    enums came from the prompt rather than from SmallVariant.java."""
    export = tmp_path / "bad.tsv"
    export.write_text(
        "trialId\tgene_alteration_interpreted\tgene_alteration_findingmodel\n"
        "NCT1\tMET exon 14 skipping\t"
        "SmallVariant[gene=MET & transcriptImpact.effects=SPLICE]\n",
        encoding="utf-8")
    rep = G.GateReport()
    G._gene_alteration_expression_gate(rep, export)
    (gate,) = rep.gates
    assert gate.name == "gene_alteration_expressions" and gate.status == G.FAIL
    assert "splice_as_effect" in gate.detail or "invalid_syntax" in gate.detail


def test_gene_alteration_expression_gate_fails_on_hla_as_pharmacogenotype(clean, tmp_path):
    export = tmp_path / "hla.tsv"
    export.write_text(
        "trialId\tgene_alteration_interpreted\tgene_alteration_findingmodel\n"
        "NCT1\tHLA-A*02:01-positive\tPharmocoGenotype[gene=HLA-A & allele=*02:01]\n", encoding="utf-8")
    rep = G.GateReport()
    G._gene_alteration_expression_gate(rep, export)
    assert rep.gates[0].status == G.FAIL and "hla_as_pharmacogenotype" in rep.gates[0].detail


def test_gene_alteration_expression_gate_passes_clean_and_warns_without_failing(clean, tmp_path):
    good = tmp_path / "good.tsv"
    good.write_text(
        "trialId\tgene_alteration_interpreted\tgene_alteration_findingmodel\n"
        "NCT1\tKRAS G12C mutation\tSmallVariant[gene=KRAS & transcriptImpact.hgvsProteinImpact=p.G12C]\n"
        "NCT2\tMET exon 14 skipping\t"
        "SmallVariant[gene=MET & transcriptImpact.affectedExon=14 & transcriptImpact.codingEffect=SPLICE]\n",
        encoding="utf-8")
    rep = G.GateReport()
    G._gene_alteration_expression_gate(rep, good)
    assert rep.gates[0].status == G.PASS

    # a canonical fusion driver expanded with type=GAIN is over-broad, but never fatal
    warny = tmp_path / "warn.tsv"
    warny.write_text(
        "trialId\tgene_alteration_interpreted\tgene_alteration_findingmodel\n"
        "NCT1\tALK gene alteration\tSmallVariant[gene=ALK] | GainDeletion[gene=ALK & type=GAIN]\n", encoding="utf-8")
    rep = G.GateReport()
    G._gene_alteration_expression_gate(rep, warny)
    assert rep.gates[0].status == G.WARN and not rep.failed


def test_gene_alteration_expression_gate_is_registered_in_the_run(clean, tmp_path):
    """The gate must actually run — a gate defined but never called is worse than no gate."""
    rep = _run(tmp_path)
    assert any(g.name == "gene_alteration_expressions" for g in rep.gates)
