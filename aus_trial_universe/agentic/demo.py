"""ISOLATED live-demo runner for the agentic pipeline (presentation use).

Runs the FULL pipeline over a tiny, hand-picked trial set — extraction → mapping (Step 1 + Step 2) →
drug reference + role classification → the matching-engine export — so an audience can watch every stage
scroll past in the log, one readable step at a time.

Two design constraints make this a clean, throwaway add-on that CANNOT affect the real pipeline:

  1. OUTPUTS ARE WHOLLY SEPARATE. Before any pipeline module is imported, this file re-points ONLY the
     OUTPUT path constants in `core.paths` at `data/agentic/demo/…`. Every store `.load()`/`.save()` and the
     export then read/write under `demo/`; the production stores under `data/agentic/{eligibility,
     drug_annotations,trial_arms,trial_info,joined,export}/` are never touched. No core module is edited —
     this works purely because `run.main()`/`build.main()` import their stores lazily and the stores capture
     their default root at import time, so patching the constants first is enough.
  2. INPUTS + CACHE ARE SHARED. `trial_universe/` (ingested trials), `resources/` (OncoTree/POTTR/RxNorm) and
     the LLM response `cache/` are deliberately LEFT on the real DATA_ROOT: the demo reads the already-ingested
     trials and reuses the existing cache, so a run is near-instant when the picked trials were seen before.

Run via `make agentic-demo` (driver: scripts/agentic/demo.sh, which resets data/agentic/demo/ and tees a log).
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime

# --- Re-root the OUTPUT paths to data/agentic/demo/ BEFORE importing any pipeline module. --------------- #
# (Inputs + cache are NOT re-rooted — see the module docstring.) This must run before `run`/`build`/`export`
# or the stores are imported, so their module-level `from paths import …` bindings and default-arg roots
# capture the demo values. paths.py imports nothing from the package, so importing it here is side-effect-free.
from aus_trial_universe.agentic.core import paths as _paths   # noqa: E402

DEMO_ROOT = _paths.DATA_ROOT / "demo"
_paths.ELIGIBILITY_OUTPUT = DEMO_ROOT / "eligibility"
_paths.DRUG_ANNOTATIONS_ROOT = DEMO_ROOT / "drug_annotations"
_paths.TRIAL_ARMS_ROOT = DEMO_ROOT / "trial_arms"
_paths.TRIAL_INFO_ROOT = DEMO_ROOT / "trial_info"
_paths.EXPORT_ROOT = DEMO_ROOT / "export"
_paths.JOINED_ROOT = DEMO_ROOT / "joined"
_paths.LOG_DIR = DEMO_ROOT / "log"
_paths.ANALYSIS_DIR = DEMO_ROOT / "analysis"
# Intentionally shared (still on DATA_ROOT): TRIAL_UNIVERSE / RESOURCES (inputs) and CACHE_DIR (LLM cache).

# Two CTGov trials with rich cancer-type + gene-alteration content but modest structure — chosen to show every
# stage doing something interesting while staying easy to follow live:
#   NCT02393625  ALK+ NSCLC (solid) · 2 arms · ALK rearrangement→Fusion · Ceritinib + Nivolumab
#   NCT05453903  AML (heme) · 3 setting-arms · KMT2A/NPM1/NUP98/NUP214 "alteration" (unspecified→expansion) ·
#                Bleximenib (main) + Venetoclax/Azacitidine/chemo (auxiliary)
DEFAULT_IDS = "NCT02393625,NCT05453903"

_WIDTH = 76


def _banner(step: int, total: int, title: str, what_to_watch: str) -> None:
    """A presentation-friendly stage header: what this step is + what to point the audience at in the log."""
    line = "═" * _WIDTH
    head = f"  STAGE {step}/{total}  ·  {title}"[:_WIDTH]   # clip so the right border never overflows
    print(f"\n\n╔{line}╗")
    print(f"║{head:<{_WIDTH}}║")
    print(f"╚{line}╝")
    print(f"  ▸ {what_to_watch}\n")


def _export_demo(ids: list[str], *, log: logging.Logger) -> int:
    """EXPORT stage — the grand join. Same deterministic assembly as `make agentic-export`, but the trial_info
    master is subset to the demo trials so the demo folder holds only what the demo produced (the production
    export builds trial_info over the whole universe)."""
    from aus_trial_universe.agentic import export as _export
    from aus_trial_universe.agentic import trial_info as _ti
    from aus_trial_universe.agentic.tasks.drug_utility.store import DrugRefStore
    from aus_trial_universe.agentic.tasks.eligibility.store import EligStore
    from aus_trial_universe.agentic.tasks.shared.store import TrialArmStore

    infos_all = _ti.build_all_trial_info()                    # deterministic, no API (~2s over the universe)
    infos = {t: infos_all[t] for t in ids if t in infos_all}  # keep only the demo trials
    _ti.save_trial_info(infos, root=_paths.TRIAL_INFO_ROOT)
    log.info("export · trial_info: %d demo trial(s)", len(infos))

    elig = EligStore.load(_paths.ELIGIBILITY_OUTPUT)
    arms = TrialArmStore.load(_paths.TRIAL_ARMS_ROOT)
    drug = DrugRefStore.load(_paths.DRUG_ANNOTATIONS_ROOT)
    elig_dir = _paths.ELIGIBILITY_OUTPUT / _paths.CURRENT_VERSION
    rows = _export.build_export_rows(elig, arms, drug, infos, elig_dir=elig_dir)

    _paths.EXPORT_ROOT.mkdir(parents=True, exist_ok=True)
    _export._write_tsv(_paths.EXPORT_ROOT / _paths.EXPORT_FILE, rows)
    drug_dir = _paths.DRUG_ANNOTATIONS_ROOT / _paths.CURRENT_VERSION
    (_paths.EXPORT_ROOT / "MANIFEST.md").write_text(
        _export._manifest(rows, stamp=datetime.now().strftime("%Y-%m-%d %H:%M"), drug_dir=drug_dir, bundled=False),
        encoding="utf-8")
    n_arms = len({r["trial_arm_id"] for r in rows})
    print(f"\n{'═' * 70}\nexport → {_paths.EXPORT_ROOT / _paths.EXPORT_FILE}  ·  {len(rows)} row(s) · {n_arms} arm(s)\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Isolated live demo of the agentic pipeline (outputs → data/agentic/demo/).")
    parser.add_argument("--ids", default=DEFAULT_IDS, help=f"Comma-separated trial ids to demo (default: {DEFAULT_IDS}).")
    args = parser.parse_args(argv)
    ids = [x.strip() for x in args.ids.split(",") if x.strip()]

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for _n in ("httpx", "openai", "urllib3", "numexpr"):
        logging.getLogger(_n).setLevel(logging.WARNING)
    log = logging.getLogger("agentic.demo")

    # Import the real entry points AFTER the re-root above — so they bind the demo output paths.
    from aus_trial_universe.agentic import run as _run
    from aus_trial_universe.agentic.tasks.drug_utility import build as _drug

    id_arg = ",".join(ids)
    # `--workers 1` keeps the log readable (no interleaving); `--no-cache-prune` leaves the shared cache untouched.
    stages = [
        ("ELIGIBILITY — Extraction (free text → DNF rows)",
         "doer extracts cancer-type / gene / drug per arm; the 5-reviewer panel critiques; refine loops until faithful.",
         lambda: _run.main(["--ids", id_arg, "--extract-only", "--workers", "1", "--no-cache-prune"])),
        ("ELIGIBILITY — Mapping Step 1 (value → vocabulary)",
         "each distinct cancer-type → OncoTree code; each gene/signature → finding-model; mapper→reviewer per value.",
         lambda: _run.main(["--map-only", "--workers", "1", "--no-cache-prune"])),
        ("ELIGIBILITY — Mapping Step 2 (reconcile across values)",
         "same concept phrased differently → one agreed code (e.g. the NSCLC / AML phrasings); genuine distinctions kept apart.",
         lambda: _run.main(["--reconcile", "--workers", "1", "--no-cache-prune"])),
        ("DRUG — reference lookup + main / auxiliary role",
         "each drug is already annotated → a pure LOOKUP (canonical id, class, TGA/PBS — NO web search); then per-arm main (investigational) vs auxiliary (backbone).",
         lambda: _drug.main(["--from-trials", id_arg, "--workers", "1", "--no-cache-prune"])),
        ("EXPORT — matching-engine deliverable (grand join)",
         "trial info ⋈ interpreted eligibility ⋈ FINAL vocab codes ⋈ per-arm drugs → one wide flat file.",
         lambda: _export_demo(ids, log=log)),
    ]

    print(f"\nAGENTIC PIPELINE — LIVE DEMO   ·   trials: {id_arg}   ·   outputs → {DEMO_ROOT}/   ·   cache: shared")
    t0 = time.perf_counter()
    for i, (title, watch, fn) in enumerate(stages, 1):
        _banner(i, len(stages), title, watch)
        rc = fn() or 0
        if rc != 0:
            print(f"\n✗ stage {i} returned rc={rc} — aborting demo")
            return rc

    print(f"\n\n{'█' * _WIDTH}")
    print(f"  DEMO COMPLETE  ·  {time.perf_counter() - t0:.0f}s  ·  every output under {DEMO_ROOT}/")
    print(f"{'█' * _WIDTH}")
    print("  key files:")
    print(f"    eligibility (3NF)   {_paths.ELIGIBILITY_OUTPUT / _paths.CURRENT_VERSION}/")
    print(f"    joined views        {_paths.JOINED_ROOT}/")
    print(f"    drug annotations    {_paths.DRUG_ANNOTATIONS_ROOT / _paths.CURRENT_VERSION}/")
    print(f"    ► deliverable       {_paths.EXPORT_ROOT / _paths.EXPORT_FILE}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
