"""APPROVED ADJUDICATIONS — mapped values the user has ruled on by hand.

Sits beside `waivers.py` for the same reason: `data/` is gitignored, so a decision that lives in code is
visible in review and in `git log`. Step 4 of the review workflow produces a hand-written FINAL for values the
automated stages cannot settle. Each one is recorded here rather than patched into the data, because a
hand-edited map table is overwritten by the next `--reconcile`, whereas a ruling here is reapplied every run.

**An entry is an EXPECTATION FIRST, an override second.** The refinement runs normally; the comparison file
then reports, per adjudicated value, whether the pipeline reached the approved answer on its own:

    match     the prompts + logic produced the approved value unaided  -> the fix works
    override  they did not, so the approved value was substituted      -> the fix is INCOMPLETE

An `override` is not a success. It means the rule that should have produced it is missing or too weak, and the
correct response is to strengthen the prompt or the deterministic layer — not to accumulate overrides. The count
of overrides is therefore a quality metric for the correction itself, and an entry that flips to `match` after
a prompt fix should be RETIRED.

Two registers, one per vocabulary column, each keyed by the INTERPRETED SOURCE VALUE (the map table's own key),
because the mapped value is what we are changing and so cannot be a stable key:

    APPROVED       cancer_type      -> oncotree_code_FINAL        applied at reconcile.py R7
    APPROVED_GENE  gene_alteration  -> finding_model_FINAL        applied at gene_alteration/reconcile.py R5

A ruling is applied AFTER the deterministic convergence pass and is NOT re-canonicalised, so `final_code` must
already be in canonical form. `""` is a LEGITIMATE ruling (the faithful mapping of a criterion the target
vocabulary cannot express), so callers must test `is not None`, never truthiness.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Adjudication:
    cancer_type: str          # the map table key (the interpreted source value)
    final_code: str           # the approved oncotree_code_FINAL
    rationale: str
    approved: str             # date + who


@dataclass(frozen=True)
class GeneAdjudication:
    gene_alteration: str      # the map table key (the interpreted source value)
    final_expression: str     # the approved finding_model_FINAL ("" is meaningful — see the module docstring)
    rationale: str
    approved: str


APPROVED: dict[str, Adjudication] = {}
APPROVED_GENE: dict[str, GeneAdjudication] = {}


def _add(a: Adjudication) -> None:
    APPROVED[a.cancer_type] = a


def _add_gene(a: GeneAdjudication) -> None:
    APPROVED_GENE[a.gene_alteration] = a


# --------------------------------------------------------------------------- #
# 2026-08-03
# --------------------------------------------------------------------------- #
_add(Adjudication(
    cancer_type=(
        "relapsed or refractory multiple myeloma AND NOT(ongoing myelodysplastic syndrome or B-cell malignancy "
        "other than multiple myeloma) AND NOT(active or high-risk recurrent malignancy other than multiple "
        "myeloma) AND NOT(active or prior CNS involvement or clinical signs of meningeal involvement of multiple "
        "myeloma)"
    ),
    final_code="PCM",
    rationale=(
        "Was `PCM AND NOT(MDS OR BLL OR (MBN AND NOT(PCM)))`. Four independent rules converge on plain PCM: "
        "(1) all three source exclusions are non-tumour-type — 'other than multiple myeloma' twice (second/"
        "recurrent malignancy) and 'CNS involvement OF multiple myeloma' (disease site), so D1 drops them; "
        "(2) MDS is myeloid and BLL is a precursor neoplasm, both disjoint from PCM, so E6/R2 strips them anyway; "
        "(3) MBN is PCM's own OncoTree parent, so the expression excludes the ancestor of the type it includes "
        "and is safe only if an engine resolves the nested double negation exactly as intended; "
        "(4) BLL is unsupported by the source — it says 'B-cell malignancy', and BLL is not under MBN."
    ),
    approved="2026-08-03, user",
))

# --------------------------------------------------------------------------- #
# 2026-08-05 — the full-corpus mapping audit (cancer_type + gene_alteration).
#
# Prompted by matching-engine feedback on 26 trials. The engine's own complaints about leaked OncoTree NAMES
# were already fixed by B1; auditing all 4,971 cancer_type and 909 gene_alteration values then surfaced two
# classes of genuine defect of OURS:
#
#   * B1 REGRESSIONS — the mapper re-rolled a value and returned a WORSE answer, replacing specific codes with
#     a sentinel. Six of these changed while fixing NO defect at all, and two while fixing only a cosmetic one.
#     They were invisible because B1's review artifact reported defects FIXED and REMAINING, never quality LOST.
#   * LONG-STANDING defects that predate B1 and that no check covered.
#
# The prompt weakness that produced them is deliberately NOT being chased here: a prompt change re-fingerprints
# the agent, prunes its cache and re-rolls all 4,971 values, which is exactly the risk that produced these
# regressions. It is tracked as a separate batched task; these rulings hold the line meanwhile, and the new
# broadening gate stops the class recurring.
# --------------------------------------------------------------------------- #

# was: 'Solid tumour'
_add(Adjudication(
    cancer_type=(
        'recurrent and/or metastatic adenoid cystic carcinoma with disease progression within 12 months'
    ),
    final_code='ACYC',
    rationale=(
        "B1 replaced ACYC with the pan-solid sentinel while fixing no defect (issues_fixed=''). Adenoid "
        'cystic carcinoma is a single OncoTree entity named verbatim in the source.'
    ),
    approved='2026-08-05, user',
))

# was: 'Solid tumour'
_add(Adjudication(
    cancer_type=(
        'gynaecological cancer'
    ),
    final_code='CERVIX OR OVARY OR UTERUS OR VULVA',
    rationale=(
        'B1 broadened the four gynaecological organ codes to the pan-solid sentinel while fixing no '
        "defect. 'Gynaecological' is not itself an OncoTree name, so only the organ set can express it."
    ),
    approved='2026-08-05, user',
))

# was: 'Solid tumour'
_add(Adjudication(
    cancer_type=(
        'histologically confirmed gynaecological malignancy'
    ),
    final_code='CERVIX OR OVARY OR UTERUS OR VULVA',
    rationale=(
        "Same defect and reasoning as 'gynaecological cancer'."
    ),
    approved='2026-08-05, user',
))

# was: 'Solid tumour'
_add(Adjudication(
    cancer_type=(
        'gynaecological cancers receiving adjuvant radiotherapy'
    ),
    final_code='CERVIX OR OVARY OR UTERUS OR VULVA',
    rationale=(
        "Same defect as 'gynaecological cancer'; the radiotherapy qualifier is not a tumour-type restriction."
    ),
    approved='2026-08-05, user',
))

# was: 'Solid tumour'
_add(Adjudication(
    cancer_type=(
        'FIGO stage I-IVA recurrent gynaecological cancer AND NOT(significant LVSI or pelvic sidewall '
        'invasion)'
    ),
    final_code='CERVIX OR OVARY OR UTERUS OR VULVA',
    rationale=(
        "Same defect as 'gynaecological cancer'; FIGO stage and LVSI are not tumour-type restrictions."
    ),
    approved='2026-08-05, user',
))

# was: 'Solid tumour AND NOT(UCEC)'
_add(Adjudication(
    cancer_type=(
        'histologically-proven recurrent/metastatic other gynaecological cancers AND NOT(endometrial cancers)'
    ),
    final_code='(CERVIX OR OVARY OR UTERUS OR VULVA) AND NOT(UCEC)',
    rationale=(
        "Same broadening as 'gynaecological cancer'. The endometrial exclusion is stated in the source "
        'and UCEC sits under UTERUS, so it is a real (non-vacuous) exclusion and is kept.'
    ),
    approved='2026-08-05, user',
))

# was: 'Solid tumour AND NOT(GINET OR GINETES)'
_add(Adjudication(
    cancer_type=(
        'UGI/LGI cancer AND NOT(neuroendocrine cancer)'
    ),
    final_code='(BOWEL OR STOMACH) AND NOT(GINET OR GINETES)',
    rationale=(
        'B1 broadened the two GI organ codes to the pan-solid sentinel while fixing only OR-branch '
        "ordering (issues_fixed='syn_or_order_noncanonical') — a cosmetic repair that rewrote the "
        'substance.'
    ),
    approved='2026-08-05, user',
))

# was: 'Solid tumour'
_add(Adjudication(
    cancer_type=(
        'spine cancer'
    ),
    final_code='BONE',
    rationale=(
        'B1 broadened BONE to the pan-solid sentinel while fixing no defect.'
    ),
    approved='2026-08-05, user',
))

# was: 'Solid tumour AND NOT(LGGNOS)'
_add(Adjudication(
    cancer_type=(
        'refractory/relapsed/progressive high-risk solid tumour: other embryonal small round blue cell '
        'tumours including paediatric type (bone) sarcoma AND NOT(low-grade gliomas) AND NOT(tumours of '
        'unknown malignant potential)'
    ),
    final_code='EMBT OR ES OR RCSNOS',
    rationale=(
        'B1 broadened three precise codes to the pan-solid sentinel, claiming to fix '
        "log_vacuous_exclusion — but it 'fixed' the vacuous NOT(LGGNOS) by widening the POSITIVE term, "
        'which is not what that check asks for. The low-grade-glioma exclusion is genuinely vacuous '
        'against these three codes, so canonical form drops it.'
    ),
    approved='2026-08-05, user',
))

# was: 'Solid tumour'
_add(Adjudication(
    cancer_type=(
        'advanced and/or metastatic, unresectable gastrointestinal histologically proven (WHO/ENET) Grade '
        '3 neuroendocrine carcinoma, including Mixed AdenoneuroEndocrine Carcinomas (MANEC) with G3 '
        'elements AND NOT(NECs confirmed not to be from gastrointestinal primaries) AND NOT(Grade 1 and '
        'Grade 2 NETs (Ki-67 <=20%)) AND NOT(suspected pulmonary origin of the NET)'
    ),
    final_code='GINET',
    rationale=(
        'The only value in the corpus that stage-2 RECONCILIATION (not the mapper) broadened to a '
        'sentinel. NOT(LNET) is vacuous against a GI neuroendocrine tumour, so canonical form drops it. '
        'Orphan map entry: 0 export rows, corrected for hygiene.'
    ),
    approved='2026-08-05, user',
))

# was: 'Solid tumour AND NOT(LGGNOS)'
_add(Adjudication(
    cancer_type=(
        'refractory/relapsed/progressive high-risk other embryonal small round blue cell tumors including '
        'pediatric type (bone) sarcoma AND NOT(low-grade gliomas or tumors of unknown malignant '
        'potential)'
    ),
    final_code='EMBT OR ES OR RCSNOS',
    rationale=(
        'Sibling of the ACTRN12619001768189 value; here the sentinel predates B1, so it is a '
        'long-standing defect rather than a regression. Same correction.'
    ),
    approved='2026-08-05, user',
))

# was: 'Solid tumour'
_add(Adjudication(
    cancer_type=(
        'locally advanced or metastatic malignant mesothelioma (MM)'
    ),
    final_code='PEMESO OR PLMESO',
    rationale=(
        'Mesothelioma mapped to the pan-solid sentinel. OncoTree has no site-agnostic mesothelioma node, '
        'so the two clinically relevant sites are enumerated; testicular mesothelioma is vanishingly rare '
        'and omitted.'
    ),
    approved='2026-08-05, user',
))

# was: 'Solid tumour'
_add(Adjudication(
    cancer_type=(
        'Recurrent Malignant Germ Cell Tumor'
    ),
    final_code='BGCT OR EGCT OR NSGCT OR OGCT OR SEM OR VGCT',
    rationale=(
        'NCI-COG Pediatric MATCH condition list, where EVERY sibling condition (Ependymoma->EPM, '
        'Ewing->ES, Hepatoblastoma->LIHB, Medulloblastoma->MBL, Wilms->WT ...) received a specific code — '
        'only this one fell to the sentinel, so it is an isolated miss, not a deliberate basket call. '
        'OncoTree has no site-agnostic germ-cell parent; this 6-code set is already the corpus convention '
        "(cf. ACTRN12618001236280). 'Malignant' excludes mature teratoma, so the teratoma codes are not "
        'included.'
    ),
    approved='2026-08-05, user',
))

# was: 'Solid tumour'
_add(Adjudication(
    cancer_type=(
        'Refractory Malignant Germ Cell Tumor'
    ),
    final_code='BGCT OR EGCT OR NSGCT OR OGCT OR SEM OR VGCT',
    rationale=(
        "Same value and reasoning as its 'Recurrent' twin."
    ),
    approved='2026-08-05, user',
))

# was: 'Solid tumour'
_add(Adjudication(
    cancer_type=(
        'mature teratoma'
    ),
    final_code='BMT',
    rationale=(
        'A named OncoTree entity (Mature Teratoma) mapped to the pan-solid sentinel.'
    ),
    approved='2026-08-05, user',
))

# was: 'Solid tumour'
_add(Adjudication(
    cancer_type=(
        'immature teratoma'
    ),
    final_code='BIMT',
    rationale=(
        'A named OncoTree entity (Immature Teratoma) mapped to the pan-solid sentinel.'
    ),
    approved='2026-08-05, user',
))

# was: 'Solid tumour'
_add(Adjudication(
    cancer_type=(
        'Advanced Solid Tumors Cancer AND hepatocellular carcinoma (HCC)'
    ),
    final_code='HCC',
    rationale=(
        "The source names HCC as the cohort's tumour type; the sentinel drops it."
    ),
    approved='2026-08-05, user',
))

# was: 'Solid tumour'
_add(Adjudication(
    cancer_type=(
        'Advanced Solid Tumors Cancer AND HCC'
    ),
    final_code='HCC',
    rationale=(
        "Same as the spelled-out 'hepatocellular carcinoma' twin."
    ),
    approved='2026-08-05, user',
))

# was: 'BLADDER'
_add(Adjudication(
    cancer_type=(
        'high grade non-muscle invasive bladder cancer (HR-NMIBC), high-grade Ta AND NOT(muscle invasive, '
        'locally advanced, nonresectable, or metastatic urothelial carcinoma [>=T2]) AND NOT(urothelial '
        'carcinoma or histological variant at any site outside of the urinary bladder, except Ta/any '
        'T1/CIS of the upper urinary tract treated with complete nephroureterectomy >24 months prior to '
        'randomization) AND NOT(tumors involving the prostatic urethra)'
    ),
    final_code='BLCA',
    rationale=(
        'B1 broadened BLCA to the BLADDER organ bucket — the organ swallowing the histology. The source '
        'is specifically non-muscle-invasive bladder UROTHELIAL carcinoma. The stated upper-tract (UTUC) '
        'and urethral (UCU) exclusions are deliberately NOT restored: both are SIBLINGS of BLCA under '
        'BLADDER rather than descendants, so as a tumour-type criterion excluding them is a no-op '
        '(canonical_form drops them). What the trial is really excluding is a concurrent second primary '
        'outside the bladder, which is a non-tumour-type criterion and belongs in the free text, not in '
        'the OncoTree expression.'
    ),
    approved='2026-08-05, user',
))

# was: 'BLADDER'
_add(Adjudication(
    cancer_type=(
        'high grade non-muscle invasive bladder cancer (HR-NMIBC), any T1 AND NOT(muscle invasive, '
        'locally advanced, nonresectable, or metastatic urothelial carcinoma [>=T2]) AND NOT(urothelial '
        'carcinoma or histological variant at any site outside of the urinary bladder, except Ta/any '
        'T1/CIS of the upper urinary tract treated with complete nephroureterectomy >24 months prior to '
        'randomization) AND NOT(tumors involving the prostatic urethra)'
    ),
    final_code='BLCA',
    rationale=(
        'Same trial and defect as the high-grade Ta cohort.'
    ),
    approved='2026-08-05, user',
))

# was: 'BLADDER'
_add(Adjudication(
    cancer_type=(
        'high grade non-muscle invasive bladder cancer (HR-NMIBC), carcinoma in-situ (CIS) AND NOT(muscle '
        'invasive, locally advanced, nonresectable, or metastatic urothelial carcinoma [>=T2]) AND '
        'NOT(urothelial carcinoma or histological variant at any site outside of the urinary bladder, '
        'except Ta/any T1/CIS of the upper urinary tract treated with complete nephroureterectomy >24 '
        'months prior to randomization) AND NOT(tumors involving the prostatic urethra)'
    ),
    final_code='BLCA',
    rationale=(
        'Same trial and defect as the high-grade Ta cohort.'
    ),
    approved='2026-08-05, user',
))

# was: 'BREAST'
_add(Adjudication(
    cancer_type=(
        'early stage unifocal histologically confirmed invasive breast carcinoma, maximum microscopic '
        'size ≤2 cm, grade 1 or 2 histology, histologically confirmed negative nodal status (pN0(i+) '
        'eligible), no evidence of distant metastasis AND NOT(multifocal or multicentric invasive '
        'carcinoma or ductal carcinoma in situ) AND NOT(clinical or pathologic T4 disease, including '
        'inflammatory carcinoma) AND NOT(micro-invasion only) AND NOT(lymphovascular invasion) AND '
        'NOT(pre-operative breast imaging evidence of disease aside from the primary carcinoma resected '
        'by breast conserving surgery) AND NOT(concurrent invasive breast carcinoma or ductal carcinoma '
        'in situ) AND NOT(prior diagnosis of invasive breast carcinoma or ductal carcinoma in situ in '
        'either breast irrespective of disease-free interval)'
    ),
    final_code='BREAST AND NOT(DCIS OR IBC)',
    rationale=(
        "Both exclusions are stated in the source ('or ductal carcinoma in situ', 'including inflammatory "
        "carcinoma') and DCIS and IBC are both BREAST descendants, so neither exclusion is vacuous."
    ),
    approved='2026-08-05, user',
))

# was: 'AML AND NOT(APLPMLRARA)'
_add(Adjudication(
    cancer_type=(
        'Acute Myeloid Leukemia (AML) AND NOT(acute promyelocytic leukemia) AND NOT(known active central '
        'nervous system (CNS) involvement with AML) AND NOT(history of myeloproliferative neoplasm (MPN) '
        'including myelofibrosis, essential thrombocythemia, polycythemia vera, chronic myeloid leukemia '
        '(CML) with or without BCR-ABL1 translocation and AML with BCR-ABL1 translocation)'
    ),
    final_code='AML AND NOT(AMLBCRABL1 OR APLPMLRARA)',
    rationale=(
        "The source explicitly excludes 'AML with BCR-ABL1 translocation'; AMLBCRABL1 is an AML "
        'descendant, so that exclusion is real and was wrongly dropped. MPN itself is disjoint from AML — '
        'that exclusion IS vacuous, so it is correctly left out.'
    ),
    approved='2026-08-05, user',
))

# was: 'Pan-cancer'
_add(Adjudication(
    cancer_type=(
        'Neoplasms AND NOT(symptomatic or untreated central nervous system (CNS) metastases) AND '
        'NOT(signs, symptoms or history of thyroid tumors)'
    ),
    final_code='Pan-cancer AND NOT(THYROID)',
    rationale=(
        "The source excludes 'signs, symptoms or history of thyroid tumors'. THYROID is a Pan-cancer "
        'descendant, so the exclusion is meaningful and was wrongly dropped.'
    ),
    approved='2026-08-05, user',
))

# was: 'MT'
_add(Adjudication(
    cancer_type=(
        'previously histologically or cytologically confirmed relapsed, refractory, or recurrent '
        'pediatric malignant brain tumor with no standard treatment options with curative potential'
    ),
    final_code='PBT',
    rationale=(
        "Mapped to MT ('Malignant Tumor'), an OncoTree catch-all bucket, for a source that says brain "
        'tumour. MT was missing from checks.CATCHALL_NODES, which is why lex_catchall_node (error) never '
        'fired; that gap is fixed alongside this ruling.'
    ),
    approved='2026-08-05, user',
))

# was: 'NHL AND NOT(BLL11Q OR HHV8DLBCL OR LYG OR PLBL OR THRLBCL OR WM)'
_add(Adjudication(
    cancer_type=(
        'transformation of indolent lymphoma AND NOT(Human herpes virus (HHV) 8-positive DLBCL OR T '
        'cell/histiocyte-rich large B-cell lymphoma OR Burkitt and high-grade B-cell lymphoma with 11q '
        "aberrations (previously Burkitt-like lymphoma) OR Richter's transformation OR Lymphomatoid "
        "granulomatosis OR Plasmablastic lymphoma OR Waldenstrom's Macroglobulinemia)"
    ),
    final_code='NHL AND NOT(BL OR BLL11Q OR CLLSLL OR HHV8DLBCL OR LYG OR PLBL OR THRLBCL OR WM)',
    rationale=(
        "Two codes were dropped from an explicitly enumerated exclusion list: BL ('Burkitt and high-grade "
        "B-cell lymphoma with 11q aberrations') and CLLSLL (Richter's transformation is by definition "
        'CLL-derived). Both are NHL descendants, so both exclusions are real.'
    ),
    approved='2026-08-05, user',
))

# was: 'Solid tumour AND NOT(STAD)'
_add(Adjudication(
    cancer_type=(
        'pathologically confirmed advanced and/or metastatic solid cancer of any histologic type AND '
        'NOT(primary tumour histology HER2 amplified breast adenocarcinoma or gastric adenocarcinoma)'
    ),
    final_code='Solid tumour AND NOT(BREAST OR STAD)',
    rationale=(
        "The source excludes 'HER2 amplified breast adenocarcinoma or gastric adenocarcinoma'. The "
        'gastric half was kept and the breast half dropped — internally inconsistent. Both are qualified '
        'by HER2 status, so either both go or both stay; kept, matching the retained STAD.'
    ),
    approved='2026-08-05, user',
))


# --------------------------------------------------------------------------- #
# gene_alteration
# --------------------------------------------------------------------------- #

# was: 'Wildtype[gene=AGA]'
_add_gene(GeneAdjudication(
    gene_alteration=(
        'AGA negative'
    ),
    final_expression=(
        ''
    ),
    rationale=(
        "'AGA' here is the NSCLC term ACTIONABLE GENOMIC ALTERATION, not the lysosomal gene AGA. The "
        'mapping asserted Wildtype[gene=AGA], a criterion the trial never states. Our own data exposed '
        "it: the sibling cohort's 'AGA positive' mapped to '' — the same token read as a gene in one "
        "value and not the other. 'No actionable genomic alteration' is a complement over an open-ended "
        'panel, and the locked rule is that an open-ended or inexpressible criterion is OMITTED, never '
        "approximated. '' is the faithful mapping and makes the positive/negative pair symmetric."
    ),
    approved='2026-08-05, user',
))

# was: 'Fusion[geneStart=PTPRZ1 & geneEnd=MET] & NOT(SmallVariant[gene=ALK]) & NOT(SmallVariant[gene=BRAF]) & NOT(SmallVariant[gene=EGFR]) & NOT(SmallVariant['
_add_gene(GeneAdjudication(
    gene_alteration=(
        'MET fusion, including PTPRZ1-MET fusion AND NOT(known MET kinase inhibitor resistance mutation) '
        'AND NOT(known actionable EGFR mutation/gene rearrangement) AND NOT(known actionable ALK '
        'mutation/gene rearrangement) AND NOT(known actionable ROS1 mutation/gene rearrangement) AND '
        'NOT(known actionable RET mutation/gene rearrangement) AND NOT(known actionable NTRK '
        'mutation/gene rearrangement) AND NOT(known actionable KRAS mutation/gene rearrangement) AND '
        'NOT(known actionable BRAF mutation/gene rearrangement)'
    ),
    final_expression=(
        'Fusion[geneStart=MET | geneEnd=MET] & NOT(SmallVariant[gene=ALK]) & NOT(SmallVariant[gene=BRAF]) '
        '& NOT(SmallVariant[gene=EGFR]) & NOT(SmallVariant[gene=KRAS]) & NOT(SmallVariant[gene=NTRK1]) & '
        'NOT(SmallVariant[gene=NTRK2]) & NOT(SmallVariant[gene=NTRK3]) & NOT(SmallVariant[gene=RET]) & '
        'NOT(SmallVariant[gene=ROS1]) & NOT(Fusion[geneEnd=NTRK1]) & NOT(Fusion[geneEnd=NTRK2]) & '
        'NOT(Fusion[geneEnd=NTRK3]) & NOT(Fusion[geneStart=ALK | geneEnd=ALK]) & '
        'NOT(Fusion[geneStart=BRAF | geneEnd=BRAF]) & NOT(Fusion[geneStart=EGFR | geneEnd=EGFR]) & '
        'NOT(Fusion[geneStart=KRAS | geneEnd=KRAS]) & NOT(Fusion[geneStart=RET | geneEnd=RET]) & '
        'NOT(Fusion[geneStart=ROS1 | geneEnd=ROS1])'
    ),
    rationale=(
        "Over-narrowing: the source is ANY MET fusion, with PTPRZ1-MET given as an example ('including'). "
        'The mapping restricted the criterion to the example alone, losing every other MET fusion '
        'partner. The exclusion clauses are correct and are preserved verbatim.'
    ),
    approved='2026-08-05, user',
))
