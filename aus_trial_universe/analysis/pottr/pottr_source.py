"""COMPONENT 1a — load POTTR's curated AU eligibility file and parse its DSL.

THE GRAMMAR, as read off POTTR's own Perl (`Rules.pm` load_proper / `ClinicalTrials.pm:446`):

    row    := term (';' term)*                    ';' is AND
    term   := '*'? 'NOT '? atom
    atom   := '(' alt (' OR ' alt)* ')' | simple  a parenthesised group is an OR
    simple := namespace ':' value

`*` IS NOT DECORATION. `Rules.pm:617` treats a `*`-prefixed constraint by "contingency reasoning by falsely
asserting a fact to satisfy a constraint" — i.e. when the patient's facts do not establish it, POTTR ASSUMES it
and records the assumption. A soft term therefore never blocks a match. 207 of 808 terms carry it, so scoring
them as hard criteria would manufacture differences that do not exist; they are parsed, kept, and flagged.

THE FILE IS HAND-CURATED AND HAS DEFECTS. This parser is deliberately tolerant, but it never repairs silently —
every repair is recorded on the row, surfaces in the crosswalk, and is countable. Real cases in the current file:

    NCT05872295   "ERBB2:overexpression ERBB2:protein_expression OR OR ..."   a missing OR and a doubled OR
    NCT05872295   "NOT Breast cancer"                                          the `catype:` prefix omitted
    NCT06380751   "ER:positive: HER2:negative"                                 a ';' typed as ':'
    NCT06112314   "HLA-A*02:01:Positive"                                       colons inside the value

Bare uppercase tokens (`COHORT_2`, `CLOSED_TO_RECRUITMENT`, `EXTENSION_STUDY`, `NOT_RECRUITING`) are POTTR's
curation ANNOTATIONS, not criteria. They route to `annotation` and are excluded from the criteria comparison —
counting them as POTTR criteria we lack would be a pure artifact.
"""
from __future__ import annotations

import csv
import logging
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from aus_trial_universe.analysis.pottr.paths import POTTR_SNAPSHOT, POTTR_SOURCE_URL, ensure_workspace

logger = logging.getLogger(__name__)

# --- our column names, so the crosswalk and the comparison agree on one spelling --------------------------- #
CANCER_TYPE = "cancer_type"
GENE_ALTERATION = "gene_alteration"
MOLECULAR_SIGNATURE = "molecular_signature"
MOLECULAR_BIOMARKER = "molecular_biomarker"
PRIOR_THERAPY = "prior_therapy"
ANNOTATION = "annotation"          # POTTR bookkeeping — never compared as a criterion

#: The five columns a POTTR term can genuinely land in, in report order.
CRITERION_COLUMNS = (CANCER_TYPE, GENE_ALTERATION, MOLECULAR_SIGNATURE, MOLECULAR_BIOMARKER, PRIOR_THERAPY)

#: Namespaces that ARE the criterion, whatever their value.
_SIGNATURE_NS = {"microsatellite_instability", "mismatch_repair", "tumour_mutational_burden",
                 "homologous_recombination_deficiency", "tumor_mutational_burden"}
#: POTTR machinery, not eligibility: `sensitive_to` drives its therapy recommender; `info`/`trial_type` are notes.
_ANNOTATION_NS = {"info", "trial_type", "sensitive_to", "sensitive_to_drug_class", "recruitment_status"}
#: Inherently non-genetic readouts, whatever the value looks like.
_CLINICAL_NS = {"TILs_percentage", "Ki67_index", "hormone_secretion", "SSTR", "SSTR2", "FOLR", "FOLR1",
                "CLDN18", "TACSTD2", "ERBB4"}
#: A PROTEIN-level value routes a gene namespace to the biomarker column (`ESR1:protein_expression` is IHC;
#: `ESR1:oncogenic_mutation` is a gene alteration). The RHS decides, which is what keeps HER2 in both places.
#: Matched against an UNDERSCORE-NORMALISED value: POTTR's file spells the same concept both ways
#: (`ESR1:protein_expression` and `ESR1:protein expression` both occur), and the space variants of
#: `low_protein_expression` / `loss_of_protein_expression` were silently routed to gene_alteration until this
#: normalisation went in — where they mapped to an empty finding model and looked like inexpressible criteria.
_EXPRESSION_VALUES = {"overexpression", "protein expression", "low protein expression",
                      "high protein expression", "loss of protein expression", "expression",
                      "positive", "negative"}

#: A bare token in ALL-CAPS-WITH-UNDERSCORES, or one of these phrases, is a curation annotation.
_ANNOTATION_TOKEN_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_ANNOTATION_PHRASES = {"expanded access program"}


@dataclass(frozen=True)
class Atom:
    """One `namespace:value` leaf.

    `negated` is the ATOM-level NOT that can appear inside an OR-group (`(A OR NOT B)`), which is distinct from
    the term-level NOT on the whole group. Rare — one occurrence in the current file — but dropping it would
    silently invert a criterion, so it is carried rather than flattened.
    """

    namespace: str
    value: str
    raw: str
    column: str
    negated: bool = False

    def key(self) -> str:
        return f"{self.namespace}:{self.value}"


@dataclass(frozen=True)
class Term:
    """One `;`-delimited conjunct: a single atom, or an OR-group of them, optionally negated and/or soft."""

    atoms: tuple[Atom, ...]
    negated: bool
    soft: bool
    raw: str

    @property
    def column(self) -> str:
        """The column this term compares in. A mixed OR-group is rare; the first atom's column wins and the
        mixture is visible in `raw`."""
        return self.atoms[0].column if self.atoms else ANNOTATION

    @property
    def is_criterion(self) -> bool:
        return self.column != ANNOTATION

    def render(self) -> str:
        body = " OR ".join(("NOT " if a.negated else "") + a.key() for a in self.atoms)
        if len(self.atoms) > 1:
            body = f"({body})"
        return ("NOT " if self.negated else "") + body


@dataclass
class PottrRow:
    """One line of POTTR's file: a trial plus ONE conjunction of its criteria. Rows of a trial are ORed."""

    trial_id: str
    row_index: int
    terms: tuple[Term, ...]
    raw: str
    repairs: list[str] = field(default_factory=list)

    def criteria(self, *, include_soft: bool = True) -> list[Term]:
        return [t for t in self.terms if t.is_criterion and (include_soft or not t.soft)]

    def by_column(self, column: str, *, include_soft: bool = True) -> list[Term]:
        return [t for t in self.criteria(include_soft=include_soft) if t.column == column]


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def download(dest: Path = POTTR_SNAPSHOT, *, force: bool = False) -> Path:
    """Snapshot POTTR's file so a run is reproducible. Public GitHub data, same host as the drug ontology."""
    ensure_workspace()
    if dest.exists() and not force:
        return dest
    logger.info("downloading %s", POTTR_SOURCE_URL)
    with urllib.request.urlopen(POTTR_SOURCE_URL, timeout=60) as resp:   # noqa: S310 — pinned https URL
        dest.write_bytes(resp.read())
    return dest


def load_rows(path: Path = POTTR_SNAPSHOT) -> list[PottrRow]:
    """Parse the snapshot into `PottrRow`s (downloading it first if absent)."""
    download(path)
    with Path(path).open(encoding="utf-8", newline="") as fh:
        raw_rows = list(csv.DictReader(fh, delimiter="\t"))
    known = _known_catypes(raw_rows)
    counter: dict[str, int] = {}
    out: list[PottrRow] = []
    for r in raw_rows:
        tid = (r.get("trial_id") or "").strip()
        if not tid:
            continue
        counter[tid] = counter.get(tid, 0) + 1
        out.append(parse_row(tid, counter[tid], (r.get("eligibility_criteria") or "").strip(), known))
    return out


def _known_catypes(raw_rows: list[dict]) -> set[str]:
    """Every value ever written as `catype:X` in this file — used to recover terms whose prefix was omitted."""
    found: set[str] = set()
    for r in raw_rows:
        for m in re.finditer(r"catype:([^;)]+)", r.get("eligibility_criteria") or ""):
            found.add(m.group(1).strip().rstrip(")").strip().lower())
    return found


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
_OR_SPLIT_RE = re.compile(r"\s+OR\s+", re.IGNORECASE)
#: Two `ns:value` atoms butted together with only whitespace — the "missing OR" defect. The `(?<!\bNOT)`
#: LOOKBEHIND is load-bearing: without it `NOT prior_therapy:systemic_therapy` splits into a bare `NOT` plus an
#: atom and the negation is silently lost — an inverted criterion, the worst failure this parser could have.
_GLUED_RE = re.compile(r"(?<=\S)(?<!\bNOT)\s+(?=[A-Za-z][A-Za-z0-9_.\-]*:)")

#: `ER:positive: HER2:negative` — a ';' typed as ':'. Repaired at ROW level, before the ';' split, because the
#: two halves are ANDed conjuncts; recovering them inside one term would wrongly make them OR alternatives.
#: The `\s+` is what keeps it off legitimate inner colons (`info:panel:AVENIO`, `HLA-A*02:01:Positive`).
_COLON_AS_SEMI_RE = re.compile(r":(?=\s+[A-Za-z][A-Za-z0-9_.\-]*:)")


def parse_row(trial_id: str, row_index: int, text: str, known_catypes: set[str] | None = None) -> PottrRow:
    known_catypes = known_catypes or set()
    repairs: list[str] = []
    body = _COLON_AS_SEMI_RE.sub(";", text)
    if body != text:
        repairs.append(f"':' used as ';' in {text!r} — split into separate conjuncts")
    terms = [t for chunk in body.split(";")
             if (t := _parse_term(chunk, known_catypes, repairs)) is not None]
    return PottrRow(trial_id=trial_id, row_index=row_index, terms=tuple(terms), raw=text, repairs=repairs)


def _parse_term(chunk: str, known_catypes: set[str], repairs: list[str]) -> Term | None:
    raw = chunk.strip()
    if not raw:
        return None
    body, soft, negated = raw, False, False
    if body.startswith("*"):
        soft, body = True, body[1:].strip()
    if re.match(r"^NOT\b", body, re.IGNORECASE):
        negated, body = True, body[3:].strip()
    # A parenthesised OR-group; `*`/`NOT` may also sit INSIDE the parens on a malformed line.
    grouped = body.startswith("(")
    if grouped:
        body = body[1:]
        body = body[:-1] if body.endswith(")") else body
    parts = [p.strip() for p in _OR_SPLIT_RE.split(body)]
    parts = [p for p in parts if p]                    # a doubled ' OR OR ' leaves an empty part
    if grouped and len(parts) != len(_OR_SPLIT_RE.split(body)):
        repairs.append(f"empty OR alternative dropped in {raw!r}")

    atoms: list[Atom] = []
    for part in parts:
        for piece in _split_glued(part, repairs, raw):
            atoms.extend(_parse_atom(piece, known_catypes, repairs, raw))
    if not atoms:
        return None
    return Term(atoms=tuple(atoms), negated=negated, soft=soft, raw=raw)


def _split_glued(part: str, repairs: list[str], raw: str) -> list[str]:
    """`A:x B:y` (a missing OR) -> ['A:x', 'B:y']. Leaves ordinary multi-word values alone, because the split
    point requires the right-hand side to look like `namespace:`."""
    pieces = [p for p in _GLUED_RE.split(part) if p.strip()]
    if len(pieces) > 1:
        repairs.append(f"missing OR between {' / '.join(pieces)} in {raw!r}")
    return pieces


def _parse_atom(piece: str, known_catypes: set[str], repairs: list[str], raw: str) -> list[Atom]:
    piece = piece.strip().strip("()").strip()
    if piece.startswith("*"):
        piece = piece[1:].strip()
    negated = bool(re.match(r"^NOT\s+", piece, flags=re.IGNORECASE))
    if negated:
        piece = re.sub(r"^NOT\s+", "", piece, flags=re.IGNORECASE).strip()
    if not piece:
        return []

    if ":" not in piece:
        return [_bare_atom(piece, known_catypes, repairs, raw, negated)]

    namespace, value = piece.split(":", 1)
    namespace, value = namespace.strip(), value.strip()

    # HLA alleles legitimately carry colons: `HLA-A*02:01:Positive`, `HLA-A:02:01_positive`.
    if namespace.upper().startswith("HLA"):
        return [Atom(namespace=namespace, value=value, raw=piece, column=GENE_ALTERATION, negated=negated)]

    # `ER:positive: HER2:negative` — a ';' typed as ':'. Split only when the tail is itself `namespace:value`.
    if m := re.match(r"^(?P<head>[^:]*?):\s*(?P<ns2>[A-Za-z][A-Za-z0-9_.\-]*):(?P<v2>.+)$", value):
        repairs.append(f"':' used as ';' in {piece!r} — split into two atoms")
        head, ns2, v2 = m.group("head").strip(), m.group("ns2"), m.group("v2").strip()
        return [
            Atom(namespace=namespace, value=head, raw=f"{namespace}:{head}",
                 column=route(namespace, head), negated=negated),
            Atom(namespace=ns2, value=v2, raw=f"{ns2}:{v2}", column=route(ns2, v2), negated=negated),
        ]
    # A trailing ':' is the amputated half of the ';'-typed-as-':' defect once the glue split has run.
    if value.endswith(":"):
        value = value[:-1].strip()
        repairs.append(f"trailing ':' stripped from {piece!r}")
    return [Atom(namespace=namespace, value=value, raw=piece, column=route(namespace, value), negated=negated)]


def _bare_atom(piece: str, known_catypes: set[str], repairs: list[str], raw: str, negated: bool = False) -> Atom:
    """A token with no `namespace:`. Either an omitted `catype:` prefix, or one of POTTR's curation annotations."""
    if piece.lower() in known_catypes:
        repairs.append(f"missing 'catype:' prefix on {piece!r} in {raw!r} — recovered")
        return Atom(namespace="catype", value=piece, raw=piece, column=CANCER_TYPE, negated=negated)
    return Atom(namespace="", value=piece, raw=piece, column=ANNOTATION, negated=negated)


def route(namespace: str, value: str) -> str:
    """Which of OUR columns a POTTR term belongs in.

    POTTR's namespaces do not line up with our columns, so this is a router, not a rename: `ESR1:protein_expression`
    is IHC and belongs with `molecular_biomarker`, while `ESR1:oncogenic_mutation` is a gene alteration. The RHS
    decides for gene namespaces, which is exactly why HER2 correctly lands in both columns depending on the value.
    """
    ns, val = namespace.strip(), value.strip().lower().replace("_", " ")
    if not ns:
        return ANNOTATION
    if ns == "catype":
        return CANCER_TYPE
    if ns == "prior_therapy":
        return PRIOR_THERAPY
    if ns in _SIGNATURE_NS:
        return MOLECULAR_SIGNATURE
    if ns in _ANNOTATION_NS:
        return ANNOTATION
    if ns in _CLINICAL_NS:
        return MOLECULAR_BIOMARKER
    if val in _EXPRESSION_VALUES:
        return MOLECULAR_BIOMARKER
    return GENE_ALTERATION


def is_annotation_token(piece: str) -> bool:
    p = piece.strip()
    return bool(_ANNOTATION_TOKEN_RE.match(p)) or p.lower() in _ANNOTATION_PHRASES


def distinct_terms(rows: list[PottrRow]) -> dict[str, tuple[str, str, str]]:
    """`atom key -> (namespace, value, column)` over every criterion atom — the crosswalk's work list."""
    out: dict[str, tuple[str, str, str]] = {}
    for r in rows:
        for t in r.terms:
            for a in t.atoms:
                if a.column != ANNOTATION:
                    out.setdefault(a.key(), (a.namespace, a.value, a.column))
    return out
