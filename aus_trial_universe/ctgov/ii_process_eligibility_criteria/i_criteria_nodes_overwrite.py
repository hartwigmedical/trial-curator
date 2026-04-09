from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

from aus_trial_universe.ctgov.i_download_trials_and_extract_eligibility.utils.load_curated_rules import (
    load_curated_rules,
)
from aus_trial_universe.ctgov.utils.general.write_curated_rules import (
    write_rules_py,
)
from aus_trial_universe.ctgov.ii_process_eligibility_criteria.criteria_registry import (
    SUPPORTED_CRITERIA,
)

from aus_trial_universe.ctgov.ii_process_eligibility_criteria.cancer_types import (
    PrimaryTumorMap,
    build_primary_tumor_map,
    overwrite_primary_tumour_in_rules,
)
from aus_trial_universe.ctgov.ii_process_eligibility_criteria.gene_alterations import (
    GeneAlterationMap,
    build_gene_alteration_map,
    overwrite_gene_alteration_in_rules,
)
from aus_trial_universe.ctgov.ii_process_eligibility_criteria.molecular_signature import (
    MolecularSignatureMap,
    build_molecular_signature_map,
    overwrite_molecular_signature_in_rules,
)

logger = logging.getLogger(__name__)


# ---------------------------
# Generic helpers
# ---------------------------

def _iter_input_py_files(curated_dir: Path) -> Iterable[Path]:
    if curated_dir.is_file():
        yield curated_dir
        return
    yield from sorted(p for p in curated_dir.glob("NCT*.py") if p.is_file())


def _pick_most_recent(paths: List[Path]) -> Path:
    if not paths:
        raise ValueError("Internal error: _pick_most_recent called with empty list")
    return max(paths, key=lambda p: p.stat().st_mtime)


def _find_mapping_resource(resources_dir: Path, token: str) -> Path:
    allowed_suffixes = {".csv", ".xlsx", ".xls"}
    matches = [
        p
        for p in resources_dir.iterdir()
        if p.is_file()
        and not p.name.startswith("~$")
        and p.suffix.lower() in allowed_suffixes
        and token.lower() in p.stem.lower()
    ]
    if not matches:
        raise FileNotFoundError(
            f"Could not find mapping resource containing '{token}' in {resources_dir}"
        )
    return _pick_most_recent(matches)


def deduce_mapping_paths(resources_dir: Path, criteria: Set[str]) -> Dict[str, Path]:
    out: Dict[str, Path] = {}

    if "primary_tumour" in criteria:
        out["primary_tumour"] = _find_mapping_resource(
            resources_dir,
            "PrimaryTumourCurationResource",
        )

    if "gene_alteration" in criteria:
        out["gene_alteration"] = _find_mapping_resource(
            resources_dir,
            "GeneAlterationCurationResource",
        )

    if "molecular_signature" in criteria:
        out["molecular_signature"] = _find_mapping_resource(
            resources_dir,
            "MolecularSignatureCurationResource",
        )

    return out


# ---------------------------
# Overwrite orchestration
# ---------------------------

def overwrite_selected_criteria_for_trial(
    rules: List[object],
    *,
    criteria: List[str],
    pt_map: Optional[PrimaryTumorMap] = None,
    ga_map: Optional[GeneAlterationMap] = None,
    ms_map: Optional[MolecularSignatureMap] = None,
) -> bool:
    crit_set = set(criteria)
    unknown = crit_set - SUPPORTED_CRITERIA
    if unknown:
        raise ValueError(f"Unknown criteria requested: {sorted(unknown)}")

    if "primary_tumour" in crit_set:
        if pt_map is None:
            raise ValueError("pt_map must be provided when primary_tumour is selected")
        overwrite_primary_tumour_in_rules(rules, mapping=pt_map)

    if "gene_alteration" in crit_set:
        if ga_map is None:
            raise ValueError("ga_map must be provided when gene_alteration is selected")
        overwrite_gene_alteration_in_rules(rules, mapping=ga_map)

    if "molecular_signature" in crit_set:
        if ms_map is None:
            raise ValueError(
                "ms_map must be provided when molecular_signature is selected"
            )
        overwrite_molecular_signature_in_rules(rules, mapping=ms_map)

    return len(rules) > 0


# ---------------------------
# CLI
# ---------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Apply selected criterion overwrite modules to curated NCT*.py files "
            "without pruning or simplification."
        )
    )
    parser.add_argument(
        "--curated_dir",
        required=True,
        type=Path,
        help="Directory of curated NCT*.py files or a single file",
    )
    parser.add_argument(
        "--output_dir",
        required=True,
        type=Path,
        help="Output directory for *_overwritten.py",
    )
    parser.add_argument(
        "--resources_dir",
        required=True,
        type=Path,
        help="Directory containing mapping resources (.csv/.xlsx/.xls)",
    )
    parser.add_argument(
        "--criteria",
        nargs="+",
        required=True,
        choices=sorted(SUPPORTED_CRITERIA),
        help=(
            "Criteria to overwrite. Supported: "
            "primary_tumour gene_alteration molecular_signature"
        ),
    )
    parser.add_argument("--log_level", default="INFO", help="Logging level (INFO/DEBUG/...)")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    crit_set = set(args.criteria)
    mapping_paths = deduce_mapping_paths(args.resources_dir, crit_set)
    for key, path in mapping_paths.items():
        logger.info("Using mapping resource for %s: %s", key, path)

    pt_map: Optional[PrimaryTumorMap] = None
    ga_map: Optional[GeneAlterationMap] = None
    ms_map: Optional[MolecularSignatureMap] = None

    if "primary_tumour" in crit_set:
        pt_map = build_primary_tumor_map(mapping_paths["primary_tumour"])
        pt_map.pop(("", ""), None)

    if "gene_alteration" in crit_set:
        ga_map = build_gene_alteration_map(mapping_paths["gene_alteration"])

    if "molecular_signature" in crit_set:
        ms_map = build_molecular_signature_map(mapping_paths["molecular_signature"])

    args.output_dir.mkdir(parents=True, exist_ok=True)

    n_files = 0
    n_written = 0

    for py_path in _iter_input_py_files(args.curated_dir):
        n_files += 1

        rules = load_curated_rules(py_path)
        if not rules:
            logger.warning("No rules loaded from %s; skipping", py_path)
            continue

        overwrite_selected_criteria_for_trial(
            rules,
            criteria=args.criteria,
            pt_map=pt_map,
            ga_map=ga_map,
            ms_map=ms_map,
        )

        out_path = args.output_dir / f"{py_path.stem}_overwritten.py"
        write_rules_py(rules, out_path)
        logger.info("Wrote %s", out_path)
        n_written += 1

    logger.info(
        "Done. Seen %d file(s); wrote %d.",
        n_files,
        n_written,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())