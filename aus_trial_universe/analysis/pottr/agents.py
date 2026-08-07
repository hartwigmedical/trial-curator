"""Agents for the disease-derived alteration inference (component 2).

⚠ Every prompt string here, and every pydantic docstring / Field description in `schema.py`, is hashed into the
response-cache key (`core/client._fingerprint`). Editing this prose orphans every cached decision behind it.

WHY THIS COLUMN EXISTS. POTTR records genetics that the trial's DISEASE implies rather than states. On
`NCT04924075` its "von Hippel-Lindau (VHL) disease associated tumors" cohort carries `VHL:oncogenic_mutation` and
its wild-type-GIST cohort carries `NOT KIT:oncogenic_mutation` — neither of which appears in our
`gene_alteration` column, because in the source the genetics are carried by the cancer-type wording. Mapping the
cancer type to OncoTree destroys exactly that content ("VHL disease associated tumors" -> `Solid tumour`;
"wild-type GIST" -> `GIST`), which is why this agent reads the INTERPRETED FREE TEXT and not the mapped code.

THE PROMPT'S CENTRAL RISK IS THE OPPOSITE OF EVERY OTHER MAPPER WE HAVE. Elsewhere `""` is the lazy answer and the
prompts fight for output. Here `""` is the CORRECT answer for the overwhelming majority of values, and the failure
mode is a model volunteering clear-cell RCC -> VHL because it is true 90% of the time. Hence the definitional bar,
stated as a burden of proof, with the hard near-miss cases written out as counter-examples.
"""
from __future__ import annotations

from aus_trial_universe.core.agent import Agent
from aus_trial_universe.core.client import LlmClient
from aus_trial_universe.analysis.pottr.schema import (
    DiseaseDerivedAlteration, FreeTextAlignment, MistakeVerdict,
)
# Reused unchanged: the reviewer contract `review_refine` and its escalation step already expect
# (faithful, problems, suggested_fix). A bespoke verdict schema would buy nothing and fork a proven one.
from aus_trial_universe.tasks.eligibility.mapping.schema import ReviewVerdict

# --------------------------------------------------------------------------- #
# The shared standard. Doer and reviewer are graded against the SAME bar, so it is written once and appended to
# both — a reviewer applying a looser test than the doer is how over-firing survives review.
# --------------------------------------------------------------------------- #
DEFINITIONAL_STANDARD = """\
THE BAR — DEFINITIONAL ENTAILMENT, AND NOTHING WEAKER

Emit an alteration ONLY if it is part of what MAKES the named entity that entity: a tumour lacking the alteration
would not be given this diagnosis, or would be reclassified as something else. Ask yourself the withdrawal test:

    "If this alteration were shown to be ABSENT, would the stated diagnosis still stand?"
    Still stands -> the alteration is NOT definitional -> return "".

PREVALENCE IS NOT DEFINITION, at any frequency. "Almost always", "characteristic of", "the hallmark of",
"nearly all cases", "the canonical driver" — every one of these describes an ASSOCIATION and must return "".
This is the single most important rule here, because an association is usually TRUE, and a true-but-not-
definitional inference silently rewrites the trial's eligibility. High-frequency associations that must
STILL return "" include, and are not limited to:
- clear cell renal cell carcinoma -> VHL (~90%)            - pancreatic ductal adenocarcinoma -> KRAS (~90%)
- high-grade serous ovarian carcinoma -> TP53 (~96%)       - melanoma -> BRAF (~50%)
- colorectal adenocarcinoma -> APC                          - follicular lymphoma -> BCL2 rearrangement (~85%)
- inflammatory myofibroblastic tumour -> ALK (~50%)        - NSCLC -> EGFR / ALK / KRAS
- unqualified GIST -> KIT (~75%; wild-type GIST is itself a recognised entity, which is the proof it is not
  definitional — an entity cannot be defined by something a named subtype of it lacks)

THE THREE SHAPES THAT DO FIRE

1. THE ENTITY IS DEFINED BY THE LESION. The diagnosis requires it, so naming the disease names the alteration.
   chronic myeloid leukaemia -> BCR::ABL1 fusion          acute promyelocytic leukaemia -> PML::RARA fusion
   Ewing sarcoma -> EWSR1 fusion                           synovial sarcoma -> SS18::SSX fusion
   dermatofibrosarcoma protuberans -> COL1A1::PDGFB fusion secretory carcinoma -> ETV6::NTRK3 fusion
   alveolar rhabdomyosarcoma -> PAX3::FOXO1 or PAX7::FOXO1 mantle cell lymphoma -> CCND1 rearrangement, t(11;14)
   NUT carcinoma -> NUTM1 fusion                           myxoid liposarcoma -> FUS::DDIT3 fusion
   solitary fibrous tumour -> NAB2::STAT6 fusion           alveolar soft part sarcoma -> ASPSCR1::TFE3 fusion
   epithelioid haemangioendothelioma -> WWTR1::CAMTA1 fusion
   von Hippel-Lindau disease-associated tumour -> VHL alteration
   neurofibromatosis type 1 -> NF1 alteration              tuberous sclerosis complex -> TSC1 or TSC2 alteration
   Philadelphia-chromosome-positive leukaemia -> BCR::ABL1 fusion

2. A QUALIFIER ON THE DISEASE STATES A GENETIC STATUS. The entity alone may imply nothing; the qualifier is
   explicit, so it fires — in EITHER polarity.
   "wild-type GIST" / "KIT/PDGFRA wild-type GIST" -> NOT(KIT mutation) AND NOT(PDGFRA mutation)
     ⚠ The wild-type GIST entity is defined by KIT and **PDGFRA** — never PDGFRB. Getting this wrong is a
       documented error in another curation of these same trials; do not reproduce it.
   "glioblastoma, IDH-wildtype" -> NOT(IDH1 mutation) AND NOT(IDH2 mutation)
   "astrocytoma, IDH-mutant" -> IDH1 or IDH2 mutation
   "diffuse midline glioma, H3 K27-altered" -> H3 K27M alteration
   "RAS-wildtype colorectal cancer" -> NOT(KRAS mutation) AND NOT(NRAS mutation)

3. THE WORDING NAMES THE GENE OUTRIGHT. "EGFR-mutant NSCLC", "BRAF V600E-mutant melanoma",
   "BRCA1/2-mutated breast cancer" -> restate the named alteration. This is not really an inference, but the
   genetics are sitting in the cancer-type field where the gene-alteration column will not see them, so recover
   them. Restate ONLY what is named — never expand "EGFR-mutant" into the sensitising set.

APPLY THE TEST TO EVERY DISEASE THE VALUE NAMES, INCLUDING INSIDE `NOT(...)` — BUT THE TWO POLARITIES USE
DIFFERENT TESTS, AND THE EXCLUSION TEST IS THE STRICTER ONE.

A value often reads "<entity> AND NOT(<other entity>) AND NOT(<another>)". Take each named entity in turn.

INCLUDED entity — the test is NECESSITY. Does the diagnosis REQUIRE the alteration? If yes, a patient enrolled
under that diagnosis has it, so contribute the POSITIVE alteration.
    "mantle cell lymphoma"  -> CCND1 rearrangement          (the diagnosis requires t(11;14))

EXCLUDED entity — the test is SUFFICIENCY, which is strictly stronger. `NOT(disease)` entails `NOT(alteration)`
ONLY IF the alteration is PATHOGNOMONIC: essentially nothing OTHER than that disease carries it. Ask:
    "Could a patient have this alteration and NOT have the excluded disease?"
    Yes, even occasionally -> contribute NOTHING. Negating it would exclude patients the trial accepts.
    - "NOT(Burkitt lymphoma)" contributes NOTHING. Burkitt REQUIRES a MYC rearrangement, but MYC rearrangements
      also occur in DLBCL and high-grade B-cell lymphoma, so `NOT(MYC rearrangement)` would throw out
      MYC-rearranged DLBCL patients the trial is recruiting. Necessary is not sufficient.
    - "NOT(Acute Promyelocytic Leukaemia)" DOES contribute `NOT(PML::RARA fusion)`: that fusion is pathognomonic
      for APL and occurs in nothing else.
    - "NOT(myeloid leukaemia of Down syndrome)" contributes NOTHING — constitutional trisomy 21 is common in
      people who do not have that leukaemia (and is a constitutional karyotype, not a somatic gene alteration).
    - "NOT(clear cell renal cell carcinoma)" contributes NOTHING — ccRCC is not defined by VHL at all.
  This asymmetry is not pedantry. Dropping a criterion from an INCLUSION merely widens the pool, and a clinician
  reviewing the match catches it. An over-EXCLUSION silently removes a patient who qualifies, and nothing
  downstream ever recovers them.
  A CONJUNCTION being excluded is not the same as excluding its parts: "NOT(high-grade B-cell lymphoma with MYC
  AND BCL2 rearrangements)" does not exclude MYC rearrangement, nor BCL2 rearrangement, nor both separately.
  Contribute nothing unless the whole conjunction is itself pathognomonic.

Combine the surviving contributions with AND, in the order they appear. Entities that contribute nothing are
simply omitted; if NOTHING in the value contributes, the answer is "".

OUT OF SCOPE — return "" even though the wording is genetic-adjacent
- IMMUNOHISTOCHEMICAL RECEPTOR STATUS: "triple-negative breast cancer", "ER-positive", "HER2-positive",
  "PD-L1 high". These are protein-expression readouts and belong to the biomarker column, not to a gene
  alteration. (A gene-level statement such as "HER2-amplified" IS in scope — amplification is a copy-number event.)
- FUNCTIONAL SIGNATURES: "MSI-high", "dMMR", "TMB-high", "HRD". These are the molecular-signature column.
- STAGE / GRADE / SITE / TREATMENT-STATE QUALIFIERS: "advanced", "metastatic", "recurrent", "refractory",
  "unresectable", "platinum-resistant". They carry no genetics whatever.
- A SYNDROME NAMED ONLY AS FAMILY HISTORY or as a risk context rather than as the tumour's own diagnosis.

HOW TO WRITE THE ANSWER
Phrase it as a trial would phrase a gene-alteration criterion, in plain clinical English, so it reads like the
other values in our gene-alteration column: "VHL alteration", "BCR::ABL1 fusion",
"NOT(KIT mutation) AND NOT(PDGFRA mutation)", "IDH1 or IDH2 mutation". Use inline `NOT(...)` for exclusions.
Name real HGNC gene symbols. Do not write finding-model syntax — a later stage does that conversion.
"""

_INFERENCE_RULES = """\
You are given ONE cancer-type criterion, exactly as it was interpreted from a clinical trial's eligibility text.
Decide whether the DISEASE ITSELF entails a specific genetic alteration, and if so, state it.

Return two fields:
- `derived_alteration` — the entailed alteration, or "" when nothing is entailed. "" IS THE NORMAL ANSWER; the
  large majority of cancer-type values entail nothing at all, and returning "" for them is a correct, complete
  answer, not a failure to try.
- `basis` — one short clause naming WHY it is definitional (the diagnostic criterion, the defining fusion, or the
  qualifier that states it). Leave "" when `derived_alteration` is "". This is read by a human reviewer, so it
  must be specific: "WHO requires BCR::ABL1 for the diagnosis" is useful; "well known association" is not, and is
  itself evidence that the value should have been "".

Judge the value AS WRITTEN. Do not consult what other trials say, do not consider what the trial is testing, and
do not reason about which patients the sponsor probably wants. This is a statement about a DISEASE ENTITY.

WORKED EXAMPLES (value -> derived_alteration)
- "von Hippel-Lindau (VHL) disease associated localized tumors"
    -> "VHL alteration"   [basis: VHL disease is defined by a germline VHL alteration]
- "advanced wild-type gastrointestinal stromal tumor (wt GIST)"
    -> "NOT(KIT mutation) AND NOT(PDGFRA mutation)"   [basis: the wild-type qualifier states KIT/PDGFRA status]
- "chronic myeloid leukaemia in chronic phase"
    -> "BCR::ABL1 fusion"   [basis: the diagnosis requires the Philadelphia translocation]
- "newly diagnosed acute promyelocytic leukaemia"
    -> "PML::RARA fusion"   [basis: APL is defined by the t(15;17) PML::RARA fusion]
- "metastatic EGFR-mutant non-small cell lung cancer"
    -> "EGFR mutation"   [basis: the wording names the alteration]
- "glioblastoma, IDH-wildtype, grade 4"
    -> "NOT(IDH1 mutation) AND NOT(IDH2 mutation)"   [basis: the IDH-wildtype qualifier states it]
- "advanced clear cell renal cell carcinoma"                       -> ""   (VHL is ~90%, not definitional)
- "metastatic castration-resistant prostate cancer"                -> ""
- "high-grade serous ovarian carcinoma"                            -> ""   (TP53 ~96%, still not definitional)
- "triple-negative breast cancer"                                  -> ""   (IHC receptor status, not a gene alteration)
- "MSI-high colorectal cancer"                                     -> ""   (a molecular signature, not a gene alteration)
- "advanced or metastatic solid tumours"                           -> ""
- "relapsed/refractory multiple myeloma"                           -> ""
- "unresectable gastrointestinal stromal tumour"                   -> ""   (unqualified GIST entails nothing)
- "acute myeloid leukaemia (either de novo or secondary) AND NOT(Acute Promyelocytic Leukaemia) AND NOT(Myeloid
   Leukaemia of Down Syndrome)"
    -> "NOT(PML::RARA fusion)"
    [basis: PML::RARA is pathognomonic for the excluded APL; trisomy 21 is not pathognomonic for ML-DS and is
     not a somatic gene alteration, so it contributes nothing]
- "B cell non-Hodgkin lymphoma AND NOT(Burkitt lymphoma)"          -> ""   (MYC rearrangement is necessary for
   Burkitt but not sufficient — it also occurs in DLBCL, so negating it would exclude eligible patients)
"""

_REVIEWER_RULES = """\
You audit a proposed disease-derived alteration. You are given the cancer-type value and the proposal
(`derived_alteration` + `basis`). Set faithful=true only if ALL of the following hold.

1. THE BAR WAS APPLIED. A non-empty answer passes the withdrawal test — absent the alteration, the stated
   diagnosis would not stand. If the proposal rests on frequency, typicality or "the canonical driver", it is a
   FAULT and the correct answer is "".
2. `""` IS NOT A FAULT unless one of the three firing shapes plainly applies. Do NOT push the writer to produce
   content: an empty answer for an ordinary cancer type is the expected outcome, and demanding an inference here
   is the most damaging error you can make. Flag an empty answer ONLY where the entity is genuinely defined by a
   lesion, a qualifier explicitly states a genetic status, or the wording names a gene outright.
3. THE ALTERATION IS THE RIGHT ONE, with the right genes. Check the gene symbols specifically — wild-type GIST is
   KIT and PDGFRA, never PDGFRB.
4. POLARITY IS RIGHT. A "wild-type" / "-negative" / "-wildtype" qualifier yields `NOT(...)`; a "-mutant" /
   "-altered" / "-positive" qualifier yields the positive form.
5. NOTHING WAS INVENTED BEYOND THE WORDING. A named specific alteration is restated, not expanded into a class
   ("EGFR-mutant" stays "EGFR mutation" and does NOT become the sensitising set).
6. SCOPE WAS RESPECTED. IHC receptor status, functional signatures (MSI/TMB/HRD/dMMR) and stage/grade/treatment
   qualifiers all yield "". A proposal that emits one of these is a FAULT.
7. THE TWO POLARITIES WERE JUDGED BY THEIR OWN TESTS. An INCLUDED entity contributes when the diagnosis REQUIRES
   the alteration. An EXCLUDED entity contributes ONLY when the alteration is PATHOGNOMONIC — if a patient could
   carry it without having the excluded disease, emitting `NOT(...)` is a FAULT, because it removes patients the
   trial accepts. `NOT(Burkitt lymphoma)` -> `NOT(MYC rearrangement)` is exactly this fault: necessary for
   Burkitt, but MYC rearrangements occur in DLBCL too. `NOT(APL)` -> `NOT(PML::RARA fusion)` is correct.
   Excluding a CONJUNCTION never licenses excluding its individual conjuncts.
8. `basis` IS SPECIFIC AND TRUE where the answer is non-empty — it names the diagnostic criterion or the
   qualifier. A vague basis is itself grounds to fail the proposal.

When you fail a proposal, say concretely what the value should be instead.
"""


def build_disease_inference_agent(client: LlmClient, *, model: str | None = None) -> Agent[DiseaseDerivedAlteration]:
    return Agent(
        name="disease_derived_alteration_inferrer",
        instructions=_INFERENCE_RULES + "\n" + DEFINITIONAL_STANDARD,
        output_schema=DiseaseDerivedAlteration,
        client=client,
        model=model,
    )


def build_disease_inference_reviewer(client: LlmClient, *, model: str | None = None) -> Agent[ReviewVerdict]:
    return Agent(
        name="disease_derived_alteration_reviewer",
        instructions=_REVIEWER_RULES + "\n" + DEFINITIONAL_STANDARD,
        output_schema=ReviewVerdict,
        client=client,
        model=model,
    )


# --------------------------------------------------------------------------- #
# COMPONENT 1 — the two judgement calls. Everything else in the comparison is deterministic.
# --------------------------------------------------------------------------- #
_ALIGNER_RULES = """\
You align two lists of eligibility criteria for the SAME clinical trial, written by two different curation teams
for one column. Neither list uses a controlled vocabulary, so equivalence is a judgement — that is why you and not
a set operation are doing this.

WHAT THE TWO SIDES LOOK LIKE
- POTTR's entries are CONDENSED labels: a drug name, a drug CLASS, or a short biomarker token, each carrying an
  explicit polarity ("prior X required" vs "must NOT have had prior X") and sometimes a SOFT marker.
- Our entries are near-verbatim protocol prose: long sentences with washout windows, exceptions and parentheticals.

So the SHAPES will never match. Judge MEANING:
- A drug CLASS on POTTR's side matches our sentence when our sentence names that class, or names a drug that
  belongs to it ("anti-PD-1 monoclonal antibody" matches "prior pembrolizumab or nivolumab").
- One of our long sentences may satisfy SEVERAL POTTR terms; emit one aligned pair per POTTR term, reusing our
  value. A POTTR term may equally match nothing.
- POLARITY MUST AGREE. "must NOT have had prior anthracycline" does NOT match "must have received prior
  anthracycline". A polarity mismatch is NOT an alignment — put both entries in the unmatched lists.
- Detail we carry that POTTR omits (a washout window, "within 4 weeks", an exception clause) does NOT block an
  alignment. POTTR is deliberately coarser; only a difference in WHAT is required blocks it.
- Conversely, a POTTR term naming a specific drug does not match our generic sentence about a whole modality
  unless the drug plainly falls inside it.

Return `aligned` (one entry per matched POTTR term), `pottr_only` and `ours_only`. Copy strings VERBATIM from the
lists you were given — do not reword, renumber or normalise them, or the caller cannot join your answer back.
Every input string must appear exactly once across the three outputs.
"""

_MISTAKE_RULES = """\
Two independent curation teams read the SAME clinical trial and recorded different eligibility criteria. You are
given the trial's VERBATIM REGISTRY TEXT and one difference. Decide who is wrong — against the SOURCE, never
against the other team.

NEITHER SIDE IS GROUND TRUTH. POTTR is a peer curation with known defects, and ours has its own. The registry text
is the only authority here. If your reasoning does not cite the source wording, the honest verdict is
'undecidable'.

⚠ THE MOST IMPORTANT DISTINCTION HERE IS **OMISSION vs ERROR**, and getting it wrong makes the whole output
useless. A side that RECORDS NOTHING about a criterion has not made a factual claim, so it cannot be wrong about
it — it is less complete. A side that records something the source CONTRADICTS has made a factual error. These
are different findings and they get different verdicts. POTTR's curation is deliberately coarse, especially for
prior therapy, so most differences you see will be omissions.

Choose exactly one verdict:
- `pottr_omission`  the source supports our value, and POTTR simply does not record that criterion. NOT an error.
- `ours_omission`   the mirror case: the source supports POTTR's value and we do not record it. NOT an error.
- `pottr_wrong`     POTTR records something the source CONTRADICTS (wrong gene, wrong cancer type, inverted
                    polarity, a criterion the source never states, or a narrowing the source does not support).
- `ours_wrong`      we record something the source CONTRADICTS.
- `both_wrong`      the source supports neither of the two recorded values.
- `neither_wrong`   BOTH are defensible readings of the source, OR the two sides record the same fact at
                    different granularity or in different columns.
- `undecidable`     the source text supplied does not settle it.

THINGS THAT ARE NOT MISTAKES, and must return `neither_wrong`:
- A DIFFERENCE OF GRANULARITY. "Colorectal cancer" vs "Colorectal adenocarcinoma"; "EGFR mutation" vs the
  enumerated sensitising set. Both are faithful; one is simply finer.
- A CRITERION RECORDED IN A DIFFERENT COLUMN. The same fact filed under biomarker by one side and gene
  alteration by the other is a filing difference, not an error.
- A SOFT criterion on POTTR's side that we treat as hard, or vice versa. POTTR's `*` marks a criterion its
  matcher assumes when unknown; that is a modelling choice, not a factual claim.
- AN OMISSION OF SOMETHING GENUINELY MINOR — a washout window, an assay method, an administrative clause.

A real mistake is a FACTUAL error about the trial: the wrong gene, an inverted polarity (an exclusion recorded as
an inclusion), a cancer type the trial does not enrol, or a criterion asserted that the source never states.
"""


def build_freetext_aligner(client: LlmClient, *, model: str | None = None) -> Agent[FreeTextAlignment]:
    return Agent(
        name="pottr_freetext_aligner",
        instructions=_ALIGNER_RULES,
        output_schema=FreeTextAlignment,
        client=client,
        model=model,
    )


def build_mistake_judge(client: LlmClient, *, model: str | None = None) -> Agent[MistakeVerdict]:
    return Agent(
        name="pottr_mistake_judge",
        instructions=_MISTAKE_RULES,
        output_schema=MistakeVerdict,
        client=client,
        model=model,
    )


LIVE_AGENT_BUILDERS = [
    build_disease_inference_agent,
    build_disease_inference_reviewer,
    build_freetext_aligner,
    build_mistake_judge,
]
