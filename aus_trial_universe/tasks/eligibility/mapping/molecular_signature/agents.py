"""Molecular-signature mapping agents: mapper -> reviewer, plus the shared finding-model Step-2 reconciler.

Split out of the former single `mapping/agents.py` so each vocabulary column owns its own prompts.
The stage-2 group reconciler that used to live here was DELETED 2026-08-06 along with its last caller: this
column's stage 2 is now an explicit pass-through, so nothing adjudicates groups with an LLM any more.
⚠ Prompt text is byte-identical to the pre-split version — it is hashed into the response-cache key.
"""
from __future__ import annotations

from aus_trial_universe.core.agent import Agent
from aus_trial_universe.core.client import LlmClient
from aus_trial_universe.tasks.eligibility.mapping.schema import FindingModelMapping, ReviewVerdict
from aus_trial_universe.tasks.eligibility.mapping.finding_model import GRAMMAR_REFERENCE

_SIGNATURE_RULES = """\
You convert a clinical trial's MOLECULAR-SIGNATURE expression into Hartwig finding-model syntax. Return
`finding_model`, preserving the logical structure (AND `&`, OR `|`, exclusions wrapped in `NOT(...)`).

There are EXACTLY SIX signature terms — the ONLY output vocabulary. Map a genuine signature to its term,
recognising the common synonyms:
- MicrosatelliteStability[PurpleMicrosatelliteStatus=MSI]  <- MSI-high / MSI-H / MSI / dMMR / MMRd / mismatch-repair
    deficient / MMR-deficient / HNPCC / Lynch syndrome / constitutional MMR deficiency.
- MicrosatelliteStability[PurpleMicrosatelliteStatus=MSS]  <- MSS / microsatellite stable / pMMR / MMR-proficient /
    normal MMR / MSI-L / MSI-low / microsatellite instability-low.
  ⚠ MSI-LOW IS NOT MSI-HIGH — it maps to MSS. The status is BINARY, so a caller reports an MSI-low tumour as MSS,
    and the clinic groups MSI-L with MSS rather than with the MSI-H population a trial is selecting for. Treating
    "MSI-low" as MSI would enrol exactly the patients an MSI-H cohort is designed to exclude.
- homologousRecombination[ChordStatus=HR_DEFICIENT]        <- HRD / HRD-positive / homologous-recombination deficient /
    HRR deficiency / BRCAness / FH-deficient / SDH-deficient / COSMIC mutational signature 3 (SBS3).
  ⚠ A MUTATIONAL SIGNATURE with an established meaning IS a signature: "mutational signature 3", "SBS3" and
    "BRCAness" all name the homologous-recombination-deficiency pattern, so they map to HR_DEFICIENT rather than
    to "". A signature identified only by number with no established meaning still maps to "".
  ⚠ An HRR GENE mutation is NOT this signature — "HRRm", "HRR gene-mutated", "deleterious HRR gene mutation" name
    a GENE PANEL and belong to gene_alteration, so they map to "" here. Only the functional DEFICIENCY is the
    signature. (Same distinction the NB below draws; the two must not be conflated.)
- homologousRecombination[ChordStatus=HR_PROFICIENT]       <- HR proficient / HRR proficient / HR-repair non-mutated.
- tumorMutationBurden[Status=HIGH]                         <- TMB-high / TMB-H / high tumour mutational BURDEN.
- tumorMutationLoad[Status=HIGH]                           <- high mutational LOAD / TML-high / high tumour mutational load.
(TMB "burden" -> tumorMutationBurden; "load"/TML -> tumorMutationLoad. Only Status=HIGH exists — a stated
"low/normal TMB/TML" requirement is expressible only as NOT(...[Status=HIGH]).)

DROP inexpressible qualifiers, then map the underlying signature: thresholds/levels ("≥100 somatic SNVs/exome",
"moderate to high"), assay/detection ("by NGS", "centrally confirmed"), treatment/timing context, "high TILs/TLS".

`""` (EMPTY) is CORRECT and COMMON here — return it whenever the value is NOT one of the six signatures. The
molecular-signature column carries MANY non-signature values; map ALL of these to "":
- risk / prognostic scores & categories: "IPI 3-5", "IPSS intermediate-2/high", "FLIPI 2-5", "Oncotype DX RS 11-25",
  "cytogenetic high-risk", "adverse/standard/favourable biology", "high-risk", "complex karyotype".
- expression / molecular SUBTYPES: "Luminal A", "PAM50", "CMS4", "SHH", "triple-negative/TNBC", "non-secretory".
- a GENE ALTERATION or chromosomal event (belongs to gene_alteration): "1p/19q-codeletion", "H3/IDH-wildtype",
  "Ph-like", "HPV", "LOH", "MYCN amplification".
- a protein-expression / receptor BIOMARKER: "HER2-", "HR+", "hormone receptor", "PD-L1".
Do NOT force any of these into a signature term. But do NOT drop a GENUINE signature — "dMMR" IS MSI, "FH-deficient"
IS HR_DEFICIENT; difficulty recognising a synonym is not a reason to bail on a real signature.

NEGATION — an excluded signature wraps its term: "MSS required, exclude MSI-H" side / "NOT(MSI-H)" ->
NOT(MicrosatelliteStability[PurpleMicrosatelliteStatus=MSI]). A signature qualified in an EXCLUSION by an
inexpressible condition ("NOT(MSI-H without prior immune checkpoint inhibitor)") — omit the inexpressible qualifier
only if that does not over-exclude; otherwise keep the bare signature negation.

EXAMPLES (source -> finding_model):
- "MSI-high"                                 -> MicrosatelliteStability[PurpleMicrosatelliteStatus=MSI]
- "dMMR/MSI-H"                                -> MicrosatelliteStability[PurpleMicrosatelliteStatus=MSI]
- "mismatch repair proficient (pMMR)"        -> MicrosatelliteStability[PurpleMicrosatelliteStatus=MSS]
- "HRD-positive"                             -> homologousRecombination[ChordStatus=HR_DEFICIENT]
- "FH deficient"                             -> homologousRecombination[ChordStatus=HR_DEFICIENT]
- "TMB-high"                                 -> tumorMutationBurden[Status=HIGH]
- "high mutational load (>100 somatic SNVs/exome)" -> tumorMutationLoad[Status=HIGH]
- "NOT(MSI-H)"                               -> NOT(MicrosatelliteStability[PurpleMicrosatelliteStatus=MSI])
- "adverse biology"                          -> (empty)
- "IPSS intermediate-2 or high-risk"         -> (empty)
- "Luminal A"                                -> (empty)
- "1p/19q-codeletion"                        -> (empty)   (a chromosomal alteration, not a signature)
- "HER2-negative"                            -> (empty)   (an expression biomarker, not a signature)

"""

SIGNATURE_REVIEWER_INSTRUCTIONS = """\
You audit a proposed finding-model conversion of a MOLECULAR-SIGNATURE value. You are given the SOURCE value and the
proposed finding_model. There are EXACTLY SIX valid signature terms (MSI/MSS microsatellite status, HR_DEFICIENT/
HR_PROFICIENT, tumorMutationBurden HIGH, tumorMutationLoad HIGH).

Set faithful=true only if ALL hold; otherwise faithful=false with concrete, actionable problems:
1. RIGHT TERM — a genuine signature uses the correct class + status, recognising synonyms (dMMR/MMRd/Lynch -> MSI;
   pMMR -> MSS; HRD/HRR-deficient/FH-/SDH-deficient -> HR_DEFICIENT; TMB "burden" vs "load" kept distinct).
2. EMPTY IS CORRECT FOR NON-SIGNATURES — a risk/prognostic score, an expression subtype, a gene/chromosomal
   alteration, or a protein-expression biomarker MUST be "" — NOT forced into a signature term. Flag a HALLUCINATED
   signature (a non-signature value mapped to one of the six terms).
3. NOT LAZY — a GENUINE signature must NOT be dropped to "" (dMMR -> MSI, not empty).
4. NEGATION — an excluded signature is wrapped in NOT(); structure matches the source.

5. MSI-LOW IS MSS, NOT MSI. The status is binary and an MSI-low tumour is called MSS, so "MSI-L" /
   "microsatellite instability-low" mapping to MicrosatelliteStability[PurpleMicrosatelliteStatus=MSI] is a FAULT.
6. A NAMED MUTATIONAL SIGNATURE counts. "Mutational signature 3" / "SBS3" / "BRCAness" name the
   homologous-recombination-deficiency pattern -> HR_DEFICIENT; mapping them to "" is a lazy empty.
7. AN HRR GENE-PANEL MUTATION IS NOT THE HRD SIGNATURE. "HRRm" / "HRR gene-mutated" / "deleterious HRR gene
   mutation" name genes and belong to gene_alteration, so "" is CORRECT for them — flag a mapping that forces
   them into HR_DEFICIENT. Only the functional deficiency ("HRD", "HRR deficiency") is the signature.

Do NOT fail a mapping for dropping an inexpressible qualifier (threshold/level, assay, timing, "high TILs"). "" is
the expected answer for the many non-signature values — do NOT demand a mapping for them.

CORRECT REFERENCE MAPPINGS — accept these:
- "dMMR/MSI-H" -> MicrosatelliteStability[PurpleMicrosatelliteStatus=MSI]
- "FH deficient" -> homologousRecombination[ChordStatus=HR_DEFICIENT]
- "high mutational load (>100 somatic SNVs/exome)" -> tumorMutationLoad[Status=HIGH]
- "NOT(MSI-H)" -> NOT(MicrosatelliteStability[PurpleMicrosatelliteStatus=MSI])
- "adverse biology" -> (empty)      - "Luminal A" -> (empty)      - "1p/19q-codeletion" -> (empty)

MAPPINGS YOU MUST FAIL (proposed -> problem -> fix):
- SOURCE "dMMR", proposed "" -> lazy empty; dMMR IS MSI. fix "MicrosatelliteStability[PurpleMicrosatelliteStatus=MSI]".
- SOURCE "cytogenetic high-risk", proposed "tumorMutationBurden[Status=HIGH]" -> hallucinated; a risk category is not
  a signature. fix "" (empty).
- SOURCE "HER2-negative", proposed "MicrosatelliteStability[...]" -> a biomarker, not a signature. fix "" (empty).
- SOURCE "high mutational BURDEN", proposed "tumorMutationLoad[Status=HIGH]" -> burden is tumorMutationBurden.
  fix "tumorMutationBurden[Status=HIGH]".

`suggested_fix` — normally leave EMPTY. ONLY when the input is marked "[ESCALATION-MODE]", fill it with the concrete
corrected finding_model you would expect.
"""
def build_molecular_signature_mapper(client: LlmClient, *, model: str | None = None) -> Agent[FindingModelMapping]:
    return Agent(
        name="molecular_signature_mapper",
        instructions=_SIGNATURE_RULES,
        output_schema=FindingModelMapping,
        client=client,
        model=model,
    )


def build_molecular_signature_reviewer(client: LlmClient, *, model: str | None = None) -> Agent[ReviewVerdict]:
    return Agent(
        name="molecular_signature_reviewer",
        instructions=SIGNATURE_REVIEWER_INSTRUCTIONS,
        output_schema=ReviewVerdict,
        client=client,
        model=model,
    )
