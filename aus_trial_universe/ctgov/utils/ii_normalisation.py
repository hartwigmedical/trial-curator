import logging
import math
import numbers
from typing import Any, Dict, Set

import pandas as pd

logger = logging.getLogger(__name__)


_EMPTY_TOKENS: Set[str] = {"", "na", "n/a", "unknown"}


def _clean_ele(value: str) -> str:
    return value.strip().lower()


def norm(x: Any) -> str:
    if x is None:
        return ""
    if isinstance(x, numbers.Real):
        try:
            if math.isnan(x):  # type: ignore[arg-type]
                return ""
        except TypeError:
            pass
    return _clean_ele(str(x))


def is_effectively_empty(x: Any) -> bool:
    return norm(x) in _EMPTY_TOKENS


MOJIBAKE_REPLACEMENTS: Dict[str, str] = {
    # comparisons
    "‚â•": "≥",
    "â‰¥": "≥",
    "â‰¤": "≤",
    # bullets / dots
    "â€¢": "•",
    "â—": "•",
    # dashes
    "â€“": "-",
    "â€”": "-",
    # quotes
    "â€˜": "'",
    "â€™": "'",
    "â€œ": '"',
    "â€�": '"',
    # misc symbols
    "Ã—": "×",
    "Ã·": "÷",
    "Â±": "±",
    "Â°": "°",
    # stray non-breaking-space marker
    "Â ": " ",
}


def fix_mojibake_str(value: str) -> str:
    result = value
    for bad, good in MOJIBAKE_REPLACEMENTS.items():
        if bad in result:
            result = result.replace(bad, good)
    return result


def fix_mojibake_df(df: pd.DataFrame) -> pd.DataFrame:
    obj_cols = df.select_dtypes(include=["object"]).columns
    for col in obj_cols:
        df[col] = df[col].apply(lambda v: fix_mojibake_str(v) if isinstance(v, str) else v)
    return df
