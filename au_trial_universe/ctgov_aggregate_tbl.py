import argparse
import logging
import re
from pathlib import Path
from typing import Tuple, Optional
import pandas as pd


logger = logging.getLogger(__name__)

# To match column name pattern
_COL_RE = re.compile(r"^(INCL|EXCL):(?:(?P<crit>[A-Za-z0-9_]+)-)?(?P<field>.+)$")


def _parse_col_label(col: str, default_criterion: Optional[str] = None) -> Tuple[str, str, str]:
    if col == "trialId":
        return ("", "Core", "trialId")

    m = _COL_RE.match(col)
    if not m:
        return ("", default_criterion or "Core", col)

    direction, crit, field = m.groups()
    if crit is None:
        return (direction, default_criterion or "Core", field)
    return (direction, crit, field)


def _load_and_tag(file_path: Path) -> pd.DataFrame:
    df = pd.read_csv(file_path, dtype={"trialId": str})

    stem = file_path.name
    if stem.endswith("_extractions.csv"):
        default_criterion = stem[:-len("_extractions.csv")]
    else:
        default_criterion = None

    tuples = [_parse_col_label(c, default_criterion=default_criterion) for c in df.columns]
    df.columns = pd.MultiIndex.from_tuples(tuples, names=["Direction", "Criterion", "Field"])
    return df


def build_multiindex_summary(drug_dir: Path, criterion_dir: Path, curation_dir: Path) -> pd.DataFrame:
    merged: Optional[pd.DataFrame] = None

    ctgov_path = drug_dir / "ctgov_field_extractions.csv"
    if ctgov_path.exists():
        logger.info("Loading ctgov field extractions (Core) from %s", ctgov_path)
        ctgov_df = pd.read_csv(ctgov_path, dtype={"nctId": str, "trialId": str})

        if "trialId" in ctgov_df.columns:
            id_col = "trialId"
        elif "nctId" in ctgov_df.columns:
            ctgov_df = ctgov_df.rename(columns={"nctId": "trialId"})
            id_col = "trialId"
        else:
            logger.error("ctgov_field_extractions.csv lacks 'nctId' or 'trialId'.")
            ctgov_df = None

        if ctgov_df is not None:
            if curation_dir is not None:
                trial_ids = set(ctgov_df[id_col].astype(str))
                py_stems = {p.stem for p in curation_dir.glob("*.py")}
                missing = sorted(trial_ids - py_stems)
                if missing:
                    logger.warning(
                        "Trials in ctgov_field_extractions.csv without matching curation .py outputs: %s",
                        ", ".join(missing),
                    )

            ctgov_df[id_col] = ctgov_df[id_col].astype(str)
            ctgov_df = ctgov_df.set_index(id_col)

            mi = pd.MultiIndex.from_tuples(
                [("", "Core", c) for c in ctgov_df.columns],
                names=["Direction", "Criterion", "Field"],
            )
            ctgov_df.columns = mi
            ctgov_df.index.name = "trialId"
            merged = ctgov_df
    else:
        logger.warning("ctgov_field_extractions.csv not found in %s.", drug_dir)

    criteria_files = [
        "PrimaryTumorCriterion",
        "MolecularBiomarkerCriterion",
        "MolecularSignatureCriterion",
        "GeneAlterationCriterion",
    ]

    for crit in criteria_files:
        path = criterion_dir / f"{crit}_extractions.csv"
        if not path.exists():
            logger.warning("%s not found in %s; skipping.", path.name, criterion_dir)
            continue

        logger.info("Loading %s", path.name)
        part = _load_and_tag(path)
        # Use the Core trialId column as index
        part = part.set_index(("", "Core", "trialId"))
        if merged is None:
            merged = part
        else:
            merged = merged.join(part, how="outer")

    if merged is None:
        logger.warning("No extraction CSVs could be loaded from %s or %s", drug_dir, criterion_dir)
        return pd.DataFrame(
            columns=pd.MultiIndex.from_tuples([("", "Core", "trialId")])
        )

    merged.index.name = "trialId"
    return merged


def reorder_columns(summary_df: pd.DataFrame) -> pd.DataFrame:
    cols = summary_df.columns
    if not isinstance(cols, pd.MultiIndex):
        return summary_df

    summary_df = summary_df.copy()
    summary_df.columns = cols.reorder_levels(["Criterion", "Direction", "Field"])

    tuples = list(summary_df.columns)

    criterion_order = {
        "Core": 0,
        "PrimaryTumorCriterion": 1,
        "MolecularBiomarkerCriterion": 2,
        "MolecularSignatureCriterion": 3,
        "GeneAlterationCriterion": 4,
    }

    core_field_order = {
        "briefTitle": 0,
        "conditions": 1,
        "interventionName": 2,
        "interventionOtherNames": 3,
        "interventionType": 4,
        "leadSponsor": 5,
        "phases": 6,
        "status": 7,
        "facility": 8,
        "address": 9,

    }

    direction_order = {
        "INCL": 0,
        "EXCL": 1,
    }

    def _col_sort_key(t):
        crit, direction, field = t
        crit = crit or ""
        direction = direction or ""
        field = field or ""

        if crit == "Core":
            return (
                criterion_order.get(crit, 50),
                direction_order.get(direction, 50),
                0,
                core_field_order.get(field, 999),
                field,
            )
        else:
            return (
                criterion_order.get(crit, 50),
                direction_order.get(direction, 50),
                1,
                0 if field == "input_text" else 1,
                field,
            )

    sorted_cols = sorted(tuples, key=_col_sort_key)
    summary_df = summary_df[sorted_cols]
    return summary_df


def main():
    parser = argparse.ArgumentParser(description=("Build a multi-index summary table from ctgov_field_extractions.csv (all non-criterion 'Core' fields) and criterion-specific *_extractions.csv files."))
    parser.add_argument("--drug_dir", type=Path, required=True, help="Directory containing ctgov_field_extractions.csv (Core / non-criterion fields).")
    parser.add_argument("--criterion_dir", type=Path, required=True, help="Directory containing criterion *_extractions.csv files.")
    parser.add_argument("--curation_dir", type=Path, required=True, help="Directory containing curated NCT*.py outputs.")
    parser.add_argument("--output_dir", type=Path, required=True, help="Directory to save the output Excel file and logs.")
    parser.add_argument("--log_level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], help="Logging level")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Starting aggregate table build...")

    summary_df = build_multiindex_summary(args.drug_dir, args.criterion_dir, args.curation_dir)
    summary_df = reorder_columns(summary_df)

    # drop rows where Core/interventionName is empty
    core_intervention_col = ("Core", "", "interventionName")
    if core_intervention_col in summary_df.columns:
        before_n = len(summary_df)
        col = summary_df[core_intervention_col]
        non_empty_mask = col.notna() & (col.astype(str).str.strip() != "")
        summary_df = summary_df[non_empty_mask]
        after_n = len(summary_df)
        logger.info("Filtered rows with empty Core/interventionName: before=%d, after=%d", before_n, after_n)
    else:
        logger.warning("Column Core/interventionName not found; skipping empty-interventionName filtering.")

    excel_path = args.output_dir / "summary_tbl.xlsx"
    with pd.ExcelWriter(excel_path) as xlw:
        summary_df.to_excel(xlw, index=True)
    logger.info("Wrote output to %s", excel_path)

    logger.info("Completed")


if __name__ == "__main__":
    main()
