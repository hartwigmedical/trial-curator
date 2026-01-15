import argparse
import logging
from pathlib import Path
import ast

import pandas as pd


logger = logging.getLogger(__name__)


def extract_unique_conditions(input_csv: Path, column_name: str, output_csv: Path) -> None:
    df = pd.read_csv(input_csv)

    if column_name not in df.columns:
        raise ValueError(f"Column '{column_name}' not found")

    all_conditions = []

    for cell in df[column_name].dropna():
        conditions = ast.literal_eval(cell)
        all_conditions.extend(conditions)

    unique_sorted = sorted(set(all_conditions))

    out_df = pd.DataFrame({"condition": unique_sorted})
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(output_csv, index=False)

    logger.info("Extracted %d unique conditions -> %s", len(unique_sorted), output_csv)


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract unique conditions from list-valued CSV column")
    parser.add_argument("--input_csv", type=Path, required=True)
    parser.add_argument("--column_name", type=str, required=True)
    parser.add_argument("--output_csv", type=Path, required=True)
    parser.add_argument("--log_level", default="INFO", help="Logging level (DEBUG, INFO, WARNING, ERROR)")

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    extract_unique_conditions(
        input_csv=args.input_csv,
        column_name=args.column_name,
        output_csv=args.output_csv,
    )


if __name__ == "__main__":
    main()
