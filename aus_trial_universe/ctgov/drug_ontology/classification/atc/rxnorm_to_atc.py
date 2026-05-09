from __future__ import annotations

"""ATC linking logic for the CTGov drug-ontology pipeline.

This module is deliberately database-agnostic. It owns only ATC/RxNorm source
logic:

    RxNorm RXCUI candidates
        -> RXNCONSO.RRF rows where SAB == "ATC"
        -> ATC tree TSV enrichment
        -> ATC code/name/level/path resolution

The candidate RxCUI set is intentionally broader than just the ingredient
RXCUI. ATC atoms may be attached to a matched concept, salt/precise ingredient,
combination concept, or another related RxNorm concept. PostgreSQL orchestration
belongs in load_atc_mappings_to_postgres.py.
"""

import csv
import logging
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

logger = logging.getLogger(__name__)

RXNCONSO_FILENAME = "RXNCONSO.RRF"
RXNREL_FILENAME = "RXNREL.RRF"
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

# Guardrails for expanded ATC lookup.
#
# The first v2 implementation treated RXNREL as an undirected depth-2 graph and
# allowed product/tradename/part relations. That can walk from an ingredient to
# many products, then back out to unrelated co-ingredients and ATC classes. For
# ATC enrichment we only want local, source-specific rescue candidates.
#
# Direction matters: these relation names are interpreted as rxcui1 -> rxcui2
# exactly as they appear in RXNREL.RRF. We do not synthesize reverse edges.
ALLOWED_RELAS_FROM_INGREDIENT = {
    "has_form",
    "form_of",
    "has_precise_ingredient",
    "precise_ingredient_of",
    "isa",
    "inverse_isa",
}

ALLOWED_RELAS_FROM_MATCHED = {
    "has_ingredient",
    "has_precise_ingredient",
    "consists_of",
    "constitutes",
    "has_form",
    "form_of",
    "isa",
    "inverse_isa",
}

ALLOWED_ATC_CANDIDATE_RELAS = ALLOWED_RELAS_FROM_INGREDIENT | ALLOWED_RELAS_FROM_MATCHED
DEFAULT_RELATED_MAX_DEPTH = 1
MAX_RELATED_CANDIDATES_PER_SOURCE = 40
MAX_ATC_LOOKUP_CANDIDATES = 80

CANDIDATE_KIND_PRIORITY: dict[str, int] = {
    "ingredient": 0,
    "matched": 1,
    "related_from_ingredient": 2,
    "related_from_matched": 3,
}


@dataclass(frozen=True)
class RxnConsoAtcAtom:
    """ATC source atom attached to a RxNorm RXCUI in RXNCONSO.RRF."""

    rxcui: str
    code: str
    name: str
    tty: str
    rxaui: str
    suppress: str


@dataclass(frozen=True)
class RxnRelRow:
    rxcui1: str
    rxaui1: str
    stype1: str
    rel: str
    rxcui2: str
    rxaui2: str
    stype2: str
    rela: str
    rui: str
    srui: str
    sab: str
    sl: str
    rg: str
    dir: str
    suppress: str
    cvf: str


@dataclass(frozen=True)
class RxnRelEdge:
    source_rxcui: str
    target_rxcui: str
    rela: str


@dataclass(frozen=True)
class AtcLookupCandidate:
    """A RxNorm concept that should be checked for ATC atoms."""

    rxcui: str
    rxcui_kind: str
    path: str


@dataclass(frozen=True)
class AtcNode:
    code: str
    level_name: str
    ddd: str = ""
    unit: str = ""
    administration_route: str = ""
    comment: str = ""

    @property
    def level(self) -> int:
        return ATC_LEVEL_BY_CODE_LENGTH.get(len(self.code), 0)


@dataclass(frozen=True)
class AtcResolution:
    atc_lookup_rxcui: str
    atc_lookup_rxcui_kind: str
    atc_lookup_path: str
    atc_code: str
    atc_name_from_rxnconso: str
    atc_tty: str
    atc_rxaui: str
    atc_suppress: str
    tree_name: str
    tree_level: int | None
    tree_path: str
    l1_code: str
    l1_name: str
    l2_code: str
    l2_name: str
    l3_code: str
    l3_name: str
    l4_code: str
    l4_name: str
    l5_code: str
    l5_name: str
    link_status: str


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
                    ddd=clean_text(row.get("DDD", "")),
                    unit=clean_text(row.get("Unit", "")),
                    administration_route=clean_text(row.get("Adm.R", "")),
                    comment=clean_text(row.get("Comment", "")),
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

    def path(self, code: str) -> str:
        pieces: list[str] = []
        for ancestor_code in self.ancestor_codes(code):
            node = self.get(ancestor_code)
            if node is not None:
                pieces.append(format_code_name(node.code, node.level_name))
        return " > ".join(pieces)

    def node_at_level(self, code: str, level: int) -> AtcNode | None:
        for ancestor_code in self.ancestor_codes(code):
            node = self.get(ancestor_code)
            if node is not None and node.level == level:
                return node
        return None


class RxnRelIndex:
    """Small RxNorm relationship graph used to broaden ATC lookup candidates."""

    def __init__(self, rows: Sequence[RxnRelRow]) -> None:
        self.rows = list(rows)
        self.adjacency: dict[str, list[RxnRelEdge]] = defaultdict(list)

        for row in self.rows:
            rela = clean_text(row.rela or row.rel)
            if not row.rxcui1 or not row.rxcui2:
                continue
            if row.sab != "RXNORM" or row.suppress == "O":
                continue
            if rela not in ALLOWED_ATC_CANDIDATE_RELAS:
                continue

            # Keep RXNREL directionality. Do not synthesize reverse edges here;
            # inverse relation rows should be represented explicitly by RxNorm
            # if they are safe to traverse.
            self.adjacency[row.rxcui1].append(RxnRelEdge(row.rxcui1, row.rxcui2, rela))

    @classmethod
    def from_rrf_dir(cls, rxnorm_rrf_dir: Path) -> "RxnRelIndex":
        return cls(list(load_rxnrel(rxnorm_rrf_dir / RXNREL_FILENAME)))

    def related_candidates(
        self,
        source_rxcui: str,
        source_kind: str,
        allowed_relas: set[str],
        max_depth: int = DEFAULT_RELATED_MAX_DEPTH,
        max_candidates: int = MAX_RELATED_CANDIDATES_PER_SOURCE,
    ) -> list[AtcLookupCandidate]:
        source_rxcui = clean_text(source_rxcui)
        if not source_rxcui:
            return []

        related_kind = f"related_from_{source_kind}"
        queue: deque[tuple[str, list[str], list[str]]] = deque([(source_rxcui, [source_rxcui], [])])
        visited = {source_rxcui}
        out: list[AtcLookupCandidate] = []

        while queue:
            current_rxcui, path, relas = queue.popleft()
            if len(path) - 1 >= max_depth:
                continue

            for edge in self.adjacency.get(current_rxcui, []):
                if edge.rela not in allowed_relas:
                    continue
                if edge.target_rxcui in visited:
                    continue
                visited.add(edge.target_rxcui)

                next_path = [*path, edge.target_rxcui]
                next_relas = [*relas, edge.rela]
                out.append(
                    AtcLookupCandidate(
                        rxcui=edge.target_rxcui,
                        rxcui_kind=related_kind,
                        path=format_rxnorm_path(next_path, next_relas),
                    )
                )
                if len(out) >= max_candidates:
                    logger.warning(
                        "ATC candidate cap reached for source_rxcui=%s source_kind=%s max_candidates=%d",
                        source_rxcui,
                        source_kind,
                        max_candidates,
                    )
                    return out
                queue.append((edge.target_rxcui, next_path, next_relas))

        return out


def clean_text(value: object) -> str:
    if value is None:
        return ""
    text_value = str(value).strip()
    return "" if text_value.lower() in {"", "nan", "none", "<na>"} else text_value


def normalize_atc_code(value: object) -> str:
    return clean_text(value).upper()


def format_code_name(code: str, name: str) -> str:
    code = normalize_atc_code(code)
    name = clean_text(name)
    return f"{code}: {name}" if name else code


def format_rxnorm_path(path: Sequence[str], relas: Sequence[str]) -> str:
    pieces: list[str] = []
    for index, rxcui in enumerate(path):
        if index < len(relas):
            pieces.append(f"{rxcui}[{relas[index]}]")
        else:
            pieces.append(rxcui)
    return " -> ".join(pieces)


def read_rrf_rows(path: Path) -> Iterable[list[str]]:
    if not path.exists():
        raise FileNotFoundError(f"Required RRF file not found: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter=RRF_DELIMITER)
        for row in reader:
            if row and row[-1] == "":
                row = row[:-1]
            yield row


def load_rxnrel(path: Path) -> Iterable[RxnRelRow]:
    for line_number, row in enumerate(read_rrf_rows(path), start=1):
        if len(row) < 15:
            logger.warning("Skipping malformed RXNREL row %d with %d columns", line_number, len(row))
            continue
        yield RxnRelRow(
            rxcui1=row[0],
            rxaui1=row[1],
            stype1=row[2],
            rel=row[3],
            rxcui2=row[4],
            rxaui2=row[5],
            stype2=row[6],
            rela=row[7],
            rui=row[8],
            srui=row[9],
            sab=row[10],
            sl=row[11],
            rg=row[12],
            dir=row[13],
            suppress=row[14],
            cvf=row[15] if len(row) > 15 else "",
        )


def load_rxnconso_atc_index(
    rxnconso_path: Path,
    target_rxcuis: set[str],
) -> dict[str, list[RxnConsoAtcAtom]]:
    """Build RXCUI -> ATC atoms from RXNCONSO.RRF for target RXCUIs."""
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
                rxaui=clean_text(row[7]),
                suppress=clean_text(row[16] if len(row) > 16 else ""),
            )
        )
        kept += 1

        if scanned % 500_000 == 0:
            logger.info("Scanned %d RXNCONSO rows; retained %d ATC atoms", scanned, kept)

    logger.info("Loaded %d ATC atoms for %d RXCUIs from %s", kept, len(atc_by_rxcui), rxnconso_path)
    return atc_by_rxcui


def build_atc_lookup_candidates(
    ingredient_rxcui: str,
    matched_rxcui: str,
    rel_index: RxnRelIndex,
    related_max_depth: int = DEFAULT_RELATED_MAX_DEPTH,
    max_candidates: int = MAX_ATC_LOOKUP_CANDIDATES,
) -> list[AtcLookupCandidate]:
    """Build ordered RxCUI candidates for ATC lookup.

    The ingredient RxCUI remains the primary anchor. The matched RxCUI and nearby
    RxNorm concepts are fallbacks that improve ATC recall for salts, precise
    ingredients, combinations, and related forms.

    Guardrails:
    - use only directed RXNREL edges;
    - use one-hop expansion by default;
    - use different relation whitelists from ingredient vs matched concepts;
    - cap candidate count so one highly connected concept cannot explode output.
    """
    candidates: list[AtcLookupCandidate] = []
    seen: set[str] = set()

    def add(candidate: AtcLookupCandidate) -> None:
        if not candidate.rxcui or candidate.rxcui in seen:
            return
        seen.add(candidate.rxcui)
        candidates.append(candidate)

    ingredient_rxcui = clean_text(ingredient_rxcui)
    matched_rxcui = clean_text(matched_rxcui)

    if ingredient_rxcui:
        add(AtcLookupCandidate(ingredient_rxcui, "ingredient", ingredient_rxcui))
    if matched_rxcui:
        add(AtcLookupCandidate(matched_rxcui, "matched", matched_rxcui))

    if ingredient_rxcui:
        for candidate in rel_index.related_candidates(
            ingredient_rxcui,
            "ingredient",
            allowed_relas=ALLOWED_RELAS_FROM_INGREDIENT,
            max_depth=related_max_depth,
        ):
            add(candidate)
    if matched_rxcui:
        for candidate in rel_index.related_candidates(
            matched_rxcui,
            "matched",
            allowed_relas=ALLOWED_RELAS_FROM_MATCHED,
            max_depth=related_max_depth,
        ):
            add(candidate)

    ordered = sorted(
        candidates,
        key=lambda c: (CANDIDATE_KIND_PRIORITY.get(c.rxcui_kind, 99), len(c.path), c.rxcui),
    )
    if len(ordered) > max_candidates:
        logger.warning(
            "Truncating ATC lookup candidates from %d to %d for ingredient_rxcui=%s matched_rxcui=%s",
            len(ordered),
            max_candidates,
            ingredient_rxcui,
            matched_rxcui,
        )
    return ordered[:max_candidates]


def resolve_candidates_to_atc(
    candidates: Sequence[AtcLookupCandidate],
    atc_by_rxcui: Mapping[str, Sequence[RxnConsoAtcAtom]],
    tree: AtcTree,
) -> list[AtcResolution]:
    """Resolve RxCUI candidates to ATC codes with tiered fallback.

    Primary candidates are the Part 2 anchors themselves: the ingredient RXCUI
    and the matched RXCUI. Related RXNREL candidates are rescue lookups only.
    This prevents a common false-positive pattern where a well-classified parent
    ingredient, such as trastuzumab or hydrocortisone, also inherits ATC codes
    from nearby derived forms.

    Within each tier, if the same ATC code is reachable through multiple RXCUIs,
    keep the first hit according to candidate priority.
    """

    def resolve_tier(tier_candidates: Sequence[AtcLookupCandidate]) -> list[AtcResolution]:
        tier_resolutions: list[AtcResolution] = []
        seen_codes: set[str] = set()
        for candidate in tier_candidates:
            atoms = atc_by_rxcui.get(candidate.rxcui, [])
            for atom in atoms:
                if atom.code in seen_codes:
                    continue
                seen_codes.add(atom.code)
                tier_resolutions.append(resolve_atc_atom(candidate, atom, tree))
        return tier_resolutions

    primary_candidates = [
        candidate
        for candidate in candidates
        if candidate.rxcui_kind in {"ingredient", "matched"}
    ]
    primary_resolutions = resolve_tier(primary_candidates)
    if primary_resolutions:
        return primary_resolutions

    related_candidates = [
        candidate
        for candidate in candidates
        if candidate.rxcui_kind.startswith("related_from_")
    ]
    related_resolutions = resolve_tier(related_candidates)
    if related_resolutions:
        return related_resolutions

    primary = candidates[0] if candidates else AtcLookupCandidate("", "none", "")
    return [
        AtcResolution(
            atc_lookup_rxcui=primary.rxcui,
            atc_lookup_rxcui_kind=primary.rxcui_kind,
            atc_lookup_path=primary.path,
            atc_code="",
            atc_name_from_rxnconso="",
            atc_tty="",
            atc_rxaui="",
            atc_suppress="",
            tree_name="",
            tree_level=None,
            tree_path="",
            l1_code="",
            l1_name="",
            l2_code="",
            l2_name="",
            l3_code="",
            l3_name="",
            l4_code="",
            l4_name="",
            l5_code="",
            l5_name="",
            link_status="NO_ATC_FOR_RXCUI",
        )
    ]


def resolve_atc_atom(candidate: AtcLookupCandidate, atom: RxnConsoAtcAtom, tree: AtcTree) -> AtcResolution:
    node = tree.get(atom.code)
    if node is None:
        return AtcResolution(
            atc_lookup_rxcui=candidate.rxcui,
            atc_lookup_rxcui_kind=candidate.rxcui_kind,
            atc_lookup_path=candidate.path,
            atc_code=atom.code,
            atc_name_from_rxnconso=atom.name,
            atc_tty=atom.tty,
            atc_rxaui=atom.rxaui,
            atc_suppress=atom.suppress,
            tree_name="",
            tree_level=None,
            tree_path="",
            l1_code="",
            l1_name="",
            l2_code="",
            l2_name="",
            l3_code="",
            l3_name="",
            l4_code="",
            l4_name="",
            l5_code="",
            l5_name="",
            link_status="ATC_CODE_NOT_IN_TREE",
        )

    level_nodes = {level: tree.node_at_level(atom.code, level) for level in range(1, 6)}
    return AtcResolution(
        atc_lookup_rxcui=candidate.rxcui,
        atc_lookup_rxcui_kind=candidate.rxcui_kind,
        atc_lookup_path=candidate.path,
        atc_code=atom.code,
        atc_name_from_rxnconso=atom.name,
        atc_tty=atom.tty,
        atc_rxaui=atom.rxaui,
        atc_suppress=atom.suppress,
        tree_name=node.level_name,
        tree_level=node.level,
        tree_path=tree.path(atom.code),
        l1_code=level_nodes[1].code if level_nodes[1] else "",
        l1_name=level_nodes[1].level_name if level_nodes[1] else "",
        l2_code=level_nodes[2].code if level_nodes[2] else "",
        l2_name=level_nodes[2].level_name if level_nodes[2] else "",
        l3_code=level_nodes[3].code if level_nodes[3] else "",
        l3_name=level_nodes[3].level_name if level_nodes[3] else "",
        l4_code=level_nodes[4].code if level_nodes[4] else "",
        l4_name=level_nodes[4].level_name if level_nodes[4] else "",
        l5_code=level_nodes[5].code if level_nodes[5] else "",
        l5_name=level_nodes[5].level_name if level_nodes[5] else "",
        link_status="MATCHED_ATC",
    )


def atc_manual_review_needed(resolution: AtcResolution) -> bool:
    return resolution.link_status == "ATC_CODE_NOT_IN_TREE"
