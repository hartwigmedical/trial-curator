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

    matched_criterion_list = [starting_criterion] if type(starting_criterion).__name__ == searching_criterion else []

    for attr_name in ("criteria", "criterion"):
        children_criterion = getattr(starting_criterion, attr_name, ())
        if not isinstance(children_criterion, (List, tuple)):
            children_criterion = (children_criterion,) if children_criterion is not None else ()

        for child_criterion in children_criterion:
            matched_criterion_list.extend(
                criterion_extraction_dfs(child_criterion, searching_criterion)
            )

    return matched_criterion_list


def tabularise_criterion_instances_per_file(py_filepath: Path, searching_criterion: str) -> pd.DataFrame:
    curated_rules = load_curated_rules(py_filepath)
    if not curated_rules:
        logger.error(f"No curated rules found in {py_filepath}.")
        return pd.DataFrame(columns=["trialId", "direction", "criterion_class"]).astype({"trialId": str})

    trialId = py_filepath.stem
    rows: List[Dict[str, Any]] = []

    for rule in curated_rules:
        if getattr(rule, "exclude"):
            direction = "EXCL"
        else:
            direction = "INCL"

        curations = getattr(rule, "curation", None)
        for crit in criterion_extraction_dfs(starting_criterion=curations, searching_criterion=searching_criterion):
            row = {
                "trialId": trialId,
                "direction": direction,
                "criterion_class": type(crit).__name__,
            }

            for key, val in vars(crit).items():
                if key.startswith("_") or callable(val) or val is None:  # ignore empty/internal data
                    continue

                row[key] = "; ".join(map(str, val)) if isinstance(val, (List, tuple, set)) else str(val)

            rows.append(row)

    return (
        pd.DataFrame(rows)
        if rows
        else pd.DataFrame(columns=["trialId", "direction", "criterion_class"]).astype({"trialId": str})
    )


def tabularise_criterion_instances_in_dir(py_dir: Path, searching_criterion: str) -> pd.DataFrame:
    rows: List[pd.DataFrame] = []

    for filepath in sorted(py_dir.glob("NCT*.py")):
        try:
            rows.append(
                tabularise_criterion_instances_per_file(filepath,searching_criterion)
            )
        except Exception as e:
            logger.exception(f"Failed on {filepath}: {e}")

    if not rows:
        return pd.DataFrame(columns=["trialId", "direction", "criterion_class"]).astype({"trialId": str})

    combined = pd.concat(rows, ignore_index=True)
    if combined.empty:
        return combined.astype({"trialId": str})
    return combined.sort_values(["trialId", "direction"]).reset_index(drop=True)


def restructure_to_one_trial_per_row(instances_df: pd.DataFrame, searching_criterion: str, joiner: str = "; ") -> pd.DataFrame | None:
    if instances_df is None or instances_df.empty:
        return pd.DataFrame(columns=["trialId"]).astype({"trialId": str})

    id_cols = {"trialId", "direction", "criterion_class"}
    attr_cols = [c for c in instances_df.columns if c not in id_cols]

    if not attr_cols:
        return pd.DataFrame({"trialId": sorted(instances_df["trialId"].astype(str).unique())})

    for col in ("trialId", "direction"):
        if col not in instances_df.columns:
            instances_df[col] = "" if col == "direction" else instances_df.get(col, "")

    def _agg_unique(series: pd.Series) -> str:
        vals = [str(x).strip() for x in series.dropna().tolist() if str(x).strip()]
        return joiner.join(sorted(set(vals))) if vals else ""

    collapsed_by_dir = []
    for direction in ("INCL", "EXCL"):
        sub = instances_df[instances_df["direction"] == direction]
        if sub.empty:
            continue

        present_attrs = [c for c in attr_cols if c in sub.columns]
        if not present_attrs:
            continue

        grouped = sub.groupby("trialId", as_index=False)[present_attrs].agg(_agg_unique)
        grouped = grouped.rename(columns={col: f"{direction}:{searching_criterion}-{col}" for col in present_attrs})
        collapsed_by_dir.append(grouped)

    if not collapsed_by_dir:
        return pd.DataFrame({"trialId": sorted(instances_df["trialId"].astype(str).unique())})

    wide = collapsed_by_dir[0]
    for extra in collapsed_by_dir[1:]:
        wide = wide.merge(extra, on="trialId", how="outer")

    return wide.fillna("").astype({"trialId": str})


def main():
    parser = argparse.ArgumentParser(description="Extract specified criterion attributes from curated eligibility rules.")
    parser.add_argument("--curated_dir", type=Path, help="Directory containing curated NCT*.py files.", required=True)
    parser.add_argument("--searching_criterion", help="Criterion class name to extract (e.g., MolecularBiomarkerCriterion).", required=True)
    parser.add_argument("--output_csv_path", type=Path, help="CSV file path to save the summary table.", required=True)
    parser.add_argument("--log_level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], help="Logging level")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    args.output_csv_path.parent.mkdir(parents=True, exist_ok=True)

    instances_tbl = tabularise_criterion_instances_in_dir(args.curated_dir, args.searching_criterion)

    instances_csv = args.output_csv_path.with_name(args.output_csv_path.stem + "_instances.csv")
    instances_tbl.to_csv(instances_csv, index=False)
    logger.info(f"Saved instance-level table to {instances_csv}")

    summary_tbl = restructure_to_one_trial_per_row(instances_tbl, args.searching_criterion)
    summary_tbl.to_csv(args.output_csv_path, index=False)
    logger.info(f"Saved collapsed summary table to {args.output_csv_path}")


if __name__ == "__main__":
    main()
