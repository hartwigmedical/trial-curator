from abc import ABC
from typing import List, Literal, Optional
from pydantic import BaseModel, SkipValidation

class IntRange(BaseModel):
    min_inclusive: Optional[int] = None
    max_inclusive: Optional[int] = None

class TimingInfo(BaseModel):
    reference: Optional[str] = None  # e.g. "now", "last_dose"
    window_days: Optional[IntRange] = None

# this class is abstract
class BaseCriterion(BaseModel, ABC):
    description: str = ''

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
    primary_tumor_location: Optional[str] = None  # e.g. lung, beast
    primary_tumor_type: Optional[str] = None      # e.g. NSCLC, LUAD, melanoma, lymphoma
    stage: Optional[int] = None
    disease_extent: Optional[str] = None          # e.g. "locally advanced"

class HistologyCriterion(BaseCriterion):
    histology_type: str  # e.g., "small cell", "combined small cell and non-small cell"

# Expression-based biomarkers (mostly protein-level)
class MolecularBiomarkerCriterion(BaseCriterion):
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

class SurgeryCriterion(BaseCriterion):
    surgical_procedure: Optional[str] = None
    timing_info: Optional[TimingInfo] = None

class MetastasesCriterion(BaseCriterion):
    location: str
    size_cm: Optional[float] = None
    additional_details: Optional[List[str]] = None

class ComorbidityCriterion(BaseCriterion):
    comorbidity: str  # diabetes, heart failure, organ transplant
    timing_info: Optional[TimingInfo] = None
    severity: Optional[str] = None  # e.g. "severe", "uncontrolled"

class PriorMedicationCriterion(BaseCriterion):
    medications: str
    timing_info: Optional[TimingInfo] = None

class CurrentMedicationCriterion(BaseCriterion):
    medications: str

# prior treatment / therapy that are not drug specific, such as systemic therapy or radiotherapy
class PriorTherapyCriterion(BaseCriterion):
    therapy: str      # e.g. "systemic therapy", "radiotherapy"
    number_of_prior_lines: Optional[IntRange] = None
    timing_info: Optional[TimingInfo] = None
    therapy_outcome: Optional[str] = None
    indication: Optional[str] = None

# What treatment is appropriate, as judged by the clinician or protocol
class TreatmentOptionCriterion(BaseCriterion):
    treatment_option: str  # e.g. "anti-EGFR monotherapy", "chemotherapy", "standard of care"

class ContraindicationCriterion(BaseCriterion):
    contraindication: str  # e.g. "immunotherapy", "pembrolizumab", "general anesthesia"
    reason: Optional[str] = None  # e.g. "hypersensitivity", "allergic reaction", "significant toxicities"

# clinical judgement such as life expectancy
class ClinicalJudgementCriterion(BaseCriterion):
    judgement: str = ''

class PhysicalConditionCriterion(BaseCriterion):
    condition: str  # e.g. 'pregnancy', 'breastfeeding', 'post-menopausal'

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

class RequiredActionCriterion(BaseCriterion):
    action: str

class TissueAvailabilityCriterion(BaseCriterion):
    pass

class OtherCriterion(BaseCriterion):
    reason: Optional[str] = None  # Optional metadata for why it's "Other"

class AndCriterion(BaseCriterion):
    criteria: List[BaseCriterion]

class OrCriterion(BaseCriterion):
    criteria: List[BaseCriterion]

class NotCriterion(BaseCriterion):
    criterion: BaseCriterion

class IfCriterion(BaseCriterion):
    condition: BaseCriterion
    then: BaseCriterion
    else_: Optional[BaseCriterion] = None
