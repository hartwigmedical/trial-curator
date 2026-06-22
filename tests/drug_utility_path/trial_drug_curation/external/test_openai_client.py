import os

import pytest

from aus_trial_universe.drug_utility_path.trial_drug_curation.llm.client import (
    DEFAULT_MODEL,
    TrialDrugCurationClient,
)


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_TRIAL_DRUG_CURATION_EXTERNAL_TESTS") != "1"
    or not os.environ.get("OPENAI_API_KEY"),
    reason=(
        "Set RUN_TRIAL_DRUG_CURATION_EXTERNAL_TESTS=1 and OPENAI_API_KEY to run "
        "live OpenAI API tests."
    ),
)


def test_live_openai_client_can_return_empty_structured_tsv():
    model = os.environ.get("TRIAL_DRUG_CURATION_TEST_MODEL", DEFAULT_MODEL)
    client = TrialDrugCurationClient(model=model)

    tsv = client.curate_tsv(
        user_prompt=(
            "No trial records are supplied. Return a valid structured response "
            "with an empty rows list."
        )
    )

    assert tsv.startswith("trialId\tdrugName")
