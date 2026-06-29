from __future__ import annotations

from pathlib import Path

import pandas as pd

from aus_trial_universe.eligibility_path.shared.utils.text_normalisation import (
    fix_mojibake_df,
)


def load_resource_csv(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(
        csv_path,
        keep_default_na=False,
        na_values=[],
    )
    df.columns = [str(c).strip() for c in df.columns]
    return fix_mojibake_df(df)
