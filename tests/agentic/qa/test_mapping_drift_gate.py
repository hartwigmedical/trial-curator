"""The mapping-drift gate: does it actually catch the regression it was written for?

The gate was added after the 2026-08-05 audit found B1 had replaced specific OncoTree codes with the pan-solid
sentinel for nine values. On the live store it PASSES — correctly, because the archived version is already
post-B1 — which means the live store cannot demonstrate that the gate works. These fixtures reproduce the real
regression exactly (the values and codes are the ones actually found) and assert the gate FAILs on it.
"""
from __future__ import annotations

import csv
from pathlib import Path

from aus_trial_universe.core.paths import ARCHIVE, CURRENT_VERSION, FINALISED_MAP_FILES
from aus_trial_universe.qa.gates import FAIL, PASS, WARN, GateReport, _mapping_drift_gate

CT_FILE = FINALISED_MAP_FILES["cancer_type_map"]


def _write(path: Path, rows: list[tuple[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(["cancer_type", "oncotree_name", "oncotree_code", "oncotree_code_FINAL"])
        for value, final in rows:
            w.writerow([value, "", final, final])


def _store(tmp_path: Path, before: list[tuple[str, str]], after: list[tuple[str, str]]) -> Path:
    root = tmp_path / "eligibility"
    _write(root / ARCHIVE / "20260101" / CT_FILE, before)
    _write(root / CURRENT_VERSION / CT_FILE, after)
    return root


def _verdict(root: Path) -> tuple[str, str]:
    rep = GateReport()
    _mapping_drift_gate(rep, store_root=root)
    gate = rep.gates[-1]
    return gate.status, gate.detail


def test_fails_on_the_real_b1_regression(tmp_path):
    """Specific codes replaced by the pan-solid sentinel — the exact defect that shipped."""
    # Fixture values are deliberately NOT real map keys: a test must not depend on what the live register
    # happens to contain, and every real regression value is now adjudicated (and therefore exempt).
    root = _store(
        tmp_path,
        [("some gynaecological cohort", "CERVIX OR OVARY OR UTERUS OR VULVA"),
         ("some adenoid cystic cohort", "ACYC")],
        [("some gynaecological cohort", "Solid tumour"),
         ("some adenoid cystic cohort", "Solid tumour")],
    )
    status, detail = _verdict(root)
    assert status == FAIL, detail
    assert "BROADENED" in detail
    assert "2 value(s)" in detail


def test_fails_when_a_code_is_replaced_by_its_own_ancestor(tmp_path):
    """`BLCA` -> `BLADDER` is the organ bucket swallowing the histology — the NMIBC defect."""
    root = _store(tmp_path, [("high grade NMIBC", "BLCA")], [("high grade NMIBC", "BLADDER")])
    status, detail = _verdict(root)
    assert status == FAIL, detail
    assert "ancestor" in detail


def test_warns_on_narrowing(tmp_path):
    """Over-restriction is real but recoverable, so it warns rather than blocking the cycle."""
    root = _store(tmp_path, [("germ cell tumour", "NSGCT OR SEM")], [("germ cell tumour", "SEM")])
    status, detail = _verdict(root)
    assert status == WARN, detail
    assert "narrowed" in detail


def test_passes_when_nothing_moved(tmp_path):
    root = _store(tmp_path, [("melanoma", "MEL")], [("melanoma", "MEL")])
    assert _verdict(root)[0] == PASS


def test_an_approved_adjudication_is_exempt(tmp_path, monkeypatch):
    """A ruling is the sanctioned way to change a mapping, so the gate must not fail on our own fix."""
    from aus_trial_universe.qa import adjudications

    monkeypatch.setitem(adjudications.for_column("cancer_type"), "some gynaecological cohort", object())
    root = _store(tmp_path, [("some gynaecological cohort", "CERVIX OR OVARY")],
                  [("some gynaecological cohort", "Solid tumour")])
    assert _verdict(root)[0] == PASS


def test_passes_vacuously_only_when_it_says_so(tmp_path):
    """An archive with no finalised map must report that, not imply a clean comparison.

    This is the bug the first version of the gate had: archives are a mix of `<YYYYMMDD>` and hand-named dirs
    (`pre_v2_format`), `pre_*` sorts after every date, and the chosen dir held no finalised map at all — so the
    gate passed while comparing nothing.
    """
    root = tmp_path / "eligibility"
    (root / ARCHIVE / "pre_v2_format").mkdir(parents=True)
    _write(root / CURRENT_VERSION / CT_FILE, [("melanoma", "MEL")])
    status, detail = _verdict(root)
    assert status == PASS
    assert "nothing comparable" in detail


def test_prefers_the_newest_archive_by_mtime_not_by_name(tmp_path):
    """`pre_v2_format` sorts after `20260804`; the gate must still diff against the dated one."""
    root = tmp_path / "eligibility"
    _write(root / ARCHIVE / "20260804" / CT_FILE, [("melanoma", "MEL")])
    _write(root / ARCHIVE / "pre_v2_format" / CT_FILE, [("melanoma", "Solid tumour")])
    import os, time
    old = time.time() - 86400
    os.utime(root / ARCHIVE / "pre_v2_format" / CT_FILE, (old, old))
    _write(root / CURRENT_VERSION / CT_FILE, [("melanoma", "MEL")])
    status, detail = _verdict(root)
    assert status == PASS, detail
    assert "20260804" in detail
