"""MY per-value judgement on every mapping the candidate prompt CHANGED — improvement, regression or neutral.

Why this is data rather than prose (user, 2026-08-05): *"independently review each mapping yourself to determine
whether it is identical to the past run, an improvement, or a regression. Use judgement and not only an automated
check."* A judgement narrated in chat cannot be checked against the row it refers to; recorded here it lands in the
review TSV beside the four code columns, so every call is auditable and any disagreement with the automated
direction predicate is visible.

HOW I REVIEWED. The 253 changed values collapse into 169 distinct (old -> new) transitions, because the same
clinical pattern recurs (17 urothelial-carcinoma values make one transition, 9 DIPG values another). I judged each
TRANSITION against the source wording of its members and recorded it once — which is how a human reviewer would
work, and it keeps the judgement consistent across identical cases instead of drifting between rows.

VERDICTS
    IMPROVEMENT   a better reading of the source than the baseline's
    REGRESSION    a worse reading — must reach zero before sign-off
    NEUTRAL       both defensible; presentational, or an equally good alternative
    UNCERTAIN     needs domain input I do not have; treat as blocking until resolved
"""
from __future__ import annotations

#: (baseline_expr, new_expr) -> (verdict, reason). Keyed by TRANSITION, applied to every value sharing it.
TRANSITIONS: dict[tuple[str, str], tuple[str, str]] = {}


def _t(old: str, new: str, verdict: str, reason: str) -> None:
    TRANSITIONS[(old, new)] = (verdict, reason)


VERDICTS = ("IDENTICAL", "IMPROVEMENT", "REGRESSION", "NEUTRAL", "UNCERTAIN", "UNREVIEWED")

# =========================================================================== #
# UROTHELIAL / BLADDER — the histology-over-organ principle (P9/P10) working.
# =========================================================================== #
_t("BLADDER", "BLCA OR UCU OR UTUC", "IMPROVEMENT",
   "source states urothelial CARCINOMA; BLADDER is the 'Bladder/Urinary Tract' organ node, and the three codes are "
   "urothelial carcinoma at its bladder, urethral and upper-tract sites")
_t("BLADDER", "BLCA", "IMPROVEMENT",
   "NMIBC / CIS of the bladder with urothelial histology stated -> the histology node, not the organ bucket")

# =========================================================================== #
# OESOPHAGO-GASTRIC — P10: STOMACH is named 'Esophagus/Stomach', so it is wrong for either organ alone.
# =========================================================================== #
_t("STOMACH", "STAD", "IMPROVEMENT",
   "gastric-only source; STOMACH silently admits every oesophageal tumour. STAD mirrors the colorectal->COADREAD "
   "convention")
_t("STOMACH", "EPDCA OR ESCA OR ESCC OR HGNEE", "IMPROVEMENT",
   "oesophageal CARCINOMA stated; restores exactly the 28 July mapping")
_t("STOMACH", "EPDCA OR ESCA OR ESCC OR ESMM OR HGNEE", "NEUTRAL",
   "generic 'Esophageal Cancer' — better than STOMACH, but ESMM (mucosal melanoma) is an unusual inclusion for a "
   "source that names no histology; harmless breadth within the correct organ")
_t("STOMACH", "ESCC", "IMPROVEMENT",
   "squamous cell carcinoma of the gastro-oesophageal junction — histology and site both stated")
_t("STOMACH", "EPDCA OR ESCA OR ESCC OR GEJ OR STAD", "IMPROVEMENT",
   "GC/GEJC/LEC names all three sites; the enumeration covers exactly them")
_t("ESCA OR GEJ OR STAD", "EGC", "NEUTRAL",
   "EGC (Esophagogastric Cancer) is the parent covering exactly oesophagus+GEJ+stomach adenocarcinoma; equivalent "
   "and more compact")
_t("BOWEL", "COAD", "IMPROVEMENT", "colorectal adenocarcinoma stated; BOWEL is two levels broader")
_t("Solid tumour", "COADREAD", "IMPROVEMENT",
   "'cancer requiring elective colorectal surgery' names the organ; the sentinel admitted every tumour type")
_t("BILIARY_TRACT", "CHOL OR GBAD", "IMPROVEMENT", "biliary tract ADENOCARCINOMA stated -> the carcinoma nodes")
_t("BILIARY_TRACT", "EHCH OR GBAD OR IHCH", "IMPROVEMENT", "same: adenocarcinoma of the biliary tract")
_t("BILIARY_TRACT OR PANCREAS", "AMPULLA_OF_VATER OR BILIARY_TRACT OR PANCREAS", "IMPROVEMENT",
   "'pancreatobiliary' conventionally includes the ampulla")
_t("Solid tumour", "AMPULLA_OF_VATER OR EHCH OR PANCREAS OR SBC", "IMPROVEMENT",
   "periampullary resection cohort; the sentinel discarded the stated organ set")

# =========================================================================== #
# GLIOMA / CNS — P4+P8: a WHO entity name states its molecular qualifier, a description does not.
# =========================================================================== #
_t("DIFG", "PDIFHG", "IMPROVEMENT",
   "DIPG is the radiological picture with no entity name, so DMG would infer H3 K27; PDIFHG spans H3-altered and "
   "H3-wildtype, whereas DIFG also covers every adult glioma. Matches the WHO CNS5 family structure")
_t("DIFG AND NOT(ODG)", "PDIFHG", "IMPROVEMENT", "same DIPG reasoning; the ODG exclusion is vacuous under PDIFHG")
_t("HGGNOS", "PDIFHG", "IMPROVEMENT", "histologically verified high-grade DIPG -> the paediatric-type family node")
_t("ASTR", "DIFG", "IMPROVEMENT",
   "bare 'Astrocytoma' must not assert IDH-mutant, which is what ASTR means; DIFG is the honest ancestor")
_t("DIFG", "DMG", "IMPROVEMENT",
   "source writes 'diffuse midline glioma' — the WHO CNS5 entity name, so DMG transcribes rather than infers")
_t("GNOS", "HGGNOS", "IMPROVEMENT", "'Malignant Glioma' is high-grade by definition")
_t("HGGNOS", "GB", "IMPROVEMENT", "molecular features consistent with glioblastoma are stated")
_t("GNOS", "DIFG AND NOT(GB)", "IMPROVEMENT", "grade 2-3 diffuse glioma without necrosis: excludes grade 4")
_t("LGGNOS", "GNOS", "REGRESSION",
   "'Recurrent WHO Grade 2 Glioma' IS low-grade, so LGGNOS was the more faithful node; GNOS drops the grade")
_t("DIFG", "GNOS", "NEUTRAL", "'glioma' unqualified — both are unspecific-glioma nodes")
_t("DIFG", "GB OR HGGNOS", "IMPROVEMENT", "'malignant glioma including glioblastoma multiforme' names both")
_t("DIFG", "ODG", "IMPROVEMENT", "'Oligodendroglioma, Childhood' names the entity")
_t("BRAIN", "ENCG OR HGGNOS OR MNET", "IMPROVEMENT",
   "grade 3-4 CNS glioma or glioneuronal tumour — the organ node discarded both stated categories")
_t("ENCG OR MNET OR PDIFLG", "ENCG OR PDIFLG", "UNCERTAIN",
   "paediatric low-grade glioma/glioneuronal: dropping MNET narrows the stated 'glioneuronal tumour' arm — needs a "
   "neuro-oncology view on whether ENCG alone covers it")
_t("BRAIN AND NOT(ATRT OR BGCT OR CPC OR EPMT OR HGGNOS OR HGNET OR HPB OR MBL OR PBL OR PNET)", "GNBL",
   "IMPROVEMENT", "'CNS ganglioneuroblastoma' names one entity; the long carve-out was an attempt to reach it "
   "by subtraction")
_t("Solid tumour AND NOT(BRAIN)", "Solid tumour AND NOT(PBT)", "IMPROVEMENT",
   "the source excludes a PRIMARY CNS tumour, not the CNS as a site")
_t("Solid tumour AND NOT(PBT)", "Solid tumour AND NOT(BRAIN)", "REGRESSION", "the converse of the above, backwards")
_t("Solid tumour", "Solid tumour AND NOT(BRAIN)", "NEUTRAL",
   "'extra-cranial solid tumour' — the exclusion is faithful; scope of BRAIN vs PBT is arguable")
_t("Solid tumour", "Solid tumour AND NOT(PBT)", "IMPROVEMENT", "stated exclusion of primary CNS malignancy restored")
_t("Solid tumour AND NOT(HL OR NHL OR PBT)", "Solid tumour AND NOT(PBT)", "NEUTRAL",
   "lymphoma is not a solid tumour, so that half of the exclusion was vacuous")
_t("Pan-cancer AND NOT(APLPMLRARA OR GNOS)", "Pan-cancer AND NOT(APLPMLRARA OR DIFG OR ENCG)", "NEUTRAL",
   "'glioma' excluded — DIFG+ENCG is a wider reading of the word than GNOS; both defensible")
_t("Solid tumour AND NOT(DIFG OR ENCG)", "Solid tumour AND NOT(GNOS)", "NEUTRAL", "mirror of the above")

# =========================================================================== #
# GYNAECOLOGICAL — the epithelial/site axis. OVT is 'Ovarian Epithelial Tumor', PSEC 'Peritoneal Serous Carcinoma'.
# =========================================================================== #
_t("OVARY", "OVT", "IMPROVEMENT", "epithelial histology stated; OVARY is the 'Ovary/Fallopian Tube' organ node")
_t("OVARY", "EOV", "IMPROVEMENT", "endometrioid histology stated")
_t("PERITONEUM", "PSEC", "IMPROVEMENT", "epithelial/serous primary peritoneal carcinoma stated")
_t("PERITONEUM AND NOT(PEMESO)", "PSEC", "IMPROVEMENT",
   "same, and the mesothelioma carve-out becomes vacuous once the carcinoma node is used")
_t("LGSOC", "PSEC", "UNCERTAIN",
   "'peritoneal LGSOC' — PSEC gets the site right and loses the low-grade qualifier; LGSOC gets the grade right and "
   "the site wrong. Needs a call on which axis matters more")
_t("OVARY OR PERITONEUM", "OVARY OR PSEC", "IMPROVEMENT", "peritoneal half gains its carcinoma node")
_t("OVARY OR PERITONEUM", "OVT OR PERITONEUM", "IMPROVEMENT", "ovarian half gains its epithelial node")
_t("OVARY OR PERITONEUM", "HGSFT OR OVT OR PERITONEUM", "IMPROVEMENT",
   "high-grade epithelial ovarian/tubal/peritoneal — adds the fallopian-tube node the source names")
_t("OVT OR PERITONEUM", "OVT OR PSEC", "IMPROVEMENT", "as above")
_t("OVT OR PERITONEUM", "HGSFT OR OVT OR PSEC", "IMPROVEMENT", "adds the stated fallopian-tube arm")
_t("HGSFT OR OVT OR PERITONEUM", "HGSFT OR OVT OR PSEC", "IMPROVEMENT", "peritoneal carcinoma node")
_t("EOV", "EOV OR PERITONEUM", "IMPROVEMENT", "source says 'inclusive of primary peritoneal'")
_t("CCOV", "CCOV OR PERITONEUM", "IMPROVEMENT", "same")
_t("PSEC OR SOC", "HGSFT OR PSEC OR SOC", "IMPROVEMENT", "adds the stated fallopian-tube arm")
_t("OVARY AND NOT(BTBOV OR CCBOV OR EBOV OR MBOV OR OCS OR OSMBT OR SBOV)", "EOV", "IMPROVEMENT",
   "FIGO III/IV ENDOMETRIOID fallopian tube cancer: the histology is stated, so the carve-out list is unnecessary")
_t("LMS", "CELI OR ULMS", "IMPROVEMENT",
   "'gynaecological leiomyosarcoma' — the two gynaecological LMS sites, rather than the site-agnostic node")

# =========================================================================== #
# GERM CELL / TERATOMA — P5 site-first-then-enumerate. Several of these are the COG paediatric trial's arms, whose
# wording is unusually terse; I mark those UNCERTAIN rather than guess.
# =========================================================================== #
_t("PEMESO OR PLMESO OR TMESO", "PEMESO OR PLMESO", "IMPROVEMENT",
   "the live value carried testicular mesothelioma; the source says only 'mesothelioma'")
_t("OMGCT OR OYST", "OYST", "UNCERTAIN", "COG ovarian GCT arm — needs the protocol's own risk-stratum definition")
_t("OYST", "OMGCT OR OYST", "UNCERTAIN", "same trial, opposite direction")
_t("OCNOS OR OMGCT", "OCNOS", "UNCERTAIN", "same trial")
_t("TYST", "MGCT OR TYST", "UNCERTAIN", "same trial, testicular arm")
_t("EMBCA OR MGCT", "NSGCT AND NOT(GCTSTM OR TT)", "UNCERTAIN", "same trial")
_t("NSGCT AND NOT(GCTSTM OR TT)", "MGCT OR TCCA", "UNCERTAIN", "same trial")
_t("NSGCT AND NOT(GCTSTM OR TT)", "NSGCT AND NOT(GCTSTM)", "UNCERTAIN", "same trial")
_t("EGCT OR NSGCT OR OGCT", "EGCT OR NSGCT OR OCNOS OR OEC OR OIMT OR OMGCT OR OPE OR OYST", "NEUTRAL",
   "NSGCT of testis/retroperitoneum/ovary: expands the ovarian arm into its subtypes; more verbose, same population")
_t("SEM", "EGCT OR SEM", "IMPROVEMENT", "'pure seminoma, any primary site' — extragonadal sites included")
_t("EGCT OR SEM", "SEM", "REGRESSION", "'any primary site' is stated, so dropping the extragonadal node narrows it")

# =========================================================================== #
# SARCOMA / PAEDIATRIC SOLID
# =========================================================================== #
_t("ES", "ES OR ESST", "IMPROVEMENT",
   "'Ewing's sarcoma' with no site stated covers both the bone and soft-tissue nodes")
_t("ESST", "ES OR ESST", "IMPROVEMENT", "same, for the 'peripheral PNET' synonym")
_t("RCSNOS", "ES OR ESST", "IMPROVEMENT", "'Ewing's sarcoma-like tumour' is closer to the Ewing nodes than to "
                                          "round-cell-sarcoma-NOS")
_t("MRT", "ATRT OR MRT OR MRTL", "IMPROVEMENT", "rhabdoid tumour with no site stated: CNS, renal and extra-renal")
_t("MRT", "MRT OR MRTL", "IMPROVEMENT", "explicitly extra-CNS rhabdoid tumour, so ATRT is correctly absent")
_t("ERMS OR SCRMS OR SCSRMS", "ERMS OR SCSRMS", "REGRESSION",
   "source names spindle cell RMS explicitly ('botryoid-type embryonal, spindle cell, and sclerosing'), so SCRMS "
   "must stay")
_t("ERMS OR SCSRMS", "ERMS OR SCRMS OR SCSRMS", "IMPROVEMENT", "the same value seen from the other side — restores "
                                                               "the stated spindle-cell arm")
_t("MDEP OR PNET", "ETANTR OR MDEP OR PNET", "UNCERTAIN", "supratentorial PNET — whether ETANTR belongs needs the "
                                                          "protocol's histology list")
_t("ETANTR OR MDEP OR PNET", "MDEP OR PNET", "UNCERTAIN", "same value, opposite direction")
_t("Solid tumour AND NOT(UNEC OR USARC)", "CSNOS OR MCS OR OCS OR UCS", "IMPROVEMENT",
   "'recurrent/metastatic carcinosarcoma' names an entity; the sentinel-with-carve-outs reached it by subtraction")

# =========================================================================== #
# LYMPHOMA — the messiest family. MBN is 'Mature B-Cell Neoplasms', far broader than large B-cell lymphoma.
# =========================================================================== #
_LBCL = ("ALKLBCL OR BCLU OR DLBCLCI OR DLBCLNOS OR EBVDLBCLNOS OR HGBCL OR HGBCLMYCBCL2 OR HHV8DLBCL OR IVBCL "
         "OR LBLIRF4 OR PCLBCLLT OR PEL OR PLBL OR PMBL OR THRLBCL")
_t("MBN", _LBCL, "IMPROVEMENT",
   "source says LARGE B-cell lymphoma; MBN also admits CLL, MZL, mantle cell and myeloma. The enumeration is "
   "verbose but faithful — the verbosity is a stage-2 canonical-form concern, not a stage-1 error")
_t("MBN", "DLBCLNOS OR FL OR HGBCL OR PMBL", "IMPROVEMENT", "the four subtypes the source names")
_t("MBN", "MBN AND NOT(PCNSL)", "IMPROVEMENT", "restores a stated CNS-lymphoma exclusion")
_t("MBN", "LNM", "REGRESSION",
   "'Richter's transformation' is a transformation to an aggressive B-cell lymphoma; LNM (Lymphoid Neoplasm) is "
   "broader still and includes T-cell disease")
_t("DLBCLNOS", "MBN", "REGRESSION", "the source names DLBCL, which has its own node")
_t("DLBCLNOS OR FL OR HGBCL OR PMBL OR THRLBCL", "MBN AND NOT(BL OR CLLSLL OR LPL)", "REGRESSION",
   "the source ENUMERATES its subtypes; replacing them with a parent minus three exclusions admits types it never "
   "named")
_t("DLBCLNOS OR EBVDLBCLNOS OR THRLBCL", "MBN AND NOT(PCNSL)", "REGRESSION", "same: enumeration replaced by a parent")
_t("MBN AND NOT(BCLU OR BL OR PCLBCLLT OR PCNSL OR PEL OR PMBL)",
   "ALKLBCL OR DLBCLCI OR DLBCLNOS OR EBVDLBCLNOS OR HGBCL OR HHV8DLBCL OR IVBCL OR LBLIRF4 OR PLBL OR THRLBCL",
   "IMPROVEMENT", "positive enumeration of LBCL rather than the parent-minus-carve-outs form")
_t("MBN AND NOT(BCLU OR BL OR PCLBCLLT OR PEL OR PMBL)",
   "ALKLBCL OR DLBCLCI OR DLBCLNOS OR EBVDLBCLNOS OR HGBCL OR HHV8DLBCL OR IVBCL OR LBLIRF4 OR PCNSL OR PLBL OR "
   "THRLBCL", "IMPROVEMENT", "same")
_t("MBN AND NOT(BLL11Q OR HHV8DLBCL OR LYG OR PLBL OR THRLBCL OR WM)", _LBCL, "NEUTRAL",
   "both forms are defensible; the enumeration is positive rather than subtractive but drops the source's explicit "
   "carve-outs, which are then implicit in the list")
_t("MBN AND NOT(PCNSL)", _LBCL, "NEUTRAL", "as above — PCNSL is absent from the enumeration, so honoured implicitly")
_t("HL OR NHL", "LNM", "REGRESSION",
   "'recurrent lymphoma' — HL OR NHL covers lymphoma exactly, while LNM adds leukaemias and plasma-cell neoplasms")
_t("LNM", "HL OR NHL", "IMPROVEMENT", "the converse: 'malignant lymphoma' is HL OR NHL, not every lymphoid neoplasm")
_t("DLBCLNOS OR FL", "DLBCLNOS", "REGRESSION",
   "'DLBCL coexistent with follicular lymphoma of any grade' names both components")
_t("DLBCLNOS OR EMALT", "DLBCLNOS", "REGRESSION", "'DLBCL coexistent with gastric MALT lymphoma' names both")
_t("DLBCLNOS", "DLBCLNOS OR PCNSL", "UNCERTAIN",
   "DLBCL 'including Richter's transformation' — whether primary CNS lymphoma belongs needs the protocol")
_t("HGBCL", "HGBCL OR HGBCLMYCBCL2", "IMPROVEMENT", "HGBL with no qualifier covers the MYC/BCL2 double-hit node")
_t("DLBCLNOS OR HGBCL", "DLBCLNOS OR HGBCL OR HGBCLMYCBCL2", "IMPROVEMENT", "same")
_t("EMALT OR NMZL OR SMZL", "MZL", "NEUTRAL",
   "MZL's children are exactly nodal, splenic and extranodal MZL, so the parent is equivalent and more compact")
_t("NPTLTFH", "NPTLTFH OR PTCL", "IMPROVEMENT", "the source names PTCL as well as the Tfh subtype")
_t("MTNN", "MYCF OR PCAECTCL OR PCATCL OR PCGDTCL OR PCLPD OR PCSMTPLD OR SPTCL", "IMPROVEMENT",
   "cutaneous T-cell lymphoma; MTNN (Mature T and NK Neoplasms) is far broader than the cutaneous entities")
_t("BLL OR MBN", "(BLL OR MBN) AND NOT(PCNSL)", "IMPROVEMENT", "restores a stated CNS/vitreoretinal exclusion")
_t("MBN AND NOT(BL)", "MBN AND NOT(BL OR BLL11Q OR LPL)", "IMPROVEMENT",
   "source excludes Burkitt-LIKE lymphoma and lymphoplasmacytic lymphoma as well as Burkitt")
_t("MBN AND NOT(DLBCLNOS OR MCL OR MZL OR PCNSL OR WM)", "MBN AND NOT(DLBCLNOS OR FL OR MCL OR MZL OR PCNSL OR WM)",
   "IMPROVEMENT", "the stated FL grade 1-3a exclusion was missing")
_t("(HL OR NHL) AND NOT(BPLL OR EP OR PCM OR SPB OR TPLL)",
   "(HL OR NHL) AND NOT(BPLL OR EP OR MGUS OR MIDD OR PCM OR SPB OR TPLL)", "IMPROVEMENT",
   "adds two stated plasma-cell-disorder exclusions")
_t("MBN AND NOT(ALKLBCL OR BCLU OR BL OR BPLL OR DLBCLCI OR EBVDLBCLNOS OR HGBCL OR HGBCLMYCBCL2 OR HHV8DLBCL OR "
   "IVBCL OR LBLIRF4 OR PCLBCLLT OR PEL OR PLBL OR PMBL OR THRLBCL)", "DLBCLNOS AND NOT(GCB)", "UNCERTAIN",
   "Richter's transformation with a non-GCB restriction — plausible but the subtraction form it replaces is unusual")
_t("MBN AND NOT(ALKLBCL OR BCLU OR BL OR HHV8DLBCL OR IVBCL OR LYG OR PCLBCLLT OR PEL OR PLBL)",
   "DLBCLCI OR DLBCLNOS OR EBVDLBCLNOS OR HGBCL OR LBLIRF4 OR PCLBCLLT OR PMBL OR THRLBCL", "NEUTRAL",
   "subtractive form rewritten positively; PCLBCLLT appears on both sides, which is inconsistent but not a "
   "population change")
_t("MBN AND NOT(ALKLBCL OR BCLU OR BL OR BLL11Q OR DLBCLCI OR IVBCL OR LYG OR PCLBCLLT OR PEL OR PLBL)",
   "DLBCLCI OR DLBCLNOS OR EBVDLBCLNOS OR HGBCL OR HHV8DLBCL OR PCLBCLLT OR PMBL OR THRLBCL", "NEUTRAL", "as above")
_t("MBN AND NOT(ALKLBCL OR BL OR DLBCLCI OR EBVDLBCLNOS OR HHV8DLBCL OR IVBCL OR LYG OR PCLBCLLT OR PEL OR PLBL)",
   "BCLU OR DLBCLNOS OR HGBCL OR LBLIRF4 OR PMBL OR THRLBCL", "NEUTRAL", "as above")
_t("DLBCLCI OR DLBCLNOS OR EBVDLBCLNOS OR HHV8DLBCL OR IVBCL OR PCLBCLLT OR PEL OR PLBL OR THRLBCL",
   "ALKLBCL OR DLBCLCI OR DLBCLNOS OR EBVDLBCLNOS OR HHV8DLBCL OR IVBCL OR PCLBCLLT OR PEL OR PLBL OR THRLBCL",
   "IMPROVEMENT", "'all subtypes of DLBCL' — ALK-positive LBCL was missing")
_t("ALKLBCL OR DLBCLCI OR DLBCLNOS OR EBVDLBCLNOS OR HGBCL OR HHV8DLBCL OR IVBCL OR LBLIRF4 OR PCLBCLLT OR PEL OR "
   "PLBL OR PMBL OR THRLBCL",
   "ALKLBCL OR DLBCLCI OR DLBCLNOS OR EBVDLBCLNOS OR HGBCL OR HGBCLMYCBCL2 OR HHV8DLBCL OR IVBCL OR LBLIRF4 OR "
   "PCLBCLLT OR PEL OR PLBL OR PMBL OR THRLBCL", "IMPROVEMENT", "WHO 2022 LBCL includes the double-hit node")
_t("ALKLBCL OR DLBCLCI OR DLBCLNOS OR EBVDLBCLNOS OR FL OR HGBCL OR HGBCLMYCBCL2 OR HHV8DLBCL OR IVBCL OR LBLIRF4 "
   "OR PCLBCLLT OR PEL OR PMBL OR THRLBCL",
   "ALKLBCL OR BCLU OR DLBCLCI OR DLBCLNOS OR EBVDLBCLNOS OR FL OR HGBCL OR HGBCLMYCBCL2 OR HHV8DLBCL OR IVBCL OR "
   "LBLIRF4 OR PCLBCLLT OR PEL OR PMBL OR THRLBCL", "IMPROVEMENT", "adds BCLU (B-cell lymphoma, unclassifiable)")
_t("ALKLBCL OR BCLU OR DLBCLCI OR DLBCLNOS OR HGBCL OR HGBCLMYCBCL2 OR HHV8DLBCL OR IVBCL OR LBLIRF4 OR PCLBCLLT "
   "OR PEL OR PLBL OR PMBL OR THRLBCL", _LBCL, "IMPROVEMENT", "adds EBV-positive DLBCL, NOS")

# =========================================================================== #
# MYELOID / LEUKAEMIA
# =========================================================================== #
_t("MPN", "ETMF OR PMF OR PVMF", "IMPROVEMENT",
   "myelofibrosis by WHO criteria — the three fibrosis entities, not every myeloproliferative neoplasm")
_t("PMF", "ETMF OR PMF OR PVMF", "IMPROVEMENT", "'MF' unqualified covers post-ET and post-PV myelofibrosis")
_t("PMFOFS OR PMFPES", "PMF", "NEUTRAL", "PMF's children are exactly prefibrotic and overt PMF; equivalent")
_t("AML", "AML AND NOT(MS)", "UNCERTAIN",
   "AML 'including AML arising from MDS' — whether myeloid sarcoma is excluded needs the protocol text")
_t("AML AND NOT(APLPMLRARA OR TAML)", "AML AND NOT(AMLMRC OR APLPMLRARA OR TAML)", "UNCERTAIN",
   "'primary AML per WHO 2016' arguably excludes AML-with-myelodysplasia-related-changes, but the source does not "
   "say so explicitly")
_t("BLL", "BLL AND NOT(BLLBCRABL1)", "IMPROVEMENT", "'Philadelphia-negative B-ALL' states the exclusion")
_t("MDS", "MDS AND NOT(MDSEB2)", "IMPROVEMENT", "'MDS excluding >10% marrow blasts' is MDS-EB2")
_t("ALAL OR AML OR BLL OR TLL", "AML OR BLL OR MPALBCRABL1 OR MPALBNOS OR MPALKMT2A OR MPALTNOS OR TLL", "NEUTRAL",
   "'acute leukaemia including biphenotypic' — ALAL expanded into its mixed-phenotype children; same population")
_t("Haematological malignancy",
   "ACML OR ALAL OR AML OR ANKL OR ATLL OR BLL OR BPLL OR CELNOS OR CLLSLL OR CML OR CMML OR ETPLL OR HCL OR "
   "LGLL OR MPALBCRABL1 OR MPALBNOS OR MPALKMT2A OR MPALTNOS OR TLL OR TPLL", "UNCERTAIN",
   "bare 'Leukemia': OncoTree has no leukaemia node, so the sentinel was a fair last resort. A 20-code enumeration "
   "is more precise but brittle and likely incomplete — worth a decision on which we prefer")
_t("Haematological malignancy AND NOT(APLPMLRARA OR MDS OR MS)", "Haematological malignancy AND NOT(APLPMLRARA)",
   "REGRESSION", "the MDS and myeloid-sarcoma exclusions are stated in the source and were dropped")
_t("TAM", "", "IMPROVEMENT",
   "'HISTORY of transient myeloproliferative disorder, not meeting criteria for AML' states no current tumour type "
   "— a prior-illness criterion, so empty is right")

# =========================================================================== #
# MELANOMA — MEL covers the cutaneous/acral children; the mucosal and uveal sites do not sit under it.
# =========================================================================== #
_t("MEL", "ARMM OR CM OR ESMM OR HNMUCM OR MEL OR PCNSM OR URMM OR VMM", "NEUTRAL",
   "'non-uveal melanoma' — enumerating the mucosal sites alongside MEL is more explicit but MEL already carries the "
   "cutaneous arm; redundant rather than wrong")
_t("MEL", "ARMM OR ESMM OR HNMUCM OR MEL OR PCNSM OR URMM OR VMM", "NEUTRAL", "as above, for 'non-ocular melanoma'")
_t("MEL", "(ARMM OR ESMM OR HNMUCM OR MEL OR OM OR PCNSM OR URMM OR VMM) AND NOT(UM)", "IMPROVEMENT",
   "'unresectable or metastatic melanoma excluding uveal' — the stated uveal exclusion is now expressed")
_t("MEL", "(ARMM OR ESMM OR HNMUCM OR MEL OR OM OR URMM OR VMM) AND NOT(UM)", "IMPROVEMENT", "as above")
_t("ACRM OR ARMM OR ESMM OR HNMUCM OR MUP OR SKCM OR UM OR URMM OR VMM",
   "ARMM OR ESMM OR HNMUCM OR MEL OR UM OR URMM OR VMM", "NEUTRAL",
   "ACRM, SKCM and MUP are all MEL descendants, so substituting the parent preserves the population and loses only "
   "the explicit naming")
_t("ARMM OR ESMM OR HNMUCM OR MEL OR UM OR URMM OR VMM",
   "ACRM OR ARMM OR ESMM OR HNMUCM OR MUP OR SKCM OR UM OR URMM OR VMM", "NEUTRAL", "the same pair, reversed")
_t("(ARMM OR ESMM OR HNMUCM OR MEL OR OM OR PCNSMT OR URMM OR VMM) AND NOT(AN)",
   "(AN OR ARMM OR ESMM OR HNMUCM OR MEL OR OM OR PCNSMT OR URMM OR VMM) AND NOT(AN)", "REGRESSION",
   "AN (atypical naevus) is now both asserted and excluded — an internal contradiction the deterministic checks do "
   "not catch because the OR-branch and the NOT are at different levels")

# =========================================================================== #
# NEUROENDOCRINE — grade is the discriminating axis and the source usually states it.
# =========================================================================== #
_t("GINET", "GINET OR GINETES", "IMPROVEMENT", "'gastroenteric origin' includes the oesophago-gastric NET node")
_t("GINET", "AWDNET OR SBWDNET", "UNCERTAIN", "'mid-gut NET' — appendiceal+small-bowel is a reasonable reading of "
                                              "mid-gut, but narrower than the GI NET node")
_t("NETNOS", "(GINET OR GINETES OR LNET OR NETNOS OR TNET) AND NOT(HGNEC OR HGNEE OR HGNES)", "IMPROVEMENT",
   "well-differentiated grade 1-3 extra-pulmonary NET: the grade restriction and the sites are both stated")
_t("GINET OR GINETES OR PANET", "(GINET OR GINETES OR PANET) AND NOT(HGNEC OR HGNEE OR HGNES)", "IMPROVEMENT",
   "'well differentiated' excludes the high-grade carcinoma nodes")
_t("GINET OR GINETES OR PANET",
   "(GINET AND NOT(HGNEC)) OR (GINETES AND NOT(HGNEE OR HGNES)) OR PANET AND NOT(PANEC)", "NEUTRAL",
   "same intent, distributed per site; the per-branch form is harder to read but not less faithful")
_t("GINET OR GINETES OR PANET", "AWDNET OR PANET OR RWDNET OR SBWDNET OR SWDNET", "UNCERTAIN",
   "GEP-NET expanded into named well-differentiated sites; whether this covers the stated scope needs a check")
_t("NECNOS", "CENE OR HGNEC OR HGNEE OR HGNES OR HGONEC OR HNNE OR NECNOS OR PANEC", "NEUTRAL",
   "'poorly differentiated extrapulmonary NEC' — enumerating the sites is more explicit, same population")
_t("CUP AND NOT(NECNOS OR NETNOS)", "CUP AND NOT(NECNOS)", "REGRESSION",
   "'excluding neuroendocrine cancer' covers tumours as well as carcinomas; dropping NETNOS narrows the exclusion")

# =========================================================================== #
# THYROID / PROSTATE / LUNG / OTHER
# =========================================================================== #
_t("THPD OR WDTC", "THHC OR THPD OR WDTC", "IMPROVEMENT",
   "differentiated thyroid cancer conventionally includes the Hurthle-cell node")
_t("WDTC", "THHC OR WDTC", "IMPROVEMENT", "same")
_t("PROSTATE", "PROSTATE AND NOT(PRNE)", "IMPROVEMENT", "'excluding majority neuroendocrine differentiation' stated")
_t("PROSTATE", "PROSTATE AND NOT(PRNE OR PRSCC)", "IMPROVEMENT", "same, plus the stated small-cell/squamous carve-out")
_t("PROSTATE AND NOT(PRSCC)", "PROSTATE AND NOT(PRNE OR PRSCC)", "IMPROVEMENT", "adds the stated neuroendocrine arm")
_t("PROSTATE AND NOT(PRNE OR PRSCC)", "PROSTATE AND NOT(PRSCC)", "REGRESSION", "drops a stated exclusion")
_t("PRAD AND (PRNE OR PRSCC)", "PRNE OR PRSCC", "IMPROVEMENT",
   "the live value was unsatisfiable — PRAD ANDed with its disjoint siblings")
_t("NSCLC AND NOT(LUSC)", "NSCLC AND NOT(LUAS OR LUSC)", "IMPROVEMENT",
   "'non-squamous NSCLC excluding mixed histology' — adenosquamous is the mixed entity")
_t("NSCLC AND NOT(LUAS OR LUSC)", "NSCLC AND NOT(LUSC)", "REGRESSION", "the converse: drops adenosquamous")
_t("SCLC", "CSCLC OR SCLC", "IMPROVEMENT", "'if mixed histology' — combined SCLC is the mixed node")
_t("SCCNOS", "ANSC OR BLSC OR CESC OR CSCC OR ESCC OR HNSC OR LUSC OR MSCC OR PRSCC OR VSC", "NEUTRAL",
   "'advanced squamous cell cancers' — the site enumeration is more actionable than SCC-NOS but risks omission")
_t("HPHSC OR LXSC OR OCSC OR OPHSC", "HPHSC OR LXSC OR NPC OR OCSC OR OPHSC", "UNCERTAIN",
   "HNSCC of 'oral cavity, pharynx, larynx' — pharynx arguably includes nasopharynx, but NPC is a distinct entity "
   "usually excluded from HNSCC trials")
_t("Solid tumour AND NOT(CUP)", "Solid tumour AND NOT(BREAST OR CUP OR OVARY)", "UNCERTAIN",
   "two extra exclusions appear; needs the source text to confirm they are stated")
_t("Solid tumour", "Solid tumour AND NOT(CHOL OR HCC)", "IMPROVEMENT",
   "'secondary liver malignancy excluding HCC/cholangiocarcinoma' states both")
_t("Solid tumour", "Solid tumour AND NOT(NSCLC)", "IMPROVEMENT", "stated exclusion restored")
_t("Solid tumour", "Solid tumour AND NOT(BGCT OR EGCT OR NSGCT OR OGCT OR SEM OR VGCT)", "UNCERTAIN",
   "a germ-cell exclusion appears where the live value had none; plausible but needs the source")
_t("Solid tumour AND NOT(BREAST OR PROSTATE)", "Solid tumour", "REGRESSION",
   "the source excludes advanced prostate and breast carcinoma explicitly; both are Solid tumour descendants")
_t("Solid tumour AND NOT(HL OR NHL)", "Solid tumour", "NEUTRAL", "lymphoma is not a solid tumour: the exclusion was "
                                                                 "vacuous either way")
_t("Solid tumour AND NOT(ALCL)", "Solid tumour", "NEUTRAL", "same — ALCL is not a solid tumour")
_t("Solid tumour AND NOT(ALAL OR AML OR BLL OR TLL)", "Solid tumour", "NEUTRAL", "same — acute leukaemia")
_t("Solid tumour", "Pan-cancer", "UNCERTAIN",
   "'rare tumor types' — whether the basket spans haematological disease decides this")
_t("Pan-cancer", "Solid tumour", "UNCERTAIN", "'other tumor types' — the mirror of the above")
_t("Solid tumour", "", "REGRESSION",
   "'additional tumor type' with a CNS-metastasis exclusion still describes a solid-tumour cohort; empty loses it")
_t("Pan-cancer AND NOT(PBT)", "", "REGRESSION",
   "'specific indications, excluding primary CNS malignancies' is a cancer cohort; empty discards it")
_t("", "Pan-cancer", "IMPROVEMENT",
   "'metastatic malignant neoplasm in the central nervous system' is a cancer, so empty was wrong")
_t("(HDCN OR NHL OR Solid tumour) AND NOT(HL OR NFIB)", "(HDCN OR NHL OR Solid tumour) AND NOT(NFIB)", "REGRESSION",
   "Hodgkin lymphoma is excluded in the source and HL is an NHL-sibling under the positive scope's lymphoma arm")

# =========================================================================== #
# GYNAE EXCLUSION-LIST CHURN — the borderline-tumour carve-out lists. These differ by one or two codes in both
# directions and none changes the population meaningfully; the source lists are long and partially inexpressible.
# =========================================================================== #
for _old, _new in (
    ("OVT AND NOT(BTBOV OR BTMOV OR CCBOV OR EBOV OR MBOV OR MOV OR OSMBT OR SBOV)",
     "OVT AND NOT(BTBOV OR BTMOV OR CCBOV OR EBOV OR MBOV OR MOV OR OSMBT OR SBMOV OR SBOV)"),
    ("OVT AND NOT(BTBOV OR CCBOV OR EBOV OR MBOV OR OSMBT OR SBOV)",
     "OVT AND NOT(BTBOV OR CCBOV OR EBOV OR MBOV OR OSMBT OR SBMOV OR SBOV)"),
    ("OVT AND NOT(BTBOV OR CCBOV OR EBOV OR MBOV OR SBMOV OR SBOV)",
     "OVT AND NOT(BTBOV OR CCBOV OR EBOV OR MBOV OR OSMBT OR SBOV)"),
    ("OVT AND NOT(BTBOV OR CCBOV OR EBOV OR MBOV OR MOV OR OSMBT OR SBMOV OR SBOV)",
     "OVT AND NOT(BTBOV OR CCBOV OR EBOV OR MBOV OR MOV OR OSMBT OR SBOV)"),
):
    _t(_old, _new, "NEUTRAL",
       "borderline/non-epithelial ovarian carve-out list differing by one code; the source names these as a prose "
       "category, so the exact membership is a stage-2 consistency question rather than a stage-1 reading error")


def for_transition(old: str, new: str) -> tuple[str, str]:
    """`(verdict, reason)`; `UNREVIEWED` when a changed transition has no recorded judgement."""
    return TRANSITIONS.get((old, new), ("UNREVIEWED", ""))


# =========================================================================== #
# EXACT-KEYED judgements for the long-expression transitions. Keyed by cancer_type VALUE rather than by the
# expression pair, because those expressions run to 200+ characters and hand-transcribing them (which is what I did
# first) mis-keys them silently. Same judgements, exact keys.
# =========================================================================== #
BY_VALUE: dict[str, tuple[str, str]] = {}


def _v(value: str, verdict: str, reason: str) -> None:
    BY_VALUE[value] = (verdict, reason)


def for_value_or_transition(value: str, old: str, new: str) -> tuple[str, str]:
    """Per-value judgement first, then the transition-level one."""
    if value in BY_VALUE:
        return BY_VALUE[value]
    return TRANSITIONS.get((old, new), ("UNREVIEWED", ""))

_v('Biopsy-proven relapsed or treatment-refractory large B-cell lymphoma, including large B-cell lymphoma of this subtype transformed from follicular or marginal zone lymphoma AND NOT(active CNS involvement by lymphoma) AND NOT(Richter transformation of chronic lymphocytic leukaemia; T-cell/histiocyte rich LBCL; primary LBCL of immune-privileged sites; fluid overload associated LBCL; fibrin-associated LBCL; plasmablastic lymphoma; mediastinal grey zone lymphoma; intravascular LBCL; ALK-positive large B-cell lymphoma; lymphomatoid granulomatosis; Burkitt lymphoma; primary effusion lymphoma; KSHV/HHV8-positive diffuse large B-cell lymphoma)',
   'NEUTRAL',
   'subtractive MBN-minus-carve-outs rewritten as a positive LBCL enumeration; same population, clearer form')
_v('CNS ganglioneuroblastoma AND NOT(CNS atypical teratoid/rhabdoid tumor (AT/RT); ependymomas including anaplastic ependymomas of the brain or spinal cord; choroid plexus carcinomas; high-grade glial and glio-neuronal tumors; primary CNS germ cell tumors; primary CNS sarcomas; primary or metastatic CNS lymphomas; solid leukemic lesions; unbiopsied diffuse intrinsic pontine tumors)',
   'IMPROVEMENT',
   "'CNS ganglioneuroblastoma' names one entity; the long carve-out reached it by subtraction")
_v("Large B-cell lymphoma (LBCL) AND NOT(Human herpes virus (HHV) 8-positive DLBCL OR T cell/histiocyte-rich large B-cell lymphoma OR Burkitt and high-grade B-cell lymphoma with 11q aberrations (previously Burkitt-like lymphoma) OR Richter's transformation OR Lymphomatoid granulomatosis OR Plasmablastic lymphoma OR Waldenstrom's Macroglobulinemia)",
   'NEUTRAL',
   'subtractive MBN-minus-carve-outs rewritten as a positive LBCL enumeration; same population, clearer form')
_v('Leukemia',
   'UNCERTAIN',
   "bare 'Leukemia': OncoTree has no leukaemia node, so the sentinel was a fair last resort. A 20-code enumeration is more precise but brittle and likely incomplete — worth an explicit decision on which we prefer")
_v('Persistent or recurrent histologically confirmed non-high-grade serous, non-high-grade endometrioid epithelial ovarian, fallopian tube, or primary peritoneal cancer, not amenable to curative surgery AND NOT(high-grade serous or high-grade endometrioid ovarian, fallopian tube, or primary peritoneal cancer) AND NOT(solely borderline epithelial ovarian tumor) AND NOT(non-epithelial ovarian tumors)',
   'IMPROVEMENT',
   'epithelial/serous primary peritoneal carcinoma stated')
_v("R/R DLBCL (including all subtypes of DLBCL) AND NOT(current CNS involvement by lymphoma/leukemia) AND NOT(plasma cell neoplasm) AND NOT(prolymphocytic leukemia) AND NOT(Richter's syndrome)",
   'IMPROVEMENT',
   "adds an LBCL subtype the source's 'all subtypes / per WHO' wording covers")
_v('Ultra-High Risk Large B-Cell Lymphoma meeting MRD criteria: end-of-treatment PET at C6D15 demonstrates complete metabolic response AND NOT(mediastinal grey zone lymphoma) AND NOT(primary mediastinal (thymic) large B-cell lymphoma) AND NOT(Burkitt lymphoma) AND NOT(primary large B-cell lymphoma of immune-privileged sites) AND NOT(primary effusion Diffuse Large B Cell lymphoma) AND NOT(primary cutaneous DLBCL, leg type) AND NOT(Post-Transplant Lymphoproliferative Disease) AND NOT(primary or secondary CNS lymphoma at recruitment) AND NOT(progressive disease at any point during first-line immunochemotherapy) AND NOT(rapid disease progression during screening/apheresis unlikely to be controlled using permissible bridging options) AND NOT(development and presence of detectable CNS lymphoma since enrolment in the NHL34 CLARIFY-Prognostic Platform)',
   'NEUTRAL',
   'subtractive MBN-minus-carve-outs rewritten as a positive LBCL enumeration; same population, clearer form')
_v('Ultra-High Risk Large B-Cell Lymphoma meeting MRD criteria: end-of-treatment PET at C6D15 demonstrates partial metabolic response AND NOT(mediastinal grey zone lymphoma) AND NOT(primary mediastinal (thymic) large B-cell lymphoma) AND NOT(Burkitt lymphoma) AND NOT(primary large B-cell lymphoma of immune-privileged sites) AND NOT(primary effusion Diffuse Large B Cell lymphoma) AND NOT(primary cutaneous DLBCL, leg type) AND NOT(Post-Transplant Lymphoproliferative Disease) AND NOT(primary or secondary CNS lymphoma at recruitment) AND NOT(progressive disease at any point during first-line immunochemotherapy) AND NOT(rapid disease progression during screening/apheresis unlikely to be controlled using permissible bridging options) AND NOT(development and presence of detectable CNS lymphoma since enrolment in the NHL34 CLARIFY-Prognostic Platform)',
   'NEUTRAL',
   'subtractive MBN-minus-carve-outs rewritten as a positive LBCL enumeration; same population, clearer form')
_v('Ultra-High Risk Large B-Cell Lymphoma meeting PET criteria: interim PET at C4D15 demonstrates stable disease AND NOT(mediastinal grey zone lymphoma) AND NOT(primary mediastinal (thymic) large B-cell lymphoma) AND NOT(Burkitt lymphoma) AND NOT(primary large B-cell lymphoma of immune-privileged sites) AND NOT(primary effusion Diffuse Large B Cell lymphoma) AND NOT(primary cutaneous DLBCL, leg type) AND NOT(Post-Transplant Lymphoproliferative Disease) AND NOT(primary or secondary CNS lymphoma at recruitment) AND NOT(progressive disease at any point during first-line immunochemotherapy) AND NOT(rapid disease progression during screening/apheresis unlikely to be controlled using permissible bridging options) AND NOT(development and presence of detectable CNS lymphoma since enrolment in the NHL34 CLARIFY-Prognostic Platform)',
   'NEUTRAL',
   'subtractive MBN-minus-carve-outs rewritten as a positive LBCL enumeration; same population, clearer form')
_v('advanced Squamous Cell Cancers AND NOT(symptomatic primary Central Nervous System (CNS) cancer or metastases unless symptoms are stable for at least 28 days prior to first dose and symptoms have returned to baseline)',
   'NEUTRAL',
   "'advanced squamous cell cancers' — a site enumeration is more actionable than SCC-NOS but risks omitting a site")
_v('biopsy proven advanced cutaneous T-cell lymphoma (CTCL) AND NOT(CNS involvement)',
   'IMPROVEMENT',
   'cutaneous T-cell lymphoma; MTNN (Mature T and NK Neoplasms) is far broader than the cutaneous entities the source names')
_v("histologically confirmed FIGO Stage III or IV platinum-sensitive epithelial ovarian carcinoma AND NOT(nonepithelial cancers [germ cell tumors and sex cord-stromal tumors]) AND NOT(low-grade serous tumors) AND NOT(low-grade endometrioid tumors) AND NOT(borderline tumors [low malignant potential]) AND NOT(mucinous) AND NOT(seromucinous that is predominantly mucinous) AND NOT(malignant Brenner's tumor) AND NOT(undifferentiated carcinoma) AND NOT(platinum-resistant OC) AND NOT(platinum-refractory OC)",
   'NEUTRAL',
   'borderline / non-epithelial ovarian carve-out list differing by one or two codes; the source states these as a prose category, so exact membership is a stage-2 consistency question rather than a stage-1 reading error')
_v("histologically confirmed epithelial ovarian carcinoma AND NOT(nonepithelial cancers OR borderline tumors OR mucinous OR seromucinous that is predominantly mucinous OR malignant Brenner's tumor OR undifferentiated carcinoma)",
   'NEUTRAL',
   'borderline / non-epithelial ovarian carve-out list differing by one or two codes; the source states these as a prose category, so exact membership is a stage-2 consistency question rather than a stage-1 reading error')
_v("histologically confirmed epithelial ovarian carcinoma of certain histologies AND NOT(nonepithelial cancers, low-grade serous tumors, low-grade endometrioid tumors, borderline tumors, mucinous, seromucinous that is predominantly mucinous, malignant Brenner's tumor, and undifferentiated carcinoma)",
   'NEUTRAL',
   'borderline / non-epithelial ovarian carve-out list differing by one or two codes; the source states these as a prose category, so exact membership is a stage-2 consistency question rather than a stage-1 reading error')
_v("histologically confirmed fallopian tube carcinoma of certain histologies AND NOT(nonepithelial cancers, low-grade serous tumors, low-grade endometrioid tumors, borderline tumors, mucinous, seromucinous that is predominantly mucinous, malignant Brenner's tumor, and undifferentiated carcinoma)",
   'IMPROVEMENT',
   "epithelial histology stated; OVARY is the 'Ovary/Fallopian Tube' organ node")
_v("large B-cell lymphoma (LBCL) per WHO 2022 AND NOT(mediastinal grey-zone lymphoma) AND NOT(Burkitt) AND NOT(Richter's transformation) AND NOT(primary effusion large B-cell lymphoma) AND NOT(Central nervous system (CNS) lymphoma)",
   'IMPROVEMENT',
   "adds an LBCL subtype the source's 'all subtypes / per WHO' wording covers")
_v('leukemia in continuous first complete remission',
   'UNCERTAIN',
   "bare 'Leukemia': OncoTree has no leukaemia node, so the sentinel was a fair last resort. A 20-code enumeration is more precise but brittle and likely incomplete — worth an explicit decision on which we prefer")
_v('locally advanced/unresectable or metastatic, well-differentiated Grade 1, 2, or 3 extra-pancreatic neuroendocrine tumor (epNET) AND NOT(neuroendocrine carcinoma including small cell lung cancer) AND NOT(medullary thyroid cancer) AND NOT(pheochromocytoma) AND NOT(paraganglioma) AND NOT(Merkel cell carcinoma) AND NOT(mixed neuroendocrine non-neuroendocrine neoplasm (MiNEN))',
   'IMPROVEMENT',
   'well-differentiated grade 1-3 extra-pulmonary NET: both the grade restriction and the sites are stated in the source')
_v('mature B-cell neoplasm: Large B-cell lymphoma AND NOT(active CNS involvement in lymphoma)',
   'IMPROVEMENT',
   'source says LARGE B-cell lymphoma; MBN also admits CLL, MZL, mantle cell and myeloma. Positive enumeration is faithful — its verbosity is a stage-2 canonical-form matter, not a stage-1 error')
_v("newly diagnosed adult 1L HR LBCL defined as IPI 3-5 AND NOT(Burkitt lymphoma) AND NOT(primary DLBCL of CNS) AND NOT(DLBCL associated with chronic inflammation) AND NOT(intravascular large B-cell lymphoma) AND NOT(ALK-positive large B-cell lymphoma) AND NOT(HHV8-positive LBCL) AND NOT(DLBCL leg type) AND NOT(EBV-positive DLBCL, NOS) AND NOT(active or prior history CNS involvement by malignancy) AND NOT(Richter's transformation) AND NOT(active CNS lymphoma)",
   'NEUTRAL',
   'subtractive MBN-minus-carve-outs rewritten as a positive LBCL enumeration; same population, clearer form')
_v('poorly differentiated unresectable locally advanced or metastatic extrapulmonary neuroendocrine carcinoma (epNEC) with mitotic rate >20 mitoses per 2 mm2, regardless of primary site (including site of unknown origin); mixed histologies eligible only if neuroendocrine carcinoma component is predominant and >70% of overall tumour tissue AND NOT(leptomeningeal disease) AND NOT(carcinomatous meningitis) AND NOT(Merkel cell carcinoma) AND NOT(medullary thyroid carcinoma) AND NOT(neuroendocrine prostate cancer) AND NOT(well-differentiated neuroendocrine tumours of any grade) AND NOT(history of well differentiated neuroendocrine tumour (NET) that transformed into poorly differentiated neuroendocrine carcinoma (NEC))',
   'NEUTRAL',
   "'poorly differentiated extrapulmonary NEC' — enumerating sites is more explicit, same population")
_v("previously untreated Large B-cell Lymphoma as per WHO-HEM5 (excluding plasmablastic lymphoma) and follicular large cell lymphoma; Stage I bulky (7.5 cm and greater) to Stage IV AND NOT(post-transplant lymphoproliferative disease) AND NOT(plasmablastic lymphoma) AND NOT(Richter's transformation) AND NOT(prior history of or concurrent indolent lymphoma, including de novo transformed or composite lymphoma)",
   'IMPROVEMENT',
   "adds an LBCL subtype the source's 'all subtypes / per WHO' wording covers")
_v('recurrent leukemia',
   'UNCERTAIN',
   "bare 'Leukemia': OncoTree has no leukaemia node, so the sentinel was a fair last resort. A 20-code enumeration is more precise but brittle and likely incomplete — worth an explicit decision on which we prefer")
_v('relapsed or refractory LBCL AND NOT(active central nervous system involvement) AND NOT(history of cardiac lymphoma involvement) AND NOT(Epstein-Barr virus (EBV)+ lymphoma)',
   'IMPROVEMENT',
   "adds an LBCL subtype the source's 'all subtypes / per WHO' wording covers")
_v("relapsed or refractory Richter's transformation (RT) AND NOT(current or history of CNS involvement by B-cell malignancy) AND NOT(known active plasma cell neoplasm; prolymphocytic leukemia; T-cell lymphoma; Burkitt lymphoma; AIDS-related B-cell lymphoma; Castleman disease; post-transplant lymphoproliferative disorders; hairy cell leukemia; germinal center B-cell (GCB) DLBCL; EBV+ DLBCL NOS; primary DLBCL of the central nervous system; primary cutaneous DLBCL - leg type; DLBCL associated with chronic inflammation; primary mediastinal (thymic) large B-cell lymphoma; intravascular large B-cell lymphoma; ALK+ large B-cell lymphoma; primary effusion lymphoma; high-grade B-cell lymphoma with MYC and BCL2 and/or BCL6 rearrangements; high-grade B-cell lymphoma NOS; B-cell lymphoma unclassifiable with features intermediate between DLBCL and classical Hodgkin lymphoma; non-eligible transformation of an indolent lymphoma to aggressive histology)",
   'UNCERTAIN',
   "Richter's transformation restricted to non-GCB DLBCL — plausible, but it replaces a long subtractive form and needs the protocol to confirm")
_v('relapsed or refractory large B-cell lymphoma (LBCL)',
   'IMPROVEMENT',
   'source says LARGE B-cell lymphoma; MBN also admits CLL, MZL, mantle cell and myeloma. Positive enumeration is faithful — its verbosity is a stage-2 canonical-form matter, not a stage-1 error')
_v("relapsed or refractory large B-cell lymphoma AND NOT(Richter's transformation of chronic leukemic lymphoma, small lymphocytic lymphoma, Burkitt lymphoma, lymphoplasmacytic lymphoma, T-cell/histiocyte-rich LBCL, mediastinal gray zone lymphoma, plasmablastic lymphoma, intravascular LBCL, primary central nervous system (CNS) lymphoma, primary vitreoretinal LBCL, fibrin-associated LBCL, fluid overload-associated LBCL lymphomatoid granulomatosis, high-grade B-cell lymphoma (HGBCL) with 11q aberrations, anaplastic lymphoma kinase-positive LBCL, LBCL with Interferon Regulatory Factor 4 (IRF4) rearrangement, transformed from Hodgkin's lymphoma) AND NOT(cardiac atrial or cardiac ventricular lymphoma involvement) AND NOT(secondary CNS lymphoma) AND NOT(full thickness lymphoma involvement of gastric or intestinal lining)",
   'NEUTRAL',
   'subtractive MBN-minus-carve-outs rewritten as a positive LBCL enumeration; same population, clearer form')
_v('relapsed/refractory other LBCL subtype with Medical Monitor approval AND NOT(active CNS disease other than PCNSL)',
   'IMPROVEMENT',
   'source says LARGE B-cell lymphoma; MBN also admits CLL, MZL, mantle cell and myeloma. Positive enumeration is faithful — its verbosity is a stage-2 canonical-form matter, not a stage-1 error')
_v('relapsed/refractory other large B-cell lymphoma (LBCL) subtype with Medical Monitor approval AND NOT(active CNS disease other than PCNSL)',
   'IMPROVEMENT',
   'source says LARGE B-cell lymphoma; MBN also admits CLL, MZL, mantle cell and myeloma. Positive enumeration is faithful — its verbosity is a stage-2 canonical-form matter, not a stage-1 error')
_v('unresectable, well-differentiated aggressive grade 2/3 GastroEnteroPancreatic NeuroEndocrine Tumors (GEP-NETs)',
   'NEUTRAL',
   'same grade restriction distributed per site; harder to read, no less faithful')

_v('advanced ovarian cancer AND NOT(abdominal adenocarcinoma of unknown origin) AND NOT(borderline ovarian tumor) AND NOT(carcinosarcomas (even if there is a serous component))',
   'NEUTRAL',
   'borderline-tumour carve-out list differing by one code (SBMOV); the source names these as a prose category, so exact membership is a stage-2 consistency question')
_v("histologically confirmed FIGO Stage III or IV platinum-sensitive epithelial fallopian tube carcinoma AND NOT(nonepithelial cancers [germ cell tumors and sex cord-stromal tumors]) AND NOT(low-grade serous tumors) AND NOT(low-grade endometrioid tumors) AND NOT(borderline tumors [low malignant potential]) AND NOT(mucinous) AND NOT(seromucinous that is predominantly mucinous) AND NOT(malignant Brenner's tumor) AND NOT(undifferentiated carcinoma) AND NOT(platinum-resistant OC) AND NOT(platinum-refractory OC)",
   'NEUTRAL',
   'same carve-out-list churn, on the fallopian-tube twin of the ovarian value')
_v('melanocytic tumor AND NOT(clearly benign nevi)',
   'REGRESSION',
   'AN (atypical naevus) is now both asserted as an OR-branch and excluded by the NOT — an internal contradiction the deterministic checks miss because the two sit at different levels of the expression')


# =========================================================================== #
# DERIVING THE 28-JULY VERDICT. 28 July's expressions predate canonicalisation, so most differ from 4 August only
# in FORM — unfactored `AND NOT(A) AND NOT(B)`, and vacuous exclusions (a CNS-metastasis `NOT(BRAIN)`) that stage 2
# later dropped. Where that is the only difference, the substantive change is identical and the 4 August verdict
# carries over. Where 28 July EXCLUDED a code that neither later version excludes, it held something real that was
# lost, and that needs judging in its own right — so it is flagged rather than inherited.
# =========================================================================== #
def negated_codes(expression: str) -> set[str]:
    """The codes an expression excludes. Used to detect an exclusion 28 July had and the new value does not."""
    from aus_trial_universe.tasks.eligibility.mapping.cancer_type.expr import atoms, classify, parse, ParseError
    if not (expression or "").strip():
        return set()
    try:
        node, table = parse(expression)
    except ParseError:
        return set()
    return {classify(a.text, table)[1] or a.text for a, neg in atoms(node) if neg}


def lost_exclusions(jul: str, new: str) -> set[str]:
    """Codes 28 July excluded that `new` does not — the substantive-loss signal."""
    return negated_codes(jul) - negated_codes(new)


def substantive_lost_exclusions(jul: str, new: str) -> set[str]:
    """Of the exclusions 28 July had and `new` lacks, only those that could actually have narrowed `new`.

    An exclusion is substantive only if the excluded code is the same as, or a DESCENDANT of, a positive code in the
    new expression — otherwise it could never have matched the population and dropping it changes nothing. This is
    the same reasoning the pipeline's own drop-vacuous rule uses, so a loss it deems vacuous is not a regression.
    Most of 28 July's apparent losses are of this kind: `WDTC AND NOT(THAP)` excluded anaplastic thyroid carcinoma,
    which was never inside well-differentiated thyroid cancer to begin with.
    """
    from aus_trial_universe.tasks.eligibility.mapping.cancer_type.expr import positive_codes
    from aus_trial_universe.tasks.eligibility.mapping.cancer_type.vocab import SENTINELS, is_subcode

    pos = positive_codes(new) or set()
    out = set()
    for code in lost_exclusions(jul, new):
        for p in pos:
            if p in SENTINELS or code == p or is_subcode(code, p):
                out.add(code)
                break
    return out


# =========================================================================== #
# The 9 values where 28 July excluded a DESCENDANT of the new positive term — a substantive loss, judged one by one.
# Keyed vs the 28 July expression so they do not collide with the 4 August judgements above.
# =========================================================================== #
VS_JUL: dict[str, tuple[str, str]] = {}


def _vj(value_prefix: str, verdict: str, reason: str) -> None:
    VS_JUL[value_prefix] = (verdict, reason)


_vj("Pathologically documented Stage IIIB", "REGRESSION",
    "'NON-SQUAMOUS NSCLC excluding mixed histology': LUAS (adenosquamous) is an NSCLC descendant and the stated "
    "mixed-histology carve-out. 28 July had it, both later runs lost it")
_vj("Solid Tumor AND NOT(acute leukemia)", "NEUTRAL",
    "acute leukaemia is not a solid tumour, so the exclusion was a no-op under the Solid tumour sentinel — the "
    "sentinel is treated as covering everything, which is why the descendancy test flags it")
_vj("advanced solid organ malignancy", "REGRESSION",
    "advanced prostate and breast carcinoma are both stated exclusions and both Solid tumour descendants; 28 July "
    "expressed both, the new run neither")
_vj("histologically confirmed metastatic or locally advanced solid tumor not amenable", "NEUTRAL",
    "Hodgkin and non-Hodgkin lymphoma are not solid tumours; the exclusion could not narrow the population")
_vj("lymphoma otherwise eligible for Part 1", "UNCERTAIN",
    "CLLSLL dropped while MGUS and MIDD were added. Given the source concerns transformation of an indolent "
    "lymphoma, whether CLL/SLL should be excluded needs the protocol text")
_vj("other hematological malignancies", "REGRESSION",
    "MDS and myeloid sarcoma are stated exclusions and Haematological malignancy descendants; only the APL "
    "exclusion survived")
_vj("recurrent epithelial ovarian cancer AND NOT(borderline ovarian tumor)", "NEUTRAL",
    "one code (OCS, ovarian carcinosarcoma) differs inside a long borderline/non-epithelial carve-out the source "
    "states as prose; exact membership is a stage-2 consistency question")
_vj("recurrent or refractory advanced solid tumors, non-Hodgkin lymphomas", "REGRESSION",
    "the source excludes Hodgkin lymphoma, which sits under the positive NHL arm's lymphoma scope; NFIB appeared in "
    "its place")
_vj("standard risk 1 testicular malignant germ cell tumor", "UNCERTAIN",
    "COG risk-stratum arm: TT (teratoma) excluded on 28 July and not now. Needs the protocol's own stratum "
    "definition, as with the other arms of this trial")


# =========================================================================== #
# FOR A REGRESSION: could STAGE 2 fix it, or is it genuinely stage 1's? (user, 2026-08-05)
# Stage 2 owns cross-value agreement and boolean/canonical-form manipulation. So a regression is stage-2-fixable
# when it is an INCONSISTENCY — the corpus contains the same transition in reverse, meaning two values that should
# agree do not, which group reconciliation exists to resolve — or when it is a purely FORMAL defect a deterministic
# rewrite can see. It is stage 1's when recovering the right answer needs the source value re-read: a dropped
# exclusion, or a wrong node choice with no disagreeing twin.
# =========================================================================== #
#: value prefix -> ("stage1"|"stage2", why). Hand-set where the reverse-transition test cannot decide.
FIX_STAGE: dict[str, tuple[str, str]] = {}


def _fs(value_prefix: str, stage: str, why: str) -> None:
    FIX_STAGE[value_prefix] = (stage, why)


_fs("melanocytic tumor", "stage2",
    "AN is both an OR-branch and inside the NOT() — a formal contradiction a deterministic check can catch and "
    "remove; it needs no re-reading of the source")
_fs("Recurrent WHO Grade 2 Glioma", "stage1",
    "GNOS vs LGGNOS is a node choice that depends on reading 'Grade 2' in the source")
_fs("DLBCL coexistent with", "stage1", "the dropped co-existent component (FL / gastric MALT) is only recoverable "
                                      "from the source wording")
_fs("advanced solid organ malignancy", "stage1", "two stated exclusions are absent; only the source shows they exist")
_fs("other hematological malignancies", "stage1", "same — MDS and myeloid sarcoma are stated in the source")
_fs("recurrent or refractory advanced solid tumors, non-Hodgkin", "stage1",
    "the Hodgkin exclusion is stated in the source and cannot be inferred from other mappings")
_fs("CUP AND NOT(neuroendocrine", "stage1", "the dropped NETNOS half of a stated exclusion needs the source")
_fs("additional tumor type", "stage1", "the mapping went empty; nothing downstream can recover a tumour type")
_fs("specific indications", "stage1", "same — an empty mapping cannot be repaired without the source")
_fs("advanced stage pure seminoma", "stage1", "'any primary site' is in the source; EGCT cannot be re-derived from "
                                              "other values")
_fs("large B-cell lymphomas (Diffuse large B-cell", "stage1",
    "the source's explicit subtype enumeration was replaced by a parent; only the source lists the subtypes")
_fs("relapsed/refractory DLBCL, as defined by the 2016 WHO", "stage1", "same")


def fix_stage(value: str, base: str, new: str, reverse_exists: bool) -> tuple[str, str]:
    """`(stage, why)` for a regression. `reverse_exists` is True when the corpus also contains new->base."""
    for pre, (stage, why) in FIX_STAGE.items():
        if value.startswith(pre):
            return stage, why
    if reverse_exists:
        return ("stage2", "the corpus maps an equivalent value the opposite way, so this is an INCONSISTENCY — "
                          "resolving disagreeing values to one answer is stage 2's job")
    return ("stage1", "recovering the right answer requires re-reading the source value, which is stage 1's "
                      "information set")


# --- v5 transitions, judged 2026-08-05 ---
_v('Adults (≥18 years) with Grade 1-4 primary CNS glioma or glioneuronal tumor AND unresectable, locally advanced or metastatic disease',
   'IMPROVEMENT',
   "adds a node the source's 'including / per WHO' wording covers, keeping the rest")
_v('Advanced Solid Tumors AND GC',
   'UNCERTAIN',
   'changed in a way I cannot adjudicate without the trial protocol')
_v("B-cell non-Hodgkin lymphoma (dose escalation: any subtype) AND NOT(large B-cell lymphomas) AND NOT(mantle cell lymphoma) AND NOT(post-transplant lymphoproliferative disease) AND NOT(Richter's transformation) AND NOT(Burkitt's lymphoma) AND NOT(chronic lymphocytic leukemia (CLL)/Small lymphocytic lymphoma (SLL)) AND NOT(Waldenstrom Macroglobulinemia/Lymphoplasmacytic Lymphoma) AND NOT(active CNS involvement from B-NHL)",
   'NEUTRAL',
   'subtractive MBN-minus-carve-outs rewritten as a positive enumeration; same population')
_v('Cervical Intraepithelial Neoplasia',
   'UNCERTAIN',
   'CIN is pre-malignant, not carcinoma — whether it belongs in cancer_type at all is a policy call')
_v('Colorectal cancer screening',
   'REGRESSION',
   'a SCREENING cohort states no cancer diagnosis; empty was correct and COADREAD asserts disease')
_v('Cutaneous T-cell lymphoma (CTCL, incl. MF or SS >=Stage IIB with B0/B1 blood involvement) AND NOT(untreated or progressive CNS disease; previously treated and stable allowed)',
   'IMPROVEMENT',
   'P12 working: a super-class node whose name admits families the source never named is replaced by the named entities')
_v('Giant Cell Astrocytoma',
   'UNCERTAIN',
   'changed in a way I cannot adjudicate without the trial protocol')
_v('Histologically or cytologically proven cutaneous in-transit melanoma metastases with cutaneous (superficial) macular, papular or small nodular in-transit melanoma deposits AND NOT(uncontrolled central nervous system metastases)',
   'REGRESSION',
   'CUTANEOUS melanoma is stated, so SKCM is the histology node; MEL is its parent and admits mucosal and acral disease')
_v('Large B-Cell Lymphoma AND NOT(history of indolent lymphoma, e.g., Follicular Lymphoma, Marginal Zone Lymphoma, Waldenstrom macroglobulinemia) AND NOT(current diagnosis of Follicular lymphoma grade 3B; transformations of indolent B-cell lymphomas; mediastinal grey zone lymphoma; primary mediastinal (thymic) large B-cell lymphoma; Burkitt lymphoma; primary large B-cell lymphoma of immune-privileged sites; primary effusion DLBCL; primary cutaneous DLBCL, leg type) AND NOT(primary or secondary CNS lymphoma at the time of recruitment or history of CNS lymphoma)',
   'NEUTRAL',
   'drops a branch; the remaining nodes still cover what the source names')
_v('Large B-cell lymphoma (LBCL) per WHO 2017 including diffuse large B-cell lymphoma, high-grade B-cell lymphoma, and primary mediastinal B-cell lymphoma AND NOT(LBCL with history of central nervous system involvement) AND NOT(LBCL transformed from other malignancy) AND NOT(T-cell/histiocyte rich LBCL)',
   'IMPROVEMENT',
   "adds a node the source's 'including / per WHO' wording covers, keeping the rest")
_v('Leukemia, B-cell',
   'IMPROVEMENT',
   'P12 working: a super-class node whose name admits families the source never named is replaced by the named entities')
_v('MDS with IPSS-R score > 3 and bone marrow blasts ≥ 5% AND NOT(known central nervous system [CNS] leukemia)',
   'UNCERTAIN',
   'changed in a way I cannot adjudicate without the trial protocol')
_v('Pediatric patients (10-17 years of age) with Grade 3 or 4 primary CNS glioma or glioneuronal tumor AND unresectable, locally advanced or metastatic disease',
   'IMPROVEMENT',
   'P12 working: a super-class node whose name admits families the source never named is replaced by the named entities')
_v("Relapsed/Refractory Large B-cell Lymphoma AND NOT(history of Richter's transformation of chronic leukemic lymphoma)",
   'IMPROVEMENT',
   'P12 working: the super-class node is replaced by the entities the source names')
_v("Richter's transformation",
   'IMPROVEMENT',
   "Richter's transforms to DLBCL or, less often, Hodgkin lymphoma — both named, replacing the MBN super-class")
_v('T-cell lymphoma',
   'IMPROVEMENT',
   'P12 working: a super-class node whose name admits families the source never named is replaced by the named entities')
_v('Ultra-High Risk Large B-Cell Lymphoma meeting PET criteria: interim PET at C4D15 demonstrates partial metabolic response with Deauville V AND NOT(mediastinal grey zone lymphoma) AND NOT(primary mediastinal (thymic) large B-cell lymphoma) AND NOT(Burkitt lymphoma) AND NOT(primary large B-cell lymphoma of immune-privileged sites) AND NOT(primary effusion Diffuse Large B Cell lymphoma) AND NOT(primary cutaneous DLBCL, leg type) AND NOT(Post-Transplant Lymphoproliferative Disease) AND NOT(primary or secondary CNS lymphoma at recruitment) AND NOT(progressive disease at any point during first-line immunochemotherapy) AND NOT(rapid disease progression during screening/apheresis unlikely to be controlled using permissible bridging options) AND NOT(development and presence of detectable CNS lymphoma since enrolment in the NHL34 CLARIFY-Prognostic Platform)',
   'NEUTRAL',
   'subtractive MBN-minus-carve-outs rewritten as a positive enumeration; same population')
_v('Ultra-High Risk Large B-Cell Lymphoma meeting PET criteria: interim PET at C4D15 demonstrates partial metabolic response with reduction in Standardized Uptake Value (SUV) of less than 70 percent AND NOT(mediastinal grey zone lymphoma) AND NOT(primary mediastinal (thymic) large B-cell lymphoma) AND NOT(Burkitt lymphoma) AND NOT(primary large B-cell lymphoma of immune-privileged sites) AND NOT(primary effusion Diffuse Large B Cell lymphoma) AND NOT(primary cutaneous DLBCL, leg type) AND NOT(Post-Transplant Lymphoproliferative Disease) AND NOT(primary or secondary CNS lymphoma at recruitment) AND NOT(progressive disease at any point during first-line immunochemotherapy) AND NOT(rapid disease progression during screening/apheresis unlikely to be controlled using permissible bridging options) AND NOT(development and presence of detectable CNS lymphoma since enrolment in the NHL34 CLARIFY-Prognostic Platform)',
   'IMPROVEMENT',
   "adds a node the source's 'including / per WHO' wording covers, keeping the rest")
_v('Vascular Tumor',
   'IMPROVEMENT',
   'P12 working: a super-class node whose name admits families the source never named is replaced by the named entities')
_v('advanced cancer with neuroendocrine features/differentiation with prior Sponsor approval',
   'UNCERTAIN',
   "dropping NETNOS narrows 'neuroendocrine features/differentiation'; arguable either way")
_v('advanced non-resectable clear cell or papillary type Renal Cell Carcinoma AND NOT(Sarcomatoid, Chromophobe, Collecting duct or Unclassified Renal Cell Carcinoma) AND NOT(active brain or other CNS metastases)',
   'UNCERTAIN',
   'changed in a way I cannot adjudicate without the trial protocol')
_v('advanced recurrent or refractory high-grade poorly differentiated gastroenteropancreatic neuroendocrine carcinoma',
   'NEUTRAL',
   'neuroendocrine site/grade enumeration; more explicit, population unchanged')
_v('advanced solid tumor AND NOT(NSCLC) AND NOT(thyroid cancer) AND NOT(RET-mutant MEN2 spectrum tumor) AND otherwise ineligible for cohorts 1-4',
   'IMPROVEMENT',
   'adds the stated pheochromocytoma exclusion')
_v('advanced solid tumor AND NOT(colorectal cancer) AND NOT(endometrial cancers with specific concurrent oncogenic alterations)',
   'IMPROVEMENT',
   'adds the stated endometrial exclusion')
_v('advanced, recurrent or metastatic gastric/gastro-oesophageal adenocarcinoma not amenable to curative therapy AND NOT(spinal cord compression or brain metastases unless asymptomatic, stable, and not requiring steroids for at least 4 weeks)',
   'IMPROVEMENT',
   'the source names the oesophago-gastric sites; the conflated organ node does not')
_v('advanced/metastatic cervical cancer AND NOT(primary neuroendocrine, mesenchymal, sarcomatoid, or other histologies not mentioned as part of the inclusion criteria)',
   'REGRESSION',
   'SCCE (small cell carcinoma of the cervix) is a CERVIX descendant and part of the stated neuroendocrine exclusion; dropping it narrows a stated exclusion')
_v('advanced/metastatic epithelial ovarian cancer (including adenocarcinoma of the fallopian tube and peritoneal epithelial cancer) AND NOT(mesothelioma)',
   'IMPROVEMENT',
   'epithelial / peritoneal-carcinoma histology stated')
_v('aggressive mature B-cell Non-Hodgkin Lymphoma including BL, BAL (mature B-cell leukemia FAB L3), DLBCL, and PMBCL AND NOT(isolated CNS disease of mature B-NHL without systemic involvement) AND NOT(primary CNS lymphoma)',
   'NEUTRAL',
   'subtractive MBN-minus-carve-outs rewritten as a positive enumeration; same population')
_v('ambiguous lineage with myeloid overlap AND NOT(acute promyelocytic leukaemia) AND NOT(relapsed AML)',
   'IMPROVEMENT',
   'P12 working: a super-class node whose name admits families the source never named is replaced by the named entities')
_v('anal cancer',
   'UNCERTAIN',
   'changed in a way I cannot adjudicate without the trial protocol')
_v('anaplastic oligoastrocytoma',
   'IMPROVEMENT',
   "a stated grade or 'anaplastic' selects the graded node — P14 working")
_v('any type of cancer AND NOT(cancer of the gastrointestinal tract and ancillary organs [mouth, oesophagus, stomach, intestine, colon, colorectal, rectal, pancreas, liver, gall bladder, bile duct, or other gastrointestinal cancer e.g., head and neck cancer]) AND NOT(brain cancer and metastases)',
   'UNCERTAIN',
   'changed in a way I cannot adjudicate without the trial protocol')
_v('biopsy-proven relapsed or treatment refractory aggressive B-cell non-Hodgkin lymphoma, DLBCL and its variants AND NOT(active or prior CNS involvement by lymphoma) AND NOT(Richter Syndrome)',
   'IMPROVEMENT',
   "adds a node the source's 'including / per WHO' wording covers, keeping the rest")
_v('biopsy-proven relapsed or treatment refractory aggressive B-cell non-Hodgkin lymphoma, follicular lymphoma AND NOT(active or prior CNS involvement by lymphoma) AND NOT(Richter Syndrome)',
   'IMPROVEMENT',
   'P12 working: the super-class node is replaced by the entities the source names')
_v('biopsy-proven relapsed or treatment refractory aggressive B-cell non-Hodgkin lymphoma, mantle cell lymphoma AND NOT(active or prior CNS involvement by lymphoma) AND NOT(Richter Syndrome)',
   'IMPROVEMENT',
   'P12 working: the super-class node is replaced by the entities the source names')
_v('biopsy-proven relapsed or treatment refractory aggressive B-cell non-Hodgkin lymphoma, transformed follicular lymphoma AND NOT(active or prior CNS involvement by lymphoma) AND NOT(Richter Syndrome)',
   'IMPROVEMENT',
   'P12 working: the super-class node is replaced by the entities the source names')
_v('bone sarcoma',
   'IMPROVEMENT',
   'P12 working: a super-class node whose name admits families the source never named is replaced by the named entities')
_v('cytologically or histologically confirmed advanced, recurrent or metastatic gastric/gastro-oesophageal adenocarcinoma not amenable to curative therapy',
   'IMPROVEMENT',
   'the source names the oesophago-gastric sites; the conflated organ node does not')
_v('de novo aggressive B-cell lymphoma (a-BCL) according to 2016 WHO classification AND NOT(any other subtype of lymphoma) AND NOT(documented or suspected CNS involvement by lymphoma)',
   'IMPROVEMENT',
   'P12 working: the super-class node is replaced by the entities the source names')
_v('de novo metastatic neuroendocrine prostate cancer with ≥1% neuroendocrine cells in a metastatic biopsy and mixed (small or large cell) neuroendocrine carcinoma-acinar adenocarcinoma morphology AND NOT(active central nervous system metastases and/or carcinomatous meningitis)',
   'UNCERTAIN',
   'changed in a way I cannot adjudicate without the trial protocol')
_v('histologically confirmed adenocarcinoma of the biliary tract, including intra-hepatic or extra-hepatic cholangiocarcinoma (CCA) and gallbladder carcinoma (GBC); unresectable locally advanced or metastatic BTC AND NOT(ampullary carcinoma)',
   'REGRESSION',
   'the source states ADENOCARCINOMA and names the sub-sites; falling back to the BILIARY_TRACT organ node is the very failure P9 exists to prevent')
_v("histologically confirmed epithelial fallopian tube carcinoma AND NOT(nonepithelial cancers OR borderline tumors OR mucinous OR seromucinous that is predominantly mucinous OR malignant Brenner's tumor OR undifferentiated carcinoma)",
   'NEUTRAL',
   'drops a branch; the remaining nodes still cover what the source names')
_v('histologically confirmed glioma, histologically grade 2 or 3 at initial diagnosis (without necrosis or microvascular proliferation; including CDKN2A/B homozygous deleted IDH-mutant astrocytomas) AND NOT(IDH-wildtype diffuse astrocytomas with any of TERT promoter mutation, EGFR amplification and/or +7/-10 copy number changes) AND NOT(metastatic tumours at the time of craniotomy that are not consistent with original glioma diagnosis)',
   'REGRESSION',
   "the source says grade 2 OR 3 and explicitly excludes grade 4 features, yet ASTR4 (grade 4) is now included — P14's grade rule applied in the wrong direction")
_v('histologically confirmed unresectable or advanced biliary tract (intrahepatic bile duct, extrahepatic bile duct, or gallbladder) cancer that is adenocarcinoma or adenosquamous carcinoma AND NOT(known active CNS metastases and/or carcinomatous meningitis)',
   'UNCERTAIN',
   'changed in a way I cannot adjudicate without the trial protocol')
_v('histologically confirmed, advanced (unresectable and/or metastatic), well-differentiated NET of GEP or presumed GEP origin',
   'NEUTRAL',
   'neuroendocrine site/grade enumeration; more explicit, population unchanged')
_v('histologically or cytologically confirmed unresectable advanced or metastatic malignant solid tumors AND NOT(NSCLC, SCLC, ESCC, CRPC, melanoma, CRC, PDAC, HNSCC, HCC, ovarian cancer including fallopian tube cancer and primary peritoneal cancer, endometrial cancer, thyroid cancer, or sarcoma)',
   'IMPROVEMENT',
   'epithelial / peritoneal-carcinoma histology stated')
_v('histologically proven/confirmed primary diagnosis AND complete macroscopic resection (R0 or R1 resection) for cancers of the periampullary region AND no evidence of malignant ascites, liver metastasis, spread to other distant abdominal organs, peritoneal metastasis, or spread to extra-abdominal organs AND NOT(pancreatic lymphoma) AND NOT(macroscopically remaining tumour (R2 resection)) AND NOT(TNM Stage IVb disease)',
   'UNCERTAIN',
   'changed in a way I cannot adjudicate without the trial protocol')
_v('histologically proven/confirmed primary diagnosis AND complete macroscopic resection (R0 or R1 resection) for periampullary cancers of uncertain origin AND no evidence of malignant ascites, liver metastasis, spread to other distant abdominal organs, peritoneal metastasis, or spread to extra-abdominal organs AND NOT(pancreatic lymphoma) AND NOT(macroscopically remaining tumour (R2 resection)) AND NOT(TNM Stage IVb disease)',
   'UNCERTAIN',
   'changed in a way I cannot adjudicate without the trial protocol')
_v('inoperable locally advanced or metastatic GC/GEJC/LEC',
   'IMPROVEMENT',
   'the source names the oesophago-gastric sites; the conflated organ node does not')
_v('large B-cell lymphoma (including DLBCL not otherwise specified, DLBCL arising from indolent lymphoma, primary mediastinal large B-cell lymphoma, high grade B-cell lymphoma, follicular lymphoma grade 3B) AND NOT(history of primary CNS lymphoma) AND NOT(presence of CNS metastases)',
   'IMPROVEMENT',
   'P12 working: the super-class node is replaced by the entities the source names')
_v('large B-cell lymphoma (including DLBCL, high-grade B-cell lymphoma [HGBCL], primary mediastinal B-cell lymphoma [PMBCL], etc) AND NOT(primary central nervous system [CNS] lymphoma) AND NOT(known CNS involvement with lymphoma)',
   'NEUTRAL',
   'subtractive MBN-minus-carve-outs rewritten as a positive enumeration; same population')
_v('locally advanced or metastatic Platinum Resistant High Grade Epithelial Ovarian Cancer (PROC)/primary peritoneal/fallopian tube cancer',
   'IMPROVEMENT',
   'epithelial / peritoneal-carcinoma histology stated')
_v('locally advanced or metastatic extra-cranial solid tumor AND NOT(infantile fibrosarcoma, congenital mesoblastic nephroma, or secretory breast cancer)',
   'REGRESSION',
   'the KIDNEY exclusion is stated in the source and was dropped')
_v('medulloblastoma variants including posterior fossa PNET AND NOT(CNS embryonal tumor other than medulloblastoma or PNET in the posterior fossa, e.g. ATRT, supratentorial PNET, pineoblastoma, ependymoblastoma, ETANTR)',
   'IMPROVEMENT',
   'P12 working: a super-class node whose name admits families the source never named is replaced by the named entities')
_v('newly diagnosed alveolar rhabdomyosarcoma (ARMS), very low-risk rhabdomyosarcoma (Stage 1, CG I disease)',
   'NEUTRAL',
   'the source names alveolar RMS and generic very-low-risk RMS; the RMS parent covers both')
_v('newly diagnosed radiologically-diagnosed malignant glioma (including glioblastoma multiforme); supratentorial, unifocal disease suitable for surgical resection; no infratentorial or intraventricular tumour visible on MRI; NOT(metastasis); NOT(T1 contrast-enhancing disease on MRI that cannot be completely resected)',
   'IMPROVEMENT',
   "a stated grade or 'anaplastic' selects the graded node — P14 working")
_v("newly diagnosed, histologically confirmed high grade B-cell lymphoma with DLBCL morphology (de novo or transformed from follicular lymphoma) AND NOT(composite/intermediate histology with high grade B-cell lymphoma NOS, Hodgkin's lymphoma, primary mediastinal (thymic) large B-cell lymphoma, Burkitt, plasmablastic lymphoma, or any CD20- lymphoma such as anaplastic lymphoma kinase-positive large B-cell lymphoma, human herpesvirus type 8-positive DLBCL, or primary effusion lymphoma)",
   'UNCERTAIN',
   'source says HGBCL with DLBCL morphology; whether DLBCLNOS alone or both nodes is right needs the protocol')
_v('other extra-cranial benign tumor',
   'REGRESSION',
   "'BENIGN tumour' is not a malignancy; empty was correct")
_v('ovarian cancer prevention',
   'IMPROVEMENT',
   "a PREVENTION cohort states no cancer diagnosis, so empty is right — P13's converse")
_v('pediatric low-grade glioma with histopathologic diagnosis of glioma or glioneuronal tumor AND NOT(Schwannoma) AND NOT(Subependymal giant cell astrocytoma (Tuberous Sclerosis)) AND NOT(Diffuse intrinsic pontine glioma, even if histologically diagnosed as World Health Organization (WHO) Grade I-II)',
   'UNCERTAIN',
   'changed in a way I cannot adjudicate without the trial protocol')
_v('platinum-resistant epithelial ovarian cancer, inclusive of primary peritoneal or fallopian-tube cancer, with histologically confirmed carcinosarcoma AND NOT(untreated or progressive brain/CNS metastases)',
   'IMPROVEMENT',
   "adds a node the source's 'including / per WHO' wording covers, keeping the rest")
_v('platinum-resistant fallopian tube cancer AND NOT(clear cell, mucinous, or sarcomatous histology; mixed tumors containing any histology; or low-grade/borderline OVC)',
   'NEUTRAL',
   'drops a branch; the remaining nodes still cover what the source names')
_v('platinum-resistant fallopian tube cancer AND NOT(seromucinous carcinoma, low-grade serous carcinoma, ovarian sarcoma, carcinosarcoma, or undifferentiated carcinoma) AND NOT(clear cell, mucinous, or sarcomatous histology; mixed tumors containing any histology; or low-grade/borderline OVC)',
   'IMPROVEMENT',
   "adds a node the source's 'including / per WHO' wording covers, keeping the rest")
_v('primary myelofibrosis (MF) characterized by bone marrow fibrosis grades 2 or 3 AND intermediate-2 or high-risk MF (DIPSS+)',
   'UNCERTAIN',
   'changed in a way I cannot adjudicate without the trial protocol')
_v('recurrent epithelial primary peritoneal cancer AND NOT(borderline ovarian tumor) AND NOT(non-epithelial histology) AND NOT(mixed histology including borderline or non-epithelial histology)',
   'UNCERTAIN',
   'changed in a way I cannot adjudicate without the trial protocol')
_v('recurrent or persistent clear cell carcinoma of the endometrium AND >=50% clear cell histology in case of mixed carcinoma',
   'UNCERTAIN',
   'adding mixed endometrioid to a >=50%-clear-cell criterion needs a gynae-pathology view')
_v('relapsed histologically proven mature B-NHL (Diffuse Large B-Cell Lymphoma (DLBCL), Burkitt Lymphoma/Leukaemia or atypical Burkitt/Burkitt-like lymphoma, primary mediastinal large B-cell lymphoma (PMLBL), or mature B-NHL/Not Otherwise Specified (NOS)) AND NOT(B-cell Acute Lymphoblastic Leukaemia (B-ALL)/B-cell Lymphoblastic Lymphoma (B-LBL))',
   'IMPROVEMENT',
   'P12 working: the super-class node is replaced by the entities the source names')
_v("relapsed or refractory LBCL AND NOT(CLL) AND NOT(Burkitt lymphoma) AND NOT(Richter's transformation) AND NOT(active CNS involvement by B-NHL) AND NOT(leukemic presentation of B-NHL)",
   'NEUTRAL',
   'subtractive MBN-minus-carve-outs rewritten as a positive enumeration; same population')
_v('relapsed or refractory mature aggressive large B-cell NHL AND NOT(HHV8-positive DLBCL) AND NOT(active CNS involvement by malignancy)',
   'NEUTRAL',
   'subtractive MBN-minus-carve-outs rewritten as a positive enumeration; same population')
_v('relapsed/refractory Burkitt-like lymphoma/leukemia AND NOT(known CNS involvement by lymphoma at screening confirmed by MRI/CT/PET brain scans; CSF-only CNS disease eligible)',
   'IMPROVEMENT',
   'P12 working: the super-class node is replaced by the entities the source names')
_v("relapsed/refractory large B-cell lymphoma (de novo or transformed from FL), HGBL NOS AND NOT(history of Waldenstrom's macroglobulinemia) AND NOT(primary mediastinal B-cell lymphoma) AND NOT(primary or secondary CNS lymphoma at the time of recruitment)",
   'NEUTRAL',
   'subtractive MBN-minus-carve-outs rewritten as a positive enumeration; same population')
_v('relapsed/refractory large B-cell lymphoma AND NOT(active CNS involvement by malignancy)',
   'IMPROVEMENT',
   'P12 working: the super-class node is replaced by the entities the source names')
_v('solid cancers AND NOT(active prostate cancer requiring treatment) AND NOT(active breast cancer requiring treatment) AND NOT(symptomatic central nervous system cancer unless stable neurological function on stable doses of steroids/antiepileptics over 4 weeks prior to screening)',
   'IMPROVEMENT',
   'restores two stated exclusions (prostate, breast) that earlier runs dropped — the class P3 targets')
_v('standard risk 1 ovarian malignant germ cell tumor, COG stage II-IV / FIGO stage IC-IV, containing choriocarcinoma AND NOT(pure seminoma) AND NOT(stage I testicular cancer after primary RPLND) AND NOT(pure ovarian or extragonadal dysgerminoma/seminoma) AND NOT(pure mature teratoma) AND NOT(pure immature teratoma with AFP >= 1000 ng/mL) AND NOT(poor risk GCT) AND NOT(primary CNS germ cell tumor) AND NOT(germ cell tumor with somatic malignant transformation) AND NOT(spermatocytic seminoma)',
   'IMPROVEMENT',
   "adds a node the source's 'including / per WHO' wording covers, keeping the rest")
_v('standard risk 1 testicular malignant germ cell tumor, COG stage II-IV, IGCCC criteria do not apply, containing choriocarcinoma AND NOT(pure seminoma) AND NOT(stage I testicular cancer after primary RPLND) AND NOT(pure ovarian or extragonadal dysgerminoma/seminoma) AND NOT(pure mature teratoma) AND NOT(pure immature teratoma with AFP >= 1000 ng/mL) AND NOT(poor risk GCT) AND NOT(primary CNS germ cell tumor) AND NOT(germ cell tumor with somatic malignant transformation) AND NOT(spermatocytic seminoma)',
   'IMPROVEMENT',
   "adds a node the source's 'including / per WHO' wording covers, keeping the rest")
_v('standard risk 1 testicular malignant germ cell tumor, COG stage II-IV, IGCCC criteria do not apply, containing embryonal carcinoma AND NOT(pure seminoma) AND NOT(stage I testicular cancer after primary RPLND) AND NOT(pure ovarian or extragonadal dysgerminoma/seminoma) AND NOT(pure mature teratoma) AND NOT(pure immature teratoma with AFP >= 1000 ng/mL) AND NOT(poor risk GCT) AND NOT(primary CNS germ cell tumor) AND NOT(germ cell tumor with somatic malignant transformation) AND NOT(spermatocytic seminoma)',
   'UNCERTAIN',
   'changed in a way I cannot adjudicate without the trial protocol')
_v('transformation of indolent B-cell lymphoma AND NOT(primary central nervous system [CNS] lymphoma) AND NOT(known CNS involvement with lymphoma)',
   'REGRESSION',
   'the answer names the INDOLENT precursors (CLL/FL/LPL/MZL) rather than the aggressive disease the patient now has; cancer_type is the current disease')
_v('treatment-emergent neuroendocrine metastatic castration-resistant prostate cancer with ≥1% neuroendocrine cells in a metastatic biopsy and mixed (small or large cell) neuroendocrine carcinoma-acinar adenocarcinoma morphology AND NOT(active central nervous system metastases and/or carcinomatous meningitis)',
   'NEUTRAL',
   'treatment-emergent neuroendocrine prostate cancer arises from adenocarcinoma, so including PRAD is defensible')
_v('unresectable locally advanced or metastatic non-medullary thyroid carcinoma AND NOT(known active CNS metastases and/or carcinomatous meningitis)',
   'UNCERTAIN',
   'changed in a way I cannot adjudicate without the trial protocol')
_v('unresectable, locally advanced or metastatic ovarian cancer',
   'REGRESSION',
   'no histology is stated, so the OVARY organ node is correct; enumerating four subtypes asserts a specificity the source does not have')
_v('well or moderately differentiated hind-gut neuroendocrine tumour, metastatic and inoperable, WHO grade 1 or 2, non-functioning NET AND NOT(gastric NET) AND NOT(lung NET)',
   'NEUTRAL',
   'neuroendocrine site/grade enumeration; more explicit, population unchanged')
_v('well-differentiated neuro-endocrine tumour of non-functional gastroenteric origin (GE-NET)',
   'NEUTRAL',
   'neuroendocrine site/grade enumeration; more explicit, population unchanged')


# --- v6 transitions, judged 2026-08-05 ---
_v('B cell NHL AND NOT(Burkitt lymphoma) AND NOT(Waldenström macroglobulinemia) AND NOT(chronic lymphocytic leukemia) AND NOT(Central nervous system (CNS) lymphoma, leptomeningeal infiltration, spinal cord compression not controlled by prior surgery or radiotherapy, or symptoms suggesting CNS involvement)',
   'IMPROVEMENT',
   'adds the stated CNS-lymphoma exclusion')
_v('Chronic myelomonocytic leukemia (CMML) with 10-29% marrow blasts without myeloproliferative disorder according to World Health Organisation (WHO) classification AND NOT(Acute myeloid leukemia (AML) with ≥ 30% blasts in bone marrow according to WHO classification)',
   'IMPROVEMENT',
   'CMML-2 is defined by the 10-29% blast count the source states')
_v('Grade 3 NET independent of primary site AND NOT(SCLC)',
   'NEUTRAL',
   "site enumeration for 'independent of primary site'; verbose, same population")
_v('Lymphoma, B-Cell',
   'NEUTRAL',
   'P12 replaces the MBN super-class, but the enumeration reaches BLL (a lymphoblastic LEUKAEMIA), so it over-covers slightly')
_v('Mixed Phenotype Acute Leukemia AND NOT(acute undifferentiated leukemia (AUL)) AND NOT(mature (Burkitt) B-cell ALL with known MYC translocation)',
   'IMPROVEMENT',
   'the four MPAL nodes are exactly mixed-phenotype acute leukaemia; ALAL was the parent and the AUL exclusion becomes vacuous')
_v("R/R B-NHL AND NOT(Burkitt's lymphoma) AND NOT(Burkitt's-like lymphoma) AND NOT(lymphoplastic lymphoma) AND NOT(current CNS involvement by malignancy)",
   'IMPROVEMENT',
   "adds BLL11Q for the stated 'Burkitt's-LIKE lymphoma'")
_v('advanced and/or metastatic solid tumors AND NOT(known central nervous system (CNS) tumors or metastases) AND NOT(leptomeningeal metastases)',
   'IMPROVEMENT',
   'a CNS-METASTASIS exclusion is a site, not a tumour type, so dropping it is right')
_v('advanced fallopian tube cancer AND NOT(endometrioid, clear cell, mucinous, or sarcomatous histology) AND NOT(mixed tumors containing any of the above histologies) AND NOT(low-grade or borderline ovarian tumor)',
   'IMPROVEMENT',
   'the stated histology carve-outs leave high-grade serous fallopian tube disease; HGSFT reaches it directly')
_v('cancer case with ICD-O histologic behavior code 1 (borderline)',
   'IMPROVEMENT',
   "behaviour code 1 IS borderline, not malignant — P13's narrowing working")
_v('earlier diagnosis of a poor prognosis cancer AND NOT(non-small cell lung cancer (NSCLC)) AND NOT(colorectal cancer) AND NOT(non-central nervous system (CNS) cancer with symptomatic CNS involvement unless stable neurological function, stable steroid/anti-epileptic doses over 4 weeks, and no CNS progression within 12 weeks prior to screening)',
   'REGRESSION',
   "'EARLIER diagnosis' is a prior malignancy, which is out of scope for this field; empty was right")
_v('high-grade fallopian tube cancer',
   'IMPROVEMENT',
   'the fallopian-tube carcinoma node rather than the Ovary/Fallopian Tube organ node')
_v('histological or cytological diagnosis of head and neck carcinoma (HNC) AND locally advanced, recurrent or metastatic disease thought to have a high probability of treatment failure with standard techniques AND high risk resectable HNC (primary and/or nodal metastases)',
   'NEUTRAL',
   "'carcinoma' is a stated histology (P9) but HEAD_NECK is a real regional node (P1); the two rules pull opposite ways here")
_v('histologically confirmed CNS B cell NHL occurring in the immunosuppressed, including ocular lymphoma AND NOT(Primary Effusion lymphoma) AND NOT(Burkitt’s lymphoma) AND NOT(ENKT lymphoma)',
   'IMPROVEMENT',
   'primary CNS lymphoma is exactly what the source describes')
_v('histologically confirmed advanced gastric/gastroesophageal adenocarcinoma that has progressed or is not suitable for standard of care systemic therapies',
   'IMPROVEMENT',
   'both stated sites named rather than the EGC parent')
_v('histologically confirmed relapsed indolent B-cell NHL',
   'IMPROVEMENT',
   'the source IS about indolent disease, so CLL/FL/LPL/MZL is the right set — unlike the transformed-lymphoma value, where naming the precursors was wrong')
_v('histologically or cytologically confirmed FIGO Stage III/IV endometrioid fallopian tube carcinoma AND NOT(abdominal adenocarcinoma of unknown origin) AND NOT(borderline ovarian tumor) AND NOT(carcinosarcomas (even if there is a serous component))',
   'NEUTRAL',
   'same carve-out-list churn')
_v('malignant hematological disease in CR AND NOT(myelofibrosis)',
   'IMPROVEMENT',
   "'myelofibrosis' covers post-ET and post-PV as well as primary")
_v('metastatic malignant neoplasm in the central nervous system',
   'IMPROVEMENT',
   'this is a malignancy, so empty was wrong')
_v('neuroendocrine carcinoma independent of primary site AND NOT(SCLC)',
   'NEUTRAL',
   'site enumeration; same population as NECNOS')
_v('newly diagnosed advanced (FIGO stage III-IV) fallopian tube cancer AND NOT(pure sarcomas) AND NOT(borderline tumors) AND NOT(mucinous tumors)',
   'NEUTRAL',
   'borderline-tumour carve-out list differing by a code or two')
_v('other haematological malignancy with significant risk of relapse (allogeneic transplant indicated) AND NOT(refractory Central Nervous System (CNS) disease) AND NOT(refractory lymphoma)',
   'REGRESSION',
   "'OTHER haematological malignancy' carries a stated lymphoma carve-out that was dropped")
_v('other hematological malignancies AND NOT(acute promyelocytic leukemia with t(15;17)(q22;q12) or abnormal promyelocytic leukemia/retinoic acid receptor alpha (APML-RARA)) AND NOT(MDS with fibrosis (MDS-f)) AND NOT(leukemic meningitis or known active central nervous system disease) AND NOT(extra-medullary disease or myeloid sarcoma alone with no morphologic hematologic relapse)',
   'REGRESSION',
   'myeloid sarcoma is recovered but the stated MDS exclusion is still dropped')
_v('pathologically documented unresectable locally recurrent carcinoma of the head and neck with primary tumor site arising from the oropharynx AND NOT(nasopharynx) AND NOT(active untreated CNS or leptomeningeal metastasis)',
   'IMPROVEMENT',
   'oropharyngeal squamous carcinoma named precisely')
_v('recurrent epithelial fallopian tube cancer AND NOT(borderline ovarian tumor) AND NOT(non-epithelial histology) AND NOT(mixed histology including borderline or non-epithelial histology)',
   'REGRESSION',
   'epithelial histology is stated, so OVT was right; this moves UP to the OVARY organ node — P9 backwards')
_v('recurrent or metastatic non-small cell lung cancer (adenocarcinoma and squamous eligible; NOT(endocrine, neuroendocrine, and small cell tumors)) AND NOT(symptomatic and/or untreated CNS metastases or leptomeningeal disease or primary tumor of CNS origin)',
   'IMPROVEMENT',
   'the source names both histologies explicitly')
_v('recurrent or persistent clear cell carcinoma of the ovary AND >=50% clear cell histology in case of mixed carcinoma',
   'UNCERTAIN',
   'adding mixed histology to a >=50%-clear-cell criterion needs a gynae-pathology view')
_v('refractory histologically proven mature B-NHL (Diffuse Large B-Cell Lymphoma (DLBCL), Burkitt Lymphoma/Leukaemia or atypical Burkitt/Burkitt-like lymphoma, primary mediastinal large B-cell lymphoma (PMLBL), or mature B-NHL/Not Otherwise Specified (NOS)) AND NOT(B-cell Acute Lymphoblastic Leukaemia (B-ALL)/B-cell Lymphoblastic Lymphoma (B-LBL))',
   'IMPROVEMENT',
   'the four named subtypes replace the MBN super-class')
_v('relapsed or refractory transformed large B-cell lymphoma',
   'IMPROVEMENT',
   'LBCL entities rather than MBN, which admits CLL and myeloma')
_v('relapsed/refractory AML AND NOT(active acute promyelocytic leukemia) AND NOT(isolated extramedullary relapse)',
   'IMPROVEMENT',
   'adds the stated myeloid-sarcoma exclusion')
_v("relapsed/refractory DLBCL, as defined by the 2016 WHO classification, including DLBCL NOS, EBV-positive DLBCL, T cell rich B-cell lymphoma, and DLBCL transformed from indolent lymphoma AND NOT(Richter's transformation) AND NOT(primary CNS lymphoma) AND NOT(active secondary CNS involvement) AND NOT(lymphomatous meningitis)",
   'IMPROVEMENT',
   "'including all subtypes' — the enumeration is now complete")
_v('relapsed/refractory large B-cell lymphoma AND NOT(primary central nervous system (CNS) lymphoma) AND NOT(active secondary CNS involvement of lymphoma at screening)',
   'NEUTRAL',
   'subtractive MBN form rewritten as a positive LBCL enumeration; same population')
_v('standard risk 1 ovarian malignant germ cell tumor, COG stage II-IV / FIGO stage IC-IV, containing embryonal carcinoma AND NOT(pure seminoma) AND NOT(stage I testicular cancer after primary RPLND) AND NOT(pure ovarian or extragonadal dysgerminoma/seminoma) AND NOT(pure mature teratoma) AND NOT(pure immature teratoma with AFP >= 1000 ng/mL) AND NOT(poor risk GCT) AND NOT(primary CNS germ cell tumor) AND NOT(germ cell tumor with somatic malignant transformation) AND NOT(spermatocytic seminoma)',
   'UNCERTAIN',
   "COG risk-stratum arm; needs the protocol's own definition")
_v('standard risk 2 ovarian malignant germ cell tumor, COG stage II/III/III-X / FIGO stage IC/II/III, containing embryonal carcinoma AND NOT(pure seminoma) AND NOT(stage I testicular cancer after primary RPLND) AND NOT(pure ovarian or extragonadal dysgerminoma/seminoma) AND NOT(pure mature teratoma) AND NOT(pure immature teratoma with AFP >= 1000 ng/mL) AND NOT(poor risk GCT) AND NOT(primary CNS germ cell tumor) AND NOT(germ cell tumor with somatic malignant transformation) AND NOT(spermatocytic seminoma)',
   'UNCERTAIN',
   'same trial')
_v('unresectable, locally advanced or metastatic pancreatic carcinoma',
   'NEUTRAL',
   "'carcinoma' is stated so enumerating the pancreatic carcinoma nodes is faithful, if verbose")
_v('von Hippel-Lindau-related neoplasms',
   'IMPROVEMENT',
   'the VHL tumour spectrum is well defined; the sentinel admitted every unrelated tumour')


def equivalent(a: str, b: str) -> bool:
    """True if two expressions are the SAME mapping, differing only by algebra a stage-2 rewrite performs.

    Per the user (2026-08-05): *"if two expressions are displayed differently but can be algebraically manipulated
    into one or the other, that is treated as identical (the manipulation is stage 2 work)"*. So compare canonical
    forms, not strings: `Solid tumour AND NOT(PROSTATE) AND NOT(BREAST)` and `Solid tumour AND NOT(BREAST OR
    PROSTATE)` are one mapping, and so are two exclusion lists differing only in OR order.
    """
    from aus_trial_universe.tasks.eligibility.mapping.reconcile import deterministic_pass
    if a == b:
        return True
    if not (a or "").strip() or not (b or "").strip():
        return False
    try:
        return deterministic_pass(a) == deterministic_pass(b)
    except Exception:                      # noqa: BLE001 — an unparseable side is simply not equivalent
        return False
