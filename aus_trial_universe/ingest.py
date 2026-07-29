"""Stage-I trial ingestion CLI (self-contained; no legacy eligibility_path/drug_utility_path).

Downloads the current recruiting oncology trial universe for each registry, filters + POTTR-appends, and writes the
input the curation pipeline reads, under the `current_version/` + `archive/` convention:

  ctgov  -> full CT.gov v2 search (status/country/drug/condition filters) + POTTR broad-net + POTTR id-append,
            merged & deduped -> inputs/trial_universe/ctgov/current_version/03_merged_ctgov_input.json (+ aliases)
  anzctr -> (Phase 3) curl_cffi all.xls download + workbook->CSV

Full download each run: the fresh merged set IS the current kept universe, so curation runs (`make agentic-run`)
only pick up genuinely-new trials and the expiry step can drop trials that fell out of the kept set.

  python -m aus_trial_universe.ingest --registry ctgov
Run via `make agentic-ingest REGISTRY=ctgov`. Wrap long live downloads in `caffeinate -i`.
"""
from __future__ import annotations

import argparse
import logging


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage-I trial ingestion (download + filter + POTTR + version).")
    parser.add_argument("--registry", choices=["ctgov", "anzctr", "all"], default="ctgov",
                        help="Which registry to ingest (default: ctgov). 'anzctr'/'all' land in Phase 3.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for _n in ("httpx", "urllib3", "requests", "numexpr"):
        logging.getLogger(_n).setLevel(logging.WARNING)
    log = logging.getLogger("agentic.ingest")

    if args.registry in ("ctgov", "all"):
        _ingest_ctgov(log)

    if args.registry in ("anzctr", "all"):
        _ingest_anzctr(log)

    return 0


def _ingest_ctgov(log: logging.Logger) -> None:
    from aus_trial_universe.tasks.ingestion.ctgov import download_ctgov, write_ctgov_current_version
    from aus_trial_universe.tasks.ingestion.pottr_ids import load_pottr_trial_ids_best_effort

    log.info("▶ INGEST · ctgov · loading POTTR trial ids (best-effort) …")
    pottr = load_pottr_trial_ids_best_effort(registry="ctgov")
    log.info("  POTTR ctgov ids: %d", len(pottr))

    records, aliases = download_ctgov(pottr_ids=pottr, log=log)
    vdir = write_ctgov_current_version(records, aliases)

    print(f"\n{'═' * 70}\ningest ctgov → {vdir}/\n"
          f"  merged trials: {len(records)} · POTTR id-aliases: {len(aliases)}\n")


def _ingest_anzctr(log: logging.Logger) -> None:
    from aus_trial_universe.tasks.ingestion.anzctr import download_anzctr, write_anzctr_current_version
    from aus_trial_universe.tasks.ingestion.pottr_ids import load_pottr_trial_ids_best_effort

    log.info("▶ INGEST · anzctr · loading POTTR trial ids (best-effort) …")
    pottr = load_pottr_trial_ids_best_effort(registry="anzctr")
    log.info("  POTTR anzctr ids: %d", len(pottr))

    zip_bytes, rows = download_anzctr(pottr_ids=pottr, log=log)
    vdir = write_anzctr_current_version(zip_bytes, rows)

    print(f"\n{'═' * 70}\ningest anzctr → {vdir}/\n"
          f"  extracted trials: {len(rows)}\n")


if __name__ == "__main__":
    raise SystemExit(main())
