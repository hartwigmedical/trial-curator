"""THE PLUMBING TEST for the shared stage tables.

A RENAMED TABLE FAILS OPEN. A reader left on an old filename finds nothing, returns `{}`, and ships blanks —
silently, with every gate still green, because "no mapping" looks exactly like "nothing to map". That is why the
2026-08-06 rename of the gene_alteration and molecular_signature tables is pinned here by CONSUMER rather than
only by unit behaviour: the question is not "does the writer work" but "is every reader looking at the file the
writer produces".

Also asserts the property that motivated factoring the module out at all (user: *"the idea is not to have
duplicate code"*): there is exactly ONE definition of each stage-table name, and no module re-states it.
"""
from __future__ import annotations

import csv
import inspect
from pathlib import Path

import pytest

from aus_trial_universe.tasks.eligibility.mapping import stage_tables as ST


def _read(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


# --------------------------------------------------------------------------- #
# The layout every column shares.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("spec", list(ST.SPECS.values()), ids=list(ST.SPECS))
def test_columns_accrete_so_each_file_carries_its_whole_provenance_chain(spec):
    seen: list[str] = []
    for stage in spec.stages:
        cols = spec.columns(stage)
        assert cols[0] == spec.column, "the key column comes first"
        assert cols[: len(seen)] == seen, f"{stage} must EXTEND the previous stage's columns, not replace them"
        assert spec.value_col(stage) in cols
        seen = cols


@pytest.mark.parametrize("spec", list(ST.SPECS.values()), ids=list(ST.SPECS))
def test_write_then_load_round_trips_every_stage(tmp_path, spec):
    mappings = {s: {"a value": f"EXPR_{s}"} for s in spec.stages}
    spec.write_all(tmp_path, **mappings)
    for stage in spec.stages:
        assert (tmp_path / spec.file(stage)).exists()
        assert spec.load(tmp_path, stage) == {"a value": f"EXPR_{stage}"}


def test_gene_alteration_has_three_stages_and_signature_has_two():
    """molecular_signature provably needs no stage 2 (0 canonicalisation changes over the live corpus), and
    gene_alteration provably does (187 of 913 values are rewritten by it)."""
    assert ST.GENE_ALTERATION.stages == ("initial", "reconciled", "finalised")
    assert ST.MOLECULAR_SIGNATURE.stages == ("initial", "finalised")
    with pytest.raises(KeyError):
        ST.MOLECULAR_SIGNATURE.file("reconciled")


def test_only_cancer_type_derives_a_name_and_it_is_never_authored(tmp_path):
    """`oncotree_name_*` is rendered FROM the code at write time, so name/code disagreement is unreachable."""
    assert ST.CANCER_TYPE.derived_stem == "oncotree_name"
    assert ST.GENE_ALTERATION.derived_stem == "" and ST.MOLECULAR_SIGNATURE.derived_stem == ""
    ST.CANCER_TYPE.write_all(tmp_path, initial={"v": "BREAST"}, reconciled={"v": "BREAST"},
                             finalised={"v": "PRAD"})
    row = _read(tmp_path / ST.CANCER_TYPE.file("finalised"))[0]
    assert row["oncotree_name_initial"] == "Breast" and row["oncotree_name_finalised"] == "Prostate Adenocarcinoma"


def test_a_retired_filename_is_still_readable_so_old_archives_load(tmp_path):
    """An ARCHIVE written before the rename must still load, or a restore silently yields an empty mapping."""
    legacy, col = ST.GENE_ALTERATION.legacy["finalised"]
    with open(tmp_path / legacy, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["gene_alteration", col], delimiter="\t", lineterminator="\n")
        w.writeheader()
        w.writerow({"gene_alteration": "EGFR mutation", col: "SmallVariant[gene=EGFR]"})
    assert ST.GENE_ALTERATION.load(tmp_path, "finalised") == {"EGFR mutation": "SmallVariant[gene=EGFR]"}


# --------------------------------------------------------------------------- #
# Every CONSUMER reads the file the writer produces.
# --------------------------------------------------------------------------- #
def test_every_consumer_resolves_its_filename_through_the_shared_spec():
    """No module may re-state a stage-table filename. A second copy is how a reader gets left behind."""
    import aus_trial_universe.export as export_mod
    import aus_trial_universe.run as run_mod
    import aus_trial_universe.qa.gates as gates_mod
    import aus_trial_universe.qa.invariants as inv_mod
    import aus_trial_universe.tasks.eligibility.store as store_mod
    import aus_trial_universe.tasks.eligibility.mapping.reconcile as rec_mod
    import aus_trial_universe.tasks.drug_utility.map_approvals as ma_mod

    for mod in (export_mod, run_mod, gates_mod, store_mod, rec_mod, ma_mod):
        src = inspect.getsource(mod)
        for spec in ST.SPECS.values():
            for stage in spec.stages:
                assert f'"{spec.file(stage)}"' not in src, (
                    f"{mod.__name__} hard-codes {spec.file(stage)}; resolve it through stage_tables instead")
            for legacy, _col in spec.legacy.values():
                assert f'"{legacy}"' not in src, f"{mod.__name__} still references the retired {legacy}"

    # invariants.py reads the live store by name; assert it names the CURRENT tables, not the retired ones.
    inv_src = inspect.getsource(inv_mod)
    for spec in ST.SPECS.values():
        assert spec.file("finalised") in inv_src
        for legacy, _col in spec.legacy.values():
            assert legacy not in inv_src


def test_export_reads_the_finalised_stage_of_all_three_columns():
    src = inspect.getsource(__import__("aus_trial_universe.export", fromlist=["x"]))
    for name in ("CANCER_TYPE", "GENE_ALTERATION", "MOLECULAR_SIGNATURE"):
        assert f'ST.{name}.load(elig_dir, "finalised")' in src, f"export must ship {name}'s finalised stage"


def test_the_store_writes_stage_one_for_all_three_columns():
    src = inspect.getsource(__import__("aus_trial_universe.tasks.eligibility.store", fromlist=["x"]))
    assert src.count("write_initial") == 1, "one shared call, not one per column"
    for name in ("CANCER_TYPE", "GENE_ALTERATION", "MOLECULAR_SIGNATURE"):
        assert f"ST.{name}" in src
