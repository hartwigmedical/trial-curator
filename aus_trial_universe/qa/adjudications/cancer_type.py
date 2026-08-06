"""Approved rulings for the `cancer_type` column -> `oncotree_code_FINAL`.

Applied at `mapping/reconcile.py` R7, as the last word after the deterministic convergence pass. See the package
docstring for the doctrine (expectation first, override second) and the two rules for callers.
"""
from __future__ import annotations

from aus_trial_universe.qa.adjudications import Adjudication

APPROVED: dict[str, Adjudication] = {}


def _add(a: Adjudication) -> None:
    APPROVED[a.value] = a


# --------------------------------------------------------------------------- #
# 2026-08-03
# --------------------------------------------------------------------------- #
_add(Adjudication(
    value=(
        "relapsed or refractory multiple myeloma AND NOT(ongoing myelodysplastic syndrome or B-cell malignancy "
        "other than multiple myeloma) AND NOT(active or high-risk recurrent malignancy other than multiple "
        "myeloma) AND NOT(active or prior CNS involvement or clinical signs of meningeal involvement of multiple "
        "myeloma)"
    ),
    final="PCM",
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
# 2026-08-05 — the full-corpus mapping audit.
#
# Prompted by matching-engine feedback on 26 trials. The engine's own complaints about leaked OncoTree NAMES were
# already fixed by B1; auditing all 4,971 cancer_type and 909 gene_alteration values then surfaced two classes of
# genuine defect of OURS:
#
#   * B1 REGRESSIONS — the mapper re-rolled a value and returned a WORSE answer, replacing specific codes with a
#     sentinel. Six changed while fixing NO defect at all, and two while fixing only a cosmetic one. They were
#     invisible because B1's review artifact reported defects FIXED and REMAINING, never quality LOST.
#   * LONG-STANDING defects that predate B1 and that no check covered.
#
# The prompt weakness behind them is deliberately NOT chased here: a prompt change re-fingerprints the agent,
# prunes its cache and re-rolls all 4,971 values, which is the very mechanism that produced these regressions. It
# is tracked as handover B4/B5; these rulings hold the line meanwhile, and the `mapping_drift` gate stops the
# class recurring.
# --------------------------------------------------------------------------- #

# was: 'ACYC'
_add(Adjudication(
    value=(
        'recurrent and/or metastatic adenoid cystic carcinoma with disease progression within 12 months'
    ),
    final=(
        'ACYC'
    ),
    rationale=(
        "B1 replaced ACYC with the pan-solid sentinel while fixing no defect (issues_fixed=''). Adenoid "
        'cystic carcinoma is a single OncoTree entity named verbatim in the source.'
    ),
    approved='2026-08-05, user',
))

# was: 'CERVIX OR OVARY OR UTERUS OR VULVA'
_add(Adjudication(
    value=(
        'gynaecological cancer'
    ),
    final=(
        'CERVIX OR OVARY OR UTERUS OR VULVA'
    ),
    rationale=(
        'B1 broadened the four gynaecological organ codes to the pan-solid sentinel while fixing no '
        "defect. 'Gynaecological' is not itself an OncoTree name, so only the organ set can express it."
    ),
    approved='2026-08-05, user',
))

# was: 'CERVIX OR OVARY OR UTERUS OR VULVA'
_add(Adjudication(
    value=(
        'histologically confirmed gynaecological malignancy'
    ),
    final=(
        'CERVIX OR OVARY OR UTERUS OR VULVA'
    ),
    rationale=(
        "Same defect and reasoning as 'gynaecological cancer'."
    ),
    approved='2026-08-05, user',
))

# was: 'CERVIX OR OVARY OR UTERUS OR VULVA'
_add(Adjudication(
    value=(
        'gynaecological cancers receiving adjuvant radiotherapy'
    ),
    final=(
        'CERVIX OR OVARY OR UTERUS OR VULVA'
    ),
    rationale=(
        "Same defect as 'gynaecological cancer'; the radiotherapy qualifier is not a tumour-type restriction."
    ),
    approved='2026-08-05, user',
))

# was: 'CERVIX OR OVARY OR UTERUS OR VULVA'
_add(Adjudication(
    value=(
        'FIGO stage I-IVA recurrent gynaecological cancer AND NOT(significant LVSI or pelvic sidewall '
        'invasion)'
    ),
    final=(
        'CERVIX OR OVARY OR UTERUS OR VULVA'
    ),
    rationale=(
        "Same defect as 'gynaecological cancer'; FIGO stage and LVSI are not tumour-type restrictions."
    ),
    approved='2026-08-05, user',
))

# was: '(CERVIX OR OVARY OR UTERUS OR VULVA) AND NOT(UCEC)'
_add(Adjudication(
    value=(
        'histologically-proven recurrent/metastatic other gynaecological cancers AND NOT(endometrial cancers)'
    ),
    final=(
        '(CERVIX OR OVARY OR UTERUS OR VULVA) AND NOT(UCEC)'
    ),
    rationale=(
        "Same broadening as 'gynaecological cancer'. The endometrial exclusion is stated in the source "
        'and UCEC sits under UTERUS, so it is a real (non-vacuous) exclusion and is kept.'
    ),
    approved='2026-08-05, user',
))

# was: '(BOWEL OR STOMACH) AND NOT(GINET OR GINETES)'
_add(Adjudication(
    value=(
        'UGI/LGI cancer AND NOT(neuroendocrine cancer)'
    ),
    final=(
        '(BOWEL OR STOMACH) AND NOT(GINET OR GINETES)'
    ),
    rationale=(
        'B1 broadened the two GI organ codes to the pan-solid sentinel while fixing only OR-branch '
        "ordering (issues_fixed='syn_or_order_noncanonical') — a cosmetic repair that rewrote the "
        'substance.'
    ),
    approved='2026-08-05, user',
))

# was: 'BONE'
_add(Adjudication(
    value=(
        'spine cancer'
    ),
    final=(
        'BONE'
    ),
    rationale=(
        'B1 broadened BONE to the pan-solid sentinel while fixing no defect.'
    ),
    approved='2026-08-05, user',
))

# was: 'EMBT OR ES OR RCSNOS'
_add(Adjudication(
    value=(
        'refractory/relapsed/progressive high-risk solid tumour: other embryonal small round blue cell '
        'tumours including paediatric type (bone) sarcoma AND NOT(low-grade gliomas) AND NOT(tumours of '
        'unknown malignant potential)'
    ),
    final=(
        'EMBT OR ES OR RCSNOS'
    ),
    rationale=(
        'B1 broadened three precise codes to the pan-solid sentinel, claiming to fix '
        "log_vacuous_exclusion — but it 'fixed' the vacuous NOT(LGGNOS) by widening the POSITIVE term, "
        'which is not what that check asks for. The low-grade-glioma exclusion is genuinely vacuous '
        'against these three codes, so canonical form drops it.'
    ),
    approved='2026-08-05, user',
))

# was: 'GINET'
_add(Adjudication(
    value=(
        'advanced and/or metastatic, unresectable gastrointestinal histologically proven (WHO/ENET) Grade '
        '3 neuroendocrine carcinoma, including Mixed AdenoneuroEndocrine Carcinomas (MANEC) with G3 '
        'elements AND NOT(NECs confirmed not to be from gastrointestinal primaries) AND NOT(Grade 1 and '
        'Grade 2 NETs (Ki-67 <=20%)) AND NOT(suspected pulmonary origin of the NET)'
    ),
    final=(
        'GINET'
    ),
    rationale=(
        'The only value in the corpus that stage-2 RECONCILIATION (not the mapper) broadened to a '
        'sentinel. NOT(LNET) is vacuous against a GI neuroendocrine tumour, so canonical form drops it. '
        'Orphan map entry: 0 export rows, corrected for hygiene.'
    ),
    approved='2026-08-05, user',
))

# was: 'EMBT OR ES OR RCSNOS'
_add(Adjudication(
    value=(
        'refractory/relapsed/progressive high-risk other embryonal small round blue cell tumors including '
        'pediatric type (bone) sarcoma AND NOT(low-grade gliomas or tumors of unknown malignant '
        'potential)'
    ),
    final=(
        'EMBT OR ES OR RCSNOS'
    ),
    rationale=(
        'Sibling of the ACTRN12619001768189 value; here the sentinel predates B1, so it is a '
        'long-standing defect rather than a regression. Same correction.'
    ),
    approved='2026-08-05, user',
))

# was: 'PEMESO OR PLMESO'
_add(Adjudication(
    value=(
        'locally advanced or metastatic malignant mesothelioma (MM)'
    ),
    final=(
        'PEMESO OR PLMESO'
    ),
    rationale=(
        'Mesothelioma mapped to the pan-solid sentinel. OncoTree has no site-agnostic mesothelioma node, '
        'so the two clinically relevant sites are enumerated; testicular mesothelioma is vanishingly rare '
        'and omitted.'
    ),
    approved='2026-08-05, user',
))

# was: 'BGCT OR EGCT OR NSGCT OR OGCT OR SEM OR VGCT'
_add(Adjudication(
    value=(
        'Recurrent Malignant Germ Cell Tumor'
    ),
    final=(
        'BGCT OR EGCT OR NSGCT OR OGCT OR SEM OR VGCT'
    ),
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

# was: 'BGCT OR EGCT OR NSGCT OR OGCT OR SEM OR VGCT'
_add(Adjudication(
    value=(
        'Refractory Malignant Germ Cell Tumor'
    ),
    final=(
        'BGCT OR EGCT OR NSGCT OR OGCT OR SEM OR VGCT'
    ),
    rationale=(
        "Same value and reasoning as its 'Recurrent' twin."
    ),
    approved='2026-08-05, user',
))

# was: 'BMT'
_add(Adjudication(
    value=(
        'mature teratoma'
    ),
    final=(
        'BMT'
    ),
    rationale=(
        'A named OncoTree entity (Mature Teratoma) mapped to the pan-solid sentinel.'
    ),
    approved='2026-08-05, user',
))

# was: 'BIMT'
_add(Adjudication(
    value=(
        'immature teratoma'
    ),
    final=(
        'BIMT'
    ),
    rationale=(
        'A named OncoTree entity (Immature Teratoma) mapped to the pan-solid sentinel.'
    ),
    approved='2026-08-05, user',
))

# was: 'HCC'
_add(Adjudication(
    value=(
        'Advanced Solid Tumors Cancer AND hepatocellular carcinoma (HCC)'
    ),
    final=(
        'HCC'
    ),
    rationale=(
        "The source names HCC as the cohort's tumour type; the sentinel drops it."
    ),
    approved='2026-08-05, user',
))

# was: 'HCC'
_add(Adjudication(
    value=(
        'Advanced Solid Tumors Cancer AND HCC'
    ),
    final=(
        'HCC'
    ),
    rationale=(
        "Same as the spelled-out 'hepatocellular carcinoma' twin."
    ),
    approved='2026-08-05, user',
))

# was: 'BLCA'
_add(Adjudication(
    value=(
        'high grade non-muscle invasive bladder cancer (HR-NMIBC), high-grade Ta AND NOT(muscle invasive, '
        'locally advanced, nonresectable, or metastatic urothelial carcinoma [>=T2]) AND NOT(urothelial '
        'carcinoma or histological variant at any site outside of the urinary bladder, except Ta/any '
        'T1/CIS of the upper urinary tract treated with complete nephroureterectomy >24 months prior to '
        'randomization) AND NOT(tumors involving the prostatic urethra)'
    ),
    final=(
        'BLCA'
    ),
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

# was: 'BLCA'
_add(Adjudication(
    value=(
        'high grade non-muscle invasive bladder cancer (HR-NMIBC), any T1 AND NOT(muscle invasive, '
        'locally advanced, nonresectable, or metastatic urothelial carcinoma [>=T2]) AND NOT(urothelial '
        'carcinoma or histological variant at any site outside of the urinary bladder, except Ta/any '
        'T1/CIS of the upper urinary tract treated with complete nephroureterectomy >24 months prior to '
        'randomization) AND NOT(tumors involving the prostatic urethra)'
    ),
    final=(
        'BLCA'
    ),
    rationale=(
        'Same trial and defect as the high-grade Ta cohort.'
    ),
    approved='2026-08-05, user',
))

# was: 'BLCA'
_add(Adjudication(
    value=(
        'high grade non-muscle invasive bladder cancer (HR-NMIBC), carcinoma in-situ (CIS) AND NOT(muscle '
        'invasive, locally advanced, nonresectable, or metastatic urothelial carcinoma [>=T2]) AND '
        'NOT(urothelial carcinoma or histological variant at any site outside of the urinary bladder, '
        'except Ta/any T1/CIS of the upper urinary tract treated with complete nephroureterectomy >24 '
        'months prior to randomization) AND NOT(tumors involving the prostatic urethra)'
    ),
    final=(
        'BLCA'
    ),
    rationale=(
        'Same trial and defect as the high-grade Ta cohort.'
    ),
    approved='2026-08-05, user',
))

# was: 'BREAST AND NOT(DCIS OR IBC)'
_add(Adjudication(
    value=(
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
    final=(
        'BREAST AND NOT(DCIS OR IBC)'
    ),
    rationale=(
        "Both exclusions are stated in the source ('or ductal carcinoma in situ', 'including inflammatory "
        "carcinoma') and DCIS and IBC are both BREAST descendants, so neither exclusion is vacuous."
    ),
    approved='2026-08-05, user',
))

# was: 'AML AND NOT(AMLBCRABL1 OR APLPMLRARA)'
_add(Adjudication(
    value=(
        'Acute Myeloid Leukemia (AML) AND NOT(acute promyelocytic leukemia) AND NOT(known active central '
        'nervous system (CNS) involvement with AML) AND NOT(history of myeloproliferative neoplasm (MPN) '
        'including myelofibrosis, essential thrombocythemia, polycythemia vera, chronic myeloid leukemia '
        '(CML) with or without BCR-ABL1 translocation and AML with BCR-ABL1 translocation)'
    ),
    final=(
        'AML AND NOT(AMLBCRABL1 OR APLPMLRARA)'
    ),
    rationale=(
        "The source explicitly excludes 'AML with BCR-ABL1 translocation'; AMLBCRABL1 is an AML "
        'descendant, so that exclusion is real and was wrongly dropped. MPN itself is disjoint from AML — '
        'that exclusion IS vacuous, so it is correctly left out.'
    ),
    approved='2026-08-05, user',
))

# was: 'Pan-cancer AND NOT(THYROID)'
_add(Adjudication(
    value=(
        'Neoplasms AND NOT(symptomatic or untreated central nervous system (CNS) metastases) AND '
        'NOT(signs, symptoms or history of thyroid tumors)'
    ),
    final=(
        'Pan-cancer AND NOT(THYROID)'
    ),
    rationale=(
        "The source excludes 'signs, symptoms or history of thyroid tumors'. THYROID is a Pan-cancer "
        'descendant, so the exclusion is meaningful and was wrongly dropped.'
    ),
    approved='2026-08-05, user',
))

# was: 'PBT'
_add(Adjudication(
    value=(
        'previously histologically or cytologically confirmed relapsed, refractory, or recurrent '
        'pediatric malignant brain tumor with no standard treatment options with curative potential'
    ),
    final=(
        'PBT'
    ),
    rationale=(
        "Mapped to MT ('Malignant Tumor'), an OncoTree catch-all bucket, for a source that says brain "
        'tumour. MT was missing from checks.CATCHALL_NODES, which is why lex_catchall_node (error) never '
        'fired; that gap is fixed alongside this ruling.'
    ),
    approved='2026-08-05, user',
))

# was: 'NHL AND NOT(BL OR BLL11Q OR CLLSLL OR HHV8DLBCL OR LYG OR PLBL OR THRLBCL OR WM)'
_add(Adjudication(
    value=(
        'transformation of indolent lymphoma AND NOT(Human herpes virus (HHV) 8-positive DLBCL OR T '
        'cell/histiocyte-rich large B-cell lymphoma OR Burkitt and high-grade B-cell lymphoma with 11q '
        "aberrations (previously Burkitt-like lymphoma) OR Richter's transformation OR Lymphomatoid "
        "granulomatosis OR Plasmablastic lymphoma OR Waldenstrom's Macroglobulinemia)"
    ),
    final=(
        'NHL AND NOT(BL OR BLL11Q OR CLLSLL OR HHV8DLBCL OR LYG OR PLBL OR THRLBCL OR WM)'
    ),
    rationale=(
        "Two codes were dropped from an explicitly enumerated exclusion list: BL ('Burkitt and high-grade "
        "B-cell lymphoma with 11q aberrations') and CLLSLL (Richter's transformation is by definition "
        'CLL-derived). Both are NHL descendants, so both exclusions are real.'
    ),
    approved='2026-08-05, user',
))

# was: 'DMG'
_add(Adjudication(
    value=(
        'inoperable radiologically diagnosed diffuse midline glioma that is diffuse intrinsic pontine glioma'
    ),
    final=(
        'DMG'
    ),
    rationale=(
        "ADOPTED 2026-08-05 from the reconcile churn rather than reverted. The source says 'diffuse "
        "midline glioma' verbatim; OncoTree has no DIPG node, and DMG ('Diffuse Midline Glioma, H3 "
        "K27-Altered') is the only node naming that entity, so it is strictly more faithful than the "
        "grandparent DIFG. It also matches how this trial's SIBLING value ('...in a supratentorial tumour "
        "location') already maps. Pinned because a narrowing is not blocked by the anti-broadening guard "
        'and would otherwise churn on every reconcile.'
    ),
    approved='2026-08-05, user',
))

# was: 'ASTR OR ODG'
_add(Adjudication(
    value=(
        'previously histologically confirmed WHO grade 2 or grade 3 glioma (astrocytoma or '
        'oligodendroglioma) clinically and/or radiologically behaving as first or subsequent recurrent '
        'grade 4 glioma in a supratentorial tumour location'
    ),
    final=(
        'ASTR2 OR ASTR3 OR ODG2 OR ODG3'
    ),
    rationale=(
        'Group adjudication collapses this to `ASTR OR ODG` on EVERY reconcile, discarding the grade the '
        "source states verbatim ('WHO grade 2 or grade 3'). It is an ancestor-broadening, which the R6 "
        'guard deliberately allows through (that shape is also how a genuine over-specification is '
        'repaired — see expr.broadening), so the only durable fix is to pin it. B1 had already produced '
        'the graded set; this restores it. Orphan map entry as of the 2026-08-05 refresh (its trial '
        'expired), so there is no export impact — pinned to stop the recurring drift WARN and to keep the '
        'value correct if the trial returns.'
    ),
    approved='2026-08-05, user',
))

# was: 'Solid tumour AND NOT(BREAST OR STAD)'
_add(Adjudication(
    value=(
        'pathologically confirmed advanced and/or metastatic solid cancer of any histologic type AND '
        'NOT(primary tumour histology HER2 amplified breast adenocarcinoma or gastric adenocarcinoma)'
    ),
    final=(
        'Solid tumour AND NOT(BREAST OR STAD)'
    ),
    rationale=(
        "The source excludes 'HER2 amplified breast adenocarcinoma or gastric adenocarcinoma'. The "
        'gastric half was kept and the breast half dropped — internally inconsistent. Both are qualified '
        'by HER2 status, so either both go or both stay; kept, matching the retained STAD.'
    ),
    approved='2026-08-05, user',
))


# =========================================================================== #
# STAGE-1 PROMPT-ITERATION RESIDUE (2026-08-05). The candidate cancer_type prompt was iterated four times
# against the 28 July and 4 August baselines; each round fixed 5-8 defects and surfaced 4-5 elsewhere, so the
# 11 below are where prompt work stopped paying. Every one is a one-off protocol wording, not a class: the
# coexistence idiom, a single grade bound, four dropped exclusions the rule already states twice. Adding a rule
# for any of them would be fitting the prompt to this corpus.
# Record: data/agentic/analysis/cancer_type_stage1_review/three_way_review.tsv (filter fix_stage_* == stage1).
# =========================================================================== #
# was: 'DLBCLNOS'   (candidate prompt, v6)
_add(Adjudication(
    value=(
        'DLBCL coexistent with follicular lymphoma (FL) of any grade AND NOT(any other histological '
        'type of lymphoma according to WHO 2016 classification of lymphoid neoplasms, e.g., primary '
        "mediastinal (thymic) large B-cell lymphoma; Burkitt's lymphoma; BCL, unclassifiable, with "
        'features intermediate between DLBCL and classical Hodgkin lymphoma (grey-zone lymphoma); '
        'primary effusion lymphoma; primary cutaneous DLBCL, leg type; primary DLBCL of the CNS; '
        'DLBCL arising from CLL or indolent lymphoma)'
    ),
    final='DLBCLNOS OR FL',
    rationale=(
        "'coexistent with follicular lymphoma of any grade' names both components, and the vocabulary "
        "carries one tumour type per patient, so they are OR'd. The prompt drops FL; the coexistence "
        'idiom is too rare to teach without overfitting.'
    ),
    approved='2026-08-05, user-authorised residue — final PENDING verification',
))

# was: 'MEL'   (candidate prompt, v6)
_add(Adjudication(
    value=(
        'Histologically or cytologically proven cutaneous in-transit melanoma metastases with '
        'cutaneous (superficial) macular, papular or small nodular in-transit melanoma deposits AND '
        'NOT(uncontrolled central nervous system metastases)'
    ),
    final='SKCM',
    rationale=(
        'CUTANEOUS melanoma is stated, so SKCM is the histology node. The prompt answers MEL, whose '
        'descendants include mucosal and acral disease the source excludes by saying cutaneous.'
    ),
    approved='2026-08-05, user-authorised residue — final PENDING verification',
))

# was: 'CERVIX AND NOT(CELI OR CENE OR CERMS)'   (candidate prompt, v6)
_add(Adjudication(
    value=(
        'advanced/metastatic cervical cancer AND NOT(primary neuroendocrine, mesenchymal, '
        'sarcomatoid, or other histologies not mentioned as part of the inclusion criteria)'
    ),
    final='CERVIX AND NOT(CELI OR CENE OR CERMS OR SCCE)',
    rationale=(
        'The stated neuroendocrine exclusion covers small cell carcinoma of the cervix (SCCE), a '
        'CERVIX descendant. The prompt maps three of the four carve-outs and drops SCCE.'
    ),
    approved='2026-08-05, user-authorised residue — final PENDING verification',
))

# was: 'Pan-cancer AND NOT(COADREAD OR NSCLC)'   (candidate prompt, v6)
_add(Adjudication(
    value=(
        'earlier diagnosis of a poor prognosis cancer AND NOT(non-small cell lung cancer (NSCLC)) AND '
        'NOT(colorectal cancer) AND NOT(non-central nervous system (CNS) cancer with symptomatic CNS '
        'involvement unless stable neurological function, stable steroid/anti-epileptic doses over 4 '
        'weeks, and no CNS progression within 12 weeks prior to screening)'
    ),
    final='',   # NB `''` is a LEGITIMATE ruling here — the value states no CURRENT tumour type at all
    rationale=(
        "'EARLIER diagnosis' is a prior malignancy, which is out of scope for this field — the "
        'governing principle is the cancer the patient CURRENTLY has. Empty is correct; the prompt '
        'maps the historical cancer.'
    ),
    approved='2026-08-05, user-authorised residue — final PENDING verification',
))

# was: 'CHOL OR GBAD'   (candidate prompt, v6)
_add(Adjudication(
    value=(
        'histologically confirmed adenocarcinoma of the biliary tract, including intra-hepatic or '
        'extra-hepatic cholangiocarcinoma (CCA) and gallbladder carcinoma (GBC); unresectable locally '
        'advanced or metastatic BTC AND NOT(ampullary carcinoma)'
    ),
    final='EHCH OR GBAD OR IHCH',
    rationale=(
        'The source states ADENOCARCINOMA and names intra-hepatic, extra-hepatic and gallbladder '
        'sites, so all three nodes belong. The prompt answers CHOL OR GBAD, collapsing the '
        'intra/extra-hepatic distinction the source draws.'
    ),
    approved='2026-08-05, user-authorised residue — final PENDING verification',
))

# was: 'ASTR2 OR ASTR3 OR ASTR4 OR HGGNOS OR LGGNOS OR ODG2 OR ODG3'   (candidate prompt, v6)
_add(Adjudication(
    value=(
        'histologically confirmed glioma, histologically grade 2 or 3 at initial diagnosis (without '
        'necrosis or microvascular proliferation; including CDKN2A/B homozygous deleted IDH-mutant '
        'astrocytomas) AND NOT(IDH-wildtype diffuse astrocytomas with any of TERT promoter mutation, '
        'EGFR amplification and/or +7/-10 copy number changes) AND NOT(metastatic tumours at the time '
        'of craniotomy that are not consistent with original glioma diagnosis)'
    ),
    final='DIFG AND NOT(GB)',
    rationale=(
        "'grade 2 or 3 at initial diagnosis (without necrosis...)' states an upper bound. P14 "
        'correctly reads grade as an axis but enumerates every graded node including ASTR4, '
        'contradicting the source. `DIFG AND NOT(GB)` expresses the bound directly and is what 28 '
        'July had.'
    ),
    approved='2026-08-05, user-authorised residue — final PENDING verification',
))

# was: 'Haematological malignancy'   (candidate prompt, v6)
_add(Adjudication(
    value=(
        'other haematological malignancy with significant risk of relapse (allogeneic transplant '
        'indicated) AND NOT(refractory Central Nervous System (CNS) disease) AND NOT(refractory '
        'lymphoma)'
    ),
    final='Haematological malignancy AND NOT(HL OR NHL)',
    rationale=(
        "'OTHER haematological malignancy' carries a stated lymphoma carve-out; the prompt drops it, "
        'leaving the bare sentinel which admits the lymphomas the source excludes.'
    ),
    approved='2026-08-05, user-authorised residue — final PENDING verification',
))

# was: 'Haematological malignancy AND NOT(APLPMLRARA OR MS)'   (candidate prompt, v6)
_add(Adjudication(
    value=(
        'other hematological malignancies AND NOT(acute promyelocytic leukemia with t(15;17)(q22;q12) '
        'or abnormal promyelocytic leukemia/retinoic acid receptor alpha (APML-RARA)) AND NOT(MDS '
        'with fibrosis (MDS-f)) AND NOT(leukemic meningitis or known active central nervous system '
        'disease) AND NOT(extra-medullary disease or myeloid sarcoma alone with no morphologic '
        'hematologic relapse)'
    ),
    final='Haematological malignancy AND NOT(APLPMLRARA OR MDS OR MS)',
    rationale=(
        'MDS and myeloid sarcoma are both stated exclusions and both Haematological malignancy '
        'descendants. The prompt recovers MS but still drops MDS.'
    ),
    approved='2026-08-05, user-authorised residue — final PENDING verification',
))

# was: 'OVARY AND NOT(BTBOV OR CCBOV OR EBOV OR MBOV OR OCNOS OR OGCT OR OSMBT OR SBOV OR SCST)'   (candidate prompt, v6)
_add(Adjudication(
    value=(
        'recurrent epithelial fallopian tube cancer AND NOT(borderline ovarian tumor) AND '
        'NOT(non-epithelial histology) AND NOT(mixed histology including borderline or non-epithelial '
        'histology)'
    ),
    final='OVT AND NOT(BTBOV OR CCBOV OR EBOV OR MBOV OR SBMOV OR SBOV)',
    rationale=(
        'EPITHELIAL histology is stated, so OVT is the node; the prompt moves up to the OVARY organ '
        'node, which is P9 applied backwards, and rewrites the borderline carve-out list at the same '
        'time.'
    ),
    approved='2026-08-05, user-authorised residue — final PENDING verification',
))

# was: '(HDCN OR NHL OR Solid tumour) AND NOT(NFIB)'   (candidate prompt, v6)
_add(Adjudication(
    value=(
        'recurrent or refractory advanced solid tumors, non-Hodgkin lymphomas, or histiocytic '
        'disorders (including CNS tumors) AND NOT(Hodgkin lymphoma) AND NOT(plexiform neurofibroma)'
    ),
    final='(HDCN OR NHL OR Solid tumour) AND NOT(HL OR NFIB)',
    rationale=(
        "Hodgkin lymphoma is a stated exclusion sitting under the positive NHL arm's scope. The "
        'prompt drops HL and substitutes NFIB.'
    ),
    approved='2026-08-05, user-authorised residue — final PENDING verification',
))

# was: ''   (candidate prompt, v6)
_add(Adjudication(
    value='specific indications AND NOT(primary central nervous system (CNS) malignancies)',
    final='Pan-cancer AND NOT(PBT)',
    rationale=(
        "'specific indications, excluding primary CNS malignancies' is a cancer cohort with a real "
        "carve-out; the prompt returns empty, discarding it. P13's narrowing is right in general but "
        'over-fires on this wording.'
    ),
    approved='2026-08-05, user-authorised residue — final PENDING verification',
))


# =========================================================================== #
# STAGE-2 CONSISTENCY RESIDUE (2026-08-06). Stage 2 was specced as DETERMINISTIC-ONLY — algebraic normalisation
# plus vacuous-exclusion removal — because making it a pure function of a single value renders cross-value churn
# structurally impossible. That leaves the cross-value consistency job unowned: 20 divergent groups existed, the
# deterministic pass dissolved 8 as pure OR-ordering noise, and these 12 survived.
#
# They are held here TEMPORARILY. Eleven of the twelve are stage 1 answering the same question two ways on a
# qualifier variant ('Recurrent X' vs 'Refractory X', 'Stage III' vs 'Stage IV'), so the root fix is a stage-1
# rule that a disease-state qualifier never changes the answer. When that lands, the review harness will report
# these as retirable (user, 2026-08-06: 'it's temporary. we will go back to stage 1 and fix it at the root').
# =========================================================================== #
# was: 'ES'
_add(Adjudication(
    value='Recurrent Ewing Sarcoma',
    final='ES OR ESST',
    rationale=(
        "'Ewing sarcoma' with no site stated covers both the bone node (ES) and the soft-tissue node "
        '(ESST). Stage 1 answers ES for some qualifier variants and ES OR ESST for others; the '
        'site-agnostic reading is correct.'
    ),
    approved='2026-08-06, user',
))

# was: 'ES'
_add(Adjudication(
    value='Ewing Sarcoma',
    final='ES OR ESST',
    rationale=(
        "'Ewing sarcoma' with no site stated covers both the bone node (ES) and the soft-tissue node "
        '(ESST). Stage 1 answers ES for some qualifier variants and ES OR ESST for others; the '
        'site-agnostic reading is correct.'
    ),
    approved='2026-08-06, user',
))

# was: 'ES'
_add(Adjudication(
    value='Ewing sarcoma',
    final='ES OR ESST',
    rationale=(
        "'Ewing sarcoma' with no site stated covers both the bone node (ES) and the soft-tissue node "
        '(ESST). Stage 1 answers ES for some qualifier variants and ES OR ESST for others; the '
        'site-agnostic reading is correct.'
    ),
    approved='2026-08-06, user',
))

# was: 'OVARY'
_add(Adjudication(
    value=(
        'newly diagnosed, histologically confirmed, high risk advanced (FIGO Stage IV) high grade '
        'endometrioid fallopian-tube cancer; NOT(synchronous primary endometrial cancer unless stage '
        '<2 and ((<60 years old at diagnosis with stage IA or IB grade 1 or 2, or stage IA grade 3 '
        'endometrioid adenocarcinoma) OR (≥60 years old at diagnosis with Stage IA grade 1 or 2 '
        'endometrioid adenocarcinoma))); NOT(serous or clear cell adenocarcinoma or carcinosarcoma of '
        'the endometrium)'
    ),
    final='EOV',
    rationale=(
        'The source states ENDOMETRIOID histology, so the histology node applies rather than the '
        'Ovary/Fallopian Tube organ node.'
    ),
    approved='2026-08-06, user',
))

# was: 'ACML OR ALAL OR AML OR ANKL OR ATLL OR BLL OR BPLL OR CELNOS OR CLLSLL OR CML OR CMML OR CNL OR HCL OR JMML OR SBLU OR SMMCL OR TLGL OR TLL OR TPLL'
_add(Adjudication(
    value='Leukemia',
    final='Haematological malignancy',
    rationale=(
        "OncoTree has no 'leukaemia' node. Two stage-1 runs produced two different long enumerations "
        'that did not even agree on lymphoid vs myeloid membership, and an enumeration two runs '
        'cannot reproduce is worse than an admitted approximation. The broad term is honest about the '
        'imprecision (user, 2026-08-06).'
    ),
    approved='2026-08-06, user',
))

# was: 'ACML OR ALAL OR AML OR ANKL OR ATLL OR BLL OR BPLL OR CELNOS OR CLLSLL OR CML OR CMML OR CNL OR HCL OR HCL-V OR JMML OR SMMCL OR TLGL OR TLL OR TPLL'
_add(Adjudication(
    value='recurrent leukemia',
    final='Haematological malignancy',
    rationale=(
        "OncoTree has no 'leukaemia' node. Two stage-1 runs produced two different long enumerations "
        'that did not even agree on lymphoid vs myeloid membership, and an enumeration two runs '
        'cannot reproduce is worse than an admitted approximation. The broad term is honest about the '
        'imprecision (user, 2026-08-06).'
    ),
    approved='2026-08-06, user',
))

# was: 'GNOS'
_add(Adjudication(
    value='Refractory Malignant Glioma',
    final='HGGNOS',
    rationale=(
        "'Malignant glioma' is high-grade by definition, so HGGNOS is the faithful node; GNOS "
        '(Glioma, NOS) drops the grade the wording carries.'
    ),
    approved='2026-08-06, user',
))

# was: 'PEMESO OR PLMESO OR TMESO'
_add(Adjudication(
    value='metastatic malignant mesothelioma (including both pleural and non-pleural)',
    final='PEMESO OR PLMESO',
    rationale=(
        "Same as 'mesothelioma': the source's 'including both pleural and non-pleural' names exactly "
        'these two.'
    ),
    approved='2026-08-06, user',
))

# was: 'MEL'
_add(Adjudication(
    value='histologically confirmed Stage III (unresectable) melanoma AND NOT(ocular melanoma)',
    final='ARMM OR ESMM OR HNMUCM OR MEL OR PCNSM OR URMM OR VMM',
    rationale=(
        'One canonical non-ocular melanoma set applied to all three wordings (user, 2026-08-06). The '
        'families are structurally separate in OncoTree: MEL and its children are cutaneous (under '
        'SKIN); the five mucosal nodes each sit under their own organ and are NOT under MEL; the '
        "ocular nodes (OM and its children UM, CM) sit under EYE. So 'non-ocular' is expressed by the "
        "set's composition and needs no NOT() — an ocular exclusion would be vacuous and dropped "
        'anyway. Stage 1 answered these three wordings three different ways, one of them including CM '
        '(conjunctival melanoma), which IS ocular and self-contradicting.'
    ),
    approved='2026-08-06, user',
))

# was: 'PEMESO OR PLMESO OR TMESO'
_add(Adjudication(
    value='mesothelioma',
    final='PEMESO OR PLMESO',
    rationale=(
        'Mesothelioma has no site-agnostic OncoTree node, so the two clinically material sites are '
        'enumerated. Testicular mesothelioma (TMESO) is vanishingly rare and misrepresents the '
        'population (user, 2026-08-05).'
    ),
    approved='2026-08-06, user',
))

# was: 'MEL'
_add(Adjudication(
    value='non-uveal Stage III (unresectable) melanoma',
    final='ARMM OR ESMM OR HNMUCM OR MEL OR PCNSM OR URMM OR VMM',
    rationale=(
        'One canonical non-ocular melanoma set applied to all three wordings (user, 2026-08-06). The '
        'families are structurally separate in OncoTree: MEL and its children are cutaneous (under '
        'SKIN); the five mucosal nodes each sit under their own organ and are NOT under MEL; the '
        "ocular nodes (OM and its children UM, CM) sit under EYE. So 'non-ocular' is expressed by the "
        "set's composition and needs no NOT() — an ocular exclusion would be vacuous and dropped "
        'anyway. Stage 1 answered these three wordings three different ways, one of them including CM '
        '(conjunctival melanoma), which IS ocular and self-contradicting.'
    ),
    approved='2026-08-06, user',
))

# was: 'ARMM OR CM OR ESMM OR HNMUCM OR MEL OR PCNSM OR URMM OR VMM'
_add(Adjudication(
    value='non-uveal Stage IV (metastatic) melanoma',
    final='ARMM OR ESMM OR HNMUCM OR MEL OR PCNSM OR URMM OR VMM',
    rationale=(
        'One canonical non-ocular melanoma set applied to all three wordings (user, 2026-08-06). The '
        'families are structurally separate in OncoTree: MEL and its children are cutaneous (under '
        'SKIN); the five mucosal nodes each sit under their own organ and are NOT under MEL; the '
        "ocular nodes (OM and its children UM, CM) sit under EYE. So 'non-ocular' is expressed by the "
        "set's composition and needs no NOT() — an ocular exclusion would be vacuous and dropped "
        'anyway. Stage 1 answered these three wordings three different ways, one of them including CM '
        '(conjunctival melanoma), which IS ocular and self-contradicting.'
    ),
    approved='2026-08-06, user',
))

# was: '(ARMM OR ESMM OR HNMUCM OR MEL OR OM OR PCNSM OR URMM OR VMM) AND NOT(UM)'
_add(Adjudication(
    value='histologically confirmed unresectable or metastatic melanoma AND NOT(uveal melanoma)',
    final='ARMM OR ESMM OR HNMUCM OR MEL OR PCNSM OR URMM OR VMM',
    rationale=(
        'One canonical non-ocular melanoma set applied to all three wordings (user, 2026-08-06). The '
        'families are structurally separate in OncoTree: MEL and its children are cutaneous (under '
        'SKIN); the five mucosal nodes each sit under their own organ and are NOT under MEL; the '
        "ocular nodes (OM and its children UM, CM) sit under EYE. So 'non-ocular' is expressed by the "
        "set's composition and needs no NOT() — an ocular exclusion would be vacuous and dropped "
        'anyway. Stage 1 answered these three wordings three different ways, one of them including CM '
        '(conjunctival melanoma), which IS ocular and self-contradicting.'
    ),
    approved='2026-08-06, user',
))

# was: 'MEL'
_add(Adjudication(
    value='unresectable or metastatic melanoma AND NOT(uveal melanoma)',
    final='ARMM OR ESMM OR HNMUCM OR MEL OR PCNSM OR URMM OR VMM',
    rationale=(
        'One canonical non-ocular melanoma set applied to all three wordings (user, 2026-08-06). The '
        'families are structurally separate in OncoTree: MEL and its children are cutaneous (under '
        'SKIN); the five mucosal nodes each sit under their own organ and are NOT under MEL; the '
        "ocular nodes (OM and its children UM, CM) sit under EYE. So 'non-ocular' is expressed by the "
        "set's composition and needs no NOT() — an ocular exclusion would be vacuous and dropped "
        'anyway. Stage 1 answered these three wordings three different ways, one of them including CM '
        '(conjunctival melanoma), which IS ocular and self-contradicting.'
    ),
    approved='2026-08-06, user',
))

# was: 'Solid tumour AND NOT(BGCT OR EGCT OR NSGCT OR OGCT OR SEM OR VGCT)'
_add(Adjudication(
    value=(
        'histologically or cytologically confirmed unresectable solid tumor AND NOT(known brain or '
        'spinal metastases) AND NOT(current evidence of new or growing brain or spinal metastases '
        'during screening) AND NOT(pure βhCG-secreting germ cell tumor)'
    ),
    final='Solid tumour',
    rationale='Same value family and reasoning as its sibling arm in NCT04503278.',
    approved='2026-08-06, user',
))

# was: 'MRT'
_add(Adjudication(
    value='Recurrent Rhabdoid Tumor',
    final='ATRT OR MRT OR MRTL',
    rationale=(
        "'Rhabdoid tumor' with no site stated spans the CNS (ATRT), renal (MRT) and extra-renal "
        '(MRTL) nodes.'
    ),
    approved='2026-08-06, user',
))

# was: 'Solid tumour AND NOT(BGCT OR EGCT OR NSGCT OR OGCT OR SEM OR VGCT)'
_add(Adjudication(
    value=(
        'histologically confirmed unresectable solid tumor AND NOT(known brain or spinal metastases) '
        'AND NOT(current evidence of new or growing brain or spinal metastases during screening) AND '
        'NOT(pure βhCG-secreting germ cell tumor)'
    ),
    final='Solid tumour',
    rationale=(
        "The source excludes 'pure BhCG-secreting germ cell tumor' — a SECRETORY qualifier OncoTree "
        'cannot express. Excluding all germ-cell tumours over-excludes: it removes GCT patients who '
        'ARE eligible because their tumour is not pure BhCG-secreting. Per the locked rule, an '
        'over-exclusion invisibly denies a patient a trial they qualify for while an omitted '
        'exclusion stays recoverable, so the qualifier is omitted. NCT04503278.'
    ),
    approved='2026-08-06, user',
))
