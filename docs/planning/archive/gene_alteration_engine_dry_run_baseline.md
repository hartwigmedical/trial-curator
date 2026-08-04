# Matching-engine DRY RUN — `finalised_gene_alteration_map.tsv` · `finding_model_FINAL`

Engine parser ported from **a97142938 (oncoact/trial_matching, 2026-05-22)**; port validated 12/12 against `TrialGeneticsParserTest.kt`.
No engine code was modified or executed — this is a transcription of its parse path.

| | distinct | rows |
|---|---:|---:|
| total | 469 | 5368 |
| CLEAN | 280 | 4044 |
| broken — **needs an ENGINE upgrade only** | 165 | 1242 |
| broken — **has a defect of OURS** | 24 | 82 |

## Findings by cause

| owner | id | distinct | rows | effect |
|---|---|---:|---:|---|
| ENGINE | G4 | 93 | 929 | Fusion[geneStart=X | geneEnd=X] -> Fusion(null,null) = ANY fusion |
| ENGINE | G2 | 75 | 345 | firstPass only splits & after a NOT( token -> term mis-parsed |
| ENGINE | G3 | 46 | 209 | positives empty -> biomarker gate silently disabled |
| ENGINE | G8 | 30 | 138 | Wildtype/Virus/PharmocoGenotype section skipped, not matched |
| ENGINE | G1 | 33 | 114 | positive conjunction has no representation in GeneticCriteria |
| OURS | A1 | 8 | 46 | effects=SPLICE is not a VariantEffect member -> expression dropped |
| OURS | A3 | 10 | 25 | NOT(A & B) with B also a positive conjunct == B & NOT(A) |
| OURS | A5 | 3 | 8 | HLA emitted as PharmocoGenotype instead of HlaAllele |
| OURS | A2 | 3 | 3 | tautological conjunct: A & (A|B) == A |

## Per-expression triage

Sorted by rows affected. `owner` = OURS if any of our defect classes is present.

| rows | owner | ids | trials | expression |
|---:|---|---|---|---|
| 154 | ENGINE | G4 | NCT03155620, NCT03157128… | `SmallVariant[gene=RET] \| GainDeletion[gene=RET & type=GAIN] \| Fusion[geneStart=RET \| geneEnd=RET]` |
| 63 | ENGINE | G4 | NCT02393625, NCT02568267… | `Fusion[geneStart=ALK \| geneEnd=ALK]` |
| 48 | ENGINE | G4 | NCT05785767, NCT05800015 | `NOT(Fusion[geneStart=ALK \| geneEnd=ALK]) & NOT(Fusion[geneStart=ROS1 \| geneEnd=ROS1])` |
| 41 | ENGINE | G4 | ACTRN12622000582752, NCT04065399… | `Fusion[geneStart=KMT2A \| geneEnd=KMT2A]` |
| 40 | ENGINE | G4 | NCT02568267, NCT03093116… | `Fusion[geneStart=ROS1 \| geneEnd=ROS1]` |
| 32 | ENGINE | G2,G3 | NCT04142437 | `(Fusion[geneEnd=NTRK1] \| Fusion[geneEnd=NTRK2] \| Fusion[geneEnd=NTRK3]) & NOT(GainDeletion[gene=NTRK1 & type=GAIN] \| GainDeletion[gene=NTRK2 & type…` |
| 28 | ENGINE | G4,G1 | ACTRN12626000586314, NCT03960840… | `Fusion[geneStart=MYC \| geneEnd=MYC] & Fusion[geneStart=BCL2 \| geneEnd=BCL2]` |
| 27 | ENGINE | G8 | NCT03787602, NCT05611931… | `Wildtype[gene=TP53]` |
| 26 | ENGINE | G4 | NCT04886804, NCT05919537 | `Fusion[geneStart=NRG1 \| geneEnd=NRG1]` |
| 26 | ENGINE | G4 | ACTRN12624001373561, NCT02453282… | `NOT(SmallVariant[gene=EGFR]) & NOT(Fusion[geneStart=ALK \| geneEnd=ALK])` |
| 25 | ENGINE | G4 | NCT03947385 | `Fusion[geneStart=PRKCA \| geneEnd=PRKCA] \| Fusion[geneStart=PRKCB \| geneEnd=PRKCB] \| Fusion[geneStart=PRKCG \| geneEnd=PRKCG] \| Fusion[geneStart=P…` |
| 24 | ENGINE | G4 | NCT05727176 | `Fusion[geneStart=FGFR2 \| geneEnd=FGFR2]` |
| 24 | ENGINE | G4 | NCT04546399 | `NOT(Fusion[geneStart=BCR & geneEnd=ABL1]) & NOT(Fusion[geneStart=ABL1 \| geneEnd=ABL1] \| Fusion[geneStart=ABL2 \| geneEnd=ABL2] \| Fusion[geneStart=C…` |
| 18 | ENGINE | G2,G3 | NCT05364073, NCT05967689… | `SmallVariant[gene=EGFR] & NOT(SmallVariant[gene=EGFR & transcriptImpact.affectedExon=20 & transcriptImpact.effects=INFRAME_INSERTION])` |
| 17 | ENGINE | G4 | NCT03157128, NCT04194944… | `Fusion[geneStart=RET \| geneEnd=RET]` |
| 16 | ENGINE | G4 | NCT03914625, NCT03959085 | `NOT(Fusion[geneStart=MYC \| geneEnd=MYC])` |
| 16 | ENGINE | G2 | NCT06982521 | `SmallVariant[gene=PIK3CA] & NOT(SmallVariant[gene=AKT1] \| SmallVariant[gene=AKT2] \| SmallVariant[gene=AKT3]) & NOT(SmallVariant[gene=PTEN])` |
| 16 | ENGINE | G8 | ACTRN12625000934448, NCT05599984… | `Wildtype[gene=IDH1] & Wildtype[gene=IDH2]` |
| 15 | ENGINE | G4 | NCT05304585 | `NOT(Fusion[geneStart=FOXO1 \| geneEnd=FOXO1]) & SmallVariant[gene=MYOD1]` |
| 15 | ENGINE | G4,G8 | NCT05304585 | `NOT(Fusion[geneStart=FOXO1 \| geneEnd=FOXO1]) & Wildtype[gene=MYOD1] & Wildtype[gene=TP53]` |
| 15 | ENGINE | G2,G4 | NCT05304585 | `SmallVariant[gene=TP53] & NOT(Fusion[geneStart=FOXO1 \| geneEnd=FOXO1])` |
| 14 | ENGINE | G4 | NCT04775485, NCT04913285… | `SmallVariant[gene=BRAF] \| GainDeletion[gene=BRAF & type=GAIN] \| Fusion[geneStart=BRAF \| geneEnd=BRAF]` |
| 14 | ENGINE | G4 | NCT05614739, NCT07218380 | `SmallVariant[gene=FGFR3] \| GainDeletion[gene=FGFR3 & type=GAIN] \| Fusion[geneStart=FGFR3 \| geneEnd=FGFR3]` |
| 13 | ENGINE | G4,G1 | NCT03960840, NCT04628494… | `Fusion[geneStart=MYC \| geneEnd=MYC] & Fusion[geneStart=BCL6 \| geneEnd=BCL6]` |
| 12 | ENGINE | G4 | ACTRN12617001468314, NCT03057106 | `NOT(Fusion[geneStart=ALK \| geneEnd=ALK])` |
| 12 | ENGINE | G4 | NCT04614103 | `NOT(SmallVariant[gene=EGFR] \| GainDeletion[gene=EGFR & type=GAIN] \| Fusion[geneStart=EGFR \| geneEnd=EGFR]) & NOT(SmallVariant[gene=ALK] \| GainDele…` |
| 12 | OURS | G2,G3,A1 | NCT02609776, NCT03175224… | `SmallVariant[gene=MET & transcriptImpact.affectedExon=14 & transcriptImpact.effects=SPLICE]` |
| 10 | ENGINE | G2,G3 | NCT06966453 | `(SmallVariant[gene=BRCA1] \| SmallVariant[gene=BRCA2]) & NOT(GainDeletion[gene=ERBB2 & type=GAIN])` |
| 10 | ENGINE | G4 | NCT02637687 | `Fusion[geneStart=ETV6 \| geneEnd=ETV6]` |
| 9 | ENGINE | G4 | NCT05984277, NCT06357533… | `NOT(SmallVariant[gene=EGFR]) & NOT(Fusion[geneStart=ALK \| geneEnd=ALK]) & NOT(Fusion[geneStart=ROS1 \| geneEnd=ROS1])` |
| 8 | ENGINE | G4 | NCT03175224 | `Fusion[geneStart=MET \| geneEnd=MET]` |
| 8 | ENGINE | G4 | NCT07479797 | `NOT(Fusion[geneStart=IRF4 \| geneEnd=IRF4])` |
| 8 | OURS | G2,G4,A1 | NCT04656652 | `NOT(SmallVariant[gene=EGFR] \| GainDeletion[gene=EGFR & type=GAIN] \| Fusion[geneStart=EGFR \| geneEnd=EGFR]) & NOT(SmallVariant[gene=ALK] \| GainDele…` |
| 8 | OURS | G2,G4,A1 | NCT06635824 | `NOT(SmallVariant[gene=MET & transcriptImpact.affectedExon=14 & transcriptImpact.effects=SPLICE] \| GainDeletion[gene=MET & type=GAIN] \| Fusion[geneSt…` |
| 8 | ENGINE | G4 | NCT03390504 | `SmallVariant[gene=FGFR1] \| SmallVariant[gene=FGFR2] \| SmallVariant[gene=FGFR3] \| SmallVariant[gene=FGFR4] \| GainDeletion[gene=FGFR1 & type=GAIN] \…` |
| 8 | ENGINE | G8 | ACTRN12625000387426, NCT07509151 | `Wildtype[gene=IGHV]` |
| 7 | ENGINE | G4,G1 | NCT03960840, NCT04628494… | `Fusion[geneStart=MYC \| geneEnd=MYC] & Fusion[geneStart=BCL2 \| geneEnd=BCL2] & Fusion[geneStart=BCL6 \| geneEnd=BCL6]` |
| 7 | ENGINE | G4 | NCT02637687, NCT03093116 | `Fusion[geneStart=NTRK3 \| geneEnd=NTRK3]` |
| 7 | ENGINE | G2,G3 | NCT04293562, NCT05457556 | `SmallVariant[gene=FLT3] & NOT(SmallVariant[gene=FLT3 & transcriptImpact.effects=INFRAME_INSERTION])` |
| 7 | ENGINE | G2 | NCT05307705 | `SmallVariant[gene=PIK3CA] & NOT(SmallVariant[gene=PIK3CA & transcriptImpact.hgvsProteinImpact=p.H1047R])` |
| 7 | ENGINE | G8 | NCT03539536, NCT05029882… | `Wildtype[gene=EGFR]` |
| 6 | ENGINE | G2,G4,G3 | NCT04656652 | `(SmallVariant[gene=ALK] \| GainDeletion[gene=ALK & type=GAIN] \| Fusion[geneStart=ALK \| geneEnd=ALK]) & NOT(SmallVariant[gene=EGFR & transcriptImpact…` |
| 6 | ENGINE | G2,G4,G3 | NCT04656652 | `(SmallVariant[gene=BRAF] \| GainDeletion[gene=BRAF & type=GAIN] \| Fusion[geneStart=BRAF \| geneEnd=BRAF]) & NOT(SmallVariant[gene=EGFR & transcriptIm…` |
| 6 | ENGINE | G2,G3 | NCT04656652 | `(SmallVariant[gene=EGFR] \| GainDeletion[gene=EGFR & type=GAIN]) & NOT(SmallVariant[gene=EGFR & transcriptImpact.hgvsProteinImpact=p.L858R]) & NOT(Sma…` |
| 6 | ENGINE | G2,G4,G3 | NCT04656652 | `(SmallVariant[gene=NTRK1] \| SmallVariant[gene=NTRK2] \| SmallVariant[gene=NTRK3] \| GainDeletion[gene=NTRK1 & type=GAIN] \| GainDeletion[gene=NTRK2 &…` |
| 6 | ENGINE | G2,G4,G3 | NCT04656652 | `(SmallVariant[gene=RET] \| GainDeletion[gene=RET & type=GAIN] \| Fusion[geneStart=RET \| geneEnd=RET]) & NOT(SmallVariant[gene=EGFR & transcriptImpact…` |
| 6 | ENGINE | G2,G4,G3 | NCT04656652 | `(SmallVariant[gene=ROS1] \| GainDeletion[gene=ROS1 & type=GAIN] \| Fusion[geneStart=ROS1 \| geneEnd=ROS1]) & NOT(SmallVariant[gene=EGFR & transcriptIm…` |
| 6 | ENGINE | G2,G4 | NCT05503797 | `Fusion[geneStart=BRAF \| geneEnd=BRAF] & NOT(SmallVariant[gene=KRAS] \| SmallVariant[gene=NRAS] \| SmallVariant[gene=HRAS])` |
| 6 | ENGINE | G4 | NCT04886804 | `Fusion[geneStart=ERBB2 \| geneEnd=ERBB2]` |
| 6 | ENGINE | G4 | NCT05544552, NCT06995677 | `Fusion[geneStart=FGFR3 \| geneEnd=FGFR3]` |
| 6 | ENGINE | G4 | NCT02567435 | `Fusion[geneStart=FOXO1 \| geneEnd=FOXO1]` |
| 6 | ENGINE | G4 | NCT06486051 | `Fusion[geneStart=IRF4 \| geneEnd=IRF4]` |
| 6 | ENGINE | G4 | NCT04665206, NCT04857372 | `Fusion[geneStart=TAZ \| geneEnd=TAZ]` |
| 6 | ENGINE | G4 | NCT04665206, NCT04857372 | `Fusion[geneStart=YAP1 \| geneEnd=YAP1]` |
| 6 | OURS | G4,A3 | NCT03960840, NCT04408638… | `NOT(Fusion[geneStart=MYC \| geneEnd=MYC] & (Fusion[geneStart=BCL2 \| geneEnd=BCL2] \| Fusion[geneStart=BCL6 \| geneEnd=BCL6]))` |
| 6 | ENGINE | G4 | NCT07718737 | `NOT(SmallVariant[gene=EGFR]) & Fusion[geneStart=ALK \| geneEnd=ALK]` |
| 6 | ENGINE | G2,G3 | NCT04129502 | `SmallVariant[gene=EGFR & transcriptImpact.affectedExon=20 & transcriptImpact.effects=INFRAME_INSERTION] & NOT(SmallVariant[gene=EGFR & transcriptImpac…` |
| 6 | ENGINE | G2 | NCT07686445 | `SmallVariant[gene=HRAS] & NOT(SmallVariant[gene=BRAF & transcriptImpact.hgvsProteinImpact=p.V600X])` |
| 6 | ENGINE | G4 | NCT06890598 | `SmallVariant[gene=KRAS & transcriptImpact.hgvsProteinImpact=p.G12C] & NOT(SmallVariant[gene=EGFR] \| GainDeletion[gene=EGFR & type=GAIN] \| Fusion[gen…` |
| 6 | ENGINE | G2 | NCT07686445 | `SmallVariant[gene=KRAS] & NOT(SmallVariant[gene=BRAF & transcriptImpact.hgvsProteinImpact=p.V600X])` |
| 6 | OURS | G2,G3,A1 | NCT04656652 | `SmallVariant[gene=MET & transcriptImpact.affectedExon=14 & transcriptImpact.effects=SPLICE] & NOT(SmallVariant[gene=EGFR & transcriptImpact.hgvsProtei…` |
| 6 | ENGINE | G2 | NCT07686445 | `SmallVariant[gene=NRAS] & NOT(SmallVariant[gene=BRAF & transcriptImpact.hgvsProteinImpact=p.V600X])` |
| 6 | ENGINE | G8 | ACTRN12617001190392, ACTRN12623000874617… | `Wildtype[gene=KRAS] & Wildtype[gene=NRAS] & Wildtype[gene=HRAS] & Wildtype[gene=BRAF]` |
| 5 | ENGINE | G2,G3 | NCT05315700 | `SmallVariant[gene=EGFR & transcriptImpact.affectedExon=20 & transcriptImpact.effects=INFRAME_INSERTION] & NOT(SmallVariant[gene=EGFR & transcriptImpac…` |
| 5 | ENGINE | G4 | NCT07111520 | `SmallVariant[gene=EGFR] \| GainDeletion[gene=EGFR & type=GAIN] \| SmallVariant[gene=ALK] \| GainDeletion[gene=ALK & type=GAIN] \| Fusion[geneStart=ALK…` |
| 5 | ENGINE | G2,G4 | NCT05650879 | `SmallVariant[gene=ERBB2] & NOT(SmallVariant[gene=EGFR]) & NOT(SmallVariant[gene=ROS1] \| GainDeletion[gene=ROS1 & type=GAIN] \| Fusion[geneStart=ROS1 …` |
| 5 | ENGINE | G2 | NCT07007312 | `SmallVariant[gene=NPM1] & NOT(Fusion[geneStart=BCR & geneEnd=ABL1])` |
| 5 | ENGINE | G8 | NCT05221840, NCT05450692 | `Wildtype[gene=EGFR] & Wildtype[gene=ALK]` |
| 4 | ENGINE | G2,G3,G1 | NCT06413706 | `(SmallVariant[gene=IDH1] \| SmallVariant[gene=IDH2]) & GainDeletion[gene=CDKN2A & type=HOM_DEL] & GainDeletion[gene=CDKN2B & type=HOM_DEL]` |
| 4 | ENGINE | G4 | NCT05170204 | `Fusion[geneStart=ALK \| geneEnd=ALK] & NOT(SmallVariant[gene=ALK & transcriptImpact.hgvsProteinImpact=p.I1171X] \| SmallVariant[gene=ALK & transcriptI…` |
| 4 | ENGINE | G4,G3,G8 | NCT05327894 | `Fusion[geneStart=KMT2A \| geneEnd=KMT2A] & NOT(Wildtype[gene=KMT2A])` |
| 4 | ENGINE | G4 | ACTRN12622000582752, NCT04065399… | `Fusion[geneStart=NUP98 \| geneEnd=NUP98]` |
| 4 | ENGINE | G4 | NCT03007147, NCT06124157 | `Fusion[geneStart=PDGFRB \| geneEnd=PDGFRB]` |
| 4 | ENGINE | G4 | NCT05170204 | `Fusion[geneStart=ROS1 \| geneEnd=ROS1] & NOT(SmallVariant[gene=EGFR])` |
| 4 | OURS | G2,G4,A1 | NCT07291037 | `NOT(SmallVariant[gene=EGFR] \| GainDeletion[gene=EGFR & type=GAIN]) & NOT(SmallVariant[gene=ALK] \| GainDeletion[gene=ALK & type=GAIN] \| Fusion[geneS…` |
| 4 | OURS | A3,G8 | NCT07218926 | `NOT(Wildtype[gene=KIT] & Wildtype[gene=PDGFRA]) & NOT(SmallVariant[gene=PDGFRA & transcriptImpact.affectedExon=18])` |
| 4 | ENGINE | G2 | NCT05217446 | `SmallVariant[gene=BRAF & transcriptImpact.hgvsProteinImpact=p.V600E] & NOT(SmallVariant[gene=KRAS] \| SmallVariant[gene=NRAS] \| SmallVariant[gene=HRA…` |
| 4 | ENGINE | G2,G3 | NCT06905197 | `SmallVariant[gene=EGFR & transcriptImpact.affectedExon=19 & transcriptImpact.effects=INFRAME_DELETION] & NOT(SmallVariant[gene=EGFR & transcriptImpact…` |
| 4 | ENGINE | G2,G3,G1 | NCT05388669 | `SmallVariant[gene=EGFR & transcriptImpact.affectedExon=19 & transcriptImpact.effects=INFRAME_DELETION] & SmallVariant[gene=EGFR & transcriptImpact.hgv…` |
| 4 | ENGINE | G1 | NCT05388669 | `SmallVariant[gene=EGFR & transcriptImpact.affectedExon=21 & transcriptImpact.hgvsProteinImpact=p.L858R] & SmallVariant[gene=EGFR & transcriptImpact.hg…` |
| 4 | ENGINE | G2,G3 | NCT06905197 | `SmallVariant[gene=EGFR & transcriptImpact.hgvsProteinImpact=p.L858R] & NOT(SmallVariant[gene=EGFR & transcriptImpact.hgvsProteinImpact=p.G719X]) & NOT…` |
| 4 | ENGINE | G2,G3 | NCT07699328 | `SmallVariant[gene=EGFR] & NOT(SmallVariant[gene=EGFR & transcriptImpact.affectedExon=20 & transcriptImpact.effects=INFRAME_INSERTION]) & NOT(SmallVari…` |
| 4 | ENGINE | G2 | ACTRN12621001756819 | `SmallVariant[gene=KIT] & NOT(SmallVariant[gene=PDGFRA & transcriptImpact.hgvsProteinImpact=p.D842X])` |
| 4 | ENGINE | G4 | NCT04811560, NCT05453903 | `SmallVariant[gene=KMT2A] \| GainDeletion[gene=KMT2A & type=GAIN] \| Fusion[geneStart=KMT2A \| geneEnd=KMT2A]` |
| 4 | ENGINE | G4 | NCT04811560, NCT05453903 | `SmallVariant[gene=NPM1] \| GainDeletion[gene=NPM1 & type=GAIN] \| Fusion[geneStart=NPM1 \| geneEnd=NPM1]` |
| 4 | ENGINE | G4 | NCT04811560, NCT05453903 | `SmallVariant[gene=NUP214] \| GainDeletion[gene=NUP214 & type=GAIN] \| Fusion[geneStart=NUP214 \| geneEnd=NUP214]` |
| 4 | ENGINE | G4 | NCT04811560, NCT05453903 | `SmallVariant[gene=NUP98] \| GainDeletion[gene=NUP98 & type=GAIN] \| Fusion[geneStart=NUP98 \| geneEnd=NUP98]` |
| 4 | ENGINE | G2 | ACTRN12621001756819 | `SmallVariant[gene=PDGFRA] & NOT(SmallVariant[gene=PDGFRA & transcriptImpact.hgvsProteinImpact=p.D842X])` |
| 4 | ENGINE | G2 | NCT05843253 | `SmallVariant[gene=PIK3CA] & NOT(GainDeletion[gene=RB1 & type=HOM_DEL])` |
| 4 | ENGINE | G2 | NCT05843253 | `SmallVariant[gene=PIK3R1] & NOT(GainDeletion[gene=RB1 & type=HOM_DEL])` |
| 4 | ENGINE | G2 | NCT05843253 | `SmallVariant[gene=PTEN] & NOT(GainDeletion[gene=RB1 & type=HOM_DEL])` |
| 4 | ENGINE | G2 | NCT05843253 | `SmallVariant[gene=TSC1] & NOT(GainDeletion[gene=RB1 & type=HOM_DEL])` |
| 4 | ENGINE | G2 | NCT05843253 | `SmallVariant[gene=TSC2] & NOT(GainDeletion[gene=RB1 & type=HOM_DEL])` |
| 4 | OURS | A1,G8 | NCT02609776 | `Wildtype[gene=EGFR] & Wildtype[gene=ALK] & NOT(SmallVariant[gene=MET & transcriptImpact.affectedExon=14 & transcriptImpact.effects=SPLICE])` |
| 4 | ENGINE | G8 | NCT03175224 | `Wildtype[gene=MET]` |
| 3 | ENGINE | G2,G3,G1 | NCT05303519 | `(SmallVariant[gene=IDH1 & transcriptImpact.hgvsProteinImpact=p.R132H] \| SmallVariant[gene=IDH1 & transcriptImpact.hgvsProteinImpact=p.R132C] \| Small…` |
| 3 | ENGINE | G4 | NCT03007147 | `Fusion[geneStart=ABL1 \| geneEnd=ABL1]` |
| 3 | ENGINE | G4 | NCT03007147 | `Fusion[geneStart=ABL2 \| geneEnd=ABL2]` |
| 3 | ENGINE | G4 | NCT03007147 | `Fusion[geneStart=CSF1R \| geneEnd=CSF1R]` |
| 3 | ENGINE | G4 | NCT05009992 | `Fusion[geneStart=FGFR1 \| geneEnd=FGFR1]` |
| 3 | ENGINE | G4 | NCT03093116, NCT04589845 | `Fusion[geneStart=NTRK1 \| geneEnd=NTRK1]` |
| 3 | ENGINE | G4 | NCT03093116, NCT04589845 | `Fusion[geneStart=NTRK2 \| geneEnd=NTRK2]` |
| 3 | ENGINE | G4 | NCT03007147 | `Fusion[geneStart=PDGFRA \| geneEnd=PDGFRA]` |
| 3 | ENGINE | G4,G3,G8 | NCT07007312 | `NOT(Fusion[geneStart=BCR & geneEnd=ABL1]) & (Fusion[geneStart=KMT2A \| geneEnd=KMT2A]) & NOT(SmallVariant[gene=KMT2A & transcriptImpact.effects=INFRAM…` |
| 3 | ENGINE | G4 | NCT07007312 | `NOT(Fusion[geneStart=BCR & geneEnd=ABL1]) & Fusion[geneStart=KMT2A \| geneEnd=KMT2A] & NOT(SmallVariant[gene=KMT2A & transcriptImpact.effects=INFRAME_…` |
| 3 | ENGINE | G2,G1 | NCT07007312 | `NOT(Fusion[geneStart=BCR & geneEnd=ABL1]) & SmallVariant[gene=NPM1] & SmallVariant[gene=FLT3 & transcriptImpact.effects=INFRAME_INSERTION]` |
| 3 | ENGINE | G4 | NCT02567435 | `NOT(Fusion[geneStart=FOXO1 \| geneEnd=FOXO1])` |
| 3 | OURS | A5,G8 | NCT05549297 | `PharmocoGenotype[gene=HLA-A & allele=*02:01] & NOT(SmallVariant[gene=BRAF & transcriptImpact.hgvsProteinImpact=p.V600X])` |
| 3 | ENGINE | G1 | NCT05304377, NCT07354074 | `SmallVariant[gene=ABL1 & transcriptImpact.hgvsProteinImpact=p.T315I] & Fusion[geneStart=BCR & geneEnd=ABL1]` |
| 3 | ENGINE | G4 | NCT06533059 | `SmallVariant[gene=AKT1 & transcriptImpact.hgvsProteinImpact=p.E17K] & NOT(SmallVariant[gene=KRAS] \| GainDeletion[gene=KRAS & type=GAIN]) & NOT(SmallV…` |
| 3 | ENGINE | G2 | NCT05503797 | `SmallVariant[gene=BRAF & transcriptImpact.hgvsProteinImpact=p.V600E] & NOT(SmallVariant[gene=NF1] \| SmallVariant[gene=KRAS] \| SmallVariant[gene=NRAS…` |
| 3 | OURS | G3,A5,G8 | NCT05549297 | `SmallVariant[gene=BRAF & transcriptImpact.hgvsProteinImpact=p.V600X] & PharmocoGenotype[gene=HLA-A & allele=*02:01]` |
| 3 | ENGINE | G4 | NCT06130553, NCT06804824 | `SmallVariant[gene=EGFR] \| GainDeletion[gene=EGFR & type=GAIN] \| Fusion[geneStart=EGFR \| geneEnd=EGFR]` |
| 3 | ENGINE | G2,G3 | NCT05315700 | `SmallVariant[gene=ERBB2 & transcriptImpact.affectedExon=20 & transcriptImpact.effects=INFRAME_INSERTION] & NOT(SmallVariant[gene=EGFR & transcriptImpa…` |
| 3 | ENGINE | G2,G4,G3,G1 | NCT07007312 | `SmallVariant[gene=FLT3 & transcriptImpact.effects=INFRAME_INSERTION] & NOT(SmallVariant[gene=KMT2A & transcriptImpact.effects=INFRAME_INSERTION]) & Fu…` |
| 3 | ENGINE | G2,G3 | NCT05457556 | `SmallVariant[gene=FLT3 & transcriptImpact.effects=INFRAME_INSERTION] & NOT(SmallVariant[gene=NPM1])` |
| 3 | ENGINE | G2,G3,G1 | NCT05457556 | `SmallVariant[gene=FLT3 & transcriptImpact.effects=INFRAME_INSERTION] & SmallVariant[gene=CEBPA]` |
| 3 | ENGINE | G2,G3,G1 | NCT05457556 | `SmallVariant[gene=FLT3 & transcriptImpact.effects=INFRAME_INSERTION] & SmallVariant[gene=NPM1]` |
| 3 | ENGINE | G2,G4,G3,G1 | NCT07007312 | `SmallVariant[gene=FLT3] & NOT(SmallVariant[gene=KMT2A & transcriptImpact.effects=INFRAME_INSERTION]) & NOT(Fusion[geneStart=BCR & geneEnd=ABL1]) & Fus…` |
| 3 | OURS | G2,G4,G3,A1 | NCT05886920 | `SmallVariant[gene=KRAS & transcriptImpact.hgvsProteinImpact=p.G12C] & NOT(SmallVariant[gene=EGFR]) & NOT(SmallVariant[gene=BRAF & transcriptImpact.hgv…` |
| 3 | ENGINE | G2,G1 | NCT07007312 | `SmallVariant[gene=NPM1] & SmallVariant[gene=FLT3] & NOT(Fusion[geneStart=BCR & geneEnd=ABL1])` |
| 3 | ENGINE | G3,G8 | NCT07007312 | `SmallVariant[gene=NPM1] & Wildtype[gene=FLT3] & NOT(Fusion[geneStart=BCR & geneEnd=ABL1])` |
| 3 | ENGINE | G8 | ACTRN12616001187437 | `Wildtype[gene=BRAF] & Wildtype[gene=NRAS]` |
| 3 | ENGINE | G8 | NCT03845166, NCT05694936 | `Wildtype[gene=KRAS] & Wildtype[gene=NRAS] & NOT(SmallVariant[gene=BRAF & transcriptImpact.hgvsProteinImpact=p.V600E])` |
| 2 | ENGINE | G2,G4,G3 | NCT05566795 | `(SmallVariant[gene=ARAF] \| SmallVariant[gene=BRAF] \| SmallVariant[gene=RAF1] \| GainDeletion[gene=ARAF & type=GAIN] \| GainDeletion[gene=BRAF & type…` |
| 2 | ENGINE | G4 | NCT04589845 | `Fusion[geneStart=BRAF \| geneEnd=BRAF]` |
| 2 | ENGINE | G3,G8 | NCT03175224 | `GainDeletion[gene=MET & type=GAIN] & Wildtype[gene=EGFR]` |
| 2 | ENGINE | G2,G1 | NCT06360354 | `GainDeletion[gene=MTAP & type=HOM_DEL] & (SmallVariant[gene=KRAS] \| SmallVariant[gene=NRAS] \| SmallVariant[gene=HRAS])` |
| 2 | ENGINE | G4 | ACTRN12626000900314 | `NOT(Fusion[geneStart=BCR & geneEnd=ABL1]) & NOT(Fusion[geneStart=KMT2A \| geneEnd=KMT2A])` |
| 2 | ENGINE | G4 | NCT07361497 | `NOT(Fusion[geneStart=EGFR \| geneEnd=EGFR]) & NOT(Fusion[geneStart=ALK \| geneEnd=ALK])` |
| 2 | ENGINE | G4 | NCT06627647 | `NOT(SmallVariant[gene=EGFR & transcriptImpact.affectedExon=19 & transcriptImpact.effects=INFRAME_DELETION] \| SmallVariant[gene=EGFR & transcriptImpac…` |
| 2 | OURS | A5,G8 | NCT07156136 | `PharmocoGenotype[gene=HLA-A & allele=*02:01]` |
| 2 | ENGINE | G2,G3,G1 | NCT05261399 | `SmallVariant[gene=EGFR & transcriptImpact.affectedExon=19 & transcriptImpact.effects=INFRAME_DELETION] & GainDeletion[gene=MET & type=GAIN]` |
| 2 | ENGINE | G1 | NCT05261399 | `SmallVariant[gene=EGFR & transcriptImpact.hgvsProteinImpact=p.L858R] & GainDeletion[gene=MET & type=GAIN]` |
| 2 | ENGINE | G1 | NCT05261399 | `SmallVariant[gene=EGFR & transcriptImpact.hgvsProteinImpact=p.T790M] & GainDeletion[gene=MET & type=GAIN]` |
| 2 | ENGINE | G2,G1 | NCT02609776, NCT03175224 | `SmallVariant[gene=EGFR] & GainDeletion[gene=MET & type=GAIN]` |
| 2 | ENGINE | G2 | NCT05315700 | `SmallVariant[gene=EGFR] & NOT(SmallVariant[gene=EGFR & transcriptImpact.hgvsProteinImpact=p.T790M])` |
| 2 | ENGINE | G4 | NCT05089734 | `SmallVariant[gene=EGFR] \| GainDeletion[gene=EGFR & type=GAIN] \| Fusion[geneStart=EGFR \| geneEnd=EGFR] \| SmallVariant[gene=ALK] \| GainDeletion[gen…` |
| 2 | ENGINE | G2,G4,G1 | NCT05544552 | `SmallVariant[gene=FGFR3] & Fusion[geneStart=FGFR3 \| geneEnd=FGFR3]` |
| 2 | OURS | A3 | NCT03839771 | `SmallVariant[gene=IDH1 & transcriptImpact.hgvsProteinImpact=p.R132X] & NOT(SmallVariant[gene=IDH1] & SmallVariant[gene=IDH2]) & NOT(SmallVariant[gene=…` |
| 2 | OURS | A3,G1 | NCT03839771 | `SmallVariant[gene=IDH1 & transcriptImpact.hgvsProteinImpact=p.R132X] & SmallVariant[gene=FLT3] & NOT(SmallVariant[gene=IDH1] & SmallVariant[gene=IDH2]…` |
| 2 | ENGINE | G2 | ACTRN12619001031156 | `SmallVariant[gene=IDH1] & NOT(SmallVariant[gene=IDH2])` |
| 2 | OURS | A3 | NCT03839771 | `SmallVariant[gene=IDH2 & transcriptImpact.hgvsProteinImpact=p.R140X] & NOT(SmallVariant[gene=IDH1] & SmallVariant[gene=IDH2]) & NOT(SmallVariant[gene=…` |
| 2 | OURS | A3,G1 | NCT03839771 | `SmallVariant[gene=IDH2 & transcriptImpact.hgvsProteinImpact=p.R140X] & SmallVariant[gene=FLT3] & NOT(SmallVariant[gene=IDH1] & SmallVariant[gene=IDH2]…` |
| 2 | OURS | A3 | NCT03839771 | `SmallVariant[gene=IDH2 & transcriptImpact.hgvsProteinImpact=p.R172X] & NOT(SmallVariant[gene=IDH1] & SmallVariant[gene=IDH2]) & NOT(SmallVariant[gene=…` |
| 2 | OURS | A3,G1 | NCT03839771 | `SmallVariant[gene=IDH2 & transcriptImpact.hgvsProteinImpact=p.R172X] & SmallVariant[gene=FLT3] & NOT(SmallVariant[gene=IDH1] & SmallVariant[gene=IDH2]…` |
| 2 | OURS | G2,A3 | ACTRN12619001031156 | `SmallVariant[gene=IDH2] & NOT(SmallVariant[gene=IDH1] & SmallVariant[gene=IDH2])` |
| 2 | ENGINE | G2,G3,G1 | NCT05734105 | `SmallVariant[gene=KIT & transcriptImpact.affectedExon=11] & SmallVariant[gene=KIT & transcriptImpact.affectedExon=17] & NOT(SmallVariant[gene=KIT & tr…` |
| 2 | ENGINE | G2,G3,G1 | NCT05734105 | `SmallVariant[gene=KIT & transcriptImpact.affectedExon=11] & SmallVariant[gene=KIT & transcriptImpact.affectedExon=18] & NOT(SmallVariant[gene=KIT & tr…` |
| 2 | ENGINE | G4 | NCT05132075 | `SmallVariant[gene=KRAS & transcriptImpact.hgvsProteinImpact=p.G12C] & NOT(Fusion[geneStart=ALK \| geneEnd=ALK])` |
| 2 | ENGINE | G2 | NCT06625775 | `SmallVariant[gene=KRAS] & NOT(SmallVariant[gene=KRAS & transcriptImpact.hgvsProteinImpact=p.G12R]) & NOT(SmallVariant[gene=BRAF & transcriptImpact.hgv…` |
| 2 | ENGINE | G8 | NCT05222087 | `Wildtype[gene=EGFR] & Wildtype[gene=ALK] & Wildtype[gene=ROS1]` |
| 2 | ENGINE | G8 | NCT04581512 | `Wildtype[gene=FLT3]` |
| 2 | ENGINE | G8 | NCT06413706 | `Wildtype[gene=H3F3A] & Wildtype[gene=HIST1H3B] & Wildtype[gene=HIST1H3C] & Wildtype[gene=IDH1] & Wildtype[gene=IDH2]` |
| 2 | ENGINE | G8 | ACTRN12607000589482 | `Wildtype[gene=KRAS]` |
| 2 | ENGINE | G8 | NCT05253651 | `Wildtype[gene=KRAS] & Wildtype[gene=NRAS] & Wildtype[gene=HRAS]` |
| 1 | ENGINE | G2,G4,G3 | NCT07699328 | `(SmallVariant[gene=EGFR & transcriptImpact.hgvsProteinImpact=p.G719X] \| SmallVariant[gene=EGFR & transcriptImpact.hgvsProteinImpact=p.L861Q] \| Small…` |
| 1 | ENGINE | G2,G3 | NCT05843253 | `(SmallVariant[gene=H3F3A & transcriptImpact.hgvsProteinImpact=p.G35R] \| SmallVariant[gene=H3F3A & transcriptImpact.hgvsProteinImpact=p.G35V]) & NOT(G…` |
| 1 | ENGINE | G2,G3 | NCT05099003 | `(SmallVariant[gene=H3F3A & transcriptImpact.hgvsProteinImpact=p.K28M] \| SmallVariant[gene=HIST1H3B & transcriptImpact.hgvsProteinImpact=p.K28M] \| Sm…` |
| 1 | ENGINE | G1 | ACTRN12625000994482 | `Arm[chromosome=17 & arm=p & type=ARM_LOSS] & Arm[chromosome=17 & arm=q & type=ARM_LOSS]` |
| 1 | ENGINE | G4 | NCT06124157 | `Fusion[geneStart=ABL1 \| geneEnd=ABL1] & NOT(Fusion[geneStart=PDGFRB \| geneEnd=PDGFRB])` |
| 1 | ENGINE | G4 | NCT06124157 | `Fusion[geneStart=ABL2 \| geneEnd=ABL2] & NOT(Fusion[geneStart=PDGFRB \| geneEnd=PDGFRB])` |
| 1 | ENGINE | G4 | NCT04775485 | `Fusion[geneStart=ARAF \| geneEnd=ARAF] \| Fusion[geneStart=BRAF \| geneEnd=BRAF] \| Fusion[geneStart=RAF1 \| geneEnd=RAF1]` |
| 1 | ENGINE | G4 | NCT06124157 | `Fusion[geneStart=CSF1R \| geneEnd=CSF1R] & NOT(Fusion[geneStart=PDGFRB \| geneEnd=PDGFRB])` |
| 1 | ENGINE | G4 | ACTRN12620000542998 | `Fusion[geneStart=KMT2A \| geneEnd=KMT2A] & NOT(Fusion[geneStart=BCR & geneEnd=ABL1])` |
| 1 | ENGINE | G4 | ACTRN12625000964415 | `Fusion[geneStart=KMT2A \| geneEnd=KMT2A] & NOT(SmallVariant[gene=KMT2A & transcriptImpact.effects=INFRAME_INSERTION] \| GainDeletion[gene=KMT2A & type…` |
| 1 | ENGINE | G4 | NCT06652438 | `Fusion[geneStart=KMT2A \| geneEnd=KMT2A] & NOT(SmallVariant[gene=KMT2A & transcriptImpact.effects=INFRAME_INSERTION]) & NOT(GainDeletion[gene=KMT2A & …` |
| 1 | ENGINE | G3,G8 | ACTRN12626000207314 | `GainDeletion[gene=KRAS & type=GAIN] & Wildtype[gene=KRAS]` |
| 1 | OURS | G2,G4,A1 | NCT06172478 | `NOT(Fusion[geneStart=ALK \| geneEnd=ALK]) & NOT(SmallVariant[gene=BRAF & transcriptImpact.hgvsProteinImpact=p.V600E]) & NOT(SmallVariant[gene=EGFR & t…` |
| 1 | ENGINE | G8 | NCT06219941 | `NOT(PharmocoGenotype[gene=UGT1A1 & allele=*28])` |
| 1 | OURS | A3 | NCT03817398 | `NOT(SmallVariant[gene=ABL1 & transcriptImpact.hgvsProteinImpact=p.T315I] & Fusion[geneStart=BCR & geneEnd=ABL1])` |
| 1 | ENGINE | G4 | NCT05785754 | `NOT(SmallVariant[gene=EGFR] \| SmallVariant[gene=BRAF & transcriptImpact.hgvsProteinImpact=p.V600E] \| SmallVariant[gene=ROS1] \| GainDeletion[gene=EG…` |
| 1 | ENGINE | G2,G1 | NCT06163430 | `SmallVariant[gene=ABL1] & Fusion[geneStart=BCR & geneEnd=ABL1]` |
| 1 | ENGINE | G4 | NCT03155620 | `SmallVariant[gene=ALK] \| GainDeletion[gene=ALK & type=GAIN] \| Fusion[geneStart=ALK \| geneEnd=ALK]` |
| 1 | OURS | A2,G2,G3,G1 | NCT02609776 | `SmallVariant[gene=EGFR & transcriptImpact.affectedExon=20 & transcriptImpact.effects=INFRAME_INSERTION] & (SmallVariant[gene=EGFR] \| GainDeletion[gen…` |
| 1 | ENGINE | G2,G3,G1 | NCT07699328 | `SmallVariant[gene=EGFR & transcriptImpact.hgvsProteinImpact=p.L858R] & SmallVariant[gene=EGFR & transcriptImpact.hgvsProteinImpact=p.C797X] & NOT(Smal…` |
| 1 | OURS | A2,G2,G4,G1 | NCT02609776 | `SmallVariant[gene=EGFR] & (SmallVariant[gene=EGFR] \| GainDeletion[gene=EGFR & type=GAIN] \| Fusion[geneStart=EGFR \| geneEnd=EGFR])` |
| 1 | ENGINE | G2,G3 | NCT05967689 | `SmallVariant[gene=EGFR] & NOT(SmallVariant[gene=EGFR & transcriptImpact.affectedExon=20 & transcriptImpact.effects=INFRAME_INSERTION]) & NOT(SmallVari…` |
| 1 | OURS | A2,G2,G3,G1 | NCT07699328 | `SmallVariant[gene=EGFR] & SmallVariant[gene=EGFR & transcriptImpact.affectedExon=19 & transcriptImpact.effects=INFRAME_DELETION] & SmallVariant[gene=E…` |
| 1 | ENGINE | G2,G1 | NCT02609776 | `SmallVariant[gene=EGFR] & SmallVariant[gene=MET]` |
| 1 | ENGINE | G2,G3 | NCT06806982 | `SmallVariant[gene=ERBB2] & NOT(SmallVariant[gene=ERBB2 & transcriptImpact.affectedExon=20])` |
| 1 | ENGINE | G1 | NCT06333951 | `SmallVariant[gene=KRAS & transcriptImpact.hgvsProteinImpact=p.G12C] & GainDeletion[gene=MTAP & type=HOM_DEL]` |
| 1 | ENGINE | G2 | NCT06625775 | `SmallVariant[gene=KRAS] & NOT(SmallVariant[gene=KRAS & transcriptImpact.hgvsProteinImpact=p.G12R])` |
| 1 | ENGINE | G2 | ACTRN12625000964415 | `SmallVariant[gene=NPM1] & NOT(Fusion[geneStart=PML & geneEnd=RARA]) & NOT(Fusion[geneStart=BCR & geneEnd=ABL1])` |
| 1 | ENGINE | G4 | NCT03155620 | `SmallVariant[gene=ROS1] \| GainDeletion[gene=ROS1 & type=GAIN] \| Fusion[geneStart=ROS1 \| geneEnd=ROS1]` |
| 1 | ENGINE | G8 | ACTRN12621001155886 | `Wildtype[gene=BRAF]` |
| 1 | ENGINE | G8 | NCT04915755 | `Wildtype[gene=BRCA1] & Wildtype[gene=BRCA2]` |
| 1 | ENGINE | G3,G8 | NCT03845166 | `Wildtype[gene=KRAS] & Wildtype[gene=NRAS] & SmallVariant[gene=BRAF & transcriptImpact.hgvsProteinImpact=p.V600E]` |
| 1 | ENGINE | G8 | NCT06926920 | `Wildtype[gene=UGT1A1]` |
