"""Matching-engine export (the deliverable). Two sets:

  Set A  `trial_eligibility.tsv` — ONE wide, denormalized flat file, one row per (trial_arm_id, conjunction_index)
         = one satisfiable eligibility path of one arm. Joins basic trial info (trial_info master) + the interpreted
         DNF eligibility cells + their FINAL vocab codes + the arm's interventions. This is what the engine scans.
  Set B  the drug 3NF tables — supplied AS-IS at drug_annotations/current_version/ (referenced, NOT copied). The
         raw-intervention / canonical_id / trial_arm_id columns of Set A join into them:
             arm_intervention_names_raw -> intervention_to_canonical -> drug_annotations_core (pottr class)
                                                                     -> drug_regulatory_approvals (TGA/PBS per indication)
             trial_arm_id -> trial_arm_drug_role (main/aux) -> ...

Single source of truth day-to-day: `export/` holds only Set A + a MANIFEST that points at Set B in place — no
drifting duplicate. `--snapshot` mints an immutable, self-contained `export/snapshot_<ts>/` bundle (Set A + a frozen
COPY of the drug tables + manifest) for hand-off; that copy is dated and never mutated, so it can't drift.

Deterministic assembly only — no LLM. Top-level module (composes both paths + the loaders via trial_info)."""
from __future__ import annotations

import argparse
import csv
import logging
import re
import shutil
from datetime import datetime
from pathlib import Path

from aus_trial_universe.core.paths import (
    CURRENT_VERSION,
    DRUG_ANNOTATIONS_ROOT,
    ELIGIBILITY_OUTPUT,
    EXPORT_FILE,
    EXPORT_ROOT,
)
from aus_trial_universe.tasks.drug_utility.schema import TABLE_FILES as DRUG_TABLE_FILES
from aus_trial_universe.tasks.shared.cohorts import trial_id_of

logger = logging.getLogger("agentic.export")

# The Set-B drug tables, referenced in place (and copied into a --snapshot bundle).
DRUG_TABLES = list(DRUG_TABLE_FILES.values())

# Set A columns (confirmed with the user 2026-07-28).
_TRIAL_INFO_FIELDS = [
    "official_title", "phase", "overall_status", "study_type", "lead_sponsor",
    "min_age", "max_age", "sex",
    "start_date", "primary_completion_date", "completion_date", "last_update_date",
    "countries", "has_AU_site", "AU_site_status", "AU_site_cities", "trial_url",
]
EXPORT_COLUMNS = (
    ["trial_arm_id", "conjunction_index", "trialId", "registry", "arm", "arm_type"]
    + _TRIAL_INFO_FIELDS
    + ["cancer_type_interpreted", "oncotree_name", "oncotree_code",
       "gene_alteration_interpreted", "gene_alteration_findingmodel",
       "molecular_signature_interpreted", "molecular_signature_findingmodel",
       "molecular_biomarker_interpreted", "prior_therapy_interpreted"]
    # Only the raw-intervention join key into Set B. The canonical ids, main/auxiliary role split, drug classes
    # and TGA/PBS approvals are all reachable through Set B via this + trial_arm_id, so they are NOT duplicated here.
    + ["arm_intervention_names_raw"]
)


def _uniq(values) -> list[str]:
    out: list[str] = []
    for v in values:
        v = (v or "").strip()
        if v and v not in out:
            out.append(v)
    return out


_CODE_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]+")
_ONCOTREE_OPS = {"AND", "OR", "NOT"}


def _render_oncotree_name(code_expr: str, vocab: dict[str, str]) -> str:   # noqa: ARG001 — vocab kept for callers
    """Human-readable rendering of a FINAL oncotree_code expression.

    Delegates to `tools.oncotree.render_name_expression`, the SINGLE source of names across the store, the joined
    views, this export and the drug-approval maps — so `oncotree_name` can never disagree with `oncotree_code`.
    """
    from aus_trial_universe.tasks.eligibility.mapping.cancer_type.vocab import render_name_expression
    return render_name_expression(code_expr)


def _read_map(path: Path, key_col: str, val_col: str) -> dict[str, str]:
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as fh:
        return {row[key_col]: row.get(val_col, "") for row in csv.DictReader(fh, delimiter="\t") if row.get(key_col)}


def _arm_drug_facts(drug_store, trial_arm_id: str) -> dict[str, str]:
    """The arm's raw intervention names (`; `-joined) — the join key into Set B. Canonical ids, the main/auxiliary
    role split, drug classes and TGA/PBS are all reachable from this + trial_arm_id via the Set-B 3NF tables
    (intervention_to_canonical / trial_arm_drug_role / drug_annotations_core / drug_regulatory_approvals), so they
    are deliberately NOT denormalized into Set A."""
    inputs = _uniq(inp for (taid, inp) in drug_store.occurrences if taid == trial_arm_id)
    return {"arm_intervention_names_raw": "; ".join(inputs)}


def build_export_rows(elig_store, arm_store, drug_store, trial_info, *, elig_dir: Path) -> list[dict]:
    """Assemble Set A: interpreted eligibility ⋈ trial_arms ⋈ trial_info ⋈ FINAL vocab maps ⋈ per-arm drug rollups.
    One row per (trial_arm_id, conjunction_index)."""
    from aus_trial_universe.tasks.eligibility.mapping.cancer_type.vocab import oncotree_vocab

    vocab = oncotree_vocab()
    # The export's `oncotree_code`/`oncotree_name` are the SHIPPING mapping, i.e. stage 3's output (user,
    # 2026-08-06). Column names here stay as they are: the matching engine consumes this file, so renaming
    # them would be a breaking change for a downstream consumer.
    from aus_trial_universe.core.paths import CANCER_TYPE_STAGE_FILES
    from aus_trial_universe.tasks.eligibility.mapping.workflow import strip_provenance
    ct_final = _read_map(elig_dir / CANCER_TYPE_STAGE_FILES["finalised"],
                         "cancer_type", "oncotree_code_finalised")
    ga_final = _read_map(elig_dir / "finalised_gene_alteration_map.tsv", "gene_alteration", "finding_model_FINAL")
    sig_final = _read_map(elig_dir / "finalised_molecular_signature_map.tsv", "molecular_signature", "finding_model_FINAL")
    arm_by_id = {a.trial_arm_id: a for arms in arm_store.arms.values() for a in arms}

    drug_cache: dict[str, dict] = {}
    rows: list[dict] = []
    for elig_rows in elig_store.interpreted.values():
        for e in elig_rows:
            a = arm_by_id.get(e.trial_arm_id)
            tid = a.trialId if a else trial_id_of(e.trial_arm_id)
            ti = trial_info.get(tid)
            if e.trial_arm_id not in drug_cache:
                drug_cache[e.trial_arm_id] = _arm_drug_facts(drug_store, e.trial_arm_id)
            # ⚠ STRIP PROVENANCE BEFORE THE LOOKUP. The map is keyed on the provenance-stripped value — that is the
            # key every other join in the pipeline uses — but this call passed the RAW cell. It silently missed for
            # any cell ending in a bracketed tail, and shipped `oncotree_code=""` for a value that HAS a mapping.
            # Found 2026-08-06 on NCT06211036, where the tail is TNM detail rather than a provenance tag:
            #   "…stage IV small-cell lung cancer (SCLC) [T any, N any, M1 a/b/c]"  ->  map has SCLC, export had ""
            # Two export rows were dropping their tumour type entirely. Pre-dates the three-stage restructure.
            code = ct_final.get(strip_provenance(e.cancer_type_interpreted), "")
            row = {
                "trial_arm_id": e.trial_arm_id, "conjunction_index": e.conjunction_index,
                "trialId": tid, "registry": a.registry if a else "",
                "arm": a.arm if a else "", "arm_type": a.arm_type if a else "",
                "cancer_type_interpreted": e.cancer_type_interpreted,
                "oncotree_code": code, "oncotree_name": _render_oncotree_name(code, vocab),
                "gene_alteration_interpreted": e.gene_alteration_interpreted,
                "gene_alteration_findingmodel": ga_final.get(e.gene_alteration_interpreted, ""),
                "molecular_signature_interpreted": e.molecular_signature_interpreted,
                "molecular_signature_findingmodel": sig_final.get(e.molecular_signature_interpreted, ""),
                "molecular_biomarker_interpreted": e.molecular_biomarker_interpreted,
                "prior_therapy_interpreted": e.prior_therapy_interpreted,
            }
            row.update({f: (getattr(ti, f) if ti else "") for f in _TRIAL_INFO_FIELDS})
            row.update(drug_cache[e.trial_arm_id])
            rows.append(row)
    return rows


def _write_tsv(path: Path, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=EXPORT_COLUMNS, delimiter="\t", lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _manifest(rows: list[dict], *, stamp: str, drug_dir: Path, bundled: bool) -> str:
    from aus_trial_universe.core.paths import REPO_ROOT
    n_arms = len({r["trial_arm_id"] for r in rows})
    n_trials = len({r["trialId"] for r in rows})
    try:
        drug_ref = drug_dir.relative_to(REPO_ROOT)
    except ValueError:
        drug_ref = drug_dir
    setb = "the frozen drug tables in THIS folder (immutable snapshot copy)" if bundled else \
        f"`{drug_ref}/` (canonical, single source of truth — referenced in place, NOT copied)"
    return f"""# Matching-engine export — MANIFEST

Built {stamp} (deterministic assembly; no LLM).

## Set A — `{EXPORT_FILE}`
One wide flat file, **one row per (trial_arm_id, conjunction_index)** — a single satisfiable eligibility path of one
trial arm. {len(rows):,} rows · {n_arms:,} arms · {n_trials:,} trials. Columns ({len(EXPORT_COLUMNS)}):
{", ".join(EXPORT_COLUMNS)}

- `oncotree_code` / `*_findingmodel` are the **FINAL** (Step-2 reconciled) vocab values; `oncotree_name` is rendered
  from the FINAL code. `*_interpreted` are the DNF cells (inline `NOT()`). `molecular_biomarker` / `prior_therapy`
  are free text (no vocab map).
- `arm_intervention_names_raw` + `trial_arm_id` are the join keys into Set B. The canonical ids, the
  main/auxiliary role split, drug classes and per-indication TGA/PBS live in the Set-B tables (see the join
  chain below) — they are NOT duplicated into Set A.

## Set B — the drug 3NF tables ({len(DRUG_TABLES)})
{setb}

Join chain from Set A:
```
arm_intervention_names_raw -> intervention_to_canonical (raw -> canonical_id)
                           -> drug_annotations_core       (canonical_id -> pottr_drug_class / drug_class)
                           -> drug_regulatory_approvals   (canonical_id -> TGA/PBS per approved indication)
trial_arm_id               -> trial_arm_drug_role         (canonical_id + role=main/auxiliary)
```
Tables: {", ".join(DRUG_TABLES)}

Symmetric-match vocab (drug-approval side, so its indications match Set A on the SAME axes):
```
drug_regulatory_approvals.cancer_type -> approval_cancer_type_map -> oncotree_code_FINAL  [match vs Set A oncotree_code]
drug_regulatory_approvals.biomarker   -> approval_biomarker_map   -> gene_alteration_findingmodel /
                                                                     molecular_signature_findingmodel
                                                                    [match vs Set A *_findingmodel]
```
(`oncotree_code_FINAL` is the Step-2-reconciled cancer code — the same reconciliation the trial side ran. The flat
join view `joined/drug_annotations/mapped_drug_regulatory_approval.tsv` denormalizes all of the above per indication.)

NB: TGA/PBS approval is **indication-specific** — matching a trial's cancer type to a drug's approved indication is
itself a match. The drug-side free-text `cancer_type`/`biomarker` are now mapped into the SAME OncoTree +
finding-model vocab (the two `approval_*_map` tables above, reusing the trial-side mappers + seeded from the trial
FINAL maps), so a trial's `oncotree_code` / `*_findingmodel` can be matched directly against a drug's approved
indication. `approval_biomarker_map.molecular_biomarker` (protein-expression / IHC) stays free text — symmetric with
Set A's `molecular_biomarker_interpreted`, which is not vocab-mapped either. ANZCTR `trial_url` is best-effort.
"""


def run_export(*, snapshot: bool = False,
               export_root: Path = EXPORT_ROOT,
               elig_root: Path = ELIGIBILITY_OUTPUT,
               drug_root: Path = DRUG_ANNOTATIONS_ROOT,
               stamp: str | None = None) -> Path:
    """Build the trial_info master, assemble Set A, write `export/trial_eligibility.tsv` + MANIFEST (Set B in place).
    With `snapshot=True`, also write an immutable self-contained `export/snapshot_<ts>/` (Set A + copied drug tables)."""
    from aus_trial_universe import trial_info as TI
    from aus_trial_universe.tasks.drug_utility.store import DrugRefStore
    from aus_trial_universe.tasks.eligibility.store import EligStore
    from aus_trial_universe.tasks.shared.store import TrialArmStore

    stamp = stamp or datetime.now().strftime("%Y-%m-%d %H:%M")
    elig_dir = elig_root / CURRENT_VERSION
    drug_dir = drug_root / "current_version"

    logger.info("export · building trial_info master …")
    infos = TI.build_all_trial_info()
    TI.save_trial_info(infos)
    logger.info("export · trial_info: %d trials", len(infos))

    elig_store = EligStore.load()
    arm_store = TrialArmStore.load()
    drug_store = DrugRefStore.load()
    rows = build_export_rows(elig_store, arm_store, drug_store, infos, elig_dir=elig_dir)

    export_root.mkdir(parents=True, exist_ok=True)
    _write_tsv(export_root / EXPORT_FILE, rows)
    (export_root / "MANIFEST.md").write_text(_manifest(rows, stamp=stamp, drug_dir=drug_dir, bundled=False),
                                             encoding="utf-8")
    logger.info("export · Set A → %s (%d rows) · Set B referenced at %s", export_root / EXPORT_FILE, len(rows), drug_dir)

    if snapshot:
        ts = datetime.now().strftime("%Y%m%d_%H%M")
        snap = export_root / f"snapshot_{ts}"
        snap.mkdir(parents=True, exist_ok=True)
        _write_tsv(snap / EXPORT_FILE, rows)
        copied = 0
        for name in DRUG_TABLES:
            src = drug_dir / name
            if src.exists():
                shutil.copy2(src, snap / name)
                copied += 1
        (snap / "MANIFEST.md").write_text(_manifest(rows, stamp=stamp, drug_dir=drug_dir, bundled=True), encoding="utf-8")
        logger.info("export · snapshot → %s (Set A + %d frozen drug tables)", snap, copied)
    return export_root / EXPORT_FILE


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the matching-engine export (Set A flat file + Set B pointer).")
    parser.add_argument("--snapshot", action="store_true",
                        help="Also write an immutable, self-contained export/snapshot_<ts>/ bundle (Set A + a frozen "
                             "copy of the drug tables) for hand-off.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for _n in ("httpx", "openai", "urllib3", "numexpr"):
        logging.getLogger(_n).setLevel(logging.WARNING)
    path = run_export(snapshot=args.snapshot)
    print(f"\n{'═' * 70}\nexport → {path}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
