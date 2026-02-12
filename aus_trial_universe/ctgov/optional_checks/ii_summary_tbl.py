import argparse
import logging
import re
import ast
from pathlib import Path
from typing import Tuple, Optional
import pandas as pd

from aus_trial_universe.ctgov.resources.i_ctgov_trials_to_remove import trials_remove

logger = logging.getLogger(__name__)

# To match column name pattern
_COL_RE = re.compile(r"^(INCL|EXCL):(?:(?P<crit>[A-Za-z0-9_]+)-)?(?P<field>.+)$")

BLOOD_CANCER_TERMS = [
    # General
    "hematologic", "haematologic",
    "hematological malignancy", "haematological malignancy",
    "blood cancer", "blood cancers",

    # Leukemias
    "leukemia", "leukaemia",
    "acute leukemia",
    "acute myeloid leukemia", "aml",
    "acute lymphoblastic leukemia", "all",
    "acute promyelocytic leukemia", "apl",
    "chronic myeloid leukemia", "cml",
    "chronic lymphocytic leukemia", "cll",
    "hairy cell leukemia",
    "large granular lymphocytic leukemia",
    "lgl leukemia",
    "plasma cell leukemia",
    "Recurrent B Acute Lymphoblastic Leukemia",
    'Adult T Acute Lymphoblastic Leukemia', 'Ann Arbor Stage II Adult Lymphoblastic Lymphoma', 'Ann Arbor Stage II Childhood Lymphoblastic Lymphoma', 'Ann Arbor Stage III Adult Lymphoblastic Lymphoma', 'Ann Arbor Stage III Childhood Lymphoblastic Lymphoma', 'Ann Arbor Stage IV Adult Lymphoblastic Lymphoma', 'Ann Arbor Stage IV Childhood Lymphoblastic Lymphoma', 'Childhood T Acute Lymphoblastic Leukemia',
    "T-ALL", "T-lymphoblastic leukemia (T-ALL)", "T-lymphoblastic lymphoma (T-LLy)",
    "T-lymphoblastic leukemia", "T-lymphoblastic lymphoma",
    'B Acute Lymphoblastic Leukemia', 'B Acute Lymphoblastic Leukemia, BCR-ABL1-Like', 'Central Nervous System Leukemia', 'Testicular Leukemia',

    # Lymphomas
    "lymphoma",
    "hodgkin", "hodgkins", "hodgkin lymphoma",
    "non-hodgkin", "non hodgkin", "nhl",
    "dlbcl", "diffuse large b cell lymphoma",
    "follicular lymphoma",
    "mantle cell lymphoma", "mcl",
    "malt lymphoma",
    "burkitt lymphoma",
    "t cell lymphoma",
    "anaplastic large cell lymphoma", "alcl",
    "cutaneous t cell lymphoma", "ctcl",
    "peripheral t cell lymphoma", "ptcl",
    "waldenstrom", "waldenstrom macroglobulinemia",
    'Lymphoma, Mantle Cell', "Acute Myeloid Leukemia (AML)",
    'Acute Lymphoblastic Leukemia', 'B Acute Lymphoblastic Leukemia', 'Mixed Phenotype Acute Leukemia', 'T Acute Lymphoblastic Leukemia'

    # Myelomas
    "myeloma", "multiple myeloma",
    "smoldering myeloma", "smouldering myeloma",
    "mgus",
    "plasma cell neoplasm",
    "plasma cell dyscrasia",
    'Multiple Myeloma', "MM",

    # Myeloproliferative neoplasms (MPN)
    "mpn", "myeloproliferative",
    "polycythemia vera", "pv",
    "essential thrombocythemia", "et",
    "primary myelofibrosis", "pmf",
    "post-polycythemia vera myelofibrosis",
    "post-essential thrombocythemia myelofibrosis",

    # Myelodysplastic syndromes (MDS)
    "myelodysplastic", "myelodysplastic syndrome", "myelodysplastic syndromes",
    "mds",
    "chronic myelomonocytic leukemia", "cmml",
]

has_solid_tumor_override = ["NCT00107198","NCT01190930","NCT01371981","NCT01804686","NCT02213926","NCT02339740","NCT02386800","NCT02521493","NCT02568267","NCT02677922","NCT02684708","NCT02952508","NCT02966756","NCT03007147","NCT03075696","NCT03117751","NCT03155620","NCT03173248","NCT03244176","NCT03336333","NCT03570892","NCT03589326","NCT03590171","NCT03643276","NCT03666000","NCT03740529","NCT03817320","NCT03817398","NCT03839771","NCT03844048","NCT03850574","NCT03888105","NCT03914625","NCT03959085","NCT03960840","NCT04002297","NCT04023526","NCT04027309","NCT04049513","NCT04065399","NCT04077723","NCT04202835","NCT04224493","NCT04320888","NCT04416984","NCT04521231","NCT04546399","NCT04594642","NCT04603001","NCT04623541","NCT04637763","NCT04666038","NCT04680052","NCT04703192","NCT04712097","NCT04728893","NCT04759586","NCT04771130","NCT04811560","NCT04870944","NCT04884035","NCT04895436","NCT04914741","NCT04920617","NCT04965493","NCT04971226","NCT04989803","NCT04994717","NCT04996875","NCT05005299","NCT05006716","NCT05023980","NCT05057494","NCT05100862","NCT05171647","NCT05201066","NCT05206357","NCT05254743","NCT05255601","NCT05304377","NCT05365659","NCT05403450","NCT05409066","NCT05424822","NCT05453903","NCT05475925","NCT05476770","NCT05533775","NCT05605899","NCT05607498","NCT05618028","NCT05624554","NCT05665530","NCT05784441","NCT05788081","NCT05824585","NCT05828589","NCT05833763","NCT05864742","NCT05878184","NCT05883956","NCT05888493","NCT05947851","NCT05951959","NCT06022029","NCT06047080","NCT06079164","NCT06088654","NCT06091254","NCT06091865","NCT06097364","NCT06136559","NCT06137118","NCT06139406","NCT06149286","NCT06163430","NCT06191744","NCT06226571","NCT06230224","NCT06287398","NCT06291220","NCT06356129","NCT06372717","NCT06383338","NCT06392477","NCT06414148","NCT06425302","NCT06486051","NCT06526793","NCT06528301","NCT06549595","NCT06564038","NCT06588478","NCT06634589","NCT06651229","NCT06660563","NCT06667687","NCT06742996","NCT06852222","NCT06876649","NCT06876662","NCT06943872","NCT06973187","NCT07051525","NCT07082803","NCT07101328","NCT07123454","NCT07202052","NCT07202078","NCT07202091","NCT07215585","NCT01949129","NCT02724163","NCT05756322","NCT04419649","NCT04468984","NCT03441113","NCT03662126","NCT04064060","NCT04176198","NCT04454658","NCT04468984","NCT04562389","NCT04576156","NCT04603495","NCT04655118","NCT04679870","NCT04717414","NCT04817007","NCT05037760","NCT06351631","NCT06479135","NCT05405166","NCT05572515","NCT05552976","NCT05862012","NCT05519085","NCT05552976","NCT05572515","NCT05927571","NCT06425991","NCT03301220","NCT04973605","NCT05259839","NCT05308654","NCT05704049","NCT05730036","NCT06615479","NCT06669247","NCT06679101","NCT07075185","NCT05936359","NCT06366789","NCT06542250","NCT07008118","NCT07064122","NCT02942290","NCT03319667","NCT04277637","NCT04643002","NCT03537482","NCT03557619","NCT06179511"]


def _norm_term(s: str) -> str:
    s = s.lower()
    for ch in [",", ";", "/", "-", "(", ")", "'"]:
        s = s.replace(ch, " ")

    s = " ".join(s.split())
    return s


BLOOD_CANCER_TERM_SET = {_norm_term(t) for t in BLOOD_CANCER_TERMS}


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


def _load_pottr_trial_ids(pottr_path: Path) -> set[str]:
    pottr_df = pd.read_csv(pottr_path, sep="\t")

    if "trial_id" not in pottr_df.columns:
        raise ValueError(f"POTTR file {pottr_path} does not contain required column 'trial_id'.")

    ids = (
        pottr_df["trial_id"]
        .dropna()
        .astype(str)
        .str.strip()
    )

    return set(ids[ids != ""])


def _has_any_nonempty_criterion(summary_df: pd.DataFrame, crit_name: str) -> pd.Series:
    crit_mask = summary_df.columns.get_level_values("Criterion") == crit_name
    if not crit_mask.any():
        logger.warning("No %s columns found; presence flag will be False for all rows", crit_name)
        return pd.Series(False, index=summary_df.index)

    sub = summary_df.loc[:, crit_mask]

    cleaned = sub.astype(str).map(lambda x: x.strip())
    non_empty = cleaned.ne("") & sub.notna()

    return non_empty.any(axis=1)

# has_solid_tumor logic:
# Collect non-empty tumour-related texts from:
# 1.Core.conditions
# 2. INCL/EXCL PrimaryTumorCriterion: primary_tumor_location, primary_tumor_type
#
# If no non-empty texts exist
# → has_solid_tumor = None
#
# Else:
# Flag each text as blood-cancer or non-blood.
# has_solid_tumor = False iff all texts are blood cancers
# has_solid_tumor = True otherwise.


def _expand_cell_to_terms(val) -> list[str]:
    if pd.isna(val):
        return []

    if isinstance(val, (list, tuple, set)):
        return [str(x).strip() for x in val if str(x).strip()]

    s = str(val).strip()
    if not s:
        return []

    if s.startswith("[") and s.endswith("]"):
        try:
            parsed = ast.literal_eval(s)
            if isinstance(parsed, (list, tuple, set)):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except (SyntaxError, ValueError):
            pass

    return [s]


def annotate_summary(summary_df: pd.DataFrame, pottr_path: Optional[Path]) -> pd.DataFrame:
    df = summary_df.copy()

    # --- 1. POTTR membership ---
    if pottr_path and pottr_path.exists():
        pottr_ids = _load_pottr_trial_ids(pottr_path)
        if not pottr_ids:
            logger.warning("No trial_id values loaded from POTTR file %s", pottr_path)
    else:
        pottr_ids: set[str] = set()
        logger.warning(
            "POTTR trials file not provided or does not exist; "
            "in_POTTR will be False for all rows."
        )

    df[("", "Core", "in_POTTR")] = df.index.astype(str).isin(pottr_ids)

    # --- 2. Solid tumour flag from Core.conditions + PrimaryTumorCriterion (INCL/EXCL) ---
    relevant_cols = [
        ("", "Core", "conditions"),
        # ("INCL", "PrimaryTumorCriterion", "primary_tumor_location"),
        # ("INCL", "PrimaryTumorCriterion", "primary_tumor_type"),
        # ("EXCL", "PrimaryTumorCriterion", "primary_tumor_location"),
        # ("EXCL", "PrimaryTumorCriterion", "primary_tumor_type"),
    ]
    relevant_cols = [col for col in relevant_cols if col in df.columns]

    if not relevant_cols:
        logger.warning("No Core.conditions or PrimaryTumorCriterion primary_tumor_* columns found")

    def _row_has_solid_tumor(row: pd.Series) -> Optional[bool]:
        terms: list[str] = []
        for col in relevant_cols:
            terms.extend(_expand_cell_to_terms(row[col]))

        if not terms:
            return None

        all_blood = True
        for term in terms:
            norm = _norm_term(term)
            if norm in BLOOD_CANCER_TERM_SET:
                continue
            else:
                all_blood = False
                break

        if all_blood:
            return False
        return True

    df[("", "Core", "has_solid_tumor")] = df.apply(_row_has_solid_tumor, axis=1)

    # --- 3. Biomarker / signature / gene alteration presence flags ---
    for crit, field in [
        ("MolecularBiomarkerCriterion", "has_molecular_biomarker"),
        ("MolecularSignatureCriterion", "has_molecular_signature"),
        ("GeneAlterationCriterion", "has_gene_alteration"),
    ]:
        df[("", "Core", field)] = _has_any_nonempty_criterion(df, crit)

    # --- 4. Any biomarker of any type ---
    df[("", "Core", "has_any_biomarker")] = (
        df[("", "Core", "has_molecular_biomarker")]
        | df[("", "Core", "has_molecular_signature")]
        | df[("", "Core", "has_gene_alteration")]
    )

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
        "minAge": 10,
        "maxAge": 11,
        "in_POTTR": 12,
        "has_solid_tumor": 13,
        "has_molecular_biomarker": 14,
        "has_molecular_signature": 15,
        "has_gene_alteration": 16,
        "has_any_biomarker": 17,
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
    parser = argparse.ArgumentParser(description=("Build a multi-index summary table from ctgov_field_extractions.csv."))
    parser.add_argument("--drug_dir", type=Path, required=True, help="Directory containing ctgov_field_extractions.csv (Core / non-criterion fields).")
    parser.add_argument("--criterion_dir", type=Path, required=True, help="Directory containing criterion *_extractions.csv files.")
    parser.add_argument("--curation_dir", type=Path, required=True, help="Directory containing curated NCT*.py outputs.")
    parser.add_argument("--more_annotation", required=False, action="store_true", help="Additional annotation with POTTR, solid tumor and biomarker presence")
    parser.add_argument("--POTTR_trials", type=Path, required=False, help="Filepath to the latest POTTR trials")
    parser.add_argument("--output_dir", type=Path, required=True, help="Directory to save the output Excel file and logs.")
    parser.add_argument("--log_level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], help="Logging level")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    if args.more_annotation and args.POTTR_trials is None:
        parser.error("--more_annotation requires --POTTR_trials")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Starting aggregate table build...")

    summary_df = build_multiindex_summary(args.drug_dir, args.criterion_dir, args.curation_dir)

    # Extra annotations (POTTR + solid tumour + biomarker presence)
    if args.more_annotation:
        logger.info("Appending additional annotations")
        summary_df = annotate_summary(summary_df, args.POTTR_trials)

        if has_solid_tumor_override:
            override_set = {tid.lower() for tid in has_solid_tumor_override}

            # Convert trial index to lowercase for safe matching
            idx_lower = summary_df.index.astype(str).str.lower()

            mask = idx_lower.isin(override_set)

            before_n = mask.sum()
            summary_df.loc[mask, ("", "Core", "has_solid_tumor")] = False

            logger.info(
                "Applied has_solid_tumor override: %d trials set to FALSE",
                before_n,
            )

    # Reorder columns after all annotations are added
    summary_df = reorder_columns(summary_df)

    # drop rows where Core/interventionName is empty
    core_intervention_col = ("Core", "", "interventionName")
    if core_intervention_col in summary_df.columns:
        before_n = len(summary_df)
        col = summary_df[core_intervention_col]
        non_empty_mask = col.notna() & (col.astype(str).str.strip() != "")
        summary_df = summary_df[non_empty_mask]
        after_n = len(summary_df)
        logger.info(
            "Filtered rows with empty Core/interventionName: before=%d, after=%d",
            before_n, after_n)
    else:
        logger.warning("Column Core/interventionName not found; skipping empty-interventionName filtering.")

    # remove irrelevant trials
    before_n = len(summary_df)
    summary_df = summary_df.loc[~summary_df.index.astype(str).isin(trials_remove)]
    after_n = len(summary_df)
    logger.info("Filtered rows after trials removal: before=%d, after=%d", before_n, after_n)

    excel_path = args.output_dir / "aus_trial_universe_2.xlsx"
    with pd.ExcelWriter(excel_path) as xlw:
        summary_df.to_excel(xlw, index=True)
    logger.info("Wrote output to %s", excel_path)

    logger.info("Completed")


if __name__ == "__main__":
    main()
