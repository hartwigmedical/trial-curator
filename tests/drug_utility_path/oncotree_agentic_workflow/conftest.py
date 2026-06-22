from __future__ import annotations

import pytest

from aus_trial_universe.drug_utility_path.oncotree_agentic_workflow.oncotree import OncoTree


@pytest.fixture(scope="session")
def tree() -> OncoTree:
    """The project OncoTree, loaded once per test session."""
    return OncoTree.from_yaml()
