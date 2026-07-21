"""Deterministic POTTR / RxNorm+ATC lookups (spec §6.1) — tested with tiny fixtures (no gitignored data)."""
from __future__ import annotations

from aus_trial_universe.agentic.tasks.drug_utility import pottr, rxnorm


# --- RxNorm + ATC (parse of RXNCONSO.RRF) ----------------------------------- #
def _rrf_line(rxcui, sab, tty, code, name, lat="ENG"):
    # RXNCONSO cols: 0 RXCUI,1 LAT,...,11 SAB,12 TTY,13 CODE,14 STR,... (pad to >=15)
    cols = [rxcui, lat, "", "", "", "", "", "", "", "", "", sab, tty, code, name, "", "N", ""]
    return "|".join(cols) + "\n"


def test_rxnorm_parse_name_to_rxcui_and_atc(tmp_path):
    rrf = tmp_path / "RXNCONSO.RRF"
    rrf.write_text(
        _rrf_line("1547545", "RXNORM", "IN", "", "pembrolizumab")
        + _rrf_line("1547545", "ATC", "IN", "L01FF02", "pembrolizumab")   # ATC code lives on the ATC-source row
        + _rrf_line("57308", "RXNORM", "IN", "", "Topotecan")
        + _rrf_line("57308", "ATC", "IN", "L01CE01", "topotecan")
        + _rrf_line("999", "SNOMEDCT_US", "PT", "x", "not an ingredient")   # ignored (wrong SAB/TTY)
        + _rrf_line("111", "RXNORM", "IN", "", "aspirin", lat="FRE"),       # ignored (not ENG)
    )
    name_to_rxcui, rxcui_to_atc = rxnorm._parse_rrf(rrf)
    assert name_to_rxcui["pembrolizumab"] == "1547545"
    assert name_to_rxcui["topotecan"] == "57308"     # case-insensitive ("Topotecan" -> topotecan)
    assert "not an ingredient" not in name_to_rxcui and "aspirin" not in name_to_rxcui
    assert rxcui_to_atc["1547545"] == "L01FF02" and rxcui_to_atc["57308"] == "L01CE01"


# --- POTTR (drug -> class -> full-path hierarchy; leaf-line model) ----------- #
def _pottr_dir(tmp_path):
    (tmp_path / "drug_database.txt").write_text(
        "Pembrolizumab|Keytruda|MK-3475\tanti-PD-1_monoclonal_antibody\n"
        "Octreotide\tsomatostatin_analog\n"
        "Enzalutamide|Xtandi\tantiandrogen,nonsteroidal,second_generation\n"     # comma is PART of ONE class name
        "Dordaviprone|ONC201\tDRD2_antagonist; AKT_inhibitor\n"                  # ';' -> TWO classes
    )
    # drug_class_hierarchy.txt: each line is a FULL path root<TAB>...<TAB>leaf (variable length; POTTR is a DAG)
    (tmp_path / "drug_class_hierarchy.txt").write_text(
        "cancer_therapy\timmunotherapy\tanti-PD-1_monoclonal_antibody\n"
        "endocrine_therapy\tendocrine_therapy,somastatin_axis-targeting\tsomatostatin_analog\n"
        "cancer_therapy\tcancer_therapy,AR-targeting\tantiandrogen,nonsteroidal,second_generation\n"
        "endocrine_therapy\tantiandrogen,nonsteroidal,second_generation\n"       # SAME class, a 2nd ancestry (DAG)
        "cancer_therapy,DRD2-targeting\tDRD2_antagonist\n"
        "DRD2_antagonist\n"                                                      # degenerate single-node instance
        "cancer_therapy\tAKT_inhibitor\n"
    )
    return tmp_path


def test_pottr_leaf_line_full_path_and_aliases(tmp_path):
    """A hierarchy line is a full root->leaf path; a class's hierarchy = the line(s) ending at it (via any alias)."""
    a2c, l2p, n2p = pottr._parse_dir(_pottr_dir(tmp_path))
    # octreotide -> the exact full path the class appears in
    assert pottr._hierarchy("octreotide", a2c, l2p, n2p) == \
        "endocrine_therapy -> endocrine_therapy,somastatin_axis-targeting -> somatostatin_analog"
    # brand / code aliases resolve to the same class path
    assert pottr._hierarchy("Keytruda", a2c, l2p, n2p) == \
        "cancer_therapy -> immunotherapy -> anti-PD-1_monoclonal_antibody"
    assert pottr._hierarchy("MK-3475", a2c, l2p, n2p) == pottr._hierarchy("pembrolizumab", a2c, l2p, n2p)
    assert pottr._hierarchy("not-in-pottr", a2c, l2p, n2p) == ""


def test_pottr_comma_in_name_semicolon_splits_and_every_instance(tmp_path):
    """Comma stays IN a class name; ';' splits multiple classes; a class that is the leaf of SEVERAL lines (DAG)
    traces out EVERY instance, while a degenerate single-node instance is dropped when a fuller path exists."""
    a2c, l2p, n2p = pottr._parse_dir(_pottr_dir(tmp_path))
    # ONE comma-containing class, leaf of TWO lines -> both instances joined by ' | '
    assert pottr._hierarchy("Enzalutamide", a2c, l2p, n2p) == (
        "cancer_therapy -> cancer_therapy,AR-targeting -> antiandrogen,nonsteroidal,second_generation"
        " | endocrine_therapy -> antiandrogen,nonsteroidal,second_generation")
    # ';' -> two classes; DRD2_antagonist's degenerate single-node line dropped for the fuller path
    assert pottr._hierarchy("Dordaviprone", a2c, l2p, n2p) == \
        "cancer_therapy,DRD2-targeting -> DRD2_antagonist | cancer_therapy -> AKT_inhibitor"


def test_pottr_class_for_any_falls_through_aliases(tmp_path, monkeypatch):
    """pottr_class_for_any tries the canonical name then each alias, first non-empty match wins."""
    parsed = pottr._parse_dir(_pottr_dir(tmp_path))
    monkeypatch.setattr(pottr, "_load", lambda: parsed)
    assert pottr.pottr_class_for_any(["not-in-pottr", "ONC201"]) == \
        "cancer_therapy,DRD2-targeting -> DRD2_antagonist | cancer_therapy -> AKT_inhibitor"   # via the alias
    assert pottr.pottr_class_for_any(["nope", "also-nope"]) == ""


def test_refresh_pottr_downloads_and_archives_previous(tmp_path):
    """refresh_pottr writes current_version/ from a (mocked) download, archives the previous version by its recorded
    date, and records the source + download date in SOURCE.txt."""
    from datetime import date
    cur = tmp_path / "current_version"
    cur.mkdir()
    (cur / "drug_database.txt").write_text("old\told_class\n")
    (cur / "SOURCE.txt").write_text("snapshot_version: 01012026\n")   # previous version's label
    files = {
        f"{pottr.POTTR_SOURCE_URL}/drug_database.txt": b"Octreotide\tsomatostatin_analog\n",
        f"{pottr.POTTR_SOURCE_URL}/drug_class_hierarchy.txt": b"endocrine_therapy\tsomatostatin_analog\n",
    }
    newdir = pottr.refresh_pottr(root=tmp_path, on=date(2026, 7, 20), fetch=lambda url: files[url])
    assert newdir == tmp_path / "current_version"
    assert (newdir / "drug_database.txt").read_text().startswith("Octreotide")     # fresh download
    assert "downloaded: 2026-07-20" in (newdir / "SOURCE.txt").read_text()
    assert (tmp_path / "archive" / "01012026" / "drug_database.txt").read_text() == "old\told_class\n"   # archived
