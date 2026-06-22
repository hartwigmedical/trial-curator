TGA_STATUS_PROMPT = """
TGA approval mapping:
- TGAStatus: Drug-level Australian approval status.
- Use `Approved` if the unique drug/active ingredient has at least one current
  ARTG medicine or biological entry in Australia, regardless of cancer
  indication, regimen, formulation, sponsor, or combination partner.
- Use `Not approved` if no current ARTG entry is found after searching the
  standardized name and known aliases.
- Use `Unclear` if the evidence is ambiguous.
- Do not count SAS, Authorised Prescriber, clinical trial access, or section 19A
  supply as general TGA approval.
""".strip()
