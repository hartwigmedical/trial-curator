import logging
import sys
from typing import NamedTuple
from difflib import unified_diff

from sentence_transformers import SentenceTransformer, SimilarityFunction, util

from .eligibility_py_loader import exec_file_into_variable

logger = logging.getLogger(__name__)

logging.basicConfig(stream=sys.stdout,
                    format='%(asctime)s %(levelname)5s - %(message)s',
                    datefmt='%H:%M:%S',
                    level=logging.INFO)

FUZZY_MATCH_THRESHOLD = 0.9


class CriteriaDiff(NamedTuple):
    old_criterion: str | None
    new_criterion: str | None
    similarity: float
    diff: list[str]


def criterion_diff(old_criteria: list[str], new_criteria: list[str], fuzzymatch_model=None) -> list[CriteriaDiff]:
    logger.info("running criterion diff")

    # remove any empty strings
    old_criteria = [c for c in old_criteria if len(c) > 0]
    new_criteria = [c for c in new_criteria if len(c) > 0]

    if fuzzymatch_model is None:
        fuzzymatch_model = SentenceTransformer("cambridgeltl/SapBERT-from-PubMedBERT-fulltext",
                                               similarity_fn_name=SimilarityFunction.DOT_PRODUCT)

    class CriteriaCompareScore(NamedTuple):
        old_criterion: str
        new_criterion: str
        similarity: float

    # Compare the description field of each criterion using fuzzy matching.
    # Generate the comparison scores for each pair of criteria and return the best
    # matches. Each criterion can only match with one other criterion.

    compare_scores: list[CriteriaCompareScore] = []

    # perform encoding
    old_criteria_embeddings = fuzzymatch_model.encode(old_criteria, show_progress_bar=False)
    new_criteria_embeddings = fuzzymatch_model.encode(new_criteria, show_progress_bar=False)

    # get all the similarity scores
    for i in range(len(old_criteria)):
        embedding1 = old_criteria_embeddings[i]
        for j in range(len(new_criteria)):
            embedding2 = new_criteria_embeddings[j]
            score = util.cos_sim(embedding1, embedding2).item()
            compare_scores.append(CriteriaCompareScore(old_criteria[i], new_criteria[j], score))

    # sort by descending
    compare_scores.sort(key=lambda x: x.similarity, reverse=True)

    matches: list[CriteriaCompareScore] = []

    # id() is a unique object identifier in python
    unmatched_c1_ids = [id(c) for c in old_criteria]
    unmatched_c2_ids = [id(c) for c in new_criteria]

    # now get the matches together, from highest to lowest
    for compare_score in compare_scores:
        c1, c2, score = compare_score

        # already matched
        if id(c1) not in unmatched_c1_ids or id(c2) not in unmatched_c2_ids:
            continue

        if score > FUZZY_MATCH_THRESHOLD:
            unmatched_c1_ids.remove(id(c1))
            unmatched_c2_ids.remove(id(c2))
            matches.append(compare_score)

    # sort the matches by c1 ordering
    criteria1_ids = [id(c) for c in old_criteria]
    matches.sort(key=lambda x: criteria1_ids.index(id(x.old_criterion)))

    diffs: list[CriteriaDiff] = [CriteriaDiff(m.old_criterion, m.new_criterion, m.similarity,
                                              list(unified_diff(m.old_criterion, m.new_criterion))) for m in matches]

    # now generate the diffs, the logic is:
    # 1. use the matches to find the old criteria ordering
    old_i = 0
    new_i = 0
    i = 0
    while i < len(diffs):
        # find any unmatched old criteria before this match
        while old_i < len(old_criteria) and (old_criteria[old_i] is not diffs[i].old_criterion):
            diffs.insert(i, CriteriaDiff(old_criteria[old_i], None, 0.0, []))
            i += 1
            old_i += 1
        # find any unmatched new criteria before this match  
        while new_i < len(new_criteria) and (new_criteria[new_i] is not diffs[i].new_criterion):
            diffs.insert(i, CriteriaDiff(None, new_criteria[new_i], 0.0, []))
            i += 1
            new_i += 1
        # move past the matched criterion
        old_i += 1
        new_i += 1
        i += 1

    # add any remaining unmatched old criteria
    while old_i < len(old_criteria):
        diffs.append(CriteriaDiff(old_criteria[old_i], None, 0.0, []))
        old_i += 1

    # add any remaining unmatched new criteria
    while new_i < len(new_criteria):
        diffs.append(CriteriaDiff(None, new_criteria[new_i], 0.0, []))
        new_i += 1

    return diffs


def format_differences(differences: list[CriteriaDiff]) -> str:
    """
    Format the differences between two runs into a readable string.
    
    Args:
        differences: Dictionary containing differences between runs and similarity scores
        
    Returns:
        Formatted string showing the differences and similarity scores
    """
    result = []
    for i, diff in enumerate(differences, 1):
        if diff.old_criterion and diff.new_criterion:
            if diff.similarity >= 0.999:
                result.append(f"\nCriterion {i} - matched (Similarity: {diff.similarity * 100:.1f}%)")
                result.append("Description: " + diff.new_criterion)
            else:
                result.append(f"\nCriterion {i} - Modified (Similarity: {diff.similarity * 100:.1f}%)")
                result.append("Old: " + diff.old_criterion)
                result.append("New: " + diff.new_criterion)
                #if diff.description_diff:
                #    result.append("Differences:")
                #    result.extend("  " + line for line in diff.description_diff)
        elif diff.old_criterion:
            result.append(f"\nCriterion {i} - Removed")
            result.append(diff.old_criterion)
        else:
            result.append(f"\nCriterion {i} - Added")
            result.append(diff.new_criterion)

    return "\n".join(result)


def find_matching_cohort(old_cohort_names, new_cohort_names):
    pass


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Criteria compare")
    parser.add_argument('--old_criteria_file', help='python file containing old criteria', required=True)
    parser.add_argument('--new_criteria_file', help='python file containing old criteria', required=True)
    parser.add_argument('--log_level', help="Set the log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)", default="INFO")
    args = parser.parse_args()

    old_cohort_criteria = exec_file_into_variable(args.old_criteria_file)
    old_cohort_criteria = next(iter(old_cohort_criteria.values()))
    new_cohort_criteria = exec_file_into_variable(args.new_criteria_file)
    new_cohort_criteria = next(iter(new_cohort_criteria.values()))

    diffs = criterion_diff([c.description for c in old_cohort_criteria], [c.description for c in new_cohort_criteria])

    print(format_differences(diffs))


if __name__ == "__main__":
    main()
