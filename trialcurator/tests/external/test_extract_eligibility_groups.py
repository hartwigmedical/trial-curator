import argparse
import unittest
import os
from trialcurator import eligibility_curator
import pandas as pd

from pathlib import Path

from trialcurator.eligibility_curator import *
from trialcurator.openai_client import OpenaiClient

logger = logging.getLogger(__name__)

logging.basicConfig(stream=sys.stdout,
                    format='%(asctime)s %(levelname)5s - %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S',
                    level=logging.INFO)

logger.setLevel(logging.DEBUG)


def get_test_data_path():
    return f"{Path(__file__).parent}/data"


'''

with open(get_test_data_path("sample.json")) as f:
    data = f.read()
'''


class TestEligibilityGroups(unittest.TestCase):

    def setUp(self):
        pass

<<<<<<< Updated upstream
    def test_extract_eligibility_groups(self):
=======
    # load up the test samples and write out json files as needed
    def write_test_json(self):
        print("writing test json files")

        input_json_dir = os.path.expanduser('~/hartwig/omico_trial/nct_json')
        output_json_dir = os.path.expanduser('~/hartwig/omico_trial/nct_json_test')
>>>>>>> Stashed changes

        client = OpenaiClient(TEMPERATURE, TOP_P)
        # load up all the trial data
        df = pd.read_csv(f"{get_test_data_path()}/nct_eligibility/trials.list", names=['TrialCode'])

        # get out all the parts / cohorts
        for idx, row in df.iterrows():
            trial_id = row["TrialCode"]
            logger.info(f" ------------------- trial id: {trial_id} -------------------")
            with open(f"{get_test_data_path()}/nct_eligibility/{trial_id}.json", 'r', encoding='utf-8') as f:
                trial_dict = json.load(f)
                criteria = trial_dict["sanitised_criteria"]
                eligibility_groups = trial_dict["eligibility_groups"]
                llm_eligibility_groups = llm_extract_eligibility_groups(criteria, client)

                # check that the number of trial groups are the same
                self.assertEqual(len(eligibility_groups), len(llm_eligibility_groups))

    # load up the test samples and write out the test data json
    @staticmethod
    def convert_trial_json(input_json_dir, output_json_dir, trial_list_path):
        print("writing test json files")

        client = OpenaiClient(TEMPERATURE, TOP_P)
        # load up all the trial data
        df = pd.read_csv(trial_list_path, names=['TrialCode'])
        # get out all the parts / cohorts
        for idx, row in df.iterrows():
            trial_id = row["TrialCode"]
            logger.info(f" ------------------- trial id: {trial_id} -------------------")
            trial_json = f"{input_json_dir}/{trial_id}.json"
            trial_data = load_trial_data(trial_json)
            criteria = load_eligibility_criteria(trial_data)
            logger.info(f"trial criteria: {criteria}")
            sanitised_criteria = llm_sanitise_text(criteria, client)
            groups = llm_extract_eligibility_groups(sanitised_criteria, client)
            logger.info(f"trial groups: {groups}")

            trial_test_data = {
                "trial_id": trial_id,
                "eligibility_criteria": criteria,
                "sanitised_criteria": sanitised_criteria,
                "eligibility_groups": groups,
            }

            # write it to the test json
            output_json = f"{output_json_dir}/{trial_id}.json"
            with open(output_json, 'w', encoding='utf-8') as f:
                json.dump(trial_test_data, f, ensure_ascii=False, indent=2)
                logger.info(f"wrote out json file: {output_json}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process NCT trial json file and write out json suitable for"
                                                 "regression testing.")
    parser.add_argument("--input_json_dir", required=True, help="Directory containing input JSON files")
    parser.add_argument("--output_json_dir", required=True, help="Directory to save output JSON files")
    parser.add_argument("--trial_list", required=True, help="Path to the trial list CSV file")
    args = parser.parse_args()
    input_json_dir = args.input_json_dir
    output_json_dir = args.output_json_dir
    trial_list_path = args.trial_list
    TestEligibilityGroups.convert_trial_json(input_json_dir, output_json_dir, trial_list_path)
