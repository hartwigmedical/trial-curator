import textwrap
from itertools import zip_longest
from pathlib import Path

import trialcurator.eligibility_curator_actin as actin
from trialcurator.eligibility_curator import parse_actin_output_to_json
from trialcurator.openai_client import OpenaiClient

from sentence_transformers import SentenceTransformer, SimilarityFunction, util


fuzzymatch_model = SentenceTransformer("cambridgeltl/SapBERT-from-PubMedBERT-fulltext", similarity_fn_name=SimilarityFunction.DOT_PRODUCT)


def test_actin_reproducibility():

    client = OpenaiClient(0.0)

    actin_rules = actin.load_actin_rules(str(Path(__file__).resolve().parent/"data/ACTIN_test_cases/ACTIN_CompleteList_03042025.csv"))

    input_text = '''
    - INCLUDE Has histologically or cytologically confirmed cancer that meets criteria as defined in the protocol
    - INCLUDE Is anti-PD-1/PD-L1 naïve, defined as never having previously been treated with a drug that targets the PD-1
    - INCLUDE Has at least 1 lesion that meets study criteria as defined in the protocol
    - INCLUDE Willing to provide tumor tissue from newly obtained biopsy (at a minimum core biopsy) from a tumor site that has not been previously irradiated
    - INCLUDE In the judgement of the investigator, has a life expectancy of at least 3 months
    - EXCLUDE Is currently participating in another study of a therapeutic agent
    - EXCLUDE Has received recent anti-EGFR antibody therapy as defined in the protocol
    - EXCLUDE Has had prior anti-cancer immunotherapy within 5 half-lives prior to study drug as defined in the protocol
    - EXCLUDE Has received any previous systemic, non-immunomodulatory biologic therapy within 4 weeks of first administration of study drug
    - EXCLUDE Has encephalitis, meningitis, organic brain disease (e.g., Parkinson's disease) or uncontrolled seizures within 1 year prior to the first dose of study drug
    '''

    for batch_size in [1,5,10]:

        print(f"\n=== BATCH SIZE: {batch_size} ===")

        output_1 = parse_actin_output_to_json("-", actin.map_actin_by_batch(input_text, client, actin_rules, batch_size))["mappings"]
        output_2 = parse_actin_output_to_json("-", actin.map_actin_by_batch(input_text, client, actin_rules, batch_size))["mappings"]

        lines = []
        width = 80

        header = f"{'RUN_1':<{width + 11}} || {'RUN_2':<{width + 11}} || {'Similarity Score'}"
        subhead = f"{'Tag':<8} | {'ACTIN Rule':<{width}} || {'Tag':<8} | {'ACTIN Rule':<{width}} ||"

        divider = "-" * (width * 2 + 47)
        lines.extend([header, subhead, divider])

        for m1, m2 in zip_longest(output_1, output_2, fillvalue=None):

            tag1 = m1["tag"] if m1 else ""
            rule1 = m1["ACTIN_rules"] if m1 else ""

            tag2 = m2["tag"] if m2 else ""
            rule2 = m2["ACTIN_rules"] if m2 else ""

            if tag1 == tag2:
                similarity_score = util.cos_sim(fuzzymatch_model.encode(rule1, convert_to_tensor=True), fuzzymatch_model.encode(rule2, convert_to_tensor=True)).item() if m1 and m2 else 0.0
            else:
                similarity_score = 0.0

            wrapped1 = textwrap.wrap(rule1, width=width)
            wrapped2 = textwrap.wrap(rule2, width=width)
            max_lines = max(len(wrapped1), len(wrapped2))
            wrapped1 += [""] * (max_lines - len(wrapped1))
            wrapped2 += [""] * (max_lines - len(wrapped2))

            for i in range(max_lines):
                sim_str = f"{similarity_score:.4f}" if i == 0 and m1 and m2 else ""

                line = (
                    f"{tag1:<8} | {wrapped1[i]:<{width}} || {tag2:<8} | {wrapped2[i]:<{width}} || {sim_str}"
                    if i == 0 else
                    f"{'':<8} | {wrapped1[i]:<{width}} || {'':<8} | {wrapped2[i]:<{width}} ||"
                )
                lines.append(line)

        print("\n".join(lines))
