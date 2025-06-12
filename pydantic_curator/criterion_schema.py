from typing import Literal, Optional
from pydantic import BaseModel, SkipValidation, model_validator, Field

class TypedModel(BaseModel):
    type: str = Field(init=False)

    @model_validator(mode='before')
    def add_type(cls, values):
        values['type'] = cls.__name__
        return values

class IntRange(TypedModel):
    min_inclusive: Optional[int] = None
    max_inclusive: Optional[int] = None

class BaseCriterion(TypedModel):
    description: str = ''
    exceptions: Optional[str] = None

class AgeCriterion(BaseCriterion):
    age: int
    operator: Literal['<', '>', '<=', '>=']

class SexCriterion(BaseCriterion):
    sex: Literal['male', 'female']

class LabValueCriterion(BaseCriterion):
    measurement: str
    unit: str
    value: float
    operator: SkipValidation[Literal['<', '>', '<=', '>=', '==', 'OOR']]

class PrimaryTumorCriterion(BaseCriterion):
    """Defines the primary tumor site, disease type, stage, and extent."""
    primary_tumor_location: Optional[str] = None  # e.g., lung, breast, bladder
    primary_tumor_type: Optional[str] = None      # e.g., NSCLC, SCLC, melanoma, lymphoma
    stage: Optional[int] = None                   # e.g., 1, 2, 3, 4 (optional)
    disease_extent: Optional[str] = None          # e.g., "locally advanced", "metastatic"

class HistologyCriterion(BaseCriterion):
    """Defines the microscopic cell or tissue type of the tumor."""
    histology_type: str  # e.g., "adenocarcinoma", "squamous cell carcinoma", "small cell", "mucinous"
    histology_grade: Optional[str] = None  # e.g., "low-grade", "high-grade", "G1", "G2", "G3" (optional)

class MolecularBiomarkerCriterion(BaseCriterion):
    """Expression-based biomarkers (mostly protein-level)"""
    biomarker: str                          # e.g., "PD-L1", "HER2", "ER", "AR"
    expression_type: Optional[str] = None   # e.g., "positive", "high", "low", "≥1%", "overexpression", "IHC 3+"
    method: Optional[str] = None            # e.g., "IHC", "FISH", "RNAseq"

# gene-based alterations (DNA or mRNA-level)
class GeneAlterationCriterion(BaseCriterion):
    gene: str                               # e.g., "EGFR", "KRAS"
    alteration: str                         # e.g., "mutation", "fusion", "amplification", "deletion", "overexpression"
    variant: Optional[str] = None           # e.g., "p.G12C", "exon 20 insertion"
    detection_method: Optional[str] = None  # e.g., "NGS", "RT-PCR", "FISH"

# Composite molecular features or signatures
class MolecularSignatureCriterion(BaseCriterion):
    signature: str    # e.g. "MSI-H", "TMB-H", "HRD", "LOH", "Genomic Instability"

class DiagnosticFindingCriterion(BaseCriterion):
    finding: str                    # e.g., "measurable disease"
    method: Optional[str] = None    # e.g. "radiology", "pathology", "clinical_examination", "biopsy", "endoscopy"
    modality: Optional[str] = None  # e.g., "CT", "MRI", "NGS", "H&E stain"

class MetastasesCriterion(BaseCriterion):
    location: str
    size_cm: Optional[float] = None
    additional_details: Optional[list[str]] = None

class ComorbidityCriterion(BaseCriterion):
    comorbidity: str  # diabetes, heart failure, organ transplant
    severity: Optional[str] = None  # e.g. "severe", "uncontrolled"

class Treatment(TypedModel):
    description: str = ''

class StandardOfCare(Treatment):
    pass

class SystemicTherapy(Treatment):
    pass

class Radiotherapy(Treatment):
    location: Optional[str] = None
    dosage_gy: Optional[IntRange] = None

class Chemotherapy(Treatment):
    chemo_type: Optional[str] = None

class Medication(Treatment):
    medications: list[str]
    dosage: Optional[str] = None

class Surgery(Treatment):
    surgical_procedure: Optional[str] = None

class PriorTreatmentCriterion(BaseCriterion):
    treatment: Treatment
    number_of_prior_lines: Optional[IntRange] = None
    therapy_outcome: Optional[str] = None
    indication: Optional[str] = None

class CurrentTreatmentCriterion(BaseCriterion):
    treatment: Treatment
    indication: Optional[str] = None

# What treatment is appropriate, as judged by the clinician or protocol
class TreatmentOptionCriterion(BaseCriterion):
    treatment: Treatment

class ContraindicationCriterion(BaseCriterion):
    contraindication: str  # e.g. "immunotherapy", "pembrolizumab", "general anesthesia"
    reason: Optional[str] = None  # e.g. "hypersensitivity", "allergic reaction", "significant toxicities"

class ClinicalJudgementCriterion(BaseCriterion):
    judgement: str = ''

class ReproductiveStatusCriterion(BaseCriterion):
    status: str  # e.g. 'pregnancy', 'breastfeeding', 'post-menopausal'

class InfectionCriterion(BaseCriterion):
    infection: str  # e.g. 'HIV'
    status: Optional[str] = None    # e.g. 'active', 'chronic', 'past', 'cleared'

class SymptomCriterion(BaseCriterion):
    symptom: str
    severity: Optional[str] = None  # e.g., "mild", "moderate", "severe"
    duration: Optional[str] = None  # e.g., "persistent", "chronic", "acute"

# performance status such as ECOG or Karnofsky
class PerformanceStatusCriterion(BaseCriterion):
    scale: str
    value_range: Optional[IntRange] = None

class LifeExpectancyCriterion(BaseCriterion):
    min_weeks: int

class RequiredActionCriterion(BaseCriterion):
    action: str

class TissueAvailabilityCriterion(BaseCriterion):
    pass

class OtherCriterion(BaseCriterion):
    reason: Optional[str] = None  # Optional metadata for why it's "Other"

class AndCriterion(BaseCriterion):
    criteria: list[BaseCriterion]

class OrCriterion(BaseCriterion):
    criteria: list[BaseCriterion]

class NotCriterion(BaseCriterion):
    criterion: BaseCriterion

class IfCriterion(BaseCriterion):
    condition: BaseCriterion
    then: BaseCriterion
    else_: Optional[BaseCriterion] = None

class TimingCriterion(BaseCriterion):
    """Add timing information to criterion"""
    reference: Optional[str] = None  # e.g. "now", "last_dose"
    window_days: Optional[IntRange] = None
    criterion: BaseCriterion
