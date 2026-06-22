from __future__ import annotations

from pathlib import Path

from aus_trial_universe.eligibility_path.shared.utils.load_curated_rules import (
    load_curated_rules,
)
from aus_trial_universe.eligibility_path.shared.utils.traverse_curation_tree import (
    normalise_forest_into_list,
)


def _write_curation_file(tmp_path: Path, source: str) -> Path:
    path = tmp_path / "ACTRN00000000000000.py"
    path.write_text(source, encoding="utf-8")
    return path


def test_load_curated_rules_repairs_duplicate_adjacent_keyword_arguments(tmp_path: Path):
    path = _write_curation_file(
        tmp_path,
        """
rules = [
    Rule(
        rule_text="INCLUDE duplicate kwargs",
        exclude=False,
        flipped=False,
        curation=SystemicTherapy(
            description="broad treatment description",
            description="specific treatment description"
        ),
    )
]
""".strip(),
    )

    rules = load_curated_rules(path)

    assert rules is not None
    roots = normalise_forest_into_list(rules[0])
    assert len(roots) == 1
    assert type(roots[0]).__name__ == "SystemicTherapy"
    assert roots[0].description == "specific treatment description"


def test_load_curated_rules_repairs_helper_assignment_after_curation(tmp_path: Path):
    path = _write_curation_file(
        tmp_path,
        """
rules = [
    Rule(
        rule_text="INCLUDE helper assignment",
        exclude=False,
        flipped=False,
        curation=
helper_criterion = AndCriterion(
            description="helper root",
            criteria=[
                DiagnosticFindingCriterion(
                    description="measurable disease",
                    finding="measurable disease"
                )
            ]
        )
        criteria_list: List[BaseCriterion] = [helper_criterion]
    )
]
""".strip(),
    )

    rules = load_curated_rules(path)

    assert rules is not None
    roots = normalise_forest_into_list(rules[0])
    assert len(roots) == 1
    assert type(roots[0]).__name__ == "AndCriterion"
    assert type(roots[0].criteria[0]).__name__ == "DiagnosticFindingCriterion"


def test_load_curated_rules_repairs_embedded_classes_before_dict_criteria(tmp_path: Path):
    path = _write_curation_file(
        tmp_path,
        """
rules = [
    Rule(
        rule_text="INCLUDE embedded classes",
        exclude=False,
        flipped=False,
        curation=
class BaseCriterion(TypedDict):
            description: str
        class DiagnosticFindingCriterion(BaseCriterion):
            finding: str

            {
                "description": "root",
                "criteria": [
                    {
                        "description": "measurable disease",
                        "finding": "measurable disease"
                    }
                ]
            }
    )
]
""".strip(),
    )

    rules = load_curated_rules(path)

    assert rules is not None
    roots = normalise_forest_into_list(rules[0])
    assert len(roots) == 1
    assert type(roots[0]).__name__ == "AndCriterion"
    assert type(roots[0].criteria[0]).__name__ == "DiagnosticFindingCriterion"
