"""THE REVIEW JUDGE — an LLM verdict per changed value, for any vocabulary column.

Rewritten 2026-08-06 on the user's instruction: *"avoid regex in all these processing and comparisons. just llm
judgement instead."*

WHY THE FIRST VERSION WAS WRONG. It recorded my verdicts as a list of `(substring key, verdict, reason)` and
resolved each key against the corpus by string matching. That collided three times in a row — a clause that occurs
both standalone and embedded in five longer values, two siblings differing only by `mutation(s)` vs `mutations`,
two more differing only in their trailing clause — and every collision needed a hand-tuned, longer key. It is the
same failure mode as `concept_key`'s unbounded `str.replace` (which silently inverted "ineligible" -> "in") and the
stage-pattern regex that ate 775 values' leading letters: a textual heuristic standing in for a judgement about
MEANING. The lesson generalises — if the question is semantic, do not answer it with string surgery.

WHAT IS STILL DETERMINISTIC, and why that is not the same thing:
  · WHICH values need a verdict — decided by `equivalent()`, i.e. by CANONICALISING both expressions and comparing.
    That is exact algebra over a parsed AST, not a textual guess, and it is precisely the user's instruction to
    treat stage-2 manipulation as no difference at all.
  · WHICH population a value belongs to (`this_run` vs `prior_approved`) — again canonical equality, exact.
Only the QUALITY judgement is delegated, because only that part is a matter of meaning.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from aus_trial_universe.core.agent import Agent
from aus_trial_universe.core.client import LlmClient
from aus_trial_universe.tasks.eligibility.mapping.finding_model import GRAMMAR_REFERENCE
from aus_trial_universe.tasks.eligibility.mapping.gene_alteration import expr as E

VERDICTS = ("IDENTICAL", "IMPROVEMENT", "REGRESSION", "NEUTRAL", "UNCERTAIN")


def equivalent(a: str, b: str, column: str = "gene_alteration") -> bool:
    """Same MEANING — stage-2 algebraic differences collapsed, by canonicalising and comparing ASTs.

    `cancer_type` has its OWN canonical form (an OncoTree code algebra), so it is dispatched separately. A column
    whose stage 2 is a pass-through (molecular_signature today) still routes through the finding-model
    canonicaliser, which is correct and free: its expressions are finding-model, so an ordering or De Morgan
    difference would be noise there too if one ever arose.

    `NOT(a|b)` == `NOT(a) & NOT(b)`; term and field order, duplicate literals and absorbed terms are all
    normalised away. Not a textual comparison: both sides are parsed, pushed to negation-factored DNF and
    re-rendered, so this is exact rather than approximate.
    """
    if column == "cancer_type":
        from aus_trial_universe.tasks.eligibility.mapping.cancer_type.stage2 import canonicalise as ct_canon
        return ct_canon(a or "") == ct_canon(b or "")
    try:
        return E.canonicalise(a or "") == E.canonicalise(b or "")
    except E.ExprError:
        return (a or "").strip() == (b or "").strip()


class MappingVerdict(BaseModel):
    """One value's review verdict."""

    verdict: str = Field(
        description="Exactly one of: IDENTICAL, IMPROVEMENT, REGRESSION, NEUTRAL, UNCERTAIN. "
                    "IMPROVEMENT/REGRESSION are judged for NEW against the CURRENT mapping.")
    improvement_reason: str = Field(
        default="", description="Why NEW is better. Fill ONLY for IMPROVEMENT or NEUTRAL. One or two sentences, "
                                "naming the concrete difference. Empty otherwise.")
    regression_reason: str = Field(
        default="", description="Why NEW is worse, or — for UNCERTAIN — what the unresolved tension is. Fill ONLY "
                                "for REGRESSION or UNCERTAIN. One or two sentences. Empty otherwise.")


_FRAME = """\
You are auditing a change to how one clinical-trial {LABEL} criterion is mapped into the Hartwig target
vocabulary. You are given the SOURCE free text and three mappings of it: an OLD one (28 July), the CURRENT
shipped one, and a NEW candidate. Judge the NEW mapping against the CURRENT one, on FAITHFULNESS TO THE SOURCE
ALONE.

Return `verdict` = exactly one of:
- IMPROVEMENT  NEW is more faithful to the source than CURRENT
- REGRESSION   NEW is less faithful
- NEUTRAL      both are defensible; the difference is a spelling or convention choice with no change in which
               patients match
- UNCERTAIN    genuinely needs a domain expert; say precisely what the unresolved tension is
- IDENTICAL    the two express the same criterion

THE ASYMMETRY THAT DECIDES MOST CASES. These mappings select patients for trial matching, so the two error
directions are NOT equally bad:
- An OVER-EXCLUSION (the mapping excludes more than the source does) silently denies the trial to patients who
  qualify. They never surface as candidates, so nobody can catch it. This is the worse error.
- An UNDER-EXCLUSION (the mapping drops an exclusion the source states) matches patients the trial explicitly
  refuses. A reviewing clinician sees them and can reject them, but the false positive is real.
- For an INCLUSION, a SUPERSET is acceptable when the source is vague — a superset is acceptable, a lost patient
  is not. So broadening a positive term is usually NOT a regression, whereas narrowing one usually IS.

{COLUMN_RULES}

DO NOT judge on spelling, term order, De Morgan form, or redundancy that a downstream normaliser removes — those
differences have already been collapsed before you see them, so any difference you are shown is a real one.
Be concrete: name the term that changed and what it does to which patients match. Do not restate the rule
you applied without saying what it did to THIS value.
"""


#: The per-column domain half. The judging FRAME above is shared; only these rules change, because only they are
#: about the vocabulary. Each block states what that column's mapper is REQUIRED to do, so the judge does not
#: flag correct behaviour as a defect.
_COLUMN_RULES = {
    "gene_alteration": """HOW THIS COLUMN'S MAPPING RULES HANDLE INEXPRESSIBLE WORDING, so you do not flag correct behaviour:
- Qualifiers finding-model cannot express are DROPPED, and that is correct: "activating", "actionable",
  "pathogenic", "germline/somatic", assay/detection wording, copy-number counts, VAF, protein DOMAIN names.
- An EXCLUSION carrying an availability/actionability/resistance judgement is decided by WHETHER ANYTHING IS
  NAMED — not by how specific the name is. An enumerated gene list IS named and must be KEPT and excluded gene by
  gene ("NOT(actionable alterations in EGFR, ALK, ROS1)" excludes each of those three). Only a clause naming
  NOTHING ("NOT(other alterations for which targeted therapy exists)") is correctly OMITTED. A mixed clause keeps
  its named members and drops the open remainder.
- A resistance mechanism is kept when it has an established molecular referent (RB1 loss for CDK4/6 inhibitors)
  and omitted when it is an open set of secondary mutations ("known MET kinase inhibitor resistance mutation").
- A gene family, panel or named complex is EXPANDED to its members. A canonical fusion driver (ALK, ROS1, RET,
  NTRK1/2/3, NRG1, FGFR3) does NOT gain `type=GAIN` from an unspecified "alteration".
- Trial-process framing ("eligible without X", "only with sponsor approval", "required for cohort B") is
  packaging; the criterion inside it should still be mapped.
""",
    "molecular_signature": """\
HOW THIS COLUMN'S MAPPING RULES WORK, so you do not flag correct behaviour:
- There are EXACTLY SIX target terms and nothing else exists: MSI / MSS microsatellite status, HR_DEFICIENT /
  HR_PROFICIENT homologous recombination, tumorMutationBurden[Status=HIGH], tumorMutationLoad[Status=HIGH].
- `""` (EMPTY) IS THE CORRECT AND COMMON ANSWER. This column carries many values that are NOT signatures, and
  forcing one into a term is a hallucination. Correctly empty: risk / prognostic scores and cytogenetic risk
  categories (IPSS, ELN, "adverse biology", "complex karyotype", Oncotype DX), expression or intrinsic SUBTYPES
  (Luminal A, CMS4, PAM50, SHH/WNT), a GENE or CHROMOSOMAL alteration (1p/19q co-deletion, del(17p), t(11;14),
  Ph+, H3K27-altered, an HRR GENE mutation), a protein-expression biomarker (HER2, PD-L1), ploidy or karyotype
  statements, MRD / ctDNA detection, and "actionable genomic alteration" (AGA) wording.
- But a GENUINE signature must NOT be dropped to "": recognise the synonyms. dMMR / MMRd / mismatch-repair
  deficient / Lynch -> MSI. pMMR / MMR-proficient -> MSS. HRD / HRR-deficient / BRCAness / FH- or SDH-deficient
  -> HR_DEFICIENT. A named mutational SIGNATURE with an established meaning counts: COSMIC signature 3 (SBS3) IS
  the homologous-recombination-deficiency signature.
- ⚠ MSI-LOW IS NOT MSI-HIGH. The target status is binary and an MSI-low tumour is called MSS, so "MSI-L" /
  "microsatellite instability-low" maps to MSS — never to MSI.
- Thresholds, assay and detection wording are DROPPED and the underlying signature kept ("TMB >= 10 mut/Mb",
  "by NGS", "centrally confirmed"). "Burden" -> tumorMutationBurden; "load" / TML -> tumorMutationLoad.
- An excluded signature is wrapped in NOT(). Where the exclusion carries an inexpressible clinical qualifier, the
  bare signature negation is kept rather than the clause dropped.""",
}


def build_mapping_judge(client: LlmClient, column: str = "gene_alteration", *,
                        model: str | None = None) -> Agent[MappingVerdict]:
    """The judge for one column. Registered per column so the two never share a response-cache key."""
    label = column.replace("_", "-").upper()
    instructions = (_FRAME.replace("{LABEL}", label)
                          .replace("{COLUMN_RULES}", _COLUMN_RULES[column]) + GRAMMAR_REFERENCE)
    return Agent(name=f"{column}_review_judge", instructions=instructions,
                 output_schema=MappingVerdict, client=client, model=model)


def judge_input(source: str, jul: str, cur: str, new: str) -> str:
    return (
        f"SOURCE criterion:\n{source}\n\n"
        f"OLD mapping (28 July):\n{jul or '(empty)'}\n\n"
        f"CURRENT shipped mapping:\n{cur or '(empty)'}\n\n"
        f"NEW candidate mapping:\n{new or '(empty)'}\n\n"
        f"Judge NEW against CURRENT."
    )
