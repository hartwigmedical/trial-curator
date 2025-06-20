import re
from dataclasses import dataclass
from typing import Optional

from pydantic_curator.criterion_schema import BaseCriterion


@dataclass
class ColumnDefinition:
    name: str
    type: type = str
    isDerived: bool = False
    defaultHidden: bool = False
    thin: bool = False
    filterable: bool = False
    width: Optional[str] = None


CRITERION_TYPE_NAMES = [re.search(r'.*\.(\w+)Criterion', str(c)).group(1) for c in BaseCriterion.__subclasses__()]

# -- Dynamic columns from criterion names --
DYNAMIC_COLUMNS = {
    name.upper(): ColumnDefinition(name, filterable=True, defaultHidden=True, thin=True)
    for name in CRITERION_TYPE_NAMES
}

# -- Create a dynamic namespace (Columns) --
# doing this allows us to refer to them like a variable
# i.e. Columns.TRIAL_ID
class Columns:
    TRIAL_ID = ColumnDefinition("TrialId", filterable=True)
    COHORT = ColumnDefinition("Cohort", filterable=True)
    RULE_NUM = ColumnDefinition("RuleNum", defaultHidden=True)
    RULE_ID = ColumnDefinition("RuleId", defaultHidden=True)
    DESCRIPTION = ColumnDefinition("Description", width="200px")
    CHECKED = ColumnDefinition("Checked", type=bool, filterable=True)
    OVERRIDE = ColumnDefinition("Override", type=bool, isDerived=True, filterable=True)
    ACTION = ColumnDefinition("Action")
    CODE = ColumnDefinition("Code")
    ERROR = ColumnDefinition("Error", defaultHidden=True)
    LLM_CODE = ColumnDefinition("LlmCode", defaultHidden=True)
    OVERRIDE_CODE = ColumnDefinition("OverrideCode", defaultHidden=True)

# also add the dynamic columns to the Columns namespace
for name, definition in DYNAMIC_COLUMNS.items():
    setattr(Columns, name, definition)

# -- Static columns defined as a dict --
COLUMN_DEFINITIONS = [
    Columns.TRIAL_ID,
    Columns.COHORT,
    Columns.RULE_NUM,
    Columns.RULE_ID,
    Columns.DESCRIPTION,
    *DYNAMIC_COLUMNS.values(),
    Columns.CHECKED,
    Columns.OVERRIDE,
    Columns.ACTION,
    Columns.CODE,
    Columns.ERROR,
    Columns.LLM_CODE,
    Columns.OVERRIDE_CODE
]

# index of the dataframe
INDEX_COLUMN = "idx"
