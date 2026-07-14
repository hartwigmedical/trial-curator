"""Deterministic POTTR / RxNorm+ATC lookups (spec §6.1) — tested with tiny fixtures (no gitignored data)."""
from __future__ import annotations

from aus_trial_universe.agentic.tasks.drug_ref import pottr, rxnorm


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


# --- POTTR (parse + hierarchy walk) ----------------------------------------- #
def _pottr_dir(tmp_path):
    (tmp_path / "drug_database.txt").write_text(
        "Pembrolizumab|Keytruda|MK-3475\tanti-PD-1_monoclonal_antibody\n"
        "Topotecan|Hycamtin\ttopoisomerase_inhibitor\n"
    )
    (tmp_path / "drug_class_hierarchy.txt").write_text(
        "cancer_therapy\timmunotherapy\n"
        "immunotherapy\tanti-PD-1_monoclonal_antibody\n"
        "cancer_therapy\tcytotoxic_chemotherapy\n"
        "cytotoxic_chemotherapy\ttopoisomerase_inhibitor\n"
    )
    return tmp_path


def test_pottr_hierarchy_walks_leaf_to_root_and_matches_aliases(tmp_path):
    a2c, c2p = pottr._parse_dir(_pottr_dir(tmp_path))
    # leaf -> root ordering, via any alias, case-insensitive
    assert pottr._hierarchy("pembrolizumab", a2c, c2p) == \
        "cancer_therapy -> immunotherapy -> anti-PD-1_monoclonal_antibody"
    assert pottr._hierarchy("Keytruda", a2c, c2p) == \
        "cancer_therapy -> immunotherapy -> anti-PD-1_monoclonal_antibody"      # brand alias resolves
    assert pottr._hierarchy("MK-3475", a2c, c2p) == pottr._hierarchy("pembrolizumab", a2c, c2p)
    assert pottr._hierarchy("Topotecan", a2c, c2p) == \
        "cancer_therapy -> cytotoxic_chemotherapy -> topoisomerase_inhibitor"
    assert pottr._hierarchy("not-in-pottr", a2c, c2p) == ""                     # absent -> empty


def test_pottr_root_path_cycle_guard():
    assert pottr._root_path("a", {"a": "b", "b": "a"})[-1] == "a"               # terminates on a cycle
