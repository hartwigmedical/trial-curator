"""Cancer-type (OncoTree) mapping agents: mapper -> reviewer, the R4 repairer, and the Step-2 reconciler.

Split out of the former single `mapping/agents.py` so each vocabulary column owns its own prompts.
⚠ Prompt text is byte-identical to the pre-split version — it is hashed into the response-cache key, so
reformatting it silently orphans every cached decision behind it.
"""
from __future__ import annotations

from aus_trial_universe.core.agent import Agent
from aus_trial_universe.core.client import LlmClient
from aus_trial_universe.tasks.eligibility.mapping.schema import (
    GroupReconciliation,
    OncotreeMapping,
    OncotreeRepair,
    ReviewVerdict,
)
from aus_trial_universe.tasks.eligibility.mapping.cancer_type.vocab import vocab_reference

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
def build_oncotree_reconciler(client: LlmClient, *, model: str | None = None) -> Agent[GroupReconciliation]:
    return Agent(name="oncotree_reconciler", instructions=_ONCOTREE_RECONCILE_RULES + vocab_reference(),
                 output_schema=GroupReconciliation, client=client, model=model)


def build_oncotree_reconcile_reviewer(client: LlmClient, *, model: str | None = None) -> Agent[ReviewVerdict]:
    return Agent(name="oncotree_reconcile_reviewer",
                 instructions=ONCOTREE_RECONCILE_REVIEWER_INSTRUCTIONS + vocab_reference(),
                 output_schema=ReviewVerdict, client=client, model=model)
