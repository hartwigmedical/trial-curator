PBS_INDICATION_PROMPT = """
PBS indication mapping:
- PBSIndicationStatus: PBS reimbursement status by cancer indication.
- For each OncoTree code in the row, return whether the drug has a PBS listing
  for that indication, e.g. `HNSC: Reimbursed | COADREAD: Not reimbursed`.
- If the drug has PBS oncology indications outside the row's OncoTree codes,
  include them as `Other PBS oncology indications: ...`.
- Use `Unclear` if the PBS restriction text cannot be confidently mapped to
  OncoTree.
""".strip()
