import logging

import pandas as pd


TRAILS_SINCE_YEAR = 2022


def trials_with_drug_intervention(xlsx_path: str) -> pd.DataFrame:

    df = pd.read_excel(xlsx_path)

    df = df.copy()
    df = df.loc[df["PURPOSE"] == "Treatment", :]
    df = df.loc[df["APPROVAL DATE"].year() >= TRAILS_SINCE_YEAR, :]

    return df







