"""OncoTree enrichment for trial drug curation.

The drug-curation LLM produces a ``cancerTypes`` value per row. Rather than
trusting the same call to also produce OncoTree codes (which is where
hallucinated codes came from), the ``Oncotree`` column is filled here by the
OncoTree agentic workflow (navigator + semantic reviewer), which can only emit
codes that exist in the project YAML.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable
from dataclasses import replace

from aus_trial_universe.drug_utility_path.oncotree_agentic_workflow.workflow import OncoTreeAgenticWorkflow
from aus_trial_universe.drug_utility_path.trial_drug_curation.llm.structured_outputs import (
    TrialDrugCurationTable,
    TrialDrugRow,
    table_from_payload,
)

ONCOTREE_CODE_SEPARATOR = " | "

# Mapping a single (possibly ``|``-delimited) cancerTypes string to a
# ``|``-joined string of OncoTree codes.
CancerTypeMapper = Callable[[str], str]


class OncotreeEnricher:
    """Overwrites the ``Oncotree`` column from ``cancerTypes`` per row.

    Results are cached by cancerTypes string, so rows that share the same cancer
    type (e.g. multiple arms of one trial) only cost one workflow run.
    """

    def __init__(self, mapper: CancerTypeMapper) -> None:
        self._map = mapper
        self._cache: dict[str, str] = {}

    @classmethod
    def from_workflow(cls, workflow: OncoTreeAgenticWorkflow) -> "OncotreeEnricher":
        def mapper(cancer_types: str) -> str:
            return ONCOTREE_CODE_SEPARATOR.join(workflow.run(cancer_types).codes)

        return cls(mapper)

    @classmethod
    def from_yaml(
        cls,
        oncotree_yaml: str,
        *,
        max_iterations: int = 3,
    ) -> "OncotreeEnricher":
        return cls.from_workflow(
            OncoTreeAgenticWorkflow.from_yaml(oncotree_yaml, max_iterations=max_iterations)
        )

    def map_cancer_types(self, cancer_types: str) -> str:
        key = (cancer_types or "").strip()
        if not key:
            return ""
        if key not in self._cache:
            self._cache[key] = self._map(key)
        return self._cache[key]

    def enrich_row(self, row: TrialDrugRow) -> TrialDrugRow:
        return replace(row, Oncotree=self.map_cancer_types(row.cancerTypes))

    def enrich_table(self, table: TrialDrugCurationTable) -> TrialDrugCurationTable:
        return TrialDrugCurationTable(rows=tuple(self.enrich_row(row) for row in table.rows))

    def enrich_tsv(self, tsv: str) -> str:
        return self.enrich_table(_table_from_tsv(tsv)).to_tsv()


def _table_from_tsv(tsv: str) -> TrialDrugCurationTable:
    reader = csv.DictReader(io.StringIO(tsv), delimiter="\t")
    return table_from_payload([dict(row) for row in reader])
