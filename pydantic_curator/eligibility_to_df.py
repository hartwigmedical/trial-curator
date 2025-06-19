import json
import logging
import re
from typing import NamedTuple

import pandas as pd
from sentence_transformers import SentenceTransformer, SimilarityFunction

from actin_curator.actin_curator_utils import format_actin_rule
from trialcurator.criterion_compare import criterion_diff
from .criterion_formatter import format_criterion
from .criterion_schema import *
from .eligibility_py_loader import exec_file_into_variable

logger = logging.getLogger(__name__)

# use this method to get all rule types, might be a little brittle
rule_types = [re.search(r'.*\.(\w+)Criterion', str(c)).group(1) for c in BaseCriterion.__subclasses__()]

class EligibilityRule(NamedTuple):
    rule_id: str
    rule_type: str
    description: str
    values: str

def count_rule_types(criterion: BaseCriterion, rule_type_counts: dict[str, int]):

    rule_type = criterion.__class__.__name__.replace('Criterion', '')
    rule_type_counts[rule_type] = rule_type_counts.get(rule_type, 0) + 1

    if isinstance(criterion, AndCriterion):
        [count_rule_types(c, rule_type_counts) for c in criterion.criteria]
    elif isinstance(criterion, OrCriterion):
        [count_rule_types(c, rule_type_counts) for c in criterion.criteria]
    elif isinstance(criterion, NotCriterion):
        count_rule_types(criterion.criterion, rule_type_counts)
    elif isinstance(criterion, IfCriterion):
        count_rule_types(criterion.condition, rule_type_counts)
        count_rule_types(criterion.then, rule_type_counts)
        if criterion.else_:
            count_rule_types(criterion.else_, rule_type_counts)

# add all these rules into a panda dictionary
def criteria_to_rule_count_df(trial_id, cohort, criteria: list[BaseCriterion]) -> pd.DataFrame:
    rule_ids = []
    rule_nums = []
    rule_types_counts = {t: [] for t in rule_types}
    descriptions = []
    llm_code = []
    for i in range(len(criteria)):
        rule_ids.append(f'{trial_id}.{i + 1}')
        rule_nums.append(i + 1)
        rule_type_counts = {}
        count_rule_types(criteria[i], rule_type_counts)
        # add it to each rule type
        for k, v in rule_types_counts.items():
            v.append(rule_type_counts.get(k, 0))
        try:
            descriptions.append(criteria[i].description)
        except Exception as e:
            logger.warning(f'failed to get description for rule {i}({trial_id}): {criteria[i]}, error: {e}')
        llm_code.append(format_criterion(criteria[i]))

    data = {
        "TrialId": [trial_id] * len(criteria),
        "Cohort": [cohort] * len(criteria),
        "RuleNum": rule_nums,
        "RuleId": rule_ids,
        "Description": descriptions,
    }
    data.update(rule_types_counts)
    data.update({"LlmCode": llm_code})
    return pd.DataFrame(data)

# merge the py and actin rules together and return a dataframe with the data
def merge_py_actin_criteria(py_criteria: list[BaseCriterion], actin_mappings, fuzzymatch_model) -> pd.DataFrame:
    py_criteria_desc = [c.description for c in py_criteria]
    actin_rule_dict = {}
    for r in actin_mappings:
        actin_rule_dict[r['description']] = format_actin_rule(r['actin_rule'])

    # write the diffs into a dataframe
    diffs = criterion_diff(py_criteria_desc, list(actin_rule_dict.keys()), fuzzymatch_model)

    data = {
        "Description": [d.old_criterion for d in diffs],
        "ActinText": [d.new_criterion for d in diffs],
        "Similarity": [d.similarity for d in diffs],
        "ActinRules": [actin_rule_dict.get(d.new_criterion) for d in diffs]
    }
    return pd.DataFrame(data)

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Clinical trial eligibility to dataframe")
    parser.add_argument('--trial_list', help='file containing trial ids', required=True)
    parser.add_argument('--trial_py_dir', help='directory containing curated trial python files', required=True)
    parser.add_argument('--trial_actin_dir', help='directory containing curated trial ACTIN files', required=False)
    parser.add_argument('--out_df', help='output dataframe', required=True)
    args = parser.parse_args()

    trial_list = pd.read_csv(args.trial_list, names=["trial_id"])["trial_id"].tolist()
    fuzzymatch_model = SentenceTransformer("cambridgeltl/SapBERT-from-PubMedBERT-fulltext",
                                           similarity_fn_name=SimilarityFunction.DOT_PRODUCT)

    logger.info(f"trial list: {args.trial_list}, contains {len(trial_list)} trials")

    dfs = []

    for trial_id in trial_list:

        logger.info(f"processing trial: {trial_id}")

        py_file = f"{args.trial_py_dir}/{trial_id}.py"

        try:
            cohort_criteria = exec_file_into_variable(py_file)
        except Exception as e:
            logger.warning(f'failed to load {py_file}, error: {e}')
            continue

        cohort_actin_mappings = None

        if args.trial_actin_dir:
            # load the curated actin rules as well
            with open(f"{args.trial_actin_dir}/{trial_id}.actin.json", 'r') as f:
                cohort_actin_mappings = json.load(f)

        # process cohort by cohort
        for cohort, criteria in cohort_criteria.items():
            df = criteria_to_rule_count_df(trial_id, cohort, criteria)

            if cohort_actin_mappings is not None and cohort in cohort_actin_mappings:
                actin_mappings = cohort_actin_mappings[cohort]
                # load the curated actin rules as well
                # match the cohort rules up
                # TODO: work out how to deal with empty description
                df = df.merge(merge_py_actin_criteria(criteria, actin_mappings, fuzzymatch_model),
                              on='Description', how='right', sort=False)

            # make sure the trial id and cohort field is always set
            df["TrialId"] = trial_id
            df["Cohort"] = cohort
            dfs.append(df)

    df = pd.concat(dfs)
    df.to_csv(args.out_df, sep='\t', index=False)


if __name__ == "__main__":
    main()
