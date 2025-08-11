# We give instructions only when a given criterion type is present
INSTRUCTION_CRITERION_TYPES: dict[str, list[str]] = {
    '- Use `PrimaryTumorCriterion` for tumor types and / or locations under current study (e.g., melanoma, prostate).'
    : ['PrimaryTumor'],

    '- Use `MolecularBiomarkerCriterion` for expression-based biomarkers (e.g., PD-L1, HER2, IHC 3+).'
    : ['MolecularBiomarker'],

    '- Use `MolecularSignatureCriterion` for composite biomarkers or genomic signatures (e.g., MSI-H, TMB-H, HRD).'
    : ['MolecularSignature'],

    '- Use `GeneAlterationCriterion` for genomic alterations (e.g., EGFR mutation, ALK fusion). \
When specifying protein variants, always use the HGVS protein notation format.'
    : ['GeneAlteration'],

    '- Use `LabValueCriterion` for lab-based requirements that have lab measurement, unit, value, and operator.'
    : ['LabValue'],

    '- Use PrimaryTumorCriterion AND MolecularSignatureCriterion for tumor type with biomarker (e.g., "PD-L1-positive \
melanoma").'
    : ['PrimaryTumor', 'MolecularSignature'],

    '- Use HistologyCriterion only for named histologic subtypes (e.g., "adenocarcinoma", "squamous cell carcinoma", \
"mucinous histology"). Use PrimaryTumorCriterion together with HistologyCriterion for tumor types + histologic type. \
Multiple histology types must be seperated into multiple HistologyCriterion wrapped inside OrCriterion.'
    : ['Histology', 'PrimaryTumor'],

    '- DiagnosticFindingCriterion for statements like "histological confirmation of cancer", but use only PrimaryTumorCriterion \
if specific tumor type or location is mentioned (e.g., "histological confirmation of melanoma").'
    : ['DiagnosticFinding', ' PrimaryTumor', 'Histology'],

    '- Use SymptomCriterion only for symptom related to the tumor. Use ComorbidityCriterion for conditions not related \
to the tumor.'
    : ['Symptom'],

    '- Do not use PrimaryTumorCriterion for criteria involving other cancers or prior malignancies; instead, use \
ComorbidityCriterion with a condition like "other active malignancy".'
    : ['PrimaryTumorCriterion', 'Comorbidity'],

    '- Use PriorTreatmentCriterion for past treatments. Multiple prior treatments must be separated into separate \
PriorTreatmentCriterion objects enclosed in appropriate OrCriterion / AndCriterion.'
    : ['PriorTreatment'],

    '- Use CurrentTreatmentCriterion for current treatments. Multiple current treatments must be separated into separate \
CurrentTreatmentCriterion objects enclosed in appropriate OrCriterion / AndCriterion.'
    : ['CurrentTreatment'],

    '- Use TreatmentOptionCriterion for requirements related to available, appropriate, or eligible treatments. In case of \
not amenable to or not eligible for a specific treatment, model it as a NotCriterion wrapping a TreatmentOptionCriterion.'
    : ['TreatmentOption'],

    '''- Use `ClinicalJudgementCriterion` only for subjective clinical assessment that are not defined or followed by \
objective measurements like lab values.
- If a criterion includes subjective or qualitative language (e.g., "adequate", "sufficient", "acceptable") but \
provides a concrete lab-based threshold (e.g., a named lab test with a value and unit), the *entire criterion* must be \
modeled as a `LabValueCriterion`. Do NOT use `ClinicalJudgementCriterion` in these cases.**
- comorbidity must be modelled using `ComorbidityCriterion` and not `ClinicalJudgementCriterion`'''
    : ['ClinicalJudgement', 'LabValue'],

    '- Use `OtherCriterion` for criteria that do not fit any other types defined in the schema.'
    : ['Other']
}
