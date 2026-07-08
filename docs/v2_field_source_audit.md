# v2 Field-Source Audit — where each criterion sources its info

- **Purpose:** for every criterion the pipeline extracts or filters on (eligibility, drug intervention, cohort, plus stage-I housekeeping), record exactly which CTGov and ANZCTR fields it draws from — so we can confirm no essential column/field is omitted before building each stage.
- **Scope:** CTGov reads the raw API JSON (`data/trial_inputs/ctgov/input_trials/version_<ddmmyyyy>/*merged*.json`); ANZCTR reads `data/trial_inputs/anzctr/extracted_trials/anzctr_field_extractions.csv` (31 columns).
- **Status:** living document. Reflects the input data as of `version_02072026` (CTGov n=1495; ANZCTR n=504).
- **How to read the "source" tags:** the section labels in the **Provenance vocabulary** section are exactly the `## <LABEL>` headers the agentic extractor sees, and are what appears in the `[source]` suffix of each extracted cell.

---

## 1. Raw field inventory

### 1.1 CTGov (`protocolSection.<module>.<field>`)
| Field (JSON path) | Notes / presence |
|---|---|
| `identificationModule.nctId` | trial id |
| `identificationModule.briefTitle` | "TITLE"; always present |
| `identificationModule.officialTitle` | "OFFICIAL TITLE"; present 1495/1495 |
| `conditionsModule.conditions` | list; "CONDITIONS" |
| `conditionsModule.keywords` | list; "KEYWORDS"; present ~1010/1495 |
| `descriptionModule.briefSummary` | "BRIEF SUMMARY" |
| `descriptionModule.detailedDescription` | "DETAILED DESCRIPTION"; present only ~779/1495 (skipped when absent) |
| `eligibilityModule.eligibilityCriteria` | free text (incl/excl); "ELIGIBILITY CRITERIA" |
| `eligibilityModule.sex` / `minimumAge` / `maximumAge` / `stdAges` / `healthyVolunteers` | demographic gates (not selected criteria) |
| `designModule.phases` / `studyType` | phase / interventional-vs-observational |
| `armsInterventionsModule.interventions[]` | `.type` (DRUG/BIOLOGICAL/…), `.name`, `.otherNames`, `.description`, `.armGroupLabels` |
| `armsInterventionsModule.armGroups[]` | `.label`, `.type`, `.interventionNames` — the cohort/arm structure |
| `statusModule.overallStatus` | recruitment status (stage-I retirement) |
| `sponsorCollaboratorsModule.leadSponsor.name` | sponsor |
| `contactsLocationsModule.locations[]` | `.country`/`.city`/`.state`/`.zip`/`.facility` (AU/NZ filter) |

### 1.2 ANZCTR (`anzctr_field_extractions.csv` columns)
| Column | Notes                                                                                             |
|---|---------------------------------------------------------------------------------------------------|
| `ACTRN` | trial id — **stored as bare digits** (e.g. `12605000025639`); display id must be `ACTRN`-prefixed |
| `STUDY TITLE` | "STUDY TITLE"                                                                                     |
| `SCIENTIFIC TITLE` | "SCIENTIFIC TITLE"                                                                                |
| `HEALTH CONDITION` | "HEALTH CONDITION"                                                                                |
| `INTERVENTIONS` | free-text intervention description (drugs + regimen + design prose)                               |
| `COMPARATOR` / `CONTROL` | comparator arm / control type                                                                     |
| `INCLUSIVE CRITERIA` | inclusion free text → "INCLUSION CRITERIA"                                                        |
| `EXCLUSIVE CRITERIA` | exclusion free text -> "NOT(INCLUSION CRITERIA)"                                                  |
| `MIN AGE` / `MIN AGE TYPE` / `MAX AGE` / `MAX AGE TYPE` / `INCLUSIVE GENDER` | demographic gates                                                                                 |
| `PHASE` | trial phase                                                                                       |
| `RECRUITMENT STATUS` | stage-I retirement                                                                                |
| `RECRUITMENT COUNTRY` / `RECRUITMENT STATE` | geography                                                                                         |
| `PRIMARY SPONSOR TYPE` / `NAME` / `COUNTRY` | sponsor                                                                                           |
| `anzctr_intervention_codes` | registry intervention code(s), e.g. `Treatment: Drugs`, `Treatment: Other` — the drug-filter key  |

---

## 2. Per-criterion source mapping

Legend: **✓ used today** in the agentic stage-II assembly · **○ available but not yet wired** · **✗ gap / needs a decision**.

### Eligibility criteria (stage-II extraction targets)
| Criterion               | CTGov sources                                                                             | ANZCTR sources                                                    |
|-------------------------|-------------------------------------------------------------------------------------------|-------------------------------------------------------------------|
| **cancer_type**         | TITLE, OFFICIAL TITLE, CONDITIONS, KEYWORDS, BRIEF SUMMARY, DETAILED DESCRIPTION, ELIGIBILITY CRITERIA | STUDY TITLE, SCIENTIFIC TITLE, HEALTH CONDITION, INCLUSION CRITERIA, EXCLUSION_CRITERIA |
| **gene_alteration**     | same set (usually ELIGIBILITY CRITERIA)                                      | same set (usually INCLUSION CRITERIA, EXCLUSION_CRITERIA)                                 |
| **molecular_signature** | same as `gene_alteration`                                                              | same as `gene_alteration`                            |
| **molecular_biomarker** | same as `gene_alteration`                                                              | same as `gene_alteration`                            |
| **prior_therapy**       | same as `gene_alteration`                                                              | same as `gene_alteration`                                      |

### Drug intervention (stage-I filter; POTTR-listed trials exempt + stage-II extraction targets)
| Criterion | CTGov sources | ANZCTR sources                                                                                                                             |
|---|---|--------------------------------------------------------------------------------------------------------------------------------------------|
| **is-drug-trial** | `armsInterventionsModule.interventions[].type ∈ {DRUG, BIOLOGICAL}` | `anzctr_intervention_codes` = `Treatment: Drugs`/`Treatment: Other`                                                                        |
| **drug names** | `interventions[].name` + `.otherNames` + `.description` | Requires RxNorm matching and/or LLM identification from free text `DRUG_rxnorm_matched_interventions/_title` (+ `llm_drugs_*` adjustments) |

### Cohort determination
| Criterion | CTGov sources                                                                                                                                                                                            | ANZCTR sources                                                                                                                      |
|---|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------|
| **cohort/arm structure** | 1. `armsInterventionsModule.armGroups[].label/.type/.interventionNames`; 2. cohort-specific inclusion sometimes only in ELIGIBILITY CRITERIA / DETAILED DESCRIPTION (If there is a conflict, defer to 1) | LLM required to parse cohorts from `INTERVENTIONS` / `INCLUSION CRITERIA` free text, or the trial treated as a single cohort. |

### Stage-I housekeeping
| Criterion | CTGov source | ANZCTR source |
|---|---|---|
| **recruitment status (retire)** | `statusModule.overallStatus` | `RECRUITMENT STATUS` |
| **phase / study type** | `designModule.phases` / `studyType` | `PHASE` |
| **geography (AU/NZ)** | `contactsLocationsModule.locations[].country` | `RECRUITMENT COUNTRY` / `STATE` |

---

## 3. Provenance vocabulary (`[source]` tags)

The extractor tags each cell with the section it was found in. Current controlled vocabulary = the assembled section headers:

- **CTGov:** `TITLE`, `OFFICIAL TITLE`, `CONDITIONS`, `KEYWORDS`, `BRIEF SUMMARY`, `DETAILED DESCRIPTION`, `ELIGIBILITY CRITERIA`, `INTERVENTIONS MODULE`
- **ANZCTR:** `STUDY TITLE`, `SCIENTIFIC TITLE`, `HEALTH CONDITION`, `INCLUSION CRITERIA`, `EXCLUSION CRITERIA`, `INTERVENTIONS`

If a value appears in several sections, cite all instances. Use the delimiter ;.

---