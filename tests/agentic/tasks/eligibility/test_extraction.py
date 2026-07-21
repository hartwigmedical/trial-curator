"""Tests for the eligibility-extraction workflow (fake client, no LLM).

Covers: regime-aware extraction, cross-product distribution (trial-wide x regime-specific),
multi-source provenance, inline NOT(), the reviewer panel (gating vs advisory), the refine
loop, and the ANZCTR path (single eligibility cohort; regimes from the drug agent — spec §6.1).
"""
from __future__ import annotations

from aus_trial_universe.agentic.core.client import LlmResult
from aus_trial_universe.agentic.tasks.eligibility.extraction.agents import (
    ENUMERATION_REVIEWER_INSTRUCTIONS,
    REVIEWERS,
)
from aus_trial_universe.agentic.tasks.eligibility.extraction.schema import (
    DrugExtraction,
    EligibilityExtraction,
    ExtractedRow,
    JudgeVerdict,
)
from aus_trial_universe.agentic.tasks.eligibility.extraction.workflow import (
    Cohort,
    extract_trial,
)


def _reviewer_key(instructions: str) -> str | None:
    if instructions == ENUMERATION_REVIEWER_INSTRUCTIONS:
        return "enumeration"
    for spec in REVIEWERS:
        if spec.instructions == instructions:
            return spec.key
    return None


class _ScriptedClient:
    """Fake LlmClient.parse dispatching canned outputs by schema.

    extractions: one per extractor call (attempt). verdicts: {reviewer_key -> [JudgeVerdict,...]},
    consumed in order per reviewer (default faithful=True). cohort_detection / drugs: ANZCTR path.
    """

    def __init__(self, extractions, verdicts=None, intervention_drugs=None, comparator_drugs=None):
        self._extractions = extractions
        self._verdicts = {k: list(v) for k, v in (verdicts or {}).items()}
        self._vcount = {k: 0 for k in self._verdicts}
        self._intervention_drugs = intervention_drugs or []
        self._comparator_drugs = comparator_drugs or []
        self.extractor_calls = 0

    def parse(self, output_schema, *, instructions, user_input, model=None,
              temperature=None, seed=None, max_completion_tokens=None):
        if output_schema is EligibilityExtraction:
            idx = min(self.extractor_calls, len(self._extractions) - 1)
            self.extractor_calls += 1
            return LlmResult(self._extractions[idx], "fake", "{}", False, 1)
        if output_schema is JudgeVerdict:
            key = _reviewer_key(instructions)
            seq = self._verdicts.get(key)
            if not seq:
                return LlmResult(JudgeVerdict(faithful=True), "fake", "{}", False, 1)
            i = min(self._vcount[key], len(seq) - 1)
            self._vcount[key] += 1
            return LlmResult(seq[i], "fake", "{}", False, 1)
        if output_schema is DrugExtraction:
            return LlmResult(DrugExtraction(intervention_drugs=self._intervention_drugs,
                                            comparator_drugs=self._comparator_drugs), "fake", "{}", False, 1)
        raise AssertionError(f"unexpected schema {output_schema}")


def _extraction(*rows):
    return EligibilityExtraction(rows=list(rows))


# --- CTGov path ------------------------------------------------------------- #
def test_trialwide_replicates_across_cohorts_with_per_cohort_drug():
    client = _ScriptedClient(extractions=[_extraction(
        ExtractedRow(cohort="trial-wide", cancer_type="NSCLC", cancer_type_sources=["TITLE"]),
    )])
    cohorts = [
        Cohort(label="Arm A", drug="drugA", drug_source="INTERVENTIONS MODULE", arm_type="EXPERIMENTAL"),
        Cohort(label="Arm B", drug="drugB", drug_source="INTERVENTIONS MODULE", arm_type="ACTIVE_COMPARATOR"),
    ]
    result = extract_trial(client, trial_id="NCT1", source_text="...", cohorts=cohorts)
    assert result.faithful and result.attempts == 1
    assert [(r.cohort, r.arm_type, r.cancer_type, r.drug) for r in result.rows] == [
        ("Arm A", "EXPERIMENTAL", "NSCLC [TITLE]", "drugA [INTERVENTIONS MODULE]"),
        ("Arm B", "ACTIVE_COMPARATOR", "NSCLC [TITLE]", "drugB [INTERVENTIONS MODULE]"),
    ]


def test_cohort_specific_criterion_merges_only_into_its_cohort():
    client = _ScriptedClient(extractions=[_extraction(
        ExtractedRow(cohort="trial-wide", cancer_type="solid tumour", cancer_type_sources=["ELIGIBILITY CRITERIA"]),
        ExtractedRow(cohort="C1", gene_alteration="EGFR mutation", gene_alteration_sources=["ELIGIBILITY CRITERIA"]),
    )])
    cohorts = [Cohort(label="Arm A", drug="osi"), Cohort(label="Arm B", drug="chemo")]
    result = extract_trial(client, trial_id="NCT2", source_text="...", cohorts=cohorts)
    by_cohort = {r.cohort: r for r in result.rows}
    assert len(result.rows) == 2
    # C1 = Arm A gets trial-wide AND its specific gene; Arm B gets only trial-wide
    assert by_cohort["Arm A"].cancer_type == "solid tumour [ELIGIBILITY CRITERIA]"
    assert by_cohort["Arm A"].gene_alteration == "EGFR mutation [ELIGIBILITY CRITERIA]"
    assert by_cohort["Arm B"].cancer_type == "solid tumour [ELIGIBILITY CRITERIA]"
    assert by_cohort["Arm B"].gene_alteration == ""


def test_cohort_wins_on_cancer_type_and_dedup_prevents_blowup():
    """Regression for the NCT04221035 680-row blow-up: staging restated in BOTH trial-wide and cohort
    scopes must not AND into impossible 'Stage A AND Stage B' rows (cohort value wins on cancer_type),
    and the redundant trial-wide OR-rows must de-duplicate away instead of cross-multiplying."""
    client = _ScriptedClient(extractions=[_extraction(
        # trial-wide: two staging OR-alternatives (as the extractor wrongly duplicated)
        ExtractedRow(cohort="trial-wide", cancer_type="Stage M NBL", cancer_type_sources=["ELIGIBILITY CRITERIA"]),
        ExtractedRow(cohort="trial-wide", cancer_type="Stage Ms NBL", cancer_type_sources=["ELIGIBILITY CRITERIA"]),
        # cohort C1: the SAME staging restated + a genuinely cohort-specific prior therapy
        ExtractedRow(cohort="C1", cancer_type="Stage M NBL", prior_therapy="post-induction",
                     cancer_type_sources=["ELIGIBILITY CRITERIA"], prior_therapy_sources=["ELIGIBILITY CRITERIA"]),
        ExtractedRow(cohort="C1", cancer_type="Stage Ms NBL", prior_therapy="post-induction",
                     cancer_type_sources=["ELIGIBILITY CRITERIA"], prior_therapy_sources=["ELIGIBILITY CRITERIA"]),
    )])
    cohorts = [Cohort(label="Arm A"), Cohort(label="Arm B")]
    result = extract_trial(client, trial_id="NCT_NBL", source_text="...", cohorts=cohorts, use_judge=False)
    # no cancer_type cell ANDs two staging values (would be unsatisfiable)
    assert all(" AND " not in r.cancer_type for r in result.rows), [r.cancer_type for r in result.rows]
    # Arm A (C1): 2 trial-wide x 2 specifics = 4 combos, but cancer_type-wins + dedup -> 2 self-contained rows
    arm_a = [r for r in result.rows if r.cohort == "Arm A"]
    assert len(arm_a) == 2, [(r.cancer_type, r.prior_therapy) for r in arm_a]
    assert {r.cancer_type for r in arm_a} == {"Stage M NBL [ELIGIBILITY CRITERIA]", "Stage Ms NBL [ELIGIBILITY CRITERIA]"}
    assert all(r.prior_therapy == "post-induction [ELIGIBILITY CRITERIA]" for r in arm_a)
    # Arm B (no specifics): gets the 2 trial-wide staging rows, still self-contained
    arm_b = [r for r in result.rows if r.cohort == "Arm B"]
    assert {r.cancer_type for r in arm_b} == {"Stage M NBL [ELIGIBILITY CRITERIA]", "Stage Ms NBL [ELIGIBILITY CRITERIA]"}


def test_cohort_wins_preserves_trialwide_cancer_type_exclusion():
    """cohort-wins on cancer_type keeps the cohort's positive type but must NOT drop a trial-wide NOT() carve-out."""
    client = _ScriptedClient(extractions=[_extraction(
        ExtractedRow(cohort="trial-wide", cancer_type="DMG AND NOT(thalamic DMG)", cancer_type_sources=["ELIGIBILITY CRITERIA"]),
        ExtractedRow(cohort="C1", cancer_type="pontine DMG", cancer_type_sources=["ELIGIBILITY CRITERIA"]),
    )])
    result = extract_trial(client, trial_id="NCT_X", source_text="...",
                           cohorts=[Cohort(label="Arm A"), Cohort(label="Arm B")], use_judge=False)
    arm_a = [r for r in result.rows if r.cohort == "Arm A"][0]
    # cohort's positive type ('pontine DMG') wins over trial-wide's ('DMG'), but the trial-wide
    # NOT() exclusion is preserved — never silently dropped
    assert arm_a.cancer_type == "pontine DMG AND NOT(thalamic DMG) [ELIGIBILITY CRITERIA]"
    # Arm B (no specifics) keeps the full trial-wide cell incl. the exclusion
    arm_b = [r for r in result.rows if r.cohort == "Arm B"][0]
    assert arm_b.cancer_type == "DMG AND NOT(thalamic DMG) [ELIGIBILITY CRITERIA]"


def test_top_level_and_splitter_respects_paren_depth():
    """The exclusion-preservation merge relies on this: split top-level ' AND ' but keep NOT(...) bodies
    (incl. an ' AND ' or ' OR ' inside them) intact."""
    from aus_trial_universe.agentic.tasks.eligibility.extraction.workflow import _top_level_and
    assert _top_level_and("DMG") == ["DMG"]
    assert _top_level_and("") == []
    assert _top_level_and("DMG AND NOT(thalamic DMG)") == ["DMG", "NOT(thalamic DMG)"]
    # ' AND ' / ' OR ' inside a NOT() body must NOT split
    assert _top_level_and("A AND NOT(x AND y)") == ["A", "NOT(x AND y)"]
    assert _top_level_and("DMG AND NOT(thalamic DMG OR cerebellar DMG) AND NOT(disseminated disease)") == \
        ["DMG", "NOT(thalamic DMG OR cerebellar DMG)", "NOT(disseminated disease)"]


def test_extraction_judgement_prompt_decisions_present():
    """Prompt-only judgement rules (2026-07-10) can't be caught by fake-client behaviour tests — guard the
    strings so a future prompt edit can't silently drop them. See memory v2-stage2-extraction-decisions."""
    from aus_trial_universe.agentic.tasks.eligibility.extraction.agents import EXTRACTOR_INSTRUCTIONS, REVIEWERS
    rv = {s.key: s.instructions for s in REVIEWERS}
    # (1) capture tumour-type exclusions as NOT(); (2) no subsuming over-enumeration; (3) one-scope assignment
    assert "except" in EXTRACTOR_INSTRUCTIONS and "excluding" in EXTRACTOR_INSTRUCTIONS
    assert "refractory to standard therapy" in EXTRACTOR_INSTRUCTIONS  # the subsuming-row example
    assert "EXACTLY ONE scope" in EXTRACTOR_INSTRUCTIONS
    assert "excluded tumour" in rv["cancer_type"].lower() or "tumour-type exclusion" in rv["cancer_type"].lower()
    assert "over-enumeration" in rv["prior_therapy"].lower()
    assert "subsuming" in rv["structural"].lower()
    # (4) drop criteria for text-cohorts NOT in the fixed regime list (closed cohorts) — decision 8 (§6.1 #5)
    assert "DROP" in EXTRACTOR_INSTRUCTIONS and "closed" in EXTRACTOR_INSTRUCTIONS.lower()
    assert "closed" in rv["structural"].lower() and "drop" in rv["structural"].lower()


def test_not_negation_and_multisource_passthrough():
    client = _ScriptedClient(extractions=[_extraction(
        ExtractedRow(cohort="trial-wide",
                     cancer_type="solid tumour AND NOT(melanoma)", cancer_type_sources=["TITLE", "ELIGIBILITY CRITERIA"],
                     prior_therapy="NOT(prior EGFR TKI)", prior_therapy_sources=["ELIGIBILITY CRITERIA"]),
    )])
    result = extract_trial(client, trial_id="NCT3", source_text="...", cohorts=[Cohort("all")])
    row = result.rows[0]
    assert row.cancer_type == "solid tumour AND NOT(melanoma) [TITLE; ELIGIBILITY CRITERIA]"
    assert row.prior_therapy == "NOT(prior EGFR TKI) [ELIGIBILITY CRITERIA]"


def test_gating_reviewer_triggers_refine_then_passes():
    client = _ScriptedClient(
        extractions=[
            _extraction(ExtractedRow(cohort="trial-wide", cancer_type="lung cancer", cancer_type_sources=["TITLE"])),
            _extraction(ExtractedRow(cohort="trial-wide", cancer_type="NSCLC", cancer_type_sources=["ELIGIBILITY CRITERIA"])),
        ],
        verdicts={"cancer_type": [JudgeVerdict(faithful=False, problems=["too vague"]), JudgeVerdict(faithful=True)]},
    )
    result = extract_trial(client, trial_id="NCT4", source_text="...", cohorts=[Cohort("all")], max_attempts=3)
    assert result.faithful and result.attempts == 2 and client.extractor_calls == 2
    assert result.rows[0].cancer_type == "NSCLC [ELIGIBILITY CRITERIA]"


def test_drug_reviewer_is_advisory_not_gating():
    client = _ScriptedClient(
        extractions=[_extraction(ExtractedRow(cohort="trial-wide", cancer_type="NSCLC", cancer_type_sources=["TITLE"]))],
        verdicts={"drug": [JudgeVerdict(faithful=False, problems=["drug mismatch"])]},
    )
    result = extract_trial(client, trial_id="NCT5", source_text="...",
                                 cohorts=[Cohort("all", drug="x")], max_attempts=3)
    assert result.faithful and result.attempts == 1  # advisory does not loop
    assert any("[drug] drug mismatch" in p for p in result.problems)


def test_empty_extraction_exhausts_attempts_before_panel():
    client = _ScriptedClient(extractions=[_extraction()])
    result = extract_trial(client, trial_id="NCT6", source_text="...", cohorts=[Cohort("all")], max_attempts=2)
    assert not result.faithful and result.attempts == 2
    assert any("no eligibility rows" in p for p in result.problems)


# --- ANZCTR path (single eligibility cohort; regimes from the drug agent — spec §6.1) --------------- #
def test_anzctr_single_eligibility_cohort_intervention_regime():
    """ANZCTR has ONE eligibility cohort; the regime axis is the INTERVENTIONS drug set (no cohort-detection)."""
    client = _ScriptedClient(
        extractions=[_extraction(ExtractedRow(cohort="trial-wide", cancer_type="colorectal cancer",
                                              cancer_type_sources=["INCLUSION CRITERIA"]))],
        intervention_drugs=["capecitabine", "bevacizumab"],
    )
    result = extract_trial(client, trial_id="ACTRN1", source_text="...", cohorts=None)
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.cohort == "intervention" and row.arm_type == "EXPERIMENTAL"
    assert row.cancer_type == "colorectal cancer [INCLUSION CRITERIA]"
    assert row.drug == "capecitabine; bevacizumab [INTERVENTIONS]"


def test_anzctr_comparator_drug_becomes_its_own_control_regime():
    """A comparator that names a real drug is its own control regime (arm_type flags control, decision 7);
    the single trial-wide eligibility is shared across both regimes — same data structure as CTGov."""
    client = _ScriptedClient(
        extractions=[_extraction(ExtractedRow(cohort="trial-wide", cancer_type="NSCLC",
                                              cancer_type_sources=["HEALTH CONDITION"]))],
        intervention_drugs=["osimertinib"],
        comparator_drugs=["chemotherapy"],
    )
    result = extract_trial(client, trial_id="ACTRN2", source_text="...", cohorts=None)
    by_cohort = {r.cohort: r for r in result.rows}
    assert set(by_cohort) == {"intervention", "comparator"}
    assert (by_cohort["intervention"].arm_type, by_cohort["intervention"].drug) == \
        ("EXPERIMENTAL", "osimertinib [INTERVENTIONS]")
    assert (by_cohort["comparator"].arm_type, by_cohort["comparator"].drug) == \
        ("ACTIVE_COMPARATOR", "chemotherapy [COMPARATOR]")
    assert all(r.cancer_type == "NSCLC [HEALTH CONDITION]" for r in result.rows)  # shared eligibility


def test_extract_anzctr_drugs_doer_reviewer():
    """ANZCTR drug identification is a doer->reviewer step (spec §6.1); returns intervention + comparator drugs."""
    from aus_trial_universe.agentic.tasks.eligibility.extraction.workflow import extract_anzctr_drugs
    client = _ScriptedClient(extractions=[], intervention_drugs=["capecitabine", "bevacizumab"],
                             comparator_drugs=["chemotherapy"])
    dr = extract_anzctr_drugs(client, "...trial text...", use_reviewer=True)   # fake reviewer -> faithful
    assert dr.intervention_drugs == ["capecitabine", "bevacizumab"] and dr.comparator_drugs == ["chemotherapy"]
    dr2 = extract_anzctr_drugs(client, "...trial text...", use_reviewer=False)  # reviewer skipped
    assert dr2.intervention_drugs == ["capecitabine", "bevacizumab"]


def test_anzctr_no_drugs_falls_back_to_single_regime():
    client = _ScriptedClient(
        extractions=[_extraction(ExtractedRow(cohort="trial-wide", cancer_type="melanoma",
                                              cancer_type_sources=["HEALTH CONDITION"]))],
    )
    result = extract_trial(client, trial_id="ACTRN3", source_text="...", cohorts=None)
    assert len(result.rows) == 1 and result.rows[0].cohort == "all"  # raw label (join key); no "(all)" transform
    assert result.rows[0].cancer_type == "melanoma [HEALTH CONDITION]"


# --- over-enumeration fixes ------------------------------------------------- #
def test_commutative_duplicate_and_cells_are_deduped():
    """`A AND B` and `B AND A` are the same conjunction — a forward generator can emit both orderings of a
    fabricated pair; _dedup_rows canonicalizes AND-terms so the commutative twin collapses (deterministic guard)."""
    client = _ScriptedClient(extractions=[_extraction(
        ExtractedRow(cohort="trial-wide", gene_alteration="MYCN amp AND MYCL amp", gene_alteration_sources=["X"]),
        ExtractedRow(cohort="trial-wide", gene_alteration="MYCL amp AND MYCN amp", gene_alteration_sources=["X"]),
    )])
    result = extract_trial(client, trial_id="NCT1", source_text="...", cohorts=[Cohort(label="all")], use_judge=False)
    assert len(result.rows) == 1                                   # commutative twin collapsed
    assert result.rows[0].gene_alteration == "MYCN amp AND MYCL amp [X]"   # first-seen text preserved


def test_enumeration_reviewer_gates_then_refine_splits_fabricated_conjunction():
    """The in-loop enumeration reviewer sees the ASSEMBLED table; an OR->AND fabrication gates the refine loop, and
    the extractor's revision (splitting into OR-rows) then passes — the vantage point is inside the loop."""
    fabricated = _extraction(
        ExtractedRow(cohort="trial-wide", gene_alteration="MYCN amp AND MYCL amp", gene_alteration_sources=["X"]))
    split = _extraction(
        ExtractedRow(cohort="trial-wide", gene_alteration="MYCN amp", gene_alteration_sources=["X"]),
        ExtractedRow(cohort="trial-wide", gene_alteration="MYCL amp", gene_alteration_sources=["X"]))
    client = _ScriptedClient(
        extractions=[fabricated, split],
        verdicts={"enumeration": [JudgeVerdict(faithful=False, problems=[
            "gene_alteration ANDs the OR-alternatives MYCN/MYCL — split into separate rows"]),
            JudgeVerdict(faithful=True)]},
    )
    result = extract_trial(client, trial_id="NCT1", source_text="...", cohorts=[Cohort(label="all")])
    assert result.attempts == 2                                    # gated once, fixed on the revision
    assert {r.gene_alteration for r in result.rows} == {"MYCN amp [X]", "MYCL amp [X]"}


# --- ANZCTR drug-extractor tightening (non-drug modality backstop + prompt exclusions) --------------- #
def test_drop_non_drug_modalities_filters_only_exact_modalities():
    """Deterministic backstop: enumerable non-drug modalities are removed (paren-stripped, case/space-insensitive),
    while real drug names — even ones whose text merely contains a modality word — are kept (no substring clipping)."""
    from aus_trial_universe.agentic.tasks.eligibility.extraction.workflow import _drop_non_drug_modalities
    got = _drop_non_drug_modalities([
        "Total Body Irradiation (TBI)", "TBI", "Surgery", "observation", "Placebo", "best supportive care",
        "Fludarabine", "Melphalan", "Radium-223 dichloride", "radiosensitising agent XYZ",
    ])
    assert got == ["Fludarabine", "Melphalan", "Radium-223 dichloride", "radiosensitising agent XYZ"]


def test_extract_anzctr_drugs_strips_non_drug_modalities():
    """The doer->reviewer output is passed through the modality backstop: TBI/Surgery/Placebo never survive as
    drugs, while the real conditioning drugs do (regression guard for the ANZCTR leak fix)."""
    from aus_trial_universe.agentic.tasks.eligibility.extraction.workflow import extract_anzctr_drugs
    client = _ScriptedClient(
        extractions=[],
        intervention_drugs=["Fludarabine", "Melphalan", "Total Body Irradiation", "Surgery"],
        comparator_drugs=["Placebo", "chemotherapy"],
    )
    dr = extract_anzctr_drugs(client, "...trial text...", use_reviewer=True)
    assert dr.intervention_drugs == ["Fludarabine", "Melphalan"]   # TBI + Surgery dropped
    assert dr.comparator_drugs == ["chemotherapy"]                 # Placebo dropped


def test_anzctr_drug_extractor_prompt_decisions_present():
    """Prompt-only tightening (2026-07-17) — guard the exclusion rules so a future edit can't silently drop them.
    Grounded in observed ANZCTR leaks: TBI (modality), AL (disease abbrev), nizatidine (title-only), PA (fragment)."""
    from aus_trial_universe.agentic.tasks.eligibility.extraction.agents import (
        DRUG_EXTRACTOR_INSTRUCTIONS as D, DRUG_EXTRACTOR_REVIEWER_INSTRUCTIONS as R)
    for text in (D, R):
        low = text.lower()
        assert "total body irradiation" in low and "surgery" in low          # non-drug modalities excluded
        assert "al amyloidosis" in low                                        # disease / condition abbreviation
        assert "concomitant" in low and "prior" in low                       # prior/concomitant meds excluded
    # doer: INTERVENTIONS primary but title is also consulted; opaque codes -> named agent
    assert "PRIMARY" in D and "TITLE" in D and "opaque" in D.lower()
    # reviewer: symmetric — a title-only drug still counts; stray fragments flagged
    assert "title counts" in R.lower() and "fragment" in R.lower()
