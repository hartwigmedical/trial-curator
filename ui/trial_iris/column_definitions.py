import re
from dataclasses import dataclass
from typing import Optional

from pydantic_curator.criterion_schema import BaseCriterion


@dataclass
class ColumnDefinition:
    name: str
    type: type = str
    defaultHidden: bool = False
    thin: bool = False
    filterable: bool = False
    width: Optional[str] = None


CRITERION_TYPE_NAMES = [re.search(r'.*\.(\w+)Criterion', str(c)).group(1) for c in BaseCriterion.__subclasses__()]

COLUMN_DEFINITIONS = [
    ColumnDefinition("TrialId", filterable=True),
    ColumnDefinition("Cohort", filterable=True),
    ColumnDefinition("RuleNum", defaultHidden=True),
    ColumnDefinition("RuleId", defaultHidden=True),
    ColumnDefinition("Description", width="200px"),
    *[ColumnDefinition(name, filterable=True, defaultHidden=True, thin=True) for name in CRITERION_TYPE_NAMES],
    ColumnDefinition("Checked", type=bool, filterable=True),
    ColumnDefinition("Edit"),
    ColumnDefinition("Code"),
    ColumnDefinition("Error", defaultHidden=True),
    ColumnDefinition("LlmCode", defaultHidden=True),
    ColumnDefinition("OverrideCode", defaultHidden=True)
]
