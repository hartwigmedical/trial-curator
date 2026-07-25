"""Tests for the eligibility-extraction workflow (fake client, no LLM).

Two sub-stages: I-a RAW (verbatim spans -> arm_eligibility_raw) and I-b INTERPRET (spans -> DNF logic).
Covers: raw-table assembly (trial-wide replication + per-fragment source), regime-aware interpretation,
cross-product distribution (trial-wide x regime-specific), inline NOT(), the reviewer panel (gating vs
advisory), the refine loop, and the ANZCTR path (single eligibility cohort; regimes from the drug agent).
Interpreted cells carry NO source tag (provenance lives in the raw table); only the drug cell keeps its tag.
"""
from __future__ import annotations

from aus_trial_universe.agentic.core.client import LlmResult
from aus_trial_universe.agentic.tasks.eligibility.extraction.agents import (
    ENUMERATION_REVIEWER_INSTRUCTIONS,
    REVIEWERS,
)
from aus_trial_universe.agentic.tasks.eligibility.extraction.schema import (
    EligibilityExtraction,
    ExtractedRow,
    JudgeVerdict,
    RawExtraction,
    RawFragment,
)
from aus_trial_universe.agentic.tasks.eligibility.extraction.workflow import (
    Cohort,
    extract_trial,
)
# ANZCTR arm identification (drug extractor + regime derivation) is a path-neutral shared module now.
from aus_trial_universe.agentic.tasks.shared.agents import DrugExtraction, RegimeVerdict


def _reviewer_key(instructions: str) -> str | None:
    if instructions == ENUMERATION_REVIEWER_INSTRUCTIONS:
        return "enumeration"
    for spec in REVIEWERS:
        if spec.instructions == instructions:
            return spec.key
    return None


_DEFAULT_RAW = [RawFragment(criterion="cancer_type", scope="trial-wide", text="cancer", source="TITLE")]


class _ScriptedClient:
    """Fake LlmClient.parse dispatching canned outputs by schema.

    extractions: one EligibilityExtraction per interpreter call (attempt). verdicts: {reviewer_key ->
    [JudgeVerdict,...]}, consumed in order per reviewer (default faithful=True; the raw reviewer's key is
    unmatched -> default faithful). raw_fragments: the RawExtraction served to stage I-a. drugs: ANZCTR path.
    """

    def __init__(self, extractions, verdicts=None, intervention_drugs=None, comparator_drugs=None,
                 raw_fragments=None):
        self._extractions = extractions
        self._verdicts = {k: list(v) for k, v in (verdicts or {}).items()}
        self._vcount = {k: 0 for k in self._verdicts}
        self._intervention_drugs = intervention_drugs or []
        self._comparator_drugs = comparator_drugs or []
        self._raw_fragments = list(raw_fragments) if raw_fragments is not None else list(_DEFAULT_RAW)
        self.extractor_calls = 0   # counts INTERPRETER (EligibilityExtraction) calls

    def parse(self, output_schema, *, instructions, user_input, model=None,
              temperature=None, seed=None, max_completion_tokens=None, **_):
        if output_schema is RawExtraction:
            return LlmResult(RawExtraction(fragments=list(self._raw_fragments)), "fake", "{}", False, 1)
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
        if output_schema is RegimeVerdict:   # the shared ANZCTR drug reviewer's verdict
            return LlmResult(RegimeVerdict(faithful=True), "fake", "{}", False, 1)
        raise AssertionError(f"unexpected schema {output_schema}")


def _extraction(*rows):
    return EligibilityExtraction(rows=list(rows))


# --- Stage I-a: raw-table assembly ------------------------------------------ #
def test_arm_raw_assembly_replicates_trialwide_and_tags_source():
    """Per-arm verbatim raw: trial-wide spans replicated onto EVERY arm; cohort-specific spans only on their
    arm; each fragment rendered `text [source]`."""
    client = _ScriptedClient(
        extractions=[_extraction(ExtractedRow(cohort="trial-wide", cancer_type="NSCLC"))],
        raw_fragments=[
            RawFragment(criterion="cancer_type", scope="trial-wide", text="advanced NSCLC", source="CONDITIONS"),
            RawFragment(criterion="gene_alteration", scope="C1", text="EGFR exon 19 del", source="ELIGIBILITY CRITERIA"),
        ],
    )
    result = extract_trial(client, trial_id="NCT1", source_text="...",
                           cohorts=[Cohort(label="Arm A"), Cohort(label="Arm B")], use_judge=False)
    by_arm = {r.arm: r for r in result.arm_raw}
    assert set(by_arm) == {"Arm A", "Arm B"}
    assert by_arm["Arm A"].cancer_type_raw == "advanced NSCLC [CONDITIONS]"
    assert by_arm["Arm B"].cancer_type_raw == "advanced NSCLC [CONDITIONS]"   # trial-wide replicated
    assert by_arm["Arm A"].gene_alteration_raw == "EGFR exon 19 del [ELIGIBILITY CRITERIA]"  # C1 = Arm A
    assert by_arm["Arm B"].gene_alteration_raw == ""


def test_norm_ws_collapses_newlines_and_tabs():
    """Verbatim spans are whitespace-normalized in the raw table so it stays line-oriented TSV (content preserved)."""
    from aus_trial_universe.agentic.tasks.eligibility.extraction.workflow import _norm_ws
    assert _norm_ws("line one\nline two\t  tab") == "line one line two tab"
    assert _norm_ws("  padded \n\n multi ") == "padded multi"


def test_arm_raw_joins_multiple_fragments_with_pipe():
    client = _ScriptedClient(
        extractions=[_extraction(ExtractedRow(cohort="trial-wide", cancer_type="x"))],
        raw_fragments=[
            RawFragment(criterion="cancer_type", scope="trial-wide", text="ovarian cancer", source="CONDITIONS"),
            RawFragment(criterion="cancer_type", scope="trial-wide", text="stage III-IV", source="ELIGIBILITY CRITERIA"),
        ],
    )
    result = extract_trial(client, trial_id="NCT1", source_text="...", cohorts=[Cohort(label="all")], use_judge=False)
    assert result.arm_raw[0].cancer_type_raw == "ovarian cancer [CONDITIONS] | stage III-IV [ELIGIBILITY CRITERIA]"


# --- Stage I-b: interpretation (CTGov path) --------------------------------- #
def test_trialwide_replicates_across_cohorts_with_per_cohort_drug():
    client = _ScriptedClient(extractions=[_extraction(
        ExtractedRow(cohort="trial-wide", cancer_type="NSCLC"),
    )])
    cohorts = [
        Cohort(label="Arm A", drug="drugA", drug_source="INTERVENTIONS MODULE", arm_type="EXPERIMENTAL"),
        Cohort(label="Arm B", drug="drugB", drug_source="INTERVENTIONS MODULE", arm_type="ACTIVE_COMPARATOR"),
    ]
    result = extract_trial(client, trial_id="NCT1", source_text="...", cohorts=cohorts)
    assert result.faithful and result.attempts == 1
    # interpreted eligibility cells carry NO source tag; the drug cell keeps its tag
    assert [(r.cohort, r.arm_type, r.cancer_type, r.drug) for r in result.rows] == [
        ("Arm A", "EXPERIMENTAL", "NSCLC", "drugA [INTERVENTIONS MODULE]"),
        ("Arm B", "ACTIVE_COMPARATOR", "NSCLC", "drugB [INTERVENTIONS MODULE]"),
    ]


def test_cohort_specific_criterion_merges_only_into_its_cohort():
    client = _ScriptedClient(extractions=[_extraction(
        ExtractedRow(cohort="trial-wide", cancer_type="solid tumour"),
        ExtractedRow(cohort="C1", gene_alteration="EGFR mutation"),
    )])
    cohorts = [Cohort(label="Arm A", drug="osi"), Cohort(label="Arm B", drug="chemo")]
    result = extract_trial(client, trial_id="NCT2", source_text="...", cohorts=cohorts)
    by_cohort = {r.cohort: r for r in result.rows}
    assert len(result.rows) == 2
    assert by_cohort["Arm A"].cancer_type == "solid tumour"
    assert by_cohort["Arm A"].gene_alteration == "EGFR mutation"
    assert by_cohort["Arm B"].cancer_type == "solid tumour"
    assert by_cohort["Arm B"].gene_alteration == ""


def test_cohort_wins_on_cancer_type_and_dedup_prevents_blowup():
    """Regression for the NCT04221035 680-row blow-up: staging restated in BOTH trial-wide and cohort scopes
    must not AND into impossible 'Stage A AND Stage B' rows (cohort value wins on cancer_type), and the
    redundant trial-wide OR-rows must de-duplicate away instead of cross-multiplying."""
    client = _ScriptedClient(extractions=[_extraction(
        ExtractedRow(cohort="trial-wide", cancer_type="Stage M NBL"),
        ExtractedRow(cohort="trial-wide", cancer_type="Stage Ms NBL"),
        ExtractedRow(cohort="C1", cancer_type="Stage M NBL", prior_therapy="post-induction"),
        ExtractedRow(cohort="C1", cancer_type="Stage Ms NBL", prior_therapy="post-induction"),
    )])
    cohorts = [Cohort(label="Arm A"), Cohort(label="Arm B")]
    result = extract_trial(client, trial_id="NCT_NBL", source_text="...", cohorts=cohorts, use_judge=False)
    assert all(" AND " not in r.cancer_type for r in result.rows), [r.cancer_type for r in result.rows]
    arm_a = [r for r in result.rows if r.cohort == "Arm A"]
    assert len(arm_a) == 2, [(r.cancer_type, r.prior_therapy) for r in arm_a]
    assert {r.cancer_type for r in arm_a} == {"Stage M NBL", "Stage Ms NBL"}
    assert all(r.prior_therapy == "post-induction" for r in arm_a)
    arm_b = [r for r in result.rows if r.cohort == "Arm B"]
    assert {r.cancer_type for r in arm_b} == {"Stage M NBL", "Stage Ms NBL"}


def test_cohort_wins_preserves_trialwide_cancer_type_exclusion():
    """cohort-wins on cancer_type keeps the cohort's positive type but must NOT drop a trial-wide NOT() carve-out."""
    client = _ScriptedClient(extractions=[_extraction(
        ExtractedRow(cohort="trial-wide", cancer_type="DMG AND NOT(thalamic DMG)"),
        ExtractedRow(cohort="C1", cancer_type="pontine DMG"),
    )])
    result = extract_trial(client, trial_id="NCT_X", source_text="...",
                           cohorts=[Cohort(label="Arm A"), Cohort(label="Arm B")], use_judge=False)
    arm_a = [r for r in result.rows if r.cohort == "Arm A"][0]
    assert arm_a.cancer_type == "pontine DMG AND NOT(thalamic DMG)"
    arm_b = [r for r in result.rows if r.cohort == "Arm B"][0]
    assert arm_b.cancer_type == "DMG AND NOT(thalamic DMG)"


def test_top_level_and_splitter_respects_paren_depth():
    from aus_trial_universe.agentic.tasks.eligibility.extraction.workflow import _top_level_and
    assert _top_level_and("DMG") == ["DMG"]
    assert _top_level_and("") == []
    assert _top_level_and("DMG AND NOT(thalamic DMG)") == ["DMG", "NOT(thalamic DMG)"]
    assert _top_level_and("A AND NOT(x AND y)") == ["A", "NOT(x AND y)"]
    assert _top_level_and("DMG AND NOT(thalamic DMG OR cerebellar DMG) AND NOT(disseminated disease)") == \
        ["DMG", "NOT(thalamic DMG OR cerebellar DMG)", "NOT(disseminated disease)"]


def test_extraction_judgement_prompt_decisions_present():
    """Prompt-only judgement rules can't be caught by fake-client behaviour tests — guard the strings so a
    future prompt edit can't silently drop them. See memory v2-stage2-extraction-decisions."""
    from aus_trial_universe.agentic.tasks.eligibility.extraction.agents import (
        INTERPRETER_INSTRUCTIONS, RAW_EXTRACTOR_INSTRUCTIONS, RAW_REVIEWER_INSTRUCTIONS, REVIEWERS)
    rv = {s.key: s.instructions for s in REVIEWERS}
    # raw stage: verbatim copying + capture exclusions + completeness
    assert "VERBATIM" in RAW_EXTRACTOR_INSTRUCTIONS and "except" in RAW_EXTRACTOR_INSTRUCTIONS
    assert "VERBATIM" in RAW_REVIEWER_INSTRUCTIONS and "MISSING" in RAW_REVIEWER_INSTRUCTIONS.upper()
    # scope tightening (2026-07-24): raw reviewer is lenient + has an explicit out-of-scope list;
    # prior_therapy = anti-cancer treatment history only (safety washouts / supplements are out of scope)
    assert "LENIENT" in RAW_REVIEWER_INSTRUCTIONS and "OUT OF SCOPE" in RAW_REVIEWER_INSTRUCTIONS
    assert "ESCALATION-MODE" in RAW_REVIEWER_INSTRUCTIONS   # raw escalates symmetrically with interpretation
    assert "OUT OF SCOPE" in INTERPRETER_INSTRUCTIONS
    assert "anti-cancer" in INTERPRETER_INSTRUCTIONS.lower() and "supplement" in INTERPRETER_INSTRUCTIONS.lower()
    # interpretation reviewers share the scope clause (resolves the "history of other malignancy" oscillation)
    assert "OUT OF SCOPE" in rv["cancer_type"] and "other / second / prior malignancy" in rv["cancer_type"]
    # molecular_biomarker = expression/receptor status only; disease-burden/measurability levels out of scope
    assert "disease-BURDEN" in INTERPRETER_INSTRUCTIONS
    # EXCLUSION-source spans become NOT() in the interpretation
    assert "EXCLUSION CRITERIA" in INTERPRETER_INSTRUCTIONS
    # interpreter: (1) capture tumour-type exclusions as NOT(); (2) no subsuming over-enumeration; (3) one scope
    assert "except" in INTERPRETER_INSTRUCTIONS and "excluding" in INTERPRETER_INSTRUCTIONS
    assert "refractory to standard therapy" in INTERPRETER_INSTRUCTIONS
    assert "EXACTLY ONE scope" in INTERPRETER_INSTRUCTIONS
    assert "excluded tumour" in rv["cancer_type"].lower() or "tumour-type exclusion" in rv["cancer_type"].lower()
    assert "over-enumeration" in rv["prior_therapy"].lower()
    assert "subsuming" in rv["structural"].lower()
    # drop criteria for text-cohorts NOT in the fixed regime list (closed cohorts)
    assert "DROP" in INTERPRETER_INSTRUCTIONS and "closed" in INTERPRETER_INSTRUCTIONS.lower()
    assert "closed" in rv["structural"].lower() and "drop" in rv["structural"].lower()


def test_not_negation_passthrough():
    """NOT() exclusions survive interpretation into the DNF cell (no source tag on interpreted cells)."""
    client = _ScriptedClient(extractions=[_extraction(
        ExtractedRow(cohort="trial-wide", cancer_type="solid tumour AND NOT(melanoma)",
                     prior_therapy="NOT(prior EGFR TKI)"),
    )])
    result = extract_trial(client, trial_id="NCT3", source_text="...", cohorts=[Cohort("all")])
    row = result.rows[0]
    assert row.cancer_type == "solid tumour AND NOT(melanoma)"
    assert row.prior_therapy == "NOT(prior EGFR TKI)"


def test_gating_reviewer_triggers_refine_then_passes():
    client = _ScriptedClient(
        extractions=[
            _extraction(ExtractedRow(cohort="trial-wide", cancer_type="lung cancer")),
            _extraction(ExtractedRow(cohort="trial-wide", cancer_type="NSCLC")),
        ],
        verdicts={"cancer_type": [JudgeVerdict(faithful=False, problems=["too vague"]), JudgeVerdict(faithful=True)]},
    )
    result = extract_trial(client, trial_id="NCT4", source_text="...", cohorts=[Cohort("all")], max_attempts=3)
    assert result.faithful and result.attempts == 2 and client.extractor_calls == 2
    assert result.rows[0].cancer_type == "NSCLC"


class _CapClient(_ScriptedClient):
    """Captures the doer (interpreter) prompts so tests can assert what reached the writer."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.doer_inputs = []

    def parse(self, output_schema, *, user_input, **kw):
        if output_schema is EligibilityExtraction:
            self.doer_inputs.append(user_input)
        return super().parse(output_schema, user_input=user_input, **kw)


def test_suggested_fix_only_fires_as_last_resort_on_a_cycle():
    """The reviewer's suggested_fix is NOT used on ordinary repairs — it kicks in ONLY when the doer cycles
    (repeats a problem-set), via refine's last-resort stuck_repair, which re-reviews in ESCALATION-MODE and
    threads the concrete fix into one final doer repair. Preserves the one-writer / always-re-checked invariant."""
    client = _CapClient(
        extractions=[
            _extraction(ExtractedRow(cohort="trial-wide", cancer_type="lung cancer")),   # attempt 1
            _extraction(ExtractedRow(cohort="trial-wide", cancer_type="lung cancer")),   # attempt 2 (same -> cycle)
            _extraction(ExtractedRow(cohort="trial-wide", cancer_type="NSCLC")),         # attempt 3 (after the fix)
        ],
        verdicts={"cancer_type": [
            JudgeVerdict(faithful=False, problems=["too vague"]),                         # attempt 1 review
            JudgeVerdict(faithful=False, problems=["too vague"]),                         # attempt 2 review (cycle)
            JudgeVerdict(faithful=False, problems=["too vague"], suggested_fix="cancer_type = NSCLC"),  # ESCALATION re-review
            JudgeVerdict(faithful=True)]},                                                # attempt 3 review passes
    )
    result = extract_trial(client, trial_id="NCT9", source_text="...", cohorts=[Cohort("all")], max_attempts=3)
    assert result.rows[0].cancer_type == "NSCLC"
    # ordinary repair (attempt 2) got critique only; the fix reached the doer ONLY via the escalation repair
    assert not any("SUGGESTED FIX" in inp for inp in client.doer_inputs[:2])
    assert any("SUGGESTED FIX: cancer_type = NSCLC" in inp for inp in client.doer_inputs)


def test_drug_reviewer_is_advisory_not_gating():
    client = _ScriptedClient(
        extractions=[_extraction(ExtractedRow(cohort="trial-wide", cancer_type="NSCLC"))],
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
    client = _ScriptedClient(
        extractions=[_extraction(ExtractedRow(cohort="trial-wide", cancer_type="colorectal cancer"))],
        intervention_drugs=["capecitabine", "bevacizumab"],
    )
    result = extract_trial(client, trial_id="ACTRN1", source_text="...", cohorts=None)
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.cohort == "intervention" and row.arm_type == "EXPERIMENTAL"
    assert row.cancer_type == "colorectal cancer"
    assert row.drug == "capecitabine; bevacizumab [INTERVENTIONS]"


def test_anzctr_comparator_drug_becomes_its_own_control_regime():
    client = _ScriptedClient(
        extractions=[_extraction(ExtractedRow(cohort="trial-wide", cancer_type="NSCLC"))],
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
    assert all(r.cancer_type == "NSCLC" for r in result.rows)  # shared eligibility


def test_extract_anzctr_drugs_doer_reviewer():
    from aus_trial_universe.agentic.tasks.shared.cohorts import extract_anzctr_drugs
    client = _ScriptedClient(extractions=[], intervention_drugs=["capecitabine", "bevacizumab"],
                             comparator_drugs=["chemotherapy"])
    dr = extract_anzctr_drugs(client, "...trial text...", use_reviewer=True)   # fake reviewer -> faithful
    assert dr.intervention_drugs == ["capecitabine", "bevacizumab"] and dr.comparator_drugs == ["chemotherapy"]
    dr2 = extract_anzctr_drugs(client, "...trial text...", use_reviewer=False)  # reviewer skipped
    assert dr2.intervention_drugs == ["capecitabine", "bevacizumab"]


def test_anzctr_no_drugs_falls_back_to_single_regime():
    client = _ScriptedClient(
        extractions=[_extraction(ExtractedRow(cohort="trial-wide", cancer_type="melanoma"))],
    )
    result = extract_trial(client, trial_id="ACTRN3", source_text="...", cohorts=None)
    assert len(result.rows) == 1 and result.rows[0].cohort == "all"  # raw label (join key); no "(all)" transform
    assert result.rows[0].cancer_type == "melanoma"
    assert result.rows[0].arm_type == "EXPERIMENTAL"   # single-arm fallback is experimental, not blank


# --- over-enumeration fixes ------------------------------------------------- #
def test_commutative_duplicate_and_cells_are_deduped():
    """`A AND B` and `B AND A` are the same conjunction; _dedup_rows canonicalizes AND-terms so the commutative
    twin collapses (deterministic guard)."""
    client = _ScriptedClient(extractions=[_extraction(
        ExtractedRow(cohort="trial-wide", gene_alteration="MYCN amp AND MYCL amp"),
        ExtractedRow(cohort="trial-wide", gene_alteration="MYCL amp AND MYCN amp"),
    )])
    result = extract_trial(client, trial_id="NCT1", source_text="...", cohorts=[Cohort(label="all")], use_judge=False)
    assert len(result.rows) == 1                                   # commutative twin collapsed
    assert result.rows[0].gene_alteration == "MYCN amp AND MYCL amp"   # first-seen text preserved


def test_enumeration_reviewer_gates_then_refine_splits_fabricated_conjunction():
    """The in-loop enumeration reviewer sees the ASSEMBLED table; an OR->AND fabrication gates the refine loop,
    and the interpreter's revision (splitting into OR-rows) then passes."""
    fabricated = _extraction(ExtractedRow(cohort="trial-wide", gene_alteration="MYCN amp AND MYCL amp"))
    split = _extraction(
        ExtractedRow(cohort="trial-wide", gene_alteration="MYCN amp"),
        ExtractedRow(cohort="trial-wide", gene_alteration="MYCL amp"))
    client = _ScriptedClient(
        extractions=[fabricated, split],
        verdicts={"enumeration": [JudgeVerdict(faithful=False, problems=[
            "gene_alteration ANDs the OR-alternatives MYCN/MYCL — split into separate rows"]),
            JudgeVerdict(faithful=True)]},
    )
    result = extract_trial(client, trial_id="NCT1", source_text="...", cohorts=[Cohort(label="all")])
    assert result.attempts == 2                                    # gated once, fixed on the revision
    assert {r.gene_alteration for r in result.rows} == {"MYCN amp", "MYCL amp"}


# --- ANZCTR drug-extractor tightening (non-drug modality backstop + prompt exclusions) --------------- #
def test_drop_non_drug_modalities_filters_only_exact_modalities():
    from aus_trial_universe.agentic.tasks.shared.cohorts import _drop_non_drug_modalities
    got = _drop_non_drug_modalities([
        "Total Body Irradiation (TBI)", "TBI", "Surgery", "observation", "Placebo", "best supportive care",
        "Fludarabine", "Melphalan", "Radium-223 dichloride", "radiosensitising agent XYZ",
    ])
    assert got == ["Fludarabine", "Melphalan", "Radium-223 dichloride", "radiosensitising agent XYZ"]


def test_extract_anzctr_drugs_strips_non_drug_modalities():
    from aus_trial_universe.agentic.tasks.shared.cohorts import extract_anzctr_drugs
    client = _ScriptedClient(
        extractions=[],
        intervention_drugs=["Fludarabine", "Melphalan", "Total Body Irradiation", "Surgery"],
        comparator_drugs=["Placebo", "chemotherapy"],
    )
    dr = extract_anzctr_drugs(client, "...trial text...", use_reviewer=True)
    assert dr.intervention_drugs == ["Fludarabine", "Melphalan"]   # TBI + Surgery dropped
    assert dr.comparator_drugs == ["chemotherapy"]                 # Placebo dropped


def test_anzctr_drug_extractor_prompt_decisions_present():
    from aus_trial_universe.agentic.tasks.shared.agents import (
        DRUG_EXTRACTOR_INSTRUCTIONS as D, DRUG_EXTRACTOR_REVIEWER_INSTRUCTIONS as R)
    for text in (D, R):
        low = text.lower()
        assert "total body irradiation" in low and "surgery" in low          # non-drug modalities excluded
        assert "al amyloidosis" in low                                        # disease / condition abbreviation
        assert "concomitant" in low and "prior" in low                       # prior/concomitant meds excluded
    assert "PRIMARY" in D and "TITLE" in D and "opaque" in D.lower()
    assert "title counts" in R.lower() and "fragment" in R.lower()
