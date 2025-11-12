import argparse
import logging
from pathlib import Path
from typing import Any, List, Dict
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


def load_curated_rules(py_filepath: Path) -> list[Any] | None:
    import pydantic_curator.criterion_schema as cs

    def _make_shim(class_name: str):
        def __init__(self, *_, **kwargs):
            self.__dict__.update(kwargs)  # stores any fields. No Pydantic validation

        return type(class_name, (), {"__init__": __init__})

    module_globs: dict[str, Any] = {"__builtins__": __builtins__}

    module_globs["BaseModel"] = type("BaseModel", (), {})
    module_globs["TypedModel"] = type("TypedModel", (), {})

    _known_noncriterion_models = {
        "Chemotherapy", "TargetedTherapy", "Immunotherapy", "HormonalTherapy",
        "RadiationTherapy", "Surgery", "Regimen", "Medication", "Drug",
        "IntRange", "FloatRange", "DateRange", "Range", "Dose", "Schedule"
    }

    for name in dir(cs):
        obj = getattr(cs, name)
        if not isinstance(obj, type):
            module_globs[name] = obj
            continue

        is_pydanticish = getattr(obj, "__pydantic_validator__", None) is not None
        name_looks_like_schema = (
                name.endswith("Criterion") or
                name.endswith("Range") or
                name in _known_noncriterion_models
        )

        if is_pydanticish or name_looks_like_schema:
            module_globs[name] = _make_shim(name)
        else:
            module_globs[name] = obj

    class Rule:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    module_globs["Rule"] = Rule

    try:
        code = py_filepath.read_text(encoding="utf-8")
        compiled = compile(code, str(py_filepath), "exec")
        exec(compiled, module_globs)
    except SyntaxError as e:
        logger.error(f"SyntaxError in {py_filepath}:{e.lineno}\n{e.msg}")
        return None
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

    matched_criterion_list = [starting_criterion] if type(
        starting_criterion).__name__ == searching_criterion else []  # Cannot directly compare because `starting_criterion` is an object instance & `searching_criterion` is a string

    for attr_name in ("criteria", "criterion"):
        children_criterion = getattr(starting_criterion, attr_name, ())

        if not isinstance(children_criterion, (list, tuple)):
            children_criterion = (
                children_criterion,) if children_criterion is not None else ()  # to ensure `children_criterion` is normalised into a tuple, empty or otherwise. Because we need to iterate through a tuple afterwards

        for child_criterion in children_criterion:
            matched_criterion_list.extend(
                criterion_extraction_dfs(child_criterion, searching_criterion)
            )

    return matched_criterion_list


def tabularise_criterion_instances_per_file(py_filepath: Path, searching_criterion: str) -> pd.DataFrame:
    curated_rules = load_curated_rules(py_filepath)
    if not curated_rules:  # Not logging an error again because errors would have been logged by the loader function
        return pd.DataFrame(columns=["trialId", "Incl/Excl", "criterion_class"]).astype(
            {"trialId": str})  # to avoid data type inconsistency because `trialId` is used as the matching key later

    trialId = py_filepath.stem
    rows: List[Dict[str, Any]] = []

    for rule in curated_rules:
        if bool(getattr(rule, "exclude", False)):  # If `rule` doesn’t have the attribute `exclude`, return False (i.e. assume it's an inclusive rule)
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
            else pd.DataFrame(columns=["trialId", "Incl/Excl", "criterion_class"]).astype({"trialId": str})
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
        return pd.DataFrame(columns=["trialId", "Incl/Excl", "criterion_class"]).astype({"trialId": str})

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

    def _agg_preserve_sequence(series: pd.Series) -> str:
        vals = []
        for ele in series:
            if pd.notna(ele):
                ele = str(ele).strip()
                if ele:  # only consider non-empty elements
                    vals.append(ele)

        return joining_delimiter.join(vals)

    grouped_list = []
    for direction in ("INCL", "EXCL"):
        direction_df = instances_df.loc[instances_df["Incl/Excl"] == direction, :]
        direction_df = direction_df.sort_values(by=["trialId", "_number_rows"], kind="stable")  # to use pandas' stable sorting algorithm - across values with the same trialId, keep the original row order
        if direction_df.empty:
            continue

        grouped_df = direction_df.groupby("trialId", as_index=False, sort=False) \
            [other_cols] \
            .agg(_agg_preserve_sequence)
        grouped_df = grouped_df.rename(columns={col: f"{direction}:{searching_criterion}-{col}" for col in other_cols})
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


# Things to do:
# 1. To include a row for EVERY trial, even if there is no associated criterion data
# 2. Include `InfectionCriterion`
# 3. Under "description", extract the parent-level or input_text field
# 4. Count the no. criteria inside the corresponding rule()
