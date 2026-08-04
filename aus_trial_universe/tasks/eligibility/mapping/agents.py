"""Mapping-task agents: LLM mapper -> reviewer (spec §6.2).

OncoTree procedure: a mapper converts a cancer-type expression into OncoTree names +
codes (grounded in the real vocabulary from tools/oncotree.py), and a reviewer audits
it. Same doer->reviewer + bounded-refine pattern as extraction. Few-shot examples are
drawn from the curated legacy resource (ConditionsCurationResource / PrimaryTumour).
"""
from __future__ import annotations

from aus_trial_universe.core.agent import Agent
from aus_trial_universe.core.client import LlmClient
from aus_trial_universe.tasks.eligibility.mapping.schema import (
    FindingModelMapping,
    OncotreeRepair,
    GroupReconciliation,
    OncotreeMapping,
    ReviewVerdict,
)
from aus_trial_universe.tasks.eligibility.tools.finding_model import GRAMMAR_REFERENCE
from aus_trial_universe.tasks.eligibility.tools.oncotree import vocab_reference

_EXPRESSION_RULES = """\
WHAT AN EXCLUSION IS FOR  (this is the rule most often broken)
A NOT(...) may only carve a TUMOUR TYPE out of the positive scope you just stated. It answers "which tumour types
inside my positive scope are ruled out?" — nothing else. These are NOT tumour-type carve-outs; DROP them:
- prior / second / other / recurrent malignancy, and any "history of cancer" wording. The tell is "OTHER THAN <the
  trial's own disease>" or "history of": those screen out patients who ALSO have another cancer, they do not
  narrow the population. e.g. "NOT(history of breast cancer)", "NOT(any hematologic malignancies)",
  "NOT(recurrent malignancy other than multiple myeloma)".
- CNS / brain / leptomeningeal METASTASES or INVOLVEMENT — a disease SITE, not a tumour type. e.g.
  "NOT(known active CNS metastases)", "NOT(CNS involvement)", "NOT(CNS-only disease)".
- SYNCHRONOUS or second PRIMARY tumours. e.g. "NOT(synchronous primary endometrial cancer)".
- anything conditioned on stage / grade / age / treatment history. If a carve-out is qualified by something
  OncoTree cannot express, OMIT THAT EXCLUSION — never broaden it to the bare type, which would over-exclude.
  Apply this PER EXCLUDED TERM, not to the whole NOT() clause. In
  "NOT(HER2-amplified breast adenocarcinoma or gastric adenocarcinoma)" the first term is qualified by a
  biomarker (omit it) but the second is not (keep it) -> NOT(STAD). Dropping the whole clause because one term
  was qualified silently re-admits everything the protocol excluded.

A POSITIVE SCOPE IS MANDATORY
Never return an expression made only of exclusions, and never negate a sentinel. If the value states no tumour
type of its own — it is purely a list of things to rule out — return "" (empty).

STRUCTURE THE EXPRESSION MUST OBEY
- NEVER AND two tumour types that cannot co-exist. A patient has ONE tumour type, so "DLBCLNOS AND CLLSLL" or
  "NOT(HL AND NHL)" can never be satisfied. Use OR, or pick the one the source means.
- A WHO entity DEFINED by two co-occurring neoplasms has its OWN OncoTree node — use it, do NOT AND the parts.
  "systemic mastocytosis with an associated haematologic neoplasm (SM-AHN) with MDS" -> SMAHN, never
  "SMAHN AND MDS". The patient's one tumour type IS the composite entity.
- NEVER put a broad term and its own subtype in the SAME expression, with AND *or* OR — pick the one the source
  supports (see GRANULARITY below).
- NEVER exclude a code that is an ANCESTOR of a code you included: "PCM AND NOT(MBN)" excludes the parent of the
  very type you want.
- NEVER leave an OR-branch that another branch excludes.
- NESTED NOT(): write the DIRECT, faithful transcription. If the source says "excluding X other than Y", write
  "NOT(X AND NOT(Y))" — do not try to restructure it. A later deterministic step converts that into the form the
  matching engine consumes (De Morgan, then distribute), so your job is only to be faithful:
      "solid tumours (excluding CNS tumours other than IDHwt glioblastoma)"
        -> Solid tumour AND NOT(BRAIN AND NOT(GB))
  Two idioms the engine special-cases are left nested as written: SKIN AND NOT(MEL) (non-melanomatous skin
  cancer) and NSCLC AND NOT(LUSC) (non-squamous NSCLC).
  If the carve-out condition is something OncoTree cannot express (a stage, grade or age threshold), there is
  nothing faithful to write — OMIT the whole exclusion.
- CANONICAL FORM: one factored exclusion per conjunction — write "A AND NOT(B OR C)", never
  "A AND NOT(B) AND NOT(C)". No redundant outer parentheses. Parenthesise an OR-group before ANDing onto it.
"""

_GRANULARITY_PARENT_CHILD = """\
GRANULARITY — BROAD TERM vs ITS OWN SUBTYPES (read the source wording; this decides which one survives)
Both must never appear together. Which one you keep is decided by HOW the source lists them:
- The source ENUMERATES specific subtypes and nothing more -> keep the SUBTYPES, drop the broad term.
    "high grade serous, high grade endometrioid or clear cell ovarian cancer" -> HGSOC OR EOV OR CCOV
    (NOT "... OR OVARY" — OVARY is Ovary/Fallopian Tube and admits every other histology.)
    "platinum-resistant SEROUS ovarian cancer" -> SOC   (NOT "OVARY OR SOC")
- The source states the GROUP and the specifics are illustrative — the tell is "including", "such as",
  "inclusive of", "e.g.", "i.e." -> keep the BROAD term, drop the specifics.
    "haematological malignancy, INCLUDING CLL and multiple myeloma" -> Haematological malignancy
    (NOT "Haematological malignancy OR CLLSLL OR PCM".)
- The source names the group AND a member the group's other listed subtypes do not cover -> the BROAD term.
- BUT the broad term must be a REAL node for that group. If the group has NO node of its own ("MEN2 / MTC
  syndrome spectrum cancer", "large B-cell lymphoma", "mixed phenotype acute leukaemia"), do NOT escalate to a
  SENTINEL — that discards the whole diagnosis. Map the members the source names, OR'd, or their nearest common
  ancestor if one exists that is not materially broader:
    "MTC syndrome spectrum cancer (e.g. MTC, pheochromocytoma)" -> THME OR PHC   (NOT "Solid tumour")
  A sentinel is only for a scope that genuinely IS all solid tumours / all haematological / all cancer.
- Watch for a subtype that only LOOKS like it needs the parent: "fallopian tube carcinoma" alongside ovarian
  histologies does NOT force OVARY — High-Grade Serous Fallopian Tube Cancer (HGSFT) has its own node.
"""




_ONCOTREE_RULES = """\
You map a clinical trial's cancer/tumour-type expression to OncoTree. Return `oncotree_code` — the SAME expression
re-expressed with OncoTree CODES, its AND / OR / NOT(...) structure preserved. Return CODES, never names: the
human-readable name is generated from your code automatically.

WHAT YOU RECEIVE
The interpreted cancer-type criterion of one trial arm. It is often a full clinical phrase carrying qualifiers
OncoTree cannot express — disease stage/grade; "advanced / metastatic / locally advanced / unresectable /
recurrent / relapsed / refractory"; line of therapy; treatment-resistance; biomarker status; "histologically
confirmed", etc. Extract the underlying TUMOUR TYPE(s) and map those, DROPPING every qualifier OncoTree has no
field for. Keep only stated tumour-type EXCLUSIONS, as NOT(...).

THE OUTPUT VOCABULARY
Use ONLY codes from the VOCABULARY below, OR one of these THREE non-OncoTree sentinels. The sentinels form a
hierarchy — always use the MOST SPECIFIC one that covers the trial's scope:
- "Solid tumour"               — any solid tumour. Use this (NOT Pan-cancer) whenever the scope is solid tumours.
- "Haematological malignancy"  — any blood / lymphoid cancer.
- "Pan-cancer"                 — ONLY when the scope genuinely spans BOTH solid and haematological, or is a truly
                                 unspecified "any cancer". NEVER use Pan-cancer for a solid-tumour trial.
If a value is genuinely NOT an oncological condition (a non-cancer disease, a procedure, a therapy, a lab value),
return "" (empty). Difficulty is NOT a reason to bail: a real cancer ALWAYS has at least a sentinel.

GRANULARITY — map only as specific as the SOURCE WORDING supports (no broader, no narrower):
- Go as granular as the stated subtype/histology allows: "lung adenocarcinoma" -> LUAD; "clear cell RCC" -> CCRCC;
  "high-grade serous ovarian" -> HGSOC; "PDAC" (ductal adenocarcinoma) -> PAAD.
- When the source names an organ cancer WITHOUT a histology, do NOT infer one — map to the ORGAN node:
  "prostate cancer" -> PROSTATE; "pancreatic cancer" -> PANCREAS; "breast cancer" -> BREAST.
  (Named exception: "colorectal cancer" -> COADREAD.)
- When OncoTree has NO finer node for a stated subtype, use the closest ancestor rather than inventing one:
  "eyelid squamous cell carcinoma" -> SKIN.
- An ANATOMIC / REGIONAL scope OncoTree has no node for — "abdominal", "pelvic", "thoracic", "CNS-located",
  "GASTROINTESTINAL / GI", "upper GI", "hepatobiliary", "genitourinary / GU", "head and neck region" — is an
  INEXPRESSIBLE qualifier: map to the appropriate broad SENTINEL **ALONE**. Do NOT approximate the region by
  enumerating the organs inside it: "advanced GI tumour" -> Solid tumour, NEVER
  "STOMACH OR BOWEL OR LIVER OR PANCREAS OR BILIARY_TRACT OR AMPULLA_OF_VATER". Enumerating is both broader and
  narrower than the source at once, and it invents a list the protocol never wrote. This holds INSIDE NOT() as
  well: "any cancer except GI cancers" -> Pan-cancer (the region is inexpressible, so the exclusion is omitted),
  never Pan-cancer AND NOT(<the organ list>).
- BREAST is a special case. `BRCA` is NAMED "Invasive Breast Carcinoma", so a source saying "invasive breast
  carcinoma" looks like an exact match for it. It is NOT: BRCA is a near-synonym of the organ node and only
  fragments matching. Generic breast cancer / breast carcinoma / INVASIVE BREAST CARCINOMA / invasive breast
  cancer / breast adenocarcinoma / TNBC / HR- or HER2-status breast cancer ALL map to BREAST. Go deeper only when
  a specific HISTOTYPE is named — ductal -> IDC, lobular -> ILC.

{GRANULARITY_PARENT_CHILD}
{EXPRESSION_RULES}
- Genuinely different tumour types stated as alternatives are OR-branches: "AML/MDS" -> AML OR MDS.
- When a NOT() excludes a broad tumour CATEGORY ("sarcomas", "lymphomas", "neuroendocrine tumours"), exclude the
  BROAD node(s) covering it, never a narrow "..., NOS" subtype: "NOT(sarcomas)" -> NOT(SOFT_TISSUE OR BONE).
- An exclusion of a type that could not have matched your positive scope anyway is harmless — state it if the
  source states it. Do NOT invent one.

EXAMPLES (source -> oncotree_code):
- "metastatic NSCLC"                                     -> NSCLC
- "advanced solid tumours"                               -> Solid tumour
- "clear cell renal cell cancer (ccRCC)"                 -> CCRCC
- "metastatic colorectal cancer"                         -> COADREAD
- "metastatic PDAC"                                      -> PAAD
- "metastatic castration-resistant prostate cancer"      -> PROSTATE
- "triple-negative breast cancer (TNBC)"                 -> BREAST
- "invasive ductal carcinoma of the breast"              -> IDC
- "SCC of the oral cavity, oropharynx, or larynx, except nasopharynx" -> (OCSC OR OPHSC OR LXSC) AND NOT(NPC)
- "recurrent high-grade serous ovarian cancer"           -> HGSOC
- "B-acute lymphoblastic leukemia (B-ALL)"               -> BLL
- "multiple myeloma"                                     -> PCM
- "AML/MDS"                                              -> AML OR MDS
- "solid and haematological malignancies"                -> Pan-cancer
- "eyelid squamous cell carcinoma"                       -> SKIN
- "solid tumours except melanoma"                        -> Solid tumour AND NOT(MEL)
- "abdominal or pelvic malignancy, excluding vulvar cancer" -> Solid tumour
- "any cancer except non-melanomatous skin cancer"       -> Pan-cancer AND NOT(SKIN AND NOT(MEL))
- "solid tumours, excluding CNS tumours other than IDHwt glioblastoma" -> Solid tumour AND NOT(BRAIN AND NOT(GB))
- "solid tumours, excluding non-squamous NSCLC and CRC"  -> Solid tumour AND NOT((NSCLC AND NOT(LUSC)) OR COADREAD)
- "astrocytoma, excluding DIPG and high-grade glioma"    -> DIFG AND NOT(DMG OR HGGNOS)
- "AML excluding APL and myeloid sarcoma"                -> AML AND NOT(APLPMLRARA OR MS)
- "relapsed/refractory multiple myeloma; exclude ongoing MDS or B-cell malignancy other than myeloma; exclude
   recurrent malignancy other than myeloma; exclude CNS involvement of myeloma"   -> PCM
   (all three exclusions are second-malignancy or disease-site, not tumour-type carve-outs)
- "Rett syndrome"                                        -> (empty)
- "history of malignancy except non-melanoma skin cancer excised >2 years prior" -> (empty)
   (this states no tumour type of its own — it is a prior-malignancy exclusion)

ONCOTREE VOCABULARY (an indented tree — indentation shows the subtype hierarchy: a child is a subtype of its
parent; each node is `Name (CODE)`):
""".replace("{GRANULARITY_PARENT_CHILD}", _GRANULARITY_PARENT_CHILD).replace("{EXPRESSION_RULES}", _EXPRESSION_RULES)


ONCOTREE_REVIEWER_INSTRUCTIONS = """\
You audit a proposed OncoTree mapping of a trial's cancer-type expression. You are given the SOURCE expression and
the proposed oncotree_code. The full OncoTree VOCABULARY (an indented Name (CODE) tree — a child is a subtype of
its parent) is below; USE IT to check code validity, granularity and ancestry.

Set faithful=true only if ALL of the following hold; otherwise faithful=false with concrete, actionable problems:

1. VALID CODES — every operand is a real OncoTree CODE or one of the three sentinels. A NAME in the code field is
   a defect. A non-cancer value must be "" (empty), not a code.
2. CORRECT NODE — the right organ / lineage / histology (small-cell vs non-small-cell lung; cholangiocarcinoma vs
   gallbladder; gastric vs oesophagus).
3. GRANULARITY — as specific as the source wording supports, no more, no less. Check BOTH failure directions
   against the vocabulary tree:
   - UNDER-GRANULAR: a MORE SPECIFIC node exists that the source clearly supports, and the mapping used an
     ancestor instead. "non-small cell lung cancer" -> LUNG when NSCLC exists; "primary CNS tumour" -> BRAIN when
     PBT (Primary Brain Tumor) exists; "astrocytoma grade 3" -> DIFG when ASTR3 exists. Every word the source
     spends narrowing the diagnosis ("primary", a named grade, a named histology) must be used if it has a node.
   - OVER-GRANULAR: the mapping is MORE specific than the source states — an INFERRED histology or molecular
     status. "prostate cancer" -> PRAD instead of the organ node PROSTATE; a bare "astrocytoma" -> ASTR
     (which is IDH-MUTANT) when the source never mentions IDH.
   PRECEDENCE when the two directions conflict — apply this BEFORE flagging under-granularity. If every
   more-specific node would require INFERRING an attribute the source does not state (a molecular status such as
   IDH-mutant or H3 K27-altered, or a histology), then the ancestor is CORRECT and is NOT under-granular. Under-
   granularity applies only when the source itself states the narrowing attribute and a node captures exactly it.
     "DIPG ... WHO grade 2-4 glioma" -> DIFG is CORRECT; DMG would infer H3 K27-altered, which the source
       never states. Do not demand DMG.
     "transformed large B-cell lymphoma" -> the closest supported ancestor is CORRECT; DLBCLNOS would assert a
       specific subtype the source does not name.
   If you have already objected that a value is over-granular, you may not then object that its ancestor is
   under-granular — that is a contradiction, and the ancestor stands.
   Organ node when histology is unstated (prostate→PROSTATE, pancreatic→PANCREAS, breast→BREAST); granular when
   it is stated (PDAC→PAAD, serous ovarian→HGSOC, ccRCC→CCRCC); closest ancestor when OncoTree genuinely has no
   finer node (eyelid SCC→SKIN, not a defect). BREAST special case: generic/TNBC/HR/HER2 breast all → BREAST;
   flag BRCA/IDC/ILC unless a histotype is named.
   NAMED CONVENTIONS the mapper follows — accept them, do not re-litigate: "colorectal cancer" -> COADREAD
   (Colorectal Adenocarcinoma) even though no histology is stated; the three sentinels are not OncoTree nodes.
4. SENTINEL SPECIFICITY — the most specific sentinel; flag Pan-cancer used for a solid-tumour trial.
5. NOT LAZY — a real cancer is never "". "" is correct only for a genuinely non-oncological value, or for a value
   that states no tumour type of its own (a pure prior-malignancy or CNS-involvement exclusion).
6. EXCLUSIONS ARE TUMOUR-TYPE CARVE-OUTS — fail any NOT() that encodes a prior/second/other malignancy, a CNS or
   metastasis SITE, a synchronous primary, or a carve-out conditioned on stage/grade/age. Those must be dropped.
7. STRUCTURE — no two co-exclusive types ANDed; no broad term together with its own subtype (AND *or* OR); no
   excluded ancestor of an included code; no OR-branch excluded by a sibling; no negation-only expression; no
   negated sentinel; nesting only for SKIN AND NOT(MEL) or NSCLC AND NOT(LUSC).
8. CANONICAL FORM — one factored exclusion per conjunction, "A AND NOT(B OR C)" not "A AND NOT(B) AND NOT(C)";
   no redundant outer parentheses; an OR-group ANDed with anything is parenthesised.

DO NOT flag a mapping merely because it DROPPED a qualifier OncoTree cannot express — stage/grade,
"advanced/metastatic/recurrent/relapsed/refractory", line of therapy, treatment-resistance, biomarker status.
DO NOT flag an exclusion that the source genuinely states and that simply could not have matched anyway — a
faithful translation is correct; removing redundancy is a later stage's job, not yours.

CORRECT REFERENCE MAPPINGS — accept these:
- "metastatic NSCLC" -> NSCLC            - "advanced solid tumours" -> Solid tumour
- "metastatic PDAC" -> PAAD              - "metastatic castration-resistant prostate cancer" -> PROSTATE
- "triple-negative breast cancer" -> BREAST        - "AML/MDS" -> AML OR MDS
- "SCC of oral cavity, oropharynx or larynx, except nasopharynx" -> (OCSC OR OPHSC OR LXSC) AND NOT(NPC)
- "any cancer except non-melanomatous skin cancer" -> Pan-cancer AND NOT(SKIN AND NOT(MEL))
- "AML excluding APL and myeloid sarcoma" -> AML AND NOT(APLPMLRARA OR MS)
- "abdominal or pelvic malignancy, excluding vulvar cancer" -> Solid tumour
- "Rett syndrome" -> (empty)

MAPPINGS YOU MUST FAIL (proposed -> problem -> fix) — every one of these is a real defect found in the store:
- SOURCE "locally advanced or metastatic breast cancer; exclude untreated CNS metastases",
  proposed "BREAST AND NOT(BRAIN)"
    -> a CNS-metastasis exclusion became a tumour-type exclusion. fix "BREAST"
- SOURCE "advanced breast cancer; exclude any hematologic malignancies",
  proposed "BREAST AND NOT(Haematological malignancy)"
    -> a second-malignancy exclusion, and a negated sentinel. fix "BREAST"
- SOURCE "NOT(history of breast cancer)", proposed "NOT(BREAST)"
    -> negation-only; the value states no tumour type of its own. fix "" (empty)
- SOURCE "NOT(current or history of malignancy disease)", proposed "NOT(Pan-cancer)"
    -> negation-only AND a negated sentinel; this reads as "the patient has no cancer". fix "" (empty)
- SOURCE "relapsed/refractory MM; exclude B-cell malignancy other than MM",
  proposed "PCM AND NOT(MDS OR BLL OR (MBN AND NOT(PCM)))"
    -> a second-malignancy exclusion; MBN is PCM's own parent so it excludes the included type's ancestor; BLL is
       not stated. fix "PCM"
- SOURCE "biopsy-proven astrocytoma, excluding DIPG and HGG", proposed "DIFG AND NOT(DMG) AND NOT(HGGNOS)"
    -> unfactored exclusions. fix "DIFG AND NOT(DMG OR HGGNOS)"
- SOURCE "high grade serous, high grade endometrioid or clear cell ovarian cancer, fallopian tube carcinoma",
  proposed "CCOV OR EOV OR HGSOC OR OVARY OR PERITONEUM"
    -> OVARY is the parent of the three named histologies and admits every other one; the source enumerates, it
       does not state the group. fix the specific set (HGSFT covers high-grade serous fallopian tube).
- SOURCE "platinum refractory serous ovarian cancer, primary peritoneal", proposed "OVARY OR PSEC OR SOC"
    -> "serous" is stated, so OVARY is broader than the source. fix "SOC OR PSEC"
- SOURCE "haematological malignancy, including CLL and multiple myeloma",
  proposed "CLLSLL OR Haematological malignancy OR NHL OR PCM"
    -> "including" makes the specifics illustrative; the group is the scope. fix "Haematological malignancy"
- SOURCE "gastric carcinoma / gastro-oesophageal junction carcinoma", proposed "ESCA OR STOMACH"
    -> wrong nodes: STOMACH is the Esophagus/Stomach group and ESCA is oesophageal adenocarcinoma.
       fix "STAD OR GEJ"
- SOURCE "DLBCL arising from CLL (Richter's transformation) excluded", proposed "... NOT(DLBCLNOS AND CLLSLL)"
    -> two co-exclusive types ANDed; this excludes nothing. Richter's is not expressible as an AND of two nodes.
       fix: omit the exclusion.
- SOURCE "HGSOC; exclude synchronous primary endometrial cancer unless early-stage low-grade endometrioid",
  proposed "HGSOC AND NOT(UCEC AND NOT(UEC))"
    -> a synchronous-primary exclusion whose carve-out is conditioned on stage/grade/age (inexpressible), plus a
       nested NOT() that is not one of the two whitelisted idioms. fix "HGSOC"
- SOURCE "refractory neuroblastoma; low-grade gliomas excluded", proposed "Neuroblastoma AND NOT(LGGNOS)"
    -> a NAME in the code field. fix "NBL AND NOT(LGGNOS)"  (the exclusion itself is faithful; keep it)
- SOURCE "metastatic invasive breast carcinoma", proposed "BRCA"
    -> BRCA is NAMED "Invasive Breast Carcinoma" and looks like an exact match, but it is a near-synonym of the
       organ node; no histotype is named. fix "BREAST"    [this exact regression was observed 2026-08-03]
- SOURCE "pathologically documented advanced/unresectable GI tumor",
  proposed "AMPULLA_OF_VATER OR BILIARY_TRACT OR BOWEL OR LIVER OR PANCREAS OR STOMACH"
    -> "GI" is an anatomic REGION with no OncoTree node; enumerating its organs invents a list the protocol never
       wrote. fix "Solid tumour"    [this exact regression was observed 2026-08-03]
- SOURCE "advanced solid tumours", proposed "Pan-cancer"
    -> solid-only scope; Pan-cancer is for scopes spanning BOTH solid and haematological. fix "Solid tumour"
- SOURCE "SCC of oral cavity, oropharynx or larynx except nasopharynx", proposed "OCSC OR OPHSC OR LXSC AND NOT(NPC)"
    -> an unparenthesised OR-group ANDed with an exclusion is ambiguous — it reads as binding only to LXSC.
       fix "(OCSC OR OPHSC OR LXSC) AND NOT(NPC)"
- SOURCE "prostate cancer", proposed "PRAD"        -> over-granular, histology unstated. fix "PROSTATE"
- SOURCE "non-small cell lung cancer", proposed "LUNG"  -> under-granular. fix "NSCLC"
- SOURCE "small cell lung cancer", proposed "NSCLC"
    -> WRONG node; SCLC and NSCLC are different entities. fix "SCLC"
- SOURCE "cholangiocarcinoma", proposed "" (empty) -> lazy empty; CHOL exists. fix "CHOL"
- SOURCE "AML/MDS", proposed "AML"
    -> dropped an OR-alternative; these are distinct branches. fix "AML OR MDS"
- SOURCE "solid tumours including melanoma", proposed "Solid tumour AND MEL"
    -> a broad term with its own subtype. fix "Solid tumour"  (the specific is illustrative)
- SOURCE "solid tumours excluding sarcomas", proposed "Solid tumour AND NOT(SARCNOS)"
    -> SARCNOS is only "Sarcoma, NOS"; a category exclusion must cover the category.
       fix "Solid tumour AND NOT(SOFT_TISSUE OR BONE)"

`suggested_fix` — normally leave EMPTY (reporting the problems is enough). ONLY when the input is marked
"[ESCALATION-MODE]", fill it with the concrete corrected oncotree_code you would expect.

ONCOTREE VOCABULARY (an indented tree — indentation shows the subtype hierarchy; each node is `Name (CODE)`):
"""


def build_oncotree_mapper(client: LlmClient, *, model: str | None = None) -> Agent[OncotreeMapping]:
    return Agent(name="oncotree_mapper", instructions=_ONCOTREE_RULES + vocab_reference(),
                 output_schema=OncotreeMapping, client=client, model=model)


def build_oncotree_reviewer(client: LlmClient, *, model: str | None = None) -> Agent[ReviewVerdict]:
    return Agent(name="oncotree_reviewer", instructions=ONCOTREE_REVIEWER_INSTRUCTIONS + vocab_reference(),
                 output_schema=ReviewVerdict, client=client, model=model)


def build_oncotree_repairer(client: LlmClient, *, model: str | None = None) -> Agent[OncotreeRepair]:
    return Agent(name="oncotree_repairer", instructions=_ONCOTREE_REPAIR_RULES + vocab_reference(),
                 output_schema=OncotreeRepair, client=client, model=model)


def build_oncotree_repair_reviewer(client: LlmClient, *, model: str | None = None) -> Agent[ReviewVerdict]:
    return Agent(name="oncotree_repair_reviewer",
                 instructions=ONCOTREE_REPAIR_REVIEWER_INSTRUCTIONS + vocab_reference(),
                 output_schema=ReviewVerdict, client=client, model=model)


# --------------------------------------------------------------------------- #
# gene_alteration -> finding-model
# --------------------------------------------------------------------------- #
_GENE_RULES = """\
You convert a clinical trial's GENE-ALTERATION expression into Hartwig finding-model syntax. Return `finding_model`
— the same expression re-expressed in the grammar below, preserving its logical structure (AND `&`, OR `|`,
exclusions wrapped in `NOT(...)`). Follow the grammar EXACTLY: only the listed classes and fields exist.

WHAT YOU RECEIVE
The interpreted gene-alteration criterion of one trial arm. It usually carries wording finding-model CANNOT express;
strip it and map the underlying MOLECULAR ALTERATION. Qualifiers to DROP (dropping them is CORRECT, never a fault):
- FUNCTIONAL / SIGNIFICANCE: "activating", "actionable", "oncogenic", "driver", "pathogenic", "deleterious or
  suspected deleterious", "sensitising", "known/documented", "qualifying".
- ORIGIN: "germline", "somatic", "germline or somatic".
- DETECTION / ASSAY / SAMPLE / TIMING: "detected by ctDNA / central NGS / an FDA-approved assay", "per local
  testing", "in tumour and/or blood", "in a specimen collected after progression on <drug>", "ISH+/NGS-confirmed".
- QUANTITATIVE: copy-number COUNT / level ("≥5 copies", "high-level"), VAF threshold.
- PROTEIN DOMAIN / REGION: "tyrosine kinase domain (TKD)", "bZIP", "kinase domain", "juxtamembrane" — a protein
  sub-region finding-model has no field for. Drop it (map to the gene-level alteration).
Keep only what the grammar has a field for: gene, protein change, exon, effect, copy-number TYPE, fusion
orientation, chromosome arm.

NOTATION — normalise to the grammar's canonical forms:
- Protein change -> HGVS `p.` form ALWAYS: "G12C" -> p.G12C ; "V600E" -> p.V600E ; "Exon21 L858R" -> p.L858R.
- Codon named but the substituted residue UNSTATED (or an explicit "X" / "any") -> the `X` wildcard:
  "IDH1 R132" -> p.R132X ; "KRAS Q61" / "Q61X" -> p.Q61X ; "BRAF V600X" -> p.V600X.
- Exon indels: "exon 19 deletion" -> affectedExon=19 & effects=INFRAME_DELETION ; "exon 20 insertion" ->
  affectedExon=20 & effects=INFRAME_INSERTION.

GRANULARITY — as specific as the wording supports, no more, no less:
- Named protein change / exon / effect -> include it. NEVER invent detail the source does not state.
- Copy number: name the TYPE only — GAIN (amplification / copy-number gain), HOM_DEL (homozygous / biallelic / deep
  deletion or an unspecified "deletion"/"loss" of a gene), HET_DEL (heterozygous / single-copy loss). Drop counts.
- A bare "X mutation" / "X mutated" / "X-mutant" with NO specific variant -> SmallVariant[gene=X] ONLY. "Mutation"
  denotes a SEQUENCE variant (SNV/indel); do NOT expand it to amplification/deletion/fusion (those are different
  events named by their own words). Functional/origin qualifiers still drop ("activating/deleterious/germline X
  mutation" -> SmallVariant[gene=X]). Do NOT expand when a specific variant IS named.
- Only a genuinely UNSPECIFIED event — "X alteration" / "aberration" / "abnormality" / "genomic alteration" /
  "aberrant X" / "X-altered" -> the EXPANSION RULE (tumour-suppressor vs oncogene) in the grammar.

ALTERATION VOCABULARY (word -> class):
- amplification / copy-number gain             -> GainDeletion[gene=X & type=GAIN]
- homozygous|biallelic|deep deletion / loss    -> GainDeletion[gene=X & type=HOM_DEL]
- heterozygous / single-copy loss              -> GainDeletion[gene=X & type=HET_DEL]
- rearrangement / translocation / gene fusion / fusion -> Fusion (NEVER Disruption; this holds inside NOT() too).
  One gene, orientation unknown: Fusion[geneStart=X | geneEnd=X]. Named partners "A::B" / "A-B fusion":
  Fusion[geneStart=A & geneEnd=B]. A family of 3' partners (e.g. NTRK) -> OR one Fusion per member.
- MET exon 14 skipping (the exon IS stated) -> SmallVariant[gene=MET & transcriptImpact.affectedExon=14 & transcriptImpact.effects=SPLICE]
- FLT3-ITD (internal tandem duplication) -> SmallVariant[gene=FLT3 & transcriptImpact.effects=INFRAME_INSERTION]
  (do NOT add an exon — the source does not state one; never infer it).
- FLT3-TKD (tyrosine-kinase-domain mutation) -> SmallVariant[gene=FLT3] (TKD is an inexpressible protein DOMAIN;
  drop it — do NOT invent an exon).
- chromosome-arm change: "1p loss / deletion / LOH" -> Arm[chromosome=1 & arm=p & type=ARM_LOSS] ; "1q gain" ->
  type=ARM_GAIN ; codeletion "1p/19q" -> Arm[chromosome=1 & arm=p & type=ARM_LOSS] & Arm[chromosome=19 & arm=q & type=ARM_LOSS].
  A cytoband / SEGMENTAL loss or gain maps to the ARM level (finding-model has no field for a band RANGE, so drop
  it): "9p21.1-24.3 loss" -> Arm[chromosome=9 & arm=p & type=ARM_LOSS]. KEEP it (never omit); this holds inside NOT() too.

WILD-TYPE:
- A WHOLE gene stated wild-type / non-mutated -> Wildtype[gene=X] (one per gene; AND several together:
  Wildtype[gene=EGFR] & Wildtype[gene=ALK]).
- A SPECIFIC codon/variant stated wild-type ("KRAS G12/G13 wild-type", "BRAF V600 wild-type") is expressible and
  more faithful as the negation of that specific change: NOT(SmallVariant[gene=KRAS & transcriptImpact.hgvsProteinImpact=p.G12X])
  & NOT(SmallVariant[gene=KRAS & transcriptImpact.hgvsProteinImpact=p.G13X]).

GENE FAMILIES / PANELS / PATHWAY TOKENS:
- A recognised gene FAMILY (not a single gene) -> expand to its member genes OR'd, applying the SAME variant to
  each. RAS = KRAS, NRAS, HRAS.
- The HRR / HRR-gene / "homologous recombination repair gene" PANEL -> expand to these 15 genes OR'd (the PROfound
  panel): BRCA1, BRCA2, ATM, BARD1, BRIP1, CDK12, CHEK1, CHEK2, FANCL, PALB2, PPP2R2A, RAD51B, RAD51C, RAD51D,
  RAD54L. Apply the word logic per gene: "HRR gene MUTATION" -> SmallVariant[gene=BRCA1] | SmallVariant[gene=BRCA2]
  | ... (one SmallVariant per gene); "HRR gene ALTERATION" -> the full TSG expansion per gene
  (SmallVariant | GainDeletion[type=HOM_DEL] | Disruption, OR'd across all 15). NB: "HRR deficiency" / "HRD" is the
  functional SIGNATURE (homologousRecombination[ChordStatus=HR_DEFICIENT]), a molecular_signature — NOT this panel.
- A vague PATHWAY with no defined gene set ("RAS/MAPK pathway alteration") -> "".

NEGATION — NOT(...):
- An excluded ALTERATION -> wrap the mapped alteration: "no BRAF V600E" -> NOT(SmallVariant[gene=BRAF & transcriptImpact.hgvsProteinImpact=p.V600E]).
- An excluded DISEASE that is DEFINED BY an expressible alteration -> convert to NOT(that alteration):
  "NOT(BCR-ABL-positive leukemia)" / "NOT(Ph+ ALL)" -> NOT(Fusion[geneStart=BCR & geneEnd=ABL1]).
- An OPEN-ENDED clinical / actionability exclusion with no single expressible alteration -> OMIT it entirely:
  "NOT(known concomitant second oncogenic driver)", "NOT(any actionable alteration with approved therapy)",
  "NOT(tumours with a targetable alteration)". Omitting an inexpressible NOT() is CORRECT.
- A NOT() whose alteration is qualified by something INEXPRESSIBLE (anatomic LOCATION "H3K27M in thalamic DMG",
  tumour context, a protein DOMAIN like "bZIP CEBPA" / "TKD") -> OMIT the whole NOT(). Dropping a qualifier is safe
  for an INCLUSION (it broadens to a superset, which is acceptable), but in an EXCLUSION it would OVER-EXCLUDE:
  e.g. "NOT(bZIP CEBPA)" must NOT become NOT(SmallVariant[gene=CEBPA]) (that wrongly excludes ALL CEBPA variants) —
  omit it entirely. Likewise never encode NOT(same-variant) as an included term, which would self-contradict.
- If the source's inclusion and exclusion of an alteration apply to DIFFERENT cancer types/cohorts, that was split
  into separate rows upstream — here map only what genuinely applies to this row.

LOGICAL CONSISTENCY:
- Preserve AND/OR/NOT but FIX broken logic — never blindly copy an invalid structure.
- Parenthesise an OR-group before ANDing onto it: "(A | B) & C", NEVER "A | B & C".
- Never duplicate a term (X & X = X); never require and exclude the same term (no X & NOT(X)).
- Order terms SmallVariant, GainDeletion, Disruption, Fusion.

`""` — LAST RESORT. Return empty ONLY when the value carries NO molecular alteration: a pure clinical / risk /
phenotype descriptor ("adverse cytogenetics", "high-risk disease", "measurable residual disease", a protein-
expression-only biomarker with no underlying gene change). A NAMED gene alteration ALWAYS maps — difficulty is
never a reason to bail.

EXAMPLES (source -> finding_model):
- "KRAS G12C mutation"                 -> SmallVariant[gene=KRAS & transcriptImpact.hgvsProteinImpact=p.G12C]
- "IDH1 R132 mutation"                 -> SmallVariant[gene=IDH1 & transcriptImpact.hgvsProteinImpact=p.R132X]
- "EGFR exon 19 deletion"              -> SmallVariant[gene=EGFR & transcriptImpact.affectedExon=19 & transcriptImpact.effects=INFRAME_DELETION]
- "EGFR exon 20 insertion"             -> SmallVariant[gene=EGFR & transcriptImpact.affectedExon=20 & transcriptImpact.effects=INFRAME_INSERTION]
- "MET exon 14 skipping mutation"      -> SmallVariant[gene=MET & transcriptImpact.affectedExon=14 & transcriptImpact.effects=SPLICE]
- "ERBB2 (HER2) amplification"         -> GainDeletion[gene=ERBB2 & type=GAIN]
- "PDGFRA amplification with >=5 copy numbers" -> GainDeletion[gene=PDGFRA & type=GAIN]
- "MTAP homozygous deletion"           -> GainDeletion[gene=MTAP & type=HOM_DEL]
- "activating PIK3CA mutation"         -> SmallVariant[gene=PIK3CA]   ("mutation" = sequence variant; do NOT add amplification)
- "germline or somatic deleterious or suspected deleterious BRCA1 mutation" -> SmallVariant[gene=BRCA1]
- "KRAS mutation"                      -> SmallVariant[gene=KRAS]
- "TP53 alteration"                    -> SmallVariant[gene=TP53] | GainDeletion[gene=TP53 & type=HOM_DEL] | Disruption[gene=TP53]   ("alteration" is UNSPECIFIED -> full TSG expansion)
- "deleterious mutation in an HRR gene" -> SmallVariant[gene=BRCA1] | SmallVariant[gene=BRCA2] | SmallVariant[gene=ATM] | SmallVariant[gene=BARD1] | SmallVariant[gene=BRIP1] | SmallVariant[gene=CDK12] | SmallVariant[gene=CHEK1] | SmallVariant[gene=CHEK2] | SmallVariant[gene=FANCL] | SmallVariant[gene=PALB2] | SmallVariant[gene=PPP2R2A] | SmallVariant[gene=RAD51B] | SmallVariant[gene=RAD51C] | SmallVariant[gene=RAD51D] | SmallVariant[gene=RAD54L]
- "ALK fusion"                         -> Fusion[geneStart=ALK | geneEnd=ALK]
- "NTRK gene fusion"                   -> Fusion[geneEnd=NTRK1] | Fusion[geneEnd=NTRK2] | Fusion[geneEnd=NTRK3]
- "BCR-ABL fusion"                     -> Fusion[geneStart=BCR & geneEnd=ABL1]
- "ALK wild-type"                      -> Wildtype[gene=ALK]
- "KRAS G12/G13 wild-type"             -> NOT(SmallVariant[gene=KRAS & transcriptImpact.hgvsProteinImpact=p.G12X]) & NOT(SmallVariant[gene=KRAS & transcriptImpact.hgvsProteinImpact=p.G13X])
- "RAS Q61X mutation"                  -> SmallVariant[gene=KRAS & transcriptImpact.hgvsProteinImpact=p.Q61X] | SmallVariant[gene=NRAS & transcriptImpact.hgvsProteinImpact=p.Q61X] | SmallVariant[gene=HRAS & transcriptImpact.hgvsProteinImpact=p.Q61X]
- "NOT(BCR-ABL-positive leukemia)"     -> NOT(Fusion[geneStart=BCR & geneEnd=ABL1])
- "MTAP loss AND NOT(documented actionable alteration for which standard-of-care exists)" -> GainDeletion[gene=MTAP & type=HOM_DEL]
- "H3K27-altered"                      -> SmallVariant[gene=H3F3A & transcriptImpact.hgvsProteinImpact=p.K28M] | SmallVariant[gene=HIST1H3B & transcriptImpact.hgvsProteinImpact=p.K28M] | SmallVariant[gene=HIST1H3C & transcriptImpact.hgvsProteinImpact=p.K28M]
- "H3K27-altered AND BRAF V600E"       -> (SmallVariant[gene=H3F3A & transcriptImpact.hgvsProteinImpact=p.K28M] | SmallVariant[gene=HIST1H3B & transcriptImpact.hgvsProteinImpact=p.K28M] | SmallVariant[gene=HIST1H3C & transcriptImpact.hgvsProteinImpact=p.K28M]) & SmallVariant[gene=BRAF & transcriptImpact.hgvsProteinImpact=p.V600E]
- "adverse cytogenetics"               -> (empty)

"""

GENE_REVIEWER_INSTRUCTIONS = """\
You audit a proposed finding-model conversion of a trial's GENE-ALTERATION wording. You are given the SOURCE wording
and the proposed finding_model. This layer is NOT human-curated — be strict but fair. The SYNTAX is already
machine-validated (real classes/fields/enums, balanced, gene-scoped), so judge SEMANTIC faithfulness.

Set faithful=true only if ALL hold; otherwise faithful=false with concrete, actionable problems:

1. RIGHT GENE(S) — the correct gene(s). A gene FAMILY (RAS -> KRAS/NRAS/HRAS) or the HRR PANEL (BRCA1, BRCA2, ATM,
   BARD1, BRIP1, CDK12, CHEK1, CHEK2, FANCL, PALB2, PPP2R2A, RAD51B, RAD51C, RAD51D, RAD54L) is expanded to its
   members, not left as a non-gene token or a single arbitrary member.
2. RIGHT ALTERATION — the stated variant / exon / protein change / copy-number TYPE / fusion orientation is
   captured; notation normalised (HGVS `p.`; the `X` wildcard when the substituted residue is unstated).
3. RIGHT EXPANSION — a bare "X mutation" / "mutated" / "-mutant" maps to SmallVariant[gene=X] ONLY (a sequence
   variant — NOT expanded to amplification/deletion/fusion); the full tumour-suppressor-vs-oncogene EXPANSION is
   ONLY for a genuinely UNSPECIFIED event ("X alteration" / "aberration" / "genomic alteration" / "X-altered"). A
   SPECIFIC named variant is NOT over-expanded.
4. NEGATION — a disease-phrased exclusion DEFINED BY an expressible alteration is CONVERTED
   ("NOT(BCR-ABL-positive leukemia)" -> NOT(Fusion[geneStart=BCR & geneEnd=ABL1])); an OPEN-ENDED clinical /
   actionability exclusion, or one qualified by an inexpressible location / context / protein domain, is OMITTED
   (correct) — NOT broadened (dropping a qualifier inside an exclusion would OVER-exclude, e.g. NOT(bZIP CEBPA) must
   not become NOT(SmallVariant[gene=CEBPA])). A rearrangement inside NOT() is a Fusion, not a Disruption. No X & NOT(X).
5. NOT LAZY — a NAMED gene alteration is never dropped to "". "" is correct ONLY for a value with no molecular
   content (a pure clinical / risk / phenotype descriptor).
6. COMPLETE + CONSISTENT — every stated alteration is present (no dropped OR-alternative); the AND/OR/NOT structure
   matches the source; an OR-group ANDed with anything is parenthesised.

Do NOT fail a mapping for DROPPING a qualifier finding-model cannot express — functional/significance
("activating / actionable / oncogenic / pathogenic / deleterious"), origin ("germline / somatic"), detection /
assay / sample / timing, copy-number count/level, VAF, anatomic location, tumour context, protein DOMAIN. That
simplification is CORRECT. Two mandated simplifications you must NOT flag as over-specified / over-broadened:
  - an unspecified "deletion" / "loss" mapped to type=HOM_DEL (GainDeletion REQUIRES a type; HOM_DEL is the
    mandated default for an unqualified loss — "MTAP loss" -> GainDeletion[gene=MTAP & type=HOM_DEL] is correct);
  - a cytoband / segmental loss or gain mapped to the ARM level with the band range dropped
    ("9p21.1-24.3 loss" -> Arm[chromosome=9 & arm=p & type=ARM_LOSS] is correct; do not demand the cytoband).
Flag only a wrong gene, a wrong or lost REPRESENTABLE detail, a wrong expansion, a mishandled negation, a lazy "",
or broken structure.

CORRECT REFERENCE MAPPINGS — accept a proposal that maps this way (note the qualifiers correctly dropped):
- "KRAS G12C mutation"                 -> SmallVariant[gene=KRAS & transcriptImpact.hgvsProteinImpact=p.G12C]
- "IDH1 R132 mutation"                 -> SmallVariant[gene=IDH1 & transcriptImpact.hgvsProteinImpact=p.R132X]
- "EGFR exon 20 insertion"             -> SmallVariant[gene=EGFR & transcriptImpact.affectedExon=20 & transcriptImpact.effects=INFRAME_INSERTION]
- "MET exon 14 skipping mutation"      -> SmallVariant[gene=MET & transcriptImpact.affectedExon=14 & transcriptImpact.effects=SPLICE]
- "germline or somatic deleterious BRCA1 mutation" -> SmallVariant[gene=BRCA1]   ("mutation" -> SmallVariant only)
- "TP53 alteration" -> SmallVariant[gene=TP53] | GainDeletion[gene=TP53 & type=HOM_DEL] | Disruption[gene=TP53]   ("alteration" -> full expansion)
- "PDGFRA amplification with >=5 copy numbers" -> GainDeletion[gene=PDGFRA & type=GAIN]
- "ALK fusion"                         -> Fusion[geneStart=ALK | geneEnd=ALK]
- "NTRK gene fusion"                   -> Fusion[geneEnd=NTRK1] | Fusion[geneEnd=NTRK2] | Fusion[geneEnd=NTRK3]
- "ALK wild-type"                      -> Wildtype[gene=ALK]
- "confirmed FLT3-ITD mutation"        -> SmallVariant[gene=FLT3 & transcriptImpact.effects=INFRAME_INSERTION]
- "confirmed FLT3-TKD mutation"        -> SmallVariant[gene=FLT3]   (TKD is a protein domain; drop it, no exon)
- "RAS Q61X mutation"                  -> SmallVariant[gene=KRAS & transcriptImpact.hgvsProteinImpact=p.Q61X] | SmallVariant[gene=NRAS & transcriptImpact.hgvsProteinImpact=p.Q61X] | SmallVariant[gene=HRAS & transcriptImpact.hgvsProteinImpact=p.Q61X]
- "NOT(BCR-ABL-positive leukemia)"     -> NOT(Fusion[geneStart=BCR & geneEnd=ABL1])
- "adverse cytogenetics"               -> (empty)

MAPPINGS YOU MUST FAIL (proposed -> problem -> fix):
- SOURCE "KRAS G12C mutation", proposed "SmallVariant[gene=KRAS]"
    -> lost the protein change. fix "...& transcriptImpact.hgvsProteinImpact=p.G12C"
- SOURCE "RAS Q61X mutation", proposed "SmallVariant[gene=KRAS & transcriptImpact.hgvsProteinImpact=p.Q61X]"
    -> gene family not expanded; NRAS and HRAS are missing. fix the 3-gene OR.
- SOURCE "IDH1 R132 mutation", proposed "SmallVariant[gene=IDH1]"
    -> lost the codon; an unstated residue is the X wildcard. fix "...hgvsProteinImpact=p.R132X"
- SOURCE "NOT(BCR-ABL-positive leukemia)", proposed "" (or the disease left as text)
    -> the disease is defined by the BCR::ABL1 fusion; convert it. fix "NOT(Fusion[geneStart=BCR & geneEnd=ABL1])"
- SOURCE "cholangiocarcinoma with FGFR2 fusion", proposed ""
    -> lazy empty; the FGFR2 fusion is expressible. fix "Fusion[geneStart=FGFR2 | geneEnd=FGFR2]"
- SOURCE "EGFR exon 20 insertion", proposed "SmallVariant[gene=EGFR]"
    -> lost the exon + effect. fix "...affectedExon=20 & transcriptImpact.effects=INFRAME_INSERTION"
- SOURCE "ALK wild-type", proposed "NOT(SmallVariant[gene=ALK])"
    -> a whole-gene wild-type uses the Wildtype class. fix "Wildtype[gene=ALK]"
- SOURCE "KRAS amplification with >=5 copies", proposed problems=["dropped the copy count"]
    -> dropping the copy count is CORRECT; do NOT fail for it.
- SOURCE "EGFR mutation", proposed "SmallVariant[gene=EGFR] | GainDeletion[gene=EGFR & type=GAIN]"
    -> "mutation" is a sequence variant; amplification must NOT be added. fix "SmallVariant[gene=EGFR]"
- SOURCE "HRR gene mutation", proposed "SmallVariant[gene=BRCA1] | SmallVariant[gene=BRCA2]"
    -> the HRR panel is 15 genes; only 2 listed. fix the full 15-gene SmallVariant OR (BRCA1/2, ATM, BARD1, BRIP1,
       CDK12, CHEK1/2, FANCL, PALB2, PPP2R2A, RAD51B/C/D, RAD54L).
- SOURCE "FLT3-ITD", proposed "SmallVariant[gene=FLT3 & transcriptImpact.affectedExon=14 & transcriptImpact.effects=INFRAME_INSERTION]"
    -> the source does not state an exon; do not infer it. fix "SmallVariant[gene=FLT3 & transcriptImpact.effects=INFRAME_INSERTION]"
- SOURCE "NOT(bZIP CEBPA mutation)", proposed "NOT(SmallVariant[gene=CEBPA])"
    -> a domain-qualified exclusion was broadened; this over-excludes ALL CEBPA variants. fix: OMIT the NOT() entirely.
- SOURCE "NOT(MYC and BCL2 rearrangement)", proposed "NOT(Disruption[gene=MYC] & Disruption[gene=BCL2])"
    -> a rearrangement is a Fusion, not a Disruption. fix "NOT(Fusion[geneStart=MYC | geneEnd=MYC] & Fusion[geneStart=BCL2 | geneEnd=BCL2])"

`suggested_fix` — normally leave EMPTY (reporting the problems is enough). ONLY when the input is marked
"[ESCALATION-MODE]", fill it with the concrete corrected finding_model you would expect.
"""

_SIGNATURE_RULES = """\
You convert a clinical trial's MOLECULAR-SIGNATURE expression into Hartwig finding-model syntax. Return
`finding_model`, preserving the logical structure (AND `&`, OR `|`, exclusions wrapped in `NOT(...)`).

There are EXACTLY SIX signature terms — the ONLY output vocabulary. Map a genuine signature to its term,
recognising the common synonyms:
- MicrosatelliteStability[PurpleMicrosatelliteStatus=MSI]  <- MSI-high / MSI-H / MSI / dMMR / MMRd / mismatch-repair
    deficient / MMR-deficient / MSI-L / microsatellite instability-low / HNPCC / Lynch syndrome / constitutional MMR deficiency.
- MicrosatelliteStability[PurpleMicrosatelliteStatus=MSS]  <- MSS / microsatellite stable / pMMR / MMR-proficient / normal MMR.
- homologousRecombination[ChordStatus=HR_DEFICIENT]        <- HRD / HRD-positive / homologous-recombination deficient /
    HRR deficiency / HRRm / BRCAness / FH-deficient / SDH-deficient.
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


def build_gene_alteration_mapper(client: LlmClient, *, model: str | None = None) -> Agent[FindingModelMapping]:
    return Agent(
        name="gene_alteration_mapper",
        instructions=_GENE_RULES + GRAMMAR_REFERENCE,
        output_schema=FindingModelMapping,
        client=client,
        model=model,
    )


def build_gene_alteration_reviewer(client: LlmClient, *, model: str | None = None) -> Agent[ReviewVerdict]:
    return Agent(
        name="gene_alteration_reviewer",
        instructions=GENE_REVIEWER_INSTRUCTIONS + "\n" + GRAMMAR_REFERENCE,
        output_schema=ReviewVerdict,
        client=client,
        model=model,
    )


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


# --------------------------------------------------------------------------- #
# R4 — per-value repair (the path that does not exist in the production stage)
# --------------------------------------------------------------------------- #
_ONCOTREE_REPAIR_RULES = """\
You REPAIR one OncoTree code expression that a deterministic validator has rejected. You are given the SOURCE
cancer-type wording, the current code expression, and the EXACT defects found. Return the corrected expression.

Fix ONLY the reported defects. Do not re-map what is already right, do not change granularity that was not
flagged, and do not add exclusions the source does not state. If fixing a defect means deleting a term, delete it.
If the value states no tumour type of its own once the invalid parts are removed, return "" (empty).

{GRANULARITY_PARENT_CHILD}
{EXPRESSION_RULES}

WHAT EACH DEFECT MEANS AND HOW TO FIX IT:
- `lex_leaked_name`            an OncoTree NAME sits where a CODE belongs -> substitute the code.
- `log_negation_only`          nothing positive is stated -> "" (empty).
- `log_negated_sentinel`       a sentinel is negated -> drop that exclusion; if nothing positive remains, "".
- `log_disjoint_and`           two types that cannot co-exist are ANDed -> OR them, or keep the one the source
                               means, or drop the exclusion if it was trying to name a transformation.
- `log_disjoint_and_inside_not` NOT(A AND B) over co-exclusive types excludes nothing -> drop or restate.
- `log_exclude_ancestor`       an ancestor of an included code is excluded -> drop that exclusion.
- `log_or_branch_excluded`     an OR-branch is excluded by a sibling -> resolve to what the source means.
- `log_or_redundant_ancestor`  a broad term sits alongside its own subtype -> apply the GRANULARITY rule above and
                               keep exactly ONE of them, decided by the source wording.
- `syn_nested_not`             a NOT() inside a NOT() -> flatten. Allowed only for SKIN AND NOT(MEL) and
                               NSCLC AND NOT(LUSC). If the carve-out is conditioned on stage/grade/age, drop the
                               whole exclusion.
- `log_vacuous_exclusion`      an excluded type could not have matched anyway -> drop it.

ONCOTREE VOCABULARY (an indented tree — indentation shows the subtype hierarchy; each node is `Name (CODE)`):
""".replace("{GRANULARITY_PARENT_CHILD}", _GRANULARITY_PARENT_CHILD).replace("{EXPRESSION_RULES}", _EXPRESSION_RULES)


ONCOTREE_REPAIR_REVIEWER_INSTRUCTIONS = """\
You audit a REPAIR of an OncoTree code expression. You are given the SOURCE wording, the ORIGINAL expression, the
defects that were reported, and the proposed repair.

Set faithful=true only if ALL hold; otherwise faithful=false with concrete problems:
1. EVERY reported defect is actually gone.
2. NO NEW defect was introduced (check: valid codes, no negation-only, no negated sentinel, no co-exclusive AND,
   no broad-term-with-its-own-subtype, no excluded ancestor, nesting only for SKIN AND NOT(MEL) / NSCLC AND
   NOT(LUSC), one factored exclusion per conjunction).
3. NOTHING the source states was lost beyond what the defect required — a repair must not quietly drop a tumour
   type that was correctly present, and must not broaden the scope.
4. Where a broad term and its subtype had to be resolved to one, the SURVIVOR matches the source wording: the
   subtypes when the source enumerates them, the broad term when the source states the group ("including",
   "such as").
5. "" (empty) is used only when the value genuinely states no tumour type of its own.

`suggested_fix` — normally EMPTY. Only under "[ESCALATION-MODE]", give the concrete expression you would expect.

ONCOTREE VOCABULARY (an indented tree — indentation shows the subtype hierarchy; each node is `Name (CODE)`):
"""


# --------------------------------------------------------------------------- #
# STEP 2 — cross-value reconciliation adjudicators (doer -> reviewer per flagged group).
# Prompts finalised with the user 2026-07-28. cancer_type uses OncoTree; gene/signature reuse the same rules with
# the finding-model grammar. Grounding (vocab / grammar) is appended by the builders.
# --------------------------------------------------------------------------- #
_ONCOTREE_RECONCILE_RULES = """\
You reconcile the OncoTree mapping of a GROUP of cancer-type phrasings that a consistency check flagged as ONE
underlying concept but which received DIFFERENT codes when each was mapped independently. Return the FINAL code
expression for EACH member.

- EQUIVALENT members — same tumour scope, differing only in phrasing / synonym / dropped qualifier-noise — MUST
  share ONE final code: the most-specific OncoTree node the group's wording jointly supports.
- GENUINELY-DISTINCT members — the group over-merged a difference OncoTree DOES encode (grade, histology subtype,
  distinct organ / lineage) — MUST keep their own correct code. "astrocytoma grade 3" -> ASTR3 and "grade 4" ->
  ASTR4 stay different; do NOT collapse a real distinction to force agreement.
- A difference OncoTree canNOT encode (laterality, stage, "advanced/metastatic/recurrent") is NOT a real
  distinction -> unify those members.

**REPAIR the structure as you go — do NOT preserve it.** Each member arrives with the defects a validator found;
fix them. (The previous version of this instruction said to preserve each member's AND/OR/NOT structure, which is
exactly why every structural defect survived reconciliation.) Apply the rules below to every value you return.

{GRANULARITY_PARENT_CHILD}
{EXPRESSION_RULES}

Return every input from the group exactly once, with its FINAL OncoTree code expression.

ONCOTREE VOCABULARY (an indented tree — indentation shows the subtype hierarchy; each node is `Name (CODE)`):
""".replace("{GRANULARITY_PARENT_CHILD}", _GRANULARITY_PARENT_CHILD).replace("{EXPRESSION_RULES}", _EXPRESSION_RULES)


ONCOTREE_RECONCILE_REVIEWER_INSTRUCTIONS = """\
You audit a reconciliation decision for a flagged group of cancer-type phrasings (each with its FINAL OncoTree
code). Set faithful=true only if ALL hold; otherwise faithful=false with concrete problems (which member, which
code, why):
1. EQUIVALENT members now share ONE code (phrasing / synonym / qualifier-noise / stage differences unified).
2. GENUINELY-DISTINCT members are kept apart, each with its own correct code — a real OncoTree-encodable
   difference (grade / histology / distinct organ) was NOT collapsed to force agreement.
3. Each unifying code is the MOST-SPECIFIC node correctly covering all its members.
4. Every final code is VALID and follows the granularity conventions.
5. **Structure is REPAIRED, not preserved:** no negation-only value, no negated sentinel, no co-exclusive AND, no
   broad term alongside its own subtype, no excluded ancestor, nesting only for SKIN AND NOT(MEL) / NSCLC AND
   NOT(LUSC), exclusions factored into one NOT(...) per conjunction. Any member still carrying one of these is a
   failure even if the group is now consistent.
6. Exclusions are tumour-type carve-outs only — no prior/second malignancy, CNS-site or synchronous-primary
   exclusions survive.

`suggested_fix` — normally EMPTY. Only under "[ESCALATION-MODE]", give the concrete per-member codes you expect.

ONCOTREE VOCABULARY (an indented tree — indentation shows the subtype hierarchy; each node is `Name (CODE)`):
"""


_FINDINGMODEL_RECONCILE_RULES = """\
You reconcile the finding-model mapping of a GROUP of gene-alteration / molecular-signature phrasings that a
consistency check flagged as ONE underlying concept but which received DIFFERENT finding-model expressions when each
was mapped independently. Return the FINAL finding-model expression for EACH member.

- EQUIVALENT members (same alteration, differing only in phrasing / synonym / dropped inexpressible qualifier) MUST
  share ONE final expression (the correct one, per the grammar conventions). e.g. two phrasings of the same fusion
  that got different orientations -> the one correct rendering.
- GENUINELY-DISTINCT members (a real difference the grammar DOES encode — different gene, variant, exon,
  copy-number type, fusion orientation) MUST keep their own correct expression; do NOT collapse a real distinction.
- Fix any expression that is simply wrong while you are here; obey the mapper's conventions (mutation -> SmallVariant
  only; families/panels expanded; notation normalised). Preserve each member's AND / OR / NOT() structure.

Return every input from the group exactly once, with its FINAL finding-model expression.
"""

FINDINGMODEL_RECONCILE_REVIEWER_INSTRUCTIONS = """\
You audit a reconciliation decision for a flagged group of gene/signature phrasings (each with its FINAL
finding-model expression). Set faithful=true only if: EQUIVALENT members now share ONE expression (phrasing noise
unified); GENUINELY-DISTINCT members kept apart with their own correct expression (a real grammar-encodable
difference NOT collapsed); every expression is valid finding-model following the mapper conventions; AND / OR / NOT()
structure preserved. Otherwise faithful=false with concrete problems. `suggested_fix` only under "[ESCALATION-MODE]".
"""


def build_oncotree_reconciler(client: LlmClient, *, model: str | None = None) -> Agent[GroupReconciliation]:
    return Agent(name="oncotree_reconciler", instructions=_ONCOTREE_RECONCILE_RULES + vocab_reference(),
                 output_schema=GroupReconciliation, client=client, model=model)


def build_oncotree_reconcile_reviewer(client: LlmClient, *, model: str | None = None) -> Agent[ReviewVerdict]:
    return Agent(name="oncotree_reconcile_reviewer",
                 instructions=ONCOTREE_RECONCILE_REVIEWER_INSTRUCTIONS + vocab_reference(),
                 output_schema=ReviewVerdict, client=client, model=model)
def build_findingmodel_reconciler(client: LlmClient, *, model: str | None = None) -> Agent[GroupReconciliation]:
    return Agent(name="findingmodel_reconciler", instructions=_FINDINGMODEL_RECONCILE_RULES + GRAMMAR_REFERENCE,
                 output_schema=GroupReconciliation, client=client, model=model)


def build_findingmodel_reconcile_reviewer(client: LlmClient, *, model: str | None = None) -> Agent[ReviewVerdict]:
    return Agent(name="findingmodel_reconcile_reviewer",
                 instructions=FINDINGMODEL_RECONCILE_REVIEWER_INSTRUCTIONS + "\n" + GRAMMAR_REFERENCE,
                 output_schema=ReviewVerdict, client=client, model=model)
