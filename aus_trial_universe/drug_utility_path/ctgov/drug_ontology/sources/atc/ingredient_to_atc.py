from __future__ import annotations

"""Resolve RxNorm ingredient anchors to ATC codes.

This module is database-agnostic. It owns only source-file logic:

    RxNorm ingredient RXCUI
        -> RXNCONSO.RRF rows where SAB == "ATC"
        -> ATC tree TSV enrichment

Important design rule
---------------------
ATC Part 3A maps from RxNorm ingredient anchors only. It does not consume CTGov
aliases directly and it does not traverse broad RxNorm relationship graphs. Trial
linkage is handled separately through the Part 2 intervention-drug-term link table.
"""

import csv
import logging
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

logger = logging.getLogger(__name__)

RXNCONSO_FILENAME = "RXNCONSO.RRF"
RRF_DELIMITER = "|"
ATC_SOURCE_ABBREVIATION = "ATC"

# WHO ATC hierarchy code lengths:
#   A       -> level 1
#   A01     -> level 2
#   A01A    -> level 3
#   A01AA   -> level 4
#   A01AA01 -> level 5
ATC_LEVEL_BY_CODE_LENGTH: dict[int, int] = {
    1: 1,
    3: 2,
    4: 3,
    5: 4,
    7: 5,
}


@dataclass(frozen=True)
class RxnConsoAtcAtom:
    """ATC atom attached to a RxNorm RXCUI in RXNCONSO.RRF."""

    rxcui: str
    code: str
    name: str
    tty: str


@dataclass(frozen=True)
class AtcNode:
    code: str
    level_name: str

    @property
    def level(self) -> int:
        return ATC_LEVEL_BY_CODE_LENGTH.get(len(self.code), 0)


@dataclass(frozen=True)
class AtcResolution:
    atc_match_status: str
    atc_code: str
    atc_name: str
    atc_level: int | None
    atc_l1_code: str
    atc_l1_name: str
    atc_l2_code: str
    atc_l2_name: str
    atc_l3_code: str
    atc_l3_name: str
    atc_l4_code: str
    atc_l4_name: str
    atc_l5_code: str
    atc_l5_name: str


class AtcTree:
    def __init__(self, nodes: Mapping[str, AtcNode]) -> None:
        self.nodes = dict(nodes)

    @classmethod
    def from_tsv(cls, atc_tree_tsv: Path) -> "AtcTree":
        if not atc_tree_tsv.exists():
            raise FileNotFoundError(f"ATC tree TSV not found: {atc_tree_tsv}")

        nodes: dict[str, AtcNode] = {}
        with atc_tree_tsv.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            if reader.fieldnames is None:
                raise ValueError(f"Could not read header from ATC tree TSV: {atc_tree_tsv}")

            required = {"ATC code", "ATC level name"}
            missing = required - set(reader.fieldnames)
            if missing:
                raise KeyError(f"ATC tree TSV missing required columns: {sorted(missing)}")

            for row in reader:
                code = normalize_atc_code(row.get("ATC code", ""))
                if not code:
                    continue
                nodes[code] = AtcNode(
                    code=code,
                    level_name=clean_text(row.get("ATC level name", "")),
                )

        logger.info("Loaded %d ATC tree nodes from %s", len(nodes), atc_tree_tsv)
        return cls(nodes)

    def get(self, code: str) -> AtcNode | None:
        return self.nodes.get(normalize_atc_code(code))

    def ancestor_codes(self, code: str) -> list[str]:
        code = normalize_atc_code(code)
        if not code:
            return []

        prefixes = [
            code[:1],
            code[:3] if len(code) >= 3 else "",
            code[:4] if len(code) >= 4 else "",
            code[:5] if len(code) >= 5 else "",
            code[:7] if len(code) >= 7 else "",
        ]
        return [prefix for prefix in prefixes if prefix and prefix in self.nodes]

    def node_at_level(self, code: str, level: int) -> AtcNode | None:
        for ancestor_code in self.ancestor_codes(code):
            node = self.get(ancestor_code)
            if node is not None and node.level == level:
                return node
        return None


def clean_text(value: object) -> str:
    if value is None:
        return ""
    text_value = str(value).strip()
    return "" if text_value.lower() in {"", "nan", "none", "<na>"} else text_value


def normalize_atc_code(value: object) -> str:
    return clean_text(value).upper()


def read_rrf_rows(path: Path) -> Iterable[list[str]]:
    if not path.exists():
        raise FileNotFoundError(f"Required RRF file not found: {path}")

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter=RRF_DELIMITER)
        for row in reader:
            if row and row[-1] == "":
                row = row[:-1]
            yield row


def load_rxnconso_atc_index(
    rxnconso_path: Path,
    target_rxcuis: set[str],
) -> dict[str, list[RxnConsoAtcAtom]]:
    """Build RXCUI -> ATC atoms from RXNCONSO.RRF for target ingredient RXCUIs."""
    atc_by_rxcui: dict[str, list[RxnConsoAtcAtom]] = defaultdict(list)
    if not target_rxcuis:
        return atc_by_rxcui

    scanned = 0
    kept = 0

    for row in read_rrf_rows(rxnconso_path):
        scanned += 1
        if len(row) < 17:
            continue

        rxcui = row[0]
        if rxcui not in target_rxcuis:
            continue
        if row[11] != ATC_SOURCE_ABBREVIATION:
            continue

        code = normalize_atc_code(row[13])
        if not code:
            continue

        atc_by_rxcui[rxcui].append(
            RxnConsoAtcAtom(
                rxcui=rxcui,
                code=code,
                name=clean_text(row[14]),
                tty=clean_text(row[12]),
            )
        )
        kept += 1

        if scanned % 500_000 == 0:
            logger.info("Scanned %d RXNCONSO rows; retained %d ATC atoms", scanned, kept)

    logger.info(
        "Loaded %d ATC atoms for %d target RXCUIs from %s",
        kept,
        len(atc_by_rxcui),
        rxnconso_path,
    )
    return atc_by_rxcui


def resolve_ingredient_to_atc(
    rxnorm_ingredient_rxcui: str,
    atc_by_rxcui: Mapping[str, Sequence[RxnConsoAtcAtom]],
    tree: AtcTree,
) -> list[AtcResolution]:
    """Resolve one RxNorm ingredient RXCUI to ATC code rows.

    No RxNorm relationship traversal is performed. If there is no ATC atom
    directly attached to the ingredient RXCUI, the result is a single
    NO_ATC_MATCH row.
    """
    rxcui = clean_text(rxnorm_ingredient_rxcui)
    atoms = list(atc_by_rxcui.get(rxcui, []))

    if not atoms:
        return [
            AtcResolution(
                atc_match_status="NO_ATC_MATCH",
                atc_code="",
                atc_name="",
                atc_level=None,
                atc_l1_code="",
                atc_l1_name="",
                atc_l2_code="",
                atc_l2_name="",
                atc_l3_code="",
                atc_l3_name="",
                atc_l4_code="",
                atc_l4_name="",
                atc_l5_code="",
                atc_l5_name="",
            )
        ]

    out: list[AtcResolution] = []
    seen_codes: set[str] = set()

    for atom in sorted(atoms, key=lambda a: (a.code, a.name, a.tty)):
        if atom.code in seen_codes:
            continue
        seen_codes.add(atom.code)
        out.append(resolve_atc_atom(atom, tree))

    return out


def resolve_atc_atom(atom: RxnConsoAtcAtom, tree: AtcTree) -> AtcResolution:
    node = tree.get(atom.code)

    if node is None:
        return AtcResolution(
            atc_match_status="ATC_CODE_NOT_IN_TREE",
            atc_code=atom.code,
            atc_name=atom.name,
            atc_level=None,
            atc_l1_code="",
            atc_l1_name="",
            atc_l2_code="",
            atc_l2_name="",
            atc_l3_code="",
            atc_l3_name="",
            atc_l4_code="",
            atc_l4_name="",
            atc_l5_code="",
            atc_l5_name="",
        )

    level_nodes = {level: tree.node_at_level(atom.code, level) for level in range(1, 6)}

    return AtcResolution(
        atc_match_status="MATCHED_ATC",
        atc_code=atom.code,
        atc_name=node.level_name or atom.name,
        atc_level=node.level,
        atc_l1_code=level_nodes[1].code if level_nodes[1] else "",
        atc_l1_name=level_nodes[1].level_name if level_nodes[1] else "",
        atc_l2_code=level_nodes[2].code if level_nodes[2] else "",
        atc_l2_name=level_nodes[2].level_name if level_nodes[2] else "",
        atc_l3_code=level_nodes[3].code if level_nodes[3] else "",
        atc_l3_name=level_nodes[3].level_name if level_nodes[3] else "",
        atc_l4_code=level_nodes[4].code if level_nodes[4] else "",
        atc_l4_name=level_nodes[4].level_name if level_nodes[4] else "",
        atc_l5_code=level_nodes[5].code if level_nodes[5] else "",
        atc_l5_name=level_nodes[5].level_name if level_nodes[5] else "",
    )
