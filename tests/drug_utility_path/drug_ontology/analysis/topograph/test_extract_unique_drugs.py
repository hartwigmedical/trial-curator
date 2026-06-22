from pathlib import Path

import pytest

from aus_trial_universe.drug_utility_path.ctgov.drug_ontology.analysis.topograph.extract_unique_drugs import (
    find_topograph_input_file,
)


def test_find_topograph_input_file_uses_master_tsv(tmp_path: Path):
    input_file = tmp_path / "TOPOGRAPH-master.tsv"
    input_file.write_text("Drugs\nimatinib\n", encoding="utf-8")

    assert find_topograph_input_file(tmp_path) == input_file


def test_find_topograph_input_file_rejects_retired_oncotree_tsv(tmp_path: Path):
    (tmp_path / "TOPOGRAPH-oncotree-21052026.tsv").write_text("Drugs\nimatinib\n", encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="TOPOGRAPH-master.tsv"):
        find_topograph_input_file(tmp_path)
