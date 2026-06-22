from __future__ import annotations

import json
from types import SimpleNamespace

from aus_trial_universe.drug_utility_path.trial_drug_curation.enrichment.oncotree import OncotreeEnricher
from aus_trial_universe.drug_utility_path.trial_drug_curation.llm.structured_outputs import TrialDrugRow
from aus_trial_universe.drug_utility_path.trial_drug_curation.pipeline import (
    CurationRunConfig,
    run_trial_drug_curation,
)
from aus_trial_universe.drug_utility_path.trial_drug_curation.utils.schema import TSV_COLUMNS


def _row(**overrides) -> TrialDrugRow:
    base = {column: "" for column in TSV_COLUMNS}
    base["cancerDrug"] = "True"
    base.update(overrides)
    return TrialDrugRow.from_mapping(base)


def test_enrich_row_overwrites_oncotree_from_cancer_types():
    enricher = OncotreeEnricher(lambda cancer_types: f"MAPPED::{cancer_types}")

    row = _row(cancerTypes="Ovarian Cancer", Oncotree="WRONG")
    enriched = enricher.enrich_row(row)

    assert enriched.Oncotree == "MAPPED::Ovarian Cancer"
    assert enriched.cancerTypes == "Ovarian Cancer"  # other columns untouched


def test_blank_cancer_types_maps_to_blank_without_calling_mapper():
    calls: list[str] = []

    def mapper(cancer_types: str) -> str:
        calls.append(cancer_types)
        return "SHOULD_NOT_HAPPEN"

    enricher = OncotreeEnricher(mapper)
    assert enricher.map_cancer_types("   ") == ""
    assert calls == []


def test_mapper_results_are_cached_per_cancer_types():
    calls: list[str] = []

    def mapper(cancer_types: str) -> str:
        calls.append(cancer_types)
        return "OVARY"

    enricher = OncotreeEnricher(mapper)
    enricher.map_cancer_types("Ovarian Cancer")
    enricher.map_cancer_types("Ovarian Cancer")

    assert calls == ["Ovarian Cancer"]  # second lookup served from cache


def test_from_workflow_joins_codes_with_separator():
    fake_workflow = SimpleNamespace(run=lambda text: SimpleNamespace(codes=("OVARY", "UCEC")))
    enricher = OncotreeEnricher.from_workflow(fake_workflow)

    assert enricher.map_cancer_types("Ovarian Cancer | Endometrial Cancer") == "OVARY | UCEC"


def test_enrich_tsv_overwrites_only_the_oncotree_column():
    enricher = OncotreeEnricher(lambda cancer_types: "OVARY | UCEC")
    header = "\t".join(TSV_COLUMNS)
    row = _row(
        trialId="NCT1",
        drugName="DRUG",
        drugRegime="DRUG",
        trialArm="Experimental",
        cancerTypes="Ovarian Cancer | Endometrial Cancer",
        Oncotree="HALLUCINATED",
    )
    tsv = header + "\n" + "\t".join(row.to_tsv_record()) + "\n"

    enriched = enricher.enrich_tsv(tsv)

    lines = enriched.strip().splitlines()
    assert lines[0] == header
    cells = dict(zip(TSV_COLUMNS, lines[1].split("\t")))
    assert cells["Oncotree"] == "OVARY | UCEC"
    assert cells["cancerTypes"] == "Ovarian Cancer | Endometrial Cancer"
    assert cells["drugName"] == "DRUG"


def test_pipeline_uses_agentic_enricher_for_oncotree_column(tmp_path):
    ctgov_input = tmp_path / "ctgov_input.json"
    ctgov_input.write_text(
        json.dumps(
            [
                {
                    "protocolSection": {
                        "identificationModule": {"nctId": "NCT00000001"},
                        "armsInterventionsModule": {
                            "interventions": [{"name": "SELECTED_DRUG"}]
                        },
                    }
                }
            ]
        ),
        encoding="utf-8",
    )
    output_path = tmp_path / "curation.tsv"

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def curate_tsv(self, *, user_prompt):
            row = _row(
                trialId="NCT00000001",
                drugName="SELECTED_DRUG",
                drugRegime="SELECTED_DRUG",
                trialArm="Experimental",
                cancerTypes="Ovarian Cancer",
                Oncotree="LLM_GUESS",
            )
            return "\t".join(TSV_COLUMNS) + "\n" + "\t".join(row.to_tsv_record()) + "\n"

    def enricher_factory(oncotree_yaml: str) -> OncotreeEnricher:
        return OncotreeEnricher(lambda cancer_types: f"AGENTIC::{cancer_types}")

    result = run_trial_drug_curation(
        CurationRunConfig(
            trial_ids=["NCT00000001"],
            output_path=output_path,
            ctgov_input=ctgov_input,
        ),
        client_factory=FakeClient,
        oncotree_enricher_factory=enricher_factory,
    )

    output_text = result.output_path.read_text(encoding="utf-8")
    cells = dict(zip(TSV_COLUMNS, output_text.strip().splitlines()[1].split("\t")))
    assert cells["Oncotree"] == "AGENTIC::Ovarian Cancer"  # LLM guess overwritten
    assert cells["cancerTypes"] == "Ovarian Cancer"
