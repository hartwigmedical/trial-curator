"""Run slice-1 eligibility extraction and write a DNF TSV (CTGov and/or ANZCTR).

Run in the trial_curator env, from the repo root (or via `make agentic-eligibility-extract`):

  ... .run --id NCT07099898                   # one ctgov trial (default source)
  ... .run --source anzctr --id ACTRN12625..  # one anzctr trial
  ... .run --selected 6                        # 3 ctgov + 3 anzctr (ceil/floor on odd N)

For every trial, ALL relevant sections are assembled as the input:
  ctgov  -> title, official title, summary, detailed description, conditions, keywords, eligibility
  anzctr -> study title, scientific title, health condition, inclusion criteria

Writes data/agentic/eligibility_extraction/eligibility_dnf_<source>_<id> | _selected<N>.tsv.
Requires OPENAI_API_KEY (auto-loaded from .env / .env.local if not already exported).
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
from pathlib import Path

from aus_trial_universe.agentic.core.pipeline_io import find_best_file, latest_version_dir

REPO_ROOT = Path(__file__).resolve().parents[4]
CTGOV_INPUT_ROOT = REPO_ROOT / "data/trial_inputs/ctgov/input_trials"
CTGOV_LATEST_JSON = REPO_ROOT / "data/trial_inputs/ctgov/download_state/ctgov_trials_latest.json"
ANZCTR_CSV = REPO_ROOT / "data/trial_inputs/anzctr/extracted_trials/anzctr_field_extractions.csv"
DEFAULT_OUT_DIR = REPO_ROOT / "data/agentic/eligibility_extraction"


def _load_openai_key() -> None:
    """Populate OPENAI_API_KEY from .env / .env.local without printing it."""
    if os.environ.get("OPENAI_API_KEY"):
        return
    for fname in (".env", ".env.local"):
        path = REPO_ROOT / fname
        if path.exists():
            for line in path.read_text().splitlines():
                s = line.strip()
                if s.startswith("OPENAI_API_KEY=") and not s.startswith("#"):
                    os.environ["OPENAI_API_KEY"] = s.split("=", 1)[1].strip().strip('"').strip("'")


def _assemble(sections: list[tuple[str, str | None]]) -> str:
    """Join non-empty (label, text) sections into one labeled document."""
    return "\n\n".join(
        f"## {label}\n{str(text).strip()}" for label, text in sections if text and str(text).strip()
    )


# --- CTGov (versioned merged JSON input; falls back to the download cache) --- #
def _ctgov_records() -> list[dict]:
    """Raw ctgov API records for the current working set.

    Reads the newest ``version_<ddmmyyyy>/*merged*ctgov*input*.json`` (the pipeline's
    proper input, located via the copied dated-file selector); falls back to the
    persistent download cache if no versioned merged file is present.
    """
    try:
        version_dir = latest_version_dir(CTGOV_INPUT_ROOT)
        path = find_best_file(
            version_dir,
            token_groups=[["merged"]],
            allowed_suffixes={".json"},
            label="merged ctgov input",
            warn_on_multiple=False,
        )
    except FileNotFoundError:
        path = CTGOV_LATEST_JSON
    return json.loads(Path(path).read_text())


def _ctgov_nct(rec: dict) -> str | None:
    ps = rec.get("protocolSection", {}) or {}
    return (ps.get("identificationModule", {}) or {}).get("nctId")


def _assemble_ctgov_text(protocol_section: dict) -> str:
    idm = protocol_section.get("identificationModule", {}) or {}
    desc = protocol_section.get("descriptionModule", {}) or {}
    cond = protocol_section.get("conditionsModule", {}) or {}
    elig = protocol_section.get("eligibilityModule", {}) or {}
    return _assemble(
        [
            ("TITLE", idm.get("briefTitle")),
            ("OFFICIAL TITLE", idm.get("officialTitle")),
            ("CONDITIONS", ", ".join(cond.get("conditions", []) or [])),
            ("KEYWORDS", ", ".join(cond.get("keywords", []) or [])),
            ("BRIEF SUMMARY", desc.get("briefSummary")),
            ("DETAILED DESCRIPTION", desc.get("detailedDescription")),
            ("ELIGIBILITY CRITERIA", elig.get("eligibilityCriteria")),
        ]
    )


def load_ctgov_trial_text(trial_id: str) -> str:
    for rec in _ctgov_records():
        if _ctgov_nct(rec) == trial_id:
            return _assemble_ctgov_text(rec.get("protocolSection", {}) or {})
    raise KeyError(f"{trial_id} not found in ctgov input")


def load_first_ctgov_trials(n: int) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for rec in _ctgov_records():
        nct = _ctgov_nct(rec)
        if not nct:
            continue
        text = _assemble_ctgov_text(rec.get("protocolSection", {}) or {})
        if text.strip():
            out.append((nct, text))
        if len(out) >= n:
            break
    return out


# --- ANZCTR (CSV, one row per selected trial) ------------------------------- #
ANZCTR_SECTIONS = [
    ("STUDY TITLE", "STUDY TITLE"),
    ("SCIENTIFIC TITLE", "SCIENTIFIC TITLE"),
    ("HEALTH CONDITION", "HEALTH CONDITION"),
    ("INCLUSION CRITERIA", "INCLUSIVE CRITERIA"),
]


def _assemble_anzctr_text(row: dict) -> str:
    return _assemble([(label, row.get(col)) for label, col in ANZCTR_SECTIONS])


def _anzctr_rows() -> list[dict]:
    with open(ANZCTR_CSV, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _norm_actrn(value: str | None) -> str:
    """Normalize ANZCTR ids so 'ACTRN12605000108617' and '12605000108617' match."""
    return (value or "").strip().upper().removeprefix("ACTRN")


def load_anzctr_trial_text(trial_id: str) -> str:
    target = _norm_actrn(trial_id)
    for row in _anzctr_rows():
        if _norm_actrn(row.get("ACTRN")) == target:
            return _assemble_anzctr_text(row)
    raise KeyError(f"{trial_id} not found in {ANZCTR_CSV}")


def load_first_anzctr_trials(n: int) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for row in _anzctr_rows():
        actrn = (row.get("ACTRN") or "").strip()
        if not actrn:
            continue
        text = _assemble_anzctr_text(row)
        if text.strip():
            out.append((actrn, text))
        if len(out) >= n:
            break
    return out


LOADERS = {
    "ctgov": load_ctgov_trial_text,
    "anzctr": load_anzctr_trial_text,
}


def load_selected(n: int) -> list[tuple[str, str, str]]:
    """Select N trials across both sources: ceil(N/2) ctgov + floor(N/2) anzctr."""
    n_ctgov = (n + 1) // 2
    n_anzctr = n // 2
    trials = [("ctgov", tid, text) for tid, text in load_first_ctgov_trials(n_ctgov)]
    trials += [("anzctr", tid, text) for tid, text in load_first_anzctr_trials(n_anzctr)]
    return trials


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Slice-1 eligibility DNF extraction (ctgov / anzctr).")
    parser.add_argument("--source", choices=["ctgov", "anzctr"], default="ctgov",
                        help="Source for --id (ignored by --selected, which spans both).")
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--id", help="Trial id (ctgov NCT... or anzctr ACTRN...); assembles all relevant sections.")
    which.add_argument("--selected", type=int, metavar="N",
                       help="Select N trials: ceil(N/2) ctgov + floor(N/2) anzctr.")
    parser.add_argument("--cohort", default="all", help="Cohort label (slice 1 treats a trial as one cohort).")
    parser.add_argument("--out", help="Output TSV path.")
    parser.add_argument("--model", default=None, help="Override the OpenAI model.")
    parser.add_argument("--no-judge", action="store_true", help="Skip the faithfulness judge (cheaper).")
    parser.add_argument("--max-attempts", type=int, default=3)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    for _noisy in ("httpx", "openai", "urllib3"):
        logging.getLogger(_noisy).setLevel(logging.WARNING)  # keep the log about the workflow, not HTTP
    _load_openai_key()

    # Imported here so `--help` works even without the openai/pydantic env.
    from aus_trial_universe.agentic.core.client import LlmClient
    from aus_trial_universe.agentic.tasks.eligibility_extraction.workflow import (
        extract_eligibility,
        rows_to_tsv,
    )

    if args.id:
        trials = [(args.source, args.id, LOADERS[args.source](args.id))]
        default_out = DEFAULT_OUT_DIR / f"eligibility_dnf_{args.source}_{args.id}.tsv"
    else:  # --selected N (both sources)
        trials = load_selected(args.selected)
        default_out = DEFAULT_OUT_DIR / f"eligibility_dnf_selected{args.selected}.tsv"
    if not trials:
        parser.error("no trials with usable text found")

    client = LlmClient(model=args.model) if args.model else LlmClient()
    log = logging.getLogger("agentic.eligibility")
    log.info("run: %d trial(s), judge=%s", len(trials), not args.no_judge)
    all_rows = []
    summaries = []
    for source, trial_id, source_text in trials:
        log.info("=== [%s] %s (%d chars) ===", source, trial_id, len(source_text))
        result = extract_eligibility(
            client,
            trial_id=trial_id,
            cohort=args.cohort,
            source_text=source_text,
            max_attempts=args.max_attempts,
            use_judge=not args.no_judge,
        )
        all_rows.extend(result.rows)
        summaries.append((source, trial_id, len(result.rows), result.faithful, result.attempts))

    out_path = Path(args.out) if args.out else default_out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tsv = rows_to_tsv(all_rows)
    out_path.write_text(tsv, encoding="utf-8")

    print(f"\nextracted {len(trials)} trial(s), {len(all_rows)} row(s):")
    for source, trial_id, n_rows, faithful, attempts in summaries:
        print(f"  [{source}] {trial_id}: rows={n_rows} faithful={faithful} attempts={attempts}")
    print(f"\nDNF table -> {out_path}\n")
    print(tsv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
