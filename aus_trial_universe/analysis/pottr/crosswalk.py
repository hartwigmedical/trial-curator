"""COMPONENT 1a — POTTR's terms, expressed in OUR controlled vocabularies.

This is the intermediate file the brief asks for. Without it the two curations are not comparable: POTTR writes
`catype:Colorectal cancer` and `BRAF:V600E`, we write `COADREAD` and
`SmallVariant[gene=BRAF & transcriptImpact.hgvsProteinImpact=p.V600E]`.

TWO TIERS, because only three of our five columns HAVE a target vocabulary:

    cancer_type          -> OncoTree code expression          86 terms   production oncotree mapper
    gene_alteration      -> finding-model                    248 terms   production gene mapper
    molecular_signature  -> finding-model                      3 terms   production signature mapper
    molecular_biomarker  -> (none — IHC/protein)              25 terms   carried as text, judged in `compare`
    prior_therapy        -> (none — free text both sides)    101 terms   carried as text, judged in `compare`

Mapping POTTR's terms with OUR OWN production mappers is the point: it puts both sides through the identical
translation, so a residual difference is a difference in CURATION rather than in vocabulary handling. If we
hand-mapped POTTR's terms instead, every disagreement would be confounded by two different mapping procedures —
which is precisely the bug the drug-approval side had before the pipelines were unified.

RENDERING IS DETERMINISTIC. `BRAF:V600E` -> "BRAF V600E" is a mechanical un-snake-casing, not a judgement, so it
costs nothing and is auditable. The LLM is spent only on the actual vocabulary translation.
"""
from __future__ import annotations

import csv
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from aus_trial_universe.analysis.pottr.paths import POTTR_TERM_CROSSWALK
from aus_trial_universe.analysis.pottr.pottr_source import (
    CANCER_TYPE, GENE_ALTERATION, MOLECULAR_BIOMARKER, MOLECULAR_SIGNATURE, PRIOR_THERAPY,
    distinct_terms, load_rows,
)
from aus_trial_universe.core.client import LlmClient
from aus_trial_universe.core.logfmt import FAIL, PASS, line
from aus_trial_universe.core.workflow import fan_out
from aus_trial_universe.tasks.eligibility.mapping.cancer_type.stage1 import map_oncotree
from aus_trial_universe.tasks.eligibility.mapping.gene_alteration.agents import (
    build_gene_alteration_mapper, build_gene_alteration_reviewer,
)
from aus_trial_universe.tasks.eligibility.mapping.molecular_signature.agents import (
    build_molecular_signature_mapper, build_molecular_signature_reviewer,
)
from aus_trial_universe.tasks.eligibility.mapping.workflow import _GENE_ESCALATION, _map_finding_model

logger = logging.getLogger(__name__)

#: Columns with a controlled target vocabulary. The other two are compared by judgement in `compare.py`.
MAPPED_COLUMNS = (CANCER_TYPE, GENE_ALTERATION, MOLECULAR_SIGNATURE)
UNMAPPED_NOTE = "no controlled vocabulary on our side — compared by LLM judgement against our free text"


@dataclass
class CrosswalkRow:
    pottr_term: str
    namespace: str
    value: str
    our_column: str
    rendered: str
    mapped_value: str = ""
    mapped_name: str = ""
    faithful: bool = True
    attempts: int = 0
    problems: str = ""
    note: str = ""

    COLUMNS = ("pottr_term", "namespace", "value", "our_column", "rendered", "mapped_value", "mapped_name",
               "faithful", "attempts", "problems", "note")

    def as_row(self) -> dict[str, str]:
        d = asdict(self)
        d["faithful"] = "true" if self.faithful else "false"
        d["attempts"] = str(self.attempts)
        return d


# --------------------------------------------------------------------------- #
# Deterministic rendering: POTTR's snake_case DSL -> the clinical English our mappers expect.
# --------------------------------------------------------------------------- #
_MISSENSE_RE = re.compile(r"^([A-Z]\d+)_missense_variant$")
_CODON_RE = re.compile(r"^codon_(\d+)_mutation$")

#: A handful of POTTR spellings whose mechanical un-snake-casing would read oddly to the mapper. Deliberately
#: short: every entry here is a rendering convenience, never a mapping decision.
_PHRASE = {
    "oncogenic_mutation": "mutation",
    "oncogenic_mutations": "mutation",
    "alteration": "alteration",
    "amplification": "amplification",
    "overexpression": "overexpression",
    "protein_expression": "protein expression",
    "low_protein_expression": "low protein expression",
    "loss_of_protein_expression": "loss of protein expression",
    "exon_19_deletion": "exon 19 deletion",
    "exon_20_insertion": "exon 20 insertion",
    "exon_14_skipping_mutation": "exon 14 skipping mutation",
    "internal_tandem_duplication": "internal tandem duplication",
}
_SIGNATURE_PHRASE = {
    "microsatellite_instability:high": "microsatellite instability-high (MSI-H)",
    "mismatch_repair:deficient": "mismatch repair deficient (dMMR)",
    "tumour_mutational_burden:high": "tumour mutational burden high (TMB-H)",
}


def render(namespace: str, value: str, column: str) -> str:
    """POTTR atom -> the natural-language phrase we hand to the production mapper."""
    key = f"{namespace}:{value}"
    if column == CANCER_TYPE:
        return value.strip()
    if column == MOLECULAR_SIGNATURE:
        return _SIGNATURE_PHRASE.get(key, f"{namespace} {value}".replace("_", " ").strip())
    if namespace.upper().startswith("HLA"):
        return f"{namespace}:{value}".replace("_", " ").strip()
    if column == PRIOR_THERAPY:
        return value.replace("_", " ").replace(",", ", ").strip()

    # gene / biomarker: "<GENE> <event>"
    tail = _PHRASE.get(value)
    if tail is None:
        if m := _MISSENSE_RE.match(value):
            tail = f"{m.group(1)} missense variant"
        elif m := _CODON_RE.match(value):
            tail = f"codon {m.group(1)} mutation"
        else:
            tail = value.replace("_", " ").replace(",", ", ").strip()
    return f"{namespace} {tail}".strip()


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #
def build_crosswalk(client: LlmClient, *, workers: int = 8, max_attempts: int = 3, use_reviewer: bool = True,
                    refresh: bool = False, path: Path = POTTR_TERM_CROSSWALK) -> list[CrosswalkRow]:
    rows = load_rows()
    atoms = distinct_terms(rows)
    logger.info("POTTR terms · %d distinct criterion atom(s) over %d trials", len(atoms),
                len({r.trial_id for r in rows}))

    done = {} if refresh else read_crosswalk(path)
    if done:
        # A cached row is only reusable while its COLUMN still matches the current routing. Without this the
        # lookup key (the term string) would happily preserve a row derived under an older `route()` — which is
        # exactly what happened when the underscore normalisation moved two ERBB2 protein-expression terms from
        # gene_alteration to molecular_biomarker: the reuse kept them in the wrong column and mapped nothing.
        stale = [k for k, r in done.items()
                 if k in atoms and r.our_column != atoms[k][2]]
        for k in stale:
            del done[k]
        if stale:
            logger.info("routing changed for %d cached term(s); re-deriving: %s", len(stale), ", ".join(stale[:5]))
        logger.info("lookup-first · %d term(s) already crosswalked; reusing", len(done))

    todo = [(k, ns, val, col) for k, (ns, val, col) in sorted(atoms.items()) if k not in done]
    unmapped = [t for t in todo if t[3] not in MAPPED_COLUMNS]
    mappable = [t for t in todo if t[3] in MAPPED_COLUMNS]
    logger.info("to map %d · carried as text %d", len(mappable), len(unmapped))

    out: list[CrosswalkRow] = list(done.values())
    out += [CrosswalkRow(pottr_term=k, namespace=ns, value=val, our_column=col,
                         rendered=render(ns, val, col), note=UNMAPPED_NOTE)
            for k, ns, val, col in unmapped]

    if mappable:
        results = fan_out([(lambda t=t: _map_one(client, t, max_attempts, use_reviewer)) for t in mappable],
                          max_workers=workers)
        out += results
        logger.info("")
        for r in results:
            logger.info(line(f"{PASS if r.faithful else FAIL}  {r.pottr_term}  →  "
                             f"{r.mapped_value or '(empty)'}", indent=8))

    write_crosswalk(out, path)
    logger.info("")
    logger.info("wrote %s  (%d terms)", path, len(out))
    return out


def _map_one(client: LlmClient, spec: tuple[str, str, str, str], max_attempts: int,
             use_reviewer: bool) -> CrosswalkRow:
    key, ns, val, col = spec
    rendered = render(ns, val, col)
    row = CrosswalkRow(pottr_term=key, namespace=ns, value=val, our_column=col, rendered=rendered)
    if col == CANCER_TYPE:
        res = map_oncotree(client, rendered, max_attempts=max_attempts, use_reviewer=use_reviewer)
        row.mapped_value, row.mapped_name = res.oncotree_code, res.oncotree_name
    else:
        mapper, reviewer, esc = (
            (build_gene_alteration_mapper, build_gene_alteration_reviewer, _GENE_ESCALATION)
            if col == GENE_ALTERATION else
            (build_molecular_signature_mapper, build_molecular_signature_reviewer, None)
        )
        kw = {"escalation": esc} if esc else {}
        res = _map_finding_model(client, rendered, mapper, reviewer, max_attempts=max_attempts,
                                 use_reviewer=use_reviewer, **kw)
        row.mapped_value = res.finding_model
    row.faithful, row.attempts, row.problems = res.faithful, res.attempts, " | ".join(res.problems)
    return row


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def write_crosswalk(rows: list[CrosswalkRow], path: Path = POTTR_TERM_CROSSWALK) -> Path:
    rows = sorted(rows, key=lambda r: (r.our_column, r.pottr_term))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(CrosswalkRow.COLUMNS), delimiter="\t", lineterminator="\n",
                           extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r.as_row())
    return path


def read_crosswalk(path: Path = POTTR_TERM_CROSSWALK) -> dict[str, CrosswalkRow]:
    if not Path(path).exists():
        return {}
    with Path(path).open(encoding="utf-8", newline="") as fh:
        return {
            r["pottr_term"]: CrosswalkRow(
                pottr_term=r["pottr_term"], namespace=r["namespace"], value=r["value"],
                our_column=r["our_column"], rendered=r["rendered"], mapped_value=r.get("mapped_value", ""),
                mapped_name=r.get("mapped_name", ""), faithful=r.get("faithful", "true") == "true",
                attempts=int(r.get("attempts") or 0), problems=r.get("problems", ""), note=r.get("note", ""),
            )
            for r in csv.DictReader(fh, delimiter="\t")
        }
