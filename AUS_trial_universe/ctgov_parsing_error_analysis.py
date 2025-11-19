import re
import argparse
import logging
from typing import List, Tuple
from pathlib import Path
import pandas as pd

logger = logging.getLogger(__name__)

# To catch lines like: 2025-11-07 15:57:39,065 | ERROR | __main__ | SyntaxError in /Users/junrancao/.../NCT02521493.py:112
ERROR_LINE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} \|\s+ERROR \| __main__ \| ([A-Za-z_][\w\.]*) in .*/(NCT\d+)\.py:(\d+)")

# To catch lines like: 2025-11-07 15:57:38,182 | ERROR | __main__ | No curated rules found in /.../NCT02521493.py.
# This repeats the same error & so can be ignored
IGNORE_LINE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} \|\s+ERROR \| __main__ \| No curated rules found in .*/(NCT\d+)\.py\.")

# To detect if a line starts with a timestamp - if it does, it is a new log statement, otherwise it is a continuation of the previous log statement
TIMESTAMP_LINE = re.compile(r"^\d{4}-\d{2}-\d{2} ")


def get_criterion_from_filename(filename: str) -> str:
    return filename.split("_", 1)[0] if "_" in filename else "CRITERION_NOT_FOUND"


def parse_extraction_logs(logpath: Path, criterion: str):
    summary_tbl = []
    log_lines = logpath.read_text("utf-8", "ignore").splitlines()

    i = 0
    while i < len(log_lines):
        curr_line = log_lines[i]

        if IGNORE_LINE.match(curr_line):
            i += 1
            continue

        found_err = ERROR_LINE.match(curr_line)
        if found_err:
            err, trial, line = found_err.groups()

            more_detail = ""
            if i + 1 < len(log_lines) and not TIMESTAMP_LINE.match(log_lines[i + 1]):  # If next line has no timestamp, treat it as more details
                more_detail = log_lines[i + 1].strip()
                i += 1

            summary_tbl.append((criterion, trial, line, err, more_detail))

        i += 1

    return summary_tbl


def group_by_trial(rows: List[Tuple[str, str, str, str, str]]):
    if not rows:
        return pd.DataFrame(columns=["trialId", "criterion", "line", "error_type", "detail"])

    df = pd.DataFrame(rows, columns=["criterion", "trialId", "line", "error_type", "detail"])

    grouped = (
        df.groupby("trialId", sort=False, dropna=False)
          .agg(lambda x: "; ".join(map(str, x)))
          .reset_index()
    )
    return grouped


def main():
    parser = argparse.ArgumentParser(description="Analyse parsing errors from the extraction of Criterion fields from LLM curations.")
    parser.add_argument("--log_dir", type=Path, help="Directory containing the log files.", required=True)
    parser.add_argument("--output_csv", type=Path, help="Output CSV with summary table.", required=True)
    parser.add_argument("--log_level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], help="Logging level")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    rows = []
    for logfile in sorted(Path(args.log_dir).glob("*.log")):
        rows.extend(
            parse_extraction_logs(logfile,
                                   get_criterion_from_filename(logfile.name)
                                  )
        )

    rows = list(dict.fromkeys(rows))  # Deduplicate exact duplicates

    grouped_df = group_by_trial(rows)
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    grouped_df.to_csv(args.output_csv, index=False)

    logger.info(f"Saved grouped summary to {args.output_csv}")


if __name__ == "__main__":
    main()
