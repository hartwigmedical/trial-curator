import argparse
import logging
from pathlib import Path
from typing import Any, List, Dict
import pandas as pd

logger = logging.getLogger(__name__)


def load_curated_rules(py_filepath: Path) -> list[Any] | None:
    import pydantic_curator.criterion_schema as cs

    def _make_shim(class_name: str):
        def __init__(self, *_, **kwargs):
            self.__dict__.update(kwargs)  # stores any fields; avoids Pydantic validation
        return type(class_name, (), {"__init__": __init__})

    module_globs: dict[str, Any] = {"__builtins__": __builtins__}
    for name in dir(cs):
        obj = getattr(cs, name)
        module_globs[name] = _make_shim(name) if isinstance(obj, type) and name.endswith("Criterion") else obj

    class Rule:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
    module_globs["Rule"] = Rule

    try:
        code = py_filepath.read_text(encoding="utf-8")
        exec(compile(code, str(py_filepath), "exec"), module_globs)
    except Exception as e:
        logger.exception(f"While executing {py_filepath}: {e}")
        return None

    rules = module_globs.get("rules")
    if rules is None:
        logger.error(f"{py_filepath} has no `rules` variable")
        return None
    return rules


def criterion_extraction_dfs(starting_criterion: object, searching_criterion: str) -> List[object]:
    if starting_criterion is None:
        return []

    matched_criterion_list = [starting_criterion] if type(starting_criterion).__name__ == searching_criterion else []  # Cannot directly compare because `starting_criterion` is an object instance & `searching_criterion` is a string

    for attr_name in ("criteria", "criterion"):
        children_criterion = getattr(starting_criterion, attr_name, ())

        if not isinstance(children_criterion, (List, tuple)):
            children_criterion = (children_criterion,) if children_criterion is not None else ()  # to ensure `children_criterion` is normalised into a tuple, empty or otherwise. Because we need to iterate through a tuple afterwards

        for child_criterion in children_criterion:
            matched_criterion_list.extend(
                criterion_extraction_dfs(child_criterion, searching_criterion)
            )

    return matched_criterion_list


def tabularise_criterion_instances_per_file(py_filepath: Path, searching_criterion: str) -> pd.DataFrame:
    curated_rules = load_curated_rules(py_filepath)
    if not curated_rules:
        logger.error(f"No curated rules found in {py_filepath}.")
        return pd.DataFrame(columns=["trialId", "Incl/Excl", "criterion_class"]).astype({"trialId": str})  # to avoid data type inconsistency because `trialId` is used as the matching key later

    trialId = py_filepath.stem
    rows: List[Dict[str, Any]] = []

    for rule in curated_rules:
        if getattr(rule, "exclude"):
            direction = "EXCL"
        else:
            direction = "INCL"

        curations = getattr(rule, "curation", None)  # start from the root criterion
        for crit in criterion_extraction_dfs(starting_criterion=curations, searching_criterion=searching_criterion):  # apply DFS
            row = {
                "trialId": trialId,
                "Incl/Excl": direction,
                "criterion_class": type(crit).__name__,
            }

            for key, val in vars(crit).items():
                if key.startswith("_") or callable(val) or val is None:  # ignore empty/internal data
                    continue

                row[key] = "; ".join(map(str, val)) if isinstance(val, (List, tuple, set)) else str(val)  # All the fields on the matched criterion object become their own columns

            rows.append(row)

    return (
        pd.DataFrame(rows)  # If the same criterion has different fields / no. of fields, when these rows are combined in a single DataFrame, pandas takes the union of these columns.
        if rows
        else pd.DataFrame(columns=["trialId", "Incl/Excl", "criterion_class"]).astype({"trialId": str})
    )


def tabularise_criterion_instances_in_dir(py_dir: Path, searching_criterion: str) -> pd.DataFrame:
    trials: List[pd.DataFrame] = []

    for filepath in sorted(py_dir.glob("NCT*.py")):
        try:
            trials.append(
                tabularise_criterion_instances_per_file(filepath,searching_criterion)
            )
        except Exception as e:
            logger.exception(f"Failed on {filepath}: {e}")

    if not trials:
        return pd.DataFrame(columns=["trialId", "Incl/Excl", "criterion_class"]).astype({"trialId": str})

    concat_trials = pd.concat(trials, ignore_index=True)
    if concat_trials.empty:
        return concat_trials.astype({"trialId": str})
    return concat_trials.sort_values(["trialId", "Incl/Excl"]).reset_index(drop=True)


def restructure_to_one_trial_per_row(instances_df: pd.DataFrame, searching_criterion: str, joining_delimiter: str = "; ") -> pd.DataFrame:
    if instances_df is None or instances_df.empty:
        return pd.DataFrame(columns=["trialId"]).astype({"trialId": str})

    core_cols = {"trialId", "Incl/Excl", "criterion_class"}
    other_cols = [c for c in instances_df.columns if c not in core_cols]

    if not other_cols:
        return pd.DataFrame({"trialId": sorted(instances_df["trialId"].astype(str).unique())})

    instances_df = instances_df.copy()

    for col in ("trialId", "Incl/Excl"):
        if col not in instances_df.columns:
            instances_df[col] = "" if col == "Incl/Excl" else instances_df.get(col, "")

    def _agg_unique(series: pd.Series) -> str:
        seen, out = set(), []
        for x in series.dropna().map(str).map(str.strip):  # Drop NaN, stringify, strip whitespace.
            if x and x not in seen:
                seen.add(x)  # Deduplicate via set()
                out.append(x)
        return joining_delimiter.join(out)

    group_by_trialid = []
    for direction in ("INCL", "EXCL"):
        sub = instances_df[instances_df["Incl/Excl"] == direction]  # rows in one direction only
        if sub.empty:
            continue

        grouped = sub.groupby("trialId", as_index=False)[other_cols].agg(_agg_unique)  # transform from long to wide format
        grouped = grouped.rename(columns={col: f"{direction}:{searching_criterion}-{col}" for col in other_cols})
        group_by_trialid.append(grouped)

    if not group_by_trialid:
        return pd.DataFrame({"trialId": sorted(instances_df["trialId"].astype(str).unique())})

    wide_tbl = group_by_trialid[0]  # headers
    for i in group_by_trialid[1:]:
        wide_tbl = wide_tbl.merge(i, on="trialId", how="outer")

    return wide_tbl.fillna("").astype({"trialId": str})


def main():
    parser = argparse.ArgumentParser(description="Extract specified criterion attributes from curated eligibility rules.")
    parser.add_argument("--curated_dir", type=Path, help="Directory containing curated NCT*.py files.", required=True)
    parser.add_argument("--searching_criterion", help="Criterion class name to extract (e.g., MolecularBiomarkerCriterion).", required=True)
    parser.add_argument("--output_dir", type=Path, help="Output directory to save the summary CSVs.", required=True)
    parser.add_argument("--log_level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], help="Logging level")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    args.output_dir.parent.mkdir(parents=True, exist_ok=True)

    instances_csv = args.output_dir / f"{args.searching_criterion}_instances.csv"
    aggregate_csv = args.output_dir / f"{args.searching_criterion}_aggregate.csv"

    instances_tbl = tabularise_criterion_instances_in_dir(args.curated_dir, args.searching_criterion)
    instances_tbl.to_csv(instances_csv, index=False)
    logger.info(f"Saved instance-level table to {instances_csv}")

    summary_tbl = restructure_to_one_trial_per_row(instances_tbl, args.searching_criterion)
    summary_tbl.to_csv(aggregate_csv, index=False)
    logger.info(f"Saved aggregate summary table to {aggregate_csv}")


if __name__ == "__main__":
    main()
