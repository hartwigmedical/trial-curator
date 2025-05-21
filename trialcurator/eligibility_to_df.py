import json
import logging
import re
from typing import NamedTuple

import pandas as pd
from sentence_transformers import SentenceTransformer, SimilarityFunction

from trialcurator.actin_formatter import format_actin_rule
from trialcurator.criterion_compare import criterion_diff
from trialcurator.criterion_schema import *
from trialcurator.criterion_serializer import CriterionSerializer
from trialcurator.eligibility_py_loader import exec_file_into_variable
from trialcurator.utils import deep_remove_field

logger = logging.getLogger(__name__)

# use this method to get all rule types, might be a little brittle
rule_types = [re.search(r'.*\.(\w+)Criterion', str(c)).group(1) for c in BaseCriterion.__subclasses__()]

class EligibilityRule(NamedTuple):
    rule_id: str
    rule_type: str
    description: str
    values: str

# format it nicely by using key=value and remove quotes
def format_criterion(c: BaseCriterion) -> str:
    return CriterionSerializer.serialize(c)

# generate rule id
def process(parent_rule_id: str, c: BaseCriterion, child_id: int, rule_list: list[EligibilityRule]):

    rule_id = f"{parent_rule_id}.{child_id}"
    rule_type = c.__class__.__name__.replace('Criterion', '')
    description = c.description

    if isinstance(c, (AndCriterion, OrCriterion, NotCriterion, IfCriterion)):
        values = None
    else:
        value_dict = deep_remove_field(c.model_dump(serialize_as_any=True, exclude_none=True), 'description')
        values = '; '.join(f'{k}={v}' for k, v in value_dict.items())

    rule_list.append(EligibilityRule(rule_id, rule_type, description, values))

    if isinstance(c, AndCriterion):
        [process(rule_id, c.criteria[i], i + 1, rule_list) for i in range(len(c.criteria))]
    elif isinstance(c, OrCriterion):
        [process(rule_id, c.criteria[i], i + 1, rule_list) for i in range(len(c.criteria))]
    elif isinstance(c, NotCriterion):
        process(rule_id, c.criterion, 1, rule_list)
    elif isinstance(c, IfCriterion):
        process(rule_id, c.condition, 1, rule_list)
        process(rule_id, c.then, 2, rule_list)
        if c.else_:
            process(rule_id, c.else_, 3, rule_list)

# add all these rules into a panda dictionary
def criteria_to_df(trial_id, criteria: list[BaseCriterion]) -> pd.DataFrame:

    rule_list = []
    for i in range(len(criteria)):
        process(trial_id, criteria[i], i + 1, rule_list)

    data = [
        {
            "TrialId": trial_id,
            "RuleNum": [i + 1 for i in range(len(rule_list))],
            "RuleId": rule.rule_id,
            "Type": rule.rule_type,
            "Description": rule.description,
            "Values": rule.values,
        }
        for rule in rule_list
    ]
    return pd.DataFrame(data)

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
def criteria_to_rule_count_df(trial_id, criteria: list[BaseCriterion]) -> pd.DataFrame:
    rule_ids = []
    rule_nums = []
    rule_types_counts = {t: [] for t in rule_types}
    descriptions = []
    values = []
    for i in range(len(criteria)):
        rule_ids.append(f'{trial_id}.{i + 1}')
        rule_nums.append(i + 1)
        rule_type_counts = {}
        count_rule_types(criteria[i], rule_type_counts)
        # add it to each rule type
        for k, v in rule_types_counts.items():
            v.append(rule_type_counts.get(k, 0))
        descriptions.append(criteria[i].description)
        values.append(format_criterion(criteria[i]))

    data = {
        "TrialId": [trial_id] * len(criteria),
        "RuleNum": rule_nums,
        "RuleId": rule_ids,
        "Description": descriptions,
    }
    data.update(rule_types_counts)
    data.update({"Values": values})
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
        df = None

        try:
            cohort_criteria = exec_file_into_variable(py_file)
        except Exception as e:
            logger.warning(f'failed to load {py_file}, error: {e}')
            continue

        # for now we just take the first cohort
        criteria = cohort_criteria[list(cohort_criteria.keys())[0]]
        #dfs.append(criteria_to_df(trial_id, criteria))
        df = criteria_to_rule_count_df(trial_id, criteria)

        if args.trial_actin_dir:
            # load the curated actin rules as well
            with open(f"{args.trial_actin_dir}/{trial_id}.actin.json", 'r') as f:
                # only first cohort for now
                actin_mappings = json.load(f)[list(cohort_criteria.keys())[0]]

            # match the cohort rules up
            # TODO: work out how to deal with empty description
            df = df.merge(merge_py_actin_criteria(criteria, actin_mappings, fuzzymatch_model),
                          on='Description', how='right', sort=False)

        # make sure the trial id field is always set
        df["TrialId"] = trial_id
        dfs.append(df)

    df = pd.concat(dfs)
    df.to_csv(args.out_df, sep='\t', index=False)


if __name__ == "__main__":
    main()
