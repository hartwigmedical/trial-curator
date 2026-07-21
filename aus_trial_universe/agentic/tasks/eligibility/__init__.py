"""Eligibility path — free-text trial eligibility -> normalized 3NF relational tables.

Sub-packages:
  extraction/  STAGE I: free text -> DNF eligibility rows (per regime/arm)
  mapping/     STAGE II: map the extracted cells -> OncoTree / finding-model vocab (lookup-first)
  tools/       vocab + validators (oncotree, finding_model) used by mapping
  qa/          independent output validator (validate_output)

The drug utility path is a SEPARATE sibling package (tasks/drug_utility/); the two join on (trialId, arm).
"""
