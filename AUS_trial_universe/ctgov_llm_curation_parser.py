import argparse
import logging
from pathlib import Path
from typing import Any, List, Dict, Tuple, Optional
import pandas as pd
import numpy as np

from .ctgov_llm_curation_loader import load_curated_rules

logger = logging.getLogger(__name__)


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


def _iter_children(crit_obj: object) -> List[object]:
    children_nodes: List[object] = []

    for attr_name in ("criteria", "criterion"):
        child = getattr(crit_obj, attr_name, None)
        if child is None:
            continue

        if isinstance(child, (list, tuple)):
            children_nodes.extend(child)
        else:
            children_nodes.append(child)

    return children_nodes


def criterion_extraction_parent_counts(starting_criterion: object, searching_criterion: str) -> List[Tuple[object, int]]:
    out: List[Tuple[object, int]] = []
    if starting_criterion is None:
        return out

    def _walk(node: object, parent: Optional[object]) -> None:
        if type(node).__name__ == searching_criterion:
            size = 1 if parent is None else (1 + len(_iter_children(parent)))
            out.append((node, size))

        for child in _iter_children(node):
            _walk(child, node)

    if isinstance(starting_criterion, (list, tuple)):
        for item in starting_criterion:
            if item is not None:
                _walk(item, None)
    else:
        _walk(starting_criterion, None)

    return out


def criterion_extraction_dfs(starting_criterion: object, searching_criterion: str) -> List[object]:
    if starting_criterion is None:
        return []

    matched_criterion_list = [starting_criterion] if type(starting_criterion).__name__ == searching_criterion else []  # Cannot directly compare because `starting_criterion` is an object instance & `searching_criterion` is a string

    for attr_name in ("criteria", "criterion"):
        children_criterion = getattr(starting_criterion, attr_name, ())

        if not isinstance(children_criterion, (list, tuple)):
            children_criterion = (children_criterion,) if children_criterion is not None else ()  # to ensure `children_criterion` is normalised into a tuple, empty or otherwise. Because we need to iterate through a tuple afterwards

        for child_criterion in children_criterion:
            matched_criterion_list.extend(
                criterion_extraction_dfs(child_criterion, searching_criterion)
            )

    return matched_criterion_list


def tabularise_criterion_instances_per_file(py_filepath: Path, searching_criterion: str) -> pd.DataFrame:
    curated_rules = load_curated_rules(py_filepath)

    if not curated_rules:  # Not logging an error again because errors would have been logged by the loader function
        return pd.DataFrame(columns=["trialId", "input_text", "Incl/Excl", "criterion_class", "rule_obj_criterion_count"]).astype(
            {"trialId": str})  # to avoid data type inconsistency because `trialId` is used as the matching key later

    trialId = py_filepath.stem
    rows: List[Dict[str, Any]] = []

    for rule in curated_rules:
        input_text = input_text_extraction(rule) or "(Missing)"

        if bool(getattr(rule, "exclude", False)):  # If `rule` doesn’t have the attribute `exclude`, return False (i.e. assume it's an inclusive rule)
            direction = "EXCL"
        else:
            direction = "INCL"

        curations = getattr(rule, "curation", None)  # start from the root criterion
        # for crit in criterion_extraction_dfs(starting_criterion=curations, searching_criterion=searching_criterion):  # apply DFS
        for crit, parent_group_size in criterion_extraction_parent_counts(curations, searching_criterion):
            row = {
                "trialId": trialId,
                "input_text": input_text,
                "Incl/Excl": direction,
                "criterion_class": type(crit).__name__,
                "rule_obj_criterion_count": parent_group_size,
            }

            for key, val in vars(crit).items():
                if key.startswith("_") or callable(val) or val is None:  # ignore empty/internal data
                    continue
                if key == "description":  # ignore node-level description since we are extracting the input_text
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
            else pd.DataFrame(columns=["trialId", "input_text", "Incl/Excl", "criterion_class", "rule_obj_criterion_count"]).astype({"trialId": str})
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
        return pd.DataFrame(columns=["trialId", "input_text", "Incl/Excl", "criterion_class", "rule_obj_criterion_count"]).astype({"trialId": str})

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

    reordered_cols = []
    if "input_text" in other_cols:
        reordered_cols.append("input_text")
    if "rule_obj_criterion_count" in other_cols:
        reordered_cols.append("rule_obj_criterion_count")
    other_cols = reordered_cols + [c for c in other_cols if c not in reordered_cols]

    def _agg_preserve_sequence(series: pd.Series) -> str:
        vals = []
        for ele in series:
            if pd.notna(ele):
                ele = str(ele).strip()
                if ele:  # only consider non-empty elements
                    vals.append(ele)

        return joining_delimiter.join(vals)

    def _agg_single_count(series: pd.Series) -> str:
        ints = []
        for ele in series.dropna():
            if isinstance(ele, (int, np.integer)):
                ints.append(int(ele))
            else:
                s = str(ele).strip()
                if s.isdigit():
                    ints.append(int(s))
        if not ints:
            return ""
        return str(max(set(ints)))  # one number per trial/direction: choose max of unique values

    grouped_list = []
    for direction in ("INCL", "EXCL"):
        direction_df = instances_df.loc[instances_df["Incl/Excl"] == direction, :]
        direction_df = direction_df.sort_values(by=["trialId", "_number_rows"], kind="stable")  # to use pandas' stable sorting algorithm - across values with the same trialId, keep the original row order
        if direction_df.empty:
            continue

        reordered_cols = []
        if "input_text" in other_cols:
            reordered_cols.append("input_text")
        if "rule_obj_criterion_count" in other_cols:
            reordered_cols.append("rule_obj_criterion_count")
        cols = reordered_cols + [c for c in other_cols if c not in reordered_cols]

        agg_map = {c: _agg_preserve_sequence for c in cols}
        if "rule_obj_criterion_count" in agg_map:
            agg_map["rule_obj_criterion_count"] = _agg_single_count

        grouped_df = direction_df.groupby("trialId", as_index=False, sort=False) \
            [other_cols] \
            .agg(agg_map)

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
    parser.add_argument("--searching_criterion", help="Criterion class name to extract (e.g., MolecularBiomarkerCriterion or PrimaryTumorCriterion).", required=True)
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
    all_trials_df = pd.DataFrame({"trialId": all_trials}).astype({"trialId": str})

    # Extract each instance of a matched searching_criterion
    logger.info(f"Extracting {args.searching_criterion} from {len(all_trials)} curated trials...")
    instances_tbl = tabularise_criterion_instances_in_dir(args.curated_dir, args.searching_criterion)

    # Group by trialId
    summary_tbl = restructure_to_one_trial_per_row(instances_tbl, args.searching_criterion)
    # Ensure one row per trial even if that trial has no matches
    summary_tbl = all_trials_df.merge(summary_tbl, on="trialId", how="left").fillna("").astype({"trialId": str})

    # Save final aggregate CSV
    aggregate_csv = args.output_dir / f"{args.searching_criterion}_aggregate.csv"
    summary_tbl.to_csv(aggregate_csv, index=False)
    logger.info(f"Saved aggregate summary table to {aggregate_csv}")


if __name__ == "__main__":
    main()

