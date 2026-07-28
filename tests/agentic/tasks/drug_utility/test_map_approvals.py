"""Symmetric-match vocab for drug regulatory approvals (fake client, no API).

Exercises the new map-approvals pass:
- the biomarker splitter (routing into gene/signature/expression + refine on an unfaithful reviewer),
- the orchestrator: cancer_type -> OncoTree, biomarker split -> gene/signature finding-model, expression kept free
  text, empty biomarker skipped,
- cross-domain SEEDING from the trial FINAL maps (a shared value is reused, never sent to a mapper),
- the ADDITIVE persistence (save_approval_maps writes only the 2 new tables; a plain store emits none).
"""
from __future__ import annotations

import aus_trial_universe.agentic.export as export_mod
from aus_trial_universe.agentic.core.client import LlmResult
from aus_trial_universe.agentic.tasks.drug_utility import map_approvals as MA
from aus_trial_universe.agentic.tasks.drug_utility.schema import (
    BiomarkerSplit,
    DrugRegulatoryApproval,
    ReviewVerdict as DrugReviewVerdict,
)
from aus_trial_universe.agentic.tasks.drug_utility.store import DrugRefStore
from aus_trial_universe.agentic.tasks.eligibility.mapping.schema import (
    FindingModelMapping,
    GroupReconciliation,
    OncotreeMapping,
    ReconciledMember,
    ReviewVerdict as MapReviewVerdict,
)

_BRAF_FM = "SmallVariant[gene=BRAF & transcriptImpact.hgvsProteinImpact=p.V600E]"
_MSI_FM = "MicrosatelliteStability[PurpleMicrosatelliteStatus=MSI]"


class _FakeClient:
    """Routes each agent call by output schema. Policies are dicts keyed by the subject (first line of user_input,
    or the phrase for the splitter); reviewers say faithful. Tracks the subjects each mapper was ASKED to map so a
    test can assert a seeded value never reached a mapper. `unfaithful_once` makes the split reviewer reject the
    first attempt to exercise refine."""

    def __init__(self, *, split=None, onco=None, fm=None, unfaithful_once=False, reconcile_to="BREAST"):
        self.split = split or {}
        self.onco = onco or {}
        self.fm = fm or {}
        self.unfaithful_once = unfaithful_once
        self.reconcile_to = reconcile_to      # code every member of a flagged group is unified to
        self.asked = {"onco": [], "fm": [], "split": [], "review": 0, "reconcile": 0}
        self._split_seen: set[str] = set()

    def _r(self, obj):
        return LlmResult(obj, "fake", "{}", False, 1)

    @staticmethod
    def _subject(user_input: str) -> str:
        return user_input.splitlines()[0].strip() if user_input else ""

    def parse(self, output_schema, *, instructions, user_input, model=None,
              temperature=None, seed=None, max_completion_tokens=None, **_):
        if output_schema is OncotreeMapping:
            subj = self._subject(user_input)
            self.asked["onco"].append(subj)
            code = self.onco.get(subj, "")
            return self._r(OncotreeMapping(oncotree_name=code, oncotree_code=code))
        if output_schema is FindingModelMapping:
            subj = self._subject(user_input)
            self.asked["fm"].append(subj)
            return self._r(FindingModelMapping(finding_model=self.fm.get(subj, "")))
        if output_schema is BiomarkerSplit:
            phrase = self._subject(user_input).replace("Biomarker phrase:", "").strip()
            self.asked["split"].append(phrase)
            g, s, m = self.split.get(phrase, ("", "", phrase))
            return self._r(BiomarkerSplit(gene_alteration=g, molecular_signature=s, molecular_biomarker=m))
        if output_schema is DrugReviewVerdict:                          # the biomarker-split reviewer
            self.asked["review"] += 1
            phrase = self._subject(user_input)
            if self.unfaithful_once and phrase not in self._split_seen:
                self._split_seen.add(phrase)
                return self._r(DrugReviewVerdict(faithful=False, problems=["route it again"]))
            return self._r(DrugReviewVerdict(faithful=True))
        if output_schema is GroupReconciliation:                        # the cancer_type Step-2 adjudicator
            self.asked["reconcile"] += 1
            import re
            members = re.findall(r'- "(.+?)"  ->', user_input)
            return self._r(GroupReconciliation(
                members=[ReconciledMember(input=m, final_value=self.reconcile_to) for m in members]))
        if output_schema is MapReviewVerdict:                           # the oncotree / finding-model reviewers
            self.asked["review"] += 1
            return self._r(MapReviewVerdict(faithful=True))
        raise AssertionError(f"unexpected schema {output_schema}")

    research = parse


def _store_with_indications(rows):
    """rows = [(cancer_type, biomarker), ...] -> a store holding them under one canonical."""
    s = DrugRefStore()
    s.put_indications("rxcui:1", [
        DrugRegulatoryApproval(canonical_id="rxcui:1", indication_id=str(i + 1), cancer_type=ct, biomarker=bm)
        for i, (ct, bm) in enumerate(rows)])
    return s


# --------------------------------------------------------------------------- #
# The biomarker splitter
# --------------------------------------------------------------------------- #
def test_split_routes_into_three_buckets():
    client = _FakeClient(split={
        "HER2-positive": ("", "", "HER2-positive"),
        "BRAF V600E mutation": ("BRAF V600E mutation", "", ""),
        "MSI-H": ("", "MSI-H", ""),
    })
    out = MA.split_biomarkers(client, ["HER2-positive", "BRAF V600E mutation", "MSI-H"])
    assert out["HER2-positive"].molecular_biomarker == "HER2-positive"
    assert out["BRAF V600E mutation"].gene_alteration == "BRAF V600E mutation"
    assert out["MSI-H"].molecular_signature == "MSI-H"


def test_split_dedups_and_skips_empty():
    client = _FakeClient()
    out = MA.split_biomarkers(client, ["CD20-positive", "CD20-positive", "", "  "])
    assert list(out) == ["CD20-positive"]                          # deduped, empties dropped
    assert client.asked["split"] == ["CD20-positive"]              # only the distinct value hit the doer


def test_split_refines_on_unfaithful_reviewer():
    client = _FakeClient(split={"BRAF V600E mutation": ("BRAF V600E mutation", "", "")}, unfaithful_once=True)
    out = MA.split_biomarkers(client, ["BRAF V600E mutation"])
    assert out["BRAF V600E mutation"].gene_alteration == "BRAF V600E mutation"
    assert client.asked["split"].count("BRAF V600E mutation") >= 2  # retried after the reject


# --------------------------------------------------------------------------- #
# The orchestrator
# --------------------------------------------------------------------------- #
def test_map_approvals_assembles_both_tables():
    store = _store_with_indications([
        ("breast cancer", "HER2-positive"),      # expression -> free text (no finding-model)
        ("NSCLC", "BRAF V600E mutation"),         # gene -> finding-model
        ("melanoma", ""),                         # empty biomarker -> no biomarker-map row
        ("colorectal cancer", "MSI-H"),           # signature -> finding-model
    ])
    client = _FakeClient(
        split={"HER2-positive": ("", "", "HER2-positive"),
               "BRAF V600E mutation": ("BRAF V600E mutation", "", ""),
               "MSI-H": ("", "MSI-H", "")},
        onco={"breast cancer": "BREAST", "NSCLC": "NSCLC", "melanoma": "SKCM", "colorectal cancer": "COAD"},
        fm={"BRAF V600E mutation": _BRAF_FM, "MSI-H": _MSI_FM})
    summary = MA.map_approvals(client, store, seed=False)

    ct = store.approval_cancer_type_map
    assert ct["breast cancer"].oncotree_code == "BREAST" and ct["NSCLC"].oncotree_code == "NSCLC"
    # no divergent group here -> FINAL == Step-1 code, and oncotree_name renders from the FINAL code
    assert ct["breast cancer"].oncotree_code_FINAL == "BREAST" and ct["breast cancer"].oncotree_name == "Breast"
    assert summary.ct_total == 4 and summary.ct_seeded == 0 and summary.ct_mapped == 4
    assert summary.ct_reconciled_groups == 0

    bm = store.approval_biomarker_map
    assert set(bm) == {"HER2-positive", "BRAF V600E mutation", "MSI-H"}   # empty biomarker skipped
    assert bm["HER2-positive"].molecular_biomarker == "HER2-positive"
    assert bm["HER2-positive"].gene_alteration_findingmodel == ""          # expression has no finding-model
    assert bm["BRAF V600E mutation"].gene_alteration_findingmodel == _BRAF_FM
    assert bm["MSI-H"].molecular_signature_findingmodel == _MSI_FM
    assert summary.bm_with_gene == 1 and summary.bm_with_signature == 1 and summary.bm_with_expression == 1


def test_cancer_type_reconciliation_unifies_divergent_via_shared_logic():
    """Two phrasings of ONE concept that mapped to DIFFERENT codes are reconciled to one FINAL code (Step-1
    codes preserved). Proves the reused eligibility `reconcile_column` runs on the drug side."""
    store = _store_with_indications([("breast cancer", ""), ("metastatic breast cancer", "")])
    client = _FakeClient(onco={"breast cancer": "BREAST", "metastatic breast cancer": "IDC"}, reconcile_to="BREAST")
    summary = MA.map_approvals(client, store, seed=False)

    ct = store.approval_cancer_type_map
    assert summary.ct_reconciled_groups == 1 and client.asked["reconcile"] >= 1
    assert ct["breast cancer"].oncotree_code == "BREAST"                 # Step-1 preserved
    assert ct["metastatic breast cancer"].oncotree_code == "IDC"         # Step-1 preserved
    assert ct["breast cancer"].oncotree_code_FINAL == "BREAST"           # unified
    assert ct["metastatic breast cancer"].oncotree_code_FINAL == "BREAST"


def test_write_mapped_approvals_joined_view(tmp_path):
    store = _store_with_indications([("NSCLC", "BRAF V600E mutation")])
    client = _FakeClient(split={"BRAF V600E mutation": ("BRAF V600E mutation", "", "")},
                         onco={"NSCLC": "NSCLC"}, fm={"BRAF V600E mutation": _BRAF_FM})
    MA.map_approvals(client, store, seed=False)
    path = MA.write_mapped_approvals(store, tmp_path)
    assert path.name == "mapped_drug_regulatory_approval.tsv"
    import csv as _csv
    rows = list(_csv.DictReader(open(path), delimiter="\t"))
    assert len(rows) == 1
    r = rows[0]
    assert r["cancer_type"] == "NSCLC" and r["oncotree_code_FINAL"] == "NSCLC"
    assert r["biomarker"] == "BRAF V600E mutation" and r["gene_alteration_findingmodel"] == _BRAF_FM
    assert set(MA.MAPPED_APPROVALS_COLUMNS) == set(r.keys())


def test_seeding_reuses_trial_final_and_skips_mapper(monkeypatch):
    """A cancer_type / gene value shared with the trial FINAL maps reuses the FINAL code and never reaches a mapper."""
    def fake_read_map(path, key_col, val_col):
        if key_col == "cancer_type":
            return {"breast cancer": "BREAST"}
        if key_col == "gene_alteration":
            return {"BRAF V600E mutation": _BRAF_FM}
        return {}
    monkeypatch.setattr(export_mod, "_read_map", fake_read_map)

    store = _store_with_indications([("breast cancer", "BRAF V600E mutation"), ("NSCLC", "MSI-H")])
    client = _FakeClient(
        split={"BRAF V600E mutation": ("BRAF V600E mutation", "", ""), "MSI-H": ("", "MSI-H", "")},
        onco={"NSCLC": "NSCLC"}, fm={"MSI-H": _MSI_FM})
    summary = MA.map_approvals(client, store, seed=True)

    assert store.approval_cancer_type_map["breast cancer"].oncotree_code == "BREAST"
    assert store.approval_biomarker_map["BRAF V600E mutation"].gene_alteration_findingmodel == _BRAF_FM
    assert summary.ct_seeded == 1 and summary.seeded_fm == 1
    assert "breast cancer" not in client.asked["onco"]                     # seeded -> no oncotree call
    assert "BRAF V600E mutation" not in client.asked["fm"]                 # seeded gene cell -> no finding-model call
    assert "NSCLC" in client.asked["onco"]                                 # novel -> mapped


# --------------------------------------------------------------------------- #
# Additive persistence
# --------------------------------------------------------------------------- #
def test_save_approval_maps_writes_only_two_tables(tmp_path):
    store = _store_with_indications([("breast cancer", "HER2-positive")])
    client = _FakeClient(split={"HER2-positive": ("", "", "HER2-positive")}, onco={"breast cancer": "BREAST"})
    MA.map_approvals(client, store, seed=False)
    vdir = store.save_approval_maps(tmp_path)
    written = sorted(p.name for p in vdir.iterdir())
    assert written == ["approval_biomarker_map.tsv", "approval_cancer_type_map.tsv"]


def test_plain_store_save_emits_no_approval_files(tmp_path):
    """A store with no approval maps (a plain drug build) must not emit empty approval-map files."""
    DrugRefStore().save(tmp_path)
    names = {p.name for p in (tmp_path / "current_version").iterdir()}
    assert "approval_cancer_type_map.tsv" not in names and "approval_biomarker_map.tsv" not in names


def test_round_trip_load(tmp_path):
    store = _store_with_indications([("NSCLC", "MSI-H")])
    client = _FakeClient(split={"MSI-H": ("", "MSI-H", "")}, onco={"NSCLC": "NSCLC"}, fm={"MSI-H": _MSI_FM})
    MA.map_approvals(client, store, seed=False)
    store.save_approval_maps(tmp_path)
    reloaded = DrugRefStore.load(tmp_path)
    assert reloaded.approval_cancer_type_map["NSCLC"].oncotree_code == "NSCLC"
    assert reloaded.approval_biomarker_map["MSI-H"].molecular_signature_findingmodel == _MSI_FM


def test_splitter_prompt_decisions_present():
    from aus_trial_universe.agentic.tasks.drug_utility.agents import (
        BIOMARKER_SPLITTER_INSTRUCTIONS as S, BIOMARKER_SPLIT_REVIEWER_INSTRUCTIONS as R)
    assert "gene_alteration" in S and "molecular_signature" in S and "molecular_biomarker" in S
    assert "amplification" in S and "MSI-H" in S                    # the HER2 / MSI edge rules
    assert "BCR-ABL" in S                                           # Ph+ -> gene_alteration
    assert "faithful" in R.lower()
