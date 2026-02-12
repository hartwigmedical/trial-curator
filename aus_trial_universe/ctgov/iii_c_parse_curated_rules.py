import argparse
import logging
from pathlib import Path
from typing import Any, List, Dict, Tuple, Optional
import pandas as pd
import numpy as np

from aus_trial_universe.ctgov.iii_b_load_curated_rules import load_curated_rules

logger = logging.getLogger(__name__)


SEARCHING_CRITERIA = [
    "PrimaryTumorCriterion", "HistologyCriterion",
    "MolecularBiomarkerCriterion",
    "MolecularSignatureCriterion",
    "GeneAlterationCriterion"
]


def input_text_extraction(rule_obj: Any) -> Optional[str]:
    if rule_obj is None:
        logger.error("input_text_extraction: Entire rule object is missing")
        return None

    if not hasattr(rule_obj, "rule_text"):
        logger.error("input_text_extraction: Input text field is missing")
        return None

    input_text = getattr(rule_obj, "rule_text", None)
    if input_text is None:
        logger.error("input_text_extraction: Input text field is None")
        return None

    return input_text.strip()


def criterion_extraction_dfs(starting_criterion: object, searching_criterion: str) -> List[Tuple[object, bool]]:
    extraction: List[Tuple[object, bool]] = []

    if starting_criterion is None:
        return extraction

    def _walk(node: object, andcriterion_ancestor: bool) -> None:
        node_name = type(node).__name__

        if node_name == searching_criterion:
            extraction.append((node, andcriterion_ancestor))

        is_andcriterion = (node_name == "AndCriterion")
        updated_andcriterion_flag = andcriterion_ancestor or is_andcriterion

        for attr_name in ("criteria", "criterion"):  # Apply recursion into children nodes
            children = getattr(node, attr_name, None)
            if children is None:
                continue

            if isinstance(children, (list, tuple)):
                for child in children:
                    if child is not None:
                        _walk(child, updated_andcriterion_flag)
            else:
                _walk(children, updated_andcriterion_flag)

    if isinstance(starting_criterion, (list, tuple)):  # if root criterion is a list/tuple
        for item in starting_criterion:
            if item is not None:
                _walk(item, False)
    else:
        _walk(starting_criterion, False)

    return extraction


def tabularise_criterion_instances_per_file(py_filepath: Path, searching_criterion: str) -> pd.DataFrame:
    curated_rules = load_curated_rules(py_filepath)

    if not curated_rules:  # Not logging an error again because errors would have been logged by the loader function
        return pd.DataFrame(columns=["trialId", "input_text", "Incl/Excl", "criterion_class", "inside_andcriterion"]).astype({"trialId": str})  # to avoid data type inconsistency because `trialId` is used as the matching key later

    trialId = py_filepath.stem
    rows: List[Dict[str, Any]] = []

    for rule in curated_rules:
        input_text = input_text_extraction(rule) or "(Missing)"

        if bool(getattr(rule, "exclude", False)):  # If `rule` doesn’t have the attribute `exclude`, return False (i.e. assume it's an inclusive rule)
            direction = "EXCL"
        else:
            direction = "INCL"

        curations = getattr(rule, "curation", None)  # start from the root criterion

        matched_criteria = criterion_extraction_dfs(starting_criterion=curations, searching_criterion=searching_criterion)  # apply DFS

        for crit, inside_andcriterion in matched_criteria:
            row: Dict[str, Any] = {
                "trialId": trialId,
                "input_text": input_text,
                "Incl/Excl": direction,
                "criterion_class": type(crit).__name__,
                "inside_andcriterion": inside_andcriterion,
            }

            for key, val in vars(crit).items():
                if key.startswith("_") or callable(val) or val is None:  # ignore empty/internal data
                    continue

                if key == "description":
                    row["criterion_description"] = str(val)
                    continue

                # All the fields on the matched criterion object become their own columns. Dynamically expanding.
                if isinstance(val, (list, tuple)):
                    row[key] = "; ".join(map(str, val))
                elif isinstance(val, set):
                    row[key] = "; ".join(map(str, sorted(val)))  # enforce ordering for set
                else:
                    row[key] = str(val)

            rows.append(row)

    return (
        # If the same criterion has different fields / no. of fields, when these rows are combined in a single DataFrame, pandas takes the union of these columns.
        # Drop row if every column is identical
        pd.DataFrame(rows).drop_duplicates()
            if rows
            else pd.DataFrame(columns=["trialId", "input_text", "Incl/Excl", "criterion_class", "inside_andcriterion"]).astype({"trialId": str})
    )


def tabularise_criterion_instances_in_dir(py_dir: Path, searching_criterion: str) -> pd.DataFrame:
    trials: List[pd.DataFrame] = []

    trials_counter = 0
    for filepath in sorted(py_dir.glob("NCT*.py")):
        try:
            trials.append(
                tabularise_criterion_instances_per_file(filepath, searching_criterion)
            )

            trials_counter += 1
        except Exception as e:
            logger.exception(f"Failed on {filepath}: {e}")

    if not trials:
        return pd.DataFrame(columns=["trialId", "input_text", "Incl/Excl", "criterion_class", "inside_andcriterion"]).astype({"trialId": str})

    concat_trials = pd.concat(trials, ignore_index=True)
    if concat_trials.empty:
        return concat_trials.astype({"trialId": str})

    logger.info(f"For criterion {searching_criterion}, processed {trials_counter} trials.")
    return concat_trials.sort_values(["trialId", "Incl/Excl"]).reset_index(drop=True)


def restructure_to_one_trial_per_row(instances_df: pd.DataFrame, searching_criterion: str, joining_delimiter: str = "; ") -> pd.DataFrame:
    if instances_df is None or instances_df.empty:
        return pd.DataFrame(columns=["trialId"]).astype({"trialId": str})

    core_cols = {"trialId", "Incl/Excl", "criterion_class"}
    other_cols = [c for c in instances_df.columns if c not in core_cols]
    if not other_cols:
        return pd.DataFrame({"trialId": sorted(instances_df["trialId"].astype(str).unique())})  # since there is no useful column to display, just return a table of unique trialIds

    instances_df = instances_df.copy()
    instances_df["_number_rows"] = np.arange(len(instances_df))  # Number the rows. Used to preserve elements sequence between adjacent cols later when grouping by trialId

    def _agg_preserve_sequence_nodedup(series: pd.Series) -> str:
        vals = []
        for ele in series:
            if pd.notna(ele):
                ele = str(ele).strip()
                if ele:  # only consider non-empty elements
                    vals.append(ele)

        return joining_delimiter.join(vals)

    def _agg_preserve_sequence_dedup(series: pd.Series) -> str:  # To only apply to input_text
        seen = set()
        vals: list[str] = []

        for ele in series:
            if pd.isna(ele):
                continue
            s = str(ele).strip()
            if not s:
                continue
            if s in seen:
                continue
            seen.add(s)
            vals.append(s)

        return joining_delimiter.join(vals)

    grouped_list = []
    for direction in ("INCL", "EXCL"):
        direction_df = instances_df.loc[instances_df["Incl/Excl"] == direction, :]
        direction_df = direction_df.sort_values(by=["trialId", "_number_rows"], kind="stable")  # to use pandas' stable sorting algorithm - across values with the same trialId, keep the original row order
        if direction_df.empty:
            continue

        reordered_cols = []
        if "input_text" in other_cols:
            reordered_cols.append("input_text")
        cols = reordered_cols + [c for c in other_cols if c not in reordered_cols]

        agg_map: dict[str, Any] = {}
        for c in cols:
            if c == "input_text":
                agg_map[c] = _agg_preserve_sequence_dedup
            else:
                agg_map[c] = _agg_preserve_sequence_nodedup

        grouped_df = (
            direction_df
            .groupby("trialId", as_index=False, sort=False)[cols]
            .agg(agg_map)
        )

        rename_map = {}
        for col in cols:
            if col == "input_text":
                rename_map[col] = f"{direction}:input_text"
            else:
                rename_map[col] = f"{direction}:{searching_criterion}-{col}"
        grouped_df = grouped_df.rename(columns=rename_map)

        grouped_list.append(grouped_df)

    if not grouped_list:
        return pd.DataFrame({"trialId": sorted(instances_df["trialId"].astype(str).unique())})

    wide_tbl = grouped_list[0]  # headers
    for i in grouped_list[1:]:
        wide_tbl = wide_tbl.merge(i, on="trialId", how="outer")

    return wide_tbl.fillna("").astype({"trialId": str})


def main():
    parser = argparse.ArgumentParser(description="Extract specified criterion attributes from curated eligibility rules.")
    parser.add_argument("--curated_dir", type=Path, help="Directory containing curated NCT*.py files.", required=True)
    parser.add_argument("--output_dir", type=Path, help="Output directory to save the summary CSVs.", required=True)
    parser.add_argument("--log_level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], help="Logging level")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Obtain complete list of trialIds
    all_trials = sorted([p.stem for p in args.curated_dir.glob("NCT*.py")])
    logger.info(f"Found {len(all_trials)} curated trials to process.")

    all_trials_df = pd.DataFrame({"trialId": all_trials}).astype({"trialId": str})

    # Process each searching criterion
    for crit in SEARCHING_CRITERIA:
        logger.info(f"Extracting fields for criterion: {crit}")

        instances_tbl = tabularise_criterion_instances_in_dir(args.curated_dir, crit)  # Info in long form: One row per matched criterion
        instance_csv = args.output_dir / f"{crit}_instances.csv"
        instances_tbl.to_csv(instance_csv, index=False)

        summary_tbl = restructure_to_one_trial_per_row(instances_tbl, crit)  # Group by trialId to convert to wide form
        summary_tbl = (
            all_trials_df.merge(summary_tbl, on="trialId", how="left")   # Ensure one row per trial even if that trial has no matches
            .fillna("")
            .astype({"trialId": str})
        )
        aggregate_csv = args.output_dir / f"{crit}_extractions.csv"
        summary_tbl.to_csv(aggregate_csv, index=False)

        logger.info(f"Saved to {aggregate_csv}")


if __name__ == "__main__":
    main()

