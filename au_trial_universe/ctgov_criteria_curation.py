from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Dict

import pandas as pd

logger = logging.getLogger(__name__)


def _as_curation_set(value) -> set:
    if pd.isna(value):
        return set()

    s = str(value).strip()
    if not s:
        return set()

    parts = [p.strip() for p in s.split(",") if p.strip()]
    return set(parts)


def _normalise_curation_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    for col in df.columns:
        if "curation" in col.lower():
            df[col] = df[col].apply(_as_curation_set)
    return df


# 1. load lookup tables (resource files)
def load_resource_tables_from_dir(resource_dir: Path) -> Dict[str, pd.DataFrame]:
    outputs: Dict[str, pd.DataFrame] = {}

    for file in Path(resource_dir).glob("*.csv"):
        name = file.stem
        df = pd.read_csv(file)
        df.columns = df.columns.str.strip()

        df = _normalise_curation_columns(df)
        outputs[name] = df
        logger.info(f"Loaded resource table: {name}")

    return outputs


# 2. load instance-level extraction files
def load_instance_tables_from_dir(instance_dir: Path) -> Dict[str, pd.DataFrame]:
    outputs: Dict[str, pd.DataFrame] = {}

    for file in Path(instance_dir).glob("*_instances.csv"):
        name = file.stem.replace("_instances", "")
        df = pd.read_csv(file, dtype={"trialId": str})
        df["criterion_class"] = name

        outputs[name] = df
        logger.info(f"Loaded instance table: {name} ({df.shape[0]} rows)")

    return outputs


# 3. Vertically concat instance-level tables
def concat_instance_tables(instance_tables: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    if not instance_tables:
        raise ValueError("No instance tables provided")

    df_all = pd.concat(instance_tables.values(), ignore_index=True, sort=False)

    base = ["trialId", "Incl/Excl", "criterion_class"]
    for col in base:
        if col not in df_all.columns:
            df_all[col] = pd.NA

    others = [c for c in df_all.columns if c not in base]

    return df_all[base + others]


# 4. Apply standardization lookup + criterion Move_to logic
def apply_curation_lookup(df: pd.DataFrame, resources: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """
    Apply standardised curation rules to the unified instance dataframe.

    For each resource table:
      1. Identify all lookup columns (e.g. "GeneAlterationCriterion_lookup_gene")
         Each implies:
             - The criterion class this table applies to (before "_lookup_")
             - The instance column to match on (after "_lookup_")

      2. For each row in the instance DF:
             if row.criterion_class == target_criterion:
                 normalise the instance field → lowercase trimmed string
                 look up matching row(s) in the resource table
                 for each match:
                     - extract all curation_* columns (already sets)
                     - optionally redirect to another criterion via Move_to
                     - union the set into df[row, output_col]
    """
    df = df.copy()

    def norm(v: object) -> str:
        if pd.isna(v):
            return ""
        return str(v).strip().lower()

    def ensure_col(col: str):
        if col not in df.columns:
            df[col] = [set() for _ in range(len(df))]

    # Iterate over each resource lookup table
    for name, tbl_raw in resources.items():
        logger.info(f"Applying resource: {name}")
        tbl = tbl_raw.copy()

        lookup_cols = [c for c in tbl.columns if "_lookup_" in c]
        curation_cols = [c for c in tbl.columns if "curation" in c.lower()]
        has_move_to = "Move_to" in tbl.columns

        if not lookup_cols or not curation_cols:
            logger.warning(f"  {name}: missing lookup or curation columns — skipped.")
            continue

        # Normalise lookup keys
        for lc in lookup_cols:
            tbl[lc] = tbl[lc].apply(norm)

        # For each lookup column
        for lookup_col in lookup_cols:
            crit_prefix, lookup_field = lookup_col.split("_lookup_", 1)

            # Instance must have the lookup field
            if lookup_field not in df.columns:
                logger.warning(f"  {name}: instance DF missing field '{lookup_field}' — skipped.")
                continue

            # Only rows whose criterion_class matches this resource prefix
            idxs = df.index[df["criterion_class"] == crit_prefix]

            matched_rows = 0
            match_events = 0

            # Apply matching to each relevant instance row
            for row in idxs:
                inst_key = norm(df.at[row, lookup_field])
                if not inst_key:
                    continue

                hits = tbl[tbl[lookup_col] == inst_key]
                if hits.empty:
                    continue

                matched_rows += 1

                # For each matching resource table row
                for _, hit in hits.iterrows():
                    move_target = hit["Move_to"].strip() if (
                                has_move_to and isinstance(hit.get("Move_to"), str)) else ""

                    for cur_col in curation_cols:
                        vals = hit[cur_col]
                        if not vals:
                            continue  # empty set

                        # Decide output column (Move_to redirects)
                        if move_target:
                            suffix = cur_col.split("_curation_", 1)[1]
                            out_col = f"{move_target}_curation_{suffix}"
                        else:
                            out_col = cur_col

                        ensure_col(out_col)

                        df.at[row, out_col] = df.at[row, out_col] | vals
                        match_events += 1

            logger.info(f"  lookup '{lookup_col}': matched {matched_rows} rows ({match_events} events)")

    return df


# 5. create matching rules
def _split_curation_cell(cell: str) -> set[str]:
    if pd.isna(cell):
        return set()

    s = str(cell).strip()
    if not s:
        return set()

    return {part.strip() for part in s.split(";") if part.strip()}


def build_trial_level_rules(df_curated: pd.DataFrame) -> pd.DataFrame:
    """
    Build one logical selection rule per trialId from the curated row-level dataframe.

    Logic (Option A, row-based):
      - Each row is a composite condition built from that row's curated values.
      - Within a row:
          * gene + alteration → cross product: 'GENE ALTERATION'
          * other curated columns → literals for each value
          * all literals in the row are OR'ed
      - Across rows:
          * INCL rows → AND of row-level clauses
          * EXCL rows → OR of NOT(row-level clause)
      - GeneAlterationCriterion_curation_type is ignored completely.
    """
    # Identify curation columns, excluding 'type'
    curation_cols = [
        c for c in df_curated.columns
        if "curation" in c.lower()
        and c != "GeneAlterationCriterion_curation_type"
    ]

    gene_col = "GeneAlterationCriterion_curation_gene"
    alt_col = "GeneAlterationCriterion_curation_alteration"

    trial_rules: list[dict[str, str]] = []

    for trial_id, group in df_curated.groupby("trialId"):
        incl_row_clauses: list[str] = []
        excl_row_clauses: list[str] = []

        for _, row in group.iterrows():
            row_literals: set[str] = set()

            # --- special handling: gene × alteration cross product ---
            gene_vals = _split_curation_cell(row[gene_col]) if gene_col in group.columns else set()
            alt_vals = _split_curation_cell(row[alt_col]) if alt_col in group.columns else set()

            if gene_vals and alt_vals:
                for g in gene_vals:
                    for a in alt_vals:
                        row_literals.add(f"{g} {a}")
            elif gene_vals:
                row_literals |= gene_vals
            elif alt_vals:
                row_literals |= alt_vals

            # --- other curation columns ---
            other_curation_cols = [
                c for c in curation_cols
                if c not in {gene_col, alt_col}
            ]

            for col in other_curation_cols:
                vals = _split_curation_cell(row[col])
                row_literals |= vals

            if not row_literals:
                continue  # this row contributes nothing

            # Build row-level clause (OR within row)
            if len(row_literals) == 1:
                row_clause = next(iter(row_literals))
            else:
                row_clause = "(" + " OR ".join(sorted(row_literals)) + ")"

            if row["Incl/Excl"] == "EXCL":
                # EXCL rows: NOT(row_clause)
                excl_row_clauses.append(f"NOT({row_clause})")
            else:
                # Treat everything else as INCL
                incl_row_clauses.append(row_clause)

        # Combine INCL and EXCL parts for this trial
        incl_expr = " AND ".join(
            f"({c})" if " OR " in c else c for c in incl_row_clauses
        ) if incl_row_clauses else ""

        excl_expr = " OR ".join(excl_row_clauses) if excl_row_clauses else ""

        if incl_expr and excl_expr:
            full_expr = f"({incl_expr}) AND ({excl_expr})"
        elif incl_expr:
            full_expr = incl_expr
        elif excl_expr:
            full_expr = excl_expr
        else:
            full_expr = ""

        trial_rules.append(
            {"trialId": trial_id, "selection_rule": full_expr}
        )

    return (
        pd.DataFrame(trial_rules)
        .sort_values("trialId")
        .reset_index(drop=True)
    )



def main():
    parser = argparse.ArgumentParser(description="Standardise LLM criterion field using curated lookup tables")
    parser.add_argument("--instance_dir", type=Path, required=True, help="Directory containing the *_instances.csv files")
    parser.add_argument("--resource_dir", type=Path, required=True, help="Directory containing resource lookup tables used for standardisation")
    parser.add_argument("--output_dir", type=Path, required=True, help="Directory where the final curated output will be written")
    parser.add_argument("--log_level", default="INFO", help="Logging verbosity (DEBUG, INFO, WARNING, ERROR). Default: INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Loading resource lookup tables...")
    resources = load_resource_tables_from_dir(args.resource_dir)
    logger.info(f"Loaded {len(resources)} resource table(s).")

    logger.info("Loading instance-level extracted criteria...")
    instances = load_instance_tables_from_dir(args.instance_dir)
    logger.info(f"Loaded {len(instances)} instance table(s).")

    df_combined = concat_instance_tables(instances)
    df_combined = _normalise_curation_columns(df_combined)
    logger.info(f"Combined instance rows: {df_combined.shape[0]}")

    df_curated = apply_curation_lookup(df_combined, resources)
    logger.info(f"After curation: {df_curated.shape[0]} rows, {df_curated.shape[1]} columns")

    # Drop unneeded raw input fields
    lookup_fields = {
        col.split("_lookup_", 1)[1]
        for tbl in resources.values()
        for col in tbl.columns
        if "_lookup_" in col
    }

    base_keep = {"trialId", "Incl/Excl", "criterion_class"}
    curation_cols = {c for c in df_curated.columns if "curation" in c.lower()}

    keep_cols = base_keep | lookup_fields | curation_cols
    df_curated = df_curated[[c for c in df_curated.columns if c in keep_cols]]

    def serialise_set(value):
        if not isinstance(value, set) or not value:
            return ""
        return ";".join(sorted(value))

    for col in curation_cols:
        if col in df_curated.columns:
            df_curated[col] = df_curated[col].apply(serialise_set)

    output_file = args.output_dir / "criterion_curations.csv"
    df_curated.to_csv(output_file, index=False)
    logger.info(f"Finished curation. Wrote curated output to: {output_file}")

    # Build trial-level logical rules
    trial_rules_df = build_trial_level_rules(df_curated)
    trial_rules_file = args.output_dir / "selection_rules.csv"
    trial_rules_df.to_csv(trial_rules_file, index=False)
    logger.info(f"Finishing matching rules. Wrote trial-level selection rules to: {trial_rules_file}")


if __name__ == "__main__":
    main()
